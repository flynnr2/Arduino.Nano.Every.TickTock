#include "MemoryTelemetry.h"

#include <Arduino.h>
#include <stdio.h>
#include <avr/io.h>

#include "Config.h"
#include "PendulumProtocol.h"
#include "SerialParser.h"

#if defined(__AVR__)
extern char __heap_start;
extern void* __brkval;
#endif

namespace {
uint16_t g_last_free_sram_bytes = 0;
uint16_t g_min_free_sram_bytes = 0xFFFFu;
bool g_low_water_warned = false;
char g_mem_status_line[CSV_PAYLOAD_MAX];

uint16_t computeFreeSramBytesNow() {
#if defined(__AVR__)
  const uintptr_t stack_addr = (uintptr_t)SP;
  const uintptr_t heap_addr = (__brkval == nullptr) ? (uintptr_t)&__heap_start : (uintptr_t)__brkval;
  if (stack_addr <= heap_addr) return 0;
  return (uint16_t)(stack_addr - heap_addr);
#else
  return 0;
#endif
}
}  // namespace

void memoryTelemetryInitAtBoot() {
  const uint16_t free_now = computeFreeSramBytesNow();
  g_last_free_sram_bytes = free_now;
  g_min_free_sram_bytes = free_now;
  g_low_water_warned = false;
}

uint16_t memoryTelemetrySample() {
  const uint16_t free_now = computeFreeSramBytesNow();
  g_last_free_sram_bytes = free_now;
  if (free_now < g_min_free_sram_bytes) g_min_free_sram_bytes = free_now;
  return free_now;
}

uint16_t memoryTelemetryMinFreeBytes() {
  return g_min_free_sram_bytes;
}

void emitStatusMemoryTelemetry(bool periodic) {
  const uint16_t free_now = g_last_free_sram_bytes;
  const uint16_t free_min = memoryTelemetryMinFreeBytes();

  const int n = snprintf(g_mem_status_line,
                         sizeof(g_mem_status_line),
                         "mem,free_now=%u,free_min=%u,phase=%s",
                         (unsigned int)free_now,
                         (unsigned int)free_min,
                         periodic ? "periodic" : "boot");
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, g_mem_status_line);

#if ENABLE_MEMORY_LOW_WATER_WARN_STS
  if (!g_low_water_warned && free_now <= MEMORY_LOW_WATER_WARN_BYTES) {
    g_low_water_warned = true;
    const int warn_n = snprintf(g_mem_status_line,
                                sizeof(g_mem_status_line),
                                "mem_warn,free_now=%u,free_min=%u,threshold=%u",
                                (unsigned int)free_now,
                                (unsigned int)free_min,
                                (unsigned int)MEMORY_LOW_WATER_WARN_BYTES);
    if (warn_n > 0) sendStatus(StatusCode::ProgressUpdate, g_mem_status_line);
  }
#endif
}
