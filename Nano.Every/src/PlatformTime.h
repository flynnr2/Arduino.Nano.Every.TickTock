#pragma once

#include "Config.h"


uint32_t platformTicks32();
uint64_t platformTicks64();
uint32_t platformMillis();
uint32_t platformMillisBackstepCount();
void platformDelayMs(uint32_t ms);

// Called from sketch setup() after Arduino core init; no-op unless configured.
void disableArduinoTimebaseTCB3IfConfigured();
