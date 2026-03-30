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
