#pragma once

// Shared serial interface definitions for the reduced Nano Every firmware.
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#ifdef __AVR__
#include <avr/pgmspace.h>
#endif

// Line tags written to DATA_SERIAL.
static constexpr char TAG_CFG[] = "CFG";     // session/config metadata (includes schema ID, not literal CSV header)
static constexpr char TAG_HDR_PART[] = "HDR_PART"; // segmented sample header part: "<part_index>,<part_count>,<csv_fields>"
static constexpr char TAG_STS[] = "STS";     // structured boot/status telemetry
static constexpr char TAG_SMP[] = "SMP";     // raw-cycle sample rows
static constexpr char TAG_CSW[] = "CSW";     // canonical swing rows (absolute shared-timeline boundaries)
static constexpr char TAG_CPS[] = "CPS";     // canonical PPS rows (absolute shared-timeline boundaries)
static constexpr char TAG_SCH[] = "SCH";     // schema declaration rows ("<tag>,<schema_id>,<csv_fields>")

// Shared wire-contract identifiers (Nano <-> Uno).
static constexpr uint8_t PROTOCOL_VERSION = 1;
static constexpr uint8_t STS_SCHEMA_VERSION = 4;
static constexpr char SAMPLE_SCHEMA_ID[] = "raw_cycles_hz_v7";
static constexpr char CANONICAL_SWING_SCHEMA_ID[] = "canonical_swing_v1";
static constexpr char CANONICAL_PPS_SCHEMA_ID[] = "canonical_pps_v1";
static constexpr uint8_t ADJ_SEMANTICS_VERSION = 2;
// Optional PPS tuning telemetry semantics (`TUNE_CFG`/`TUNE_WIN`/`TUNE_EVT`).
// v2: `TUNE_EVT` exports dedicated unlock-breach columns for all active
// unlock mask bits (0..5): uae/uam/use/usm/urg/uan.
static constexpr uint8_t PPS_TUNING_SEMANTICS_VERSION = 2;

// CFG key names emitted on the wire and consumed by host parsers.
// Compact keys reduce flash and wire bytes while preserving field semantics.
#ifndef COMPACT_CFG_KEYS
#define COMPACT_CFG_KEYS 1
#endif

#if COMPACT_CFG_KEYS
static constexpr char CFG_KEY_PROTOCOL_VERSION[] = "pv";
static constexpr char CFG_KEY_NOMINAL_HZ[] = "nhz";
static constexpr char CFG_KEY_SAMPLE_TAG[] = "st";
static constexpr char CFG_KEY_SAMPLE_SCHEMA[] = "ss";
static constexpr char CFG_KEY_ADJ_SEMANTICS_VERSION[] = "asv";
static constexpr char CFG_KEY_HDR_MODE[] = "hm";
static constexpr char CFG_KEY_EMIT_MODE[] = "em";
static constexpr char CFG_KEY_CANONICAL_SWING_TAG[] = "cst";
static constexpr char CFG_KEY_CANONICAL_SWING_SCHEMA[] = "css";
static constexpr char CFG_KEY_CANONICAL_PPS_TAG[] = "cpt";
static constexpr char CFG_KEY_CANONICAL_PPS_SCHEMA[] = "cps";
#else
static constexpr char CFG_KEY_PROTOCOL_VERSION[] = "protocol_version";
static constexpr char CFG_KEY_NOMINAL_HZ[] = "nominal_hz";
static constexpr char CFG_KEY_SAMPLE_TAG[] = "sample_tag";
static constexpr char CFG_KEY_SAMPLE_SCHEMA[] = "sample_schema";
static constexpr char CFG_KEY_ADJ_SEMANTICS_VERSION[] = "adj_semantics_version";
static constexpr char CFG_KEY_HDR_MODE[] = "hdr_mode";
static constexpr char CFG_KEY_EMIT_MODE[] = "emit_mode";
static constexpr char CFG_KEY_CANONICAL_SWING_TAG[] = "canonical_swing_tag";
static constexpr char CFG_KEY_CANONICAL_SWING_SCHEMA[] = "canonical_swing_schema";
static constexpr char CFG_KEY_CANONICAL_PPS_TAG[] = "canonical_pps_tag";
static constexpr char CFG_KEY_CANONICAL_PPS_SCHEMA[] = "canonical_pps_schema";
#endif

enum class EmitMode : uint8_t {
  UNKNOWN = 0,
  CANONICAL = 1,
  DERIVED = 2,
};

static constexpr EmitMode EMIT_MODE_DERIVED = EmitMode::DERIVED;
static constexpr EmitMode EMIT_MODE_CANONICAL = EmitMode::CANONICAL;

#ifndef PENDULUM_EMIT_MODE
#define PENDULUM_EMIT_MODE EMIT_MODE_DERIVED
#endif

static constexpr EmitMode ACTIVE_EMIT_MODE = PENDULUM_EMIT_MODE;

inline const char* emitModeToStr(EmitMode mode) {
  switch (mode) {
    case EmitMode::UNKNOWN:   return "UNKNOWN";
    case EmitMode::CANONICAL: return "CANONICAL";
    case EmitMode::DERIVED:   return "DERIVED";
    default:                  return "UNKNOWN";
  }
}

static constexpr char HDR_MODE_SEGMENTED_V1[] = "segmented_v1";
static constexpr uint8_t HDR_SEGMENTED_PART_COUNT = 4;
static constexpr uint8_t HDR_SEGMENTED_MAX_PARTS = 8;
static_assert(HDR_SEGMENTED_PART_COUNT <= HDR_SEGMENTED_MAX_PARTS,
              "HDR segmented part count exceeds transport maximum");

static constexpr const char* HDR_MODE_ACTIVE = HDR_MODE_SEGMENTED_V1;

static const char CANONICAL_SWING_SCHEMA[] PROGMEM =
    "seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing,adj_diag,adj_comp_diag";
static const char CANONICAL_PPS_SCHEMA[] PROGMEM =
    "seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps";

// STS payload family identifiers that are part of the wire contract.
static constexpr char STS_FAMILY_SCHEMA[] = "schema";
static constexpr char STS_FAMILY_CFG[] = "cfg";

// Shared packed PPS-tag layout used for interval provenance fields.
static constexpr uint8_t PPS_TAG_TICKS_BITS = 25U;
static constexpr uint32_t PPS_TAG_TICKS_MASK = (1UL << PPS_TAG_TICKS_BITS) - 1UL;
static constexpr uint64_t PPS_TAG_INVALID = 0xFFFFFFFFFFFFFFFFULL;

inline constexpr uint64_t ppsTagInvalid() {
  return PPS_TAG_INVALID;
}

inline constexpr uint64_t ppsTagPack(uint32_t pps_seq, uint32_t ticks_into_sec) {
  return (static_cast<uint64_t>(pps_seq) << PPS_TAG_TICKS_BITS) |
         static_cast<uint64_t>(ticks_into_sec & PPS_TAG_TICKS_MASK);
}

inline constexpr bool ppsTagIsValid(uint64_t tag) {
  return tag != PPS_TAG_INVALID;
}

inline constexpr uint32_t ppsTagSeq(uint64_t tag) {
  return static_cast<uint32_t>(tag >> PPS_TAG_TICKS_BITS);
}

inline constexpr uint32_t ppsTagTicksIntoSec(uint64_t tag) {
  return static_cast<uint32_t>(tag & PPS_TAG_TICKS_MASK);
}

static const char SAMPLE_SCHEMA[] PROGMEM =
    "tick,tick_adj,tick_block,tick_block_adj,tick_total_adj_direct,tick_total_adj_diag,tock,tock_adj,tock_block,tock_block_adj,tock_total_adj_direct,tock_total_adj_diag,tick_total_f_hat_hz,tock_total_f_hat_hz,gps_status,holdover_age_ms,dropped,adj_diag,adj_comp_diag,pps_seq_row";
// Wire contract note:
// - SAMPLE_SCHEMA and enum CsvField define canonical SMP row field order.
// - SAMPLE_SCHEMA_HDR_PARTS are segmented HDR_PART transport/readability chunks only
//   and must not be used as SMP serialization order.
// - Hosts should reassemble/validate all HDR_PART payload fields, then treat
//   SAMPLE_SCHEMA as the only authoritative canonical ordering.
// - Valid HDR_PART payloads do not need to concatenate into SAMPLE_SCHEMA order.

// Segmented HDR payload groups for readability (raw, adjusted/component, aggregate totals, diagnostics/meta).
static const char SAMPLE_SCHEMA_HDR_PART_1_RAW[] PROGMEM =
    "tick,tick_block,tock,tock_block";
static const char SAMPLE_SCHEMA_HDR_PART_2_ADJUSTED_COMPONENT[] PROGMEM =
    "tick_adj,tick_block_adj,tock_adj,tock_block_adj";
static const char SAMPLE_SCHEMA_HDR_PART_3_AGGREGATE_TOTALS[] PROGMEM =
    "tick_total_adj_direct,tick_total_adj_diag,tock_total_adj_direct,tock_total_adj_diag";
static const char SAMPLE_SCHEMA_HDR_PART_4_DIAGNOSTICS_META[] PROGMEM =
    "tick_total_f_hat_hz,tock_total_f_hat_hz,gps_status,holdover_age_ms,dropped,adj_diag,adj_comp_diag,pps_seq_row";

static const char* const SAMPLE_SCHEMA_HDR_PARTS[HDR_SEGMENTED_PART_COUNT] PROGMEM = {
  SAMPLE_SCHEMA_HDR_PART_1_RAW,
  SAMPLE_SCHEMA_HDR_PART_2_ADJUSTED_COMPONENT,
  SAMPLE_SCHEMA_HDR_PART_3_AGGREGATE_TOTALS,
  SAMPLE_SCHEMA_HDR_PART_4_DIAGNOSTICS_META,
};
static_assert((sizeof(SAMPLE_SCHEMA_HDR_PARTS) / sizeof(SAMPLE_SCHEMA_HDR_PARTS[0])) ==
                  HDR_SEGMENTED_PART_COUNT,
              "SAMPLE_SCHEMA_HDR_PARTS must match advertised segmented part count");
// Field semantics:
// - *_total_adj_diag columns use DirectAdjDiagBits and apply to direct full
//   half-swing observables only.
// - adj_diag uses AdjDiagBits and applies to component intervals only.
// - adj_comp_diag provides per-component degradation detail for
//   missing-scale/degraded/multi-boundary in compact packed form.
// - adj_semantics_version defines authority split and diagnostic interpretation:
//   raw component timings remain high-fidelity component observables; *_adj fields
//   are component/sub-interval adjusted observables; *_total_adj_direct fields
//   are direct full-half-swing adjusted observables.
// - tick_total_f_hat_hz / tock_total_f_hat_hz are half-swing applied-scale
//   provenance fields (not generic live row PPS snapshots).

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

// Header/sample field order for the raw-cycle row contract used by parsers/storage.
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
  CF_TICK_TOTAL_F_HAT_HZ,
  CF_TOCK_TOTAL_F_HAT_HZ,
  CF_GPS_STATUS,
  CF_HOLDOVER_AGE_MS,
  CF_DROPPED,
  CF_ADJ_DIAG,
  CF_ADJ_COMP_DIAG,
  CF_PPS_SEQ_ROW,
  CF_COUNT
};

static constexpr uint8_t CF_REQUIRED_COUNT = CF_COUNT;

static const char CSV_FIELD_NAME_00[] PROGMEM = "tick";
static const char CSV_FIELD_NAME_01[] PROGMEM = "tick_adj";
static const char CSV_FIELD_NAME_02[] PROGMEM = "tick_block";
static const char CSV_FIELD_NAME_03[] PROGMEM = "tick_block_adj";
static const char CSV_FIELD_NAME_04[] PROGMEM = "tick_total_adj_direct";
static const char CSV_FIELD_NAME_05[] PROGMEM = "tick_total_adj_diag";
static const char CSV_FIELD_NAME_06[] PROGMEM = "tock";
static const char CSV_FIELD_NAME_07[] PROGMEM = "tock_adj";
static const char CSV_FIELD_NAME_08[] PROGMEM = "tock_block";
static const char CSV_FIELD_NAME_09[] PROGMEM = "tock_block_adj";
static const char CSV_FIELD_NAME_10[] PROGMEM = "tock_total_adj_direct";
static const char CSV_FIELD_NAME_11[] PROGMEM = "tock_total_adj_diag";
static const char CSV_FIELD_NAME_12[] PROGMEM = "tick_total_f_hat_hz";
static const char CSV_FIELD_NAME_13[] PROGMEM = "tock_total_f_hat_hz";
static const char CSV_FIELD_NAME_14[] PROGMEM = "gps_status";
static const char CSV_FIELD_NAME_15[] PROGMEM = "holdover_age_ms";
static const char CSV_FIELD_NAME_16[] PROGMEM = "dropped";
static const char CSV_FIELD_NAME_17[] PROGMEM = "adj_diag";
static const char CSV_FIELD_NAME_18[] PROGMEM = "adj_comp_diag";
static const char CSV_FIELD_NAME_19[] PROGMEM = "pps_seq_row";

static const char* const CSV_FIELD_NAMES[CF_COUNT] PROGMEM = {
  CSV_FIELD_NAME_00,
  CSV_FIELD_NAME_01,
  CSV_FIELD_NAME_02,
  CSV_FIELD_NAME_03,
  CSV_FIELD_NAME_04,
  CSV_FIELD_NAME_05,
  CSV_FIELD_NAME_06,
  CSV_FIELD_NAME_07,
  CSV_FIELD_NAME_08,
  CSV_FIELD_NAME_09,
  CSV_FIELD_NAME_10,
  CSV_FIELD_NAME_11,
  CSV_FIELD_NAME_12,
  CSV_FIELD_NAME_13,
  CSV_FIELD_NAME_14,
  CSV_FIELD_NAME_15,
  CSV_FIELD_NAME_16,
  CSV_FIELD_NAME_17,
  CSV_FIELD_NAME_18,
  CSV_FIELD_NAME_19,
};

inline const char* flashStrAt(const char* const* table, uint8_t index) {
#ifdef __AVR__
  return reinterpret_cast<const char*>(pgm_read_ptr(&table[index]));
#else
  return table[index];
#endif
}

inline size_t flashStrLen(const char* text) {
#ifdef __AVR__
  return text ? strlen_P(text) : 0;
#else
  return text ? strlen(text) : 0;
#endif
}

inline int cmpRamToFlash(const char* ramText, const char* flashText) {
  if (!ramText || !flashText) return (ramText == flashText) ? 0 : 1;
#ifdef __AVR__
  return strcmp_P(ramText, flashText);
#else
  return strcmp(ramText, flashText);
#endif
}

inline int cmpRamToFlashN(const char* ramText, const char* flashText, size_t n) {
  if (!ramText || !flashText) return (ramText == flashText) ? 0 : 1;
#ifdef __AVR__
  return strncmp_P(ramText, flashText, n);
#else
  return strncmp(ramText, flashText, n);
#endif
}

inline void copyFlashToRam(void* dst, const char* flashSrc, size_t len) {
#ifdef __AVR__
  memcpy_P(dst, flashSrc, len);
#else
  memcpy(dst, flashSrc, len);
#endif
}

enum class SchemaValidationResult : uint8_t {
  Ok = 0,
  PartialOrTruncated,
  WrongFieldCount,
  SchemaMismatch,
};

enum class HeaderPartsValidationResult : uint8_t {
  Ok = 0,
  InvalidFormat,
  InvalidPartIndex,
  InvalidPartCount,
  CountMismatch,
  OutOfOrder,
  DuplicatePart,
  MissingPart,
  UnknownField,
  DuplicateField,
  OutputTooSmall,
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
  return csvFieldIndexIsValid(index) ? flashStrAt(CSV_FIELD_NAMES, index) : nullptr;
}

inline size_t requiredSchemaPayloadLen() {
  size_t total = 0;
  for (uint8_t i = 0; i < CF_REQUIRED_COUNT; ++i) {
    total += flashStrLen(flashStrAt(CSV_FIELD_NAMES, i));
    if (i + 1 < CF_REQUIRED_COUNT) ++total;
  }
  return total;
}

inline size_t buildRequiredSchemaPayload(char* out, size_t outSize) {
  const size_t needed = requiredSchemaPayloadLen();
  if (!out || outSize == 0 || needed + 1 > outSize) return 0;
  size_t pos = 0;
  for (uint8_t i = 0; i < CF_REQUIRED_COUNT; ++i) {
    const char* field = flashStrAt(CSV_FIELD_NAMES, i);
    const size_t fieldLen = flashStrLen(field);
    copyFlashToRam(out + pos, field, fieldLen);
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
         strcmp(key, CFG_KEY_SAMPLE_TAG) == 0 ||
         strcmp(key, CFG_KEY_ADJ_SEMANTICS_VERSION) == 0 ||
         strcmp(key, CFG_KEY_HDR_MODE) == 0 ||
         strcmp(key, CFG_KEY_EMIT_MODE) == 0 ||
         strcmp(key, CFG_KEY_CANONICAL_SWING_TAG) == 0 ||
         strcmp(key, CFG_KEY_CANONICAL_SWING_SCHEMA) == 0 ||
         strcmp(key, CFG_KEY_CANONICAL_PPS_TAG) == 0 ||
         strcmp(key, CFG_KEY_CANONICAL_PPS_SCHEMA) == 0;
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
  if (strcmp(key, CFG_KEY_ADJ_SEMANTICS_VERSION) == 0) {
    uint32_t parsed = 0;
    return parseUint32Dec(value, parsed) && parsed == ADJ_SEMANTICS_VERSION
             ? CfgValidationResult::Ok
             : CfgValidationResult::InvalidValue;
  }
  if (strcmp(key, CFG_KEY_HDR_MODE) == 0) {
    return strcmp(value, HDR_MODE_SEGMENTED_V1) == 0
             ? CfgValidationResult::Ok
             : CfgValidationResult::InvalidValue;
  }
  if (strcmp(key, CFG_KEY_EMIT_MODE) == 0) {
    return (strcmp(value, emitModeToStr(EMIT_MODE_DERIVED)) == 0 ||
            strcmp(value, emitModeToStr(EMIT_MODE_CANONICAL)) == 0)
             ? CfgValidationResult::Ok
             : CfgValidationResult::InvalidValue;
  }
  if (strcmp(key, CFG_KEY_CANONICAL_SWING_TAG) == 0) {
    return strcmp(value, TAG_CSW) == 0
             ? CfgValidationResult::Ok
             : CfgValidationResult::InvalidValue;
  }
  if (strcmp(key, CFG_KEY_CANONICAL_SWING_SCHEMA) == 0) {
    return strcmp(value, CANONICAL_SWING_SCHEMA_ID) == 0
             ? CfgValidationResult::Ok
             : CfgValidationResult::InvalidValue;
  }
  if (strcmp(key, CFG_KEY_CANONICAL_PPS_TAG) == 0) {
    return strcmp(value, TAG_CPS) == 0
             ? CfgValidationResult::Ok
             : CfgValidationResult::InvalidValue;
  }
  if (strcmp(key, CFG_KEY_CANONICAL_PPS_SCHEMA) == 0) {
    return strcmp(value, CANONICAL_PPS_SCHEMA_ID) == 0
             ? CfgValidationResult::Ok
             : CfgValidationResult::InvalidValue;
  }
  return CfgValidationResult::InvalidValue;
}

inline bool parseHeaderPartPayload(const char* payload,
                                   uint8_t& partIndexOut,
                                   uint8_t& partCountOut,
                                   const char*& partSchemaPayloadOut) {
  if (!payload || *payload == '\0') return false;
  const char* comma1 = strchr(payload, ',');
  if (!comma1 || comma1 == payload) return false;
  const char* comma2 = strchr(comma1 + 1, ',');
  if (!comma2 || comma2 == comma1 + 1 || *(comma2 + 1) == '\0') return false;

  uint32_t parsedIndex = 0;
  uint32_t parsedCount = 0;
  char indexBuf[4] = {0, 0, 0, 0};
  char countBuf[4] = {0, 0, 0, 0};
  const size_t indexLen = static_cast<size_t>(comma1 - payload);
  const size_t countLen = static_cast<size_t>(comma2 - (comma1 + 1));
  if (indexLen == 0 || indexLen >= sizeof(indexBuf) || countLen == 0 || countLen >= sizeof(countBuf)) {
    return false;
  }
  memcpy(indexBuf, payload, indexLen);
  memcpy(countBuf, comma1 + 1, countLen);
  if (!parseUint32Dec(indexBuf, parsedIndex) || !parseUint32Dec(countBuf, parsedCount)) {
    return false;
  }
  if (parsedIndex == 0 || parsedIndex > 255 || parsedCount == 0 || parsedCount > 255) {
    return false;
  }

  partIndexOut = static_cast<uint8_t>(parsedIndex);
  partCountOut = static_cast<uint8_t>(parsedCount);
  partSchemaPayloadOut = comma2 + 1;
  return true;
}

struct HeaderPartsReassemblyState {
  uint8_t expectedPartCount = 0;
  uint8_t nextPartIndex = 1;
  uint8_t receivedPartCount = 0;
  bool partSeen[HDR_SEGMENTED_MAX_PARTS] = {false, false, false, false, false, false, false, false};
  static constexpr size_t PART_1_LEN = sizeof(SAMPLE_SCHEMA_HDR_PART_1_RAW) - 1;
  static constexpr size_t PART_2_LEN = sizeof(SAMPLE_SCHEMA_HDR_PART_2_ADJUSTED_COMPONENT) - 1;
  static constexpr size_t PART_3_LEN = sizeof(SAMPLE_SCHEMA_HDR_PART_3_AGGREGATE_TOTALS) - 1;
  static constexpr size_t PART_4_LEN = sizeof(SAMPLE_SCHEMA_HDR_PART_4_DIAGNOSTICS_META) - 1;
  static constexpr size_t MAX_PART_PAYLOAD_LEN =
      (PART_1_LEN > PART_2_LEN
        ? (PART_1_LEN > PART_3_LEN
            ? (PART_1_LEN > PART_4_LEN ? PART_1_LEN : PART_4_LEN)
            : (PART_3_LEN > PART_4_LEN ? PART_3_LEN : PART_4_LEN))
        : (PART_2_LEN > PART_3_LEN
            ? (PART_2_LEN > PART_4_LEN ? PART_2_LEN : PART_4_LEN)
            : (PART_3_LEN > PART_4_LEN ? PART_3_LEN : PART_4_LEN)));
  char partPayloadStorage[HDR_SEGMENTED_MAX_PARTS][MAX_PART_PAYLOAD_LEN + 1] = {};
};

inline void resetHeaderPartsReassembly(HeaderPartsReassemblyState& state) {
  state.expectedPartCount = 0;
  state.nextPartIndex = 1;
  state.receivedPartCount = 0;
  for (uint8_t i = 0; i < HDR_SEGMENTED_MAX_PARTS; ++i) {
    state.partSeen[i] = false;
    state.partPayloadStorage[i][0] = '\0';
  }
}

inline HeaderPartsValidationResult ingestHeaderPartPayload(HeaderPartsReassemblyState& state, const char* payload) {
  uint8_t partIndex = 0;
  uint8_t partCount = 0;
  const char* partPayload = nullptr;
  if (!parseHeaderPartPayload(payload, partIndex, partCount, partPayload)) {
    return HeaderPartsValidationResult::InvalidFormat;
  }
  if (partCount == 0 || partCount > HDR_SEGMENTED_MAX_PARTS) return HeaderPartsValidationResult::InvalidPartCount;
  if (partIndex == 0 || partIndex > partCount) return HeaderPartsValidationResult::InvalidPartIndex;

  if (state.expectedPartCount == 0) {
    state.expectedPartCount = partCount;
  } else if (state.expectedPartCount != partCount) {
    return HeaderPartsValidationResult::CountMismatch;
  }
  if (partIndex != state.nextPartIndex) return HeaderPartsValidationResult::OutOfOrder;

  const uint8_t slot = static_cast<uint8_t>(partIndex - 1);
  if (state.partSeen[slot]) return HeaderPartsValidationResult::DuplicatePart;

  const size_t payloadLen = cstrLen(partPayload);
  if (payloadLen == 0 || payloadLen + 1 > sizeof(state.partPayloadStorage[slot])) {
    return HeaderPartsValidationResult::InvalidFormat;
  }
  state.partSeen[slot] = true;
  memcpy(state.partPayloadStorage[slot], partPayload, payloadLen);
  state.partPayloadStorage[slot][payloadLen] = '\0';
  state.receivedPartCount = static_cast<uint8_t>(state.receivedPartCount + 1);
  state.nextPartIndex = static_cast<uint8_t>(partIndex + 1);
  return HeaderPartsValidationResult::Ok;
}

inline int8_t schemaFieldIndexByName(const char* fieldStart, size_t fieldLen) {
  for (uint8_t i = 0; i < CF_REQUIRED_COUNT; ++i) {
    const char* expected = flashStrAt(CSV_FIELD_NAMES, i);
    const size_t expectedLen = flashStrLen(expected);
    if (expectedLen == fieldLen && cmpRamToFlashN(fieldStart, expected, fieldLen) == 0) {
      return static_cast<int8_t>(i);
    }
  }
  return -1;
}

inline HeaderPartsValidationResult finalizeHeaderPartsReassembly(const HeaderPartsReassemblyState& state,
                                                                 char* outSchemaPayload,
                                                                 size_t outSchemaPayloadSize) {
  if (!outSchemaPayload || outSchemaPayloadSize < sizeof(SAMPLE_SCHEMA)) {
    return HeaderPartsValidationResult::OutputTooSmall;
  }
  if (state.expectedPartCount == 0 || state.receivedPartCount != state.expectedPartCount) {
    return HeaderPartsValidationResult::MissingPart;
  }

  bool seenFields[CF_REQUIRED_COUNT];
  for (uint8_t i = 0; i < CF_REQUIRED_COUNT; ++i) seenFields[i] = false;

  for (uint8_t p = 0; p < state.expectedPartCount; ++p) {
    if (!state.partSeen[p] || state.partPayloadStorage[p][0] == '\0') {
      return HeaderPartsValidationResult::MissingPart;
    }

    const size_t fieldCount = countCsvFields(state.partPayloadStorage[p]);
    for (uint8_t field = 0; field < fieldCount; ++field) {
      const char* fieldStart = nullptr;
      size_t fieldLen = 0;
      if (!getCsvFieldSpanAt(state.partPayloadStorage[p], field, fieldStart, fieldLen) || !fieldStart || fieldLen == 0) {
        return HeaderPartsValidationResult::InvalidFormat;
      }
      const int8_t schemaIdx = schemaFieldIndexByName(fieldStart, fieldLen);
      if (schemaIdx < 0) return HeaderPartsValidationResult::UnknownField;
      if (seenFields[schemaIdx]) return HeaderPartsValidationResult::DuplicateField;
      seenFields[schemaIdx] = true;
    }
  }
  for (uint8_t i = 0; i < CF_REQUIRED_COUNT; ++i) {
    if (!seenFields[i]) return HeaderPartsValidationResult::MissingPart;
  }

  copyFlashToRam(outSchemaPayload, SAMPLE_SCHEMA, sizeof(SAMPLE_SCHEMA));
  return HeaderPartsValidationResult::Ok;
}

inline SchemaValidationResult validateHeaderSchemaPayload(const char* payload) {
  if (!payload || *payload == '\0') {
    return SchemaValidationResult::PartialOrTruncated;
  }

  const size_t fieldCount = countCsvFields(payload);
  if (fieldCount < CF_REQUIRED_COUNT) {
    return SchemaValidationResult::PartialOrTruncated;
  }
  if (fieldCount > CF_REQUIRED_COUNT) {
    return SchemaValidationResult::WrongFieldCount;
  }

  for (uint8_t i = 0; i < static_cast<uint8_t>(fieldCount); ++i) {
    const char* fieldStart = nullptr;
    size_t fieldLen = 0;
    if (!getCsvFieldSpanAt(payload, i, fieldStart, fieldLen) || !fieldStart) {
      return SchemaValidationResult::SchemaMismatch;
    }
    const char* expected = flashStrAt(CSV_FIELD_NAMES, i);
    const size_t expectedLen = flashStrLen(expected);
    const bool exactMatch = (fieldLen == expectedLen) && (cmpRamToFlashN(fieldStart, expected, expectedLen) == 0);
    if (!exactMatch) {
      const bool looksPartialTail =
          (i + 1 == fieldCount) &&
          (fieldLen < expectedLen) &&
          (cmpRamToFlashN(fieldStart, expected, fieldLen) == 0);
      return looksPartialTail
               ? SchemaValidationResult::PartialOrTruncated
               : SchemaValidationResult::SchemaMismatch;
    }
  }

  return SchemaValidationResult::Ok;
}

static constexpr size_t SAMPLE_SCHEMA_FIELD_COUNT = CF_REQUIRED_COUNT;
static constexpr size_t SAMPLE_SCHEMA_COMMA_COUNT = SAMPLE_SCHEMA_FIELD_COUNT - 1;
static constexpr size_t PENDULUM_ENV_FIELD_COUNT = 3; // temperature_C,humidity_pct,pressure_hPa
static constexpr size_t PENDULUM_ENV_FIELD_TEMPERATURE_C_LEN = sizeof("temperature_C") - 1;
static constexpr size_t PENDULUM_ENV_FIELD_HUMIDITY_PCT_LEN = sizeof("humidity_pct") - 1;
static constexpr size_t PENDULUM_ENV_FIELD_PRESSURE_HPA_LEN = sizeof("pressure_hPa") - 1;
static constexpr size_t PENDULUM_ENV_FIELDS_TOTAL_LEN =
    PENDULUM_ENV_FIELD_TEMPERATURE_C_LEN +
    PENDULUM_ENV_FIELD_HUMIDITY_PCT_LEN +
    PENDULUM_ENV_FIELD_PRESSURE_HPA_LEN;
static constexpr size_t PENDULUM_CSV_FIELD_COUNT = SAMPLE_SCHEMA_FIELD_COUNT + PENDULUM_ENV_FIELD_COUNT;
static constexpr size_t PENDULUM_CSV_HEADER_REQUIRED_LEN =
    (sizeof(SAMPLE_SCHEMA) - 1) + // canonical SAMPLE_SCHEMA text
    PENDULUM_ENV_FIELD_COUNT +    // commas before appended environment fields
    PENDULUM_ENV_FIELDS_TOTAL_LEN +
    1;                            // null terminator
static constexpr size_t HDR_PART_PREFIX_MAX_LEN = 3 + 1 + 3 + 1; // "<part_index>,<part_count>,"
static constexpr size_t HDR_PART_1_PAYLOAD_LEN = sizeof(SAMPLE_SCHEMA_HDR_PART_1_RAW) - 1;
static constexpr size_t HDR_PART_2_PAYLOAD_LEN = sizeof(SAMPLE_SCHEMA_HDR_PART_2_ADJUSTED_COMPONENT) - 1;
static constexpr size_t HDR_PART_3_PAYLOAD_LEN = sizeof(SAMPLE_SCHEMA_HDR_PART_3_AGGREGATE_TOTALS) - 1;
static constexpr size_t HDR_PART_4_PAYLOAD_LEN = sizeof(SAMPLE_SCHEMA_HDR_PART_4_DIAGNOSTICS_META) - 1;
static constexpr size_t HDR_PART_MAX_PAYLOAD_LEN =
    (HDR_PART_1_PAYLOAD_LEN > HDR_PART_2_PAYLOAD_LEN)
      ? ((HDR_PART_1_PAYLOAD_LEN > HDR_PART_3_PAYLOAD_LEN)
          ? ((HDR_PART_1_PAYLOAD_LEN > HDR_PART_4_PAYLOAD_LEN) ? HDR_PART_1_PAYLOAD_LEN : HDR_PART_4_PAYLOAD_LEN)
          : ((HDR_PART_3_PAYLOAD_LEN > HDR_PART_4_PAYLOAD_LEN) ? HDR_PART_3_PAYLOAD_LEN : HDR_PART_4_PAYLOAD_LEN))
      : ((HDR_PART_2_PAYLOAD_LEN > HDR_PART_3_PAYLOAD_LEN)
          ? ((HDR_PART_2_PAYLOAD_LEN > HDR_PART_4_PAYLOAD_LEN) ? HDR_PART_2_PAYLOAD_LEN : HDR_PART_4_PAYLOAD_LEN)
          : ((HDR_PART_3_PAYLOAD_LEN > HDR_PART_4_PAYLOAD_LEN) ? HDR_PART_3_PAYLOAD_LEN : HDR_PART_4_PAYLOAD_LEN));
static constexpr size_t HDR_PART_LINE_MAX_ENCODED_LEN =
    (sizeof(TAG_HDR_PART) - 1) + 1 + HDR_PART_PREFIX_MAX_LEN + HDR_PART_MAX_PAYLOAD_LEN + 1 + 1;

static_assert(CF_REQUIRED_COUNT == static_cast<uint8_t>(CF_PPS_SEQ_ROW + 1),
              "CF_REQUIRED_COUNT must match CsvField::CF_COUNT");
static_assert((sizeof(CSV_FIELD_NAMES) / sizeof(CSV_FIELD_NAMES[0])) == CF_COUNT,
              "CSV_FIELD_NAMES must match CsvField count");
static_assert(SAMPLE_SCHEMA_COMMA_COUNT + 1 == SAMPLE_SCHEMA_FIELD_COUNT,
              "SAMPLE_SCHEMA comma/field count mismatch");
static_assert(SAMPLE_SCHEMA_FIELD_COUNT == CF_REQUIRED_COUNT,
              "SAMPLE_SCHEMA must include all fields");
static_assert(PENDULUM_CSV_HEADER_REQUIRED_LEN > sizeof(SAMPLE_SCHEMA),
              "Pendulum CSV header requirement must include appended environment columns");
static_assert(HDR_SEGMENTED_PART_COUNT <= HDR_SEGMENTED_MAX_PARTS,
              "HDR segmented part count exceeds parser slots");

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
static constexpr char PARAM_PPS_METROLOGY_GRACE_MS[] = "ppsMetrologyGraceMs";

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
  // Half-swing applied-scale provenance for tick/tock total intervals.
  uint32_t tick_total_f_hat_hz;
  uint32_t tock_total_f_hat_hz;
  uint32_t holdover_age_ms;
  // Trailing wire field with PPS sequence provenance for final interval closure.
  uint32_t pps_seq_row;
  uint16_t dropped_events;    // [0, 65535] dropped-edge counter snapshot.
  uint16_t adj_comp_diag;     // 12-bit payload packed into [0, 4095].
  GpsStatus gps_status;       // [0, 3] GpsStatus enum.
  // AdjDiagBits for component intervals (tick/tick_block/tock/tock_block).
  uint8_t adj_diag;           // [0, 127] bitfield.
};

struct CanonicalSwingSample {
  uint32_t seq;
  uint32_t edge0_tcb0;
  uint32_t edge1_tcb0;
  uint32_t edge2_tcb0;
  uint32_t edge3_tcb0;
  uint32_t edge4_tcb0;
  uint32_t drop_ir;
  uint32_t drop_pps;
  uint32_t drop_swing;
  uint16_t adj_comp_diag;
  uint8_t adj_diag;
};

struct CanonicalPpsSample {
  uint32_t seq;
  uint32_t edge_tcb0;
  uint32_t now32;
  uint16_t cap16;
  uint16_t latency16;
  uint32_t holdover_age_ms;
  uint32_t drop_pps;
  GpsStatus gps_status;
};

#define SERIAL_BAUD_NANO 115200
