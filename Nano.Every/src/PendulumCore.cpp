#include "Config.h"

#include <Arduino.h>
#include <math.h>
#include <util/atomic.h>
#include "PendulumProtocol.h"
#include "SerialParser.h"
#include "EEPROMConfig.h"
#include "PendulumCore.h"
#include "CaptureInit.h"
#include "StsHeader.h"
#include <stdlib.h>
#include <string.h>
#include "AtomicUtils.h"
#include "PpsValidator.h"
#include "FreqDiscipliner.h"
#include "DisciplinedTime.h"

static_assert(sizeof(uint16_t) == 2, "Expected 16-bit capture modulo domain");
static_assert(sizeof(uint32_t) == 4, "Expected 32-bit PPS tick domain");

static inline uint32_t ppm_from_frac(float f){ if (f<0) f=-f; return (uint32_t)lroundf(f*1.0e6f);}

static inline uint32_t elapsed32(uint32_t now, uint32_t then) {
  return (uint32_t)(now - then);
}


volatile uint32_t pps_seen                   = 0;        // count of PPS edges seen in ISR

volatile uint32_t droppedEvents              = 0;

volatile uint32_t lastPpsCapture             = 0;        // last PPS capture tick count
volatile uint32_t lastPpsEdgeCapture         = 0;        // most recent PPS edge tick from ISR

volatile uint16_t pps_latency_last           = 0;        // latest PPS ISR latency (ticks)
volatile uint16_t pps_latency_min            = 0xFFFF;   // min PPS ISR latency since last report
volatile uint16_t pps_latency_max            = 0;        // max PPS ISR latency since last report
volatile uint32_t pps_latency_sum            = 0;        // sum PPS ISR latency since last report
volatile uint16_t pps_latency_count          = 0;        // number of PPS latency samples since last report
volatile uint16_t g_pps_latency16_max        = 0;        // max observed raw PPS latency16 (ticks)
volatile uint32_t g_pps_latency16_wrapRisk   = 0;        // count of latency16 values near 16-bit wrap-risk zone
volatile uint16_t isr_last_tcb0_ticks         = 0;        // latest observed TCB0 ISR execution time (ticks)
volatile uint16_t isr_last_tcb1_ticks         = 0;        // latest observed TCB1 ISR execution time (ticks)
volatile uint16_t isr_last_tcb2_ticks         = 0;        // latest observed TCB2 ISR execution time (ticks)
volatile uint16_t max_isr_tcb0_ticks          = 0;        // max observed TCB0 ISR execution time (ticks)
volatile uint16_t max_isr_tcb1_ticks          = 0;        // max observed TCB1 ISR execution time (ticks)
volatile uint16_t max_isr_tcb2_ticks          = 0;        // max observed TCB2 ISR execution time (ticks)

volatile uint16_t tcb0Ovf                    = 0;        // overflow counter for TCB0 capture
volatile uint32_t tcb0WrapDetected           = 0;        // number of observed TCB0 overflows
volatile uint32_t nowBackstepUnexpectedCount = 0;        // coherent-read unexpected regressions
volatile uint32_t coherentOvfFlagSeenCount   = 0;        // coherent-read snapshots with pending OVF flag set
volatile uint32_t coherentOvfAppliedCount    = 0;        // coherent-read snapshots where pending OVF compensation was applied
volatile uint32_t coherentReadRetryCount     = 0;        // coherent-read retries due to unstable OVF/CNT sampling


#if STS_DIAG > 0
enum DiagCliTag : uint8_t {
  DIAG_CLI_TAG_SETUP_INIT = 1
};

static volatile uint8_t diag_cli_depth = 0;
static volatile uint32_t diag_cli_start_now32 = 0;
static volatile uint8_t diag_cli_start_tag = 0;
static volatile uint32_t cli_max_ticks = 0;
static volatile uint32_t cli_gt_65536_count = 0;
static volatile uint32_t cli_gt_131072_count = 0;
static volatile uint32_t cli_unbalanced_count = 0;
static volatile uint8_t cli_max_tag = 0;
static volatile uint8_t cli_last_tag = 0;

static volatile uint32_t tcb0_gap_last_ticks = 0;
static volatile uint32_t tcb0_gap_max_ticks = 0;
static volatile uint32_t tcb0_gap_gt_65536_count = 0;
static volatile uint32_t tcb0_gap_gt_131072_count = 0;
static volatile uint32_t tcb0_gap_bin_le_65536 = 0;
static volatile uint32_t tcb0_gap_bin_le_131072 = 0;
static volatile uint32_t tcb0_gap_bin_le_196608 = 0;
static volatile uint32_t tcb0_gap_bin_gt_196608 = 0;
static volatile uint32_t tcb0_gap_ref_now32 = 0;
static volatile uint8_t tcb0_gap_ref_valid = 0;

static volatile uint32_t pps_isr_count = 0;
static volatile uint32_t pps_proc_count = 0;
static volatile uint32_t pps_backlog_max = 0;
static volatile uint32_t pps_dup_isr_suspect_count = 0;
static volatile uint32_t pps_missed_isr_suspect_count = 0;

static volatile uint32_t gap_total = 0;
static volatile uint32_t hard_glitch_total = 0;
static volatile uint32_t gap_bin_lt_1000 = 0;
static volatile uint32_t gap_bin_1000_50000 = 0;
static volatile uint32_t gap_bin_50000_60000 = 0;
static volatile uint32_t gap_bin_60000_65000 = 0;
static volatile uint32_t gap_bin_65000_70000 = 0;
static volatile uint32_t gap_bin_gt_70000 = 0;

static inline void diag_cli_enter(uint8_t tag, uint32_t now32) {
  cli_last_tag = tag;
  if (diag_cli_depth == 0) {
    diag_cli_start_now32 = now32;
    diag_cli_start_tag = tag;
  }
  if (diag_cli_depth < 0xFF) diag_cli_depth++;
}

static inline void diag_cli_exit(uint8_t tag, uint32_t now32) {
  cli_last_tag = tag;
  if (diag_cli_depth == 0) {
    cli_unbalanced_count++;
    return;
  }
  diag_cli_depth--;
  if (diag_cli_depth != 0) return;
  const uint32_t dur = elapsed32(now32, diag_cli_start_now32);
  if (dur > cli_max_ticks) {
    cli_max_ticks = dur;
    cli_max_tag = diag_cli_start_tag;
  }
  if (dur > 65536UL) cli_gt_65536_count++;
  if (dur > 131072UL) cli_gt_131072_count++;
}

#define DIAG_CLI(tag, now32) diag_cli_enter((uint8_t)(tag), (uint32_t)(now32))
#define DIAG_SEI(tag, now32) diag_cli_exit((uint8_t)(tag), (uint32_t)(now32))

static inline void diag_tcb0_gap_record(uint32_t now32) {
  if (tcb0_gap_ref_valid) {
    const uint32_t gap = elapsed32(now32, tcb0_gap_ref_now32);
    tcb0_gap_last_ticks = gap;
    if (gap > tcb0_gap_max_ticks) tcb0_gap_max_ticks = gap;
    if (gap > 65536UL) tcb0_gap_gt_65536_count++;
    if (gap > 131072UL) tcb0_gap_gt_131072_count++;
    if (gap <= 65536UL) tcb0_gap_bin_le_65536++;
    else if (gap <= 131072UL) tcb0_gap_bin_le_131072++;
    else if (gap <= 196608UL) tcb0_gap_bin_le_196608++;
    else tcb0_gap_bin_gt_196608++;
  }
  tcb0_gap_ref_now32 = now32;
  tcb0_gap_ref_valid = 1;
}
#else
#define DIAG_CLI(tag, now32) do { (void)(tag); (void)(now32); } while (0)
#define DIAG_SEI(tag, now32) do { (void)(tag); (void)(now32); } while (0)
#endif

#if ENABLE_STS_GPS_DEBUG
static uint32_t pend_edge_count = 0;
static uint32_t pend_backstep_count = 0;
static uint32_t pend_big_jump_count = 0;
static uint32_t pend_small_jump_count = 0;
static uint32_t pend_wrapish_count = 0;
static uint32_t pend_last_bad_seq = 0;
static uint32_t pend_last_bad_delta = 0;
static uint32_t pend_prev_edge32 = 0;
static bool pend_prev_edge32_valid = false;
static uint32_t pend_seq = 0;

static constexpr uint32_t MIN_EDGE_DELTA_TICKS = (uint32_t)(F_CPU / 5UL);   // 0.2 s
static constexpr uint32_t MAX_EDGE_DELTA_TICKS = (uint32_t)(F_CPU * 3UL);   // 3.0 s
static constexpr uint32_t WRAP_TICKS = 65536UL;
static constexpr uint32_t WRAP_TOL_TICKS = 2048UL;
#endif
float correctionFactor                        = 1.0;      // Used to adjust TCB0 timing drift;
float corrInst                                = 1.0;      // Instantaneous correction factor
bool isTick                                   = true;     // Alternates each swing
GpsStatus gpsStatus = GpsStatus::NO_PPS;


// ==== New event and data buffers ====
struct EdgeEvent {
  uint32_t ticks;
  uint8_t  type;
};

constexpr uint8_t  EVBUF_SIZE = 64;
static_assert((EVBUF_SIZE & (EVBUF_SIZE - 1U)) == 0U, "EVBUF_SIZE must be power-of-two for mask arithmetic");
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
static_assert((SWING_RING_SIZE & (SWING_RING_SIZE - 1U)) == 0U, "SWING_RING_SIZE must be power-of-two for mask arithmetic");
FullSwing          swing_buf[SWING_RING_SIZE];
volatile uint8_t   swing_head = 0, swing_tail = 0;

constexpr uint8_t PPS_RING_SIZE = RING_SIZE_PPS;
static_assert((PPS_RING_SIZE & (PPS_RING_SIZE - 1U)) == 0U, "PPS_RING_SIZE must be power-of-two for mask arithmetic");
struct PpsCapture {
  uint32_t edge32;
  uint32_t now32;
  uint16_t ovf;
  uint16_t cap16;
  uint16_t cnt;
  uint16_t latency16;
};
PpsCapture ppsBuffer[PPS_RING_SIZE];
volatile uint8_t  ppsHead = 0, ppsTail = 0;

static PpsValidator gPpsValidator;
static FreqDiscipliner gFreqDiscipliner;
static DisciplinedTime gDisciplinedTime;

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
static uint32_t pps_edge_seq = 0;
static uint16_t gps_edge_gap_cnt = 0;
static char gps_debug_line_buf[CSV_LINE_MAX];

static char* prepare_gps_debug_line_buf() {
  memset(gps_debug_line_buf, 0, sizeof(gps_debug_line_buf));
  return gps_debug_line_buf;
}

static constexpr uint8_t  STEADY_MAD_WINDOW = 31;
#if ENABLE_STS_GPS_DEBUG
static constexpr uint32_t GPS_HEALTH_PERIOD_MS = 10000UL;

enum class SnapClass : uint8_t { OK=0, SOFT=1, HARD=2 };
enum class SnapReason : uint8_t { NONE=0, HARD=1, BACKSTEP=2, E_BIG=3 };

struct GpsSnap {
  uint32_t edge_seq;
  uint32_t pps;
  uint32_t log_seq;
  uint32_t d;
  uint16_t dc;
  uint32_t exp;
  int32_t  e;
  uint32_t mad_e;
  uint32_t soft_ticks;
  uint32_t now32;
  uint16_t ovf;
  uint16_t cap16;
  uint16_t cnt;
  uint8_t  flags;
  uint8_t  cls;
  uint8_t  prime;
  uint8_t  gap;
  uint8_t  exp_valid;
  uint8_t  exp_init;
  uint16_t lat16;
  uint8_t  within;
  uint8_t  lock_ready;
  char     reset_reason;
};

static constexpr uint8_t GPS_HEALTH_WINDOW_EDGES = 10;
struct HealthEdgeFlags {
  uint8_t within;
  uint8_t lock_ready;
  uint8_t so;
  uint8_t hg;
  uint8_t gap;
  uint8_t bs;
};

static HealthEdgeFlags gpsHealthWindow[GPS_HEALTH_WINDOW_EDGES] = {};
static uint8_t gpsHealthHead = 0;
static uint8_t gpsHealthFill = 0;

static constexpr uint8_t GPS_SNAP_RING_SIZE = 6;
static GpsSnap gpsSnapRing[GPS_SNAP_RING_SIZE] = {};
static uint8_t gpsSnapHead = 0;
static uint16_t gps_hg_cnt = 0;
static uint16_t gps_so_cnt = 0;
static uint16_t gps_bs_cnt = 0;
static uint16_t gps_warm_so_cnt = 0;
static uint16_t gps_gap_cnt = 0;
static bool gps_dump_pending = false;
static SnapReason gps_dump_reason = SnapReason::NONE;
static uint32_t gps_dump_pps = 0;
static uint32_t gps_dump_trigger_edge_seq = 0;
static uint32_t gps_dump_last_emitted_edge_seq = 0;
static uint32_t gps_edge_seq = 0;
static uint32_t gps_log_seq = 0;
static uint32_t gps_last_logged_edge_seq = 0;
static bool gps_have_pending_log = false;
static GpsSnap gps_pending_log = {};
static constexpr uint8_t GPS_DIAG_RING_SIZE = 30;
static int32_t gps_br_ring[GPS_DIAG_RING_SIZE] = {};
static uint8_t gps_diag_head = 0;
static uint8_t gps_diag_fill = 0;

static int32_t gps_diag_scratch[GPS_DIAG_RING_SIZE];
static inline char gps_state_char(GpsState s) {
  switch (s) {
    case GpsState::LOCKED: return 'L';
    case GpsState::ACQUIRING: return 'A';
    default: return 'P';
  }
}

static inline const char* gps_state_name(FreqDiscipliner::DiscState s) {
  switch (s) {
    case FreqDiscipliner::DiscState::ACQUIRE: return "ACQUIRE";
    case FreqDiscipliner::DiscState::DISCIPLINED: return "DISCIPLINED";
    case FreqDiscipliner::DiscState::HOLDOVER: return "HOLDOVER";
    default: return "FREE_RUN";
  }
}


static inline const char* sample_class_name(PpsValidator::SampleClass c) {
  switch (c) {
    case PpsValidator::SampleClass::OK: return "OK";
    case PpsValidator::SampleClass::GAP: return "GAP";
    case PpsValidator::SampleClass::HARD_GLITCH: return "HARD";
    case PpsValidator::SampleClass::DUP: return "SOFT";
    default: return "SOFT";
  }
}

static inline const char* snap_reason_name(SnapReason reason) {
  switch (reason) {
    case SnapReason::HARD: return "HG";
    case SnapReason::BACKSTEP: return "BS";
    case SnapReason::E_BIG: return "E_BIG";
    default: return "NONE";
  }
}

static inline void gps_health_push(bool within, bool lock_ready, bool so, bool hg, bool gap, bool bs) {
  gpsHealthWindow[gpsHealthHead].within = within ? 1 : 0;
  gpsHealthWindow[gpsHealthHead].lock_ready = lock_ready ? 1 : 0;
  gpsHealthWindow[gpsHealthHead].so = so ? 1 : 0;
  gpsHealthWindow[gpsHealthHead].hg = hg ? 1 : 0;
  gpsHealthWindow[gpsHealthHead].gap = gap ? 1 : 0;
  gpsHealthWindow[gpsHealthHead].bs = bs ? 1 : 0;
  gpsHealthHead = (uint8_t)((gpsHealthHead + 1) % GPS_HEALTH_WINDOW_EDGES);
  if (gpsHealthFill < GPS_HEALTH_WINDOW_EDGES) gpsHealthFill++;
}

static inline void gps_diag_push(int32_t br) {
  gps_br_ring[gps_diag_head] = br;
  gps_diag_head = (uint8_t)((gps_diag_head + 1) % GPS_DIAG_RING_SIZE);
  if (gps_diag_fill < GPS_DIAG_RING_SIZE) gps_diag_fill++;
}

static uint32_t median_sorted_i32_abs(int32_t *vals, uint8_t n) {
  for (uint8_t i = 1; i < n; i++) {
    int32_t v = vals[i];
    uint8_t j = i;
    while (j > 0 && vals[j - 1] > v) {
      vals[j] = vals[j - 1];
      j--;
    }
    vals[j] = v;
  }
  int32_t med = vals[n / 2];
  return (uint32_t)(med >= 0 ? med : -med);
}

static void compute_diag_health(uint32_t &br_mad, uint32_t &br_max) {
  br_mad = 0;
  br_max = 0;
  if (gps_diag_fill == 0) return;

  for (uint8_t i = 0; i < gps_diag_fill; i++) {
    int32_t br = gps_br_ring[i];
    uint32_t abs_br = (uint32_t)(br >= 0 ? br : -br);
    gps_diag_scratch[i] = (int32_t)abs_br;
    if (abs_br > br_max) br_max = abs_br;
  }

  uint32_t med_abs = median_sorted_i32_abs(gps_diag_scratch, gps_diag_fill);

  for (uint8_t i = 0; i < gps_diag_fill; i++) {
    int32_t br = gps_br_ring[i];
    uint32_t a = (uint32_t)(br >= 0 ? br : -br);
    gps_diag_scratch[i] = (int32_t)((a > med_abs) ? (a - med_abs) : (med_abs - a));
  }
  br_mad = median_sorted_i32_abs(gps_diag_scratch, gps_diag_fill);
}

static void gps_snap_push(const GpsSnap &snap) {
#if ENABLE_STS_GPS_SNAP
  gpsSnapRing[gpsSnapHead] = snap;
  gpsSnapHead = (uint8_t)((gpsSnapHead + 1) % GPS_SNAP_RING_SIZE);
#else
  (void)snap;
#endif
}

static void emit_gps_line(const GpsSnap &snap,
                          bool hard_glitch,
                          bool backstep,
                          bool soft_outlier) {
  char* dbg = prepare_gps_debug_line_buf();
  int n = snprintf(dbg, CSV_PAYLOAD_MAX,
    "gps1,edge_seq=%lu,log_seq=%lu,pps=%lu,prime=%u,gap=%u,exp_valid=%u,exp_init=%u,steady=%u,w=%u,lr=%u,rr=%c,dc=%u,dm=%lu,exp=%lu,e=%ld,mad_e=%lu,soft_ticks=%lu",
    (unsigned long)snap.edge_seq,
    (unsigned long)snap.log_seq,
    (unsigned long)snap.pps,
    (unsigned int)snap.prime,
    (unsigned int)snap.gap,
    (unsigned int)snap.exp_valid,
    (unsigned int)snap.exp_init,
    (unsigned int)((snap.flags & 0x8) ? 1 : 0),
    (unsigned int)snap.within,
    (unsigned int)snap.lock_ready,
    snap.reset_reason,
    (unsigned int)snap.dc,
    (unsigned long)snap.d,
    (unsigned long)snap.exp,
    (long)snap.e,
    (unsigned long)snap.mad_e,
    (unsigned long)snap.soft_ticks);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);

  n = snprintf(dbg, CSV_PAYLOAD_MAX,
    "gps2,edge_seq=%lu,log_seq=%lu,hg=%u,bs=%u,so=%u,wrap=%lu,coh=%lu|%lu,lat=%u,cap=%u,cnt=%u",
    (unsigned long)snap.edge_seq,
    (unsigned long)snap.log_seq,
    (unsigned int)(hard_glitch ? 1 : 0),
    (unsigned int)(backstep ? 1 : 0),
    (unsigned int)(soft_outlier ? 1 : 0),
    (unsigned long)atomicRead32(tcb0WrapDetected),
    (unsigned long)atomicRead32(coherentOvfFlagSeenCount),
    (unsigned long)atomicRead32(coherentOvfAppliedCount),
    (unsigned int)snap.lat16,
    (unsigned int)snap.cap16,
    (unsigned int)snap.cnt);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);
}

static void emit_gps_health(GpsState state, uint32_t ppsRppm, uint32_t ppsJppm) {
  char* dbg = prepare_gps_debug_line_buf();
  uint16_t max_tcb0;
  uint16_t max_tcb1;
  uint16_t max_tcb2;
  uint16_t lat16_max;
  uint32_t lat16_wrap_risk;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    max_tcb0 = max_isr_tcb0_ticks;
    max_tcb1 = max_isr_tcb1_ticks;
    max_tcb2 = max_isr_tcb2_ticks;
    lat16_max = g_pps_latency16_max;
    lat16_wrap_risk = g_pps_latency16_wrapRisk;
  }
  uint32_t trunc_cnt = 0;
  uint8_t w_ok = 0;
  uint8_t lr_ok = 0;
  uint8_t so_cnt_w = 0;
  uint8_t hg_cnt_w = 0;
  uint8_t gap_cnt_w = 0;
  uint8_t bs_cnt_w = 0;
  for (uint8_t i = 0; i < gpsHealthFill; i++) {
    w_ok += gpsHealthWindow[i].within;
    lr_ok += gpsHealthWindow[i].lock_ready;
    so_cnt_w += gpsHealthWindow[i].so;
    hg_cnt_w += gpsHealthWindow[i].hg;
    gap_cnt_w += gpsHealthWindow[i].gap;
    bs_cnt_w += gpsHealthWindow[i].bs;
  }
  uint32_t br_mad = 0;
  uint32_t br_max = 0;
  compute_diag_health(br_mad, br_max);
#if ENABLE_METRICS
  trunc_cnt = atomicRead32(csvLineTrunc) + atomicRead32(stsPayloadTrunc);
#endif
  int n = snprintf(dbg, CSV_PAYLOAD_MAX,
    "gps_health1,t=%lu,st=%c,R=%u,J=%u,br_mad=%lu,br_max=%lu,w10=%u,lr10=%u,so10=%u,hg10=%u,gap10=%u,bs10=%u",
    (unsigned long)millis(),
    gps_state_char(state),
    (unsigned int)ppsRppm,
    (unsigned int)ppsJppm,
    (unsigned long)br_mad,
    (unsigned long)br_max,
    (unsigned int)w_ok,
    (unsigned int)lr_ok,
    (unsigned int)so_cnt_w,
    (unsigned int)hg_cnt_w,
    (unsigned int)gap_cnt_w,
    (unsigned int)bs_cnt_w);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);

  n = snprintf(dbg, CSV_PAYLOAD_MAX,
    "gps_health2,hg_cnt=%u,gap_cnt=%u,so_cnt=%u,warm_so_cnt=%u,bs_cnt=%u,edge_gap_cnt=%u,edge_seq=%lu,log_seq=%lu,trunc_cnt=%lu",
    (unsigned int)gps_hg_cnt,
    (unsigned int)gps_gap_cnt,
    (unsigned int)gps_so_cnt,
    (unsigned int)gps_warm_so_cnt,
    (unsigned int)gps_bs_cnt,
    (unsigned int)gps_edge_gap_cnt,
    (unsigned long)gps_edge_seq,
    (unsigned long)gps_log_seq,
    (unsigned long)trunc_cnt);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);

  n = snprintf(dbg, CSV_PAYLOAD_MAX,
    "gps_health3,maxisr_tcb0=%u,maxisr_tcb1=%u,maxisr_tcb2=%u,lat16_max=%u,lat16_wr=%lu",
    (unsigned int)max_tcb0,
    (unsigned int)max_tcb1,
    (unsigned int)max_tcb2,
    (unsigned int)lat16_max,
    (unsigned long)lat16_wrap_risk);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);
}

static void emit_gps_snap_dump() {
#if ENABLE_STS_GPS_SNAP
  if (!gps_dump_pending) return;
  if (gps_dump_trigger_edge_seq == gps_dump_last_emitted_edge_seq) {
    gps_dump_pending = false;
    gps_dump_reason = SnapReason::NONE;
    return;
  }

  char* hdr = prepare_gps_debug_line_buf();
  snprintf(hdr, CSV_PAYLOAD_MAX, "gps_snap_dump,reason=%s,pps=%lu", snap_reason_name(gps_dump_reason), (unsigned long)gps_dump_pps);
  sendStatus(StatusCode::ProgressUpdate, hdr);

  for (uint8_t i = 0; i < GPS_SNAP_RING_SIZE; i++) {
    uint8_t idx = (uint8_t)((gpsSnapHead + i) % GPS_SNAP_RING_SIZE);
    const GpsSnap &s = gpsSnapRing[idx];
    if (s.pps == 0) continue;
    char* line = prepare_gps_debug_line_buf();
    int n = snprintf(line, CSV_PAYLOAD_MAX,
      "gps_snap1,i=%u,edge_seq=%lu,pps=%lu,prime=%u,gap=%u,exp_valid=%u,exp_init=%u,w=%u,lr=%u,hg=%u,so=%u,dc=%u,dm=%lu,exp=%lu,e=%ld,mad_e=%lu",
      (unsigned int)i,
      (unsigned long)s.edge_seq,
      (unsigned long)s.pps,
      (unsigned int)s.prime,
      (unsigned int)s.gap,
      (unsigned int)s.exp_valid,
      (unsigned int)s.exp_init,
      (unsigned int)s.within,
      (unsigned int)s.lock_ready,
      (unsigned int)((s.flags & 0x1) ? 1 : 0),
      (unsigned int)((s.flags & 0x4) ? 1 : 0),
      (unsigned int)s.dc,
      (unsigned long)s.d,
      (unsigned long)s.exp,
      (long)s.e,
      (unsigned long)s.mad_e);
    if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);

    n = snprintf(line, CSV_PAYLOAD_MAX,
      "gps_snap2,i=%u,soft_ticks=%lu,now=%lu,ovf=%u,cap=%u,cnt=%u,fl=%02X,cls=%c,lat=%u",
      (unsigned int)i,
      (unsigned long)s.soft_ticks,
      (unsigned long)s.now32,
      (unsigned int)s.ovf,
      (unsigned int)s.cap16,
      (unsigned int)s.cnt,
      (unsigned int)s.flags,
      (s.cls == (uint8_t)SnapClass::HARD) ? 'H' : ((s.cls == (uint8_t)SnapClass::SOFT) ? 'S' : 'O'),
      (unsigned int)s.lat16);
    if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
  }
  gps_dump_pending = false;
  gps_dump_reason = SnapReason::NONE;
  gps_dump_last_emitted_edge_seq = gps_dump_trigger_edge_seq;
#endif
}
#endif

// Small helpers
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
static inline void droppedEvents_inc_isr() { droppedEvents++; }
static inline void ppsData_push_isr(uint32_t edge32,
                                    uint32_t now32,
                                    uint16_t ovf,
                                    uint16_t cap16,
                                    uint16_t cnt,
                                    uint16_t latency16) {
  uint8_t n = pps_mask(ppsHead + 1);
  if (n != ppsTail) {
    PpsCapture &slot = ppsBuffer[ppsHead];
    slot.edge32 = edge32;
    slot.now32 = now32;
    slot.ovf = ovf;
    slot.cap16 = cap16;
    slot.cnt = cnt;
    slot.latency16 = latency16;
    ppsHead = n;
  } else {
    droppedEvents_inc_isr();
  }
}

static inline PpsCapture ppsData_pop() {
  const uint8_t tail = ppsTail;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    ppsTail = pps_mask(tail + 1);
  }
  return ppsBuffer[tail];
}

static inline uint32_t ticks_to_us_pps_with_denom(uint32_t ticks, uint32_t denom) {
  return (uint32_t)(((uint64_t)ticks * 1000000ULL) / denom);
}

static inline uint32_t ticks_to_ns_pps_with_denom(uint32_t ticks, uint32_t denom) {
  return (uint32_t)(((uint64_t)ticks * 1000000000ULL) / denom);
}


// IR beam timing (tick/tock transitions)

static inline uint16_t read_TCB0_CNT() { return TCB0.CNT; }

static inline bool evbuf_available() { return ev_tail != ev_head; }

static inline void push_event(uint32_t ticks, uint8_t type) {
  uint8_t next = (uint8_t)(ev_head + 1) & (EVBUF_SIZE - 1);
  if (next != ev_tail) {
    evbuf[ev_head].ticks = ticks;
    evbuf[ev_head].type  = type;
    ev_head = next;
  } else {
    droppedEvents_inc_isr();
  }
}

static inline EdgeEvent pop_event() {
  const uint8_t tail = ev_tail;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    ev_tail = (uint8_t)(tail + 1) & (EVBUF_SIZE - 1);
  }
  return evbuf[tail];
}

static void process_edge_events() {
  static uint8_t  swing_state = 0;
  static uint32_t last_ts     = 0;
  static FullSwing curr;

  while (evbuf_available()) {
    EdgeEvent e = pop_event();

#if ENABLE_STS_GPS_DEBUG
    pend_edge_count++;
    pend_seq++;
    if (pend_prev_edge32_valid) {
      uint32_t d = elapsed32(e.ticks, pend_prev_edge32);
      if (e.ticks < pend_prev_edge32) {
        pend_backstep_count++;
        pend_last_bad_seq = pend_seq;
        pend_last_bad_delta = d;
      }
      if (d < MIN_EDGE_DELTA_TICKS) {
        pend_small_jump_count++;
        pend_last_bad_seq = pend_seq;
        pend_last_bad_delta = d;
      }
      if (d > MAX_EDGE_DELTA_TICKS) {
        pend_big_jump_count++;
        pend_last_bad_seq = pend_seq;
        pend_last_bad_delta = d;
      }
      uint32_t wrap_diff = (d > WRAP_TICKS) ? (d - WRAP_TICKS) : (WRAP_TICKS - d);
      if (wrap_diff <= WRAP_TOL_TICKS) {
        pend_wrapish_count++;
      }
    }
    pend_prev_edge32 = e.ticks;
    pend_prev_edge32_valid = true;
#endif

    switch (swing_state) {
      case 0: // wait for first rising edge to start swing exit in inverted sensor
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
          swing_state = 1; // start next swing with this rising edge
        }
        break;
    }
  }
}

// Coherent 32-bit timestamp from TCB0 {ovf_count, CNT}
static inline uint32_t tcb0_now_coherent_isr() {
  uint16_t ovf = tcb0Ovf;
  uint16_t cnt2 = read_TCB0_CNT();
  uint8_t intflags2 = TCB0.INTFLAGS;

  if (intflags2 & TCB_CAPT_bm) {
    coherentOvfFlagSeenCount++;
    ovf++;
    coherentOvfAppliedCount++;
    cnt2 = read_TCB0_CNT();
  }

  return ((uint32_t)ovf << 16) | (uint32_t)cnt2;
}

// 16-bit wrap-safe subtract
static inline uint16_t sub16(uint16_t a, uint16_t b) { return (uint16_t)(a - b); }

static inline int32_t round_to_65536(int32_t v) {
  // Deterministic nearest-multiple quantization; ties resolve away from zero.
  constexpr int32_t Q = 65536;
  constexpr int32_t H = Q / 2;
  if (v >= 0) return ((v + H) / Q) * Q;
  return ((v - H) / Q) * Q;
}

namespace Tunables {
  float     correctionJumpThresh = CORRECTION_JUMP_THRESHOLD; // compatibility-only (no-op in live discipliner path)

  // Back-compat alias to slow
  uint8_t   ppsEmaShift          = PPS_SLOW_SHIFT_DEFAULT;

  // New:
  uint8_t   ppsFastShift         = PPS_FAST_SHIFT_DEFAULT;
  uint8_t   ppsSlowShift         = PPS_SLOW_SHIFT_DEFAULT;
  uint8_t   ppsHampelWin         = PPS_HAMPEL_WIN_DEFAULT;   // compatibility-only (no-op); retained for CLI/EEPROM/status round-trip
  uint16_t  ppsHampelKx100       = PPS_HAMPEL_KX100_DEFAULT; // compatibility-only (no-op); retained for CLI/EEPROM/status round-trip
  bool      ppsMedian3           = PPS_MEDIAN3_DEFAULT;      // compatibility-only (no-op); retained for CLI/EEPROM/status round-trip
  uint16_t  ppsBlendLoPpm        = PPS_BLEND_LO_PPM_DEFAULT;
  uint16_t  ppsBlendHiPpm        = PPS_BLEND_HI_PPM_DEFAULT;
  uint16_t  ppsLockRppm          = PPS_LOCK_R_PPM_DEFAULT;
  uint16_t  ppsLockJppm          = PPS_LOCK_J_PPM_DEFAULT;
  uint16_t  ppsUnlockRppm        = PPS_UNLOCK_R_PPM_DEFAULT;
  uint16_t  ppsUnlockJppm        = PPS_UNLOCK_J_PPM_DEFAULT;
  uint8_t   ppsLockCount         = PPS_LOCK_COUNT_DEFAULT;
  uint8_t   ppsUnlockCount       = PPS_UNLOCK_COUNT_DEFAULT;
  uint16_t  ppsHoldoverMs        = PPS_HOLDOVER_MS_DEFAULT;

  DataUnits dataUnits            = DATA_UNITS_DEFAULT;
}
#if ENABLE_STS_GPS_DEBUG
static void emit_pending_gps_line() {
  if (!gps_have_pending_log) return;
  if (gps_pending_log.edge_seq == gps_last_logged_edge_seq) return;
  gps_log_seq++;
  gps_pending_log.log_seq = gps_log_seq;
  bool hg = (gps_pending_log.flags & 0x1) != 0;
  bool bs = (gps_pending_log.flags & 0x2) != 0;
  bool so = (gps_pending_log.flags & 0x4) != 0;
  emit_gps_line(gps_pending_log, hg, bs, so);
  gps_last_logged_edge_seq = gps_pending_log.edge_seq;
  gps_have_pending_log = false;
}
#endif

#if STS_DIAG > 0
static void emit_court_summary(uint32_t now_ms) {
  char* dbg = prepare_gps_debug_line_buf();
  uint32_t isr_cnt;
  uint32_t proc_cnt;
  uint32_t backlog_max;
  uint32_t gap65536;
  uint32_t gap131072;
  uint32_t gap_max;
  uint32_t coh_retry;
  uint32_t cli_max;
  uint32_t cli65536;
  uint32_t cli131072;
  uint32_t coh_seen;
  uint32_t coh_applied;
  uint32_t backstep;
  uint32_t hard_total;
  uint32_t gap_total_local;
  uint16_t isr0_max;
  uint16_t isr1_max;
  uint16_t isr2_max;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    isr_cnt = pps_isr_count;
    proc_cnt = pps_proc_count;
    backlog_max = pps_backlog_max;
    gap65536 = tcb0_gap_gt_65536_count;
    gap131072 = tcb0_gap_gt_131072_count;
    gap_max = tcb0_gap_max_ticks;
    coh_retry = coherentReadRetryCount;
    cli_max = cli_max_ticks;
    cli65536 = cli_gt_65536_count;
    cli131072 = cli_gt_131072_count;
    coh_seen = coherentOvfFlagSeenCount;
    coh_applied = coherentOvfAppliedCount;
    backstep = nowBackstepUnexpectedCount;
    hard_total = hard_glitch_total;
    gap_total_local = gap_total;
    isr0_max = max_isr_tcb0_ticks;
    isr1_max = max_isr_tcb1_ticks;
    isr2_max = max_isr_tcb2_ticks;
  }

  int n = snprintf(dbg, CSV_PAYLOAD_MAX,
    "court1,tms=%lu,pps_isr=%lu,pps_proc=%lu,pps_backlog_max=%lu,gap_total=%lu,hard_total=%lu,dup_sus=%lu,miss_sus=%lu",
    (unsigned long)now_ms,
    (unsigned long)isr_cnt,
    (unsigned long)proc_cnt,
    (unsigned long)backlog_max,
    (unsigned long)gap_total_local,
    (unsigned long)hard_total,
    (unsigned long)pps_dup_isr_suspect_count,
    (unsigned long)pps_missed_isr_suspect_count);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);

  n = snprintf(dbg, CSV_PAYLOAD_MAX,
    "court2,tms=%lu,cli_max=%lu,cli_gt65536=%lu,cli_gt131072=%lu,cli_unbal=%lu,coh_retry=%lu,coh_seen=%lu,coh_applied=%lu,backstep=%lu,isr0_max=%u,isr1_max=%u,isr2_max=%u",
    (unsigned long)now_ms,
    (unsigned long)cli_max,
    (unsigned long)cli65536,
    (unsigned long)cli131072,
    (unsigned long)cli_unbalanced_count,
    (unsigned long)coh_retry,
    (unsigned long)coh_seen,
    (unsigned long)coh_applied,
    (unsigned long)backstep,
    (unsigned int)isr0_max,
    (unsigned int)isr1_max,
    (unsigned int)isr2_max);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);

  n = snprintf(dbg, CSV_PAYLOAD_MAX,
    "court3,tms=%lu,gap65536=%lu,gap131072=%lu,gap_max=%lu,gap_hist=%lu|%lu|%lu|%lu|%lu|%lu",
    (unsigned long)now_ms,
    (unsigned long)gap65536,
    (unsigned long)gap131072,
    (unsigned long)gap_max,
    (unsigned long)gap_bin_lt_1000,
    (unsigned long)gap_bin_1000_50000,
    (unsigned long)gap_bin_50000_60000,
    (unsigned long)gap_bin_60000_65000,
    (unsigned long)gap_bin_65000_70000,
    (unsigned long)gap_bin_gt_70000);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);

  n = snprintf(dbg, CSV_PAYLOAD_MAX,
    "court4,tms=%lu,tcb0_gap_hist=%lu|%lu|%lu|%lu",
    (unsigned long)now_ms,
    (unsigned long)tcb0_gap_bin_le_65536,
    (unsigned long)tcb0_gap_bin_le_131072,
    (unsigned long)tcb0_gap_bin_le_196608,
    (unsigned long)tcb0_gap_bin_gt_196608);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);

  PpsValidator::SeedDiagnostics seed_diag = gPpsValidator.seedDiagnostics();
  n = snprintf(dbg, CSV_PAYLOAD_MAX,
    "court5,tms=%lu,seed_ph=%u,seed_cnt=%u,seed_cand=%lu,s_near1=%u,s_near2=%u,s_rst=%u,reseed=%u,ref_src=%u,n_ref=%lu",
    (unsigned long)now_ms,
    (unsigned int)seed_diag.startup_phase,
    (unsigned int)seed_diag.seed_count,
    (unsigned long)seed_diag.seed_candidate_ticks,
    (unsigned int)seed_diag.startup_near1x,
    (unsigned int)seed_diag.startup_near2x,
    (unsigned int)seed_diag.startup_resets,
    (unsigned int)seed_diag.recovery_reseeds,
    (unsigned int)seed_diag.source,
    (unsigned long)gPpsValidator.referenceTicks());
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);
}
#endif


#if PPS_TUNING_TELEMETRY
static constexpr uint8_t TUNE_WIN_SIZE = 60;
static uint32_t tune_r_samples[TUNE_WIN_SIZE] = {};
static uint32_t tune_j_samples[TUNE_WIN_SIZE] = {};
static uint8_t tune_win_fill = 0;
static uint8_t tune_ok_count = 0;
static uint8_t tune_val_count = 0;
static uint8_t tune_lock_pass_count = 0;
static uint8_t tune_unlock_breach_count = 0;

static inline const char* tune_state_name(FreqDiscipliner::DiscState s) {
  switch (s) {
    case FreqDiscipliner::DiscState::ACQUIRE: return "ACQUIRE";
    case FreqDiscipliner::DiscState::DISCIPLINED: return "DISCIPLINED";
    case FreqDiscipliner::DiscState::HOLDOVER: return "HOLDOVER";
    default: return "FREE_RUN";
  }
}

static uint32_t percentile_u32(uint32_t* values, uint8_t n, uint8_t percentile) {
  for (uint8_t i = 1; i < n; i++) {
    const uint32_t v = values[i];
    uint8_t j = i;
    while (j > 0 && values[j - 1] > v) {
      values[j] = values[j - 1];
      j--;
    }
    values[j] = v;
  }
  const uint8_t idx = (uint8_t)(((uint16_t)(n - 1U) * percentile + 99U) / 100U);
  return values[idx];
}

void emitPpsTuningConfigSnapshot() {
  char line[CSV_PAYLOAD_MAX];
  const int n = snprintf(line,
                         sizeof(line),
                         "TUNE_CFG,lockR=%u,lockJ=%u,lockN=%u,unlockR=%u,unlockJ=%u,unlockN=%u",
                         (unsigned int)Tunables::ppsLockRppmActive(),
                         (unsigned int)Tunables::ppsLockJppmActive(),
                         (unsigned int)Tunables::ppsLockCountActive(),
                         (unsigned int)Tunables::ppsUnlockRppmActive(),
                         (unsigned int)Tunables::ppsUnlockJppmActive(),
                         (unsigned int)Tunables::ppsUnlockCountActive());
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

static void emit_tune_window(FreqDiscipliner::DiscState state) {
  if (tune_win_fill == 0U) return;

  uint32_t r_sorted[TUNE_WIN_SIZE];
  uint32_t j_sorted[TUNE_WIN_SIZE];
  uint32_t r_max = tune_r_samples[0];
  uint32_t j_max = tune_j_samples[0];

  for (uint8_t i = 0; i < tune_win_fill; ++i) {
    const uint32_t rv = tune_r_samples[i];
    const uint32_t jv = tune_j_samples[i];
    r_sorted[i] = rv;
    j_sorted[i] = jv;
    if (rv > r_max) r_max = rv;
    if (jv > j_max) j_max = jv;
  }

  const uint32_t r95 = percentile_u32(r_sorted, tune_win_fill, 95U);
  const uint32_t j95 = percentile_u32(j_sorted, tune_win_fill, 95U);

  char line[CSV_PAYLOAD_MAX];
  const int n = snprintf(line,
                         sizeof(line),
                         "TUNE_WIN,state=%s,win=%u,ok=%u,val=%u,R95=%lu,Rmax=%lu,J95=%lu,Jmax=%lu,LP=%u,UB=%u",
                         tune_state_name(state),
                         (unsigned int)tune_win_fill,
                         (unsigned int)tune_ok_count,
                         (unsigned int)tune_val_count,
                         (unsigned long)r95,
                         (unsigned long)r_max,
                         (unsigned long)j95,
                         (unsigned long)j_max,
                         (unsigned int)tune_lock_pass_count,
                         (unsigned int)tune_unlock_breach_count);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);

  tune_win_fill = 0;
  tune_ok_count = 0;
  tune_val_count = 0;
  tune_lock_pass_count = 0;
  tune_unlock_breach_count = 0;
}

static void tune_push_sample(FreqDiscipliner::DiscState state,
                             PpsValidator::SampleClass cls,
                             bool pps_valid,
                             uint32_t r_ppm,
                             uint32_t j_ticks) {
  tune_r_samples[tune_win_fill] = r_ppm;
  tune_j_samples[tune_win_fill] = j_ticks;
  tune_win_fill++;

  if (cls == PpsValidator::SampleClass::OK) tune_ok_count++;
  if (pps_valid) tune_val_count++;

  const bool lock_pass = (r_ppm < FreqDiscipliner::lockFrequencyErrorThresholdPpm()) &&
                         (j_ticks < FreqDiscipliner::lockMadThresholdTicks());
  if (lock_pass) tune_lock_pass_count++;

  const bool unlock_breach = (r_ppm > (uint32_t)Tunables::ppsUnlockRppmActive()) ||
                             (j_ticks > (uint32_t)Tunables::ppsUnlockJppmActive());
  if (unlock_breach) tune_unlock_breach_count++;

  if (tune_win_fill >= TUNE_WIN_SIZE) emit_tune_window(state);
}

static void emit_tune_event(FreqDiscipliner::DiscState from,
                            FreqDiscipliner::DiscState to,
                            uint32_t now_ms,
                            uint32_t r_ppm,
                            uint32_t j_ticks,
                            uint8_t streak) {
  char line[CSV_PAYLOAD_MAX];
  const int n = snprintf(line,
                         sizeof(line),
                         "TUNE_EVT,from=%s,to=%s,t=%lu,R=%lu,J=%lu,streak=%u",
                         tune_state_name(from),
                         tune_state_name(to),
                         (unsigned long)(now_ms / 1000UL),
                         (unsigned long)r_ppm,
                         (unsigned long)j_ticks,
                         (unsigned int)streak);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}
#else
void emitPpsTuningConfigSnapshot() {}
#endif

static void emit_sts_build_header() {
  char* line = prepare_gps_debug_line_buf();
  int n = snprintf(line,
                   CSV_PAYLOAD_MAX,
                   "build,git=%s,dirty=%s,utc=%s,board=NanoEvery,mcu=ATmega4809,f_cpu=%lu,baud=%lu,cfg_ver=%u",
                   GIT_SHA,
                   StsHeader::dirtyField(),
                   BUILD_UTC,
                   (unsigned long)F_CPU,
                   (unsigned long)SERIAL_BAUD_NANO,
                   (unsigned int)StsHeader::CFG_SCHEMA_VER);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

static void emit_sts_schema_header() {
  char* line = prepare_gps_debug_line_buf();
#if ENABLE_STS_GPS_DEBUG
  #if ENABLE_STS_GPS_SNAP
  int n = snprintf(line, CSV_PAYLOAD_MAX, "schema,gps=%u,gps_health=%u,gps_snap=%u,court=%u",
                   (unsigned int)StsHeader::GPS_SCHEMA_VER,
                   (unsigned int)StsHeader::GPS_HEALTH_SCHEMA_VER,
                   (unsigned int)StsHeader::GPS_SNAP_SCHEMA_VER,
                   (unsigned int)StsHeader::COURT_SCHEMA_VER);
  #else
  int n = snprintf(line, CSV_PAYLOAD_MAX, "schema,gps=%u,gps_health=%u,court=%u",
                   (unsigned int)StsHeader::GPS_SCHEMA_VER,
                   (unsigned int)StsHeader::GPS_HEALTH_SCHEMA_VER,
                   (unsigned int)StsHeader::COURT_SCHEMA_VER);
  #endif
#else
  int n = snprintf(line, CSV_PAYLOAD_MAX, "schema");
#endif
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

static void emit_sts_flags_header() {
  char* line = prepare_gps_debug_line_buf();
  int n = snprintf(line,
                   CSV_PAYLOAD_MAX,
                   "flags,sts_verbose=%u,gps_dbg_verbose=%u,crlf=%u,evsys_pps=%u,evsys_ir=%u,median3=%u,sts_diag=%u",
                   (unsigned int)StsHeader::FLAG_STS_VERBOSE,
                   (unsigned int)StsHeader::FLAG_GPS_DBG_VERBOSE,
                   (unsigned int)StsHeader::FLAG_CRLF,
                   (unsigned int)StsHeader::FLAG_EVSYS_PPS,
                   (unsigned int)StsHeader::FLAG_EVSYS_IR,
                   (unsigned int)StsHeader::FLAG_MEDIAN3_DEFAULT,
                   (unsigned int)StsHeader::FLAG_STS_DIAG);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

static void emit_sts_tunables_header() {
  // All discipliner tunables are emitted from normalized active values.
  char* line = prepare_gps_debug_line_buf();
  int n = snprintf(line,
                   CSV_PAYLOAD_MAX,
                   "tun1,fastShift=%u,slowShift=%u,blendLo=%u,blendHi=%u,lockRppm=%u,lockMadTicks=%u,lockN=%u,unlockRppm=%u,unlockMadTicks=%u,unlockN=%u,holdoverMs=%u,jump_ppm=%lu,hardTicks=%lu",
                   (unsigned int)Tunables::ppsFastShiftActive(),
                   (unsigned int)Tunables::ppsSlowShiftActive(),
                   (unsigned int)Tunables::ppsBlendLoPpmActive(),
                   (unsigned int)Tunables::ppsBlendHiPpmActive(),
                   (unsigned int)FreqDiscipliner::lockFrequencyErrorThresholdPpm(),
                   (unsigned int)FreqDiscipliner::lockMadThresholdTicks(),
                   (unsigned int)FreqDiscipliner::lockConsecutiveGoodSamplesRequired(),
                   (unsigned int)Tunables::ppsUnlockRppmActive(),
                   (unsigned int)Tunables::ppsUnlockJppmActive(),
                   (unsigned int)Tunables::ppsUnlockCountActive(),
                   (unsigned int)Tunables::ppsHoldoverMsActive(),
                   (unsigned long)ppm_from_frac(Tunables::correctionJumpThresh),
                   (unsigned long)PpsValidator::kHardTicks());
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);

  n = snprintf(line,
               CSV_PAYLOAD_MAX,
               "tun2,k_valid=%u,r_disc_ppm=%u,mad_disc_ticks=%u,br_note=dt16-vs-dt32_lo16",
               (unsigned int)PpsValidator::kValid(),
               (unsigned int)FreqDiscipliner::lockFrequencyErrorThresholdPpm(),
               (unsigned int)FreqDiscipliner::lockMadThresholdTicks());
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

static void emit_sts_pps_cfg() {
  char* line = prepare_gps_debug_line_buf();
  // PPS validator domain is full 32-bit ticks-per-second intervals.
  const uint32_t ref = (uint32_t)F_CPU;
  // Always source from PpsValidator helpers to keep reporting drift-proof.
  const uint32_t min_ticks = PpsValidator::minOkTicks(ref);
  const uint32_t max_ticks = PpsValidator::maxOkTicks(ref);
  int n = snprintf(line,
                   CSV_PAYLOAD_MAX,
                   "pps_cfg,ref=%lu,min=%lu,max=%lu,seed_n2_max10=%u,seed_cons100=%u,seed_need=%u,reseed_need=%u,hard_ticks=%lu,dup_num10=%u,ok_min_num10=%u,ok_max_num10=%u,gap_num10=%u,ratio_den10=%u",
                   (unsigned long)ref,
                   (unsigned long)min_ticks,
                   (unsigned long)max_ticks,
                   (unsigned int)PpsValidator::seedNear2xMaxNum10(),
                   (unsigned int)PpsValidator::seedConsistencyNum100(),
                   (unsigned int)PpsValidator::startupSeedRequired(),
                   (unsigned int)PpsValidator::recoverySeedRequired(),
                   (unsigned long)PpsValidator::kHardTicks(),
                   (unsigned int)PpsValidator::dupNum10(),
                   (unsigned int)PpsValidator::okMinNum10(),
                   (unsigned int)PpsValidator::okMaxNum10(),
                   (unsigned int)PpsValidator::gapNum10(),
                   (unsigned int)PpsValidator::ratioDen10());
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

static bool sts_pps_cfg_reemit_pending = false;

static void emitStsHeader() {
  emit_sts_build_header();
  emit_sts_schema_header();
  emit_sts_flags_header();
  emit_sts_tunables_header();
  emit_sts_pps_cfg();
  sts_pps_cfg_reemit_pending = true;
}

static void emitResetCause() {
  const uint8_t rstfr = RSTCTRL.RSTFR;
  char flags[48];
  size_t pos = 0;

  auto append_flag = [&](const char* flag) {
    if (pos >= sizeof(flags) - 1) return;
    if (pos != 0 && pos < sizeof(flags) - 1) flags[pos++] = '|';
    for (size_t i = 0; flag[i] != '\0' && pos < sizeof(flags) - 1; ++i) {
      flags[pos++] = flag[i];
    }
    flags[pos] = '\0';
  };

  flags[0] = '\0';
  if (rstfr & RSTCTRL_PORF_bm) append_flag("PORF");
  if (rstfr & RSTCTRL_BORF_bm) append_flag("BORF");
  if (rstfr & RSTCTRL_EXTRF_bm) append_flag("EXTRF");
  if (rstfr & RSTCTRL_WDRF_bm) append_flag("WDRF");
  if (rstfr & RSTCTRL_SWRF_bm) append_flag("SWRF");
  if (rstfr & RSTCTRL_UPDIRF_bm) append_flag("UPDIRF");
  if (pos == 0) append_flag("NONE");

  char line[96];
  const int n = snprintf(line, sizeof(line), "rstfr,raw=0x%02X,flags=%s", (unsigned int)rstfr, flags);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);

  // Clear only the currently latched reset-cause bits (write-1-to-clear).
  RSTCTRL.RSTFR = rstfr;
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

  emitResetCause();

  sendStatus(StatusCode::ProgressUpdate, "Begin setup() ...");

  TunableConfig cfg;
  //XXX Priming for EEPROM
  //XXX saveConfig(cfg);saveConfig(cfg);
  if (loadConfig(cfg)) applyConfig(cfg);

  gPpsValidator.reset();
  gFreqDiscipliner.reset((uint32_t)F_CPU);
  gDisciplinedTime.begin((uint32_t)F_CPU);
  g_pps_latency16_max = 0;
  g_pps_latency16_wrapRisk = 0;

  emitStsHeader();
#if PPS_TUNING_TELEMETRY
  emitPpsTuningConfigSnapshot();
#endif

#if STS_DIAG > 0
  const uint32_t cli_start_now32 = 0;
#endif
  cli();
#if STS_DIAG > 0
  DIAG_CLI(DIAG_CLI_TAG_SETUP_INIT, cli_start_now32);
#endif
  evsys_init();
  tcb0_init_free_running();
  tcb1_init_IR_capt();
  tcb2_init_PPS_capt();
#if STS_DIAG > 0
  const uint32_t sei_now32 = tcb0_now_coherent_isr();
  DIAG_SEI(DIAG_CLI_TAG_SETUP_INIT, sei_now32);
#endif
  sei();

#if ENABLE_STS_GPS_DEBUG
  char clk_line[96];
  snprintf(clk_line, sizeof(clk_line), "tcb_clk,t0=%02X,t1=%02X,t2=%02X", (unsigned int)TCB0.CTRLA, (unsigned int)TCB1.CTRLA, (unsigned int)TCB2.CTRLA);
  sendStatus(StatusCode::ProgressUpdate, clk_line);
#endif

  sendStatus(StatusCode::ProgressUpdate, "... end setup()");
  printCsvHeader();
}

static void process_pps() {
  static bool pps_primed = false;
  static uint32_t pps_seen_prev = 0;
  static uint32_t last_pps_seen_change_ms = 0;
  static uint32_t last_health_ms = 0;
#if STS_DIAG > 0
  static uint32_t last_court_ms = 0;
  static uint32_t last_pps_isr_count = 0;
  static uint32_t last_pps_isr_change_ms = 0;
#endif
  static uint32_t pps_last_edge32 = 0;
  static uint16_t lastPpsCap16 = 0;
  static uint32_t last_edge_seq = 0;
  static uint32_t edge_seq = 0;
  static constexpr int32_t BR_MAX = 200;
  static bool warned_f_hat_suspicious = false;

  const uint32_t now_ms = millis();
  uint32_t pps_seen_now = atomicRead32(pps_seen);
#if STS_DIAG > 0
  uint32_t pps_isr_now = atomicRead32(pps_isr_count);
  if (pps_isr_now != last_pps_isr_count) {
    last_pps_isr_count = pps_isr_now;
    last_pps_isr_change_ms = now_ms;
  } else if ((uint32_t)(now_ms - last_pps_isr_change_ms) > 1500UL) {
    pps_missed_isr_suspect_count++;
    last_pps_isr_change_ms = now_ms;
  }
#endif
  if (pps_seen_now != pps_seen_prev) {
    pps_seen_prev = pps_seen_now;
    last_pps_seen_change_ms = now_ms;
  }

  if ((uint32_t)(now_ms - last_pps_seen_change_ms) > 1500UL) {
    gPpsValidator.reset();
    gFreqDiscipliner.observe(PpsValidator::SampleClass::GAP, false, (uint32_t)F_CPU, now_ms, true);
    gDisciplinedTime.sync(gFreqDiscipliner, false);
  }

  // Re-emit pps_cfg exactly once from the runtime path after startup settles,
  // so the configuration survives cases where the boot burst is missed.
  if (sts_pps_cfg_reemit_pending && now_ms >= 2000UL) {
    emit_sts_pps_cfg();
    sts_pps_cfg_reemit_pending = false;
  }

#if STS_DIAG > 0
  if ((uint32_t)(now_ms - last_court_ms) >= STS_DIAG_COURT_PERIOD_MS) {
    last_court_ms = now_ms;
    emit_court_summary(now_ms);
  }
#endif

  while (ppsData_available()) {
    PpsCapture cap = ppsData_pop();
#if STS_DIAG > 0
    pps_proc_count++;
    const uint32_t isr_cnt = atomicRead32(pps_isr_count);
    const uint32_t backlog = (isr_cnt >= pps_proc_count) ? (isr_cnt - pps_proc_count) : 0;
    if (backlog > pps_backlog_max) pps_backlog_max = backlog;
    diag_tcb0_gap_record(cap.now32);
#endif
    uint32_t t = cap.edge32;
    edge_seq++;
    bool edge_seq_gap = (last_edge_seq != 0 && edge_seq != (last_edge_seq + 1));
    if (edge_seq_gap && gps_edge_gap_cnt < 0xFFFF) gps_edge_gap_cnt++;
    last_edge_seq = edge_seq;

    if (!pps_primed) {
#if ENABLE_STS_GPS_DEBUG
      {
        char* dbg = prepare_gps_debug_line_buf();
        int n = snprintf(dbg, CSV_PAYLOAD_MAX,
                         "gps_decision,ms=%lu,state=%s,pps_valid=%u,cls=SOFT,lockN=%u,dt32=0,dt16=%u,R_ppm=%lu,J=%lu,reason=NOT_PRIMED",
                         (unsigned long)now_ms,
                         gps_state_name(gFreqDiscipliner.state()),
                         (unsigned int)(gPpsValidator.isValid() ? 1 : 0),
                         (unsigned int)gPpsValidator.okStreak(),
                         (unsigned int)cap.latency16,
                         (unsigned long)gFreqDiscipliner.rPpm(),
                         (unsigned long)gFreqDiscipliner.madTicks());
        if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);
      }
#endif
      pps_last_edge32 = t;
      pps_primed = true;
      lastPpsCapture = t;
      lastPpsCap16 = cap.cap16;
      continue;
    }

    // Two distinct PPS interval domains:
    // - pps_dt32_ticks: full 32-bit elapsed ticks across PPS edges (~16,000,000 @ 16 MHz),
    //                   used as the primary validator/discipliner input
    // - pps_dt16_mod:   16-bit capture modulo delta, retained only as compact timing telemetry
    const uint32_t prev_edge32 = pps_last_edge32;
    const uint16_t prev_cap16 = lastPpsCap16;
    uint32_t pps_dt32_ticks = elapsed32(t, prev_edge32);
    uint16_t pps_dt16_mod = sub16(cap.cap16, prev_cap16);
    int32_t br = (int32_t)(pps_dt32_ticks & 0xFFFFU) - (int32_t)pps_dt16_mod;

    pps_last_edge32 = t;
    lastPpsCapture = t;
    lastPpsCap16 = cap.cap16;

    PpsValidator::SampleClass cls = gPpsValidator.classify(pps_dt32_ticks, false);
#if STS_DIAG > 0
    if (pps_dt16_mod < 1000U) pps_dup_isr_suspect_count++;
    if (cls == PpsValidator::SampleClass::GAP) {
      const uint32_t dt16_u32 = (uint32_t)pps_dt16_mod;
      gap_total++;
      if (dt16_u32 < 1000UL) gap_bin_lt_1000++;
      else if (dt16_u32 < 50000UL) gap_bin_1000_50000++;
      else if (dt16_u32 < 60000UL) gap_bin_50000_60000++;
      else if (dt16_u32 < 65000UL) gap_bin_60000_65000++;
      else if (dt16_u32 <= 70000UL) gap_bin_65000_70000++;
      else gap_bin_gt_70000++;
    } else if (cls == PpsValidator::SampleClass::HARD_GLITCH) {
      hard_glitch_total++;
    }
#endif
    gPpsValidator.observe(cls, pps_dt32_ticks, now_ms);

    const bool pps_valid = gPpsValidator.isValid();
    const bool anomaly = (cls != PpsValidator::SampleClass::OK);
    const FreqDiscipliner::DiscState prev_disc_state = gFreqDiscipliner.state();
    gFreqDiscipliner.observe(cls, pps_valid, pps_dt32_ticks, now_ms, anomaly);
    gDisciplinedTime.sync(gFreqDiscipliner, pps_valid);

    pps_edge_seq = edge_seq;
    pps_delta_inst = pps_dt32_ticks;
    pps_delta_fast = gFreqDiscipliner.fast();
    pps_delta_slow = gFreqDiscipliner.slow();
    pps_delta_active = gDisciplinedTime.ticksPerSecond();
    pps_R_ppm = gFreqDiscipliner.rPpm();
    pps_J_ppm = gFreqDiscipliner.madTicks();

#if PPS_TUNING_TELEMETRY
    tune_push_sample(gFreqDiscipliner.state(), cls, pps_valid, pps_R_ppm, pps_J_ppm);
    const FreqDiscipliner::DiscState curr_disc_state = gFreqDiscipliner.state();
    if (curr_disc_state != prev_disc_state) {
      uint8_t streak = gFreqDiscipliner.transitionStreak();
      if (streak == 0U) {
        streak = (curr_disc_state == FreqDiscipliner::DiscState::DISCIPLINED) ?
                 gFreqDiscipliner.lockStreak() :
                 gFreqDiscipliner.unlockStreak();
      }
      emit_tune_event(prev_disc_state, curr_disc_state, now_ms, pps_R_ppm, pps_J_ppm, streak);
    }
#endif

    corrInst = (float)F_CPU / (float)(pps_dt32_ticks ? pps_dt32_ticks : (uint32_t)F_CPU);

    switch (gFreqDiscipliner.state()) {
      case FreqDiscipliner::DiscState::DISCIPLINED: gpsStatus = GpsStatus::LOCKED; gpsState = GpsState::LOCKED; break;
      case FreqDiscipliner::DiscState::ACQUIRE: gpsStatus = GpsStatus::ACQUIRING; gpsState = GpsState::ACQUIRING; break;
      case FreqDiscipliner::DiscState::HOLDOVER: gpsStatus = GpsStatus::HOLDOVER; gpsState = GpsState::HOLDOVER; break;
      default: gpsStatus = GpsStatus::NO_PPS; gpsState = GpsState::NO_PPS; break;
    }

#if ENABLE_STS_GPS_DEBUG
    const bool dt32_in_range = (pps_dt32_ticks >= 8000000UL) && (pps_dt32_ticks <= 32000000UL);
    const bool holdover_stale = (gFreqDiscipliner.state() == FreqDiscipliner::DiscState::HOLDOVER) &&
                                (gFreqDiscipliner.holdoverAgeMs() > (uint32_t)Tunables::ppsHoldoverMs);
    const bool r_too_high = gFreqDiscipliner.rPpm() > FreqDiscipliner::lockFrequencyErrorThresholdPpm();
    const bool j_too_high = gFreqDiscipliner.madTicks() > FreqDiscipliner::lockMadThresholdTicks();

    const char* reason = "OK";
    if (cls == PpsValidator::SampleClass::GAP) reason = "GAP";
    else if (cls == PpsValidator::SampleClass::HARD_GLITCH) reason = "HARD_GLITCH";
    else if (!dt32_in_range) reason = "DT32_RANGE";
    else if (cls == PpsValidator::SampleClass::DUP) reason = "DUP";
    else if (r_too_high) reason = "R_TOO_HIGH";
    else if (j_too_high) reason = "J_TOO_HIGH";
    else if (holdover_stale) reason = "HOLDOVER_STALE";

    {
      char* dbg = prepare_gps_debug_line_buf();
      int n = snprintf(dbg, CSV_PAYLOAD_MAX,
                       "gps_decision,ms=%lu,state=%s,pps_valid=%u,cls=%s,lockN=%u,dt32=%lu,dt16=%u,R_ppm=%lu,J=%lu,reason=%s",
                       (unsigned long)now_ms,
                       gps_state_name(gFreqDiscipliner.state()),
                       (unsigned int)(gPpsValidator.isValid() ? 1 : 0),
                       sample_class_name(cls),
                       (unsigned int)gPpsValidator.okStreak(),
                       (unsigned long)pps_dt32_ticks,
                       (unsigned int)cap.latency16,
                       (unsigned long)gFreqDiscipliner.rPpm(),
                       (unsigned long)gFreqDiscipliner.madTicks(),
                       reason);
      if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);
    }

#if STS_DIAG > 1
    if (cls == PpsValidator::SampleClass::GAP || cls == PpsValidator::SampleClass::HARD_GLITCH) {
      char* gapdbg = prepare_gps_debug_line_buf();
      int gn = snprintf(gapdbg, CSV_PAYLOAD_MAX,
                        "gap_evt,tms=%lu,cls=%s,cap_prev=%u,cap_cur=%u,dc=%u,dm=%lu,lat=%u,cnt2=%u,edge_seq=%lu",
                        (unsigned long)now_ms,
                        sample_class_name(cls),
                        (unsigned int)prev_cap16,
                        (unsigned int)cap.cap16,
                        (unsigned int)pps_dt16_mod,
                        (unsigned long)pps_dt32_ticks,
                        (unsigned int)cap.latency16,
                        (unsigned int)cap.cnt,
                        (unsigned long)edge_seq);
      if (gn > 0) sendStatus(StatusCode::ProgressUpdate, gapdbg);
    }
#endif

    if (!warned_f_hat_suspicious) {
      const uint32_t f_hat = gDisciplinedTime.ticksPerSecond();
      if (dt32_in_range && (f_hat < 1000000UL)) {
        char warn[112];
        snprintf(warn, sizeof(warn), "WARN,f_hat_suspicious,dt32=%lu,f_hat=%lu",
                 (unsigned long)pps_dt32_ticks, (unsigned long)f_hat);
        sendStatus(StatusCode::ProgressUpdate, warn);
        warned_f_hat_suspicious = true;
      }
    }
#endif

#if ENABLE_STS_GPS_DEBUG
    gps_diag_push(br);
    GpsSnap snap{};
    snap.edge_seq = edge_seq;
    snap.pps = pps_seen_now;
    snap.d = pps_dt32_ticks;
    snap.dc = pps_dt16_mod;
    snap.now32 = cap.now32;
    snap.ovf = cap.ovf;
    snap.cap16 = cap.cap16;
    snap.cnt = cap.cnt;
    snap.lat16 = cap.latency16;
    snap.gap = (cls == PpsValidator::SampleClass::GAP) ? 1 : 0;
    snap.flags = 0;
    if (cls == PpsValidator::SampleClass::HARD_GLITCH) snap.flags |= 0x1;
    if (edge_seq_gap) snap.flags |= 0x2;
    if (cls == PpsValidator::SampleClass::DUP) snap.flags |= 0x4;
    snap.within = (cls == PpsValidator::SampleClass::OK) ? 1 : 0;
    snap.lock_ready = (gFreqDiscipliner.state() == FreqDiscipliner::DiscState::DISCIPLINED) ? 1 : 0;
    snap.cls = (cls == PpsValidator::SampleClass::OK)
      ? (uint8_t)SnapClass::OK
      : (cls == PpsValidator::SampleClass::HARD_GLITCH ? (uint8_t)SnapClass::HARD : (uint8_t)SnapClass::SOFT);
    gps_health_push(snap.within != 0,
                    snap.lock_ready != 0,
                    cls == PpsValidator::SampleClass::DUP,
                    cls == PpsValidator::SampleClass::HARD_GLITCH,
                    cls == PpsValidator::SampleClass::GAP,
                    edge_seq_gap);
    snap.reset_reason = edge_seq_gap ? 'E' : '-';
    gps_pending_log = snap;
    gps_have_pending_log = true;
    gps_snap_push(snap);
    if (cls == PpsValidator::SampleClass::HARD_GLITCH || cls == PpsValidator::SampleClass::GAP) {
      gps_dump_pending = true;
      gps_dump_pps = pps_seen_now;
      gps_dump_trigger_edge_seq = edge_seq;
      gps_dump_reason = (cls == PpsValidator::SampleClass::GAP ? SnapReason::E_BIG : SnapReason::HARD);
    }

    if ((uint32_t)(now_ms - last_health_ms) >= GPS_HEALTH_PERIOD_MS) {
      last_health_ms = now_ms;
      PpsValidator::Health h = gPpsValidator.health();
      uint16_t tcb0_ovf_now;
      uint32_t tcb0_wraps_now;
      uint32_t coh_seen_now;
      uint32_t coh_bump_now;
      uint8_t capt_pending;
      ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        tcb0_ovf_now = tcb0Ovf;
        tcb0_wraps_now = tcb0WrapDetected;
        coh_seen_now = coherentOvfFlagSeenCount;
        coh_bump_now = coherentOvfAppliedCount;
        capt_pending = (TCB0.INTFLAGS & TCB_CAPT_bm) ? 1 : 0;
      }
      PpsValidator::SeedDiagnostics seed_diag = gPpsValidator.seedDiagnostics();
      char* dbg = prepare_gps_debug_line_buf();
      int n = snprintf(dbg, CSV_PAYLOAD_MAX,
                       "gps_health_d1,edge_seq=%lu,ok=%u,gap=%u,dup=%u,hg=%u,pps_valid=%u,disc=%u,f_fast=%lu,f_slow=%lu,f_hat=%lu,R_ppm=%lu,mad_ticks=%lu,hold_ms=%lu",
                       (unsigned long)edge_seq,
                       (unsigned int)h.ok,
                       (unsigned int)h.gap,
                       (unsigned int)h.dup,
                       (unsigned int)h.hg,
                       (unsigned int)(gPpsValidator.isValid() ? 1 : 0),
                       (unsigned int)gFreqDiscipliner.state(),
                       (unsigned long)gFreqDiscipliner.fast(),
                       (unsigned long)gFreqDiscipliner.slow(),
                       (unsigned long)gDisciplinedTime.ticksPerSecond(),
                       (unsigned long)gFreqDiscipliner.rPpm(),
                       (unsigned long)gFreqDiscipliner.madTicks(),
                       (unsigned long)gFreqDiscipliner.holdoverAgeMs());
      if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);

      n = snprintf(dbg, CSV_PAYLOAD_MAX,
                   "gps_health_d2,edge_seq=%lu,dt16=%u,dt32=%lu,br=%ld,lat16_max=%u,lat16_wr=%lu,seed_ph=%u,seed_cnt=%u,seed_cand=%lu,s_near1=%u,s_near2=%u,s_rst=%u,reseed=%u,ref_src=%u",
                   (unsigned long)edge_seq,
                   (unsigned int)pps_dt16_mod,
                   (unsigned long)pps_dt32_ticks,
                   (long)br,
                       (unsigned int)g_pps_latency16_max,
                       (unsigned long)g_pps_latency16_wrapRisk,
                       (unsigned int)seed_diag.startup_phase,
                       (unsigned int)seed_diag.seed_count,
                       (unsigned long)seed_diag.seed_candidate_ticks,
                       (unsigned int)seed_diag.startup_near1x,
                       (unsigned int)seed_diag.startup_near2x,
                       (unsigned int)seed_diag.startup_resets,
                       (unsigned int)seed_diag.recovery_reseeds,
                       (unsigned int)seed_diag.source);
      if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);

      n = snprintf(dbg, CSV_PAYLOAD_MAX,
                   "tcb0_health,ms=%lu,ovf=%u,wraps=%lu,capt_pending=%u,coh_capt_seen=%lu,coh_bump=%lu,coh_retry=%lu,isr0_last=%u,isr1_last=%u,isr2_last=%u,isr0_max=%u,isr1_max=%u,isr2_max=%u",
                   (unsigned long)now_ms,
                   (unsigned int)tcb0_ovf_now,
                   (unsigned long)tcb0_wraps_now,
                   (unsigned int)capt_pending,
                   (unsigned long)coh_seen_now,
                   (unsigned long)coh_bump_now,
                   (unsigned long)coherentReadRetryCount,
                   (unsigned int)isr_last_tcb0_ticks,
                   (unsigned int)isr_last_tcb1_ticks,
                   (unsigned int)isr_last_tcb2_ticks,
                   (unsigned int)max_isr_tcb0_ticks,
                   (unsigned int)max_isr_tcb1_ticks,
                   (unsigned int)max_isr_tcb2_ticks);
      if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);

      n = snprintf(dbg, CSV_PAYLOAD_MAX,
                   "pend_health,ms=%lu,edges=%lu,dropped=%lu,back=%lu,big=%lu,small=%lu,wrapish=%lu,last_bad_seq=%lu,last_bad_d=%lu",
                   (unsigned long)now_ms,
                   (unsigned long)pend_edge_count,
                   (unsigned long)atomicRead32(droppedEvents),
                   (unsigned long)pend_backstep_count,
                   (unsigned long)pend_big_jump_count,
                   (unsigned long)pend_small_jump_count,
                   (unsigned long)pend_wrapish_count,
                   (unsigned long)pend_last_bad_seq,
                   (unsigned long)pend_last_bad_delta);
      if (n > 0) sendStatus(StatusCode::ProgressUpdate, dbg);

      int n_int = snprintf(dbg, CSV_PAYLOAD_MAX,
                           "pps_int,dt32=%lu,dt16=%u,f_hat=%lu",
                           (unsigned long)pps_dt32_ticks,
                           (unsigned int)pps_dt16_mod,
                           (unsigned long)gDisciplinedTime.ticksPerSecond());
      if (n_int > 0) sendStatus(StatusCode::ProgressUpdate, dbg);
      emit_gps_health(gpsState, pps_R_ppm, pps_J_ppm);
    }
#endif
  }
}

void pendulumLoop() {
  processSerialCommands();
  process_pps();
#if ENABLE_STS_GPS_DEBUG
  emit_pending_gps_line();
  emit_gps_snap_dump();
#endif
  process_edge_events();

  while (swing_available()) {
    FullSwing fs = swing_pop();
    const uint32_t ticks_per_second = gDisciplinedTime.ticksPerSecond();

    PendulumSample sample{};
    switch (Tunables::dataUnits) {
      case DataUnits::RawCycles:
        sample.tick       = fs.tick;
        sample.tock       = fs.tock;
        sample.tick_block = fs.tick_block;
        sample.tock_block = fs.tock_block;
        break;
      case DataUnits::AdjustedNs:
        sample.tick       = ticks_to_ns_pps_with_denom(fs.tick, ticks_per_second);
        sample.tock       = ticks_to_ns_pps_with_denom(fs.tock, ticks_per_second);
        sample.tick_block = ticks_to_ns_pps_with_denom(fs.tick_block, ticks_per_second);
        sample.tock_block = ticks_to_ns_pps_with_denom(fs.tock_block, ticks_per_second);
        break;
      case DataUnits::AdjustedUs:
        sample.tick       = ticks_to_us_pps_with_denom(fs.tick, ticks_per_second);
        sample.tock       = ticks_to_us_pps_with_denom(fs.tock, ticks_per_second);
        sample.tick_block = ticks_to_us_pps_with_denom(fs.tick_block, ticks_per_second);
        sample.tock_block = ticks_to_us_pps_with_denom(fs.tock_block, ticks_per_second);
        break;
      case DataUnits::AdjustedMs:
        sample.tick       = ticks_to_us_pps_with_denom(fs.tick, ticks_per_second) / 1000;
        sample.tock       = ticks_to_us_pps_with_denom(fs.tock, ticks_per_second) / 1000;
        sample.tick_block = ticks_to_us_pps_with_denom(fs.tick_block, ticks_per_second) / 1000;
        sample.tock_block = ticks_to_us_pps_with_denom(fs.tock_block, ticks_per_second) / 1000;
        break;
    }

    double corr_inst = corrInst;
    double pps_blend_denom = pps_delta_active ? (double)pps_delta_active : (double)F_CPU;
    double corr_blend = (double)F_CPU / pps_blend_denom;

    sample.corr_inst_ppm  = (int32_t)lround((corr_inst - 1.0) * (double)CORR_PPM_SCALE);
    sample.corr_blend_ppm = (int32_t)lround((corr_blend - 1.0) * (double)CORR_PPM_SCALE);
    sample.gps_status     = gpsStatus;
    sample.dropped_events = atomicRead32(droppedEvents);

    sendSample(sample);
  }
#if ENABLE_PERIODIC_FLUSH
  static uint32_t s_last_flush_ms = 0;
  const uint32_t now_ms = millis();
  if ((uint32_t)(now_ms - s_last_flush_ms) >= FLUSH_PERIOD_MS) {
    s_last_flush_ms = now_ms;
    DATA_SERIAL.flush();
  }
#endif
}

// |----------------------------------------------------------------------------------------------|
// | ISR: TCB0_INT_vect (free-running timer overflow)                                             |
// | Estimated cycle cost (ATmega4809 @ 16MHz)                                                    |
// | Component                          | Cycles | Explanation                                    |
// |------------------------------------|--------|------------------------------------------------|
// | ISR prologue + epilogue            | ~22    | gcc pushes/pops regs + `reti`                  |
// | Write `TCB0.INTFLAGS`              | 2      | clear CAPT/OVF flags                           |
// | Increment `tcb0Ovf`                | ~10    | 32-bit increment                               |
// | Increment `tcb0WrapDetected`       | ~10    | 32-bit increment                               |
// | Optional diag timing updates       | ~18-30 | read CNT twice + sub16 + compare/store max     |
// | **Total (diag OFF / ON)**          | **~44 / ~62-74** | **~2.8µs / ~3.9-4.6µs @ 16MHz**      |
// -----------------------------------------------------------------------------------------------|
ISR(TCB0_INT_vect) {
#if ENABLE_ISR_DIAGNOSTICS
  uint16_t isr_start = read_TCB0_CNT();
#endif
  TCB0.INTFLAGS = TCB_CAPT_bm;
  tcb0Ovf++;
  tcb0WrapDetected++;
#if STS_DIAG > 0
  diag_tcb0_gap_record((((uint32_t)tcb0Ovf) << 16) | (uint32_t)read_TCB0_CNT());
#endif
#if ENABLE_ISR_DIAGNOSTICS
  uint16_t dur = sub16(read_TCB0_CNT(), isr_start);
  isr_last_tcb0_ticks = dur;
  if (dur > max_isr_tcb0_ticks) max_isr_tcb0_ticks = dur;
#endif
}

/*
ISR timestamp capture rationale — why ISR(TCBn_INT_vect) beats reading TCBn.CCMP directly

- Single 32-bit timeline: TCBn.CCMP is only 16-bit in TCn2’s domain (wraps every ~4.096 ms @ 16 MHz).
  The ISR maps each PPS edge onto the TCB0+overflow 32-bit clock so PPS and IR events share one timebase.

- Removes ISR latency/jitter: measure how late we are (d = TCBn.CNT - TCBn.CCMP) and backdate the
  timestamp into the TCB0 domain, making the result independent of interrupt latency or main-loop load.

- Coherency & overflow safe: use tcb0_now_coherent_isr() to read TCB0’s 32-bit time without wrap glitches;
  clear CAPT promptly to avoid the one-deep capture buffer being overwritten by the next PPS.

- Avoids cross-domain drift: no need to track TCBn overflows or calibrate a fixed phase offset to TCB0.

- Centralizes bookkeeping: ISR is the right place to push ring buffers, set flags, and apply sanity guards.

Minimal math (inside ISR):
    uint16_t ccmp = TCBn.CCMP;
    uint16_t cnt  = TCB .CNT;
    TCBn.INTFLAGS = TCB_CAPT_bm;           // clear early to prevent overwrite
    uint16_t d16  = cnt - ccmp;            // ticks since the edge (same tick rate as TCB0)
    uint32_t now  = tcb0_now_coherent_isr();   // race-free 32-bit read of TCB0 time
    uint32_t ts32 = now - (uint32_t)d16;   // PPS timestamp in TCB0’s 32-bit timeline
*/


// |-----------------------------------------------------------------------------------------------|
// | ISR: TCB1_INT_vect (IR sensor edge capture)                                                   |
// | Estimated cycle cost (ATmega4809 @ 16MHz)                                                     |
// | Component                          | Cycles | Explanation                                     |
// |------------------------------------|--------|-------------------------------------------------|
// | ISR prologue + epilogue            | ~22    | gcc pushes/pops regs + `reti`                   |
// | Read+clear `TCB1.INTFLAGS`         | ~4     | load flags + write-1-to-clear                   |
// | Non-CAPT gate + branch             | ~3-6   | `flags & TCB_CAPT_bm` test and branch           |
// | Read `TCB1.CCMP` + `TCB1.CNT`      | 8      | two 16-bit peripheral reads                     |
// | `now32 = tcb0_now_coherent_isr()`  | ~20    | coherent overflow/CNT sample in TCB0 domain     |
// | `latency16` + `edge32` arithmetic  | ~6     | 16-bit sub + 32-bit backdate                    |
// | Tick/tock branch logic             | ~2-4   | branch on `isTick`                              |
// | `push_event(...)`                  | ~33    | ring index, stores, dropped-event guard         |
// | Update `TCB1.EVCTRL` + `isTick`    | ~4     | select next edge + state toggle                 |
// | Optional diag timing updates       | ~18-30 | read CNT twice + sub16 + compare/store max      |
// | **Total (CAPT path, diag OFF / ON)**| **~102-107 / ~120-137** | **~6.4-6.7µs / ~7.5-8.6µs**   |
// |-----------------------------------------------------------------------------------------------|
ISR(TCB1_INT_vect) {
#if ENABLE_ISR_DIAGNOSTICS
  const uint16_t isr_start = read_TCB0_CNT();
#endif
  const uint8_t flags = TCB1.INTFLAGS;
  TCB1.INTFLAGS = flags;

  if (!(flags & TCB_CAPT_bm)) {
    return;
  }

  // Latch the captured edge time (TCB1 domain)
  const uint16_t ccmp = TCB1.CCMP;

  // Coherent 32-bit "now" in TCB0 domain (t2)
  const uint32_t now32 = tcb0_now_coherent_isr();

  // Tightened: sample CNT as close as possible to now32 (also ~t2)
  const uint16_t cnt = TCB1.CNT;

  // Latency since edge measured at ~t2, in TCB1 ticks
  const uint16_t latency16 = (uint16_t)(cnt - ccmp);

  // Backdate into TCB0 domain
  const uint32_t edge32 = now32 - (uint32_t)latency16;

  constexpr uint8_t EVCTRL_CAPTURE_EDGE_HIGH_TO_LOW = TCB_CAPTEI_bm | TCB_EDGE_bm | TCB_FILTER_bm;
  constexpr uint8_t EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH = TCB_CAPTEI_bm | TCB_FILTER_bm;

  if (isTick) {
    push_event(edge32, 0);                             // rising (LOW->HIGH, EDGE=0)
    TCB1.EVCTRL = EVCTRL_CAPTURE_EDGE_HIGH_TO_LOW;     // capture events EDGE = 1
    isTick = false;
  } else {
    push_event(edge32, 1);                             // falling (HIGH->LOW, EDGE=1)
    TCB1.EVCTRL = EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH;     // capture events EDGE = 0
    isTick = true;
  }
#if ENABLE_ISR_DIAGNOSTICS
  const uint16_t dur = sub16(read_TCB0_CNT(), isr_start);
  isr_last_tcb1_ticks = dur;
  if (dur > max_isr_tcb1_ticks) max_isr_tcb1_ticks = dur;
#endif
}

// |------------------------------------------------------------------------------------------------|
// | ISR: TCB2_INT_vect (PPS capture)                                                               |
// | Estimated cycle cost (ATmega4809 @ 16MHz)                                                      |
// | Component                           | Cycles | Explanation                                     |
// |-------------------------------------|--------|-------------------------------------------------|
// | ISR prologue + epilogue             | ~22    | gcc pushes/pops regs + `reti`                   |
// | Read+clear `TCB2.INTFLAGS`          | ~4     | load flags + write-1-to-clear                   |
// | Non-CAPT gate + branch              | ~3-6   | `flags & TCB_CAPT_bm` test and branch           |
// | Read `TCB2.CCMP` + `TCB2.CNT`       | 8      | two 16-bit peripheral reads                     |
// | `now32 = tcb0_now_coherent_isr()`   | ~20    | coherent overflow/CNT sample in TCB0 domain     |
// | `latency16` + `edge32` arithmetic   | ~6     | 16-bit sub + 32-bit backdate                    |
// | `pps_seen++` + `lastPpsEdgeCapture` | ~12-16 | 32-bit increment + 32-bit store                 |
// | `ppsData_push_isr(...)`             | ~28    | ring-buffer index + payload stores              |
// | Optional PPS latency diagnostics    | ~34-60 | max/wrap/min/max/sum/count updates              |
// | Optional diag timing updates        | ~18-30 | read CNT twice + sub16 + compare/store max      |
// | **Total (CAPT path, diag OFF / ON)**| **~103-110 / ~155-200** | **~6.4-6.9µs / ~9.7-12.5µs**   |
// |------------------------------------------------------------------------------------------------|
ISR(TCB2_INT_vect) {
#if ENABLE_ISR_DIAGNOSTICS
  uint16_t isr_start = read_TCB0_CNT();
#endif
  // Read and clear flags early to avoid losing the one-deep capture
  const uint8_t flags = TCB2.INTFLAGS;
  TCB2.INTFLAGS = flags; // clear CAPT/OVF that were set (write-1-to-clear)

  // If this ISR can fire for non-CAPT reasons, gate it
  if (!(flags & TCB_CAPT_bm)) {
    return;
  }

  // Latch the captured edge time (TCB2 domain)
  const uint16_t ccmp = TCB2.CCMP;

  // Coherent 32-bit "now" in TCB0 domain (t2)
  const uint32_t now32 = tcb0_now_coherent_isr();

  // Tightened: sample CNT as close as possible to now32 (also ~t2)
  const uint16_t cnt = TCB2.CNT;

  // Latency since edge measured at ~t2, in TCB2 ticks
  const uint16_t latency16 = (uint16_t)(cnt - ccmp);

  // Backdate into TCB0 domain
  const uint32_t edge32 = now32 - (uint32_t)latency16;

  pps_seen++;
#if STS_DIAG > 0
  pps_isr_count++;
#endif
#if ENABLE_ISR_DIAGNOSTICS
  pps_latency_last = latency16;
  if (latency16 > g_pps_latency16_max) g_pps_latency16_max = latency16;
  // >60000 sits close to 16-bit rollover (65535), hinting bad reconstruction or extreme ISR latency.
  if (latency16 > 60000U) g_pps_latency16_wrapRisk++;
  if (pps_latency_count == 0 || latency16 < pps_latency_min) pps_latency_min = latency16;
  if (latency16 > pps_latency_max) pps_latency_max = latency16;
  pps_latency_sum += (uint32_t)latency16;
  pps_latency_count++;
#endif

  ppsData_push_isr(edge32, now32, tcb0Ovf, ccmp, cnt, latency16); // enqueue PPS timestamp/capture context
  lastPpsEdgeCapture = edge32;           // freshest PPS arrival for holdover timing
#if ENABLE_ISR_DIAGNOSTICS
  uint16_t dur = sub16(read_TCB0_CNT(), isr_start);
  isr_last_tcb2_ticks = dur;
  if (dur > max_isr_tcb2_ticks) max_isr_tcb2_ticks = dur;
#endif
}
