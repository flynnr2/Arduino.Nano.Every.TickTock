#include "WiFiStorage.h"
#include <EEPROM.h>

namespace WiFiStorage {

#define WIFI_REC_VERSION   1
#define WIFI_MAGIC         0x57494649UL

static_assert(sizeof(WifiRecord) <= EEPROM_WIFI_SLOT_SIZE, "WifiRecord must fit WiFi slot");
static_assert(EEPROM_WIFI_SLOT1_ADDR + sizeof(WifiRecord) <= EEPROM_SIZE, "WifiRecord slots exceed EEPROM");
static_assert(EEPROM_WIFI_SLOT0_ADDR >= EEPROM_UNO_SLOT_B_ADDR + 64, "WiFi slots must not overlap tunables");

static uint32_t crc32_update(uint32_t crc, uint8_t data) {
  crc ^= data;
  for (uint8_t i = 0; i < 8; i++) {
    crc = (crc >> 1) ^ (0xEDB88320UL & -(crc & 1));
  }
  return crc;
}

static uint32_t crc32_block(const uint8_t* buf, size_t len) {
  uint32_t crc = 0xFFFFFFFFUL;
  for (size_t i = 0; i < len; i++) crc = crc32_update(crc, buf[i]);
  return ~crc;
}

static bool readSlot(uint16_t addr, WifiRecord &rec) {
  uint8_t* p = reinterpret_cast<uint8_t*>(&rec);
  for (size_t i=0;i<sizeof(WifiRecord);i++) p[i] = EEPROM.read(addr + i);
  if (rec.magic != WIFI_MAGIC) return false;
  if (rec.version != WIFI_REC_VERSION) return false;
  uint32_t calc = crc32_block(reinterpret_cast<const uint8_t*>(&rec), offsetof(WifiRecord, crc32));
  return (rec.valid == 0xA5 && calc == rec.crc32);
}

void getFirmwareDefaultCredentials(char* ssidOut, char* passOut) {
  if (ssidOut) {
    strncpy(ssidOut, WIFI_SSID, MAX_SSID_LEN);
    ssidOut[MAX_SSID_LEN - 1] = 0;
  }
  if (passOut) {
    strncpy(passOut, WIFI_PASS, MAX_PASS_LEN);
    passOut[MAX_PASS_LEN - 1] = 0;
  }
}

void saveFirmwareDefaultCredentials() {
  char ssid[MAX_SSID_LEN] = {0};
  char pass[MAX_PASS_LEN] = {0};
  getFirmwareDefaultCredentials(ssid, pass);
  saveCredentials(ssid, pass);
}

void saveCredentials(const char* newSsid, const char* newPass) {
  WifiRecord a, b; bool va = readSlot(EEPROM_WIFI_SLOT0_ADDR, a); bool vb = readSlot(EEPROM_WIFI_SLOT1_ADDR, b);
  uint32_t nextSeq = 1;
  if (va && a.seq >= nextSeq) nextSeq = a.seq + 1;
  if (vb && b.seq >= nextSeq) nextSeq = b.seq + 1;

  uint16_t targetAddr;
  if (!va) targetAddr = EEPROM_WIFI_SLOT0_ADDR;
  else if (!vb) targetAddr = EEPROM_WIFI_SLOT1_ADDR;
  else targetAddr = (a.seq <= b.seq) ? EEPROM_WIFI_SLOT0_ADDR : EEPROM_WIFI_SLOT1_ADDR;

  WifiRecord rec{};
  rec.magic   = WIFI_MAGIC;
  rec.version = WIFI_REC_VERSION;
  rec.len     = sizeof(rec.ssid) + sizeof(rec.pass);
  rec.seq     = nextSeq;
  strncpy(rec.ssid, newSsid ? newSsid : "", MAX_SSID_LEN);
  rec.ssid[MAX_SSID_LEN-1] = 0;
  strncpy(rec.pass, newPass ? newPass : "", MAX_PASS_LEN);
  rec.pass[MAX_PASS_LEN-1] = 0;
  rec.valid = 0xA5;
  rec.crc32 = crc32_block(reinterpret_cast<const uint8_t*>(&rec), offsetof(WifiRecord, crc32));

  const uint8_t* p = reinterpret_cast<const uint8_t*>(&rec);
  for (size_t i=0;i<sizeof(WifiRecord);i++) {
    if (i == offsetof(WifiRecord, valid)) continue;
    EEPROM.update(targetAddr + i, p[i]);
  }
  EEPROM.update(targetAddr + offsetof(WifiRecord, valid), 0xA5);
}

void readCredentials(char* ssidOut, char* passOut) {
  WifiRecord a, b; bool va = readSlot(EEPROM_WIFI_SLOT0_ADDR, a); bool vb = readSlot(EEPROM_WIFI_SLOT1_ADDR, b);
  const WifiRecord* best = nullptr;
  if (va && vb)      best = (a.seq >= b.seq) ? &a : &b;
  else if (va)       best = &a;
  else if (vb)       best = &b;

  if (best) {
    strncpy(ssidOut, best->ssid, MAX_SSID_LEN);
    ssidOut[MAX_SSID_LEN-1] = 0;
    strncpy(passOut, best->pass, MAX_PASS_LEN);
    passOut[MAX_PASS_LEN-1] = 0;
  } else {
    ssidOut[0] = '\0';
    passOut[0] = '\0';
  }
}

} // namespace WiFiStorage
