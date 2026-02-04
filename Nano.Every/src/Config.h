#pragma once

#include <Arduino.h>

const int ledPin = LED_BUILTIN;

enum class DataUnits : uint8_t;

// Some ATmega4809 headers don’t define the usual “_gc” aliases or the TCB overflow mask,
// so we provide them here to keep our code portable and consistent:
//  • TCB_OVF_bm: bit-mask for TCB0 overflow flag (bit 1 in INTFLAGS).  
#ifndef TCB_OVF_bm
  #define TCB_OVF_bm (1<<1)
#endif

constexpr uint8_t  RING_SIZE_IR_SENSOR       = 64;      // default ring size for IR sensor readings
constexpr uint8_t  RING_SIZE_PPS             = 16;      // default ring size for GPS PPS interrupts
constexpr float    CORRECTION_JUMP_THRESHOLD = 0.002f;  // >2000 ppm deviation (empirically determined)
constexpr uint8_t  PPS_EMA_SHIFT_DEFAULT     = 6;       // EMA shift default for PPS correction
constexpr uint8_t  FLAG_PPS_TRIGGERED        = 0;       // whether PPS ISR has triggered
constexpr uint8_t  PPS_FAST_SHIFT_DEFAULT    = 3;       // α ≈ 1/8  (~8 s)
constexpr uint8_t  PPS_SLOW_SHIFT_DEFAULT    = 8;       // α ≈ 1/256 (~4.3 min)
constexpr uint8_t  PPS_HAMPEL_WIN_DEFAULT    = 7;       // must be odd (5..9 recommended)
constexpr uint16_t PPS_HAMPEL_KX100_DEFAULT  = 300;     // k=3.00 → 3*scaled MAD
constexpr bool     PPS_MEDIAN3_DEFAULT       = true;    // enable median-of-3 after Hampel
constexpr uint16_t PPS_BLEND_LO_PPM_DEFAULT  = 5;        // prefer slow below this R
constexpr uint16_t PPS_BLEND_HI_PPM_DEFAULT  = 200;      // fully fast above this R
constexpr uint16_t PPS_LOCK_R_PPM_DEFAULT    = 50;       // R threshold to declare LOCKED
constexpr uint16_t PPS_LOCK_J_PPM_DEFAULT    = 20;       // Jitter threshold to declare LOCKED
constexpr uint16_t PPS_UNLOCK_R_PPM_DEFAULT  = 200;      // R to drop lock
constexpr uint16_t PPS_UNLOCK_J_PPM_DEFAULT  = 100;      // Jitter to drop lock
constexpr uint8_t  PPS_LOCK_STABLE_COUNT     = 10;       // consecutive pulses to lock
constexpr uint8_t  PPS_UNLOCK_COUNT_DEFAULT  = 3;        // consecutive pulses to unlock
constexpr uint16_t PPS_HOLDOVER_MS_DEFAULT   = 1500;     // miss-PPS threshold


#ifndef PROTECT_SHARED_READS
#define PROTECT_SHARED_READS 1 // atomic shared-read guard
#endif

namespace Tunables {
  extern float     correctionJumpThresh; // ns diff to trigger step correction
  // DEPRECATED: kept for back-compat; aliased to ppsSlowShift
  extern uint8_t   ppsEmaShift;           // slow PPS EWMA shift (legacy alias)

  // New:
  extern uint8_t   ppsFastShift;          // fast PPS EWMA shift
  extern uint8_t   ppsSlowShift;          // slow PPS EWMA shift
  extern uint8_t   ppsHampelWin;          // Hampel filter window size (odd)
  extern uint16_t  ppsHampelKx100;        // Hampel filter threshold (×100 MAD)
  extern bool      ppsMedian3;            // apply median-of-3 after Hampel
  extern uint16_t  ppsBlendLoPpm;         // drift threshold for full slow blend
  extern uint16_t  ppsBlendHiPpm;         // drift threshold for full fast blend
  extern uint16_t  ppsLockRppm;           // max drift to declare LOCKED
  extern uint16_t  ppsLockJppm;           // max jitter to declare LOCKED
  extern uint16_t  ppsUnlockRppm;         // drift to unlock from LOCKED
  extern uint16_t  ppsUnlockJppm;         // jitter to unlock from LOCKED
  extern uint8_t   ppsUnlockCount;        // consecutive bad PPS to unlock
  extern uint16_t  ppsHoldoverMs;         // PPS gap to enter HOLDOVER

  extern DataUnits dataUnits;             // output units (ticks/us/ns)
}

struct TunableConfig {
  float     correctionJumpThresh;
  uint8_t   ppsEmaShift;
  uint8_t   dataUnits;

  // New:
  uint8_t   ppsFastShift;
  uint8_t   ppsSlowShift;
  uint8_t   ppsHampelWin;
  uint16_t  ppsHampelKx100;
  uint8_t   ppsMedian3;
  uint16_t  ppsBlendLoPpm;
  uint16_t  ppsBlendHiPpm;
  uint16_t  ppsLockRppm;
  uint16_t  ppsLockJppm;
  uint16_t  ppsUnlockRppm;
  uint16_t  ppsUnlockJppm;
  uint8_t   ppsUnlockCount;
  uint16_t  ppsHoldoverMs;

  uint32_t  seq;
  uint16_t  crc16;
};
