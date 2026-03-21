#include "Config.h"
#include "PendulumProtocol.h"

namespace Tunables {
  float     correctionJumpThresh = CORRECTION_JUMP_THRESHOLD; // compatibility-only (no-op in live discipliner path)

  // Back-compat alias to slow
  uint8_t   ppsEmaShift          = PPS_SLOW_SHIFT_DEFAULT;

  // New:
  uint8_t   ppsFastShift         = PPS_FAST_SHIFT_DEFAULT;
  uint8_t   ppsSlowShift         = PPS_SLOW_SHIFT_DEFAULT;
  uint8_t   ppsHampelWin         = PPS_HAMPEL_WIN_DEFAULT;   // compatibility-only (no-op); retained for CLI/EEPROM/status round-trip
  uint16_t  ppsHampelKx100       = PPS_HAMPEL_KX100_DEFAULT; // compatibility-only (no-op); retained for CLI/EEPROM/status round-trip
  bool      ppsMedian3           = PPS_MEDIAN3_DEFAULT;      // compatibility-only (no-op); retained for CLI/EEPROM/status round-trip
  uint16_t  ppsBlendLoPpm        = PPS_BLEND_LO_PPM_DEFAULT;
  uint16_t  ppsBlendHiPpm        = PPS_BLEND_HI_PPM_DEFAULT;
  uint16_t  ppsLockRppm          = PPS_LOCK_R_PPM_DEFAULT;
  uint16_t  ppsLockJppm          = PPS_LOCK_J_PPM_DEFAULT;
  uint16_t  ppsUnlockRppm        = PPS_UNLOCK_R_PPM_DEFAULT;
  uint16_t  ppsUnlockJppm        = PPS_UNLOCK_J_PPM_DEFAULT;
  uint8_t   ppsLockCount         = PPS_LOCK_COUNT_DEFAULT;
  uint8_t   ppsUnlockCount       = PPS_UNLOCK_COUNT_DEFAULT;
  uint16_t  ppsHoldoverMs        = PPS_HOLDOVER_MS_DEFAULT;
  uint16_t  ppsStaleMs           = PPS_STALE_MS_DEFAULT;
  uint16_t  ppsIsrStaleMs        = PPS_ISR_STALE_MS_DEFAULT;
  uint16_t  ppsConfigReemitDelayMs = PPS_CFG_REEMIT_DELAY_MS_DEFAULT;
  uint16_t  ppsAcquireMinMs      = PPS_ACQUIRE_MIN_MS_DEFAULT;

  DataUnits dataUnits            = DATA_UNITS_DEFAULT;

  uint8_t ppsFastShiftActive() {
    const uint8_t v = ppsFastShift;
    return (v < PPS_SHIFT_MIN) ? PPS_SHIFT_MIN : (v > PPS_SHIFT_MAX ? PPS_SHIFT_MAX : v);
  }

  uint8_t ppsSlowShiftActive() {
    const uint8_t v = ppsSlowShift;
    return (v < PPS_SHIFT_MIN) ? PPS_SHIFT_MIN : (v > PPS_SHIFT_MAX ? PPS_SHIFT_MAX : v);
  }

  uint16_t ppsBlendLoPpmActive() {
    return ppsBlendLoPpm;
  }

  uint16_t ppsBlendHiPpmActive() {
    return (ppsBlendHiPpm <= ppsBlendLoPpm) ? (uint16_t)(ppsBlendLoPpm + 1U) : ppsBlendHiPpm;
  }

  uint16_t ppsLockRppmActive() {
    return ppsLockRppm;
  }

  uint16_t ppsLockJppmActive() {
    return ppsLockJppm;
  }

  uint16_t ppsUnlockRppmActive() {
    return ppsUnlockRppm;
  }

  uint16_t ppsUnlockJppmActive() {
    return ppsUnlockJppm;
  }

  uint8_t ppsLockCountActive() {
    const uint8_t v = ppsLockCount;
    return (v < PPS_LOCK_COUNT_MIN) ? PPS_LOCK_COUNT_MIN : (v > PPS_LOCK_COUNT_MAX ? PPS_LOCK_COUNT_MAX : v);
  }

  uint8_t ppsUnlockCountActive() {
    return (ppsUnlockCount == 0U) ? 1U : ppsUnlockCount;
  }

  uint16_t ppsHoldoverMsActive() {
    return (ppsHoldoverMs == 0U) ? 1U : ppsHoldoverMs;
  }

  uint16_t ppsStaleMsActive() {
    return (ppsStaleMs == 0U) ? 1U : ppsStaleMs;
  }

  uint16_t ppsIsrStaleMsActive() {
    return (ppsIsrStaleMs == 0U) ? 1U : ppsIsrStaleMs;
  }

  uint16_t ppsConfigReemitDelayMsActive() {
    return ppsConfigReemitDelayMs;
  }

  uint16_t ppsAcquireMinMsActive() {
    return (ppsAcquireMinMs == 0U) ? 1U : ppsAcquireMinMs;
  }

  void normalizePpsTunables() {
    ppsFastShift = ppsFastShiftActive();
    ppsSlowShift = ppsSlowShiftActive();
    ppsEmaShift = ppsSlowShift; // deprecated alias must mirror live slow shift
    ppsBlendHiPpm = ppsBlendHiPpmActive();
    ppsLockCount = ppsLockCountActive();
    ppsUnlockCount = ppsUnlockCountActive();
    ppsHoldoverMs = ppsHoldoverMsActive();
    ppsStaleMs = ppsStaleMsActive();
    ppsIsrStaleMs = ppsIsrStaleMsActive();
    ppsAcquireMinMs = ppsAcquireMinMsActive();
  }
}
