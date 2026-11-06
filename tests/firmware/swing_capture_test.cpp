#include <cassert>
#include <deque>
#include "PendulumCapture.h"
#include "SwingAssembler.h"

static std::deque<EdgeEvent> edges;
static uint32_t dropped = 0;
bool captureTryPopEdge(EdgeEvent* out) {
  if (edges.empty()) return false;
  *out = edges.front();
  edges.pop_front();
  return true;
}
void captureRecordSwingRowDrop() { ++dropped; }
static void feed(uint32_t tick, uint8_t type) {
  edges.push_back(EdgeEvent{tick, type});
  swingAssemblerProcessEdges();
}
int main() {
  static_assert(sizeof(FullSwing) == 6 * sizeof(uint32_t), "Capture boundaries only");
  FullSwing row{};
  assert(!swingAssemblerTryPeekOldest(&row));
  assert(!swingAssemblerTryPeekOldest(nullptr));
  feed(50, 1); // Ignore the wrong initial polarity.
  const uint32_t start = UINT32_MAX - 15;
  feed(start, 0);
  feed(start + 4, 1);
  feed(start + 8, 0);
  feed(start + 12, 1);
  assert(!swingAssemblerTryPeekOldest(&row));
  feed(start + 20, 0); // Counter wrap is preserved exactly.
  assert(swingAssemblerTryPeekOldest(&row));
  assert(row.swing_seq == 1 && row.edge0_tcb0 == start && row.edge4_tcb0 == 4);
  assert(row.edge1_tcb0 == start + 4 && row.edge2_tcb0 == start + 8 && row.edge3_tcb0 == start + 12);
  swingAssemblerRecordEmitAttemptFailed();
  FullSwing again{};
  assert(swingAssemblerTryPeekOldest(&again));
  assert(again.swing_seq == row.swing_seq && again.edge4_tcb0 == row.edge4_tcb0);
  assert(swingAssemblerEmitAttemptFailedCount() == 1);
  assert(swingAssemblerRetireOldest());
  assert(!swingAssemblerRetireOldest());

  // Keep the oldest row under backpressure and count newly completed losses.
  for (uint32_t n = 0; n < 9; ++n) {
    const uint32_t base = 4 + n * 20;
    feed(base + 4, 1); feed(base + 8, 0); feed(base + 12, 1); feed(base + 20, 0);
  }
  assert(dropped == 2 && swingAssemblerTransportDropCount() == 2);
  for (uint32_t n = 0; n < 7; ++n) {
    assert(swingAssemblerTryPeekOldest(&row));
    assert(row.swing_seq == n + 2);
    assert(row.edge0_tcb0 == 4 + n * 20);
    assert(row.edge4_tcb0 == row.edge0_tcb0 + 20);
    assert(swingAssemblerRetireOldest());
  }
  assert(!swingAssemblerTryPeekOldest(&row));
  feed(188, 1); feed(192, 0); feed(196, 1); feed(204, 0);
  assert(swingAssemblerTryPeekOldest(&row));
  assert(row.swing_seq == 11 && row.edge0_tcb0 == 184);
}
