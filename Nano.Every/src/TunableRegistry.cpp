#include "TunableRegistry.h"

#include "PendulumCommands.h"
#include "PendulumProtocol.h"
#include "SerialParser.h"

#include <stdlib.h>
#include <string.h>

#include "StringCase.h"

namespace {

void printInvalidValue(const char* name, const char* detail);

bool parseUnsignedLong(const char* value, unsigned long& out) {
  if (!value || *value == '\0') return false;
  char* end = nullptr;
  out = strtoul(value, &end, 10);
  return end && *end == '\0';
}

bool parseUnsignedLongInRange(const char* value, unsigned long minValue, unsigned long maxValue, unsigned long& out) {
  if (!parseUnsignedLong(value, out)) return false;
  return out >= minValue && out <= maxValue;
}

bool assignUint8InRange(const char* value,
                        const char* paramName,
                        unsigned long minValue,
                        unsigned long maxValue,
                        const char* errorDetail,
                        uint8_t& out) {
  unsigned long parsed = 0;
  if (!parseUnsignedLongInRange(value, minValue, maxValue, parsed)) {
    printInvalidValue(paramName, errorDetail);
    return false;
  }
  out = (uint8_t)parsed;
  return true;
}

bool assignUint16InRange(const char* value,
                         const char* paramName,
                         unsigned long minValue,
                         unsigned long maxValue,
                         const char* errorDetail,
                         uint16_t& out) {
  unsigned long parsed = 0;
  if (!parseUnsignedLongInRange(value, minValue, maxValue, parsed)) {
    printInvalidValue(paramName, errorDetail);
    return false;
  }
  out = (uint16_t)parsed;
  return true;
}

void printInvalidValue(const char* name, const char* detail) {
  CMD_SERIAL.print(F("ERROR: "));
  CMD_SERIAL.print(name);
  CMD_SERIAL.print(F(": "));
  CMD_SERIAL.println(detail);
  sendStatus(StatusCode::InvalidValue, detail);
}

#define DEFINE_PRINT_TUNABLE_UINT(NAME, FIELD) \
  void NAME(Print& out) { out.print((unsigned)Tunables::FIELD); }

DEFINE_PRINT_TUNABLE_UINT(printUIntPpsFastShift, ppsFastShift)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsSlowShift, ppsSlowShift)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsBlendLoPpm, ppsBlendLoPpm)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsBlendHiPpm, ppsBlendHiPpm)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsLockRppm, ppsLockRppm)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsLockMadTicks, ppsLockMadTicks)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsUnlockRppm, ppsUnlockRppm)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsUnlockMadTicks, ppsUnlockMadTicks)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsLockCount, ppsLockCount)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsUnlockCount, ppsUnlockCount)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsHoldoverMs, ppsHoldoverMs)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsStaleMs, ppsStaleMs)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsIsrStaleMs, ppsIsrStaleMs)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsConfigReemitDelayMs, ppsConfigReemitDelayMs)
DEFINE_PRINT_TUNABLE_UINT(printUIntPpsAcquireMinMs, ppsAcquireMinMs)
void printUIntPpsMetrologyGraceMs(Print& out) { out.print((unsigned long)Tunables::ppsMetrologyGraceMs); }

#undef DEFINE_PRINT_TUNABLE_UINT

bool assignUint16InRangeAndMarkChanged(const char* value,
                                       const char* paramName,
                                       unsigned long minValue,
                                       unsigned long maxValue,
                                       const char* errorDetail,
                                       uint16_t& out,
                                       bool& tuningChanged) {
  if (!assignUint16InRange(value, paramName, minValue, maxValue, errorDetail, out)) {
    return false;
  }
  tuningChanged = true;
  return true;
}

bool assignUint32InRangeAndMarkChanged(const char* value,
                                       const char* paramName,
                                       unsigned long minValue,
                                       unsigned long maxValue,
                                       const char* errorDetail,
                                       uint32_t& out,
                                       bool& tuningChanged) {
  unsigned long parsed = 0;
  if (!parseUnsignedLongInRange(value, minValue, maxValue, parsed)) {
    printInvalidValue(paramName, errorDetail);
    return false;
  }
  out = (uint32_t)parsed;
  tuningChanged = true;
  return true;
}
bool setPpsFastShift(const char* value, bool&, bool&) {
  return assignUint8InRange(value,
                            PARAM_PPS_FAST_SHIFT,
                            0UL,
                            255UL,
                            "expected unsigned integer in range 0..255",
                            Tunables::ppsFastShift);
}

bool setPpsSlowShift(const char* value, bool&, bool&) {
  return assignUint8InRange(value,
                            PARAM_PPS_SLOW_SHIFT,
                            0UL,
                            255UL,
                            "expected unsigned integer in range 0..255",
                            Tunables::ppsSlowShift);
}

bool setPpsBlendLoPpm(const char* value, bool&, bool&) {
  return assignUint16InRange(value,
                             PARAM_PPS_BLEND_LO_PPM,
                             0UL,
                             65535UL,
                             "expected unsigned integer in range 0..65535",
                             Tunables::ppsBlendLoPpm);
}

bool setPpsBlendHiPpm(const char* value, bool&, bool&) {
  return assignUint16InRange(value,
                             PARAM_PPS_BLEND_HI_PPM,
                             0UL,
                             65535UL,
                             "expected unsigned integer in range 0..65535",
                             Tunables::ppsBlendHiPpm);
}

bool setPpsLockRppm(const char* value, bool& tuningChanged, bool&) {
  return assignUint16InRangeAndMarkChanged(value,
                                           PARAM_PPS_LOCK_R_PPM,
                                           0UL,
                                           65535UL,
                                           "expected unsigned integer in range 0..65535",
                                           Tunables::ppsLockRppm,
                                           tuningChanged);
}

bool setPpsLockMadTicks(const char* value, bool& tuningChanged, bool&) {
  return assignUint16InRangeAndMarkChanged(value,
                                           PARAM_PPS_LOCK_MAD_TICKS,
                                           0UL,
                                           65535UL,
                                           "expected unsigned integer in range 0..65535",
                                           Tunables::ppsLockMadTicks,
                                           tuningChanged);
}

bool setPpsUnlockRppm(const char* value, bool& tuningChanged, bool&) {
  return assignUint16InRangeAndMarkChanged(value,
                                           PARAM_PPS_UNLOCK_R_PPM,
                                           0UL,
                                           65535UL,
                                           "expected unsigned integer in range 0..65535",
                                           Tunables::ppsUnlockRppm,
                                           tuningChanged);
}

bool setPpsUnlockMadTicks(const char* value, bool& tuningChanged, bool&) {
  return assignUint16InRangeAndMarkChanged(value,
                                           PARAM_PPS_UNLOCK_MAD_TICKS,
                                           0UL,
                                           65535UL,
                                           "expected unsigned integer in range 0..65535",
                                           Tunables::ppsUnlockMadTicks,
                                           tuningChanged);
}

bool setPpsLockCount(const char* value, bool& tuningChanged, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_LOCK_COUNT, "expected unsigned integer");
    return false;
  }
  if (parsed < PPS_LOCK_COUNT_MIN || parsed > PPS_LOCK_COUNT_MAX) {
    printInvalidValue(PARAM_PPS_LOCK_COUNT, "must be in range 1..60");
    return false;
  }
  Tunables::ppsLockCount = (uint8_t)parsed;
  tuningChanged = true;
  return true;
}

bool setPpsUnlockCount(const char* value, bool& tuningChanged, bool&) {
  if (!assignUint8InRange(value,
                          PARAM_PPS_UNLOCK_COUNT,
                          0UL,
                          255UL,
                          "expected unsigned integer in range 0..255",
                          Tunables::ppsUnlockCount)) {
    return false;
  }
  tuningChanged = true;
  return true;
}

bool setPpsHoldoverMs(const char* value, bool& tuningChanged, bool&) {
  return assignUint16InRangeAndMarkChanged(value,
                                           PARAM_PPS_HOLDOVER_MS,
                                           0UL,
                                           65535UL,
                                           "expected unsigned integer in range 0..65535",
                                           Tunables::ppsHoldoverMs,
                                           tuningChanged);
}

bool setPpsStaleMs(const char* value, bool& tuningChanged, bool&) {
  return assignUint16InRangeAndMarkChanged(value,
                                           PARAM_PPS_STALE_MS,
                                           0UL,
                                           65535UL,
                                           "expected unsigned integer in range 0..65535",
                                           Tunables::ppsStaleMs,
                                           tuningChanged);
}

bool setPpsIsrStaleMs(const char* value, bool& tuningChanged, bool&) {
  return assignUint16InRangeAndMarkChanged(value,
                                           PARAM_PPS_ISR_STALE_MS,
                                           0UL,
                                           65535UL,
                                           "expected unsigned integer in range 0..65535",
                                           Tunables::ppsIsrStaleMs,
                                           tuningChanged);
}

bool setPpsConfigReemitDelayMs(const char* value, bool& tuningChanged, bool&) {
  return assignUint16InRangeAndMarkChanged(value,
                                           PARAM_PPS_CFG_REEMIT_DELAY_MS,
                                           0UL,
                                           65535UL,
                                           "expected unsigned integer in range 0..65535",
                                           Tunables::ppsConfigReemitDelayMs,
                                           tuningChanged);
}

bool setPpsAcquireMinMs(const char* value, bool& tuningChanged, bool&) {
  return assignUint16InRangeAndMarkChanged(value,
                                           PARAM_PPS_ACQUIRE_MIN_MS,
                                           0UL,
                                           65535UL,
                                           "expected unsigned integer in range 0..65535",
                                           Tunables::ppsAcquireMinMs,
                                           tuningChanged);
}

bool setPpsMetrologyGraceMs(const char* value, bool& tuningChanged, bool&) {
  return assignUint32InRangeAndMarkChanged(value,
                                           PARAM_PPS_METROLOGY_GRACE_MS,
                                           0UL,
                                           4294967295UL,
                                           "expected unsigned integer in range 0..4294967295",
                                           Tunables::ppsMetrologyGraceMs,
                                           tuningChanged);
}

#define DEFINE_WRITE_TUNABLE(NAME, FIELD) \
  void NAME(TunableConfig& cfg) { cfg.FIELD = Tunables::FIELD; }

DEFINE_WRITE_TUNABLE(writePpsFastShift, ppsFastShift)
DEFINE_WRITE_TUNABLE(writePpsSlowShift, ppsSlowShift)
DEFINE_WRITE_TUNABLE(writePpsBlendLoPpm, ppsBlendLoPpm)
DEFINE_WRITE_TUNABLE(writePpsBlendHiPpm, ppsBlendHiPpm)
DEFINE_WRITE_TUNABLE(writePpsLockRppm, ppsLockRppm)
DEFINE_WRITE_TUNABLE(writePpsLockMadTicks, ppsLockMadTicks)
DEFINE_WRITE_TUNABLE(writePpsUnlockRppm, ppsUnlockRppm)
DEFINE_WRITE_TUNABLE(writePpsUnlockMadTicks, ppsUnlockMadTicks)
DEFINE_WRITE_TUNABLE(writePpsLockCount, ppsLockCount)
DEFINE_WRITE_TUNABLE(writePpsUnlockCount, ppsUnlockCount)
DEFINE_WRITE_TUNABLE(writePpsHoldoverMs, ppsHoldoverMs)
DEFINE_WRITE_TUNABLE(writePpsStaleMs, ppsStaleMs)
DEFINE_WRITE_TUNABLE(writePpsIsrStaleMs, ppsIsrStaleMs)
DEFINE_WRITE_TUNABLE(writePpsConfigReemitDelayMs, ppsConfigReemitDelayMs)
DEFINE_WRITE_TUNABLE(writePpsAcquireMinMs, ppsAcquireMinMs)
DEFINE_WRITE_TUNABLE(writePpsMetrologyGraceMs, ppsMetrologyGraceMs)

#undef DEFINE_WRITE_TUNABLE

#define DEFINE_APPLY_TUNABLE(NAME, FIELD) \
  void NAME(const TunableConfig& cfg) { Tunables::FIELD = cfg.FIELD; }

DEFINE_APPLY_TUNABLE(applyPpsFastShift, ppsFastShift)
DEFINE_APPLY_TUNABLE(applyPpsSlowShift, ppsSlowShift)
DEFINE_APPLY_TUNABLE(applyPpsBlendLoPpm, ppsBlendLoPpm)
DEFINE_APPLY_TUNABLE(applyPpsBlendHiPpm, ppsBlendHiPpm)
DEFINE_APPLY_TUNABLE(applyPpsLockRppm, ppsLockRppm)
DEFINE_APPLY_TUNABLE(applyPpsLockMadTicks, ppsLockMadTicks)
DEFINE_APPLY_TUNABLE(applyPpsUnlockRppm, ppsUnlockRppm)
DEFINE_APPLY_TUNABLE(applyPpsUnlockMadTicks, ppsUnlockMadTicks)
void applyPpsLockCount(const TunableConfig& cfg) {
  uint8_t lockCount = cfg.ppsLockCount;
  if (lockCount < PPS_LOCK_COUNT_MIN) lockCount = PPS_LOCK_COUNT_MIN;
  if (lockCount > PPS_LOCK_COUNT_MAX) lockCount = PPS_LOCK_COUNT_MAX;
  Tunables::ppsLockCount = lockCount;
}
DEFINE_APPLY_TUNABLE(applyPpsUnlockCount, ppsUnlockCount)
DEFINE_APPLY_TUNABLE(applyPpsHoldoverMs, ppsHoldoverMs)
DEFINE_APPLY_TUNABLE(applyPpsStaleMs, ppsStaleMs)
DEFINE_APPLY_TUNABLE(applyPpsIsrStaleMs, ppsIsrStaleMs)
DEFINE_APPLY_TUNABLE(applyPpsConfigReemitDelayMs, ppsConfigReemitDelayMs)
DEFINE_APPLY_TUNABLE(applyPpsAcquireMinMs, ppsAcquireMinMs)
DEFINE_APPLY_TUNABLE(applyPpsMetrologyGraceMs, ppsMetrologyGraceMs)

#undef DEFINE_APPLY_TUNABLE

const TunableDescriptor REGISTRY[] = {
  { PARAM_PPS_FAST_SHIFT, TunableCliType::Unsigned, nullptr, nullptr, "EWMA fast shift", printUIntPpsFastShift, setPpsFastShift, writePpsFastShift, applyPpsFastShift },
  { PARAM_PPS_SLOW_SHIFT, TunableCliType::Unsigned, nullptr, nullptr, "EWMA slow shift", printUIntPpsSlowShift, setPpsSlowShift, writePpsSlowShift, applyPpsSlowShift },
  { PARAM_PPS_BLEND_LO_PPM, TunableCliType::Unsigned, nullptr, nullptr, "slow/fast blend low", printUIntPpsBlendLoPpm, setPpsBlendLoPpm, writePpsBlendLoPpm, applyPpsBlendLoPpm },
  { PARAM_PPS_BLEND_HI_PPM, TunableCliType::Unsigned, nullptr, nullptr, "slow/fast blend high", printUIntPpsBlendHiPpm, setPpsBlendHiPpm, writePpsBlendHiPpm, applyPpsBlendHiPpm },
  { PARAM_PPS_LOCK_R_PPM, TunableCliType::Unsigned, nullptr, nullptr, "lock drift threshold", printUIntPpsLockRppm, setPpsLockRppm, writePpsLockRppm, applyPpsLockRppm },
  { PARAM_PPS_LOCK_MAD_TICKS, TunableCliType::Unsigned, nullptr, nullptr, "lock MAD threshold", printUIntPpsLockMadTicks, setPpsLockMadTicks, writePpsLockMadTicks, applyPpsLockMadTicks },
  { PARAM_PPS_LOCK_COUNT, TunableCliType::Unsigned, "range 1..60", nullptr, "lock streak", printUIntPpsLockCount, setPpsLockCount, writePpsLockCount, applyPpsLockCount },
  { PARAM_PPS_UNLOCK_R_PPM, TunableCliType::Unsigned, nullptr, nullptr, "unlock drift threshold", printUIntPpsUnlockRppm, setPpsUnlockRppm, writePpsUnlockRppm, applyPpsUnlockRppm },
  { PARAM_PPS_UNLOCK_MAD_TICKS, TunableCliType::Unsigned, nullptr, nullptr, "unlock MAD threshold", printUIntPpsUnlockMadTicks, setPpsUnlockMadTicks, writePpsUnlockMadTicks, applyPpsUnlockMadTicks },
  { PARAM_PPS_UNLOCK_COUNT, TunableCliType::Unsigned, nullptr, nullptr, "unlock streak", printUIntPpsUnlockCount, setPpsUnlockCount, writePpsUnlockCount, applyPpsUnlockCount },
  { PARAM_PPS_HOLDOVER_MS, TunableCliType::Unsigned, nullptr, nullptr, "holdover ms", printUIntPpsHoldoverMs, setPpsHoldoverMs, writePpsHoldoverMs, applyPpsHoldoverMs },
  { PARAM_PPS_STALE_MS, TunableCliType::Unsigned, nullptr, nullptr, "main-loop stale ms", printUIntPpsStaleMs, setPpsStaleMs, writePpsStaleMs, applyPpsStaleMs },
  { PARAM_PPS_ISR_STALE_MS, TunableCliType::Unsigned, nullptr, nullptr, "ISR stale ms", printUIntPpsIsrStaleMs, setPpsIsrStaleMs, writePpsIsrStaleMs, applyPpsIsrStaleMs },
  { PARAM_PPS_CFG_REEMIT_DELAY_MS, TunableCliType::Unsigned, nullptr, nullptr, "cfg reemit delay ms", printUIntPpsConfigReemitDelayMs, setPpsConfigReemitDelayMs, writePpsConfigReemitDelayMs, applyPpsConfigReemitDelayMs },
  { PARAM_PPS_ACQUIRE_MIN_MS, TunableCliType::Unsigned, nullptr, nullptr, "acquire min ms", printUIntPpsAcquireMinMs, setPpsAcquireMinMs, writePpsAcquireMinMs, applyPpsAcquireMinMs },
  { PARAM_PPS_METROLOGY_GRACE_MS, TunableCliType::Unsigned, nullptr, nullptr, "metrology grace ms", printUIntPpsMetrologyGraceMs, setPpsMetrologyGraceMs, writePpsMetrologyGraceMs, applyPpsMetrologyGraceMs },
};

} // namespace

const TunableDescriptor* tunableRegistryBegin() {
  return &REGISTRY[0];
}

const TunableDescriptor* tunableRegistryEnd() {
  return &REGISTRY[0] + (sizeof(REGISTRY) / sizeof(REGISTRY[0]));
}

size_t tunableRegistryCount() {
  return sizeof(REGISTRY) / sizeof(REGISTRY[0]);
}

const TunableDescriptor* findTunableDescriptor(const char* cliName) {
  if (!cliName) return nullptr;
  for (const TunableDescriptor* it = tunableRegistryBegin(); it != tunableRegistryEnd(); ++it) {
    if (equalsIgnoreCaseAscii(cliName, it->cliName)) return it;
  }
  return nullptr;
}

const char* tunableTypeName(TunableCliType type) {
  switch (type) {
    case TunableCliType::Unsigned: return "uint";
    case TunableCliType::Enum:     return "enum";
    default:                       return "?";
  }
}
