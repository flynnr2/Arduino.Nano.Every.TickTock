#pragma once

#include <stdint.h>

// Forensic reset-cause capture latched in AVR startup (.init3), before normal
// Arduino runtime/setup() processing. Values are retained in .noinit.
void resetCauseEarlyInitNoopReference();

bool resetCauseEarlyValid();
uint8_t resetCauseEarlyRaw();
uint16_t resetCauseEarlyCaptureCount();
