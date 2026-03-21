#pragma once

#include <stdint.h>

struct FullSwing {
  uint32_t tick_block;
  uint32_t tick;
  uint32_t tock_block;
  uint32_t tock;
};

struct SwingDiagnostics {
  uint32_t edge_count;
  uint32_t backstep_count;
  uint32_t big_jump_count;
  uint32_t small_jump_count;
  uint32_t wrapish_count;
  uint32_t last_bad_seq;
  uint32_t last_bad_delta;
};

void swingAssemblerProcessEdges();
bool swingAssemblerAvailable();
FullSwing swingAssemblerPop();
SwingDiagnostics swingAssemblerDiagnostics();
