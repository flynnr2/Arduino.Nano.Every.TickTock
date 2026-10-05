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
};

// Foreground-only API. Capture ISRs must never mutate the completed-swing ring:
// TryPeekOldest copies a stable ring slot with interrupts enabled, and relies
// on ProcessEdges/RetireOldest running in this same non-reentrant foreground.
void swingAssemblerProcessEdges();
bool swingAssemblerTryPeekOldest(FullSwing* out);
bool swingAssemblerRetireOldest();
void swingAssemblerRecordEmitAttemptFailed();
uint32_t swingAssemblerEmitAttemptFailedCount();
uint32_t swingAssemblerTransportDropCount();
