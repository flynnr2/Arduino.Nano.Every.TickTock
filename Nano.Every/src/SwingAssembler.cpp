#include "Config.h"

#include <util/atomic.h>

#include "PendulumCapture.h"
#include "SwingAssembler.h"

namespace {

static inline uint32_t elapsed32(uint32_t now, uint32_t then) {
  return (uint32_t)(now - then);
}

#if ENABLE_STS_GPS_DEBUG || ENABLE_PPS_BASELINE_TELEMETRY
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

static constexpr uint32_t MIN_EDGE_DELTA_TICKS = (uint32_t)(F_CPU / 5UL);
static constexpr uint32_t MAX_EDGE_DELTA_TICKS = (uint32_t)(F_CPU * 3UL);
static constexpr uint32_t WRAP_TICKS = 65536UL;
static constexpr uint32_t WRAP_TOL_TICKS = 2048UL;
#endif

constexpr uint8_t SWING_RING_SIZE = RING_SIZE_IR_SENSOR;
static_assert((SWING_RING_SIZE & (SWING_RING_SIZE - 1U)) == 0U, "SWING_RING_SIZE must be power-of-two for mask arithmetic");

FullSwing swing_buf[SWING_RING_SIZE];
volatile uint8_t swing_head = 0;
volatile uint8_t swing_tail = 0;

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
      case 0:
        if (e.type == 0) {
          last_ts = e.ticks;
          swing_state = 1;
        }
        break;
      case 1:
        if (e.type == 1) {
          curr.tick_block = elapsed32(e.ticks, last_ts);
          last_ts = e.ticks;
          swing_state = 2;
        }
        break;
      case 2:
        if (e.type == 0) {
          curr.tick = elapsed32(e.ticks, last_ts);
          last_ts = e.ticks;
          swing_state = 3;
        }
        break;
      case 3:
        if (e.type == 1) {
          curr.tock_block = elapsed32(e.ticks, last_ts);
          last_ts = e.ticks;
          swing_state = 4;
        }
        break;
      case 4:
        if (e.type == 0) {
          curr.tock = elapsed32(e.ticks, last_ts);
          swing_push(curr);
          last_ts = e.ticks;
          swing_state = 1;
        }
        break;
    }
  }
}

SwingDiagnostics swingAssemblerDiagnostics() {
  SwingDiagnostics diag{};
#if ENABLE_STS_GPS_DEBUG || ENABLE_PPS_BASELINE_TELEMETRY
  diag.edge_count = pend_edge_count;
  diag.backstep_count = pend_backstep_count;
  diag.big_jump_count = pend_big_jump_count;
  diag.small_jump_count = pend_small_jump_count;
  diag.wrapish_count = pend_wrapish_count;
  diag.last_bad_seq = pend_last_bad_seq;
  diag.last_bad_delta = pend_last_bad_delta;
#endif
  return diag;
}
