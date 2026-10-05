#pragma once
#include "OledRatingEstimator.h"
#include "CanonicalTiming.h"

// Downstream display statistics only; never changes captured/logged samples.
class PeriodDisplayState {
 public:
  void configure(uint16_t shortMinutes, uint16_t longMinutes) {
    if (display_.configure(shortMinutes, longMinutes)) available_ = false;
  }
  void observePps(const CanonicalPpsSample& pps, uint32_t nominalHz, uint32_t now) {
    select(nominalHz);
    if (clock_.observe(pps, nominalHz, now)) {
      resetEstimates();
      haveSwing_ = false;
    }
  }
  void observeSwing(const CanonicalSwingSample& s, uint32_t nominalHz, uint32_t now) {
    select(nominalHz);
    if (haveSwing_ && s.seq == previous_.seq) return;
    if (haveSwing_ && (s.seq - previous_.seq != 1 ||
        s.edge0_tcb0 != previous_.edge4_tcb0 || s.drop_ir != previous_.drop_ir ||
        s.drop_swing != previous_.drop_swing)) resetEstimates();
    previous_ = s;
    haveSwing_ = true;
    CanonicalTiming::Intervals timing = {};
    if (!CanonicalTiming::intervals(s, timing)) { resetEstimates(); return; }
    const double calibrated = clock_.correctedHz(nominalHz, now);
    // Preserve the existing explicit NOMINAL fallback when no current scale exists.
    const double hz = calibrated ? calibrated : nominalHz;
    accept(hz >= 1000 ? double(timing.period) * 1000000.0 / hz : 0,
           hz >= 1000 ? double(timing.blockDelta) * 1000000.0 / hz : 0,
           calibrated != 0, now);
  }
  void formatRatingRow(char* out, size_t size, uint8_t row, uint32_t now) const {
    display_.formatRow(out, size, row, available_ && uint32_t(now - lastMs_) >= OledRatingEstimator::STALE_MS);
  }
  const OledRatingEstimator& estimator() const { return display_; }
  const CanonicalTiming::PpsClock& timebaseClock() const { return clock_; }
  const char* shortTimebase(uint32_t now) const {
    const char* value = timebase(now);
    if (value[0] == 'H') return "HOLD";
    if (value[0] == 'N') return "NOM";
    if (value[0] == 'S') return "STL";
    return value;
  }
  const char* timebase(uint32_t now) const {
    if (!available_) return "WAIT";
    if (uint32_t(now - lastMs_) >= OledRatingEstimator::STALE_MS) return "STALE";
    return corrected_ ? (holdover_ ? "HOLDOVER" : "PPS") : "NOMINAL";
  }
 private:
  void resetEstimates() { display_.reset(); available_ = false; }
  void select(uint32_t nominal) {
    if (nominal == nominal_) return;
    resetEstimates();
    clock_ = CanonicalTiming::PpsClock{};
    previous_ = CanonicalSwingSample{};
    lastMs_ = 0;
    haveSwing_ = corrected_ = holdover_ = false;
    nominal_ = nominal;
  }
  void accept(double period, double block, bool corrected, uint32_t now) {
    if (available_ && (uint32_t(now - lastMs_) >= OledRatingEstimator::STALE_MS || corrected != corrected_)) resetEstimates();
    if (!display_.observe(period, block)) { available_ = false; return; }
    available_ = true;
    corrected_ = corrected;
    holdover_ = false;
    lastMs_ = now;
  }
  OledRatingEstimator display_;
  CanonicalTiming::PpsClock clock_;
  CanonicalSwingSample previous_ = {};
  uint32_t nominal_ = 0, lastMs_ = 0;
  bool haveSwing_ = false, available_ = false, corrected_ = false;
  bool holdover_ = false;
};
