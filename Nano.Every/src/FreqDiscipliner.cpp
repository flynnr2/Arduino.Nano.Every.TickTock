#include "FreqDiscipliner.h"

namespace {
static constexpr uint8_t kMadWindowSize = 31;

static uint32_t abs_i32(int32_t v) {
  return (uint32_t)(v >= 0 ? v : -v);
}

static uint32_t median_u32(uint32_t *vals, uint8_t n) {
  for (uint8_t i = 1; i < n; i++) {
    uint32_t v = vals[i];
    uint8_t j = i;
    while (j > 0 && vals[j - 1] > v) {
      vals[j] = vals[j - 1];
      j--;
    }
    vals[j] = v;
  }
  return vals[n / 2];
}

static uint32_t ppm_from_error(int32_t err_ticks, uint32_t denom_ticks) {
  if (denom_ticks == 0U) return 0U;
  return (uint32_t)((1000000ULL * (uint64_t)abs_i32(err_ticks)) / (uint64_t)denom_ticks);
}

static uint32_t mad_from_residuals(const int32_t* residuals, uint8_t n) {
  if (n == 0U) return 0U;

  uint32_t mags[kMadWindowSize];
  for (uint8_t i = 0; i < n; ++i) {
    mags[i] = abs_i32(residuals[i]);
  }
  const uint32_t med_abs = median_u32(mags, n);
  for (uint8_t i = 0; i < n; ++i) {
    const uint32_t a = abs_i32(residuals[i]);
    mags[i] = (a > med_abs) ? (a - med_abs) : (med_abs - a);
  }
  return median_u32(mags, n);
}

} // namespace

void FreqDiscipliner::reset(uint32_t f_cpu_nominal) {
  state_ = DiscState::FREE_RUN;
  f_fast_ = f_cpu_nominal;
  f_slow_ = f_cpu_nominal;
  f_hat_ = f_cpu_nominal;
  r_ppm_ = 0;
  err_fast_ticks_ = 0;
  err_slow_ticks_ = 0;
  err_applied_ticks_ = 0;
  fast_err_ppm_ = 0;
  slow_err_ppm_ = 0;
  applied_err_ppm_ = 0;
  mad_slow_ticks_ = 0;
  mad_applied_ticks_ = 0;
  mad_legacy_ticks_ = 0;
  lock_pass_mask_ = 0;
  unlock_breach_mask_ = 0;
  res_head_ = 0;
  res_fill_ = 0;
  lock_streak_ = 0;
  unlock_streak_ = 0;
  transition_streak_ = 0;
  acq_start_ms_ = 0;
  holdover_start_ms_ = 0;
  holdover_age_ms_ = 0;
  last_good_slow_ = f_cpu_nominal;
}

void FreqDiscipliner::observe(PpsValidator::SampleClass cls, bool pps_valid, uint32_t n_k, uint32_t now_ms, bool had_recent_anomaly) {
  if (state_ == DiscState::HOLDOVER) holdover_age_ms_ = now_ms - holdover_start_ms_;
  else holdover_age_ms_ = 0;
  transition_streak_ = 0;
  lock_pass_mask_ = 0;
  unlock_breach_mask_ = 0;

  if (pps_valid && state_ == DiscState::FREE_RUN) {
    state_ = DiscState::ACQUIRE;
    acq_start_ms_ = now_ms;
    lock_streak_ = 0;
    unlock_streak_ = 0;
  } else if (!pps_valid && state_ == DiscState::DISCIPLINED) {
    state_ = DiscState::HOLDOVER;
    holdover_start_ms_ = now_ms;
    last_good_slow_ = f_slow_;
    f_hat_ = last_good_slow_;
  } else if (!pps_valid && state_ == DiscState::ACQUIRE) {
    state_ = DiscState::FREE_RUN;
    unlock_streak_ = 0;
  } else if (pps_valid && state_ == DiscState::HOLDOVER) {
    state_ = DiscState::ACQUIRE;
    acq_start_ms_ = now_ms;
    lock_streak_ = 0;
    unlock_streak_ = 0;
  }

  const bool sample_ok = (cls == PpsValidator::SampleClass::OK);
  if (pps_valid && sample_ok) {
    const int32_t err_fast = (int32_t)n_k - (int32_t)f_fast_;
    f_fast_ = (uint32_t)((int32_t)f_fast_ + (err_fast >> Tunables::ppsFastShiftActive()));

    const int32_t err_slow = (int32_t)n_k - (int32_t)f_slow_;
    f_slow_ = (uint32_t)((int32_t)f_slow_ + (err_slow >> Tunables::ppsSlowShiftActive()));

    const uint32_t abs_diff = (f_fast_ > f_slow_) ? (f_fast_ - f_slow_) : (f_slow_ - f_fast_);
    r_ppm_ = (f_slow_ > 0) ? (uint32_t)((1000000ULL * abs_diff) / f_slow_) : 0;

    const uint16_t lo = Tunables::ppsBlendLoPpmActive();
    const uint16_t hi = Tunables::ppsBlendHiPpmActive();
    uint32_t w_q16 = 0;
    if (r_ppm_ <= lo) w_q16 = 0;
    else if (r_ppm_ >= hi) w_q16 = 65535;
    else w_q16 = (uint32_t)(((uint64_t)(r_ppm_ - lo) * 65535ULL) / (uint64_t)(hi - lo));

    f_hat_ = (uint32_t)(((uint64_t)f_slow_ * (65535U - w_q16) + (uint64_t)f_fast_ * w_q16) / 65535U);

    err_fast_ticks_ = (int32_t)n_k - (int32_t)f_fast_;
    err_slow_ticks_ = (int32_t)n_k - (int32_t)f_slow_;
    err_applied_ticks_ = (int32_t)n_k - (int32_t)f_hat_;
    fast_err_ppm_ = ppm_from_error(err_fast_ticks_, f_fast_);
    slow_err_ppm_ = ppm_from_error(err_slow_ticks_, f_slow_);
    applied_err_ppm_ = ppm_from_error(err_applied_ticks_, f_hat_);

    slow_residuals_[res_head_] = err_slow_ticks_;
    applied_residuals_[res_head_] = err_applied_ticks_;
    // Preserve the richer slow/applied MAD telemetry, but drive the state machine
    // with the earlier single residual path that worked better for noisy
    // oscillators and pendulum metrology. In ACQUIRE/FREE_RUN this matches the
    // blended estimator; in DISCIPLINED/HOLDOVER it matches the exported slow
    // estimate and holdover anchor.
    const bool use_slow_legacy_residual =
        (state_ == DiscState::DISCIPLINED) || (state_ == DiscState::HOLDOVER);
    legacy_residuals_[res_head_] = use_slow_legacy_residual ? err_slow_ticks_ : err_applied_ticks_;
    res_head_ = (uint8_t)((res_head_ + 1U) % W_MAD);
    if (res_fill_ < W_MAD) res_fill_++;
    mad_slow_ticks_ = mad_from_residuals(slow_residuals_, res_fill_);
    mad_applied_ticks_ = mad_from_residuals(applied_residuals_, res_fill_);
    mad_legacy_ticks_ = mad_from_residuals(legacy_residuals_, res_fill_);

    if (r_ppm_ < lockFrequencyErrorThresholdPpm()) lock_pass_mask_ |= kMetricFastSlowAgree;
    if (slow_err_ppm_ < lockFrequencyErrorThresholdPpm()) lock_pass_mask_ |= kMetricSlowErr;
    if (mad_slow_ticks_ < lockMadThresholdTicks()) lock_pass_mask_ |= kMetricSlowMad;
    if (applied_err_ppm_ < lockFrequencyErrorThresholdPpm()) lock_pass_mask_ |= kMetricAppliedErr;
    if (mad_applied_ticks_ < lockMadThresholdTicks()) lock_pass_mask_ |= kMetricAppliedMad;
    if (!had_recent_anomaly) lock_pass_mask_ |= kMetricNoAnomaly;

    const bool lock_ok = (r_ppm_ < lockFrequencyErrorThresholdPpm()) &&
                         (mad_legacy_ticks_ < lockMadThresholdTicks()) &&
                         !had_recent_anomaly;
    if (lock_ok) {
      if (lock_streak_ < 255) lock_streak_++;
    } else {
      lock_streak_ = 0;
    }

    if (state_ == DiscState::ACQUIRE &&
        (uint32_t)(now_ms - acq_start_ms_) >= (uint32_t)Tunables::ppsAcquireMinMsActive() &&
        lock_streak_ >= lockConsecutiveGoodSamplesRequired()) {
      state_ = DiscState::DISCIPLINED;
      transition_streak_ = lock_streak_;
      f_hat_ = f_slow_;
      last_good_slow_ = f_slow_;
    }

    if (state_ == DiscState::DISCIPLINED) {
      f_hat_ = f_slow_;
      last_good_slow_ = f_slow_;

      if (applied_err_ppm_ > (uint32_t)Tunables::ppsUnlockRppmActive()) unlock_breach_mask_ |= kMetricAppliedErr;
      if (mad_applied_ticks_ > (uint32_t)Tunables::ppsUnlockMadTicksActive()) unlock_breach_mask_ |= kMetricAppliedMad;
      if (slow_err_ppm_ > (uint32_t)Tunables::ppsUnlockRppmActive()) unlock_breach_mask_ |= kMetricSlowErr;
      if (mad_slow_ticks_ > (uint32_t)Tunables::ppsUnlockMadTicksActive()) unlock_breach_mask_ |= kMetricSlowMad;
      if (r_ppm_ > (uint32_t)Tunables::ppsUnlockRppmActive()) unlock_breach_mask_ |= kMetricFastSlowAgree;
      if (had_recent_anomaly) unlock_breach_mask_ |= kMetricNoAnomaly;
      const bool unlock_bad = (r_ppm_ > (uint32_t)Tunables::ppsUnlockRppmActive()) ||
                              (mad_legacy_ticks_ > (uint32_t)Tunables::ppsUnlockMadTicksActive()) ||
                              had_recent_anomaly;
      if (unlock_bad) {
        if (unlock_streak_ < 255U) unlock_streak_++;
      } else {
        unlock_streak_ = 0;
      }

      if (unlock_streak_ >= Tunables::ppsUnlockCountActive()) {
        transition_streak_ = unlock_streak_;
        state_ = DiscState::ACQUIRE;
        acq_start_ms_ = now_ms;
        lock_streak_ = 0;
        unlock_streak_ = 0;
      }
    }
  }

  if (state_ == DiscState::HOLDOVER) {
    f_hat_ = last_good_slow_;
    if ((uint32_t)(now_ms - holdover_start_ms_) > (uint32_t)Tunables::ppsHoldoverMsActive()) {
      state_ = DiscState::FREE_RUN;
    }
  }

  if (state_ == DiscState::FREE_RUN) {
    // keep f_hat unchanged; caller sets nominal in DisciplinedTime if needed
    lock_streak_ = 0;
    unlock_streak_ = 0;
  }
}
