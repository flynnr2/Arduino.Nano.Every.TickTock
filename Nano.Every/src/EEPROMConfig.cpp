#include "Config.h"
#include "PendulumProtocol.h"

#include <EEPROM.h>
#include "EEPROMConfig.h"

uint16_t computeCRC16(const uint8_t* data, size_t len) {
  uint16_t crc = 0x0000;
  while (len--) {
    crc ^= (uint16_t)(*data++ << 8);
    for (uint8_t i = 0; i < 8; i++) {
      crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : (crc << 1);
    }
  }
  return crc;
}

static uint16_t crcConfig(TunableConfig cfg) {
  cfg.crc16 = 0;
  return computeCRC16((const uint8_t*)&cfg, sizeof(cfg));
}

static uint32_t currentSeq = 0;

TunableConfig getCurrentConfig() {
  TunableConfig cfg;
  cfg.correctionJumpThresh = Tunables::correctionJumpThresh;
  cfg.ppsEmaShift          = Tunables::ppsSlowShift;   // alias
  cfg.dataUnits            = static_cast<uint8_t>(Tunables::dataUnits);

  cfg.ppsFastShift         = Tunables::ppsFastShift;
  cfg.ppsSlowShift         = Tunables::ppsSlowShift;
  cfg.ppsHampelWin         = Tunables::ppsHampelWin;
  cfg.ppsHampelKx100       = Tunables::ppsHampelKx100;
  cfg.ppsMedian3           = Tunables::ppsMedian3 ? 1 : 0;

  cfg.ppsBlendLoPpm        = Tunables::ppsBlendLoPpm;
  cfg.ppsBlendHiPpm        = Tunables::ppsBlendHiPpm;
  cfg.ppsLockRppm          = Tunables::ppsLockRppm;
  cfg.ppsLockJppm          = Tunables::ppsLockJppm;
  cfg.ppsUnlockRppm        = Tunables::ppsUnlockRppm;
  cfg.ppsUnlockJppm        = Tunables::ppsUnlockJppm;
  cfg.ppsUnlockCount       = Tunables::ppsUnlockCount;
  cfg.ppsHoldoverMs        = Tunables::ppsHoldoverMs;

  cfg.seq                  = currentSeq;
  cfg.crc16                = crcConfig(cfg);
  return cfg;
}

void applyConfig(const TunableConfig &cfg) {
  Tunables::correctionJumpThresh = cfg.correctionJumpThresh;
  Tunables::ppsSlowShift         = cfg.ppsEmaShift; // for back-compat
  Tunables::dataUnits            = static_cast<DataUnits>(cfg.dataUnits);

  // New (use with bounds guards):
  Tunables::ppsFastShift   = cfg.ppsFastShift   ? cfg.ppsFastShift   : PPS_FAST_SHIFT_DEFAULT;
  Tunables::ppsSlowShift   = cfg.ppsSlowShift   ? cfg.ppsSlowShift   : Tunables::ppsSlowShift;
  Tunables::ppsHampelWin   = (cfg.ppsHampelWin >= 5 && (cfg.ppsHampelWin & 1))
                            ? cfg.ppsHampelWin : PPS_HAMPEL_WIN_DEFAULT;
  Tunables::ppsHampelKx100 = cfg.ppsHampelKx100 ? cfg.ppsHampelKx100 : PPS_HAMPEL_KX100_DEFAULT;
  Tunables::ppsMedian3     = (cfg.ppsMedian3 != 0);

  Tunables::ppsBlendLoPpm  = cfg.ppsBlendLoPpm  ? cfg.ppsBlendLoPpm  : PPS_BLEND_LO_PPM_DEFAULT;
  Tunables::ppsBlendHiPpm  = cfg.ppsBlendHiPpm  ? cfg.ppsBlendHiPpm  : PPS_BLEND_HI_PPM_DEFAULT;
  if (Tunables::ppsBlendHiPpm <= Tunables::ppsBlendLoPpm) {
    Tunables::ppsBlendHiPpm = (uint16_t)(Tunables::ppsBlendLoPpm + 1);
  }

  Tunables::ppsLockRppm    = cfg.ppsLockRppm    ? cfg.ppsLockRppm    : PPS_LOCK_R_PPM_DEFAULT;
  Tunables::ppsLockJppm    = cfg.ppsLockJppm    ? cfg.ppsLockJppm    : PPS_LOCK_J_PPM_DEFAULT;
  Tunables::ppsUnlockRppm  = cfg.ppsUnlockRppm  ? cfg.ppsUnlockRppm  : PPS_UNLOCK_R_PPM_DEFAULT;
  Tunables::ppsUnlockJppm  = cfg.ppsUnlockJppm  ? cfg.ppsUnlockJppm  : PPS_UNLOCK_J_PPM_DEFAULT;
  Tunables::ppsUnlockCount = cfg.ppsUnlockCount ? cfg.ppsUnlockCount : PPS_UNLOCK_COUNT_DEFAULT;
  Tunables::ppsHoldoverMs  = cfg.ppsHoldoverMs  ? cfg.ppsHoldoverMs  : PPS_HOLDOVER_MS_DEFAULT;
}

bool loadConfig(TunableConfig &out) {
  TunableConfig a, b;
  EEPROM.get(EEPROM_SLOT_NANO_A_ADDR, a);
  EEPROM.get(EEPROM_SLOT_NANO_B_ADDR, b);
  bool validA = (crcConfig(a) == a.crc16);
  bool validB = (crcConfig(b) == b.crc16);
  if (validA && !validB) { out = a; currentSeq = a.seq; return true; }
  if (!validA && validB) { out = b; currentSeq = b.seq; return true; }
  if (validA && validB) {
    if (b.seq > a.seq) {
      out = b;
      currentSeq = b.seq;
    } else {
      out = a;
      currentSeq = a.seq;
    }
    return true;
  }
  currentSeq = (a.seq > b.seq) ? a.seq : b.seq;
  return false;
}

void saveConfig(TunableConfig cfg) {
  static bool toggle = false;
  cfg.seq = ++currentSeq;
  cfg.crc16 = crcConfig(cfg);
  int addr = toggle ? EEPROM_SLOT_NANO_A_ADDR : EEPROM_SLOT_NANO_B_ADDR;
  EEPROM.put(addr, cfg);
  toggle = !toggle;
}
