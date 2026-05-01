#pragma once

#include "Config.h"

#include <Arduino.h>

TunableConfig getCurrentConfig();
void applyConfig(const TunableConfig &cfg);
bool loadConfig(TunableConfig &out);
void saveConfig(TunableConfig cfg);
uint16_t computeCRC16(const uint8_t* data, size_t len);

constexpr uint8_t  EEPROM_SLOT_NANO_A_ADDR = 0;       // [0, 255] EEPROM base byte for 1st copy
constexpr uint8_t  EEPROM_SLOT_NANO_B_ADDR = 64;      // [0, 255] EEPROM base byte for 2nd copy
constexpr uint16_t EEPROM_SLOT_SIZE        = (uint16_t)(EEPROM_SLOT_NANO_B_ADDR - EEPROM_SLOT_NANO_A_ADDR);

constexpr uint8_t  EEPROM_CONFIG_VERSION_EXPLICIT_V1 = 6;
constexpr uint8_t  EEPROM_CONFIG_VERSION_CURRENT     = EEPROM_CONFIG_VERSION_EXPLICIT_V1;

enum class EepromSlotCode : uint8_t {
  Ok = 0,
  Mag,
  Ver,
  Len,
  Lay,
  Crc,
  Sem,
  Ncm
};

struct EepromLoadDiag {
  char source;
  EepromSlotCode slotA;
  EepromSlotCode slotB;
  uint8_t schema;
  uint32_t sequence;
  bool hasSequence;
};

const EepromLoadDiag& getEepromLoadDiag();
