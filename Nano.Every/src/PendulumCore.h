#pragma once

#include "Config.h"

#include <Arduino.h>

void pendulumSetup();
void pendulumLoop();

void emitPpsTuningConfigSnapshot();

extern volatile uint32_t droppedEvents;
