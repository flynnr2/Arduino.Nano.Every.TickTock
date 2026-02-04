#include "Config.h"

#include <Arduino.h>
#include <math.h>
#include <util/atomic.h>
#include "PendulumProtocol.h"
#include "SerialParser.h"
#include "EEPROMConfig.h"
#include "PendulumCore.h"
#include "CaptureInit.h"
#include <stdlib.h>
#include "AtomicUtils.h"

static inline uint32_t ppm_from_frac(float f){ if (f<0) f=-f; return (uint32_t)lroundf(f*1.0e6f);} 

#define setFlag(bit)   (GPIOR0 |=  (1 << (bit)))
#define clearFlag(bit) (GPIOR0 &= ~(1 << (bit)))
#define checkFlag(bit) (GPIOR0 &   (1 << (bit)))

static inline uint32_t elapsed32(uint32_t now, uint32_t then) {
  return (uint32_t)(now - then);
}


volatile uint16_t tcb0Ovf                    = 0;        // overflow counter for TCB0 capture

volatile uint32_t lastPpsCapture             = 0;        // last PPS capture tick count
float correctionFactor                        = 1.0;      // Used to adjust TCB0 timing drift;
float corrInst                                = 1.0;      // Instantaneous correction factor
bool isTick                                   = true;     // Alternates each swing
GpsStatus gpsStatus = GpsStatus::NO_PPS;


// ==== New event and data buffers ====
struct EdgeEvent {
  uint32_t ticks;
  uint8_t  type;
  uint16_t ovf;
};

constexpr uint8_t  EVBUF_SIZE = 64;
EdgeEvent          evbuf[EVBUF_SIZE];
volatile uint8_t   ev_head = 0;
volatile uint8_t   ev_tail = 0;

struct FullSwing {
  uint32_t tick_block;
  uint32_t tick;
  uint32_t tock_block;
  uint32_t tock;
};

constexpr uint8_t  SWING_RING_SIZE = RING_SIZE_IR_SENSOR;
FullSwing          swing_buf[SWING_RING_SIZE];
volatile uint8_t   swing_head = 0, swing_tail = 0;

constexpr uint8_t PPS_RING_SIZE = RING_SIZE_PPS;
volatile uint32_t ppsBuffer[PPS_RING_SIZE];
volatile uint8_t  ppsHead = 0, ppsTail = 0;

static uint32_t pps_delta_inst = (uint32_t)F_CPU;
static uint64_t pps_delta_fast = (uint32_t)F_CPU;
static uint64_t pps_delta_slow = (uint32_t)F_CPU;
// Cached active denominator for unit conversions (blended fast/slow)
static uint64_t pps_delta_active = (uint32_t)F_CPU;

// Quality metrics
static uint32_t pps_R_ppm = 0;   // |fast - slow| / slow in ppm
static uint32_t pps_J_ppm = 0;   // MAD / slow in ppm (robust jitter)

// Internal GPS state for richer lock tracking
enum class GpsState : uint8_t { NO_PPS=0, ACQUIRING=1, LOCKED=2, HOLDOVER=3, BAD_JITTER=4 };
static GpsState gpsState = GpsState::NO_PPS;

// Last Hampel MAD (in ticks) exposed by filter
static uint32_t last_hampel_mad = 0;


// Hampel ring on raw delta (ticks)
static constexpr uint8_t HAMPEL_MAX = 9;
static uint32_t hampelBuf[HAMPEL_MAX] = {0};
static uint8_t  hampelIdx = 0;
static uint8_t  hampelFill = 0;

// Small helpers
static inline uint32_t clamp_delta(uint32_t d) {
  const uint32_t MIN_TICKS = (uint32_t)(F_CPU / 4);  // 0.25 s
  const uint32_t MAX_TICKS = (uint32_t)(F_CPU * 4);  // 4 s
  if (d < MIN_TICKS) return MIN_TICKS;
  if (d > MAX_TICKS) return MAX_TICKS;
  return d;
}

static inline uint8_t swing_mask(uint8_t v) { return v & (SWING_RING_SIZE - 1); }
static inline bool swing_available() { return swing_tail != swing_head; }
static inline void swing_push(const FullSwing &s) {
  uint8_t n = swing_mask(swing_head + 1);
  if (n != swing_tail) {
    swing_buf[swing_head] = s;
    swing_head            = n;
  } else {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      droppedEvents++;
    }
  }
}
static inline FullSwing swing_pop() {
  FullSwing s;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    s = swing_buf[swing_tail];
    swing_tail = swing_mask(swing_tail + 1);
  }
  return s;
}

static inline uint8_t pps_mask(uint8_t v) { return v & (PPS_RING_SIZE - 1); }
static inline bool ppsData_available() { return ppsTail != ppsHead; }
static inline void ppsData_push(uint32_t t) {
  uint8_t n = pps_mask(ppsHead + 1);
  if (n != ppsTail) {
    ppsBuffer[ppsHead] = t;
    ppsHead            = n;
  } else {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      droppedEvents++;
    }
  }
}
static inline uint32_t ppsData_pop() {
  uint32_t t;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    t = ppsBuffer[ppsTail];
    ppsTail = pps_mask(ppsTail + 1);
  }
  return t;
}

uint32_t ticks_to_us_pps(uint32_t ticks) {
  uint32_t denom = pps_delta_active ? (uint32_t)pps_delta_active : (uint32_t)F_CPU;
  return (uint32_t)(((uint64_t)ticks * 1000000ULL) / denom);
}

static inline uint32_t ticks_to_ns_pps(uint32_t ticks) {
  uint32_t denom = pps_delta_active ? (uint32_t)pps_delta_active : (uint32_t)F_CPU;
  return (uint32_t)(((uint64_t)ticks * 1000000000ULL) / denom);
}


// Count of dropped events
volatile uint32_t droppedEvents              = 0;

// IR beam timing (tick/tock transitions)
volatile uint32_t currentSwingTicks          = 0;

static inline uint16_t read_TCB0_CNT() { return TCB0.CNT; }

static inline bool evbuf_available() { return ev_tail != ev_head; }

static inline void push_event(uint32_t ticks, uint8_t type, uint16_t ovf) {
  uint8_t next = (uint8_t)(ev_head + 1) & (EVBUF_SIZE - 1);
  if (next != ev_tail) {
    evbuf[ev_head].ticks = ticks;
    evbuf[ev_head].type  = type;
    evbuf[ev_head].ovf   = ovf;
    ev_head = next;
  } else {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      droppedEvents++;
    }
  }
}

static inline EdgeEvent pop_event() {
  EdgeEvent e;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    e = evbuf[ev_tail];
    ev_tail = (uint8_t)(ev_tail + 1) & (EVBUF_SIZE - 1);
  }
  return e;
}

static void process_edge_events() {
  static uint8_t  swing_state = 0;
  static uint32_t last_ts     = 0;
  static FullSwing curr;

  while (evbuf_available()) {
    EdgeEvent e = pop_event();

    switch (swing_state) {
      case 0: // wait for first falling edge to start swing exiting in inverted sensor
        if (e.type == 0) {
          last_ts     = e.ticks;
          swing_state = 1;
        }
        break;
      case 1: // end tick block
        if (e.type == 1) {
          curr.tick_block = elapsed32(e.ticks, last_ts);
          last_ts         = e.ticks;
          swing_state     = 2;
        }
        break;
      case 2: // end tick
        if (e.type == 0) {
          curr.tick  = elapsed32(e.ticks, last_ts);
          last_ts    = e.ticks;
          swing_state = 3;
        }
        break;
      case 3: // end tock block
        if (e.type == 1) {
          curr.tock_block = elapsed32(e.ticks, last_ts);
          last_ts         = e.ticks;
          swing_state     = 4;
        }
        break;
      case 4: // end tock
        if (e.type == 0) {
          curr.tock = elapsed32(e.ticks, last_ts);
          swing_push(curr);
          last_ts     = e.ticks;
          swing_state = 1; // start next swing with this falling edge
        }
        break;
    }
  }
}

// Coherent 32-bit timestamp from TCB0 {ovf_count, CNT}
static inline uint32_t tcb0_now_coherent() {
  uint16_t o1 = tcb0Ovf;
  uint16_t c  = read_TCB0_CNT();
  uint16_t o2 = tcb0Ovf;
  if (o1 != o2) {
    c = read_TCB0_CNT();
    o1 = o2;
  }
  return ((uint32_t)o1 << 16) | (uint32_t)c;
}

// 16-bit wrap-safe subtract
static inline uint16_t sub16(uint16_t a, uint16_t b) { return (uint16_t)(a - b); }

namespace Tunables {
  float     correctionJumpThresh = CORRECTION_JUMP_THRESHOLD;

  // Back-compat alias to slow
  uint8_t   ppsEmaShift          = PPS_SLOW_SHIFT_DEFAULT;

  // New:
  uint8_t   ppsFastShift         = PPS_FAST_SHIFT_DEFAULT;
  uint8_t   ppsSlowShift         = PPS_SLOW_SHIFT_DEFAULT;
  uint8_t   ppsHampelWin         = PPS_HAMPEL_WIN_DEFAULT;   // odd
  uint16_t  ppsHampelKx100       = PPS_HAMPEL_KX100_DEFAULT;
  bool      ppsMedian3           = PPS_MEDIAN3_DEFAULT;
  uint16_t  ppsBlendLoPpm        = PPS_BLEND_LO_PPM_DEFAULT;
  uint16_t  ppsBlendHiPpm        = PPS_BLEND_HI_PPM_DEFAULT;
  uint16_t  ppsLockRppm          = PPS_LOCK_R_PPM_DEFAULT;
  uint16_t  ppsLockJppm          = PPS_LOCK_J_PPM_DEFAULT;
  uint16_t  ppsUnlockRppm        = PPS_UNLOCK_R_PPM_DEFAULT;
  uint16_t  ppsUnlockJppm        = PPS_UNLOCK_J_PPM_DEFAULT;
  uint8_t   ppsUnlockCount       = PPS_UNLOCK_COUNT_DEFAULT;
  uint16_t  ppsHoldoverMs        = PPS_HOLDOVER_MS_DEFAULT;

  DataUnits dataUnits            = DATA_UNITS_DEFAULT;
}
static uint32_t median_copy(uint32_t *src, uint8_t n) {
  uint32_t a[HAMPEL_MAX];
  for (uint8_t i=0;i<n;i++) a[i]=src[i];
  // insertion sort
  for (uint8_t i=1;i<n;i++){
    uint32_t key=a[i]; int8_t j=i-1;
    while (j>=0 && a[j]>key){ a[j+1]=a[j]; j--; }
    a[j+1]=key;
  }
  return a[n/2];
}

static uint32_t hampel_filter(uint32_t raw) {
  // Fill ring
  uint8_t W = Tunables::ppsHampelWin;
  if (W < 5 || W > HAMPEL_MAX || !(W&1)) W = PPS_HAMPEL_WIN_DEFAULT;

  hampelBuf[hampelIdx] = raw;
  hampelIdx = (uint8_t)((hampelIdx + 1) % W);
  if (hampelFill < W) ++hampelFill;

  if (hampelFill < W) return raw; // not enough history yet

  // Build window ordered starting from idx
  uint32_t win[HAMPEL_MAX];
  for (uint8_t i=0;i<W;i++){
    uint8_t k = (uint8_t)((hampelIdx + W - 1 - i) % W);
    win[i] = hampelBuf[k];
  }
  uint32_t med = median_copy(win, W);

  // MAD
  for (uint8_t i=0;i<W;i++){
    win[i] = (win[i] > med) ? (win[i] - med) : (med - win[i]);
  }
  uint32_t mad = median_copy(win, W);
  last_hampel_mad = mad;

  if (mad == 0) return med; // very stable → clamp to center

  // Threshold: k * 1.4826 * MAD ~ 1.5*mad, but keep it integer using Kx100
  uint32_t kx100 = Tunables::ppsHampelKx100 ? Tunables::ppsHampelKx100 : PPS_HAMPEL_KX100_DEFAULT;
  // approx scale for 1.4826 ≈ 148; multiply k (×100) by 148 then /100
  uint32_t scaled = (uint32_t)((uint32_t)mad * (uint32_t)((kx100 * 148u) / 100u));

  uint32_t diff = (raw > med) ? (raw - med) : (med - raw);
  if (diff > scaled) return med; // outlier → replace
  return raw;
}

static inline uint32_t median3(uint32_t a, uint32_t b, uint32_t c){
  if (a>b){ uint32_t t=a;a=b;b=t; }
  if (b>c){ uint32_t t=b;b=c;c=t; }
  if (a>b){ uint32_t t=a;a=b;b=t; }
  return b;
}

void pendulumSetup() {
  pinMode(ledPin, OUTPUT);

  gpsStatus = GpsStatus::NO_PPS;
  DATA_SERIAL.begin(SERIAL_BAUD_NANO); delay(10);
  if (&CMD_SERIAL != &DATA_SERIAL) {
    CMD_SERIAL.begin(SERIAL_BAUD_NANO); delay(10);
  }
  if (&DEBUG_SERIAL != &DATA_SERIAL && &DEBUG_SERIAL != &CMD_SERIAL) {
    DEBUG_SERIAL.begin(SERIAL_BAUD_NANO); delay(10);
  }

  sendStatus(StatusCode::ProgressUpdate, "Begin setup() ...");

  cli();
  evsys_init();
  tcb0_init_free_running();
  tcb1_init_IR_capt();
  tcb2_init_PPS_capt();
  sei();
  
  TunableConfig cfg;
  if (loadConfig(cfg)) applyConfig(cfg);

  sendStatus(StatusCode::ProgressUpdate, "... end setup()");
  printCsvHeader();
}

static void process_pps() {
  uint32_t now = tcb0_now_coherent();
  if (lastPpsCapture != 0) {
    uint32_t since = elapsed32(now, lastPpsCapture);
    if (since > (uint32_t)(F_CPU + F_CPU / 2)) {
      gpsStatus = GpsStatus::HOLDOVER;
      gpsState  = GpsState::HOLDOVER;
    }
  }
  while (ppsData_available()) {
    uint32_t t = ppsData_pop();
    if (lastPpsCapture != 0) {
      uint32_t delta_raw = elapsed32(t, lastPpsCapture);
      delta_raw = clamp_delta(delta_raw);
      pps_delta_inst = delta_raw;

      // 1) Outlier guard
      uint32_t delta_clean = hampel_filter(delta_raw);
      if (Tunables::ppsMedian3 && hampelFill >= 3) {
        static uint32_t d1=delta_clean, d2=delta_clean;
        uint32_t d0 = delta_clean;
        delta_clean = median3(d0, d1, d2);
        d2 = d1; d1 = d0;
      }

      // 2) Fast EWMA on clean delta
      uint64_t fast = pps_delta_fast;
      int64_t  errf = (int64_t)delta_clean - (int64_t)fast;
      uint8_t  sF   = Tunables::ppsFastShift ? Tunables::ppsFastShift : PPS_FAST_SHIFT_DEFAULT;
      fast += (errf >> sF);
      pps_delta_fast = fast;

      // 3) Slow EWMA on fast output
      uint64_t slow = pps_delta_slow;
      int64_t  errs = (int64_t)fast - (int64_t)slow;
      uint8_t  sS   = Tunables::ppsSlowShift ? Tunables::ppsSlowShift : PPS_SLOW_SHIFT_DEFAULT;
      slow += (errs >> sS);
      pps_delta_slow = slow;

      // 3.5) Quality metrics and blend selection
      float R_frac = fabsf((float)((int64_t)pps_delta_fast - (int64_t)pps_delta_slow)) / (float)pps_delta_slow;
      pps_R_ppm = ppm_from_frac(R_frac);

      float J_frac = (pps_delta_slow ? (float)last_hampel_mad / (float)pps_delta_slow : 0.0f);
      pps_J_ppm = ppm_from_frac(J_frac);

      float frac = fabsf((float)((int64_t)pps_delta_inst - (int64_t)pps_delta_slow)) / (float)pps_delta_slow;
      bool within = (frac <= Tunables::correctionJumpThresh);

      // State machine transitions (hysteresis)
      uint32_t now_ms = millis();
      static uint32_t last_pps_ms = 0;
      uint32_t since_pps_ms = (last_pps_ms==0) ? 0 : (now_ms - last_pps_ms);
      last_pps_ms = now_ms;

      // Holdover detection (also checked at top if no PPS for long time)
      if (since_pps_ms > Tunables::ppsHoldoverMs) {
        gpsState = GpsState::HOLDOVER;
      }

      // Evaluate based on R and J
      static uint8_t lockStable = 0;
      static uint8_t unlockCtr  = 0;
      if (gpsState == GpsState::NO_PPS) {
        gpsState = GpsState::ACQUIRING;
      }
      bool lockReady = (pps_R_ppm <= Tunables::ppsLockRppm)
        && (pps_J_ppm <= Tunables::ppsLockJppm)
        && within;
      lockStable = lockReady ? (uint8_t)min<int>(lockStable+1, 255) : 0;

      if (lockStable >= PPS_LOCK_STABLE_COUNT) {
        gpsState = GpsState::LOCKED;
      }

      bool unlockR = (pps_R_ppm >= Tunables::ppsUnlockRppm);
      bool unlockJ = (pps_J_ppm >= Tunables::ppsUnlockJppm);
      if (unlockR || unlockJ) {
        unlockCtr = (uint8_t)min<int>(unlockCtr+1, 255);
      } else {
        unlockCtr = 0;
      }
      if (gpsState == GpsState::LOCKED && unlockCtr >= Tunables::ppsUnlockCount) {
        gpsState = unlockJ ? GpsState::BAD_JITTER : GpsState::ACQUIRING;
      }

      // Blend weight from R_ppm with hysteresis
      uint16_t lo = Tunables::ppsBlendLoPpm;
      uint16_t hi = Tunables::ppsBlendHiPpm;
      uint32_t w_num = (pps_R_ppm <= lo) ? 0u : (pps_R_ppm >= hi ? (uint32_t)(hi - lo) : (uint32_t)(pps_R_ppm - lo));
      uint32_t w_den = (hi > lo) ? (uint32_t)(hi - lo) : 1u;
      uint32_t w_q16 = (uint32_t)((w_num << 16) / w_den);

      // State overrides
      if (gpsState == GpsState::LOCKED)   w_q16 = 0;
      if (gpsState == GpsState::ACQUIRING) w_q16 = 65535;

      // Cache active denominator (Q16 mix)
      slow = pps_delta_slow, fast = pps_delta_fast;
      pps_delta_active = ((slow * (65536 - w_q16)) + (fast * w_q16)) >> 16;

      // Map internal state to public gpsStatus for CSV compatibility
      switch (gpsState) {
        case GpsState::NO_PPS:     gpsStatus = GpsStatus::NO_PPS; break;
        case GpsState::LOCKED:     gpsStatus = GpsStatus::LOCKED; break;
        case GpsState::HOLDOVER:   gpsStatus = GpsStatus::HOLDOVER; break;
        case GpsState::BAD_JITTER: gpsStatus = GpsStatus::BAD_JITTER; break;
        default:                   gpsStatus = GpsStatus::ACQUIRING; break;
      }

      // 4) Corrections (for reporting)
      corrInst = (float)F_CPU / (float)pps_delta_inst;
    } else {
      gpsStatus = GpsStatus::ACQUIRING;
      // (stable counter implicitly resets on first edge or by 'within' above)
    }
    lastPpsCapture = t;
  }
}

void pendulumLoop() {
  processSerialCommands();
  process_pps();
  process_edge_events();

  while (swing_available()) {
    FullSwing fs = swing_pop();

    PendulumSample sample{};
    switch (Tunables::dataUnits) {
      case DataUnits::RawCycles:
        sample.tick       = fs.tick;
        sample.tock       = fs.tock;
        sample.tick_block = fs.tick_block;
        sample.tock_block = fs.tock_block;
        break;
      case DataUnits::AdjustedNs:
        sample.tick       = ticks_to_ns_pps(fs.tick);
        sample.tock       = ticks_to_ns_pps(fs.tock);
        sample.tick_block = ticks_to_ns_pps(fs.tick_block);
        sample.tock_block = ticks_to_ns_pps(fs.tock_block);
        break;
      case DataUnits::AdjustedUs:
        sample.tick       = ticks_to_us_pps(fs.tick);
        sample.tock       = ticks_to_us_pps(fs.tock);
        sample.tick_block = ticks_to_us_pps(fs.tick_block);
        sample.tock_block = ticks_to_us_pps(fs.tock_block);
        break;
      case DataUnits::AdjustedMs:
        sample.tick       = ticks_to_us_pps(fs.tick) / 1000;
        sample.tock       = ticks_to_us_pps(fs.tock) / 1000;
        sample.tick_block = ticks_to_us_pps(fs.tick_block) / 1000;
        sample.tock_block = ticks_to_us_pps(fs.tock_block) / 1000;
        break;
    }

    double corr_inst = corrInst;
    double denom = pps_delta_active ? (double)pps_delta_active : (double)F_CPU;
    double corr_blend = (double)F_CPU / denom;

    sample.corr_inst_ppm  = (int32_t)lround((corr_inst - 1.0) * (double)CORR_PPM_SCALE);
    sample.corr_blend_ppm = (int32_t)lround((corr_blend - 1.0) * (double)CORR_PPM_SCALE);
    sample.gps_status     = gpsStatus;
    sample.dropped_events = atomicRead32(droppedEvents);

    sendSample(sample);
  }

  DATA_SERIAL.flush();
}

// |-------------------------------------------------------------------------------------|
// | ISR: TCB0_INT_vect (free-running timer overflow)                                    |
// | Estimated cycle cost (ATmega4809 @ 20MHz)                                           |
// | Component                          | Cycles | Explanation                           |
// |------------------------------------|--------|---------------------------------------|
// | ISR prologue + epilogue            | ~22    | gcc pushes/pops regs + `reti`         |
// | Write `TCB0.INTFLAGS`              | 2      | `ldi` + `out` to clear CAPT/OVF flags |
// | Increment `tcb0Ovf`                | ~10    | 2×`lds` + `adiw` + 2×`sts`            |
// | **Total (≈22 + 2 + 10)**           | **~34**| ≈1.7µs at 20MHz                       |
// --------------------------------------------------------------------------------------|
ISR(TCB0_INT_vect) {
  TCB0.INTFLAGS = TCB_CAPT_bm | TCB_OVF_bm;
  tcb0Ovf++;
}

/* 
ISR timestampe capture rationale — why ISR(TCBn_INT_vect) beats reading TCBn.CCMP directly

- Single 32-bit timeline: TCBn.CCMP is only 16-bit in TCn2’s domain (wraps every ~4.096 ms @ 16 MHz).
  The ISR maps each PPS edge onto the TCB0+overflow 32-bit clock so PPS and IR events share one timebase.

- Removes ISR latency/jitter: measure how late we are (d = TCBn.CNT - TCBn.CCMP) and backdate the
  timestamp into the TCB0 domain, making the result independent of interrupt latency or main-loop load.

- Coherency & overflow safe: use tcb0_now_coherent() to read TCB0’s 32-bit time without wrap glitches;
  clear CAPT promptly to avoid the one-deep capture buffer being overwritten by the next PPS.

- Avoids cross-domain drift: no need to track TCBn overflows or calibrate a fixed phase offset to TCB0.

- Centralizes bookkeeping: ISR is the right place to push ring buffers, set flags, and apply sanity guards.

Minimal math (inside ISR):
    uint16_t ccmp = TCBn.CCMP;
    uint16_t cnt  = TCB .CNT;
    TCBn.INTFLAGS = TCB_CAPT_bm;           // clear early to prevent overwrite
    uint16_t d16  = cnt - ccmp;            // ticks since the edge (same tick rate as TCB0)
    uint32_t now  = tcb0_now_coherent();   // race-free 32-bit read of TCB0 time
    uint32_t ts32 = now - (uint32_t)d16;   // PPS timestamp in TCB0’s 32-bit timeline
*/


// |------------------------------------------------------------------------------------------|
// | ISR: TCB1_INT_vect (IR sensor edge capture)                                              |
// | Estimated cycle cost (typical tick path)                                                 |             
// | Component                          | Cycles | Explanation                                |
// |------------------------------------|--------|--------------------------------------------|
// | ISR prologue + epilogue            | ~22    | gcc pushes/pops regs + `reti`              |
// | Read `TCB1.CCMP`                   | 4      | two 8‑bit loads                            |
// | Read `TCB1.CNT`                    | 4      | two 8‑bit loads                            |
// | Clear `TCB1.INTFLAGS`              | 2      | `ldi` + `out`                              |
// | `latency16 = sub16(cnt, ccmp)`     | 2      | inline 16‑bit subtraction                  |
// | `now32 = tcb0_now_coherent()`      | ~20    | two reads of overflow counter + timer count|
// | `edge32 = now32 - latency16`       | 4      | 32‑bit subtraction                         |
// | `push_event(...)` (ring buffer)    | ~33    | index math, 32‑bit store, type/ovf stores  |
// | Update `TCB1.EVCTRL`               | 2      | `ldi` + `out`                              |
// | Toggle `isTick`                    | 2      | byte store                                 |
// | **Total (≈22 + remaining)**        | **~95**| ≈4.8µs at 20MHz                            |
// |------------------------------------------------------------------------------------------|
ISR(TCB1_INT_vect) {
  uint16_t ccmp = TCB1.CCMP;        // value of TCB1.CNT at the edge
  uint16_t cnt  = TCB1.CNT;         // value of TCB1.CNT now (at ISR time)
  TCB1.INTFLAGS = TCB_CAPT_bm | TCB_OVF_bm;

  uint16_t latency16 = sub16(cnt, ccmp);          // ticks since edge
  uint32_t now32     = tcb0_now_coherent();       // TCB0 domain
  uint32_t edge32    = now32 - (uint32_t)latency16;

  if (isTick) {
    push_event(edge32, 0, tcb0Ovf);                               // rising
    TCB1.EVCTRL   = TCB_CAPTEI_bm | TCB_EDGE_bm | TCB_FILTER_bm;  // capture events EDGE = 1
    isTick = false;
  } else {
    push_event(edge32, 1, tcb0Ovf);                               // falling
    TCB1.EVCTRL   = TCB_CAPTEI_bm |  TCB_FILTER_bm;               // capture events EDGE = 0
    isTick = true;
  }
}

// |-------------------------------------------------------------------------------------------|
// | ISR: TCB2_INT_vect (PPS capture)                                                          |
// | Estimated cycle cost                                                                      |
// | Component                          | Cycles | Explanation                                 |
// |------------------------------------|--------|---------------------------------------------|
// | ISR prologue + epilogue            | ~22    | gcc pushes/pops regs + `reti`               |
// | Read `TCB2.CCMP`                   | 4      | two 8‑bit loads                             |
// | Read `TCB2.CNT`                    | 4      | two 8‑bit loads                             |
// | Clear `TCB2.INTFLAGS`              | 2      | `ldi` + `out`                               |
// | `latency16 = sub16(cnt, ccmp)`     | 2      | inline 16‑bit subtraction                   |
// | `now32 = tcb0_now_coherent()`      | ~20    | two reads of overflow counter + timer count |
// | `edge32 = now32 - latency16`       | 4      | 32‑bit subtraction                          |
// | `ppsData_push(edge32)`             | ~18    | ring-buffer index + 32‑bit store            |
// | `setFlag(FLAG_PPS_TRIGGERED)`      | 4      | `in` + `ori` + `out`                        |
// | **Total (≈22 + remaining)**        | **~80**| ≈4.0µs at 20MHz                             |
// |-------------------------------------------------------------------------------------------|
ISR(TCB2_INT_vect) {
  uint16_t ccmp = TCB2.CCMP;        // value of TCB2.CNT at the edge
  uint16_t cnt  = TCB2.CNT;         // value of TCB2.CNT now (at ISR time)
  TCB2.INTFLAGS = TCB_CAPT_bm | TCB_OVF_bm;

  uint16_t latency16 = sub16(cnt, ccmp);          // ticks since edge
  uint32_t now32     = tcb0_now_coherent();       // TCB0 domain
  uint32_t edge32    = now32 - (uint32_t)latency16;

  ppsData_push(edge32);                   // enqueue PPS timestamp
  setFlag(FLAG_PPS_TRIGGERED);
}
