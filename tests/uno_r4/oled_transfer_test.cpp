#include "../../Uno.R4.Deprecated/src/OledTransfer.h"
#include <cassert>
#include <cstring>
#include <cstdio>

int main() {
  static_assert(sizeof(OledTransfer) <= 112, "Keep OLED delta state small");
  OledTransfer transfer;
  uint8_t buffer[1024] = {}, panel[1024] = {};
  uint32_t now = 0xfffffff0u;
  unsigned calls = 0, page = 0, offset = 0;
  int failureAt = -1;
  auto clock = [&]() { return now; };
  auto write = [&](uint8_t control, const uint8_t* bytes, size_t count) -> uint8_t {
    ++calls; now += 3;
    assert(count <= 31);
    if (int(calls) == failureAt) return 5;
    if (control == 0) {
      assert(count == 6 && bytes[0] == 0x21 && bytes[1] == 0 && bytes[2] == 127);
      assert(bytes[3] == 0x22 && bytes[4] == bytes[5] && bytes[4] < 8);
      page = bytes[4]; offset = 0;
    } else {
      assert(control == 0x40 && offset + count <= 128);
      memcpy(panel + page * 128 + offset, bytes, count); offset += count;
    }
    return 0;
  };
  auto drain = [&]() {
    while (transfer.active()) {
      unsigned before = calls;
      transfer.step(buffer, clock, write);
      assert(calls == before + 1); // Never a full-frame blocking burst.
    }
  };
  memset(buffer, 0x42, sizeof(buffer));
  transfer.begin(buffer, now); drain();
  assert(transfer.metrics.completed == 1 && transfer.metrics.pagesSent == 8);
  assert(transfer.metrics.maxFrameMs == 144); // Includes final transfer and millis wrap.
  assert(!memcmp(panel, buffer, 1024));
  unsigned before = calls;
  transfer.begin(buffer, now); drain();
  assert(calls == before && transfer.metrics.unchanged == 1);
  buffer[3 * 128 + 127] ^= 1;
  transfer.begin(buffer, now); drain();
  assert(calls == before + 6 && transfer.metrics.pagesSent == 9);
  assert(!memcmp(panel, buffer, 1024));
  // Two disjoint changed pages; no intervening unchanged pages are written.
  buffer[0] ^= 1; buffer[1023] ^= 1;
  before = calls; transfer.begin(buffer, now); drain();
  assert(calls == before + 12 && !memcmp(panel, buffer, 1024));
  buffer[0] ^= 2;
  failureAt = int(calls) + 3; // Partial data then timeout.
  transfer.begin(buffer, now); drain();
  assert(transfer.metrics.failed == 1 && transfer.metrics.lastError == 5);
  failureAt = -1;
  buffer[0] ^= 2; // Even reverting to old content must repair the partial page.
  before = calls; transfer.begin(buffer, now); drain();
  assert(calls == before + 6 && !memcmp(panel, buffer, 1024));
  transfer.invalidate(); before = calls; transfer.begin(buffer, now); drain();
  assert(calls == before + 48);
  puts("OLED deltas, chunk bounds, partial-write recovery, full refresh and wrap passed");
}
