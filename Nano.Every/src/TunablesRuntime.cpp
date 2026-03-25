#include "Config.h"
#include "PendulumProtocol.h"

namespace Tunables {
  uint8_t   ppsFastShift         = PPS_FAST_SHIFT_DEFAULT;
  uint8_t   ppsSlowShift         = PPS_SLOW_SHIFT_DEFAULT;
  uint16_t  ppsBlendLoPpm        = PPS_BLEND_LO_PPM_DEFAULT;
  uint16_t  ppsBlendHiPpm        = PPS_BLEND_HI_PPM_DEFAULT;
  uint16_t  ppsLockRppm          = PPS_LOCK_R_PPM_DEFAULT;
  uint16_t  ppsLockMadTicks      = PPS_LOCK_MAD_TICKS_DEFAULT;
  uint16_t  ppsUnlockRppm        = PPS_UNLOCK_R_PPM_DEFAULT;
  uint16_t  ppsUnlockMadTicks    = PPS_UNLOCK_MAD_TICKS_DEFAULT;
  uint8_t   ppsLockCount         = PPS_LOCK_COUNT_DEFAULT;
  uint8_t   ppsUnlockCount       = PPS_UNLOCK_COUNT_DEFAULT;
  uint16_t  ppsHoldoverMs        = PPS_HOLDOVER_MS_DEFAULT;
  uint16_t  ppsStaleMs           = PPS_STALE_MS_DEFAULT;
  uint16_t  ppsIsrStaleMs        = PPS_ISR_STALE_MS_DEFAULT;
  uint16_t  ppsConfigReemitDelayMs = PPS_CFG_REEMIT_DELAY_MS_DEFAULT;
  uint16_t  ppsAcquireMinMs      = PPS_ACQUIRE_MIN_MS_DEFAULT;


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

  uint16_t ppsLockMadTicksActive() {
    return ppsLockMadTicks;
  }

  uint16_t ppsUnlockRppmActive() {
    return ppsUnlockRppm;
  }

  uint16_t ppsUnlockMadTicksActive() {
    return ppsUnlockMadTicks;
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
    ppsBlendHiPpm = ppsBlendHiPpmActive();
    ppsLockCount = ppsLockCountActive();
    ppsUnlockCount = ppsUnlockCountActive();
    ppsHoldoverMs = ppsHoldoverMsActive();
    ppsStaleMs = ppsStaleMsActive();
    ppsIsrStaleMs = ppsIsrStaleMsActive();
    ppsAcquireMinMs = ppsAcquireMinMsActive();
  }

  void restoreDefaults() {
    ppsFastShift = PPS_FAST_SHIFT_DEFAULT;
    ppsSlowShift = PPS_SLOW_SHIFT_DEFAULT;
    ppsBlendLoPpm = PPS_BLEND_LO_PPM_DEFAULT;
    ppsBlendHiPpm = PPS_BLEND_HI_PPM_DEFAULT;
    ppsLockRppm = PPS_LOCK_R_PPM_DEFAULT;
    ppsLockMadTicks = PPS_LOCK_MAD_TICKS_DEFAULT;
    ppsUnlockRppm = PPS_UNLOCK_R_PPM_DEFAULT;
    ppsUnlockMadTicks = PPS_UNLOCK_MAD_TICKS_DEFAULT;
    ppsLockCount = PPS_LOCK_COUNT_DEFAULT;
    ppsUnlockCount = PPS_UNLOCK_COUNT_DEFAULT;
    ppsHoldoverMs = PPS_HOLDOVER_MS_DEFAULT;
    ppsStaleMs = PPS_STALE_MS_DEFAULT;
    ppsIsrStaleMs = PPS_ISR_STALE_MS_DEFAULT;
    ppsConfigReemitDelayMs = PPS_CFG_REEMIT_DELAY_MS_DEFAULT;
    ppsAcquireMinMs = PPS_ACQUIRE_MIN_MS_DEFAULT;
    normalizePpsTunables();
  }
}
