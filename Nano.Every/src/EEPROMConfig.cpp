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
// The low 24 bits store a wrapping counter; when comparing two values, treat a
// forward delta smaller than half the 24-bit range as "newer" to handle wrap.
static constexpr uint32_t EEPROM_CONFIG_SEQ_MASK = 0x00FFFFFFUL;
static constexpr uint32_t EEPROM_CONFIG_SEQ_HALF_RANGE = (EEPROM_CONFIG_SEQ_MASK + 1UL) / 2UL;

static uint8_t configVersion(const TunableConfig &cfg) {
  return (uint8_t)(cfg.seq >> EEPROM_CONFIG_VERSION_SHIFT);
}

static uint32_t configSeqCounter(const TunableConfig &cfg) {
  return (cfg.seq & EEPROM_CONFIG_SEQ_MASK);
}

static bool isSeqCounterNewer(uint32_t candidate, uint32_t reference) {
  const uint32_t delta = (candidate - reference) & EEPROM_CONFIG_SEQ_MASK;
  return (delta != 0U) && (delta < EEPROM_CONFIG_SEQ_HALF_RANGE);
}

static uint32_t newerSeqCounter(uint32_t a, uint32_t b) {
  return isSeqCounterNewer(b, a) ? b : a;
}

static bool readConfigAt(uint8_t addr, TunableConfig& out, bool& valid) {
  TunableConfig cfg = {};
  EEPROM.get(static_cast<int>(addr), cfg);
  valid = (crcConfig(cfg) == cfg.crc16) && (configVersion(cfg) == EEPROM_CONFIG_VERSION_CURRENT);
  if (valid) {
    out = cfg;
  }
  return valid;
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
  for (const TunableDescriptor* it = tunableRegistryBegin(); it != tunableRegistryEnd(); ++it) {
    it->applyFromConfig(cfg);
  }

  Tunables::normalizePpsTunables();
}


bool loadConfig(TunableConfig &out) {
  TunableConfig a = {};
  TunableConfig b = {};
  bool validA = false;
  bool validB = false;
  readConfigAt(EEPROM_SLOT_NANO_A_ADDR, a, validA);
  readConfigAt(EEPROM_SLOT_NANO_B_ADDR, b, validB);
  if (validA && !validB) { out = a; currentSeq = configSeqCounter(a); return true; }
  if (!validA && validB) { out = b; currentSeq = configSeqCounter(b); return true; }
  if (validA && validB) {
    if (isSeqCounterNewer(configSeqCounter(b), configSeqCounter(a))) {
      out = b;
      currentSeq = configSeqCounter(b);
    } else {
      out = a;
      currentSeq = configSeqCounter(a);
    }
    return true;
  }
  currentSeq = newerSeqCounter(configSeqCounter(a), configSeqCounter(b));
  return false;
}

void saveConfig(TunableConfig cfg) {
  static bool toggle = false;
  cfg.seq = packConfigSeq(++currentSeq, EEPROM_CONFIG_VERSION_CURRENT);
  cfg.crc16 = crcConfig(cfg);
  const uint8_t addr = toggle ? EEPROM_SLOT_NANO_A_ADDR : EEPROM_SLOT_NANO_B_ADDR; // [0, 255] byte address in Nano Every EEPROM map.
  EEPROM.put(static_cast<int>(addr), cfg);
  toggle = !toggle;
}
