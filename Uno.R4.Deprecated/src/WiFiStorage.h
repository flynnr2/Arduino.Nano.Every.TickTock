#pragma once
#include "Config.h"

namespace WiFiStorage {
  struct WifiRecord {
    uint32_t magic;
    uint16_t version;
    uint16_t len;
    uint32_t seq;
    char ssid[MAX_SSID_LEN];
    char pass[MAX_PASS_LEN];
    uint32_t crc32;
    uint8_t  valid;
  } __attribute__((packed));

void getFirmwareDefaultCredentials(char* ssidOut, char* passOut);
void saveFirmwareDefaultCredentials();
void saveCredentials(const char* ssid, const char* pass);
void readCredentials(char* ssidOut, char* passOut);
}
