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
      f_hat_ = discipliner.slow();
      break;
    case FreqDiscipliner::DiscState::ACQUIRE:
      f_hat_ = discipliner.applied();
      break;
    case FreqDiscipliner::DiscState::HOLDOVER:
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

double DisciplinedTime::ticksToSeconds(uint32_t dt_ticks) const {
  return (double)dt_ticks / (double)ticksPerSecond();
}

double DisciplinedTime::ticksToPpm(uint32_t dt_ticks, double nominal_period_s) const {
  const double sec = ticksToSeconds(dt_ticks);
  return 1000000.0 * ((sec / nominal_period_s) - 1.0);
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
