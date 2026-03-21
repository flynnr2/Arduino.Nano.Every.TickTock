#include "Config.h"

#include <Arduino.h>
#include <util/atomic.h>

#include "PendulumCapture.h"

static volatile uint32_t pps_seen = 0;
static volatile uint32_t droppedEvents = 0;
static volatile uint32_t lastPpsEdgeCapture = 0;

static volatile uint16_t pps_latency_last = 0;
static volatile uint16_t pps_latency_min = 0xFFFF;
static volatile uint16_t pps_latency_max = 0;
static volatile uint32_t pps_latency_sum = 0;
static volatile uint16_t pps_latency_count = 0;
static volatile uint16_t g_pps_latency16_max = 0;
static volatile uint32_t g_pps_latency16_wrapRisk = 0;
static volatile uint16_t isr_last_tcb0_ticks = 0;
static volatile uint16_t isr_last_tcb1_ticks = 0;
static volatile uint16_t isr_last_tcb2_ticks = 0;
static volatile uint16_t max_isr_tcb0_ticks = 0;
static volatile uint16_t max_isr_tcb1_ticks = 0;
static volatile uint16_t max_isr_tcb2_ticks = 0;

static volatile uint16_t tcb0Ovf = 0;
static volatile uint32_t tcb0WrapDetected = 0;
static volatile uint32_t coherentOvfFlagSeenCount = 0;
static volatile uint32_t coherentOvfAppliedCount = 0;

static bool isTick = true;

static EdgeEvent evbuf[CAPTURE_EDGE_BUFFER_SIZE];
static volatile uint8_t ev_head = 0;
static volatile uint8_t ev_tail = 0;

static PpsCapture ppsBuffer[CAPTURE_PPS_RING_SIZE];
static volatile uint8_t ppsHead = 0;
static volatile uint8_t ppsTail = 0;

#if STS_DIAG > 0
static volatile uint32_t pps_isr_count = 0;
extern void diag_tcb0_gap_record(uint32_t now32);
#endif

static_assert((CAPTURE_EDGE_BUFFER_SIZE & (CAPTURE_EDGE_BUFFER_SIZE - 1U)) == 0U, "CAPTURE_EDGE_BUFFER_SIZE must be power-of-two for mask arithmetic");
static_assert((CAPTURE_PPS_RING_SIZE & (CAPTURE_PPS_RING_SIZE - 1U)) == 0U, "CAPTURE_PPS_RING_SIZE must be power-of-two for mask arithmetic");

static inline uint16_t read_TCB0_CNT() { return TCB0.CNT; }
static inline uint16_t sub16(uint16_t a, uint16_t b) { return (uint16_t)(a - b); }

static inline uint8_t pps_mask(uint8_t v) { return v & (CAPTURE_PPS_RING_SIZE - 1); }
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

uint32_t captureDroppedEvents() {
  uint32_t dropped = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    dropped = droppedEvents;
  }
  return dropped;
}

uint32_t capturePpsSeen() {
  uint32_t seen = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    seen = pps_seen;
  }
  return seen;
}

void captureRecordDroppedEvent() {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    droppedEvents++;
  }
}

CaptureDiagnosticsSnapshot captureDiagnosticsSnapshot() {
  CaptureDiagnosticsSnapshot snapshot{};
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    snapshot.pps_seen = pps_seen;
    snapshot.dropped_events = droppedEvents;
    snapshot.last_pps_edge_capture = lastPpsEdgeCapture;
#if STS_DIAG > 0
    snapshot.pps_isr_count = pps_isr_count;
#else
    snapshot.pps_isr_count = 0;
#endif
    snapshot.tcb0_wrap_detected = tcb0WrapDetected;
    snapshot.coherent_ovf_flag_seen_count = coherentOvfFlagSeenCount;
    snapshot.coherent_ovf_applied_count = coherentOvfAppliedCount;
    snapshot.pps_latency_sum = pps_latency_sum;
    snapshot.pps_latency16_wrap_risk = g_pps_latency16_wrapRisk;
    snapshot.pps_latency_last = pps_latency_last;
    snapshot.pps_latency_min = pps_latency_min;
    snapshot.pps_latency_max = pps_latency_max;
    snapshot.pps_latency_count = pps_latency_count;
    snapshot.pps_latency16_max = g_pps_latency16_max;
    snapshot.isr_last_tcb0_ticks = isr_last_tcb0_ticks;
    snapshot.isr_last_tcb1_ticks = isr_last_tcb1_ticks;
    snapshot.isr_last_tcb2_ticks = isr_last_tcb2_ticks;
    snapshot.max_isr_tcb0_ticks = max_isr_tcb0_ticks;
    snapshot.max_isr_tcb1_ticks = max_isr_tcb1_ticks;
    snapshot.max_isr_tcb2_ticks = max_isr_tcb2_ticks;
    snapshot.tcb0_ovf = tcb0Ovf;
    snapshot.capt_pending = (TCB0.INTFLAGS & TCB_CAPT_bm) ? 1U : 0U;
  }
  return snapshot;
}

bool captureEdgeAvailable() {
  return ev_tail != ev_head;
}

EdgeEvent capturePopEdge() {
  const uint8_t tail = ev_tail;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    ev_tail = (uint8_t)(tail + 1) & (CAPTURE_EDGE_BUFFER_SIZE - 1);
  }
  return evbuf[tail];
}

bool capturePpsAvailable() {
  return ppsTail != ppsHead;
}

PpsCapture capturePopPps() {
  const uint8_t tail = ppsTail;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    ppsTail = pps_mask(tail + 1);
  }
  return ppsBuffer[tail];
}

uint32_t tcb0NowCoherent() {
  return tcb0_now_coherent_isr();
}

uint64_t tcb0NowCoherent64() {
  uint64_t now64 = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    uint32_t wraps = tcb0WrapDetected;
    uint16_t cnt = read_TCB0_CNT();
    const uint8_t intflags = TCB0.INTFLAGS;
    if (intflags & TCB_CAPT_bm) {
      wraps++;
      cnt = read_TCB0_CNT();
    }
    now64 = ((uint64_t)wraps << 16) | (uint64_t)cnt;
  }
  return now64;
}

void captureResetState() {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    pps_seen = 0;
    droppedEvents = 0;
    lastPpsEdgeCapture = 0;

    pps_latency_last = 0;
    pps_latency_min = 0xFFFF;
    pps_latency_max = 0;
    pps_latency_sum = 0;
    pps_latency_count = 0;
    g_pps_latency16_max = 0;
    g_pps_latency16_wrapRisk = 0;
    isr_last_tcb0_ticks = 0;
    isr_last_tcb1_ticks = 0;
    isr_last_tcb2_ticks = 0;
    max_isr_tcb0_ticks = 0;
    max_isr_tcb1_ticks = 0;
    max_isr_tcb2_ticks = 0;

    tcb0Ovf = 0;
    tcb0WrapDetected = 0;
    coherentOvfFlagSeenCount = 0;
    coherentOvfAppliedCount = 0;

    isTick = true;
    ev_head = 0;
    ev_tail = 0;
    ppsHead = 0;
    ppsTail = 0;
#if STS_DIAG > 0
    pps_isr_count = 0;
#endif
  }
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
    uint8_t next = (uint8_t)(ev_head + 1) & (CAPTURE_EDGE_BUFFER_SIZE - 1);
    if (next != ev_tail) {
      evbuf[ev_head].ticks = edge32;
      evbuf[ev_head].type  = 0;
      ev_head = next;
    } else {
      droppedEvents_inc_isr();
    }
    TCB1.EVCTRL = EVCTRL_CAPTURE_EDGE_HIGH_TO_LOW;
    isTick = false;
  } else {
    uint8_t next = (uint8_t)(ev_head + 1) & (CAPTURE_EDGE_BUFFER_SIZE - 1);
    if (next != ev_tail) {
      evbuf[ev_head].ticks = edge32;
      evbuf[ev_head].type  = 1;
      ev_head = next;
    } else {
      droppedEvents_inc_isr();
    }
    TCB1.EVCTRL = EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH;
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
  TCB2.INTFLAGS = flags;

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
  if (latency16 > 60000U) g_pps_latency16_wrapRisk++;
  if (pps_latency_count == 0 || latency16 < pps_latency_min) pps_latency_min = latency16;
  if (latency16 > pps_latency_max) pps_latency_max = latency16;
  pps_latency_sum += (uint32_t)latency16;
  pps_latency_count++;
#endif

  ppsData_push_isr(edge32, now32, tcb0Ovf, ccmp, cnt, latency16);
  lastPpsEdgeCapture = edge32;
#if ENABLE_ISR_DIAGNOSTICS
  uint16_t dur = sub16(read_TCB0_CNT(), isr_start);
  isr_last_tcb2_ticks = dur;
  if (dur > max_isr_tcb2_ticks) max_isr_tcb2_ticks = dur;
#endif
}
