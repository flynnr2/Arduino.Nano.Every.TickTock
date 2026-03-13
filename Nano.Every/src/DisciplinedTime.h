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
  uint32_t f_cpu_nominal_ = 16000000UL;
  uint32_t f_hat_ = 16000000UL;
  FreqDiscipliner::DiscState state_ = FreqDiscipliner::DiscState::FREE_RUN;
  bool pps_valid_ = false;
  uint32_t holdover_age_ms_ = 0;
};
