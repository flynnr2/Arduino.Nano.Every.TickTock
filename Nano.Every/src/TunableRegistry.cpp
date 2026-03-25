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

void printInvalidValue(const char* name, const char* detail) {
  CMD_SERIAL.print(F("ERROR: "));
  CMD_SERIAL.print(name);
  CMD_SERIAL.print(F(": "));
  CMD_SERIAL.println(detail);
  sendStatus(StatusCode::InvalidValue, detail);
}

void printUIntPpsFastShift(Print& out) { out.print((unsigned)Tunables::ppsFastShift); }
void printUIntPpsSlowShift(Print& out) { out.print((unsigned)Tunables::ppsSlowShift); }
void printUIntPpsBlendLoPpm(Print& out) { out.print((unsigned)Tunables::ppsBlendLoPpm); }
void printUIntPpsBlendHiPpm(Print& out) { out.print((unsigned)Tunables::ppsBlendHiPpm); }
void printUIntPpsLockRppm(Print& out) { out.print((unsigned)Tunables::ppsLockRppm); }
void printUIntPpsLockMadTicks(Print& out) { out.print((unsigned)Tunables::ppsLockMadTicks); }
void printUIntPpsUnlockRppm(Print& out) { out.print((unsigned)Tunables::ppsUnlockRppm); }
void printUIntPpsUnlockMadTicks(Print& out) { out.print((unsigned)Tunables::ppsUnlockMadTicks); }
void printUIntPpsLockCount(Print& out) { out.print((unsigned)Tunables::ppsLockCount); }
void printUIntPpsUnlockCount(Print& out) { out.print((unsigned)Tunables::ppsUnlockCount); }
void printUIntPpsHoldoverMs(Print& out) { out.print((unsigned)Tunables::ppsHoldoverMs); }
void printUIntPpsStaleMs(Print& out) { out.print((unsigned)Tunables::ppsStaleMs); }
void printUIntPpsIsrStaleMs(Print& out) { out.print((unsigned)Tunables::ppsIsrStaleMs); }
void printUIntPpsConfigReemitDelayMs(Print& out) { out.print((unsigned)Tunables::ppsConfigReemitDelayMs); }
void printUIntPpsAcquireMinMs(Print& out) { out.print((unsigned)Tunables::ppsAcquireMinMs); }
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

bool setPpsLockMadTicks(const char* value, bool& tuningChanged, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_LOCK_MAD_TICKS, "expected unsigned integer");
    return false;
  }
  Tunables::ppsLockMadTicks = (uint16_t)parsed;
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

bool setPpsUnlockMadTicks(const char* value, bool& tuningChanged, bool&) {
  unsigned long parsed = 0;
  if (!parseUnsignedLong(value, parsed)) {
    printInvalidValue(PARAM_PPS_UNLOCK_MAD_TICKS, "expected unsigned integer");
    return false;
  }
  Tunables::ppsUnlockMadTicks = (uint16_t)parsed;
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

void writePpsFastShift(TunableConfig& cfg) { cfg.ppsFastShift = Tunables::ppsFastShift; }
void writePpsSlowShift(TunableConfig& cfg) { cfg.ppsSlowShift = Tunables::ppsSlowShift; }
void writePpsBlendLoPpm(TunableConfig& cfg) { cfg.ppsBlendLoPpm = Tunables::ppsBlendLoPpm; }
void writePpsBlendHiPpm(TunableConfig& cfg) { cfg.ppsBlendHiPpm = Tunables::ppsBlendHiPpm; }
void writePpsLockRppm(TunableConfig& cfg) { cfg.ppsLockRppm = Tunables::ppsLockRppm; }
void writePpsLockMadTicks(TunableConfig& cfg) { cfg.ppsLockMadTicks = Tunables::ppsLockMadTicks; }
void writePpsUnlockRppm(TunableConfig& cfg) { cfg.ppsUnlockRppm = Tunables::ppsUnlockRppm; }
void writePpsUnlockMadTicks(TunableConfig& cfg) { cfg.ppsUnlockMadTicks = Tunables::ppsUnlockMadTicks; }
void writePpsLockCount(TunableConfig& cfg) { cfg.ppsLockCount = Tunables::ppsLockCount; }
void writePpsUnlockCount(TunableConfig& cfg) { cfg.ppsUnlockCount = Tunables::ppsUnlockCount; }
void writePpsHoldoverMs(TunableConfig& cfg) { cfg.ppsHoldoverMs = Tunables::ppsHoldoverMs; }
void writePpsStaleMs(TunableConfig& cfg) { cfg.ppsStaleMs = Tunables::ppsStaleMs; }
void writePpsIsrStaleMs(TunableConfig& cfg) { cfg.ppsIsrStaleMs = Tunables::ppsIsrStaleMs; }
void writePpsConfigReemitDelayMs(TunableConfig& cfg) { cfg.ppsConfigReemitDelayMs = Tunables::ppsConfigReemitDelayMs; }
void writePpsAcquireMinMs(TunableConfig& cfg) { cfg.ppsAcquireMinMs = Tunables::ppsAcquireMinMs; }

void applyPpsFastShift(const TunableConfig& cfg) { Tunables::ppsFastShift = cfg.ppsFastShift ? cfg.ppsFastShift : PPS_FAST_SHIFT_DEFAULT; }
void applyPpsSlowShift(const TunableConfig& cfg) { Tunables::ppsSlowShift = cfg.ppsSlowShift ? cfg.ppsSlowShift : PPS_SLOW_SHIFT_DEFAULT; }
void applyPpsBlendLoPpm(const TunableConfig& cfg) { Tunables::ppsBlendLoPpm = cfg.ppsBlendLoPpm ? cfg.ppsBlendLoPpm : PPS_BLEND_LO_PPM_DEFAULT; }
void applyPpsBlendHiPpm(const TunableConfig& cfg) { Tunables::ppsBlendHiPpm = cfg.ppsBlendHiPpm ? cfg.ppsBlendHiPpm : PPS_BLEND_HI_PPM_DEFAULT; }
void applyPpsLockRppm(const TunableConfig& cfg) { Tunables::ppsLockRppm = cfg.ppsLockRppm ? cfg.ppsLockRppm : PPS_LOCK_R_PPM_DEFAULT; }
void applyPpsLockMadTicks(const TunableConfig& cfg) { Tunables::ppsLockMadTicks = cfg.ppsLockMadTicks ? cfg.ppsLockMadTicks : PPS_LOCK_MAD_TICKS_DEFAULT; }
void applyPpsUnlockRppm(const TunableConfig& cfg) { Tunables::ppsUnlockRppm = cfg.ppsUnlockRppm ? cfg.ppsUnlockRppm : PPS_UNLOCK_R_PPM_DEFAULT; }
void applyPpsUnlockMadTicks(const TunableConfig& cfg) { Tunables::ppsUnlockMadTicks = cfg.ppsUnlockMadTicks ? cfg.ppsUnlockMadTicks : PPS_UNLOCK_MAD_TICKS_DEFAULT; }
void applyPpsLockCount(const TunableConfig& cfg) {
  uint8_t lockCount = cfg.ppsLockCount ? cfg.ppsLockCount : PPS_LOCK_COUNT_DEFAULT;
  if (lockCount < PPS_LOCK_COUNT_MIN) lockCount = PPS_LOCK_COUNT_MIN;
  if (lockCount > PPS_LOCK_COUNT_MAX) lockCount = PPS_LOCK_COUNT_MAX;
  Tunables::ppsLockCount = lockCount;
}
void applyPpsUnlockCount(const TunableConfig& cfg) { Tunables::ppsUnlockCount = cfg.ppsUnlockCount ? cfg.ppsUnlockCount : PPS_UNLOCK_COUNT_DEFAULT; }
void applyPpsHoldoverMs(const TunableConfig& cfg) { Tunables::ppsHoldoverMs = cfg.ppsHoldoverMs ? cfg.ppsHoldoverMs : PPS_HOLDOVER_MS_DEFAULT; }
void applyPpsStaleMs(const TunableConfig& cfg) { Tunables::ppsStaleMs = cfg.ppsStaleMs ? cfg.ppsStaleMs : PPS_STALE_MS_DEFAULT; }
void applyPpsIsrStaleMs(const TunableConfig& cfg) { Tunables::ppsIsrStaleMs = cfg.ppsIsrStaleMs ? cfg.ppsIsrStaleMs : PPS_ISR_STALE_MS_DEFAULT; }
void applyPpsConfigReemitDelayMs(const TunableConfig& cfg) { Tunables::ppsConfigReemitDelayMs = cfg.ppsConfigReemitDelayMs; }
void applyPpsAcquireMinMs(const TunableConfig& cfg) { Tunables::ppsAcquireMinMs = cfg.ppsAcquireMinMs ? cfg.ppsAcquireMinMs : PPS_ACQUIRE_MIN_MS_DEFAULT; }

const TunableDescriptor REGISTRY[] = {
  { PARAM_PPS_FAST_SHIFT, nullptr, TunableCliType::Unsigned, nullptr, "set ppsFastShift 3", "lower=faster", printUIntPpsFastShift, setPpsFastShift, writePpsFastShift, applyPpsFastShift },
  { PARAM_PPS_SLOW_SHIFT, nullptr, TunableCliType::Unsigned, nullptr, "set ppsSlowShift 8", "higher=smoother", printUIntPpsSlowShift, setPpsSlowShift, writePpsSlowShift, applyPpsSlowShift },
  { PARAM_PPS_BLEND_LO_PPM, nullptr, TunableCliType::Unsigned, nullptr, "set ppsBlendLoPpm 5", "prefer slow blend below this drift", printUIntPpsBlendLoPpm, setPpsBlendLoPpm, writePpsBlendLoPpm, applyPpsBlendLoPpm },
  { PARAM_PPS_BLEND_HI_PPM, nullptr, TunableCliType::Unsigned, nullptr, "set ppsBlendHiPpm 200", "prefer fast blend above this drift", printUIntPpsBlendHiPpm, setPpsBlendHiPpm, writePpsBlendHiPpm, applyPpsBlendHiPpm },
  { PARAM_PPS_LOCK_R_PPM, nullptr, TunableCliType::Unsigned, nullptr, "set ppsLockRppm 175", "max drift to declare lock", printUIntPpsLockRppm, setPpsLockRppm, writePpsLockRppm, applyPpsLockRppm },
  { PARAM_PPS_LOCK_MAD_TICKS, PARAM_PPS_LOCK_MAD_TICKS_DEPRECATED_ALIAS, TunableCliType::Unsigned, nullptr, "set ppsLockMadTicks 600", "max MAD threshold in ticks to declare lock", printUIntPpsLockMadTicks, setPpsLockMadTicks, writePpsLockMadTicks, applyPpsLockMadTicks },
  { PARAM_PPS_LOCK_COUNT, nullptr, TunableCliType::Unsigned, "range 1..60", "set ppsLockCount 10", "consecutive good PPS samples required to lock", printUIntPpsLockCount, setPpsLockCount, writePpsLockCount, applyPpsLockCount },
  { PARAM_PPS_UNLOCK_R_PPM, nullptr, TunableCliType::Unsigned, nullptr, "set ppsUnlockRppm 300", "drift threshold to unlock", printUIntPpsUnlockRppm, setPpsUnlockRppm, writePpsUnlockRppm, applyPpsUnlockRppm },
  { PARAM_PPS_UNLOCK_MAD_TICKS, PARAM_PPS_UNLOCK_MAD_TICKS_DEPRECATED_ALIAS, TunableCliType::Unsigned, nullptr, "set ppsUnlockMadTicks 900", "max MAD threshold in ticks before unlock", printUIntPpsUnlockMadTicks, setPpsUnlockMadTicks, writePpsUnlockMadTicks, applyPpsUnlockMadTicks },
  { PARAM_PPS_UNLOCK_COUNT, nullptr, TunableCliType::Unsigned, nullptr, "set ppsUnlockCount 3", "consecutive bad PPS samples required to unlock", printUIntPpsUnlockCount, setPpsUnlockCount, writePpsUnlockCount, applyPpsUnlockCount },
  { PARAM_PPS_HOLDOVER_MS, nullptr, TunableCliType::Unsigned, nullptr, "set ppsHoldoverMs 60000", "holdover dwell before dropping to free-run", printUIntPpsHoldoverMs, setPpsHoldoverMs, writePpsHoldoverMs, applyPpsHoldoverMs },
  { PARAM_PPS_STALE_MS, nullptr, TunableCliType::Unsigned, nullptr, "set ppsStaleMs 2200", "timeout for processed queued PPS samples (main-loop freshness)", printUIntPpsStaleMs, setPpsStaleMs, writePpsStaleMs, applyPpsStaleMs },
  { PARAM_PPS_ISR_STALE_MS, nullptr, TunableCliType::Unsigned, nullptr, "set ppsIsrStaleMs 2200", "timeout for PPS ISR edge/count changes before declaring no PPS", printUIntPpsIsrStaleMs, setPpsIsrStaleMs, writePpsIsrStaleMs, applyPpsIsrStaleMs },
  { PARAM_PPS_CFG_REEMIT_DELAY_MS, nullptr, TunableCliType::Unsigned, nullptr, "set ppsCfgReemitDelayMs 2000", "delay before re-emitting config/status after boot", printUIntPpsConfigReemitDelayMs, setPpsConfigReemitDelayMs, writePpsConfigReemitDelayMs, applyPpsConfigReemitDelayMs },
  { PARAM_PPS_ACQUIRE_MIN_MS, nullptr, TunableCliType::Unsigned, nullptr, "set ppsAcquireMinMs 60000", "minimum acquire dwell before disciplined lock-in", printUIntPpsAcquireMinMs, setPpsAcquireMinMs, writePpsAcquireMinMs, applyPpsAcquireMinMs },
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
    if (it->deprecatedCliAlias && equalsIgnoreCaseAscii(cliName, it->deprecatedCliAlias)) return it;
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
