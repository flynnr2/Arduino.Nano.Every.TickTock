#pragma once


// -----------------------------------------------------------------------------
// PendulumProtocol.h
// Shared serial interface definitions for Nano Every ↔ Uno R4
// -----------------------------------------------------------------------------

// 0) DATA_SERIAL line tags
//    Prefixes prepended to each line written to DATA_SERIAL so consumers can
//    distinguish content.
static constexpr char TAG_HDR[] = "HDR"; // CSV header/meta line
static constexpr char TAG_DAT[] = "DAT"; // data sample line
static constexpr char TAG_STS[] = "STS"; // status/diagnostic line
static constexpr char TAG_16MHZ[] = "16Mhz"; // raw-cycle sample line
static constexpr char TAG_NS[]   = "nSec";  // nanosecond sample line
static constexpr char TAG_US[]   = "uSec";  // microsecond sample line
static constexpr char TAG_MS[]   = "mSec";  // millisecond sample line

// Status codes for STS lines
enum class StatusCode : uint8_t {
  Ok = 0,            // generic success / informational message
  UnknownCommand,    // command was not recognized
  InvalidParam,      // parameter name is not valid
  InvalidValue,      // value provided is out of range/invalid
  InternalError,     // catch‑all for unexpected failures
  ProgressUpdate,    // catch‑all for unexpected failures
};

// Optional helper to stringify StatusCode
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

// 1) Field indicesa
//    Use these to index into split() or sscanf() results
//    NB: all scaled to use integer math (correctionFator gets split)
enum CsvField {
  CF_TICK = 0,
  CF_TOCK,
  CF_TICK_BLOCK,
  CF_TOCK_BLOCK,
  CF_CORR_INST_PPM,
  CF_CORR_BLEND_PPM,
  CF_GPS_STATUS,
  CF_DROPPED,
  CF_COUNT
};

enum GpsStatus : uint8_t {
  NO_PPS     = 0,  // never seen PPS or PPS absent beyond horizon; no valid epoch
  ACQUIRING  = 1,  // PPS present but not yet stable enough to trust
  LOCKED     = 2,  // PPS present and stable; trusted regime
  HOLDOVER   = 3,  // PPS absent, but last-good scale exists and is being held
  BAD_JITTER = 4,  // PPS present but too noisy/out-of-family to trust
};
static_assert(sizeof(GpsStatus) == 1, "GpsStatus must be 1 byte");

// 2) Command protocol
//    Shared commands for runtime tuning
static constexpr char CMD_HELP[]  = "help";
static constexpr char CMD_GET[]   = "get";
static constexpr char CMD_SET[]   = "set";
static constexpr char CMD_STATS[] = "stats";

// 3) Tunable names
//    Must match members in namespace Tunables
static constexpr char PARAM_CORR_JUMP[]     = "correctionJumpThresh";
static constexpr char PARAM_PPS_EMA_SHIFT[] = "ppsEmaShift";
static constexpr char PARAM_DATA_UNITS[]    = "dataUnits";
static constexpr char PARAM_PPS_FAST_SHIFT[]   = "ppsFastShift";
static constexpr char PARAM_PPS_SLOW_SHIFT[]   = "ppsSlowShift";
static constexpr char PARAM_PPS_HAMPEL_WIN[]   = "ppsHampelWin";
static constexpr char PARAM_PPS_HAMPEL_KX100[] = "ppsHampelKx100";
static constexpr char PARAM_PPS_MEDIAN3[]      = "ppsMedian3";
static constexpr char PARAM_PPS_BLEND_LO_PPM[] = "ppsBlendLoPpm";
static constexpr char PARAM_PPS_BLEND_HI_PPM[] = "ppsBlendHiPpm";
static constexpr char PARAM_PPS_LOCK_R_PPM[]   = "ppsLockRppm";
static constexpr char PARAM_PPS_LOCK_J_PPM[]   = "ppsLockJppm";
static constexpr char PARAM_PPS_UNLOCK_R_PPM[] = "ppsUnlockRppm";
static constexpr char PARAM_PPS_UNLOCK_J_PPM[] = "ppsUnlockJppm";
static constexpr char PARAM_PPS_UNLOCK_COUNT[] = "ppsUnlockCount";
static constexpr char PARAM_PPS_HOLDOVER_MS[]  = "ppsHoldoverMs";

// 4) Structure
struct PendulumSample {
  uint32_t tick;                  // raw ticks since last tick entry
  uint32_t tock;                  // raw ticks since last tick exit
  uint32_t tick_block;            // raw ticks beam entry duration
  uint32_t tock_block;            // raw ticks beam exit duration
  int32_t  corr_inst_ppm;         // instantaneous clock correction (ppm ×1e6)
  int32_t  corr_blend_ppm;        // blended clock correction (ppm ×1e6)
  uint16_t dropped_events;        // number of lost events
  GpsStatus gps_status;           // GPS lock status
  float    temperature_C;         // °C
  float    humidity_pct;          // % RH
  float    pressure_hPa;          // hPa
};

// 5) Units & scaling
//    How raw values map to engineering units
//    - tick etc. are raw TCB0 ticks (integer)
//    - pps correction factors are ppm×1e6 (integer micro‑ppm deltas)
enum class DataUnits : uint8_t {
  RawCycles = 0,
  AdjustedMs,
  AdjustedUs,
  AdjustedNs,
};

constexpr DataUnits DATA_UNITS_DEFAULT = DataUnits::RawCycles;

// Map DataUnits to CSV line tag describing the units
inline const char* dataUnitsTag(DataUnits du) {
  switch (du) {
    case DataUnits::RawCycles:  return TAG_16MHZ;
    case DataUnits::AdjustedNs: return TAG_NS;
    case DataUnits::AdjustedUs: return TAG_US;
    case DataUnits::AdjustedMs: return TAG_MS;
    default:                    return TAG_DAT; // fallback for legacy parsers
  }
}

static constexpr int32_t CORR_PPM_SCALE = 1000000;

// Serial baud rate
#define SERIAL_BAUD_NANO   115200

// 6) Utility declarations (optional)
//   e.g. bool parseParam(const char* name, const char* val);
