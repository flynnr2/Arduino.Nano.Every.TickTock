#include <EEPROM.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "Config.h"
#include "EEPROMConfig.h"
#ifdef EEPROM_CONFIG_HOST_TEST
#include "eeprom_config_host_stubs.h"
#else
#include "SDLogger.h"
#include "Display.h"
#endif

namespace {

constexpr uint32_t UNO_CFG_MAGIC = 0x55434F4EUL; // UCON
constexpr uint16_t UNO_CFG_VERSION = 4;
constexpr uint16_t UNO_CFG_VERSION_LEGACY_POLICY = 3;
constexpr uint16_t UNO_CFG_VERSION_LEGACY_APPEND = 2;
constexpr uint8_t UNO_CFG_VALID = 0xA5;
constexpr uint32_t UNO_STATS_WINDOW_MIN = 1;
constexpr uint32_t UNO_STATS_WINDOW_MAX = DEFAULT_STATS_WINDOW;
constexpr uint32_t UNO_ROLLING_MS_MIN = 1000UL;
constexpr uint32_t UNO_ROLLING_MS_MAX = 3600000UL;
constexpr int32_t UNO_BLOCK_JUMP_US_MIN = 0;
constexpr int32_t UNO_BLOCK_JUMP_US_MAX = 1000000;

struct UnoConfigRecordV3 {
  uint32_t magic;
  uint16_t version;
  uint16_t payloadLen;
  uint32_t seq;

  uint16_t statsWindowSize;
  uint32_t rollingWindowMs;
  int32_t  blockJumpUs;
  uint8_t  logEnabled;
  uint8_t  logDaily;
  uint8_t  logAppend;
  uint8_t  logStartupPolicy;
  char     logBaseName[LOG_FILENAME_LEN];

  uint32_t crc32;
  uint8_t  valid;
} __attribute__((packed));

struct UnoConfigRecord {
  uint32_t magic;
  uint16_t version;
  uint16_t payloadLen;
  uint32_t seq;

  uint16_t statsWindowSize;
  uint32_t rollingWindowMs;
  int32_t  blockJumpUs;
  uint8_t  logEnabled;
  uint8_t  logDaily;
  uint8_t  logAppend;
  uint8_t  logStartupPolicy;
  char     logBaseName[LOG_FILENAME_LEN];

  uint16_t oledShortMinutes;
  uint16_t oledLongMinutes;

  uint32_t crc32;
  uint8_t  valid;
} __attribute__((packed));

static_assert(sizeof(UnoConfigRecord) <= (EEPROM_UNO_SLOT_B_ADDR - EEPROM_UNO_SLOT_A_ADDR), "UnoConfigRecord must fit EEPROM slot");

static uint32_t currentSeqUno = 0;

static uint32_t crc32Update(uint32_t crc, uint8_t data) {
  crc ^= data;
  for (uint8_t i = 0; i < 8; i++) {
    crc = (crc >> 1) ^ (0xEDB88320UL & -(crc & 1));
  }
  return crc;
}

static uint32_t crc32Block(const uint8_t* buf, size_t len) {
  uint32_t crc = 0xFFFFFFFFUL;
  for (size_t i = 0; i < len; i++) crc = crc32Update(crc, buf[i]);
  return ~crc;
}

static bool isSeqNewer(uint32_t lhs, uint32_t rhs) {
  return static_cast<int32_t>(lhs - rhs) > 0;
}

static uint32_t clampU32(uint32_t value, uint32_t lo, uint32_t hi) {
  if (value < lo) return lo;
  if (value > hi) return hi;
  return value;
}

static int32_t clampI32(int32_t value, int32_t lo, int32_t hi) {
  if (value < lo) return lo;
  if (value > hi) return hi;
  return value;
}

static void normalizeFilename(char* out, const char* in) {
  // Validation must not change the running logger, including on a GET request.
  if (in && SDLogger::isValidFilename(in)) {
    if (out != in) strncpy(out, in, LOG_FILENAME_LEN);
  } else {
    strncpy(out, SDLogger::getFilename(), LOG_FILENAME_LEN);
  }
  out[LOG_FILENAME_LEN - 1] = 0;
}

static void encodeRecord(UnoConfigRecord &record, const UnoConfig &cfg, uint32_t seq) {
  memset(&record, 0, sizeof(record));
  record.magic = UNO_CFG_MAGIC;
  record.version = UNO_CFG_VERSION;
  record.payloadLen = sizeof(record) - offsetof(UnoConfigRecord, statsWindowSize)
                      - sizeof(record.crc32) - sizeof(record.valid);
  record.seq = seq;
  record.statsWindowSize = cfg.statsWindowSize;
  record.rollingWindowMs = cfg.rollingWindowMs;
  record.blockJumpUs = cfg.blockJumpUs;
  record.logEnabled = cfg.logEnabled ? 1 : 0;
  record.logDaily = cfg.logDaily ? 1 : 0;
  record.logAppend = cfg.logAppend ? 1 : 0;
  record.logStartupPolicy = cfg.logStartupPolicy;
  strncpy(record.logBaseName, cfg.logBaseName, LOG_FILENAME_LEN);
  record.logBaseName[LOG_FILENAME_LEN - 1] = 0;
  record.oledShortMinutes = cfg.oledShortMinutes;
  record.oledLongMinutes = cfg.oledLongMinutes;
  record.valid = UNO_CFG_VALID;
  record.crc32 = crc32Block(reinterpret_cast<const uint8_t*>(&record), offsetof(UnoConfigRecord, crc32));
}

static void decodeRecord(UnoConfig &cfg, const UnoConfigRecord &record) {
  memset(&cfg, 0, sizeof(cfg));
  cfg.statsWindowSize = record.statsWindowSize;
  cfg.rollingWindowMs = record.rollingWindowMs;
  cfg.blockJumpUs = record.blockJumpUs;
  cfg.logEnabled = (record.logEnabled != 0);
  cfg.logDaily = (record.logDaily != 0);
  cfg.logAppend = (record.logAppend != 0);
  cfg.logStartupPolicy = record.logStartupPolicy;
  cfg.oledShortMinutes = record.oledShortMinutes;
  cfg.oledLongMinutes = record.oledLongMinutes;
  strncpy(cfg.logBaseName, record.logBaseName, LOG_FILENAME_LEN);
  cfg.logBaseName[LOG_FILENAME_LEN - 1] = 0;
}

struct UnoConfigRecordV2 {
  uint32_t magic;
  uint16_t version;
  uint16_t payloadLen;
  uint32_t seq;

  uint16_t statsWindowSize;
  uint32_t rollingWindowMs;
  int32_t  blockJumpUs;
  uint8_t  logEnabled;
  uint8_t  logDaily;
  uint8_t  logAppend;
  char     logBaseName[LOG_FILENAME_LEN];

  uint32_t crc32;
  uint8_t  valid;
} __attribute__((packed));

template<typename Record>
static bool readRecord(int addr, Record& record, uint16_t version) {
  uint8_t* p = reinterpret_cast<uint8_t*>(&record);
  for (size_t i = 0; i < sizeof(record); ++i) p[i] = EEPROM.read(addr + static_cast<int>(i));
  if (record.magic != UNO_CFG_MAGIC || record.version != version) return false;
  if (record.payloadLen != sizeof(record) - offsetof(Record, statsWindowSize)
                           - sizeof(record.crc32) - sizeof(record.valid)) return false;
  if (record.valid != UNO_CFG_VALID) return false;
  return crc32Block(reinterpret_cast<const uint8_t*>(&record), offsetof(Record, crc32)) == record.crc32;
}

static void decodeRecordV3(UnoConfig& cfg, const UnoConfigRecordV3& record) {
  cfg = makeDefaultUnoConfig();
  cfg.statsWindowSize = record.statsWindowSize;
  cfg.rollingWindowMs = record.rollingWindowMs;
  cfg.blockJumpUs = record.blockJumpUs;
  cfg.logEnabled = record.logEnabled != 0;
  cfg.logDaily = record.logDaily != 0;
  cfg.logAppend = record.logAppend != 0;
  cfg.logStartupPolicy = record.logStartupPolicy;
  strncpy(cfg.logBaseName, record.logBaseName, LOG_FILENAME_LEN);
  cfg.logBaseName[LOG_FILENAME_LEN - 1] = 0;
}

static void decodeRecordV2(UnoConfig &cfg, const UnoConfigRecordV2 &record) {
  cfg = makeDefaultUnoConfig();
  cfg.statsWindowSize = record.statsWindowSize;
  cfg.rollingWindowMs = record.rollingWindowMs;
  cfg.blockJumpUs = record.blockJumpUs;
  cfg.logEnabled = (record.logEnabled != 0);
  cfg.logDaily = (record.logDaily != 0);
  cfg.logAppend = (record.logAppend != 0);
  cfg.logStartupPolicy = cfg.logAppend ? LOG_STARTUP_POLICY_APPEND : LOG_STARTUP_POLICY_OVERWRITE;
  strncpy(cfg.logBaseName, record.logBaseName, LOG_FILENAME_LEN);
  cfg.logBaseName[LOG_FILENAME_LEN - 1] = 0;
}

// Compare all supported versions together: a migration always writes the
// older/invalid slot, preserving the newest legacy record if power is lost.
struct SavedSlot {
  bool valid = false;
  uint32_t seq = 0;
  uint16_t version = 0;
  UnoConfig cfg{};
};

static SavedSlot readSlot(int addr) {
  SavedSlot slot;
  UnoConfigRecord current{};
  UnoConfigRecordV3 v3{};
  UnoConfigRecordV2 v2{};
  if (readRecord(addr, current, UNO_CFG_VERSION)) {
    decodeRecord(slot.cfg, current);
    slot.seq = current.seq;
    slot.version = UNO_CFG_VERSION;
  } else if (readRecord(addr, v3, UNO_CFG_VERSION_LEGACY_POLICY)) {
    decodeRecordV3(slot.cfg, v3);
    slot.seq = v3.seq;
    slot.version = UNO_CFG_VERSION_LEGACY_POLICY;
  } else if (readRecord(addr, v2, UNO_CFG_VERSION_LEGACY_APPEND)) {
    decodeRecordV2(slot.cfg, v2);
    slot.seq = v2.seq;
    slot.version = UNO_CFG_VERSION_LEGACY_APPEND;
  } else {
    return slot;
  }
  slot.valid = true;
  return slot;
}

} // namespace

UnoConfig makeDefaultUnoConfig() {
  UnoConfig cfg{};
  cfg.oledShortMinutes = OledRatingConfig::DEFAULT_SHORT_MINUTES;
  cfg.oledLongMinutes = OledRatingConfig::DEFAULT_LONG_MINUTES;
  cfg.statsWindowSize = DEFAULT_STATS_WINDOW;
  cfg.rollingWindowMs = DEFAULT_ROLLING_MS;
  cfg.blockJumpUs = DEFAULT_BLOCK_JUMP_US;
  cfg.logDaily = LOG_DAILY_DEFAULT;
  cfg.logEnabled = LOG_ENABLED_DEFAULT;
  cfg.logAppend = LOG_APPEND_DEFAULT;
  cfg.logStartupPolicy = LOG_STARTUP_POLICY_DEFAULT;
  strncpy(cfg.logBaseName, LOG_FILENAME, LOG_FILENAME_LEN);
  cfg.logBaseName[LOG_FILENAME_LEN - 1] = 0;
  sanitizeUnoConfig(cfg);
  return cfg;
}

UnoConfig getCurrentUnoConfig() {
  UnoConfig cfg{};
  cfg.oledShortMinutes = UnoTunables::oledShortMinutes;
  cfg.oledLongMinutes = UnoTunables::oledLongMinutes;
  cfg.statsWindowSize = UnoTunables::statsWindowSize;
  cfg.rollingWindowMs = UnoTunables::rollingWindowMs;
  cfg.blockJumpUs = UnoTunables::blockJumpUs;
  cfg.logDaily = UnoTunables::logDaily;
  cfg.logEnabled = UnoTunables::logEnabled;
  cfg.logAppend = UnoTunables::logAppend;
  cfg.logStartupPolicy = UnoTunables::logStartupPolicy;
  strncpy(cfg.logBaseName, UnoTunables::logBaseName, LOG_FILENAME_LEN);
  cfg.logBaseName[LOG_FILENAME_LEN - 1] = 0;
  sanitizeUnoConfig(cfg);
  return cfg;
}

void sanitizeUnoConfig(UnoConfig &cfg) {
  OledRatingConfig::sanitize(cfg.oledShortMinutes, cfg.oledLongMinutes);
  cfg.statsWindowSize = static_cast<uint16_t>(clampU32(cfg.statsWindowSize, UNO_STATS_WINDOW_MIN, UNO_STATS_WINDOW_MAX));
  cfg.rollingWindowMs = clampU32(cfg.rollingWindowMs, UNO_ROLLING_MS_MIN, UNO_ROLLING_MS_MAX);
  cfg.blockJumpUs = clampI32(cfg.blockJumpUs, UNO_BLOCK_JUMP_US_MIN, UNO_BLOCK_JUMP_US_MAX);
  cfg.logEnabled = (cfg.logEnabled != 0);
  cfg.logDaily = (cfg.logDaily != 0);
  cfg.logAppend = (cfg.logAppend != 0);
  if (cfg.logStartupPolicy > LOG_STARTUP_POLICY_ARCHIVE) {
    cfg.logStartupPolicy = cfg.logAppend ? LOG_STARTUP_POLICY_APPEND : LOG_STARTUP_POLICY_OVERWRITE;
  }
  cfg.logAppend = (cfg.logStartupPolicy == LOG_STARTUP_POLICY_APPEND);
  normalizeFilename(cfg.logBaseName, cfg.logBaseName);
}

void applyUnoConfig(const UnoConfig &cfgIn) {
  UnoConfig cfg = cfgIn;
  sanitizeUnoConfig(cfg);

  UnoTunables::oledShortMinutes = cfg.oledShortMinutes;
  UnoTunables::oledLongMinutes = cfg.oledLongMinutes;
  Display::configureRating(cfg.oledShortMinutes, cfg.oledLongMinutes);
  UnoTunables::statsWindowSize = cfg.statsWindowSize;
  UnoTunables::rollingWindowMs = cfg.rollingWindowMs;
  UnoTunables::blockJumpUs = cfg.blockJumpUs;
  UnoTunables::logDaily = cfg.logDaily;
  UnoTunables::logEnabled = cfg.logEnabled;
  UnoTunables::logAppend = cfg.logAppend;
  UnoTunables::logStartupPolicy = cfg.logStartupPolicy;
  strncpy(UnoTunables::logBaseName, cfg.logBaseName, LOG_FILENAME_LEN);
  UnoTunables::logBaseName[LOG_FILENAME_LEN - 1] = 0;
}

bool loadUnoConfig(UnoConfig &unoOut) {
  const SavedSlot a = readSlot(EEPROM_UNO_SLOT_A_ADDR);
  const SavedSlot b = readSlot(EEPROM_UNO_SLOT_B_ADDR);
  if (!a.valid && !b.valid) {
    currentSeqUno = 0;
    return false;
  }
  const SavedSlot& best = (!b.valid || (a.valid && isSeqNewer(a.seq, b.seq))) ? a : b;
  currentSeqUno = best.seq;
  unoOut = best.cfg;
  sanitizeUnoConfig(unoOut);
  if (best.version != UNO_CFG_VERSION) saveUnoConfig(unoOut);
  return true;
}

void saveUnoConfig(UnoConfig unoCfg) {
  sanitizeUnoConfig(unoCfg);
  const SavedSlot a = readSlot(EEPROM_UNO_SLOT_A_ADDR);
  const SavedSlot b = readSlot(EEPROM_UNO_SLOT_B_ADDR);
  uint32_t newest = currentSeqUno;
  if (a.valid && (!b.valid || isSeqNewer(a.seq, b.seq))) newest = a.seq;
  else if (b.valid) newest = b.seq;
  const uint32_t nextSeq = newest + 1U; // wrap-safe comparison also accepts zero
  const int targetAddr = !a.valid ? EEPROM_UNO_SLOT_A_ADDR :
                         !b.valid ? EEPROM_UNO_SLOT_B_ADDR :
                         isSeqNewer(a.seq, b.seq) ? EEPROM_UNO_SLOT_B_ADDR : EEPROM_UNO_SLOT_A_ADDR;
  UnoConfigRecord out{};
  encodeRecord(out, unoCfg, nextSeq);
  const uint8_t* p = reinterpret_cast<const uint8_t*>(&out);
  // Invalidate first; commit last. The other slot remains untouched throughout.
  EEPROM.update(targetAddr + static_cast<int>(offsetof(UnoConfigRecord, valid)), 0);
  for (size_t i = 0; i < offsetof(UnoConfigRecord, valid); ++i) {
    EEPROM.update(targetAddr + static_cast<int>(i), p[i]);
  }
  EEPROM.update(targetAddr + static_cast<int>(offsetof(UnoConfigRecord, valid)), UNO_CFG_VALID);
  currentSeqUno = nextSeq;
}

void saveFactoryDefaults(UnoConfig &unoOut) {
  unoOut = makeDefaultUnoConfig();
  saveUnoConfig(unoOut);
}
