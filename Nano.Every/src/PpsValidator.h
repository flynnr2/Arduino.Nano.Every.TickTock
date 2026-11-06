#pragma once

#include "Config.h"

#include <stdint.h>

class PpsValidator {
public:
  enum class SampleClass : uint8_t { OK = 0, GAP = 1, DUP = 2, HARD_GLITCH = 3 };
  enum class RefSeedSource : uint8_t { NONE = 0, STARTUP = 1, RECOVERY = 2 };

  struct Health {
    uint8_t ok = 0;
    uint8_t gap = 0;
    uint8_t dup = 0;
    uint8_t hg = 0;
  };

  struct SeedDiagnostics {
    uint32_t seed_candidate_ticks = 0; // [0, ~16,000,000] nominal PPS interval ticks
    uint16_t startup_near1x = 0;
    uint16_t startup_near2x = 0;
    uint16_t startup_resets = 0;
    uint16_t recovery_reseeds = 0;
    uint8_t startup_phase = 0;         // [0, 1] startup seeding inactive/active
    uint8_t seed_count = 0;            // [0, 255] bounded by startup/recovery seed policy
    RefSeedSource source = RefSeedSource::NONE;
  };

  static constexpr uint8_t W_RATE = 60;

  // Validator policy constants (single source of truth for live thresholds).
  static constexpr uint32_t kHardTicks() { return 20000UL; }
  static constexpr uint8_t kValid() { return 5; }

  // Ratio thresholds in tenths to keep classify() integer-only on MCU.
  static constexpr uint8_t dupNum10() { return 3; }   // <0.3*ref => DUP
  static constexpr uint8_t okMinNum10() { return 7; } // >=0.7*ref => broad OK lower bound
  static constexpr uint8_t okMaxNum10() { return 13; } // <=1.3*ref => broad OK upper bound
  static constexpr uint8_t gapNum10() { return 17; }  // >1.7*ref => GAP
  static constexpr uint8_t ratioDen10() { return 10; }
  static constexpr uint32_t nominalRefTicks() { return (uint32_t)MAIN_CLOCK_HZ; }

  // Startup/recovery guidance bands around the known 1 Hz PPS period.
  static constexpr uint8_t seedNear2xMaxNum10() { return 23; }
  static constexpr uint8_t startupSeedConsistencyNum100() { return 1; }  // 1%
  static constexpr uint8_t recoverySeedConsistencyNum100() { return 10; } // 10%
  static constexpr uint8_t seedConsistencyDen100() { return 100; }
  static constexpr uint8_t startupSeedRequired() { return 3; }
  static constexpr uint8_t recoverySeedRequired() { return 3; }

  static inline uint32_t minOkTicks(uint32_t ref_ticks) {
    return (uint32_t)(((uint64_t)ref_ticks * okMinNum10()) / ratioDen10());
  }
  static inline uint32_t maxOkTicks(uint32_t ref_ticks) {
    return (uint32_t)(((uint64_t)ref_ticks * okMaxNum10()) / ratioDen10());
  }
  static inline bool isDupTicks(uint32_t n_k, uint32_t ref_ticks) {
    return ((uint64_t)n_k * ratioDen10()) < ((uint64_t)ref_ticks * dupNum10());
  }
  static inline bool isGapTicks(uint32_t n_k, uint32_t ref_ticks) {
    return ((uint64_t)n_k * ratioDen10()) > ((uint64_t)ref_ticks * gapNum10());
  }
  static inline bool isInOkBandTicks(uint32_t n_k, uint32_t ref_ticks) {
    const uint64_t lhs = (uint64_t)n_k * ratioDen10();
    return lhs >= ((uint64_t)ref_ticks * okMinNum10()) &&
           lhs <= ((uint64_t)ref_ticks * okMaxNum10());
  }

  void reset();
  // n_k is the full 32-bit PPS interval in timer ticks.
  SampleClass classify(uint32_t n_k, bool extension_signature) const;
  void observe(SampleClass cls, uint32_t n_k, uint32_t now_ms);

  bool isValid() const { return pps_valid_; }
  // Reference full 32-bit PPS interval (ticks/second estimate used by validator).
  uint32_t referenceTicks() const { return n_ref_; }
  uint16_t okStreak() const { return ok_streak_; }
  Health health() const;
  SeedDiagnostics seedDiagnostics() const;

private:
  bool pps_valid_ = false;
  bool n_ref_valid_ = false;
  uint32_t n_ref_ = 0;
  uint16_t ok_streak_ = 0;
  uint8_t startup_seed_count_ = 0;
  uint32_t startup_seed_candidate_ = 0;
  uint16_t startup_near1x_count_ = 0;
  uint16_t startup_near2x_count_ = 0;
  uint16_t startup_reset_count_ = 0;
  uint8_t recovery_seed_count_ = 0;
  uint32_t recovery_seed_candidate_ = 0;
  uint16_t recovery_reseed_count_ = 0;
  RefSeedSource ref_seed_source_ = RefSeedSource::NONE;

  SampleClass ring_[W_RATE] = {};
  uint8_t ring_head_ = 0;
  uint8_t ring_fill_ = 0;
};
