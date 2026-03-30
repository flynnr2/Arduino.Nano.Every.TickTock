#pragma once

#include <stdint.h>

void memoryTelemetryInitAtBoot();
uint16_t memoryTelemetrySample();
uint16_t memoryTelemetryMinFreeBytes();
void emitStatusMemoryTelemetry(bool periodic);
