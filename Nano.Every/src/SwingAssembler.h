#pragma once

#include <stdint.h>

struct FullSwing {
  uint32_t tick_block;
  uint32_t tick;
  uint32_t tock_block;
  uint32_t tock;
};

void swingAssemblerProcessEdges();
bool swingAssemblerAvailable();
FullSwing swingAssemblerPop();
