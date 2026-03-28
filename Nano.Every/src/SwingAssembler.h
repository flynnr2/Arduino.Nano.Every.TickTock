#pragma once

#include <stdint.h>

#include "Config.h"

struct FullSwing {
  uint32_t tick_block;
  uint32_t tick_block_adj;
  uint32_t tick;
  uint32_t tick_adj;
  uint32_t tock_block;
  uint32_t tock_block_adj;
  uint32_t tock;
  uint32_t tock_adj;
  uint8_t adj_diag;
#if ENABLE_PENDULUM_ADJ_PROVENANCE
  uint32_t pps_seq_row;
#endif
};

void swingAssemblerProcessEdges();
bool swingAssemblerAvailable();
FullSwing swingAssemblerPop();
