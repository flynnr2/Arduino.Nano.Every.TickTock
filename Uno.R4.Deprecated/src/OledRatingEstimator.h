#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include "OledRatingConfig.h"

// Display-only, constant-space estimates. The captured full-swing duration is
// the elapsed sample time: UART batching cannot change the smoothing weights.
// Arrival millis is used only by PeriodDisplayState to detect stale feeds.
class OledRatingEstimator {
 public:
  static constexpr size_t ROW_CHARS = 21; // 126 pixels with the 6px OLED font.
  static constexpr uint32_t STALE_MS = 10000;
  static double alpha(double seconds, uint16_t halfLifeMinutes) {
    return -std::expm1(-0.6931471805599453094 * seconds / (60.0 * halfLifeMinutes));
  }

  // Resolution follows recent RMS movement of the *displayed estimate*, not
  // raw-sample variance. Promotion needs 60 measured seconds below half a fine
  // digit; demotion needs 6 seconds above 1.5 fine digits. A 30-second EWMA and
  // separate thresholds suppress chatter. This changes formatting only.
  struct Precision {
    double movementSquared = 0;
    double stableSeconds = 0;
    double noisySeconds = 0;
    bool fine = false;
    void observe(double change, double seconds, double fineDigit, double weight) {
      movementSquared += weight * (change * change - movementSquared);
      const double promote = 0.5 * fineDigit;
      const double demote = 1.5 * fineDigit;
      if (!fine) {
        stableSeconds = movementSquared < promote * promote ? stableSeconds + seconds : 0;
        if (stableSeconds >= 60) { fine = true; stableSeconds = 0; }
      } else {
        noisySeconds = movementSquared > demote * demote ? noisySeconds + seconds : 0;
        if (noisySeconds >= 6) { fine = false; noisySeconds = stableSeconds = 0; }
      }
    }
  };

  struct Horizon {
    double periodUs = 0;
    double blockUs = 0;
    double elapsedSeconds = 0;
    Precision periodPrecision, bpmPrecision, blockPrecision;
    double bpm() const { return periodUs > 0 ? 60000000.0 / periodUs : 0; }
  };

  bool configure(uint16_t shortMinutes, uint16_t longMinutes) {
    OledRatingConfig::sanitize(shortMinutes, longMinutes);
    if (shortMinutes == shortMinutes_ && longMinutes == longMinutes_) return false;
    shortMinutes_ = shortMinutes;
    longMinutes_ = longMinutes;
    reset();
    return true;
  }
  void reset() { horizons_[0] = Horizon{}; horizons_[1] = Horizon{}; seen_ = false; }
  bool observe(double periodUs, double blockUs) {
    if (!std::isfinite(periodUs) || periodUs <= 0 || !std::isfinite(blockUs)) {
      reset();
      return false;
    }
    const double seconds = periodUs / 1000000.0;
    const double movementWeight = seen_ ? -std::expm1(-seconds / 30.0) : 0;
    for (uint8_t i = 0; i < 2; ++i) {
      Horizon& h = horizons_[i];
      if (!seen_) { h.periodUs = periodUs; h.blockUs = blockUs; continue; }
      const double oldPeriod = h.periodUs, oldBlock = h.blockUs, oldBpm = h.bpm();
      const double weight = alpha(seconds, minutes(i));
      h.periodUs += weight * (periodUs - h.periodUs);
      h.blockUs += weight * (blockUs - h.blockUs);
      // Saturation keeps maturity state bounded even over years of operation.
      const double mature = 60.0 * minutes(i);
      h.elapsedSeconds = std::fmin(mature, h.elapsedSeconds + seconds);
      h.periodPrecision.observe(h.periodUs - oldPeriod, seconds, 0.01, movementWeight);
      h.bpmPrecision.observe(h.bpm() - oldBpm, seconds, 0.000001, movementWeight);
      h.blockPrecision.observe(h.blockUs - oldBlock, seconds, 0.01, movementWeight);
    }
    seen_ = true;
    return true;
  }
  bool available() const { return seen_; }
  const Horizon& horizon(uint8_t i) const { return horizons_[i ? 1 : 0]; }
  uint16_t minutes(uint8_t i) const { return i ? longMinutes_ : shortMinutes_; }
  bool learning(uint8_t i) const { return !seen_ || horizon(i).elapsedSeconds < 60.0 * minutes(i); }

  // Full-width rows prioritize unit-preserving fit over inter-row alignment.
  // Reduce fractional places first, then omit the optional numeric separator
  // for extreme values. Mark impossible values as range rather than clipping
  // digits or losing the unit.
  void formatRow(char* out, size_t size, uint8_t row, bool stale) const {
    if (!size) return;
    const size_t capacity = size < ROW_CHARS + 1 ? size : ROW_CHARS + 1;
    if (row >= 6) { out[0] = '\0'; return; }
    const uint8_t i = row % 2, metric = row / 2;
    const char* label = metric == 0 ? "P" : metric == 1 ? "B" : "dB";
    const char* unit = metric == 1 ? "BPM" : "us";
    const char which = i ? 'L' : 'S';
    if (!seen_ || stale) {
      std::snprintf(out, capacity, "%s %c: %s", label, which, seen_ ? "stale" : "waiting");
      return;
    }
    const Horizon& h = horizons_[i];
    const double value = metric == 0 ? h.periodUs : metric == 1 ? h.bpm() : h.blockUs;
    const bool fine = metric == 0 ? h.periodPrecision.fine : metric == 1 ? h.bpmPrecision.fine : h.blockPrecision.fine;
    const int decimals = (metric == 1 ? 5 : 1) + (fine ? 1 : 0);
    for (int places = decimals; places >= 0; --places) {
      const double roundedValue = std::fabs(value) < 0.5 / std::pow(10.0, places) ? 0 : value;
      const int written = std::snprintf(out, capacity, metric == 2 ? "%s %c%c %+.*f%s" : "%s %c%c %.*f%s",
          label, which, learning(i) ? '~' : ' ', places, roundedValue, unit);
      if (written >= 0 && size_t(written) < capacity) return;
    }
    const double roundedValue = std::fabs(value) < 0.5 ? 0 : value;
    const int compact = std::snprintf(out, capacity, metric == 2 ? "%s %c%c%+.0f%s" : "%s %c%c%.0f%s",
        label, which, learning(i) ? '~' : ' ', roundedValue, unit);
    if (compact >= 0 && size_t(compact) < capacity) return;
    std::snprintf(out, capacity, "%s %c: range", label, which);
  }

 private:
  Horizon horizons_[2];
  uint16_t shortMinutes_ = OledRatingConfig::DEFAULT_SHORT_MINUTES;
  uint16_t longMinutes_ = OledRatingConfig::DEFAULT_LONG_MINUTES;
  bool seen_ = false;
};
