#pragma once

// Shared serial interface definitions for the reduced Nano Every firmware.
#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Line tags written to DATA_SERIAL.
static constexpr char TAG_CFG[] = "CFG";     // session/config metadata (includes schema ID, not literal CSV header)
static constexpr char TAG_HDR[] = "HDR";     // authoritative literal CSV sample header; SMP rows must match this order
static constexpr char TAG_STS[] = "STS";     // structured boot/status telemetry
static constexpr char TAG_SMP[] = "SMP";     // raw-cycle sample rows

// Shared wire-contract identifiers (Nano <-> Uno).
static constexpr uint8_t PROTOCOL_VERSION = 1;
static constexpr uint8_t STS_SCHEMA_VERSION = 4;
static constexpr char SAMPLE_SCHEMA_ID[] = "raw_cycles_hz_v4";

// CFG key names emitted on the wire and consumed by host parsers.
static constexpr char CFG_KEY_PROTOCOL_VERSION[] = "protocol_version";
static constexpr char CFG_KEY_NOMINAL_HZ[] = "nominal_hz";
static constexpr char CFG_KEY_SAMPLE_TAG[] = "sample_tag";
static constexpr char CFG_KEY_SAMPLE_SCHEMA[] = "sample_schema";

// STS payload family identifiers that are part of the wire contract.
static constexpr char STS_FAMILY_SCHEMA[] = "schema";
static constexpr char STS_FAMILY_CFG[] = "cfg";

#if ENABLE_PENDULUM_ADJ_PROVENANCE
static constexpr char SAMPLE_SCHEMA[] =
    "tick,tick_adj,tick_block,tick_block_adj,tick_total_adj_direct,tick_total_adj_diag,tock,tock_adj,tock_block,tock_block_adj,tock_total_adj_direct,tock_total_adj_diag,f_inst_hz,f_hat_hz,gps_status,holdover_age_ms,r_ppm,j_ticks,dropped,adj_diag,pps_seq_row";
#else
static constexpr char SAMPLE_SCHEMA[] =
    "tick,tick_adj,tick_block,tick_block_adj,tick_total_adj_direct,tick_total_adj_diag,tock,tock_adj,tock_block,tock_block_adj,tock_total_adj_direct,tock_total_adj_diag,f_inst_hz,f_hat_hz,gps_status,holdover_age_ms,r_ppm,j_ticks,dropped,adj_diag";
#endif
// Field semantics:
// - *_total_adj_diag columns use DirectAdjDiagBits and apply to direct full
//   half-swing observables only.
// - adj_diag uses AdjDiagBits and applies to component intervals only.

// Status codes for STS lines.
enum class StatusCode : uint8_t {
  Ok = 0,
  UnknownCommand,
  InvalidParam,
  InvalidValue,
  InternalError,
  ProgressUpdate,
};

inline const char* statusCodeToStr(StatusCode code) {
  switch (code) {
    case StatusCode::Ok:             return "OK";
    case StatusCode::UnknownCommand: return "UNKNOWN_COMMAND";
    case StatusCode::InvalidParam:   return "INVALID_PARAM";
    case StatusCode::InvalidValue:   return "INVALID_VALUE";
    case StatusCode::InternalError:  return "INTERNAL_ERROR";
    case StatusCode::ProgressUpdate: return "PROGRESS_UPDATE";
    default:                         return "UNKNOWN";
  }
}

// Header/sample field order for the superset raw-cycle row contract used by parsers/storage.
// Wire emission remains controlled by SAMPLE_SCHEMA and may omit trailing optional fields.
enum CsvField {
  CF_TICK = 0,
  CF_TICK_ADJ,
  CF_TICK_BLOCK,
  CF_TICK_BLOCK_ADJ,
  CF_TICK_TOTAL_ADJ_DIRECT,
  CF_TICK_TOTAL_ADJ_DIAG,
  CF_TOCK,
  CF_TOCK_ADJ,
  CF_TOCK_BLOCK,
  CF_TOCK_BLOCK_ADJ,
  CF_TOCK_TOTAL_ADJ_DIRECT,
  CF_TOCK_TOTAL_ADJ_DIAG,
  CF_F_INST_HZ,
  CF_F_HAT_HZ,
  CF_GPS_STATUS,
  CF_HOLDOVER_AGE_MS,
  CF_R_PPM,
  CF_J_TICKS,
  CF_DROPPED,
  CF_ADJ_DIAG,
  CF_PPS_SEQ_ROW,
  CF_COUNT
};

static constexpr uint8_t CF_REQUIRED_COUNT = CF_ADJ_DIAG + 1;
static constexpr uint8_t CF_OPTIONAL_COUNT = CF_COUNT;

static constexpr const char* const CSV_FIELD_NAMES[CF_COUNT] = {
  "tick",
  "tick_adj",
  "tick_block",
  "tick_block_adj",
  "tick_total_adj_direct",
  "tick_total_adj_diag",
  "tock",
  "tock_adj",
  "tock_block",
  "tock_block_adj",
  "tock_total_adj_direct",
  "tock_total_adj_diag",
  "f_inst_hz",
  "f_hat_hz",
  "gps_status",
  "holdover_age_ms",
  "r_ppm",
  "j_ticks",
  "dropped",
  "adj_diag",
  "pps_seq_row",
};

enum class SchemaValidationResult : uint8_t {
  Ok = 0,
  PartialOrTruncated,
  WrongFieldCount,
  SchemaMismatch,
};

enum class CfgValidationResult : uint8_t {
  Ok = 0,
  UnknownKey,
  InvalidValue,
};

inline constexpr bool csvFieldIndexIsValid(uint8_t index) {
  return index < static_cast<uint8_t>(CF_COUNT);
}

inline size_t countCsvFields(const char* csv) {
  if (!csv || *csv == '\0') return 0;
  size_t count = 1;
  for (const char* p = csv; *p; ++p) {
    if (*p == ',') ++count;
  }
  return count;
}

inline size_t cstrLen(const char* text) {
  size_t len = 0;
  if (!text) return 0;
  while (text[len] != '\0') ++len;
  return len;
}

inline size_t countCommas(const char* csv) {
  size_t count = 0;
  if (!csv) return 0;
  for (const char* p = csv; *p; ++p) {
    if (*p == ',') ++count;
  }
  return count;
}

inline bool getCsvFieldSpanAt(const char* csv, uint8_t index, const char*& fieldStart, size_t& fieldLen) {
  fieldStart = nullptr;
  fieldLen = 0;
  if (!csv || *csv == '\0') return false;
  uint8_t field = 0;
  const char* start = csv;
  for (const char* p = csv; ; ++p) {
    const char ch = *p;
    if (ch == ',' || ch == '\0') {
      if (field == index) {
        fieldStart = start;
        fieldLen = static_cast<size_t>(p - start);
        return true;
      }
      if (ch == '\0') return false;
      ++field;
      start = p + 1;
    }
  }
}

inline const char* schemaFieldNameAt(uint8_t index) {
  return csvFieldIndexIsValid(index) ? CSV_FIELD_NAMES[index] : nullptr;
}

inline size_t requiredSchemaPayloadLen() {
  size_t total = 0;
  for (uint8_t i = 0; i < CF_REQUIRED_COUNT; ++i) {
    total += cstrLen(CSV_FIELD_NAMES[i]);
    if (i + 1 < CF_REQUIRED_COUNT) ++total;
  }
  return total;
}

inline size_t buildRequiredSchemaPayload(char* out, size_t outSize) {
  const size_t needed = requiredSchemaPayloadLen();
  if (!out || outSize == 0 || needed + 1 > outSize) return 0;
  size_t pos = 0;
  for (uint8_t i = 0; i < CF_REQUIRED_COUNT; ++i) {
    const char* field = CSV_FIELD_NAMES[i];
    const size_t fieldLen = cstrLen(field);
    memcpy(out + pos, field, fieldLen);
    pos += fieldLen;
    if (i + 1 < CF_REQUIRED_COUNT) {
      out[pos++] = ',';
    }
  }
  out[pos] = '\0';
  return pos;
}

inline bool isProtocolOwnedCfgKey(const char* key) {
  if (!key) return false;
  return strcmp(key, CFG_KEY_PROTOCOL_VERSION) == 0 ||
         strcmp(key, CFG_KEY_NOMINAL_HZ) == 0 ||
         strcmp(key, CFG_KEY_SAMPLE_SCHEMA) == 0 ||
         strcmp(key, CFG_KEY_SAMPLE_TAG) == 0;
}

inline bool parseUint32Dec(const char* text, uint32_t& valueOut) {
  if (!text || *text == '\0') return false;
  uint32_t value = 0;
  for (const char* p = text; *p; ++p) {
    const char c = *p;
    if (c < '0' || c > '9') return false;
    const uint32_t digit = static_cast<uint32_t>(c - '0');
    if (value > (0xFFFFFFFFUL - digit) / 10UL) return false;
    value = value * 10UL + digit;
  }
  valueOut = value;
  return true;
}

inline CfgValidationResult validateProtocolCfgValue(const char* key, const char* value, uint32_t expectedNominalHz) {
  if (!isProtocolOwnedCfgKey(key)) return CfgValidationResult::UnknownKey;
  if (!value) return CfgValidationResult::InvalidValue;

  if (strcmp(key, CFG_KEY_PROTOCOL_VERSION) == 0) {
    uint32_t parsed = 0;
    return parseUint32Dec(value, parsed) && parsed == PROTOCOL_VERSION
             ? CfgValidationResult::Ok
             : CfgValidationResult::InvalidValue;
  }
  if (strcmp(key, CFG_KEY_NOMINAL_HZ) == 0) {
    uint32_t parsed = 0;
    return parseUint32Dec(value, parsed) && parsed == expectedNominalHz
             ? CfgValidationResult::Ok
             : CfgValidationResult::InvalidValue;
  }
  if (strcmp(key, CFG_KEY_SAMPLE_SCHEMA) == 0) {
    return strcmp(value, SAMPLE_SCHEMA_ID) == 0
             ? CfgValidationResult::Ok
             : CfgValidationResult::InvalidValue;
  }
  if (strcmp(key, CFG_KEY_SAMPLE_TAG) == 0) {
    return strcmp(value, TAG_SMP) == 0
             ? CfgValidationResult::Ok
             : CfgValidationResult::InvalidValue;
  }
  return CfgValidationResult::InvalidValue;
}

inline SchemaValidationResult validateHeaderSchemaPayload(const char* payload) {
  if (!payload || *payload == '\0') {
    return SchemaValidationResult::PartialOrTruncated;
  }

  const size_t fieldCount = countCsvFields(payload);
  if (fieldCount < CF_REQUIRED_COUNT) {
    return SchemaValidationResult::PartialOrTruncated;
  }
  if (fieldCount > CF_OPTIONAL_COUNT) {
    return SchemaValidationResult::WrongFieldCount;
  }
  if (fieldCount != CF_REQUIRED_COUNT && fieldCount != CF_OPTIONAL_COUNT) {
    return SchemaValidationResult::WrongFieldCount;
  }

  for (uint8_t i = 0; i < static_cast<uint8_t>(fieldCount); ++i) {
    const char* fieldStart = nullptr;
    size_t fieldLen = 0;
    if (!getCsvFieldSpanAt(payload, i, fieldStart, fieldLen) || !fieldStart) {
      return SchemaValidationResult::SchemaMismatch;
    }
    const char* expected = CSV_FIELD_NAMES[i];
    const size_t expectedLen = cstrLen(expected);
    const bool exactMatch = (fieldLen == expectedLen) && (strncmp(fieldStart, expected, expectedLen) == 0);
    if (!exactMatch) {
      const bool looksPartialTail =
          (i + 1 == fieldCount) &&
          (fieldLen < expectedLen) &&
          (strncmp(fieldStart, expected, fieldLen) == 0);
      return looksPartialTail
               ? SchemaValidationResult::PartialOrTruncated
               : SchemaValidationResult::SchemaMismatch;
    }
  }

  return SchemaValidationResult::Ok;
}

#if ENABLE_PENDULUM_ADJ_PROVENANCE
static constexpr size_t SAMPLE_SCHEMA_FIELD_COUNT = CF_OPTIONAL_COUNT;
#else
static constexpr size_t SAMPLE_SCHEMA_FIELD_COUNT = CF_REQUIRED_COUNT;
#endif
static constexpr size_t SAMPLE_SCHEMA_COMMA_COUNT = SAMPLE_SCHEMA_FIELD_COUNT - 1;
static constexpr size_t HDR_LINE_MIN_ENCODED_LEN = sizeof(TAG_HDR) - 1 + 1 + sizeof(SAMPLE_SCHEMA) - 1 + 1;

static_assert(CF_REQUIRED_COUNT == static_cast<uint8_t>(CF_ADJ_DIAG + 1),
              "CF_REQUIRED_COUNT must include all required enum fields");
static_assert(CF_OPTIONAL_COUNT == static_cast<uint8_t>(CF_COUNT),
              "CF_OPTIONAL_COUNT must match CsvField::CF_COUNT");
static_assert(CF_REQUIRED_COUNT <= CF_OPTIONAL_COUNT,
              "Required field count cannot exceed optional superset count");
static_assert((sizeof(CSV_FIELD_NAMES) / sizeof(CSV_FIELD_NAMES[0])) == CF_COUNT,
              "CSV_FIELD_NAMES must match CsvField count");
static_assert(SAMPLE_SCHEMA_COMMA_COUNT + 1 == SAMPLE_SCHEMA_FIELD_COUNT,
              "SAMPLE_SCHEMA comma/field count mismatch");
#if ENABLE_PENDULUM_ADJ_PROVENANCE
static_assert(SAMPLE_SCHEMA_FIELD_COUNT == CF_OPTIONAL_COUNT,
              "SAMPLE_SCHEMA must include optional trailing fields when provenance is enabled");
#else
static_assert(SAMPLE_SCHEMA_FIELD_COUNT == CF_REQUIRED_COUNT,
              "SAMPLE_SCHEMA must include required-only fields when provenance is disabled");
#endif

enum GpsStatus : uint8_t {
  NO_PPS    = 0,
  ACQUIRING = 1,
  LOCKED    = 2,
  HOLDOVER  = 3,
};
static_assert(sizeof(GpsStatus) == 1, "GpsStatus must be 1 byte");

inline const char* gpsStatusToStr(GpsStatus status) {
  switch (status) {
    case NO_PPS:    return "NO_PPS";
    case ACQUIRING: return "ACQUIRING";
    case LOCKED:    return "LOCKED";
    case HOLDOVER:  return "HOLDOVER";
    default:        return "UNKNOWN";
  }
}

inline const char* gpsStatusToShortStr(GpsStatus status) {
  switch (status) {
    case NO_PPS:    return "NO";
    case ACQUIRING: return "ACQ";
    case LOCKED:    return "LCK";
    case HOLDOVER:  return "HLD";
    default:        return "UNK";
  }
}

// Runtime CLI commands.
static constexpr char CMD_HELP[] = "help";
static constexpr char CMD_GET[]  = "get";
static constexpr char CMD_SET[]  = "set";
static constexpr char CMD_RESET[] = "reset";
static constexpr char CMD_RESET_DEFAULTS[] = "defaults";
static constexpr char CMD_EMIT[] = "emit";
static constexpr char CMD_EMIT_META[] = "meta";
static constexpr char CMD_EMIT_STARTUP[] = "startup";

// Structured tunables command acknowledgements carried in STS,OK,... replies.
static constexpr char STS_TUNABLES_GET[] = "get";
static constexpr char STS_TUNABLES_SET[] = "set";
static constexpr char STS_TUNABLES_RESET[] = "reset";
static constexpr char STS_EMIT_META[] = "emit,meta";
static constexpr char STS_EMIT_STARTUP[] = "emit,startup";

// Runtime tunable names. These must match namespace Tunables.
static constexpr char PARAM_PPS_FAST_SHIFT[]   = "ppsFastShift";
static constexpr char PARAM_PPS_SLOW_SHIFT[]   = "ppsSlowShift";
static constexpr char PARAM_PPS_BLEND_LO_PPM[] = "ppsBlendLoPpm";
static constexpr char PARAM_PPS_BLEND_HI_PPM[] = "ppsBlendHiPpm";
static constexpr char PARAM_PPS_LOCK_R_PPM[]   = "ppsLockRppm";
static constexpr char PARAM_PPS_LOCK_MAD_TICKS[] = "ppsLockMadTicks";
static constexpr char PARAM_PPS_LOCK_MAD_TICKS_DEPRECATED_ALIAS[] = "ppsLockJppm";
static constexpr char PARAM_PPS_UNLOCK_R_PPM[] = "ppsUnlockRppm";
static constexpr char PARAM_PPS_UNLOCK_MAD_TICKS[] = "ppsUnlockMadTicks";
static constexpr char PARAM_PPS_UNLOCK_MAD_TICKS_DEPRECATED_ALIAS[] = "ppsUnlockJppm";
static constexpr char PARAM_PPS_LOCK_COUNT[]   = "ppsLockCount";
static constexpr char PARAM_PPS_UNLOCK_COUNT[] = "ppsUnlockCount";
static constexpr char PARAM_PPS_HOLDOVER_MS[]  = "ppsHoldoverMs";
static constexpr char PARAM_PPS_STALE_MS[]     = "ppsStaleMs";
static constexpr char PARAM_PPS_ISR_STALE_MS[] = "ppsIsrStaleMs";
static constexpr char PARAM_PPS_CFG_REEMIT_DELAY_MS[] = "ppsCfgReemitDelayMs";
static constexpr char PARAM_PPS_ACQUIRE_MIN_MS[] = "ppsAcquireMinMs";

struct PendulumSample {
  uint32_t tick;
  uint32_t tick_adj;
  uint32_t tick_block;
  uint32_t tick_block_adj;
  uint32_t tick_total_adj_direct;
  // DirectAdjDiagBits for tick_total_adj_direct (direct full half-swing).
  uint8_t tick_total_adj_diag;
  uint32_t tock;
  uint32_t tock_adj;
  uint32_t tock_block;
  uint32_t tock_block_adj;
  uint32_t tock_total_adj_direct;
  // DirectAdjDiagBits for tock_total_adj_direct (direct full half-swing).
  uint8_t tock_total_adj_diag;
  uint32_t f_inst_hz;
  uint32_t f_hat_hz;
  uint32_t holdover_age_ms;
  uint32_t r_ppm;
  uint32_t j_ticks;
  GpsStatus gps_status;
  uint16_t dropped_events;
  // AdjDiagBits for component intervals (tick/tick_block/tock/tock_block).
  uint8_t adj_diag;
  // Optional trailing wire field (when provenance is enabled), but always present
  // in the superset parser/storage contract.
  uint32_t pps_seq_row;
};

#define SERIAL_BAUD_NANO 115200
