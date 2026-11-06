#pragma once

#include "PpsValidator.h"

// Keep health-estimation candidates separate from raw captures.
// An early extra capture must not shorten the next genuine one-second sample.
class PpsEdgeFilter {
public:
  struct Sample {
    uint32_t interval_ticks;
    PpsValidator::SampleClass cls;
    bool early_extra;

    bool accepted() const { return cls == PpsValidator::SampleClass::OK; }
  };

  void prime(uint32_t edge32) { candidate_edge32_ = edge32; }

  Sample observe(uint32_t edge32, const PpsValidator& validator) {
    Sample sample{(uint32_t)(edge32 - candidate_edge32_),
                  PpsValidator::SampleClass::HARD_GLITCH, false};
    sample.cls = validator.classify(sample.interval_ticks, false);
    const uint32_t ref = validator.referenceTicks();
    // An extra edge before the acceptance window is raw diagnostic evidence,
    // not a failed expected second. It must not poison the health/streak window.
    const uint32_t earliest = ref ? ref - PpsValidator::kHardTicks()
                                 : PpsValidator::minOkTicks(PpsValidator::nominalRefTicks());
    sample.early_extra = sample.interval_ticks < earliest;
    const uint32_t latest = ref ? ref + PpsValidator::kHardTicks()
                               : PpsValidator::maxOkTicks(PpsValidator::nominalRefTicks());
    if (sample.accepted() || sample.interval_ticks > latest) {
      // A late edge is only a recovery candidate: it is still rejected, and
      // cannot update the frequency estimate.
      // A subsequent plausible one-second interval is required to resume.
      candidate_edge32_ = edge32;
    }
    return sample;
  }

private:
  uint32_t candidate_edge32_ = 0;
};
