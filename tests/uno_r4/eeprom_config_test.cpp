#include <assert.h>
#include <initializer_list>
#include <stdio.h>
#include <string.h>
#define EEPROM_CONFIG_HOST_TEST
#include "../../Uno.R4.Deprecated/src/EEPROMConfig.cpp"
#include "../../Uno.R4.Deprecated/src/Tunables.cpp"

FakeEeprom EEPROM;
namespace SDLogger {
bool isValidFilename(const char* name) {
  return name && strlen(name) > 0 && strlen(name) < LOG_FILENAME_LEN && strchr(name, '.') != nullptr;
}
const char* getFilename() { return "active.csv"; }
}
namespace Display {
uint16_t lastShort = 0, lastLong = 0;
void configureRating(uint16_t s, uint16_t l) { lastShort = s; lastLong = l; }
}

template<typename Record>
static void storeLegacy(int address, Record record, uint16_t version, uint32_t seq) {
  record.magic = UNO_CFG_MAGIC;
  record.version = version;
  record.payloadLen = sizeof(record) - offsetof(Record, statsWindowSize) - sizeof(record.crc32) - sizeof(record.valid);
  record.seq = seq;
  record.statsWindowSize = 32;
  record.rollingWindowMs = 12345;
  record.blockJumpUs = 234;
  record.logDaily = 1;
  record.logEnabled = 0;
  strcpy(record.logBaseName, "saved.csv");
  record.valid = UNO_CFG_VALID;
  record.crc32 = crc32Block(reinterpret_cast<const uint8_t*>(&record), offsetof(Record, crc32));
  memcpy(EEPROM.bytes + address, &record, sizeof(record));
}

static void assertLegacyPreserved(const UnoConfig& cfg, uint8_t policy) {
  assert(cfg.oledShortMinutes == 5 && cfg.oledLongMinutes == 60);
  assert(cfg.statsWindowSize == 32 && cfg.rollingWindowMs == 12345 && cfg.blockJumpUs == 234);
  assert(cfg.logDaily && !cfg.logEnabled);
  assert(cfg.logStartupPolicy == policy);
  assert(cfg.logAppend == (policy == LOG_STARTUP_POLICY_APPEND));
  assert(strcmp(cfg.logBaseName, "saved.csv") == 0);
}

static void testBoundsAndParsing() {
  using namespace OledRatingConfig;
  assert(DEFAULT_SHORT_MINUTES == 5 && DEFAULT_LONG_MINUTES == 60);
  for (uint16_t s : {0, 1, 5, 30, 31, 65535}) {
    for (uint16_t l : {0, 15, 60, 360, 361, 65535}) {
      uint16_t ss = s, ll = l;
      sanitize(ss, ll);
      assert(valid(ss, ll));
    }
  }
  assert(!valid(30, 59));
  assert(valid(30, 60));
  uint16_t value = 99;
  for (const char* text : {"", "-1", "+5", "5.5", "5m", " 5", "65541", "999999999999999999999"}) {
    assert(!parseMinutes(text, value));
    assert(value == 99);
  }
  assert(parseMinutes("360", value) && value == 360);
  assert(parseMinutes("005", value) && value == 5);
}

static void testRoundTripAndConfigApply() {
  EEPROM.reset();
  UnoConfig cfg = makeDefaultUnoConfig(), loaded{};
  assert(!loadUnoConfig(loaded));
  cfg.oledShortMinutes = 12;
  cfg.oledLongMinutes = 90;
  saveUnoConfig(cfg);
  assert(loadUnoConfig(loaded));
  assert(loaded.oledShortMinutes == 12 && loaded.oledLongMinutes == 90);
  applyUnoConfig(loaded);
  assert(UnoTunables::oledShortMinutes == 12 && UnoTunables::oledLongMinutes == 90);
  assert(Display::lastShort == 12 && Display::lastLong == 90);
  assert(strcmp(SDLogger::getFilename(), "active.csv") == 0);
  cfg.oledShortMinutes = 30;
  cfg.oledLongMinutes = 15;
  saveUnoConfig(cfg);
  assert(loadUnoConfig(loaded));
  assert(loaded.oledShortMinutes == 30 && loaded.oledLongMinutes == 60);
  // Corrupt newest record; the previous slot is still usable.
  EEPROM.bytes[EEPROM_UNO_SLOT_B_ADDR + offsetof(UnoConfigRecord, crc32)] ^= 1;
  assert(loadUnoConfig(loaded));
  assert(loaded.oledShortMinutes == 12 && loaded.oledLongMinutes == 90);
  saveFactoryDefaults(loaded);
  assert(loaded.oledShortMinutes == 5 && loaded.oledLongMinutes == 60);
  for (size_t i = EEPROM_WIFI_SLOT0_ADDR; i < sizeof(EEPROM.bytes); ++i) assert(EEPROM.bytes[i] == 0xff);
}

static void testMigrationAndInterruptedWrites() {
  for (bool v2 : {false, true}) {
    for (int newestAddress : {EEPROM_UNO_SLOT_A_ADDR, EEPROM_UNO_SLOT_B_ADDR}) {
      for (int cut = 0; cut <= static_cast<int>(sizeof(UnoConfigRecord)) + 1; ++cut) {
        EEPROM.reset();
        const int olderAddress = newestAddress == 0 ? EEPROM_UNO_SLOT_B_ADDR : EEPROM_UNO_SLOT_A_ADDR;
        if (v2) {
          UnoConfigRecordV2 old{};
          old.logAppend = 1;
          storeLegacy(olderAddress, old, 2, 41);
          storeLegacy(newestAddress, old, 2, 42);
        } else {
          UnoConfigRecordV3 old{};
          old.logStartupPolicy = LOG_STARTUP_POLICY_ARCHIVE;
          storeLegacy(olderAddress, old, 3, 41);
          storeLegacy(newestAddress, old, 3, 42);
        }
        uint8_t preserved[128];
        memcpy(preserved, EEPROM.bytes + newestAddress, sizeof(preserved));
        EEPROM.writesRemaining = cut;
        UnoConfig loaded{};
        try { assert(loadUnoConfig(loaded)); } catch (const std::runtime_error&) {}
        assert(memcmp(preserved, EEPROM.bytes + newestAddress, sizeof(preserved)) == 0);
        EEPROM.writesRemaining = -1;
        assert(loadUnoConfig(loaded));
        assertLegacyPreserved(loaded, v2 ? LOG_STARTUP_POLICY_APPEND : LOG_STARTUP_POLICY_ARCHIVE);
      }
    }
  }
}

static void testSequenceWrapAndMixedVersions() {
  EEPROM.reset();
  UnoConfig cfg = makeDefaultUnoConfig();
  cfg.oledShortMinutes = 7;
  UnoConfigRecord record{};
  encodeRecord(record, cfg, UINT32_MAX);
  memcpy(EEPROM.bytes, &record, sizeof(record));
  cfg.oledShortMinutes = 8;
  saveUnoConfig(cfg);
  UnoConfig loaded{};
  assert(loadUnoConfig(loaded));
  assert(loaded.oledShortMinutes == 8);
  assert(readSlot(EEPROM_UNO_SLOT_B_ADDR).seq == 0);

  EEPROM.reset();
  encodeRecord(record, cfg, 12);
  memcpy(EEPROM.bytes, &record, sizeof(record));
  UnoConfigRecordV3 legacy{};
  legacy.logStartupPolicy = LOG_STARTUP_POLICY_ARCHIVE;
  storeLegacy(EEPROM_UNO_SLOT_B_ADDR, legacy, 3, 13);
  assert(loadUnoConfig(loaded));
  assertLegacyPreserved(loaded, LOG_STARTUP_POLICY_ARCHIVE);
}

int main() {
  testBoundsAndParsing();
  testRoundTripAndConfigApply();
  testMigrationAndInterruptedWrites();
  testSequenceWrapAndMixedVersions();
  puts("OLED configuration / EEPROM migration tests passed");
}
