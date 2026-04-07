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

constexpr uint8_t  EEPROM_CONFIG_VERSION_ACTIVE_PPS_ONLY      = 5; // adds ppsMetrologyGraceMs tunable to persisted config
constexpr uint8_t  EEPROM_CONFIG_VERSION_CURRENT               = EEPROM_CONFIG_VERSION_ACTIVE_PPS_ONLY;

static_assert(sizeof(TunableConfig) <= EEPROM_SLOT_SIZE,
              "TunableConfig must fit within one EEPROM slot");

// Older EEPROM layouts are not migrated anymore. If a saved record does not match
// the current schema/CRC, firmware falls back to compiled defaults until a new save
// rewrites the active schema.
