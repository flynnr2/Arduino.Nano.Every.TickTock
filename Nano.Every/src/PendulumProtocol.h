#pragma once

// Shared serial interface definitions for the reduced Nano Every firmware.

// Line tags written to DATA_SERIAL.
static constexpr char TAG_CFG[] = "CFG";     // session/config metadata (includes schema ID, not literal CSV header)
static constexpr char TAG_HDR[] = "HDR";     // authoritative literal CSV sample header; SMP rows must match this order
static constexpr char TAG_STS[] = "STS";     // structured boot/status telemetry
static constexpr char TAG_SMP[] = "SMP";     // raw-cycle sample rows

#if ENABLE_PENDULUM_ADJ_PROVENANCE
static constexpr char SAMPLE_SCHEMA[] =
    "tick,tick_adj,tick_block,tick_block_adj,tock,tock_adj,tock_block,tock_block_adj,f_inst_hz,f_hat_hz,gps_status,holdover_age_ms,r_ppm,j_ticks,dropped,adj_diag,pps_seq_row";
#else
static constexpr char SAMPLE_SCHEMA[] =
    "tick,tick_adj,tick_block,tick_block_adj,tock,tock_adj,tock_block,tock_block_adj,f_inst_hz,f_hat_hz,gps_status,holdover_age_ms,r_ppm,j_ticks,dropped,adj_diag";
#endif

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
  CF_TOCK,
  CF_TOCK_ADJ,
  CF_TOCK_BLOCK,
  CF_TOCK_BLOCK_ADJ,
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

// Structured tunables command acknowledgements carried in STS,OK,... replies.
static constexpr char STS_TUNABLES_GET[] = "get";
static constexpr char STS_TUNABLES_SET[] = "set";
static constexpr char STS_TUNABLES_RESET[] = "reset";
static constexpr char STS_EMIT_META[] = "emit,meta";

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
  uint32_t tock;
  uint32_t tock_adj;
  uint32_t tock_block;
  uint32_t tock_block_adj;
  uint32_t f_inst_hz;
  uint32_t f_hat_hz;
  uint32_t holdover_age_ms;
  uint32_t r_ppm;
  uint32_t j_ticks;
  GpsStatus gps_status;
  uint16_t dropped_events;
  uint8_t adj_diag;
  // Optional trailing wire field (when provenance is enabled), but always present
  // in the superset parser/storage contract.
  uint32_t pps_seq_row;
};

#define SERIAL_BAUD_NANO 115200
