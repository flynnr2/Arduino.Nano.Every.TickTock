#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdexcept>
#include <string.h>
struct FakeEeprom {
  uint8_t bytes[512];
  int writesRemaining = -1;
  uint8_t read(int address) const { return bytes[address]; }
  void update(int address, uint8_t value) {
    if (writesRemaining == 0) throw std::runtime_error("power lost");
    if (writesRemaining > 0) --writesRemaining;
    bytes[address] = value;
  }
  void reset() { memset(bytes, 0xff, sizeof(bytes)); writesRemaining = -1; }
};
extern FakeEeprom EEPROM;
