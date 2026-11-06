#pragma once
#include <stdint.h>

struct ExtClockHandoffDiagnostics {
  uint8_t preSwitchDelayApplied;
  uint8_t soscClearPollSucceeded;
  uint16_t soscClearPollIterations;
  uint8_t mclkctrla;
  uint8_t mclkctrlb;
  uint8_t mclkstatus;
};

// Boot-time-only main clock configuration hook.
// This must run before serial/timer initialization and must not be invoked
// again after startup completes.
void configureMainClockIfConfigured();

ExtClockHandoffDiagnostics getExtClockHandoffDiagnostics();
