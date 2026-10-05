#pragma once

// Uno-only wire validation. The shared PendulumProtocol.h is Nano-owned.
#include "PendulumProtocol.h"
#include "ProgmemCompat.h"

#include <string.h>

enum class CfgValidationResult : uint8_t { Ok, UnknownKey, InvalidValue };
static constexpr char CFG_KEY_FIRMWARE_VERSION[] = "fw";

inline size_t countCsvFields(const char* csv) {
  if (!csv || !*csv) return 0;
  size_t count = 1;
  for (const char* p = csv; *p; ++p) if (*p == ',') ++count;
  return count;
}

inline bool parseUint32Dec(const char* text, uint32_t& out) {
  if (!text || !*text) return false;
  uint32_t value = 0;
  for (const char* p = text; *p; ++p) {
    if (*p < '0' || *p > '9') return false;
    const uint32_t digit = uint32_t(*p - '0');
    if (value > (UINT32_MAX - digit) / 10U) return false;
    value = value * 10U + digit;
  }
  out = value;
  return true;
}

inline bool isProtocolOwnedCfgKey(const char* key) {
  return key && (strcmp(key, CFG_KEY_PROTOCOL_VERSION) == 0 ||
      strcmp(key, CFG_KEY_NOMINAL_HZ) == 0 ||
      strcmp(key, CFG_KEY_CANONICAL_SWING_TAG) == 0 ||
      strcmp(key, CFG_KEY_CANONICAL_SWING_SCHEMA) == 0 ||
      strcmp(key, CFG_KEY_CANONICAL_PPS_TAG) == 0 ||
      strcmp(key, CFG_KEY_CANONICAL_PPS_SCHEMA) == 0 ||
      strcmp(key, CFG_KEY_FIRMWARE_VERSION) == 0);
}

inline CfgValidationResult validateProtocolCfgValue(const char* key, const char* value) {
  if (!isProtocolOwnedCfgKey(key)) return CfgValidationResult::UnknownKey;
  if (!value || !*value) return CfgValidationResult::InvalidValue;
  uint32_t parsed = 0;
  bool valid = false;
  if (strcmp(key, CFG_KEY_PROTOCOL_VERSION) == 0)
    valid = parseUint32Dec(value, parsed) && parsed == PROTOCOL_VERSION;
  else if (strcmp(key, CFG_KEY_NOMINAL_HZ) == 0)
    valid = parseUint32Dec(value, parsed) && parsed >= 1000;
  else if (strcmp(key, CFG_KEY_CANONICAL_SWING_TAG) == 0)
    valid = strcmp(value, TAG_CSW) == 0;
  else if (strcmp(key, CFG_KEY_CANONICAL_SWING_SCHEMA) == 0)
    valid = strcmp(value, CANONICAL_SWING_SCHEMA_ID) == 0;
  else if (strcmp(key, CFG_KEY_CANONICAL_PPS_TAG) == 0)
    valid = strcmp(value, TAG_CPS) == 0;
  else if (strcmp(key, CFG_KEY_CANONICAL_PPS_SCHEMA) == 0)
    valid = strcmp(value, CANONICAL_PPS_SCHEMA_ID) == 0;
  else if (strcmp(key, CFG_KEY_FIRMWARE_VERSION) == 0)
    valid = strlen(value) < 64;
  return valid ? CfgValidationResult::Ok : CfgValidationResult::InvalidValue;
}

inline const char* gpsStatusToStr(GpsStatus status) {
  switch (status) {
    case NO_PPS: return "NO_PPS";
    case ACQUIRING: return "ACQUIRING";
    case LOCKED: return "LOCKED";
    case HOLDOVER: return "HOLDOVER";
    default: return "UNKNOWN";
  }
}
inline const char* gpsStatusToShortStr(GpsStatus status) {
  switch (status) {
    case NO_PPS: return "NO";
    case ACQUIRING: return "ACQ";
    case LOCKED: return "LCK";
    case HOLDOVER: return "HLD";
    default: return "---";
  }
}
