#include "Config.h"

#include <util/atomic.h>

#include "PendulumCapture.h"
#include "SwingAssembler.h"

namespace {

static inline uint32_t elapsed32(uint32_t now, uint32_t then) {
  return (uint32_t)(now - then);
}

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
