#pragma once

#include "Config.h"

#ifndef BUILD_UTC
#define BUILD_UTC "unknown"
#endif

#ifndef BUILD_DIRTY
#define BUILD_DIRTY -1
#endif

namespace StsHeader {

constexpr uint8_t CFG_SCHEMA_VER = 8;
constexpr uint8_t GPS_SCHEMA_VER = 6;
constexpr uint8_t GPS_HEALTH_SCHEMA_VER = 6;
constexpr uint8_t GPS_SNAP_SCHEMA_VER = 3;
constexpr uint8_t COURT_SCHEMA_VER = 2;

constexpr uint8_t FLAG_STS_VERBOSE = ENABLE_STS_GPS_DEBUG ? 1 : 0;
constexpr uint8_t FLAG_GPS_DBG_VERBOSE = ENABLE_STS_GPS_DEBUG_VERBOSE ? 1 : 0;
constexpr uint8_t FLAG_CRLF = 1;
constexpr uint8_t FLAG_EVSYS_PPS = 1;
constexpr uint8_t FLAG_EVSYS_IR = 1;
constexpr uint8_t FLAG_MEDIAN3_DEFAULT = PPS_MEDIAN3_DEFAULT ? 1 : 0;
constexpr uint8_t FLAG_STS_DIAG = (uint8_t)STS_DIAG;

inline const char* dirtyField() {
  if (BUILD_DIRTY == 0) return "0";
  if (BUILD_DIRTY == 1) return "1";
  return "unknown";
}

} // namespace StsHeader
