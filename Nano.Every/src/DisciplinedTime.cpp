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
  export_mode_ = ExportMode::NOMINAL;
  has_disciplined_once_ = false;
}

void DisciplinedTime::sync(const FreqDiscipliner& discipliner, bool pps_valid, uint32_t now_ms) {
  const FreqDiscipliner::DiscState prev_state = state_;
  state_ = discipliner.state();

  const bool mild_unlock_transition =
      (prev_state == FreqDiscipliner::DiscState::DISCIPLINED) &&
      (state_ == FreqDiscipliner::DiscState::ACQUIRE) &&
      pps_valid;

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
        if (grace_expired || !pps_valid || !has_disciplined_once_) {
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
