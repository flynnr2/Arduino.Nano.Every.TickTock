#pragma once

#include <Arduino.h>

#ifndef USE_ARDUINO_TIMEBASE
#define USE_ARDUINO_TIMEBASE 0
#endif

#if ((USE_ARDUINO_TIMEBASE) != 0) && ((USE_ARDUINO_TIMEBASE) != 1)
#error "USE_ARDUINO_TIMEBASE must be 0 or 1"
#endif

// Build-time firmware clock contract:
// - F_CPU remains the Arduino/core toolchain contract.
// - MAIN_CLOCK_HZ is the firmware's semantic source of truth for nominal
//   timer/CPU tick rate assumptions used in runtime math and telemetry.
// External main clock mode is a boot-time-only option for Nano Every /
// ATmega4809 EXTCLK use and must match the board build's F_CPU.
#ifndef USE_EXTCLK_MAIN
#define USE_EXTCLK_MAIN 0
#endif

#if ((USE_EXTCLK_MAIN) != 0) && ((USE_EXTCLK_MAIN) != 1)
#error "USE_EXTCLK_MAIN must be 0 or 1"
#endif

#ifndef MAIN_CLOCK_HZ
#define MAIN_CLOCK_HZ F_CPU
#endif

static_assert(static_cast<uint32_t>(MAIN_CLOCK_HZ) == static_cast<uint32_t>(F_CPU),
              "MAIN_CLOCK_HZ must match F_CPU");

#ifndef DISABLE_ARDUINO_TCB3_TIMEBASE
#define DISABLE_ARDUINO_TCB3_TIMEBASE ((USE_ARDUINO_TIMEBASE) ? 0 : 1)
#endif

#if ((DISABLE_ARDUINO_TCB3_TIMEBASE) != 0) && ((DISABLE_ARDUINO_TCB3_TIMEBASE) != 1)
#error "DISABLE_ARDUINO_TCB3_TIMEBASE must be 0 or 1"
#endif

#if (USE_ARDUINO_TIMEBASE == 1) && (DISABLE_ARDUINO_TCB3_TIMEBASE == 1)
#error "Cannot disable Arduino TCB3 timebase while USE_ARDUINO_TIMEBASE=1"
#endif

#ifndef PPS_TUNING_TELEMETRY
#define PPS_TUNING_TELEMETRY 1 // emit optional TUNE_CFG/TUNE_WIN/TUNE_EVT STS records
#endif

#ifndef ENABLE_PPS_BASELINE_TELEMETRY
#define ENABLE_PPS_BASELINE_TELEMETRY 1 // emit optional PPS_BASE records for PPS-only characterization
#endif

#ifndef ENABLE_CLOCK_DIAG_STS
#define ENABLE_CLOCK_DIAG_STS 1 // emit optional STS clock-diagnostic records at boot
#endif

#ifndef ENABLE_PENDULUM_ADJ_PROVENANCE
#define ENABLE_PENDULUM_ADJ_PROVENANCE 1 // include compact PPS-adjustment provenance fields in SMP/HDR rows
#endif

#ifndef ENABLE_PERIODIC_FLUSH
#define ENABLE_PERIODIC_FLUSH 0 // periodically flush DATA_SERIAL from the main loop
#endif

#ifndef FLUSH_PERIOD_MS
#define FLUSH_PERIOD_MS 250UL // periodic flush interval when ENABLE_PERIODIC_FLUSH=1
#endif

#ifndef LED_ACTIVITY_ENABLE
#define LED_ACTIVITY_ENABLE 1 // toggle the onboard LED after successful serial writes
#endif

#ifndef LED_ACTIVITY_DIV
#define LED_ACTIVITY_DIV 1 // power-of-two divider for LED_ACTIVITY_ENABLE write counts
#endif

#ifndef GIT_SHA
#define GIT_SHA "unknown"
#endif

const int ledPin = LED_BUILTIN;

constexpr uint8_t  RING_SIZE_IR_SENSOR              = 64;      // IR edge ring depth
constexpr uint8_t  RING_SIZE_SWING_ROWS             = 16;      // completed full-swing row ring depth (slower-rate queue than edge capture)
constexpr uint8_t  RING_SIZE_PPS                    = 16;      // PPS capture ring depth
constexpr uint8_t  PPS_SCALE_RING_SIZE              = 16;      // finalized PPS-second scale spans retained for interval correction
constexpr uint8_t  PPS_FAST_SHIFT_DEFAULT           = 3;       // fast EWMA gain (~8 s)
constexpr uint8_t  PPS_SLOW_SHIFT_DEFAULT           = 8;       // slow EWMA gain (~4.3 min)
constexpr uint8_t  PPS_SHIFT_MIN                    = 1;       // smallest supported EWMA shift
constexpr uint8_t  PPS_SHIFT_MAX                    = 15;      // largest supported EWMA shift on AVR
constexpr uint16_t PPS_BLEND_LO_PPM_DEFAULT         = 50;      // prefer the slow estimate below this R threshold
constexpr uint16_t PPS_BLEND_HI_PPM_DEFAULT         = 150;     // prefer the fast estimate above this R threshold
constexpr uint16_t PPS_LOCK_R_PPM_DEFAULT           = 175;     // max frequency error to enter DISCIPLINED
constexpr uint16_t PPS_LOCK_MAD_TICKS_DEFAULT       = 600;     // max MAD residual ticks to enter DISCIPLINED
constexpr uint16_t PPS_UNLOCK_R_PPM_DEFAULT         = 300;     // frequency error threshold to leave DISCIPLINED
constexpr uint16_t PPS_UNLOCK_MAD_TICKS_DEFAULT     = 900;     // MAD residual ticks threshold to leave DISCIPLINED
constexpr uint8_t  PPS_LOCK_COUNT_MIN               = 1;       // minimum good-sample streak needed to lock
constexpr uint8_t  PPS_LOCK_COUNT_MAX               = 60;      // maximum accepted good-sample streak setting
constexpr uint8_t  PPS_LOCK_COUNT_DEFAULT           = 30;      // default good-sample streak needed to lock
constexpr uint8_t  PPS_UNLOCK_COUNT_DEFAULT         = 5;       // default bad-sample streak needed to unlock
constexpr uint16_t PPS_HOLDOVER_MS_DEFAULT          = 60000;   // HOLDOVER dwell before dropping to FREE_RUN
constexpr uint16_t PPS_STALE_MS_DEFAULT             = 2200;    // PPS freshness timeout for processed queued samples
constexpr uint16_t PPS_ISR_STALE_MS_DEFAULT         = 2200;    // PPS freshness timeout for ISR edge/counter activity
constexpr uint16_t PPS_CFG_REEMIT_DELAY_MS_DEFAULT  = 2000;    // delay before re-emitting PPS config after boot
constexpr uint16_t PPS_ACQUIRE_MIN_MS_DEFAULT       = 60000;   // minimum ACQUIRE dwell before DISCIPLINED

namespace Tunables {
  extern uint8_t   ppsFastShift;           // fast PPS EWMA shift
  extern uint8_t   ppsSlowShift;           // slow PPS EWMA shift
  extern uint16_t  ppsBlendLoPpm;          // lower drift threshold for slow-blend preference
  extern uint16_t  ppsBlendHiPpm;          // upper drift threshold for fast-blend preference
  extern uint16_t  ppsLockRppm;            // max frequency error to enter DISCIPLINED
  extern uint16_t  ppsLockMadTicks;        // max MAD residual ticks to enter DISCIPLINED
  extern uint16_t  ppsUnlockRppm;          // frequency error threshold to leave DISCIPLINED
  extern uint16_t  ppsUnlockMadTicks;      // MAD residual ticks threshold to leave DISCIPLINED
  extern uint8_t   ppsLockCount;           // consecutive good PPS samples required to lock
  extern uint8_t   ppsUnlockCount;         // consecutive bad PPS samples required to unlock
  extern uint16_t  ppsHoldoverMs;          // holdover dwell before dropping to FREE_RUN
  extern uint16_t  ppsStaleMs;             // PPS freshness timeout for processed queued PPS samples
  extern uint16_t  ppsIsrStaleMs;          // PPS freshness timeout for ISR edge/count changes
  extern uint16_t  ppsConfigReemitDelayMs; // startup delay before re-emitting PPS config
  extern uint16_t  ppsAcquireMinMs;        // minimum ACQUIRE dwell before DISCIPLINED

  uint8_t ppsFastShiftActive();
  uint8_t ppsSlowShiftActive();
  uint16_t ppsBlendLoPpmActive();
  uint16_t ppsBlendHiPpmActive();
  uint16_t ppsLockRppmActive();
  uint16_t ppsLockMadTicksActive();
  uint16_t ppsUnlockRppmActive();
  uint16_t ppsUnlockMadTicksActive();
  uint8_t ppsLockCountActive();
  uint8_t ppsUnlockCountActive();
  uint16_t ppsHoldoverMsActive();
  uint16_t ppsStaleMsActive();
  uint16_t ppsIsrStaleMsActive();
  uint16_t ppsConfigReemitDelayMsActive();
  uint16_t ppsAcquireMinMsActive();
  void normalizePpsTunables();
  void restoreDefaults();
}

struct TunableConfig {
  uint32_t  seq;

  uint16_t  ppsBlendLoPpm;
  uint16_t  ppsBlendHiPpm;
  uint16_t  ppsLockRppm;
  uint16_t  ppsLockMadTicks;     // persisted MAD residual ticks threshold for lock
  uint16_t  ppsUnlockRppm;
  uint16_t  ppsUnlockMadTicks;   // persisted MAD residual ticks threshold for unlock
  uint16_t  ppsHoldoverMs;
  uint16_t  ppsStaleMs;
  uint16_t  ppsIsrStaleMs;
  uint16_t  ppsConfigReemitDelayMs;
  uint16_t  ppsAcquireMinMs;
  uint16_t  crc16;

  uint8_t   ppsFastShift;
  uint8_t   ppsSlowShift;
  uint8_t   ppsLockCount;
  uint8_t   ppsUnlockCount;
};
