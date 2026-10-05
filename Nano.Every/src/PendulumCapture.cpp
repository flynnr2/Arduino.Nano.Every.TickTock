#include "Config.h"

#include <Arduino.h>
#include <util/atomic.h>

#include "PendulumCapture.h"
#include "CaptureEvctrl.h"

static volatile uint32_t pps_seen = 0;
static volatile uint32_t droppedIrEvents = 0;
static volatile uint32_t droppedPpsEvents = 0;
static volatile uint32_t droppedSwingRows = 0;
#if ENABLE_TCB_LATENCY_DIAG
static volatile uint32_t droppedTcbLatencyTraceEvents = 0;
static volatile uint32_t tcb1LatencyTraceSeq = 0;
#endif

static volatile uint16_t tcb0Ovf = 0;
static volatile uint32_t tcb0WrapDetected = 0;
static volatile uint32_t coherentOvfFlagSeenCount = 0;
static volatile uint32_t coherentOvfAppliedCount = 0;

enum Tcb1CaptureSide : uint8_t {
  TCB1_CAPTURE_SIDE_TICK = 0,
  TCB1_CAPTURE_SIDE_TOCK = 1,
};
static volatile uint8_t tcb1CaptureSide = TCB1_CAPTURE_SIDE_TICK;

static EdgeEvent evbuf[CAPTURE_EDGE_BUFFER_SIZE];
static volatile uint8_t ev_head = 0;
static volatile uint8_t ev_tail = 0;

static PpsCapture ppsBuffer[CAPTURE_PPS_RING_SIZE];
static volatile uint8_t ppsHead = 0;
static volatile uint8_t ppsTail = 0;
#if ENABLE_TCB_LATENCY_DIAG
static TcbLatencyTraceEvent tcbLatencyTraceBuffer[TCB_LATENCY_TRACE_RING_SIZE];
static volatile uint8_t tcbLatencyTraceHead = 0;
static volatile uint8_t tcbLatencyTraceTail = 0;
#endif

#if ENABLE_PROFILING && DUAL_PPS_PROFILING
static volatile uint32_t dual_tcb1_rising_seq = 0;
static volatile uint32_t dual_tcb2_rising_seq = 0;
static volatile uint16_t dual_tcb1_rising_cap16 = 0;
static volatile uint32_t dual_tcb1_rising_edge32 = 0;
#endif

static bool captureHardwareInitialized = false;

static_assert(CAPTURE_EDGE_BUFFER_SIZE > 0U &&
                  (CAPTURE_EDGE_BUFFER_SIZE & (CAPTURE_EDGE_BUFFER_SIZE - 1U)) == 0U,
              "CAPTURE_EDGE_BUFFER_SIZE must be a non-zero power-of-two for mask arithmetic");
static_assert((CAPTURE_PPS_RING_SIZE & (CAPTURE_PPS_RING_SIZE - 1U)) == 0U, "CAPTURE_PPS_RING_SIZE must be power-of-two for mask arithmetic");
static_assert(sizeof(evbuf) <= 512U, "Edge-event ring exceeds SRAM guardrail");
static_assert(sizeof(ppsBuffer) <= 256U, "PPS ring exceeds SRAM guardrail");
#if ENABLE_TCB_LATENCY_DIAG
static_assert(sizeof(tcbLatencyTraceBuffer) <= 384U, "TCB latency trace ring exceeds SRAM guardrail");
#endif

static inline uint32_t tcb0_now_coherent_isr_only();

namespace {

constexpr uint8_t TCB0_MODE_FREE_RUNNING = TCB_CNTMODE_INT_gc;
constexpr uint8_t TCB_CAPTURE_MODE = TCB_CNTMODE_CAPT_gc;

// EVCTRL values used to arm the next TCB1 capture edge.
// Important: this table is indexed by the NEXT side, not the side just captured.
// Check this carefully before changing polarity definitions.
static constexpr uint8_t TCB1_EVCTRL_FOR_NEXT_SIDE[2] = {
    EVCTRL_CAPTURE_EDGE_HIGH_TO_LOW,  // next side = tick (0): arm high->low
    EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH,  // next side = tock (1): arm low->high
};

struct CaptureMathResult {
  uint32_t now32;
  uint16_t latency16;
  uint32_t edge32;
};

// Read both counters as one instruction sequence. Each AVRxt LDS takes three
// cycles, so the capture-counter low byte is sampled exactly six timer ticks
// after the TCB0 low byte. Both timers use CLKDIV1. Low-byte reads latch each
// timer's high byte; no access to another register in that timer intervenes.
constexpr uint8_t CAPTURE_SAMPLE_SKEW_TICKS = 6U;

template <uint16_t CaptureCntAddress>
static inline __attribute__((always_inline)) void read_capture_counter_pair(
    uint16_t& tcb0Cnt, uint16_t& captureCnt) {
  asm volatile(
      "lds %A0, %2\n\t"
      "lds %B0, %2+1\n\t"
      "lds %A1, %3\n\t"
      "lds %B1, %3+1\n\t"
      : "=&r" (tcb0Cnt), "=&r" (captureCnt)
      : "n" (_SFR_MEM_ADDR(TCB0.CNT)), "n" (CaptureCntAddress)
      : "memory");
}

// ISR context only, with capture CCMP already latched and CAPT cleared.
// As for the other coherent readers, at most one TCB0 overflow may be pending;
// interrupts must not remain masked for a full 65536-tick timer period.
// Check overflow AFTER the pair: a wrap between either read and the flag check
// requires a fresh pair in the incremented epoch. Re-reading only TCB0 would
// change the counter sampling separation and introduce wrap-dependent jitter.
template <uint16_t CaptureCntAddress, uint8_t CaptureEvctrl>
static inline CaptureMathResult capture_math_from_regs_isr_only(uint16_t ccmp) {
  uint16_t ovf = tcb0Ovf;
  uint16_t tcb0Cnt;
  uint16_t cnt;
  read_capture_counter_pair<CaptureCntAddress>(tcb0Cnt, cnt);
  if (TCB0.INTFLAGS & TCB_CAPT_bm) {
    ovf++;
    read_capture_counter_pair<CaptureCntAddress>(tcb0Cnt, cnt);
    coherentOvfFlagSeenCount++;
    coherentOvfAppliedCount++;
  }
  // Align the extended TCB0 sample to the later capture-counter sample. Do the
  // addition in 32 bits so a wrap during the pair carries into the epoch.
  const uint32_t now32 = (((uint32_t)ovf << 16) | tcb0Cnt) +
                         CAPTURE_SAMPLE_SKEW_TICKS;
  const uint16_t latency16 = (uint16_t)(cnt - ccmp);
  // Project before input filtering; preserve the raw post-capture latency.
  // Unsigned subtraction also handles the shared timeline's 32-bit wrap.
  const uint32_t edge32 = now32 - (uint32_t)latency16 - captureFilterDelayTicks(CaptureEvctrl);
  return CaptureMathResult{now32, latency16, edge32};
}

static inline void resetCaptureSoftwareState() {
  pps_seen = 0;
  droppedIrEvents = 0;
  droppedPpsEvents = 0;
  droppedSwingRows = 0;
#if ENABLE_TCB_LATENCY_DIAG
  droppedTcbLatencyTraceEvents = 0;
  tcb1LatencyTraceSeq = 0;
#endif
  tcb0Ovf = 0;
  tcb0WrapDetected = 0;
  coherentOvfFlagSeenCount = 0;
  coherentOvfAppliedCount = 0;

  tcb1CaptureSide = TCB1_CAPTURE_SIDE_TICK;
  ev_head = 0;
  ev_tail = 0;
  ppsHead = 0;
  ppsTail = 0;
#if ENABLE_TCB_LATENCY_DIAG
  tcbLatencyTraceHead = 0;
  tcbLatencyTraceTail = 0;
#endif
#if ENABLE_PROFILING && DUAL_PPS_PROFILING
  dual_tcb1_rising_seq = 0;
  dual_tcb2_rising_seq = 0;
  dual_tcb1_rising_cap16 = 0;
  dual_tcb1_rising_edge32 = 0;
#endif
}

}  // namespace

static inline uint16_t read_TCB0_CNT() { return TCB0.CNT; }

static inline uint8_t pps_mask(uint8_t v) { return v & (CAPTURE_PPS_RING_SIZE - 1); }
static inline void droppedIrEvents_inc_isr() {
  droppedIrEvents++;
}
static inline void droppedPpsEvents_inc_isr() {
  droppedPpsEvents++;
}
#if ENABLE_TCB_LATENCY_DIAG
static inline uint8_t tcbLatency_mask(uint8_t v) { return v & (TCB_LATENCY_TRACE_RING_SIZE - 1U); }
static inline void tcbLatencyTracePushIsr(uint8_t tcb,
                                          uint8_t edgeKind,
                                          uint32_t seqOrEdgeIndex,
                                          uint32_t edge32,
                                          uint16_t cap16,
                                          uint16_t cnt16,
                                          uint16_t latency16) {
  const uint8_t n = tcbLatency_mask((uint8_t)(tcbLatencyTraceHead + 1U));
  if (n != tcbLatencyTraceTail) {
    TcbLatencyTraceEvent& slot = tcbLatencyTraceBuffer[tcbLatencyTraceHead];
    slot.seq_or_edge_index = seqOrEdgeIndex;
    slot.edge32 = edge32;
    slot.cap16 = cap16;
    slot.cnt16 = cnt16;
    slot.latency16 = latency16;
    slot.tcb = tcb;
    slot.edge_kind = edgeKind;
    tcbLatencyTraceHead = n;
  } else {
    droppedTcbLatencyTraceEvents++;
  }
}
#endif

static inline void ppsData_push_isr(uint32_t seq,
                                    uint32_t edge32,
                                    uint32_t now32,
                                    uint16_t cap16,
                                    uint16_t latency16
#if ENABLE_PROFILING && DUAL_PPS_PROFILING
                                    , uint32_t rise_seq
#endif
                                    ) {
  uint8_t n = pps_mask(ppsHead + 1);
  if (n != ppsTail) {
    PpsCapture &slot = ppsBuffer[ppsHead];
    slot.seq = seq;
    slot.edge32 = edge32;
    slot.now32 = now32;
    slot.cap16 = cap16;
    slot.latency16 = latency16;
#if ENABLE_PROFILING && DUAL_PPS_PROFILING
    slot.rise_seq = rise_seq;
#endif
    ppsHead = n;
  } else {
    droppedPpsEvents_inc_isr();
  }
}

// Usage contract: ISR context only; no ATOMIC_BLOCK here.
// Callers in foreground/main-loop code must use tcb0NowCoherentMainLoop()/tcb0NowCoherent64().
static inline uint32_t tcb0_now_coherent_isr_only() {
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

uint32_t capturePpsSeen() {
  uint32_t seen = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    seen = pps_seen;
  }
  return seen;
}

void captureRecordSwingRowDrop() {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    droppedSwingRows++;
  }
}

uint32_t captureDroppedIrEvents() {
  uint32_t dropped = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    dropped = droppedIrEvents;
  }
  return dropped;
}

uint32_t captureDroppedPpsEvents() {
  uint32_t dropped = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    dropped = droppedPpsEvents;
  }
  return dropped;
}

uint32_t captureDroppedSwingRows() {
  uint32_t dropped = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    dropped = droppedSwingRows;
  }
  return dropped;
}

#if ENABLE_PROFILING && DUAL_PPS_PROFILING
bool captureReadDualPpsTcb1RisingSnapshot(DualPpsTcb1RisingSnapshot& out) {
  bool valid = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (dual_tcb1_rising_seq != 0U) {
      out.rise_seq = dual_tcb1_rising_seq;
      out.edge32 = dual_tcb1_rising_edge32;
      out.cap16 = dual_tcb1_rising_cap16;
      valid = true;
    }
  }
  return valid;
}

void captureReadDualPpsSeenCounters(DualPpsProfilingCounters& out) {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    out.tcb1_rising_seen = dual_tcb1_rising_seq;
    out.tcb2_rising_seen = dual_tcb2_rising_seq;
  }
}
#endif

bool captureTryPopEdge(EdgeEvent* out) {
  if (out == nullptr) {
    return false;
  }
  bool popped = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (ev_tail != ev_head) {
      const uint8_t tail = ev_tail;
      // Take a stable snapshot before freeing the ring slot; the ISR may wrap and reuse it immediately.
      *out = evbuf[tail];
      ev_tail = (uint8_t)(tail + 1) & (CAPTURE_EDGE_BUFFER_SIZE - 1);
      popped = true;
    }
  }
  return popped;
}

bool captureTryPopPps(PpsCapture* out) {
  if (out == nullptr) {
    return false;
  }
  bool popped = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (ppsTail != ppsHead) {
      const uint8_t tail = ppsTail;
      // Take a stable snapshot before freeing the ring slot; the ISR may wrap and reuse it immediately.
      *out = ppsBuffer[tail];
      ppsTail = pps_mask(tail + 1);
      popped = true;
    }
  }
  return popped;
}
#if ENABLE_TCB_LATENCY_DIAG
bool captureTryPopTcbLatencyTrace(TcbLatencyTraceEvent* out) {
  if (out == nullptr) return false;
  bool popped = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (tcbLatencyTraceTail != tcbLatencyTraceHead) {
      const uint8_t tail = tcbLatencyTraceTail;
      *out = tcbLatencyTraceBuffer[tail];
      tcbLatencyTraceTail = tcbLatency_mask((uint8_t)(tail + 1U));
      popped = true;
    }
  }
  return popped;
}

uint32_t captureDroppedTcbLatencyTraceEvents() {
  uint32_t dropped = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    dropped = droppedTcbLatencyTraceEvents;
  }
  return dropped;
}
#endif

uint32_t tcb0NowCoherentMainLoop() {
  uint32_t now32 = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    now32 = tcb0_now_coherent_isr_only();
  }
  return now32;
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

void captureMarkHardwareInitialized() {
  captureHardwareInitialized = true;
}

void captureResetAndReinit() {
  if (!captureHardwareInitialized) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      resetCaptureSoftwareState();
    }
    return;
  }

  const uint8_t sreg = SREG;
  cli();

  // Mask capture interrupts before touching timer state so no ISR observes a partial reset.
  TCB0.INTCTRL = 0x0;
  TCB1.INTCTRL = 0x0;
  TCB2.INTCTRL = 0x0;

  // Quiesce the timers first, then scrub timer-local state while they are stopped.
  TCB2.CTRLA = 0x0;
  TCB1.CTRLA = 0x0;
  TCB0.CTRLA = 0x0;

  TCB0.CNT = 0x0000;
  TCB0.CCMP = 0xFFFF;
  TCB0.INTFLAGS = TCB_CAPT_bm;

  TCB1.CNT = 0x0000;
  TCB1.CCMP = 0x0000;
  TCB1.INTFLAGS = TCB_CAPT_bm;

  TCB2.CNT = 0x0000;
  TCB2.CCMP = 0x0000;
  TCB2.INTFLAGS = TCB_CAPT_bm;

  resetCaptureSoftwareState();

  // Restore the expected post-reset edge polarity so side=tick matches the next IR edge.
  TCB0.CTRLB = TCB0_MODE_FREE_RUNNING;
  TCB1.CTRLB = TCB_CAPTURE_MODE;
  TCB2.CTRLB = TCB_CAPTURE_MODE;
  TCB1.EVCTRL = EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH;
  TCB2.EVCTRL = EVCTRL_PPS_CAPTURE;

  TCB0.INTCTRL = TCB_CAPT_bm;
  TCB1.INTCTRL = TCB_CAPT_bm;
  TCB2.INTCTRL = TCB_CAPT_bm;

  // Restart the shared timebase first, then consumers that timestamp against it.
  TCB0.CTRLA = TCB0_ENABLE;
  TCB1.CTRLA = TCB1_ENABLE;
  TCB2.CTRLA = TCB2_ENABLE;

  SREG = sreg;
}

void captureResetState() {
  captureResetAndReinit();
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
// | **Total**                         | **~44** | **~2.8µs @ 16MHz**                              |
// -----------------------------------------------------------------------------------------------|
ISR(TCB0_INT_vect) {
  TCB0.INTFLAGS = TCB_CAPT_bm;
  tcb0Ovf++;
  tcb0WrapDetected++;
}

/*
Capture reconstruction uses a fixed-separation TCB0/TCBn counter pair in the
shared helper above. now32 is aligned to the TCBn CNT sample before subtracting
latency16 = CNT - CCMP. Pending overflow handling refreshes the entire pair.
Projection then removes the filter delay selected by the path's EVCTRL setting.
The same implementation timestamps both IR edges and PPS edges, without
branch-dependent counter skew or tick/tock-specific timing compensation.
*/

// TCB1_INT_vect (IR sensor edge capture)
// Counter-pair instruction timing is verified by tools/capture_reconstruction.
ISR(TCB1_INT_vect) {
  const uint8_t flags = TCB1.INTFLAGS;
  TCB1.INTFLAGS = flags;

  if (!(flags & TCB_CAPT_bm)) {
    return;
  }

  const uint16_t ccmp = TCB1.CCMP;
  const CaptureMathResult captureMath = capture_math_from_regs_isr_only<_SFR_MEM_ADDR(TCB1.CNT), EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH>(ccmp);

  // TCB1 capture ISR timing note:
  // This hot path is deliberately branch-symmetric with respect to tick/tock.
  // The hardware capture timestamp is already latched in CCMP; this ISR only
  // records the captured edge into the current semantic slot, arms TCB1 for
  // the next opposite edge, and toggles the side index.
  //
  // Do not reintroduce:
  //
  //     if (isTick) { ... } else { ... }
  //
  // here. Even small tick-vs-tock branch differences make later asymmetry
  // analysis harder to trust. Sequence validation, startup resync, diagnostics,
  // and row assembly should happen outside this ISR.
  const uint8_t side = tcb1CaptureSide;
  const uint8_t nextSide = side ^ 1u;

#if ENABLE_PROFILING && DUAL_PPS_PROFILING
  const uint8_t tickSideCaptured = (uint8_t)(side == TCB1_CAPTURE_SIDE_TICK);
  dual_tcb1_rising_seq += tickSideCaptured;
  dual_tcb1_rising_cap16 = tickSideCaptured != 0U ? ccmp : dual_tcb1_rising_cap16;
  dual_tcb1_rising_edge32 = tickSideCaptured != 0U ? captureMath.edge32 : dual_tcb1_rising_edge32;
#endif
  uint8_t next = (uint8_t)(ev_head + 1) & (CAPTURE_EDGE_BUFFER_SIZE - 1);
#if ENABLE_TCB_LATENCY_DIAG
  tcb1LatencyTraceSeq++;
  tcbLatencyTracePushIsr(1U, side == TCB1_CAPTURE_SIDE_TICK ? TCB_LATENCY_EDGE_TICK : TCB_LATENCY_EDGE_TOCK,
                         tcb1LatencyTraceSeq, captureMath.edge32, ccmp,
                         (uint16_t)(ccmp + captureMath.latency16), captureMath.latency16);
#endif
  if (next != ev_tail) {
    evbuf[ev_head].ticks = captureMath.edge32;
    evbuf[ev_head].type  = side;
    ev_head = next;
  } else {
    droppedIrEvents_inc_isr();
  }
  TCB1.EVCTRL = TCB1_EVCTRL_FOR_NEXT_SIDE[nextSide];
  tcb1CaptureSide = nextSide;
}

// TCB2_INT_vect (PPS capture)
// Counter-pair instruction timing is verified by tools/capture_reconstruction.
ISR(TCB2_INT_vect) {
  // Read and clear flags early to avoid losing the one-deep capture
  const uint8_t flags = TCB2.INTFLAGS;
  TCB2.INTFLAGS = flags;

  // If this ISR can fire for non-CAPT reasons, gate it
  if (!(flags & TCB_CAPT_bm)) {
    return;
  }

  const uint16_t ccmp = TCB2.CCMP;
  const CaptureMathResult captureMath = capture_math_from_regs_isr_only<_SFR_MEM_ADDR(TCB2.CNT), EVCTRL_PPS_CAPTURE>(ccmp);
  const uint16_t cnt = (uint16_t)(ccmp + captureMath.latency16);

  pps_seen++;
#if ENABLE_TCB_LATENCY_DIAG
  tcbLatencyTracePushIsr(2U, TCB_LATENCY_EDGE_PPS, pps_seen, captureMath.edge32, ccmp, cnt, captureMath.latency16);
#endif
#if ENABLE_PROFILING && DUAL_PPS_PROFILING
  dual_tcb2_rising_seq++;
  ppsData_push_isr(pps_seen, captureMath.edge32, captureMath.now32, ccmp, captureMath.latency16, dual_tcb2_rising_seq);
#else
  ppsData_push_isr(pps_seen, captureMath.edge32, captureMath.now32, ccmp, captureMath.latency16);
#endif
}

/*
PPS_BASE `l` is raw latency16: elapsed TCB2 ticks from the hardware capture to
its CNT sample. A high latency can result from another ISR or a pending TCB0
wrap. It is not itself a timestamp error: reconstruction must use counters with
fixed sampling separation. The old coherent-now-then-CNT implementation broke
that invariant by six cycles on its overflow branch. See
Docs/PPS_Timestamp_Investigation.md for the generated-instruction evidence and
wrap-boundary validation. Capture latency must remain below 65536 timer ticks;
the 16-bit difference cannot distinguish a delay of a whole counter period.
*/
