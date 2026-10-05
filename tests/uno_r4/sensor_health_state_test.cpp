#define SENSORS_HOST_TEST 1
#include "../../Uno.R4.Deprecated/src/Sensors.h"

#include <assert.h>
#include <stdint.h>

int main() {
  using Sensors::detail::isFresh;
  using Sensors::detail::retryBackoffMs;
  using Sensors::detail::timeReached;

  // A successful sample remains usable for exactly three polling periods.
  assert(isFresh(4000u, 1000u, true, 1000u));
  assert(!isFresh(4001u, 1000u, true, 1000u));
  assert(!isFresh(1000u, 1000u, false, 1000u));

  // Unsigned arithmetic keeps the freshness and due checks valid across wrap.
  assert(isFresh(0x00000010u, 0xFFFFFFF0u, true, 1000u));
  assert(timeReached(0x00000010u, 0xFFFFFFF0u));
  assert(!timeReached(0xFFFFFFF0u, 0x00000010u));

  // Retry delays grow but cap, so a failed device is retried indefinitely.
  assert(retryBackoffMs(1) == 1000u);
  assert(retryBackoffMs(4) == 8000u);
  assert(retryBackoffMs(6) == 30000u);
  assert(retryBackoffMs(255) == 30000u);
  return 0;
}
