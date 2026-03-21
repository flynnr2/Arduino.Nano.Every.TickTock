#pragma once

#include "FreqDiscipliner.h"
#include <stdint.h>

class DisciplinedTime {
public:
  struct Quality {
    FreqDiscipliner::DiscState disc_state;
    bool pps_valid;
    uint8_t confidence;
    uint32_t holdover_age_ms;
  };

  void begin(uint32_t f_cpu_nominal);
  void sync(const FreqDiscipliner& discipliner, bool pps_valid);

  uint32_t ticksPerSecond() const;
  double ticksToSeconds(uint32_t dt_ticks) const;
  double ticksToPpm(uint32_t dt_ticks, double nominal_period_s) const;
  Quality timeQuality() const;

private:
#if defined(F_CPU)
  static constexpr uint32_t kDefaultFcpu = static_cast<uint32_t>(F_CPU);
#else
  static constexpr uint32_t kDefaultFcpu = 16UL * 1000000UL;
#endif

  uint32_t f_cpu_nominal_ = kDefaultFcpu;
  uint32_t f_hat_ = kDefaultFcpu;
  FreqDiscipliner::DiscState state_ = FreqDiscipliner::DiscState::FREE_RUN;
  bool pps_valid_ = false;
  uint32_t holdover_age_ms_ = 0;
};
