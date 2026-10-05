#pragma once
#include <stdint.h>
#include <string.h>
struct FakeEeprom {
 uint8_t bytes[256];
 int updates = 0, cut = -1, stuck = -1;
 void reset() { memset(bytes, 0xff, sizeof bytes); updates = 0; cut = stuck = -1; }
 uint8_t read(int address) { return bytes[address]; }
 void update(int address, uint8_t value) {
   if (cut >= 0 && updates++ >= cut) return;
   if (address != stuck) bytes[address] = value;
 }
};
extern FakeEeprom EEPROM;
