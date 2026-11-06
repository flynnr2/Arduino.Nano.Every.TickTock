#pragma once

#include "Config.h"
#include "PendulumCapture.h"
#include "StatusTelemetry.h"

#include <Arduino.h>

void pendulumSetup();
void pendulumLoop();
void resetRuntimeStateAfterTunablesChange();
void emitMetadataNow();
void emitStartupNow();
void noteSerialCommandActivity();
void noteExplicitStartupReplayRequest();

#if ENABLE_PROFILING && DUAL_PPS_PROFILING
struct DualPpsRuntimeCounters {
  uint32_t matched_pairs;
  uint32_t unpaired_tcb1_rising;
  uint32_t unpaired_tcb2_rising;
  uint32_t skipped_pairs;
};

void getDualPpsRuntimeCounters(DualPpsRuntimeCounters& out);
#endif
