#include "Config.h"

#include <util/atomic.h>

#include "PendulumCapture.h"
#include "PendulumProtocol.h"
#include "SwingAssembler.h"

namespace {

constexpr uint8_t SWING_RING_SIZE = RING_SIZE_SWING_ROWS;
static_assert((SWING_RING_SIZE & (SWING_RING_SIZE - 1U)) == 0U, "SWING_RING_SIZE must be power-of-two for mask arithmetic");

FullSwing swing_buf[SWING_RING_SIZE];
volatile uint8_t swing_head = 0;
volatile uint8_t swing_tail = 0;
uint32_t swing_seq = 0;
volatile uint32_t swing_emit_attempt_failures = 0;
volatile uint32_t swing_transport_drops = 0;

// SRAM guardrails: rows are produced at pendulum cadence (much slower than edge ISR
// cadence), so this queue should stay compact on ATmega4809.
static_assert(sizeof(FullSwing) == 24U, "FullSwing grew unexpectedly; revisit SRAM budget");
static_assert(sizeof(swing_buf) <= 256U, "Swing row ring exceeds SRAM budget");

static inline uint8_t swing_mask(uint8_t v) { return v & (SWING_RING_SIZE - 1); }

static inline void swing_push(const FullSwing &s) {
  uint8_t n = swing_mask(swing_head + 1);
  if (n != swing_tail) {
    swing_buf[swing_head] = s;
    swing_head = n;
  } else {
    // Ring full under serial backpressure: preserve existing pending rows and
    // drop this newly completed row explicitly/countably.
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      swing_transport_drops++;
    }
    captureRecordSwingRowDrop();
  }
}

} // namespace

bool swingAssemblerTryPeekOldest(FullSwing* out) {
  if (out == nullptr) {
    return false;
  }

  // Swing rows are produced and consumed only by the foreground loop. Capture
  // ISRs publish EdgeEvent/PpsCapture records, but never touch this ring. Take
  // the volatile queue-index snapshot atomically, then copy the large row with
  // interrupts enabled. The selected slot cannot change until this same
  // foreground caller later retires it.
  uint8_t tail_snapshot = 0U;
  bool peeked = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (swing_tail != swing_head) {
      tail_snapshot = swing_tail;
      peeked = true;
    }
  }
  if (peeked) {
    *out = swing_buf[tail_snapshot];
  }
  return peeked;
}

bool swingAssemblerRetireOldest() {
  bool retired = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (swing_tail != swing_head) {
      swing_tail = swing_mask(swing_tail + 1);
      retired = true;
    }
  }
  return retired;
}

void swingAssemblerRecordEmitAttemptFailed() {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    swing_emit_attempt_failures++;
  }
}

uint32_t swingAssemblerEmitAttemptFailedCount() {
  uint32_t count = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    count = swing_emit_attempt_failures;
  }
  return count;
}

uint32_t swingAssemblerTransportDropCount() {
  uint32_t count = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    count = swing_transport_drops;
  }
  return count;
}

void swingAssemblerProcessEdges() {
  static uint8_t swing_state = 0;
  static FullSwing curr{};

  EdgeEvent e;
  while (captureTryPopEdge(&e)) {
    // HIGH = beam blocked, LOW = beam open. The five boundaries alternate
    // falling/rising/falling/rising/falling. Adjacent swings share edge4/edge0.
    switch (swing_state) {
      case 0:
        if (e.type == 0) {
          curr.swing_seq = ++swing_seq;
          curr.edge0_tcb0 = e.ticks;
          swing_state = 1;
        }
        break;
      case 1:
        if (e.type == 1) {
          curr.edge1_tcb0 = e.ticks;
          swing_state = 2;
        }
        break;
      case 2:
        if (e.type == 0) {
          curr.edge2_tcb0 = e.ticks;
          swing_state = 3;
        }
        break;
      case 3:
        if (e.type == 1) {
          curr.edge3_tcb0 = e.ticks;
          swing_state = 4;
        }
        break;
      case 4:
        if (e.type == 0) {
          curr.edge4_tcb0 = e.ticks;
          swing_push(curr);
          curr.swing_seq = ++swing_seq;
          curr.edge0_tcb0 = e.ticks;
          swing_state = 1;
        }
        break;
    }
  }
}
