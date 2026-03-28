#include "Config.h"

#include <util/atomic.h>

#include "PendulumCapture.h"
#include "PpsAdjust.h"
#include "SwingAssembler.h"

namespace {

static inline uint32_t elapsed32(uint32_t now, uint32_t then) {
  return (uint32_t)(now - then);
}

constexpr uint8_t SWING_RING_SIZE = RING_SIZE_SWING_ROWS;
static_assert((SWING_RING_SIZE & (SWING_RING_SIZE - 1U)) == 0U, "SWING_RING_SIZE must be power-of-two for mask arithmetic");

FullSwing swing_buf[SWING_RING_SIZE];
volatile uint8_t swing_head = 0;
volatile uint8_t swing_tail = 0;

// SRAM guardrails: rows are produced at pendulum cadence (much slower than edge ISR
// cadence), so this queue should stay compact on ATmega4809.
static_assert(sizeof(FullSwing) <= 48U, "FullSwing grew unexpectedly; revisit SRAM budget");
static_assert(sizeof(swing_buf) <= 1024U, "Swing row ring exceeds SRAM budget");

static inline uint8_t swing_mask(uint8_t v) { return v & (SWING_RING_SIZE - 1); }

static inline void swing_push(const FullSwing &s) {
  uint8_t n = swing_mask(swing_head + 1);
  if (n != swing_tail) {
    swing_buf[swing_head] = s;
    swing_head = n;
  } else {
    captureRecordDroppedEvent();
  }
}

static void adjust_interval_or_fallback(uint32_t start_tick32,
                                        uint32_t end_tick32,
                                        uint32_t raw_ticks,
                                        uint8_t crossed_bit,
                                        uint32_t* adj_out,
                                        uint8_t* diag_bits) {
  PpsTaggedStamp start{};
  PpsTaggedStamp end{};
  if (!ppsAdjustTagTick(start_tick32, &start) || !ppsAdjustTagTick(end_tick32, &end)) {
    *adj_out = raw_ticks;
    *diag_bits |= (ADJ_DIAG_MISSING_SCALE | ADJ_DIAG_DEGRADED_FALLBACK);
    return;
  }

  uint32_t adjusted = raw_ticks;
  if (!ppsAdjustIntervalToNominal16Mhz(start, end, raw_ticks, &adjusted, diag_bits, crossed_bit)) {
    *adj_out = raw_ticks;
    return;
  }
  *adj_out = adjusted;
}

} // namespace

bool swingAssemblerAvailable() {
  return swing_tail != swing_head;
}

FullSwing swingAssemblerPop() {
  FullSwing s;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    s = swing_buf[swing_tail];
    swing_tail = swing_mask(swing_tail + 1);
  }
  return s;
}

void swingAssemblerProcessEdges() {
  static uint8_t swing_state = 0;
  static uint32_t last_ts = 0;
  static FullSwing curr;

  while (captureEdgeAvailable()) {
    EdgeEvent e = capturePopEdge();

    switch (swing_state) {
      case 0:
        if (e.type == 0) {
          last_ts = e.ticks;
          curr.adj_diag = 0U;
#if ENABLE_PENDULUM_ADJ_PROVENANCE
          curr.pps_seq_row = 0U;
#endif
          swing_state = 1;
        }
        break;
      case 1:
        if (e.type == 1) {
          curr.tick_block = elapsed32(e.ticks, last_ts);
          adjust_interval_or_fallback(last_ts,
                                      e.ticks,
                                      curr.tick_block,
                                      ADJ_DIAG_TICK_BLOCK_CROSSED,
                                      &curr.tick_block_adj,
                                      &curr.adj_diag);
          last_ts = e.ticks;
          swing_state = 2;
        }
        break;
      case 2:
        if (e.type == 0) {
          curr.tick = elapsed32(e.ticks, last_ts);
          adjust_interval_or_fallback(last_ts,
                                      e.ticks,
                                      curr.tick,
                                      ADJ_DIAG_TICK_CROSSED,
                                      &curr.tick_adj,
                                      &curr.adj_diag);
          last_ts = e.ticks;
          swing_state = 3;
        }
        break;
      case 3:
        if (e.type == 1) {
          curr.tock_block = elapsed32(e.ticks, last_ts);
          adjust_interval_or_fallback(last_ts,
                                      e.ticks,
                                      curr.tock_block,
                                      ADJ_DIAG_TOCK_BLOCK_CROSSED,
                                      &curr.tock_block_adj,
                                      &curr.adj_diag);
          last_ts = e.ticks;
          swing_state = 4;
        }
        break;
      case 4:
        if (e.type == 0) {
          curr.tock = elapsed32(e.ticks, last_ts);
          adjust_interval_or_fallback(last_ts,
                                      e.ticks,
                                      curr.tock,
                                      ADJ_DIAG_TOCK_CROSSED,
                                      &curr.tock_adj,
                                      &curr.adj_diag);
#if ENABLE_PENDULUM_ADJ_PROVENANCE
          PpsTaggedStamp row_stamp{};
          curr.pps_seq_row = ppsAdjustTagTick(e.ticks, &row_stamp) ? row_stamp.pps_seq : ppsAdjustCurrentSeq();
#endif
          swing_push(curr);
          last_ts = e.ticks;
          curr.adj_diag = 0U;
          swing_state = 1;
        }
        break;
    }
  }
}
