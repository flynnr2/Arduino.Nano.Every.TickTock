#include "PpsValidator.h"

static inline uint32_t abs_diff_u32(uint32_t a, uint32_t b) {
  return (a > b) ? (a - b) : (b - a);
}

// PPS ground truth is 1 Hz. Startup/recovery logic uses nominalRefTicks()
// as guidance to avoid poisoning n_ref_ from an anomalous first interval.
static inline bool is_near_1x_nominal(uint32_t n_k) {
  return PpsValidator::isInOkBandTicks(n_k, PpsValidator::nominalRefTicks());
}

static inline bool is_near_2x_nominal(uint32_t n_k) {
  const uint64_t lhs = (uint64_t)n_k * PpsValidator::ratioDen10();
  const uint64_t min2 = (uint64_t)PpsValidator::nominalRefTicks() * 2ULL * PpsValidator::okMinNum10();
  const uint64_t max2 = (uint64_t)PpsValidator::nominalRefTicks() * 2ULL * PpsValidator::seedNear2xMaxNum10();
  return lhs >= min2 && lhs <= max2;
}

static inline bool is_mutually_consistent(uint32_t n_k, uint32_t candidate) {
  if (candidate == 0U) return true;
  const uint64_t diff = (uint64_t)abs_diff_u32(n_k, candidate);
  const uint64_t lim = ((uint64_t)candidate * PpsValidator::seedConsistencyNum100()) /
                       PpsValidator::seedConsistencyDen100();
  return diff <= lim;
}

void PpsValidator::reset() {
  pps_valid_ = false;
  n_ref_valid_ = false;
  n_ref_ = 0;
  ok_streak_ = 0;
  startup_seed_count_ = 0;
  startup_seed_candidate_ = 0;
  startup_near1x_count_ = 0;
  startup_near2x_count_ = 0;
  startup_reset_count_ = 0;
  recovery_seed_count_ = 0;
  recovery_seed_candidate_ = 0;
  recovery_reseed_count_ = 0;
  ref_seed_source_ = RefSeedSource::NONE;
  ring_head_ = 0;
  ring_fill_ = 0;
}

PpsValidator::SampleClass PpsValidator::classify(uint32_t n_k, bool extension_signature) const {
  if (extension_signature) return SampleClass::HARD_GLITCH;

  if (!n_ref_valid_) {
    return is_near_1x_nominal(n_k) ? SampleClass::OK : SampleClass::HARD_GLITCH;
  }

  const uint32_t ref = n_ref_;
  const bool dup = isDupTicks(n_k, ref);
  const bool gap = isGapTicks(n_k, ref);
  if (dup) return SampleClass::DUP;
  if (gap) return SampleClass::GAP;

  const bool hard_mag = abs_diff_u32(n_k, ref) > kHardTicks();
  if (hard_mag || extension_signature) return SampleClass::HARD_GLITCH;

  const bool ok = isInOkBandTicks(n_k, ref);
  return ok ? SampleClass::OK : SampleClass::HARD_GLITCH;
}

void PpsValidator::observe(SampleClass cls, uint32_t n_k, uint32_t /*now_ms*/) {
  // n_k is full 32-bit PPS interval ticks.

  ring_[ring_head_] = cls;
  ring_head_ = (uint8_t)((ring_head_ + 1U) % W_RATE);
  if (ring_fill_ < W_RATE) ring_fill_++;

  const bool near1x = is_near_1x_nominal(n_k);
  const bool near2x = is_near_2x_nominal(n_k);

  if (!n_ref_valid_) {
    if (near1x) {
      startup_near1x_count_++;
      if (startup_seed_count_ == 0U) {
        startup_seed_candidate_ = n_k;
        startup_seed_count_ = 1U;
      } else if (is_mutually_consistent(n_k, startup_seed_candidate_)) {
        startup_seed_candidate_ = (uint32_t)((((uint64_t)startup_seed_candidate_ * startup_seed_count_) + n_k) /
                                             (startup_seed_count_ + 1U));
        startup_seed_count_++;
      } else {
        startup_seed_candidate_ = n_k;
        startup_seed_count_ = 1U;
        startup_reset_count_++;
      }

      if (startup_seed_count_ >= startupSeedRequired()) {
        n_ref_ = startup_seed_candidate_;
        n_ref_valid_ = true;
        ref_seed_source_ = RefSeedSource::STARTUP;
        recovery_seed_count_ = 0;
        recovery_seed_candidate_ = 0;
      }
    } else {
      if (near2x) startup_near2x_count_++;
      if (startup_seed_count_ != 0U) {
        startup_seed_count_ = 0;
        startup_seed_candidate_ = 0;
        startup_reset_count_++;
      }
      ok_streak_ = 0;
    }
  }

  if (cls == SampleClass::OK) {
    ok_streak_++;
    if (n_ref_valid_) {
      const int32_t err = (int32_t)n_k - (int32_t)n_ref_;
      n_ref_ = (uint32_t)((int32_t)n_ref_ + (err >> 6));
    }
  } else {
    ok_streak_ = 0;
    if (cls == SampleClass::GAP) pps_valid_ = false;
  }

  if (n_ref_valid_ && n_ref_ != 0U && near1x &&
      n_ref_ > (nominalRefTicks() + (nominalRefTicks() >> 1))) {
    if (recovery_seed_count_ == 0U) {
      recovery_seed_candidate_ = n_k;
      recovery_seed_count_ = 1U;
    } else if (is_mutually_consistent(n_k, recovery_seed_candidate_)) {
      recovery_seed_candidate_ = (uint32_t)((((uint64_t)recovery_seed_candidate_ * recovery_seed_count_) + n_k) /
                                            (recovery_seed_count_ + 1U));
      recovery_seed_count_++;
    } else {
      recovery_seed_candidate_ = n_k;
      recovery_seed_count_ = 1U;
    }

    if (recovery_seed_count_ >= recoverySeedRequired()) {
      n_ref_ = recovery_seed_candidate_;
      n_ref_valid_ = true;
      ref_seed_source_ = RefSeedSource::RECOVERY;
      recovery_reseed_count_++;
      recovery_seed_count_ = 0;
      recovery_seed_candidate_ = 0;
      ok_streak_ = 0;
      pps_valid_ = false;
    }
  } else {
    recovery_seed_count_ = 0;
    recovery_seed_candidate_ = 0;
  }

  if (ok_streak_ >= kValid()) pps_valid_ = true;

  Health h = health();
  if (h.hg >= 2) pps_valid_ = false;
}

PpsValidator::Health PpsValidator::health() const {
  Health h;
  for (uint8_t i = 0; i < ring_fill_; i++) {
    switch (ring_[i]) {
      case SampleClass::OK: h.ok++; break;
      case SampleClass::GAP: h.gap++; break;
      case SampleClass::DUP: h.dup++; break;
      case SampleClass::HARD_GLITCH: h.hg++; break;
    }
  }
  return h;
}

PpsValidator::SeedDiagnostics PpsValidator::seedDiagnostics() const {
  SeedDiagnostics d;
  d.startup_phase = n_ref_valid_ ? 0U : 1U;
  d.seed_count = n_ref_valid_ ? recovery_seed_count_ : startup_seed_count_;
  d.seed_candidate_ticks = n_ref_valid_ ? recovery_seed_candidate_ : startup_seed_candidate_;
  d.startup_near1x = startup_near1x_count_;
  d.startup_near2x = startup_near2x_count_;
  d.startup_resets = startup_reset_count_;
  d.recovery_reseeds = recovery_reseed_count_;
  d.source = ref_seed_source_;
  return d;
}
