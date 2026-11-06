#pragma once

#include <stdint.h>

struct PpsFreshnessInputs {
  uint32_t now_ms = 0;
  uint32_t last_pps_isr_change_ms = 0;
  uint32_t last_pps_processed_ms = 0;
  uint16_t pps_isr_stale_ms = 0;
  uint16_t pps_processing_stale_ms = 0;
  bool pps_primed = false;
};

struct PpsFreshnessResult {
  bool pps_isr_stale = false;
  bool pps_processing_stale = false;

  bool shouldResetToNoPps() const {
    return pps_isr_stale || pps_processing_stale;
  }
};

inline PpsFreshnessResult evaluatePpsFreshness(const PpsFreshnessInputs& in) {
  PpsFreshnessResult out;
  out.pps_isr_stale =
      (uint32_t)(in.now_ms - in.last_pps_isr_change_ms) > (uint32_t)in.pps_isr_stale_ms;
  out.pps_processing_stale =
      in.pps_primed &&
      ((uint32_t)(in.now_ms - in.last_pps_processed_ms) > (uint32_t)in.pps_processing_stale_ms);
  return out;
}
