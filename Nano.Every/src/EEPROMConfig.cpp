#include "Config.h"
#include "PendulumProtocol.h"

#include <EEPROM.h>
#include "EEPROMConfig.h"
#include "TunableRegistry.h"

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

static constexpr uint32_t EEPROM_CONFIG_VERSION_SHIFT = 24U;
static constexpr uint32_t EEPROM_CONFIG_SEQ_MASK = 0x00FFFFFFUL;

static uint8_t configVersion(const TunableConfig &cfg) {
  return (uint8_t)(cfg.seq >> EEPROM_CONFIG_VERSION_SHIFT);
}

static uint32_t configSeqCounter(const TunableConfig &cfg) {
  return (cfg.seq & EEPROM_CONFIG_SEQ_MASK);
}

static uint32_t packConfigSeq(uint32_t seqCounter, uint8_t version) {
  return ((uint32_t)version << EEPROM_CONFIG_VERSION_SHIFT) | (seqCounter & EEPROM_CONFIG_SEQ_MASK);
}

TunableConfig getCurrentConfig() {
  TunableConfig cfg = {};
  for (const TunableDescriptor* it = tunableRegistryBegin(); it != tunableRegistryEnd(); ++it) {
    it->writeToConfig(cfg);
  }
  cfg.seq = packConfigSeq(currentSeq, EEPROM_CONFIG_VERSION_CURRENT);
  cfg.crc16 = crcConfig(cfg);
  return cfg;
}


void applyConfig(const TunableConfig &cfg) {
  const TunableConfigLoadContext context = {
    (configVersion(cfg) <= EEPROM_CONFIG_VERSION_LEGACY),
    (configVersion(cfg) >= EEPROM_CONFIG_VERSION_PPS_TIMEBASE_TUNABLES),
  };

  for (const TunableDescriptor* it = tunableRegistryBegin(); it != tunableRegistryEnd(); ++it) {
    it->applyFromConfig(cfg, context);
  }

  Tunables::normalizePpsTunables();
}


bool loadConfig(TunableConfig &out) {
  TunableConfig a, b;
  EEPROM.get(EEPROM_SLOT_NANO_A_ADDR, a);
  EEPROM.get(EEPROM_SLOT_NANO_B_ADDR, b);
  bool validA = (crcConfig(a) == a.crc16);
  bool validB = (crcConfig(b) == b.crc16);
  if (validA && !validB) { out = a; currentSeq = configSeqCounter(a); return true; }
  if (!validA && validB) { out = b; currentSeq = configSeqCounter(b); return true; }
  if (validA && validB) {
    if (configSeqCounter(b) > configSeqCounter(a)) {
      out = b;
      currentSeq = configSeqCounter(b);
    } else {
      out = a;
      currentSeq = configSeqCounter(a);
    }
    return true;
  }
  currentSeq = (configSeqCounter(a) > configSeqCounter(b)) ? configSeqCounter(a) : configSeqCounter(b);
  return false;
}

void saveConfig(TunableConfig cfg) {
  static bool toggle = false;
  cfg.seq = packConfigSeq(++currentSeq, EEPROM_CONFIG_VERSION_CURRENT);
  cfg.crc16 = crcConfig(cfg);
  int addr = toggle ? EEPROM_SLOT_NANO_A_ADDR : EEPROM_SLOT_NANO_B_ADDR;
  EEPROM.put(addr, cfg);
  toggle = !toggle;
}
