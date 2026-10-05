#pragma once

#include <cstdint>

// Equal-weight full-swing window. No allocations or window scans on ingestion.
class RollingPeriodMean {
 public:
  static constexpr uint16_t SAMPLES = 150; // Ten 15-swing impulse cycles.

  void reset() { count_ = next_ = 0; sum_ = 0; }
  void observe(double value) {
    if (count_ == SAMPLES) sum_ -= values_[next_];
    else ++count_;
    values_[next_] = value;
    sum_ += values_[next_];
    if (++next_ == SAMPLES) next_ = 0;
  }
  uint16_t count() const { return count_; }
  bool ready() const { return count_ == SAMPLES; }
  double mean() const { return count_ ? sum_ / count_ : 0; }

 private:
  // Old entries need not be cleared: each is replaced before it is subtracted.
  // Residuals are small relative to the two-second absolute period. Store
  // them as floats, but sum/subtract the exact stored values in double.
  float values_[SAMPLES] = {};
  double sum_ = 0;
  uint16_t count_ = 0, next_ = 0;
};
