#include "TunableRegistry.h"

#include "PendulumProtocol.h"
#include "SerialParser.h"

#include <stdlib.h>
#include <string.h>

#include "StringCase.h"

namespace {

bool parseUnsignedLong(const char* value, unsigned long& out) {
  if (!value || *value == '\0') return false;
  char* end = nullptr;
  out = strtoul(value, &end, 10);
  return end && *end == '\0';
}

bool parseFloatValue(const char* value, float& out) {
  if (!value || *value == '\0') return false;
  char* end = nullptr;
  out = static_cast<float>(strtod(value, &end));
  return end && *end == '\0';
}

void printInvalidValue(const char* name, const char* detail) {
  CMD_SERIAL.print(F("ERROR: "));
  CMD_SERIAL.print(name);
  CMD_SERIAL.print(F(": "));
  CMD_SERIAL.println(detail);
  sendStatus(StatusCode::InvalidValue, detail);
}

void printFloatCorrectionJump(Print& out) { out.print(Tunables::correctionJumpThresh, 6); }
void printUIntPpsEmaShift(Print& out) { out.print((unsigned)Tunables::ppsEmaShift); }
void printUIntPpsFastShift(Print& out) { out.print((unsigned)Tunables::ppsFastShift); }
void printUIntPpsSlowShift(Print& out) { out.print((unsigned)Tunables::ppsSlowShift); }
void printUIntPpsHampelWin(Print& out) { out.print((unsigned)Tunables::ppsHampelWin); }
void printUIntPpsHampelKx100(Print& out) { out.print((unsigned)Tunables::ppsHampelKx100); }
void printUIntPpsMedian3(Print& out) { out.print(Tunables::ppsMedian3 ? 1U : 0U); }
void printUIntPpsBlendLoPpm(Print& out) { out.print((unsigned)Tunables::ppsBlendLoPpm); }
void printUIntPpsBlendHiPpm(Print& out) { out.print((unsigned)Tunables::ppsBlendHiPpm); }
void printUIntPpsLockRppm(Print& out) { out.print((unsigned)Tunables::ppsLockRppm); }
void printUIntPpsLockJppm(Print& out) { out.print((unsigned)Tunables::ppsLockJppm); }
void printUIntPpsUnlockRppm(Print& out) { out.print((unsigned)Tunables::ppsUnlockRppm); }
void printUIntPpsUnlockJppm(Print& out) { out.print((unsigned)Tunables::ppsUnlockJppm); }
void printUIntPpsLockCount(Print& out) { out.print((unsigned)Tunables::ppsLockCount); }
void printUIntPpsUnlockCount(Print& out) { out.print((unsigned)Tunables::ppsUnlockCount); }
void printUIntPpsHoldoverMs(Print& out) { out.print((unsigned)Tunables::ppsHoldoverMs); }
void printUIntPpsStaleMs(Print& out) { out.print((unsigned)Tunables::ppsStaleMs); }
void printUIntPpsIsrStaleMs(Print& out) { out.print((unsigned)Tunables::ppsIsrStaleMs); }
void printUIntPpsConfigReemitDelayMs(Print& out) { out.print((unsigned)Tunables::ppsConfigReemitDelayMs); }
void printUIntPpsAcquireMinMs(Print& out) { out.print((unsigned)Tunables::ppsAcquireMinMs); }
void printDataUnits(Print& out) {
  const char* name = dataUnitsName(Tunables::dataUnits);
  out.print(name ? name : "?");
}

bool setCorrectionJumpThresh(const char* value, bool&, bool&) {
  float parsed = 0.0f;
  if (!parseFloatValue(value, parsed)) {
    printInvalidValue(PARAM_CORR_JUMP, "expected floating-point value");
    return false;
  }
  Tunables::correctionJumpThresh = parsed;
  return true;
}

bool setPpsEmaShift(const char* value, bool&, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_EMA_SHIFT, "expected unsigned integer");
    return false;
  }
  Tunables::ppsEmaShift = (uint8_t)parsed;
  Tunables::ppsSlowShift = Tunables::ppsEmaShift;
  return true;
}

bool setPpsFastShift(const char* value, bool&, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_FAST_SHIFT, "expected unsigned integer");
    return false;
  }
  Tunables::ppsFastShift = (uint8_t)parsed;
  return true;
}

bool setPpsSlowShift(const char* value, bool&, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_SLOW_SHIFT, "expected unsigned integer");
    return false;
  }
  Tunables::ppsSlowShift = (uint8_t)parsed;
  Tunables::ppsEmaShift = Tunables::ppsSlowShift;
  return true;
}

bool setPpsHampelWin(const char* value, bool&, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_HAMPEL_WIN, "expected unsigned integer");
    return false;
  }
  Tunables::ppsHampelWin = (uint8_t)parsed;
  return true;
}

bool setPpsHampelKx100(const char* value, bool&, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_HAMPEL_KX100, "expected unsigned integer");
    return false;
  }
  Tunables::ppsHampelKx100 = (uint16_t)parsed;
  return true;
}

bool setPpsMedian3(const char* value, bool&, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_MEDIAN3, "expected 0 or 1");
    return false;
  }
  if (parsed > 1UL) {
    printInvalidValue(PARAM_PPS_MEDIAN3, "must be 0 or 1");
    return false;
  }
  Tunables::ppsMedian3 = (parsed != 0UL);
  return true;
}

bool setPpsBlendLoPpm(const char* value, bool&, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_BLEND_LO_PPM, "expected unsigned integer");
    return false;
  }
  Tunables::ppsBlendLoPpm = (uint16_t)parsed;
  return true;
}

bool setPpsBlendHiPpm(const char* value, bool&, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_BLEND_HI_PPM, "expected unsigned integer");
    return false;
  }
  Tunables::ppsBlendHiPpm = (uint16_t)parsed;
  return true;
}

bool setPpsLockRppm(const char* value, bool& tuningChanged, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_LOCK_R_PPM, "expected unsigned integer");
    return false;
  }
  Tunables::ppsLockRppm = (uint16_t)parsed;
  tuningChanged = true;
  return true;
}

bool setPpsLockJppm(const char* value, bool& tuningChanged, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_LOCK_J_PPM, "expected unsigned integer");
    return false;
  }
  Tunables::ppsLockJppm = (uint16_t)parsed;
  tuningChanged = true;
  return true;
}

bool setPpsUnlockRppm(const char* value, bool& tuningChanged, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_UNLOCK_R_PPM, "expected unsigned integer");
    return false;
  }
  Tunables::ppsUnlockRppm = (uint16_t)parsed;
  tuningChanged = true;
  return true;
}

bool setPpsUnlockJppm(const char* value, bool& tuningChanged, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_UNLOCK_J_PPM, "expected unsigned integer");
    return false;
  }
  Tunables::ppsUnlockJppm = (uint16_t)parsed;
  tuningChanged = true;
  return true;
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
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_UNLOCK_COUNT, "expected unsigned integer");
    return false;
  }
  Tunables::ppsUnlockCount = (uint8_t)parsed;
  tuningChanged = true;
  return true;
}

bool setPpsHoldoverMs(const char* value, bool& tuningChanged, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_HOLDOVER_MS, "expected unsigned integer");
    return false;
  }
  Tunables::ppsHoldoverMs = (uint16_t)parsed;
  tuningChanged = true;
  return true;
}

bool setPpsStaleMs(const char* value, bool& tuningChanged, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_STALE_MS, "expected unsigned integer");
    return false;
  }
  Tunables::ppsStaleMs = (uint16_t)parsed;
  tuningChanged = true;
  return true;
}

bool setPpsIsrStaleMs(const char* value, bool& tuningChanged, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_ISR_STALE_MS, "expected unsigned integer");
    return false;
  }
  Tunables::ppsIsrStaleMs = (uint16_t)parsed;
  tuningChanged = true;
  return true;
}

bool setPpsConfigReemitDelayMs(const char* value, bool& tuningChanged, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_CFG_REEMIT_DELAY_MS, "expected unsigned integer");
    return false;
  }
  Tunables::ppsConfigReemitDelayMs = (uint16_t)parsed;
  tuningChanged = true;
  return true;
}

bool setPpsAcquireMinMs(const char* value, bool& tuningChanged, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_ACQUIRE_MIN_MS, "expected unsigned integer");
    return false;
  }
  Tunables::ppsAcquireMinMs = (uint16_t)parsed;
  tuningChanged = true;
  return true;
}

bool setDataUnits(const char* value, bool&, bool& headerPending) {
  DataUnits parsed;
  if (!tryParseDataUnits(value, parsed)) {
    printInvalidValue(PARAM_DATA_UNITS, "must be raw_cycles, adjusted_ms, adjusted_us, or adjusted_ns");
    return false;
  }
  Tunables::dataUnits = parsed;
  headerPending = true;
  return true;
}

void writeCorrectionJumpThresh(TunableConfig& cfg) { cfg.correctionJumpThresh = Tunables::correctionJumpThresh; }
void writePpsEmaShift(TunableConfig& cfg) { cfg.ppsEmaShift = Tunables::ppsSlowShift; }
void writeDataUnits(TunableConfig& cfg) { cfg.dataUnits = static_cast<uint8_t>(Tunables::dataUnits); }
void writePpsFastShift(TunableConfig& cfg) { cfg.ppsFastShift = Tunables::ppsFastShift; }
void writePpsSlowShift(TunableConfig& cfg) { cfg.ppsSlowShift = Tunables::ppsSlowShift; }
void writePpsHampelWin(TunableConfig& cfg) { cfg.ppsHampelWin = Tunables::ppsHampelWin; }
void writePpsHampelKx100(TunableConfig& cfg) { cfg.ppsHampelKx100 = Tunables::ppsHampelKx100; }
void writePpsMedian3(TunableConfig& cfg) { cfg.ppsMedian3 = Tunables::ppsMedian3 ? 1U : 0U; }
void writePpsBlendLoPpm(TunableConfig& cfg) { cfg.ppsBlendLoPpm = Tunables::ppsBlendLoPpm; }
void writePpsBlendHiPpm(TunableConfig& cfg) { cfg.ppsBlendHiPpm = Tunables::ppsBlendHiPpm; }
void writePpsLockRppm(TunableConfig& cfg) { cfg.ppsLockRppm = Tunables::ppsLockRppm; }
void writePpsLockJppm(TunableConfig& cfg) { cfg.ppsLockJppm = Tunables::ppsLockJppm; }
void writePpsUnlockRppm(TunableConfig& cfg) { cfg.ppsUnlockRppm = Tunables::ppsUnlockRppm; }
void writePpsUnlockJppm(TunableConfig& cfg) { cfg.ppsUnlockJppm = Tunables::ppsUnlockJppm; }
void writePpsLockCount(TunableConfig& cfg) { cfg.ppsLockCount = Tunables::ppsLockCount; }
void writePpsUnlockCount(TunableConfig& cfg) { cfg.ppsUnlockCount = Tunables::ppsUnlockCount; }
void writePpsHoldoverMs(TunableConfig& cfg) { cfg.ppsHoldoverMs = Tunables::ppsHoldoverMs; }
void writePpsStaleMs(TunableConfig& cfg) { cfg.ppsStaleMs = Tunables::ppsStaleMs; }
void writePpsIsrStaleMs(TunableConfig& cfg) { cfg.ppsIsrStaleMs = Tunables::ppsIsrStaleMs; }
void writePpsConfigReemitDelayMs(TunableConfig& cfg) { cfg.ppsConfigReemitDelayMs = Tunables::ppsConfigReemitDelayMs; }
void writePpsAcquireMinMs(TunableConfig& cfg) { cfg.ppsAcquireMinMs = Tunables::ppsAcquireMinMs; }

void applyCorrectionJumpThresh(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::correctionJumpThresh = cfg.correctionJumpThresh; }
void applyPpsEmaShift(const TunableConfig& cfg, const TunableConfigLoadContext& ctx) { Tunables::ppsEmaShift = ctx.useLegacyPpsEmaShift ? cfg.ppsEmaShift : cfg.ppsSlowShift; }
void applyDataUnits(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::dataUnits = static_cast<DataUnits>(cfg.dataUnits); }
void applyPpsFastShift(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::ppsFastShift = cfg.ppsFastShift ? cfg.ppsFastShift : PPS_FAST_SHIFT_DEFAULT; }
void applyPpsSlowShift(const TunableConfig& cfg, const TunableConfigLoadContext& ctx) { Tunables::ppsSlowShift = ctx.useLegacyPpsEmaShift ? cfg.ppsEmaShift : cfg.ppsSlowShift; }
void applyPpsHampelWin(const TunableConfig& cfg, const TunableConfigLoadContext&) {
  Tunables::ppsHampelWin = (cfg.ppsHampelWin >= 5U && (cfg.ppsHampelWin & 1U)) ? cfg.ppsHampelWin : PPS_HAMPEL_WIN_DEFAULT;
}
void applyPpsHampelKx100(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::ppsHampelKx100 = cfg.ppsHampelKx100 ? cfg.ppsHampelKx100 : PPS_HAMPEL_KX100_DEFAULT; }
void applyPpsMedian3(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::ppsMedian3 = (cfg.ppsMedian3 != 0U); }
void applyPpsBlendLoPpm(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::ppsBlendLoPpm = cfg.ppsBlendLoPpm ? cfg.ppsBlendLoPpm : PPS_BLEND_LO_PPM_DEFAULT; }
void applyPpsBlendHiPpm(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::ppsBlendHiPpm = cfg.ppsBlendHiPpm ? cfg.ppsBlendHiPpm : PPS_BLEND_HI_PPM_DEFAULT; }
void applyPpsLockRppm(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::ppsLockRppm = cfg.ppsLockRppm ? cfg.ppsLockRppm : PPS_LOCK_R_PPM_DEFAULT; }
void applyPpsLockJppm(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::ppsLockJppm = cfg.ppsLockJppm ? cfg.ppsLockJppm : PPS_LOCK_J_PPM_DEFAULT; }
void applyPpsUnlockRppm(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::ppsUnlockRppm = cfg.ppsUnlockRppm ? cfg.ppsUnlockRppm : PPS_UNLOCK_R_PPM_DEFAULT; }
void applyPpsUnlockJppm(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::ppsUnlockJppm = cfg.ppsUnlockJppm ? cfg.ppsUnlockJppm : PPS_UNLOCK_J_PPM_DEFAULT; }
void applyPpsLockCount(const TunableConfig& cfg, const TunableConfigLoadContext&) {
  uint8_t lockCount = cfg.ppsLockCount ? cfg.ppsLockCount : PPS_LOCK_COUNT_DEFAULT;
  if (lockCount < PPS_LOCK_COUNT_MIN) lockCount = PPS_LOCK_COUNT_MIN;
  if (lockCount > PPS_LOCK_COUNT_MAX) lockCount = PPS_LOCK_COUNT_MAX;
  Tunables::ppsLockCount = lockCount;
}
void applyPpsUnlockCount(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::ppsUnlockCount = cfg.ppsUnlockCount ? cfg.ppsUnlockCount : PPS_UNLOCK_COUNT_DEFAULT; }
void applyPpsHoldoverMs(const TunableConfig& cfg, const TunableConfigLoadContext&) { Tunables::ppsHoldoverMs = cfg.ppsHoldoverMs ? cfg.ppsHoldoverMs : PPS_HOLDOVER_MS_DEFAULT; }
void applyPpsStaleMs(const TunableConfig& cfg, const TunableConfigLoadContext& ctx) { Tunables::ppsStaleMs = (ctx.hasTimebaseTunables && cfg.ppsStaleMs) ? cfg.ppsStaleMs : PPS_STALE_MS_DEFAULT; }
void applyPpsIsrStaleMs(const TunableConfig& cfg, const TunableConfigLoadContext& ctx) { Tunables::ppsIsrStaleMs = (ctx.hasTimebaseTunables && cfg.ppsIsrStaleMs) ? cfg.ppsIsrStaleMs : PPS_ISR_STALE_MS_DEFAULT; }
void applyPpsConfigReemitDelayMs(const TunableConfig& cfg, const TunableConfigLoadContext& ctx) { Tunables::ppsConfigReemitDelayMs = ctx.hasTimebaseTunables ? cfg.ppsConfigReemitDelayMs : PPS_CFG_REEMIT_DELAY_MS_DEFAULT; }
void applyPpsAcquireMinMs(const TunableConfig& cfg, const TunableConfigLoadContext& ctx) { Tunables::ppsAcquireMinMs = (ctx.hasTimebaseTunables && cfg.ppsAcquireMinMs) ? cfg.ppsAcquireMinMs : PPS_ACQUIRE_MIN_MS_DEFAULT; }

const TunableDescriptor REGISTRY[] = {
  { PARAM_CORR_JUMP, TunableCliType::Float, nullptr, "set correctionJumpThresh 0.50", "compatibility-only no-op", printFloatCorrectionJump, setCorrectionJumpThresh, writeCorrectionJumpThresh, applyCorrectionJumpThresh },
  { PARAM_PPS_EMA_SHIFT, TunableCliType::Unsigned, nullptr, "set ppsEmaShift 8", "legacy alias of ppsSlowShift", printUIntPpsEmaShift, setPpsEmaShift, writePpsEmaShift, applyPpsEmaShift },
  { PARAM_PPS_FAST_SHIFT, TunableCliType::Unsigned, nullptr, "set ppsFastShift 3", "lower=faster", printUIntPpsFastShift, setPpsFastShift, writePpsFastShift, applyPpsFastShift },
  { PARAM_PPS_SLOW_SHIFT, TunableCliType::Unsigned, nullptr, "set ppsSlowShift 8", "higher=smoother", printUIntPpsSlowShift, setPpsSlowShift, writePpsSlowShift, applyPpsSlowShift },
  { PARAM_PPS_HAMPEL_WIN, TunableCliType::Unsigned, "odd 5..9 recommended", "set ppsHampelWin 7", "compatibility-only no-op", printUIntPpsHampelWin, setPpsHampelWin, writePpsHampelWin, applyPpsHampelWin },
  { PARAM_PPS_HAMPEL_KX100, TunableCliType::Unsigned, nullptr, "set ppsHampelKx100 300", "k=3.00, compatibility-only no-op", printUIntPpsHampelKx100, setPpsHampelKx100, writePpsHampelKx100, applyPpsHampelKx100 },
  { PARAM_PPS_MEDIAN3, TunableCliType::Unsigned, "must be 0 or 1", "set ppsMedian3 1", "compatibility-only no-op", printUIntPpsMedian3, setPpsMedian3, writePpsMedian3, applyPpsMedian3 },
  { PARAM_PPS_BLEND_LO_PPM, TunableCliType::Unsigned, nullptr, "set ppsBlendLoPpm 5", "prefer slow blend below this drift", printUIntPpsBlendLoPpm, setPpsBlendLoPpm, writePpsBlendLoPpm, applyPpsBlendLoPpm },
  { PARAM_PPS_BLEND_HI_PPM, TunableCliType::Unsigned, nullptr, "set ppsBlendHiPpm 200", "prefer fast blend above this drift", printUIntPpsBlendHiPpm, setPpsBlendHiPpm, writePpsBlendHiPpm, applyPpsBlendHiPpm },
  { PARAM_PPS_LOCK_R_PPM, TunableCliType::Unsigned, nullptr, "set ppsLockRppm 50", "max drift to declare lock", printUIntPpsLockRppm, setPpsLockRppm, writePpsLockRppm, applyPpsLockRppm },
  { PARAM_PPS_LOCK_J_PPM, TunableCliType::Unsigned, nullptr, "set ppsLockJppm 20", "legacy jitter threshold to declare lock", printUIntPpsLockJppm, setPpsLockJppm, writePpsLockJppm, applyPpsLockJppm },
  { PARAM_PPS_LOCK_COUNT, TunableCliType::Unsigned, "range 1..60", "set ppsLockCount 10", "consecutive good PPS samples required to lock", printUIntPpsLockCount, setPpsLockCount, writePpsLockCount, applyPpsLockCount },
  { PARAM_PPS_UNLOCK_R_PPM, TunableCliType::Unsigned, nullptr, "set ppsUnlockRppm 200", "drift threshold to unlock", printUIntPpsUnlockRppm, setPpsUnlockRppm, writePpsUnlockRppm, applyPpsUnlockRppm },
  { PARAM_PPS_UNLOCK_J_PPM, TunableCliType::Unsigned, nullptr, "set ppsUnlockJppm 100", "legacy jitter threshold to unlock", printUIntPpsUnlockJppm, setPpsUnlockJppm, writePpsUnlockJppm, applyPpsUnlockJppm },
  { PARAM_PPS_UNLOCK_COUNT, TunableCliType::Unsigned, nullptr, "set ppsUnlockCount 3", "consecutive bad PPS samples required to unlock", printUIntPpsUnlockCount, setPpsUnlockCount, writePpsUnlockCount, applyPpsUnlockCount },
  { PARAM_PPS_HOLDOVER_MS, TunableCliType::Unsigned, nullptr, "set ppsHoldoverMs 60000", "holdover dwell before dropping to free-run", printUIntPpsHoldoverMs, setPpsHoldoverMs, writePpsHoldoverMs, applyPpsHoldoverMs },
  { PARAM_PPS_STALE_MS, TunableCliType::Unsigned, nullptr, "set ppsStaleMs 2200", "PPS freshness timeout for sample arrival", printUIntPpsStaleMs, setPpsStaleMs, writePpsStaleMs, applyPpsStaleMs },
  { PARAM_PPS_ISR_STALE_MS, TunableCliType::Unsigned, nullptr, "set ppsIsrStaleMs 2200", "PPS freshness timeout for ISR activity", printUIntPpsIsrStaleMs, setPpsIsrStaleMs, writePpsIsrStaleMs, applyPpsIsrStaleMs },
  { PARAM_PPS_CFG_REEMIT_DELAY_MS, TunableCliType::Unsigned, nullptr, "set ppsCfgReemitDelayMs 2000", "delay before re-emitting config/status after boot", printUIntPpsConfigReemitDelayMs, setPpsConfigReemitDelayMs, writePpsConfigReemitDelayMs, applyPpsConfigReemitDelayMs },
  { PARAM_PPS_ACQUIRE_MIN_MS, TunableCliType::Unsigned, nullptr, "set ppsAcquireMinMs 60000", "minimum acquire dwell before disciplined lock-in", printUIntPpsAcquireMinMs, setPpsAcquireMinMs, writePpsAcquireMinMs, applyPpsAcquireMinMs },
  { PARAM_DATA_UNITS, TunableCliType::Enum, "raw_cycles | adjusted_ms | adjusted_us | adjusted_ns", "set dataUnits adjusted_us", "output units for serial data", printDataUnits, setDataUnits, writeDataUnits, applyDataUnits },
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
    case TunableCliType::Float:    return "float";
    case TunableCliType::Enum:     return "enum";
    default:                       return "?";
  }
}

const char* dataUnitsName(DataUnits du) {
  switch (du) {
    case DataUnits::RawCycles:  return "raw_cycles";
    case DataUnits::AdjustedMs: return "adjusted_ms";
    case DataUnits::AdjustedUs: return "adjusted_us";
    case DataUnits::AdjustedNs: return "adjusted_ns";
    default:                    return nullptr;
  }
}

bool tryParseDataUnits(const char* value, DataUnits& out) {
  if (equalsIgnoreCaseAscii(value, "raw_cycles")) {
    out = DataUnits::RawCycles;
    return true;
  }
  if (equalsIgnoreCaseAscii(value, "adjusted_ms")) {
    out = DataUnits::AdjustedMs;
    return true;
  }
  if (equalsIgnoreCaseAscii(value, "adjusted_us")) {
    out = DataUnits::AdjustedUs;
    return true;
  }
  if (equalsIgnoreCaseAscii(value, "adjusted_ns")) {
    out = DataUnits::AdjustedNs;
    return true;
  }
  return false;
}
