#pragma once

#include "Config.h"
#include "FreqDiscipliner.h"
#include <stdint.h>

class DisciplinedTime {
public:
  struct Quality {
    uint32_t holdover_age_ms;               // [0, 2^32-1] monotonic ms age while in HOLDOVER.
    FreqDiscipliner::DiscState disc_state;  // [0, 3] discipliner state enum.
    uint8_t confidence;                     // [0, 100] coarse quality score for telemetry.
    bool pps_valid;                         // [0, 1] validator lock indicator.
  };

  void begin(uint32_t f_cpu_nominal);
  void sync(const FreqDiscipliner& discipliner, bool pps_valid);

  uint32_t ticksPerSecond() const;
  uint32_t ticksToMillis(uint32_t dt_ticks) const;
  int32_t ticksToPpmX1000(uint32_t dt_ticks, uint32_t nominal_period_ticks) const;
  Quality timeQuality() const;

private:
#if defined(MAIN_CLOCK_HZ)
  static constexpr uint32_t kDefaultFcpu = static_cast<uint32_t>(MAIN_CLOCK_HZ);
#else
  static constexpr uint32_t kDefaultFcpu = 16UL * 1000000UL;
#endif

  uint32_t f_cpu_nominal_ = kDefaultFcpu;
  uint32_t f_hat_ = kDefaultFcpu;
  FreqDiscipliner::DiscState state_ = FreqDiscipliner::DiscState::FREE_RUN;
  bool pps_valid_ = false;
  uint32_t holdover_age_ms_ = 0;
};
