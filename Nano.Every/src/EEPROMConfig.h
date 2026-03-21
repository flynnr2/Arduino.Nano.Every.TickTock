#pragma once

#include "Config.h"

#include <Arduino.h>

TunableConfig getCurrentConfig();
void applyConfig(const TunableConfig &cfg);
bool loadConfig(TunableConfig &out);
void saveConfig(TunableConfig cfg);
uint16_t computeCRC16(const uint8_t* data, size_t len);

constexpr int      EEPROM_SLOT_NANO_A_ADDR = 0;       // EEPROM slot for 1st copy
constexpr int      EEPROM_SLOT_NANO_B_ADDR = 64;      // EEPROM slot for 2nd copy
constexpr uint16_t EEPROM_SLOT_SIZE        = (uint16_t)(EEPROM_SLOT_NANO_B_ADDR - EEPROM_SLOT_NANO_A_ADDR);

constexpr uint8_t  EEPROM_CONFIG_VERSION_LEGACY                = 0; // seq did not encode schema version
constexpr uint8_t  EEPROM_CONFIG_VERSION_PPS_SLOWSHIFT_PRIMARY = 1; // ppsSlowShift is canonical
constexpr uint8_t  EEPROM_CONFIG_VERSION_PPS_TIMEBASE_TUNABLES = 2; // adds PPS freshness/acquire timing tunables
constexpr uint8_t  EEPROM_CONFIG_VERSION_CURRENT                = EEPROM_CONFIG_VERSION_PPS_TIMEBASE_TUNABLES;

static_assert(sizeof(TunableConfig) <= EEPROM_SLOT_SIZE,
              "TunableConfig must fit within one EEPROM slot");
