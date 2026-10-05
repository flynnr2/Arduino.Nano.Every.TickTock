#pragma once

// Ownership: shared Nano/Uno wire-contract header.
// Sync rule: this file must remain byte-for-byte identical between Nano and Uno.
// Scope: Nano-emitted / Uno-consumed protocol tags, schema IDs, field order, and
// shared data models only. Keep receiver-only validation/reassembly in
// `PendulumProtocolReceiver.h`.
// Emitter-owned wire contract. This file defines records the Nano may emit and the Uno must understand. Keep this file byte-aligned with the Nano repo. Receiver-only validation/reassembly belongs in `PendulumProtocolReceiver.h`.
// Shared serial interface definitions for the reduced Nano Every firmware.
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#ifdef __AVR__
#include <avr/pgmspace.h>
#else
#ifndef PROGMEM
#define PROGMEM
#endif
#endif

// -----------------------------------------------------------------------------
// Emit wire contract (shared with Uno; compatibility surface).
// -----------------------------------------------------------------------------
// Line tags written to DATA_SERIAL.
static constexpr char TAG_CFG[] = "CFG";     // session/config metadata (includes schema ID, not literal CSV header)
static constexpr char TAG_STS[] = "STS";     // structured boot/status telemetry
static constexpr char TAG_CSW[] = "CSW";     // canonical swing rows (absolute shared-timeline boundaries)
static constexpr char TAG_CPS[] = "CPS";     // canonical PPS rows (absolute shared-timeline boundaries)
static constexpr char TAG_SCH[] = "SCH";     // schema declaration rows ("<tag>,<schema_id>,<csv_fields>")

// Shared wire-contract identifiers (Nano <-> Uno).
static constexpr uint8_t PROTOCOL_VERSION = 3;
static constexpr uint8_t STS_SCHEMA_VERSION = 5;
static constexpr char CANONICAL_SWING_SCHEMA_ID[] = "canonical_swing_v2";
static constexpr char CANONICAL_PPS_SCHEMA_ID[] = "canonical_pps_v1";
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
static constexpr char CFG_KEY_CANONICAL_SWING_TAG[] = "cst";
static constexpr char CFG_KEY_CANONICAL_SWING_SCHEMA[] = "css";
static constexpr char CFG_KEY_CANONICAL_PPS_TAG[] = "cpt";
static constexpr char CFG_KEY_CANONICAL_PPS_SCHEMA[] = "cps";
#else
#error "Only compact CFG keys are supported in this build."
#endif

static const char CANONICAL_SWING_SCHEMA[] PROGMEM =
    "seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing";
static const char CANONICAL_PPS_SCHEMA[] PROGMEM =
    "seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps";

// STS payload family identifiers that are part of the wire contract.
static constexpr char STS_FAMILY_SCHEMA[] = "schema";
static constexpr char STS_FAMILY_CFG[] = "cfg";

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

enum GpsStatus : uint8_t {
  NO_PPS    = 0,
  ACQUIRING = 1,
  LOCKED    = 2,
  HOLDOVER  = 3,
};
static_assert(sizeof(GpsStatus) == 1, "GpsStatus must be 1 byte");

// -----------------------------------------------------------------------------
// Shared data model (shared with Uno; compatibility surface).
// -----------------------------------------------------------------------------
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

// -----------------------------------------------------------------------------
// Nano implementation helpers (Nano-local utility helpers that may remain here).
// -----------------------------------------------------------------------------
#define SERIAL_BAUD_NANO 115200
