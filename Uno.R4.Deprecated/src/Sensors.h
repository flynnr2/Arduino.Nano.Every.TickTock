#pragma once

#include <stdint.h>

// Keep the small health helpers buildable in host tests without Arduino libraries.
#if !defined(SENSORS_HOST_TEST)
#include <Adafruit_BMP280.h>
#include <Adafruit_SHT4x.h>
#include "Config.h"
#endif

namespace Sensors {

  enum class HealthState : uint8_t {
    Initializing = 0,
    Ready,
    Degraded,
    Stale,
    Offline
  };

  struct ChannelHealth {
    HealthState state;
    bool initialized;
    bool fresh;
    bool suspect;
    uint16_t consecutiveFailures;
    uint16_t initFailures;
    uint32_t lastSuccessMs;
    uint32_t nextAttemptMs;
  };

  struct HealthSnapshot {
    ChannelHealth sht4x;
    ChannelHealth bmp280;
  };

  namespace detail {
    constexpr uint8_t kFreshnessPeriods = 3;

    inline bool timeReached(uint32_t nowMs, uint32_t dueMs) {
      return static_cast<int32_t>(nowMs - dueMs) >= 0;
    }

    inline bool isFresh(uint32_t nowMs, uint32_t lastSuccessMs, bool hasSuccess,
                        uint32_t periodMs) {
      return hasSuccess && (uint32_t)(nowMs - lastSuccessMs) <=
          periodMs * kFreshnessPeriods;
    }

    inline uint32_t retryBackoffMs(uint8_t attempt) {
      // 1, 2, 4, 8, 16, then 30 seconds.  No unbounded retry delay.
      if (attempt == 0) return 1000UL;
      if (attempt == 1) return 1000UL;
      if (attempt == 2) return 2000UL;
      if (attempt == 3) return 4000UL;
      if (attempt == 4) return 8000UL;
      if (attempt == 5) return 16000UL;
      return 30000UL;
    }
  } // namespace detail

  // Schedules independent initialization. I2C work starts in poll().
  void begin();
  // Performs at most one sensor I2C operation and yields while ingest is pending.
  void poll();
  // Returns NAN for any channel whose last successful reading has expired.
  void getLatest(float &temperature, float &humidity, float &pressure);
  const HealthSnapshot& health();
  const char* healthStateName(HealthState state);
  void scanI2C();
}
