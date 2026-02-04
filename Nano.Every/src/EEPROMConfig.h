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
