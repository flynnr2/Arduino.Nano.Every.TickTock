#pragma once

#include "Config.h"

#include <Arduino.h>

void pendulumSetup();
void pendulumLoop();

extern volatile uint32_t droppedEvents;
