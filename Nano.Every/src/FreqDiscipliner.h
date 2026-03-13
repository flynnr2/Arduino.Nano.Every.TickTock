#pragma once

#include "PpsValidator.h"
#include "Config.h"
#include <stdint.h>

class FreqDiscipliner {
public:
  enum class DiscState : uint8_t { FREE_RUN = 0, ACQUIRE = 1, DISCIPLINED = 2, HOLDOVER = 3 };

  void reset(uint32_t f_cpu_nominal);
  void observe(PpsValidator::SampleClass cls, bool pps_valid, uint32_t n_k, uint32_t now_ms, bool had_recent_anomaly);

  uint32_t fast() const { return f_fast_; }
  uint32_t slow() const { return f_slow_; }
  uint32_t applied() const { return f_hat_; }
  uint32_t rPpm() const { return r_ppm_; }
  uint32_t madTicks() const { return mad_res_ticks_; }
  DiscState state() const { return state_; }
  uint32_t holdoverAgeMs() const { return holdover_age_ms_; }
  uint8_t lockStreak() const { return lock_streak_; }
  uint8_t unlockStreak() const { return unlock_streak_; }
  uint8_t transitionStreak() const { return transition_streak_; }
  static inline uint32_t lockFrequencyErrorThresholdPpm() { return Tunables::ppsLockRppmActive(); }
  // Legacy name retained for STS/API compatibility; value is MAD residual ticks threshold.
  static inline uint32_t lockMadThresholdTicks() { return Tunables::ppsLockJppmActive(); }
  static inline uint8_t lockConsecutiveGoodSamplesRequired() { return Tunables::ppsLockCountActive(); }

private:
  static constexpr uint32_t T_ACQ_MIN_MS = 60000;

  static constexpr uint8_t W_MAD = 31;

  DiscState state_ = DiscState::FREE_RUN;
  uint32_t f_fast_ = 0;
  uint32_t f_slow_ = 0;
  uint32_t f_hat_ = 0;
  uint32_t r_ppm_ = 0;
  uint32_t mad_res_ticks_ = 0;

  int32_t residuals_[W_MAD] = {};
  uint8_t res_head_ = 0;
  uint8_t res_fill_ = 0;
  uint8_t lock_streak_ = 0;
  uint8_t unlock_streak_ = 0;
  uint8_t transition_streak_ = 0;

  uint32_t acq_start_ms_ = 0;
  uint32_t holdover_start_ms_ = 0;
  uint32_t holdover_age_ms_ = 0;
  uint32_t last_good_slow_ = 0;
};
