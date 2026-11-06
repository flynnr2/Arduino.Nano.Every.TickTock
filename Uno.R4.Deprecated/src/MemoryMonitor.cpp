#include "MemoryMonitor.h"
#include "DiagLog.h"
#include "Display.h"
#include "SDLogger.h"
#include <cstdio>
#include <climits>

namespace MemoryMonitor {

constexpr unsigned long RAM_CHECK_INTERVAL_MS = 2000;
constexpr unsigned long RAM_TELEMETRY_INTERVAL_MS = 10000;

enum RamState { RAM_OK, RAM_LOW, RAM_CRIT };
static RamState ramState = RAM_OK;
static unsigned long lastRamCheck = 0;
static unsigned long lastRamTelemetry = 0;
static int minFreeRamSeen = INT_MAX;

extern "C" char* sbrk(int);
int freeRam() {
  char stack_dummy;
  return &stack_dummy - sbrk(0);
}

void poll() {
  const unsigned long now = millis();
  if (now - lastRamCheck < RAM_CHECK_INTERVAL_MS && now - lastRamTelemetry < RAM_TELEMETRY_INTERVAL_MS) return;
  if (now - lastRamCheck >= RAM_CHECK_INTERVAL_MS) {
    lastRamCheck = now;
  }
  int fr = freeRam();
  if (fr < minFreeRamSeen) minFreeRamSeen = fr;
  RamState newState = (fr < RAM_CRIT_THRESHOLD) ? RAM_CRIT : (fr < RAM_WARN_THRESHOLD ? RAM_LOW : RAM_OK);
  if (newState != ramState) {
    ramState = newState;
    char msg[40];
    switch (ramState) {
      case RAM_OK:
        snprintf(msg, sizeof(msg), "RAM OK: %d bytes", fr);
        break;
      case RAM_LOW:
        snprintf(msg, sizeof(msg), "RAM LOW: %d bytes", fr);
        break;
      case RAM_CRIT:
        snprintf(msg, sizeof(msg), "RAM CRIT: %d bytes", fr);
        break;
    }
    const DiagLog::Severity severity = (ramState == RAM_CRIT)
                                           ? DiagLog::Severity::Error
                                           : ((ramState == RAM_LOW) ? DiagLog::Severity::Warn : DiagLog::Severity::Info);
    DiagLog::emit(severity, msg);
    SDLogger::logUnoEvent("mem.state", msg);
  }

  if (now - lastRamTelemetry >= RAM_TELEMETRY_INTERVAL_MS) {
    lastRamTelemetry = now;
    SDLogger::logUnoValue("mem", "free_ram_bytes", fr);
    SDLogger::logUnoValue("mem", "min_free_ram_bytes", minFreeRamSeen);
    SDLogger::logUnoLine("mem", "state", (ramState == RAM_CRIT) ? "crit" : (ramState == RAM_LOW ? "low" : "ok"));
  }
}

void serviceBlink() {
  // LED matrix blinking removed; RAM warnings are now logged to the display.
}

int minFreeRam() {
  return (minFreeRamSeen == INT_MAX) ? freeRam() : minFreeRamSeen;
}

} // namespace MemoryMonitor
