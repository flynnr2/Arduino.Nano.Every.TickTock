#pragma once

#include "EEPROMConfig.h"

#ifndef BUILD_UTC
#define BUILD_UTC __DATE__ " " __TIME__
#endif

#ifndef BUILD_DIRTY
#define BUILD_DIRTY -1
#endif

namespace StsHeader {

inline const char* dirtyField() {
  if (BUILD_DIRTY == 0) return "0";
  if (BUILD_DIRTY == 1) return "1";
  return "unknown";
}

inline const char* timebaseField() {
  return USE_ARDUINO_TIMEBASE ? "arduino" : "tcb0";
}

inline const char* mainClockSourceField() {
  return USE_EXTCLK_MAIN ? "extclk" : "internal";
}

inline uint8_t sharedReadsField() {
  return 1U;
}

inline uint8_t disableTcb3Field() {
  return DISABLE_ARDUINO_TCB3_TIMEBASE ? 1U : 0U;
}

inline uint8_t ppsTuningTelemetryField() {
  return PPS_TUNING_TELEMETRY ? 1U : 0U;
}

inline uint8_t ppsBaselineTelemetryField() {
  return ENABLE_PPS_BASELINE_TELEMETRY ? 1U : 0U;
}

inline uint8_t clockDiagStsField() {
  return ENABLE_CLOCK_DIAG_STS ? 1U : 0U;
}

inline uint8_t periodicFlushField() {
  return ENABLE_PERIODIC_FLUSH ? 1U : 0U;
}

inline uint8_t ledActivityField() {
  return LED_ACTIVITY_ENABLE ? 1U : 0U;
}

} // namespace StsHeader
