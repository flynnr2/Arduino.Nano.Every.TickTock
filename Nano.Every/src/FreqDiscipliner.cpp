#include "FreqDiscipliner.h"

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

void FreqDiscipliner::reset(uint32_t f_cpu_nominal) {
  state_ = DiscState::FREE_RUN;
  f_fast_ = f_cpu_nominal;
  f_slow_ = f_cpu_nominal;
  f_hat_ = f_cpu_nominal;
  r_ppm_ = 0;
  mad_res_ticks_ = 0;
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

    if (state_ == DiscState::DISCIPLINED) w_q16 = 0;
    if (state_ == DiscState::ACQUIRE) {
      // keep fast-dominant in acquire
    }

    f_hat_ = (uint32_t)(((uint64_t)f_slow_ * (65535U - w_q16) + (uint64_t)f_fast_ * w_q16) / 65535U);

    residuals_[res_head_] = (int32_t)n_k - (int32_t)f_hat_;
    res_head_ = (uint8_t)((res_head_ + 1U) % W_MAD);
    if (res_fill_ < W_MAD) res_fill_++;

    uint32_t mags[W_MAD];
    for (uint8_t i = 0; i < res_fill_; i++) {
      mags[i] = (uint32_t)(residuals_[i] >= 0 ? residuals_[i] : -residuals_[i]);
    }
    uint32_t med_abs = median_u32(mags, res_fill_);
    for (uint8_t i = 0; i < res_fill_; i++) {
      uint32_t a = (uint32_t)(residuals_[i] >= 0 ? residuals_[i] : -residuals_[i]);
      mags[i] = (a > med_abs) ? (a - med_abs) : (med_abs - a);
    }
    mad_res_ticks_ = median_u32(mags, res_fill_);

    const bool lock_ok = (r_ppm_ < lockFrequencyErrorThresholdPpm()) &&
                         (mad_res_ticks_ < lockMadThresholdTicks()) &&
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

      const bool unlock_bad = (r_ppm_ > (uint32_t)Tunables::ppsUnlockRppmActive()) ||
                              (mad_res_ticks_ > (uint32_t)Tunables::ppsUnlockJppmActive()) ||
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
