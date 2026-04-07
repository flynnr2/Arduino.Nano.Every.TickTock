#pragma once

#include <stdint.h>

#include "Config.h"

struct FullSwing {
  uint32_t swing_seq;
  uint32_t edge0_tcb0;
  uint32_t edge1_tcb0;
  uint32_t edge2_tcb0;
  uint32_t edge3_tcb0;
  uint32_t edge4_tcb0;
  uint32_t tick_block;
  uint32_t tick_block_adj;
  uint64_t tick_block_start_tag;
  uint64_t tick_block_end_tag;
  uint32_t tick;
  uint32_t tick_adj;
  uint64_t tick_start_tag;
  uint64_t tick_end_tag;
  uint32_t tick_total_adj_direct;
  uint64_t tick_total_start_tag;
  uint64_t tick_total_end_tag;
  uint32_t tock_block;
  uint32_t tock_block_adj;
  uint64_t tock_block_start_tag;
  uint64_t tock_block_end_tag;
  uint32_t tock;
  uint32_t tock_adj;
  uint64_t tock_start_tag;
  uint64_t tock_end_tag;
  uint32_t tock_total_adj_direct;
  uint64_t tock_total_start_tag;
  uint64_t tock_total_end_tag;
  uint32_t pps_seq_row;
  // Packed per-component degradation flags (3 bits per component, order:
  // tick,tick_block,tock,tock_block) for missing-scale/degraded/multi-boundary.
  uint16_t adj_comp_diag;
  // AdjDiagBits for component intervals (bitfield fits in [0, 127]).
  uint8_t adj_diag;
  // DirectAdjDiagBits for direct-composite half-swing.
  uint8_t tick_total_adj_diag;  // [0, 15] bitfield.
  // DirectAdjDiagBits for direct-composite half-swing.
  uint8_t tock_total_adj_diag;  // [0, 15] bitfield.
};

void swingAssemblerProcessEdges();
bool swingAssemblerAvailable();
FullSwing swingAssemblerPop();
