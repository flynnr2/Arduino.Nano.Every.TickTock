#pragma once

#include <stdint.h>

#include "Config.h"

struct FullSwing {
  uint32_t tick_block;
  uint32_t tick_block_adj;
  uint32_t tick;
  uint32_t tick_adj;
  uint32_t tick_total_adj_direct;
  uint32_t tock_block;
  uint32_t tock_block_adj;
  uint32_t tock;
  uint32_t tock_adj;
  uint32_t tock_total_adj_direct;
#if ENABLE_PENDULUM_ADJ_PROVENANCE
  uint32_t pps_seq_row;
#endif
  // AdjDiagBits for component intervals.
  uint8_t adj_diag;
  // DirectAdjDiagBits for direct-composite half-swing.
  uint8_t tick_total_adj_diag;
  // DirectAdjDiagBits for direct-composite half-swing.
  uint8_t tock_total_adj_diag;
};

void swingAssemblerProcessEdges();
bool swingAssemblerAvailable();
FullSwing swingAssemblerPop();
