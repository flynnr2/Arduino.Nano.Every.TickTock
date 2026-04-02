#include "DisciplinedTime.h"

void DisciplinedTime::begin(uint32_t f_cpu_nominal) {
  f_cpu_nominal_ = f_cpu_nominal;
  f_hat_ = f_cpu_nominal;
  state_ = FreqDiscipliner::DiscState::FREE_RUN;
  pps_valid_ = false;
  holdover_age_ms_ = 0;
}

void DisciplinedTime::sync(const FreqDiscipliner& discipliner, bool pps_valid) {
  state_ = discipliner.state();
  pps_valid_ = pps_valid;
  holdover_age_ms_ = discipliner.holdoverAgeMs();

  switch (state_) {
    case FreqDiscipliner::DiscState::DISCIPLINED:
      // In DISCIPLINED, use the smoother slow estimate for metrology stability.
      f_hat_ = discipliner.slow();
      break;
    case FreqDiscipliner::DiscState::ACQUIRE:
    case FreqDiscipliner::DiscState::HOLDOVER:
      // HOLDOVER is anchored by the discipliner on the last known-good slow
      // estimate, which is exposed through applied() while holdover is active.
      f_hat_ = discipliner.applied();
      break;
    case FreqDiscipliner::DiscState::FREE_RUN:
    default:
      f_hat_ = f_cpu_nominal_;
      break;
  }

  if (f_hat_ == 0) f_hat_ = f_cpu_nominal_;
}

uint32_t DisciplinedTime::ticksPerSecond() const {
  return f_hat_ ? f_hat_ : f_cpu_nominal_;
}

uint32_t DisciplinedTime::ticksToMillis(uint32_t dt_ticks) const {
  const uint32_t tps = ticksPerSecond();
  if (tps == 0U) return 0U;
  const uint64_t scaled = (uint64_t)dt_ticks * 1000ULL;
  return (uint32_t)((scaled + (uint64_t)(tps / 2U)) / (uint64_t)tps);
}

int32_t DisciplinedTime::ticksToPpmX1000(uint32_t dt_ticks, uint32_t nominal_period_ticks) const {
  if (nominal_period_ticks == 0U) return 0;
  const int64_t delta_ticks = (int64_t)dt_ticks - (int64_t)nominal_period_ticks;
  const int64_t scaled = delta_ticks * 1000000000LL;
  const int64_t ppm_x1000 = scaled / (int64_t)nominal_period_ticks;
  if (ppm_x1000 > INT32_MAX) return INT32_MAX;
  if (ppm_x1000 < INT32_MIN) return INT32_MIN;
  return (int32_t)ppm_x1000;
}

DisciplinedTime::Quality DisciplinedTime::timeQuality() const {
  Quality q{};
  q.disc_state = state_;
  q.pps_valid = pps_valid_;
  q.holdover_age_ms = holdover_age_ms_;
  switch (state_) {
    case FreqDiscipliner::DiscState::DISCIPLINED: q.confidence = 100; break;
    case FreqDiscipliner::DiscState::ACQUIRE: q.confidence = 70; break;
    case FreqDiscipliner::DiscState::HOLDOVER: q.confidence = (holdover_age_ms_ > 600000UL) ? 20 : 50; break;
    default: q.confidence = 10; break;
  }
  return q;
}
