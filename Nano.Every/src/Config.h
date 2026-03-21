#pragma once

#include <Arduino.h>


#ifndef USE_ARDUINO_TIMEBASE
#define USE_ARDUINO_TIMEBASE 0
#endif

#if ((USE_ARDUINO_TIMEBASE) != 0) && ((USE_ARDUINO_TIMEBASE) != 1)
#error "USE_ARDUINO_TIMEBASE must be 0 or 1"
#endif

#ifndef DISABLE_ARDUINO_TCB3_TIMEBASE
#define DISABLE_ARDUINO_TCB3_TIMEBASE ((USE_ARDUINO_TIMEBASE) ? 0 : 1)
#endif

#if ((DISABLE_ARDUINO_TCB3_TIMEBASE) != 0) && ((DISABLE_ARDUINO_TCB3_TIMEBASE) != 1)
#error "DISABLE_ARDUINO_TCB3_TIMEBASE must be 0 or 1"
#endif

#if (USE_ARDUINO_TIMEBASE == 1) && (DISABLE_ARDUINO_TCB3_TIMEBASE == 1)
#error "Cannot disable Arduino TCB3 timebase while USE_ARDUINO_TIMEBASE=1"
#endif
const int ledPin = LED_BUILTIN;

enum class DataUnits : uint8_t;

constexpr uint8_t  RING_SIZE_IR_SENSOR       = 64;      // default ring size for IR sensor readings
constexpr uint8_t  RING_SIZE_PPS             = 16;      // default ring size for GPS PPS interrupts
constexpr float    CORRECTION_JUMP_THRESHOLD = 0.002f;  // >2000 ppm deviation (empirically determined)
constexpr uint8_t  PPS_FAST_SHIFT_DEFAULT    = 3;       // α ≈ 1/8  (~8 s)
constexpr uint8_t  PPS_SLOW_SHIFT_DEFAULT    = 8;       // α ≈ 1/256 (~4.3 min)
constexpr uint8_t  PPS_EMA_SHIFT_DEFAULT     = PPS_SLOW_SHIFT_DEFAULT; // legacy alias default (mirrors slow shift)
constexpr uint8_t  PPS_SHIFT_MIN             = 1;       // right-shift guard: keep >0
constexpr uint8_t  PPS_SHIFT_MAX             = 15;      // right-shift guard: keep small on AVR
constexpr uint8_t  PPS_HAMPEL_WIN_DEFAULT    = 7;       // must be odd (5..9 recommended)
constexpr uint16_t PPS_HAMPEL_KX100_DEFAULT  = 300;     // k=3.00 → 3*scaled MAD
constexpr bool     PPS_MEDIAN3_DEFAULT       = true;    // enable median-of-3 after Hampel
constexpr uint16_t PPS_BLEND_LO_PPM_DEFAULT  = 50;       // prefer slow below this R
constexpr uint16_t PPS_BLEND_HI_PPM_DEFAULT  = 150;      // fully fast above this R
constexpr uint16_t PPS_LOCK_R_PPM_DEFAULT    = 175;      // R threshold to declare LOCKED (matches legacy live policy)
constexpr uint16_t PPS_LOCK_J_PPM_DEFAULT    = 600;      // legacy name: MAD ticks threshold to declare LOCKED
constexpr uint16_t PPS_UNLOCK_R_PPM_DEFAULT  = 300;      // R to drop lock
constexpr uint16_t PPS_UNLOCK_J_PPM_DEFAULT  = 900;      // Jitter to drop lock
constexpr uint8_t  PPS_LOCK_COUNT_MIN        = 1;        // min consecutive good PPS to lock
constexpr uint8_t  PPS_LOCK_COUNT_MAX        = 60;       // max consecutive good PPS to lock
constexpr uint8_t  PPS_LOCK_COUNT_DEFAULT    = 30;       // consecutive good PPS to lock (matches legacy live policy)
constexpr uint8_t  PPS_UNLOCK_COUNT_DEFAULT  = 5;        // consecutive pulses to unlock
constexpr uint16_t PPS_HOLDOVER_MS_DEFAULT   = 60000;    // holdover exit threshold
constexpr uint16_t PPS_STALE_MS_DEFAULT      = 2200;     // PPS freshness timeout for sample arrival
constexpr uint16_t PPS_ISR_STALE_MS_DEFAULT  = 2200;     // PPS freshness timeout for ISR activity
constexpr uint16_t PPS_CFG_REEMIT_DELAY_MS_DEFAULT = 2000; // delay before re-emitting config/status after boot
constexpr uint16_t PPS_ACQUIRE_MIN_MS_DEFAULT = 60000;   // minimum acquire dwell before disciplined lock-in


#ifndef PROTECT_SHARED_READS
#define PROTECT_SHARED_READS 1 // atomic shared-read guard
#endif

#ifndef ENABLE_ISR_DIAGNOSTICS
#define ENABLE_ISR_DIAGNOSTICS 0 // PPS ISR latency telemetry bookkeeping
#endif

#ifndef ENABLE_COHERENT_NOW_BACKSTEP_CHECK
#define ENABLE_COHERENT_NOW_BACKSTEP_CHECK 0 // compatibility define (legacy backstep check path removed)
#endif

#ifndef ENABLE_STS_GPS_DEBUG
#define ENABLE_STS_GPS_DEBUG 0 // emit STS ProgressUpdate "gps_dbg" diagnostics
#endif

#ifndef ENABLE_STS_GPS_SNAP
#define ENABLE_STS_GPS_SNAP 1 // emit anomaly-triggered GPS forensic snapshot burst
#endif

#ifndef ENABLE_STS_GPS_DEBUG_VERBOSE
#define ENABLE_STS_GPS_DEBUG_VERBOSE 0 // 0=minimal gps_dbg fields, 1=full diagnostics payload
#endif

#ifndef PPS_TUNING_TELEMETRY
#define PPS_TUNING_TELEMETRY 0 // concise PPS threshold-tuning telemetry (TUNE_CFG/TUNE_WIN/TUNE_EVT)
#endif

#ifndef ENABLE_PPS_BASELINE_TELEMETRY
#define ENABLE_PPS_BASELINE_TELEMETRY 0 // compact per-PPS baseline telemetry stream for long PPS-only characterization runs
#endif

#ifndef STS_DIAG
#define STS_DIAG 0 // 0=off, 1=court summaries, 2=event-level courtroom diagnostics
#endif

#ifndef STS_DIAG_COURT_PERIOD_MS
#define STS_DIAG_COURT_PERIOD_MS 10000UL // court summary cadence when STS_DIAG>=1
#endif

#ifndef ENABLE_PERIODIC_FLUSH
#define ENABLE_PERIODIC_FLUSH 0 // optional timed DATA_SERIAL.flush() policy in main loop
#endif

#ifndef FLUSH_PERIOD_MS
#define FLUSH_PERIOD_MS 250UL // periodic flush interval when ENABLE_PERIODIC_FLUSH=1
#endif

#ifndef ENABLE_FIXED_POINT_CONVERSIONS
#define ENABLE_FIXED_POINT_CONVERSIONS 0 // reserved opt-in for reciprocal conversion math
#endif

#ifndef LED_ACTIVITY_ENABLE
#define LED_ACTIVITY_ENABLE 1 // toggle onboard LED periodically after successful serial line writes
#endif

#ifndef LED_ACTIVITY_DIV
#define LED_ACTIVITY_DIV 1 // power-of-two successful-line divider for LED activity toggle rate
#endif

#ifndef GIT_SHA
#define GIT_SHA "unknown"
#endif

namespace Tunables {
  extern float     correctionJumpThresh; // compatibility-only/no-op tunable (kept for CLI/EEPROM/status compatibility)
  // DEPRECATED: kept for back-compat; aliased to ppsSlowShift
  extern uint8_t   ppsEmaShift;           // slow PPS EWMA shift (legacy alias)

  // New:
  extern uint8_t   ppsFastShift;          // fast PPS EWMA shift
  extern uint8_t   ppsSlowShift;          // slow PPS EWMA shift
  extern uint8_t   ppsHampelWin;          // compatibility-only/no-op tunable (kept for CLI/EEPROM/status compatibility)
  extern uint16_t  ppsHampelKx100;        // compatibility-only/no-op tunable (kept for CLI/EEPROM/status compatibility)
  extern bool      ppsMedian3;            // compatibility-only/no-op tunable (kept for CLI/EEPROM/status compatibility)
  extern uint16_t  ppsBlendLoPpm;         // drift threshold for full slow blend
  extern uint16_t  ppsBlendHiPpm;         // drift threshold for full fast blend
  extern uint16_t  ppsLockRppm;           // max drift to declare LOCKED
  extern uint16_t  ppsLockJppm;           // legacy name: max MAD residual ticks to declare LOCKED
  extern uint16_t  ppsUnlockRppm;         // drift to unlock from LOCKED
  extern uint16_t  ppsUnlockJppm;         // legacy name: MAD residual ticks to unlock from LOCKED
  extern uint8_t   ppsLockCount;          // consecutive good PPS to lock
  extern uint8_t   ppsUnlockCount;        // consecutive bad PPS to unlock
  extern uint16_t  ppsHoldoverMs;         // holdover dwell before dropping to FREE_RUN
  extern uint16_t  ppsStaleMs;            // PPS freshness timeout for sample arrival
  extern uint16_t  ppsIsrStaleMs;         // PPS freshness timeout for ISR count changes
  extern uint16_t  ppsConfigReemitDelayMs; // startup delay before config/status re-emit
  extern uint16_t  ppsAcquireMinMs;       // minimum ACQUIRE dwell before DISCIPLINED

  extern DataUnits dataUnits;             // output units (ticks/us/ns)

  uint8_t ppsFastShiftActive();
  uint8_t ppsSlowShiftActive();
  uint16_t ppsBlendLoPpmActive();
  uint16_t ppsBlendHiPpmActive();
  uint16_t ppsLockRppmActive();
  uint16_t ppsLockJppmActive();
  uint16_t ppsUnlockRppmActive();
  uint16_t ppsUnlockJppmActive();
  uint8_t ppsLockCountActive();
  uint8_t ppsUnlockCountActive();
  uint16_t ppsHoldoverMsActive();
  uint16_t ppsStaleMsActive();
  uint16_t ppsIsrStaleMsActive();
  uint16_t ppsConfigReemitDelayMsActive();
  uint16_t ppsAcquireMinMsActive();
  void normalizePpsTunables();
}

struct TunableConfig {
  float     correctionJumpThresh;
  uint32_t  seq;

  uint16_t  ppsHampelKx100;
  uint16_t  ppsBlendLoPpm;
  uint16_t  ppsBlendHiPpm;
  uint16_t  ppsLockRppm;
  uint16_t  ppsLockJppm;
  uint16_t  ppsUnlockRppm;
  uint16_t  ppsUnlockJppm;
  uint16_t  ppsHoldoverMs;
  uint16_t  ppsStaleMs;
  uint16_t  ppsIsrStaleMs;
  uint16_t  ppsConfigReemitDelayMs;
  uint16_t  ppsAcquireMinMs;
  uint16_t  crc16;

  uint8_t   ppsEmaShift;
  uint8_t   dataUnits;
  uint8_t   ppsFastShift;
  uint8_t   ppsSlowShift;
  uint8_t   ppsHampelWin;
  uint8_t   ppsMedian3;
  uint8_t   ppsLockCount;
  uint8_t   ppsUnlockCount;
};
