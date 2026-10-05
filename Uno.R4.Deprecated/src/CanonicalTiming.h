#pragma once

#include "PendulumProtocol.h"
#include <cmath>

// Uno-side display timing. Capture records stay raw in the log; PPS calibration
// and interval conversion are downstream calculations.
namespace CanonicalTiming {

struct Intervals {
  uint32_t period;
  int32_t beatDelta;
  int32_t blockDelta;
};

inline bool intervals(const CanonicalSwingSample& swing, Intervals& out) {
  // Unsigned subtraction preserves intervals across the 32-bit capture wrap.
  const uint32_t tick = swing.edge1_tcb0 - swing.edge0_tcb0;
  const uint32_t tickBlock = swing.edge2_tcb0 - swing.edge1_tcb0;
  const uint32_t tock = swing.edge3_tcb0 - swing.edge2_tcb0;
  const uint32_t tockBlock = swing.edge4_tcb0 - swing.edge3_tcb0;
  const uint64_t period = (uint64_t)tick + tickBlock + tock + tockBlock;
  if (!tick || !tickBlock || !tock || !tockBlock || period > INT32_MAX) return false;
  out.period = (uint32_t)period;
  out.beatDelta = (int32_t)tick - (int32_t)tock;
  out.blockDelta = (int32_t)tickBlock - (int32_t)tockBlock;
  return true;
}

class PpsClock {
 public:
  static constexpr double FAST_HALF_LIFE_SECONDS = 20.0;
  static constexpr double SLOW_HALF_LIFE_SECONDS = 3600.0;
  static constexpr double FAST_WEIGHT = 0.75;
  static constexpr uint32_t STALE_MS = 5000;
  static double alpha(double halfLifeSeconds) {
    return -std::expm1(-0.6931471805599453094 / halfLifeSeconds);
  }

  // Returns true when a sequence reversal indicates a new capture timeline.
  bool observe(const CanonicalPpsSample& pps, uint32_t nominalHz, uint32_t nowMs) {
    const uint32_t step = pps.seq - previous_.seq;
    const bool restarted = seen_ && step >= 0x80000000UL;
    if (seen_ && step == 0) return false; // Replayed records are not new pulses.
    const uint32_t ticks = pps.edge_tcb0 - previous_.edge_tcb0;
    // Only adjacent one-second captures may establish a scale. Gaps, bad
    // pulses and restarts require another valid pair, never a guessed divisor.
    const bool valid = seen_ && step == 1 && nominalHz >= 1000 &&
        nominalHz == nominalHz_ && pps.gps_status == LOCKED &&
        previous_.gps_status == LOCKED && pps.drop_pps == previous_.drop_pps &&
        ticks >= nominalHz - nominalHz / 10 &&
        (uint64_t)ticks <= (uint64_t)nominalHz + nominalHz / 10;
    // A new oscillator configuration/timeline must not inherit the old scale.
    // Ordinary rejections and gaps preserve both states, even through staleness.
    if (restarted || nominalHz != nominalHz_) {
      initialized_ = false;
      fastHz_ = slowHz_ = blendedHz_ = 0;
      observationHz_ = 0;
    }
    if (valid) {
      observationHz_ = ticks;
      if (!initialized_) {
        fastHz_ = slowHz_ = ticks; // Unbiased, deterministic first observation.
        initialized_ = true;
      } else {
        // dt is the qualified one-second observation, never UART arrival time
        // or elapsed wall time across a gap. Missing observations do no work.
        fastHz_ += alpha(FAST_HALF_LIFE_SECONDS) * (ticks - fastHz_);
        slowHz_ += alpha(SLOW_HALF_LIFE_SECONDS) * (ticks - slowHz_);
      }
      blendedHz_ = FAST_WEIGHT * fastHz_ + (1.0 - FAST_WEIGHT) * slowHz_;
      calibratedMs_ = nowMs;
    }
    previous_ = pps;
    nominalHz_ = nominalHz;
    seen_ = true;
    lastAccepted_ = valid;
    return restarted;
  }

  double correctedHz(uint32_t nominalHz, uint32_t nowMs) const {
    return nominalHz == nominalHz_ && initialized_ && !stale(nowMs)
        ? blendedHz_ : 0;
  }
  bool initialized() const { return initialized_; }
  bool stale(uint32_t nowMs) const {
    return initialized_ && (uint32_t)(nowMs - calibratedMs_) >= STALE_MS;
  }
  double fastHz() const { return fastHz_; }
  double slowHz() const { return slowHz_; }
  double blendedHz() const { return blendedHz_; }
  uint32_t observationHz() const { return observationHz_; }
  const char* status(uint32_t nowMs) const {
    if (stale(nowMs)) return "STALE";
    if (!seen_) return "WAIT";
    switch (previous_.gps_status) {
      case NO_PPS: return "NO PPS";
      case HOLDOVER: return "HOLDOVER";
      case ACQUIRING: return "ACQUIRING";
      case LOCKED: break;
      default: return "REJECTED";
    }
    if (!initialized_) return "WAIT";
    return lastAccepted_ ? "LOCKED" : "REJECTED";
  }

 private:
  CanonicalPpsSample previous_ = {};
  double fastHz_ = 0, slowHz_ = 0, blendedHz_ = 0;
  uint32_t nominalHz_ = 0, observationHz_ = 0, calibratedMs_ = 0;
  bool seen_ = false, initialized_ = false, lastAccepted_ = false;
};

} // namespace CanonicalTiming
