#pragma once

#include <stdint.h>

#include "Config.h"

// PPS-tagged timestamp on the shared 32-bit TCB0 timeline.
// pps_seq identifies the PPS second segment and ticks_into_sec is the offset
// from that segment start in raw timer ticks.
struct PpsTaggedStamp {
  uint32_t pps_seq;
  uint32_t ticks_into_sec;
};

struct PpsScaleEntry {
  uint32_t pps_seq;
  uint64_t scale_q32;  // round((nominal_hz << 32) / f_hat_hz_for_that_pps_second)
};

// Compact row-level diagnostics for component interval adjustments:
// tick, tick_block, tock, tock_block.
enum AdjDiagBits : uint8_t {
  ADJ_DIAG_TICK_CROSSED        = 1U << 0,
  ADJ_DIAG_TICK_BLOCK_CROSSED  = 1U << 1,
  ADJ_DIAG_TOCK_CROSSED        = 1U << 2,
  ADJ_DIAG_TOCK_BLOCK_CROSSED  = 1U << 3,
  ADJ_DIAG_MISSING_SCALE       = 1U << 4,
  ADJ_DIAG_DEGRADED_FALLBACK   = 1U << 5,
  ADJ_DIAG_MULTI_BOUNDARY      = 1U << 6,
};

// Per-component degradation packing for adj_comp_diag (12-bit payload in uint16_t):
// component slot order: tick, tick_block, tock, tock_block.
// each slot uses 3 bits:
//   bit0: missing PPS scale
//   bit1: degraded fallback used
//   bit2: crossed more than one PPS boundary
enum AdjComponentIndex : uint8_t {
  ADJ_COMPONENT_TICK = 0,
  ADJ_COMPONENT_TICK_BLOCK = 1,
  ADJ_COMPONENT_TOCK = 2,
  ADJ_COMPONENT_TOCK_BLOCK = 3,
};

static constexpr uint8_t ADJ_COMPONENT_DIAG_BITS_PER_COMPONENT = 3U;

static inline uint16_t adjComponentDiagBit(uint8_t component_index, uint8_t bit_in_component) {
  return static_cast<uint16_t>(1U)
      << (component_index * ADJ_COMPONENT_DIAG_BITS_PER_COMPONENT + bit_in_component);
}

// Compact diagnostics for direct-composite half-swing adjustments:
// tick_total_adj_direct / tock_total_adj_direct.
enum DirectAdjDiagBits : uint8_t {
  DIRECT_ADJ_DIAG_CROSSED           = 1U << 0,
  DIRECT_ADJ_DIAG_MISSING_SCALE     = 1U << 1,
  DIRECT_ADJ_DIAG_DEGRADED_FALLBACK = 1U << 2,
  DIRECT_ADJ_DIAG_MULTI_BOUNDARY    = 1U << 3,
};

void ppsAdjustReset(uint32_t nominal_hz);
void ppsAdjustOnPpsPrimed(uint32_t first_edge32, uint32_t active_hz);
void ppsAdjustOnPpsFinalized(uint32_t prev_edge32,
                             uint32_t curr_edge32,
                             uint32_t applied_hz_for_completed_second,
                             uint32_t next_active_hz);

bool ppsAdjustTagTick(uint32_t edge32, PpsTaggedStamp* out);
bool ppsAdjustLookupSeq(uint32_t seq, uint32_t* span_ticks, uint32_t* applied_hz);
bool ppsAdjustIntervalToNominalTicks(const PpsTaggedStamp& start,
                                     const PpsTaggedStamp& end,
                                     uint32_t raw_ticks,
                                     uint32_t* adjusted_ticks,
                                     uint8_t* diag_bits,
                                     uint8_t crossed_bit);
bool ppsAdjustIntervalToNominal16Mhz(const PpsTaggedStamp& start,
                                     const PpsTaggedStamp& end,
                                     uint32_t raw_ticks,
                                     uint32_t* adjusted_ticks,
                                     uint8_t* diag_bits,
                                     uint8_t crossed_bit);
uint32_t ppsAdjustCurrentSeq();
