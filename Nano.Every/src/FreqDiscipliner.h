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
  // Legacy accessor retained for STS/API compatibility; returns the state-machine
  // MAD metric that matches the earlier lock/unlock semantics.
  uint32_t madTicks() const { return mad_legacy_ticks_; }
  int32_t fastErrTicks() const { return err_fast_ticks_; }
  int32_t slowErrTicks() const { return err_slow_ticks_; }
  int32_t appliedErrTicks() const { return err_applied_ticks_; }
  uint32_t fastErrPpm() const { return fast_err_ppm_; }
  uint32_t slowErrPpm() const { return slow_err_ppm_; }
  uint32_t appliedErrPpm() const { return applied_err_ppm_; }
  uint32_t slowMadTicks() const { return mad_slow_ticks_; }
  uint32_t appliedMadTicks() const { return mad_applied_ticks_; }
  uint8_t lockPassMask() const { return lock_pass_mask_; }
  uint8_t unlockBreachMask() const { return unlock_breach_mask_; }
  DiscState state() const { return state_; }
  uint32_t holdoverAgeMs() const { return holdover_age_ms_; }
  uint8_t lockStreak() const { return lock_streak_; }
  uint8_t unlockStreak() const { return unlock_streak_; }
  uint8_t transitionStreak() const { return transition_streak_; }
  uint32_t lastGoodSlow() const { return last_good_slow_; }
  static inline uint32_t lockFrequencyErrorThresholdPpm() { return Tunables::ppsLockRppmActive(); }
  // Legacy name retained for STS/API compatibility; value is MAD residual ticks threshold.
  static inline uint32_t lockMadThresholdTicks() { return Tunables::ppsLockMadTicksActive(); }
  static inline uint8_t lockConsecutiveGoodSamplesRequired() { return Tunables::ppsLockCountActive(); }
  // The legacy MAD gate is checked separately via madTicks(); this mask captures
  // the remaining legacy lock bits that are represented in telemetry.
  static inline uint8_t lockPassRequiredMask() {
    return (uint8_t)((1U << 4) | (1U << 5));
  }

private:
  static constexpr uint8_t W_MAD = 31;
  // lockPassMask() bit meanings:
  //  bit0 applied_err_ppm below lockR threshold (diagnostic only)
  //  bit1 applied MAD residual ticks below the lock MAD threshold (diagnostic only)
  //  bit2 slow_err_ppm below lockR threshold (diagnostic only)
  //  bit3 slow MAD residual ticks below the lock MAD threshold (diagnostic only)
  //  bit4 fast/slow disagreement below lockR threshold (legacy lock/unlock gate)
  //  bit5 current sample is anomaly-free (legacy lock gate / anomaly breach)
  // The state machine intentionally gates on the older simpler semantics using
  // r_ppm plus a legacy-equivalent MAD metric, while retaining richer masks for
  // telemetry and diagnosis. unlockBreachMask() marks both legacy breaches and
  // richer applied/slow diagnostics.
  enum MetricMask : uint8_t {
    kMetricAppliedErr = 1U << 0,
    kMetricAppliedMad = 1U << 1,
    kMetricSlowErr = 1U << 2,
    kMetricSlowMad = 1U << 3,
    kMetricFastSlowAgree = 1U << 4,
    kMetricNoAnomaly = 1U << 5,
  };

  DiscState state_ = DiscState::FREE_RUN;
  uint32_t f_fast_ = 0;
  uint32_t f_slow_ = 0;
  uint32_t f_hat_ = 0;
  uint32_t r_ppm_ = 0;
  int32_t err_fast_ticks_ = 0;
  int32_t err_slow_ticks_ = 0;
  int32_t err_applied_ticks_ = 0;
  uint32_t fast_err_ppm_ = 0;
  uint32_t slow_err_ppm_ = 0;
  uint32_t applied_err_ppm_ = 0;
  uint32_t mad_slow_ticks_ = 0;
  uint32_t mad_applied_ticks_ = 0;
  uint32_t mad_legacy_ticks_ = 0;
  uint8_t lock_pass_mask_ = 0;
  uint8_t unlock_breach_mask_ = 0;

  int32_t slow_residuals_[W_MAD] = {};
  int32_t applied_residuals_[W_MAD] = {};
  int32_t legacy_residuals_[W_MAD] = {};
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
