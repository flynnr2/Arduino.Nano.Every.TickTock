#include "Config.h"

#include <Arduino.h>
#include <util/atomic.h>

#include "PendulumCapture.h"

static volatile uint32_t pps_seen = 0;
static volatile uint32_t droppedEvents = 0;

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

static bool captureHardwareInitialized = false;

static_assert(CAPTURE_EDGE_BUFFER_SIZE > 0U &&
                  (CAPTURE_EDGE_BUFFER_SIZE & (CAPTURE_EDGE_BUFFER_SIZE - 1U)) == 0U,
              "CAPTURE_EDGE_BUFFER_SIZE must be a non-zero power-of-two for mask arithmetic");
static_assert((CAPTURE_PPS_RING_SIZE & (CAPTURE_PPS_RING_SIZE - 1U)) == 0U, "CAPTURE_PPS_RING_SIZE must be power-of-two for mask arithmetic");
static_assert(sizeof(evbuf) <= 512U, "Edge-event ring exceeds SRAM guardrail");
static_assert(sizeof(ppsBuffer) <= 256U, "PPS ring exceeds SRAM guardrail");

namespace {

constexpr uint8_t TCB0_ENABLE = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;
constexpr uint8_t TCB1_ENABLE = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;
constexpr uint8_t TCB2_ENABLE = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;

constexpr uint8_t TCB0_MODE_FREE_RUNNING = TCB_CNTMODE_INT_gc;
constexpr uint8_t TCB_CAPTURE_MODE = TCB_CNTMODE_CAPT_gc;

constexpr uint8_t EVCTRL_CAPTURE_EDGE_HIGH_TO_LOW = TCB_CAPTEI_bm | TCB_EDGE_bm | TCB_FILTER_bm;
constexpr uint8_t EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH = TCB_CAPTEI_bm | TCB_FILTER_bm;
constexpr uint8_t EVCTRL_PPS_CAPTURE = TCB_CAPTEI_bm;

static inline void resetCaptureSoftwareState() {
  pps_seen = 0;
  droppedEvents = 0;
  tcb0Ovf = 0;
  tcb0WrapDetected = 0;
  coherentOvfFlagSeenCount = 0;
  coherentOvfAppliedCount = 0;

  isTick = true;
  ev_head = 0;
  ev_tail = 0;
  ppsHead = 0;
  ppsTail = 0;
}

}  // namespace

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

bool captureEdgeAvailable() {
  return ev_tail != ev_head;
}

EdgeEvent capturePopEdge() {
  EdgeEvent event;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    const uint8_t tail = ev_tail;
    // Take a stable snapshot before freeing the ring slot; the ISR may wrap and reuse it immediately.
    event = evbuf[tail];
    ev_tail = (uint8_t)(tail + 1) & (CAPTURE_EDGE_BUFFER_SIZE - 1);
  }
  return event;
}

bool capturePpsAvailable() {
  return ppsTail != ppsHead;
}

PpsCapture capturePopPps() {
  PpsCapture capture;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    const uint8_t tail = ppsTail;
    // Take a stable snapshot before freeing the ring slot; the ISR may wrap and reuse it immediately.
    capture = ppsBuffer[tail];
    ppsTail = pps_mask(tail + 1);
  }
  return capture;
}

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

  // Restore the expected post-reset edge polarity so `isTick == true` matches the next IR edge.
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
ISR timestamp capture rationale — why ISR(TCBn_INT_vect) beats reading TCBn.CCMP directly

- Single 32-bit timeline: TCBn.CCMP is only 16-bit in TCn2’s domain (wraps every 65536 / MAIN_CLOCK_HZ seconds;
  ~4.096 ms in the default 16 MHz build).
  The ISR maps each PPS edge onto the TCB0+overflow 32-bit clock so PPS and IR events share one timebase.

- Removes ISR latency/jitter: measure how late we are (d = TCBn.CNT - TCBn.CCMP) and backdate the
  timestamp into the TCB0 domain, making the result independent of interrupt latency or main-loop load.

- Coherency & overflow safe: use tcb0_now_coherent_isr_only() to read TCB0’s 32-bit time without wrap glitches;
  clear CAPT promptly to avoid the one-deep capture buffer being overwritten by the next PPS.

- Avoids cross-domain drift: no need to track TCBn overflows or calibrate a fixed phase offset to TCB0.

- Centralizes bookkeeping: ISR is the right place to push ring buffers, set flags, and apply sanity guards.

Minimal math (inside ISR):
    uint16_t ccmp = TCBn.CCMP;
    uint16_t cnt  = TCB .CNT;
    TCBn.INTFLAGS = TCB_CAPT_bm;           // clear early to prevent overwrite
    uint16_t d16  = cnt - ccmp;            // ticks since the edge (same tick rate as TCB0)
    uint32_t now  = tcb0_now_coherent_isr_only();   // race-free 32-bit read of TCB0 time
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
// | `now32 = tcb0_now_coherent_isr_only()`  | ~20    | coherent overflow/CNT sample in TCB0 domain     |
// | `latency16` + `edge32` arithmetic  | ~6     | 16-bit sub + 32-bit backdate                    |
// | Tick/tock branch logic             | ~2-4   | branch on `isTick`                              |
// | `push_event(...)`                  | ~33    | ring index, stores, dropped-event guard         |
// | Update `TCB1.EVCTRL` + `isTick`    | ~4     | select next edge + state toggle                 |
// | **Total (CAPT path)**              | **~102-107** | **~6.4-6.7µs**                             |
// |-----------------------------------------------------------------------------------------------|
ISR(TCB1_INT_vect) {
  const uint8_t flags = TCB1.INTFLAGS;
  TCB1.INTFLAGS = flags;

  if (!(flags & TCB_CAPT_bm)) {
    return;
  }

  // Latch the captured edge time (TCB1 domain)
  const uint16_t ccmp = TCB1.CCMP;

  // Coherent 32-bit "now" in TCB0 domain (t2)
  const uint32_t now32 = tcb0_now_coherent_isr_only();

  // Tightened: sample CNT as close as possible to now32 (also ~t2)
  const uint16_t cnt = TCB1.CNT;

  // Latency since edge measured at ~t2, in TCB1 ticks
  const uint16_t latency16 = (uint16_t)(cnt - ccmp);

  // Backdate into TCB0 domain
  const uint32_t edge32 = now32 - (uint32_t)latency16;

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
// | `now32 = tcb0_now_coherent_isr_only()`   | ~20    | coherent overflow/CNT sample in TCB0 domain     |
// | `latency16` + `edge32` arithmetic   | ~6     | 16-bit sub + 32-bit backdate                    |
// | `pps_seen++`                        | ~10    | 32-bit increment                                |
// | `ppsData_push_isr(...)`             | ~28    | ring-buffer index + payload stores              |
// | **Total (CAPT path)**               | **~103-110** | **~6.4-6.9µs**                            |
// |------------------------------------------------------------------------------------------------|
ISR(TCB2_INT_vect) {
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
  const uint32_t now32 = tcb0_now_coherent_isr_only();

  // Tightened: sample CNT as close as possible to now32 (also ~t2)
  const uint16_t cnt = TCB2.CNT;

  // Latency since edge measured at ~t2, in TCB2 ticks
  const uint16_t latency16 = (uint16_t)(cnt - ccmp);

  // Backdate into TCB0 domain
  const uint32_t edge32 = now32 - (uint32_t)latency16;

  pps_seen++;
  ppsData_push_isr(edge32, now32, tcb0Ovf, ccmp, cnt, latency16);
}

/*
About PPS_BASE field `l` (raw `latency16`) and why it sometimes shows structure
-------------------------------------------------------------------------------

In the PPS capture ISR (`ISR(TCB2_INT_vect)`), we compute:

    const uint16_t ccmp      = TCB2.CCMP;
    const uint16_t now_cnt   = TCB2.CNT;
    const uint32_t now32     = tcb0_now_coherent_isr_only();
    const uint16_t latency16 = (uint16_t)(now_cnt - ccmp);
    const uint32_t edge32    = now32 - (uint32_t)latency16;

and `latency16` is later reported in `PPS_BASE` as field `l`.

Meaning of `latency16` / `l`
----------------------------

`latency16` is the elapsed time, in TCB2 ticks, between:
  1) the hardware-captured PPS edge time latched in `TCB2.CCMP`, and
  2) the later point in the ISR when `TCB2.CNT` is sampled.

So `l` is primarily an ISR service-latency diagnostic.
It is NOT, by itself, a direct timestamp-error metric.

Why `l` can show a quantized main mode plus a rare secondary bump
-----------------------------------------------------------------

In analysis, `l` usually has:
  - a dominant mode at the normal PPS ISR service latency,
  - 1-tick quantization (expected from timer granularity), and
  - a rare higher-latency secondary mode.

A likely explanation for the rare higher-latency mode is coincidence with
`TCB0` wrap / overflow bookkeeping:

  - `ISR(TCB2_INT_vect)` timestamps PPS edges onto the shared TCB0+overflow
    32-bit timebase via `tcb0_now_coherent_isr_only()`.
  - If the PPS edge arrives very close to a 16-bit wrap of TCB0, then
    `ISR(TCB2_INT_vect)` can coincide with `ISR(TCB0_INT_vect)` or with the
    coherent-read logic handling an overflow-adjacent sample.
  - That can increase observed `latency16` without implying that `edge32`
    reconstruction is wrong.

Intuition:
    latency16 ~= normal PPS ISR latency
or, more rarely,
    latency16 ~= normal PPS ISR latency + wrap/overflow coincidence overhead

Why this is usually harmless
----------------------------

The PPS edge time used by the rest of the system is `edge32`, not `now32`.
We explicitly backdate from `now32` by `latency16`:

    edge32 = now32 - (uint32_t)latency16;

So a larger `latency16` does NOT automatically mean a bad PPS timestamp.
What matters is whether `edge32` is reconstructed correctly.

In the analysed baseline runs:
  - `l` / `latency16` showed real structure,
  - but there was no evidence that this structure caused bad discipline,
    unlocks, or obvious PPS timestamp failure.
It appeared to be a harmless implementation fingerprint, especially near
wrap-adjacent cases.

Related observability
---------------------

Useful nearby, real fields/counters in the current codebase:
  - `PPS_BASE` field `l`  -> this raw `latency16` value.
  - `PPS_BASE` field `cp` -> raw captured `TCB2.CCMP` (`cap16`) for the same PPS sample.
  - `PPS_BASE` fields `r`, `js`, `ja` -> discipliner output/error context in the
    same line, useful when checking whether latency changes correlate with control behavior.
  - `PPS_BASE` fields `lg`, `ub`, `s`, `v`, `c` -> lock/unlock/state/validity/class
    context for the same PPS sample.
  - `capturePpsSeen()` / `pps_seen` -> how many PPS captures reached the ring.
  - `captureDroppedEvents()` / `droppedEvents` -> capture-ring pressure symptom.
    This does not include formatter/emit-side drops; correlate with serial STS
    counters (`serial_diag`: `fmt_acq_fail`, `queue_reject`, `tx_reentry_drop`)
    to separate capture loss from outbound telemetry congestion.
  - `coherentOvfFlagSeenCount` and `coherentOvfAppliedCount` -> internal counters
    in `tcb0_now_coherent_isr_only()` that track overflow-adjacent coherent reads.

When to care
------------

Investigate further only if one or more of the following start happening:
  - elevated `latency16` events become frequent,
  - `l` starts correlating with `r` / `js` / `ja` or with `lg` / `ub` / `s` changes,
  - `captureDroppedEvents()` rises during PPS activity,
  - or there is evidence that `edge32` reconstruction is actually wrong.

Until then, treat `l` as a useful ISR-timing diagnostic, not as a failure
indicator by itself.
*/
