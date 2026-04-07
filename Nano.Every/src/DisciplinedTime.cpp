#include "DisciplinedTime.h"

namespace {

static inline uint32_t elapsedMsWrapSafe(uint32_t now_ms, uint32_t then_ms) {
  return (uint32_t)(now_ms - then_ms);
}

}  // namespace

const char* DisciplinedTime::exportModeName(ExportMode mode) {
  switch (mode) {
    case ExportMode::NOMINAL: return "NOMINAL";
    case ExportMode::BLEND_TRACK: return "BLEND_TRACK";
    case ExportMode::SLOW_TRACK: return "SLOW_TRACK";
    case ExportMode::SLOW_GRACE: return "SLOW_GRACE";
    case ExportMode::SLOW_HOLDOVER: return "SLOW_HOLDOVER";
    default: return "UNKNOWN";
  }
}

void DisciplinedTime::begin(uint32_t f_cpu_nominal) {
  f_cpu_nominal_ = f_cpu_nominal;
  f_hat_ = f_cpu_nominal;
  grace_anchor_hz_ = f_cpu_nominal;
  grace_start_ms_ = 0;
  state_ = FreqDiscipliner::DiscState::FREE_RUN;
  prev_state_ = FreqDiscipliner::DiscState::FREE_RUN;
  export_mode_ = ExportMode::NOMINAL;
  pps_valid_ = false;
  has_disciplined_once_ = false;
  holdover_age_ms_ = 0;
}

void DisciplinedTime::sync(const FreqDiscipliner& discipliner, bool pps_valid, uint32_t now_ms) {
  prev_state_ = state_;
  state_ = discipliner.state();
  pps_valid_ = pps_valid;
  holdover_age_ms_ = discipliner.holdoverAgeMs();

  const bool mild_unlock_transition =
      (prev_state_ == FreqDiscipliner::DiscState::DISCIPLINED) &&
      (state_ == FreqDiscipliner::DiscState::ACQUIRE) &&
      pps_valid_;

  if (state_ == FreqDiscipliner::DiscState::DISCIPLINED) {
    has_disciplined_once_ = true;
  }
  if (mild_unlock_transition) {
    grace_start_ms_ = now_ms;
    grace_anchor_hz_ = discipliner.lastGoodSlow();
    if (grace_anchor_hz_ == 0U) grace_anchor_hz_ = f_cpu_nominal_;
  }

  switch (state_) {
    case FreqDiscipliner::DiscState::FREE_RUN:
      export_mode_ = ExportMode::NOMINAL;
      break;
    case FreqDiscipliner::DiscState::DISCIPLINED:
      export_mode_ = ExportMode::SLOW_TRACK;
      break;
    case FreqDiscipliner::DiscState::HOLDOVER:
      export_mode_ = ExportMode::SLOW_HOLDOVER;
      break;
    case FreqDiscipliner::DiscState::ACQUIRE:
    default:
      if (mild_unlock_transition) {
        export_mode_ = ExportMode::SLOW_GRACE;
      } else if (export_mode_ == ExportMode::SLOW_GRACE) {
        const uint32_t grace_ms = (uint32_t)Tunables::ppsMetrologyGraceMsActive();
        const bool grace_expired =
            elapsedMsWrapSafe(now_ms, grace_start_ms_) >= grace_ms;
        if (grace_expired || !pps_valid_ || !has_disciplined_once_) {
          export_mode_ = ExportMode::BLEND_TRACK;
        }
      } else {
        export_mode_ = ExportMode::BLEND_TRACK;
      }
      break;
  }

  switch (export_mode_) {
    case ExportMode::NOMINAL:
      f_hat_ = f_cpu_nominal_;
      break;
    case ExportMode::BLEND_TRACK:
      f_hat_ = discipliner.applied();
      break;
    case ExportMode::SLOW_TRACK:
      f_hat_ = discipliner.slow();
      break;
    case ExportMode::SLOW_GRACE:
      f_hat_ = grace_anchor_hz_;
      break;
    case ExportMode::SLOW_HOLDOVER:
      f_hat_ = discipliner.lastGoodSlow();
      break;
    default:
      f_hat_ = f_cpu_nominal_;
      break;
  }

  if (f_hat_ == 0U) f_hat_ = f_cpu_nominal_;
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
  q.export_mode = export_mode_;
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
