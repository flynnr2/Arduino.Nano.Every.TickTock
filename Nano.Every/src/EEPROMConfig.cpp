#include "Config.h"

#include <EEPROM.h>
#include "EEPROMConfig.h"
#include "TunableRegistry.h"

namespace {

constexpr uint16_t EEPROM_RECORD_MAGIC = 0x5045U; // "EP" little-endian
constexpr uint16_t EEPROM_LAYOUT_ID = 0x0101U;    // explicit payload field order fingerprint
constexpr uint8_t EEPROM_COMMITTED_MARKER = 0xA5U;
constexpr uint8_t EEPROM_UNCOMMITTED_MARKER = 0x00U;

constexpr uint8_t HEADER_OFF_MAGIC = 0;
constexpr uint8_t HEADER_OFF_SCHEMA = 2;
constexpr uint8_t HEADER_OFF_PAYLOAD_LEN = 3;
constexpr uint8_t HEADER_OFF_LAYOUT_ID = 4;
constexpr uint8_t HEADER_OFF_SEQ = 6;
constexpr uint8_t HEADER_OFF_PAYLOAD_CRC = 10;
constexpr uint8_t HEADER_OFF_COMMITTED = 12;
constexpr uint8_t HEADER_OFF_RESERVED0 = 13;
constexpr uint8_t HEADER_OFF_RESERVED1 = 14;
constexpr uint8_t HEADER_OFF_RESERVED2 = 15;
constexpr uint8_t EEPROM_HEADER_SIZE = 16;

constexpr uint8_t PAYLOAD_OFF_FAST_SHIFT = 0;
constexpr uint8_t PAYLOAD_OFF_SLOW_SHIFT = 1;
constexpr uint8_t PAYLOAD_OFF_BLEND_LO = 2;
constexpr uint8_t PAYLOAD_OFF_BLEND_HI = 4;
constexpr uint8_t PAYLOAD_OFF_LOCK_RPPM = 6;
constexpr uint8_t PAYLOAD_OFF_LOCK_MAD = 8;
constexpr uint8_t PAYLOAD_OFF_UNLOCK_RPPM = 10;
constexpr uint8_t PAYLOAD_OFF_UNLOCK_MAD = 12;
constexpr uint8_t PAYLOAD_OFF_LOCK_COUNT = 14;
constexpr uint8_t PAYLOAD_OFF_UNLOCK_COUNT = 15;
constexpr uint8_t PAYLOAD_OFF_HOLDOVER_MS = 16;
constexpr uint8_t PAYLOAD_OFF_STALE_MS = 18;
constexpr uint8_t PAYLOAD_OFF_ISR_STALE_MS = 20;
constexpr uint8_t PAYLOAD_OFF_CFG_REEMIT_MS = 22;
constexpr uint8_t PAYLOAD_OFF_ACQUIRE_MIN_MS = 24;
constexpr uint8_t PAYLOAD_OFF_METROLOGY_GRACE_MS = 26;
constexpr uint8_t EEPROM_PAYLOAD_SIZE = 30;

static_assert(EEPROM_HEADER_SIZE <= EEPROM_SLOT_SIZE, "EEPROM header must fit slot");
static_assert(EEPROM_PAYLOAD_SIZE <= EEPROM_SLOT_SIZE, "EEPROM payload must fit slot");
static_assert((uint16_t)EEPROM_HEADER_SIZE + (uint16_t)EEPROM_PAYLOAD_SIZE <= EEPROM_SLOT_SIZE,
              "EEPROM record must fit slot");

static uint32_t currentSeq = 0;
static EepromLoadDiag gLoadDiag = {'D', EepromSlotCode::Ncm, EepromSlotCode::Ncm,
                                    EEPROM_CONFIG_VERSION_CURRENT, 0U, false};

struct DecodedRecord {
  TunableConfig cfg;
  uint32_t seq;
  EepromSlotCode code;
  bool valid;
};

static void writeU16LE(uint8_t* out, uint16_t value) {
  out[0] = (uint8_t)(value & 0xFFU);
  out[1] = (uint8_t)((value >> 8) & 0xFFU);
}

static void writeU32LE(uint8_t* out, uint32_t value) {
  out[0] = (uint8_t)(value & 0xFFU);
  out[1] = (uint8_t)((value >> 8) & 0xFFU);
  out[2] = (uint8_t)((value >> 16) & 0xFFU);
  out[3] = (uint8_t)((value >> 24) & 0xFFU);
}

static uint16_t readU16LE(const uint8_t* in) {
  return (uint16_t)in[0] | ((uint16_t)in[1] << 8);
}

static uint32_t readU32LE(const uint8_t* in) {
  return (uint32_t)in[0]
       | ((uint32_t)in[1] << 8)
       | ((uint32_t)in[2] << 16)
       | ((uint32_t)in[3] << 24);
}

static void encodePayload(const TunableConfig& cfg, uint8_t* payloadOut) {
  payloadOut[PAYLOAD_OFF_FAST_SHIFT] = cfg.ppsFastShift;
  payloadOut[PAYLOAD_OFF_SLOW_SHIFT] = cfg.ppsSlowShift;
  writeU16LE(payloadOut + PAYLOAD_OFF_BLEND_LO, cfg.ppsBlendLoPpm);
  writeU16LE(payloadOut + PAYLOAD_OFF_BLEND_HI, cfg.ppsBlendHiPpm);
  writeU16LE(payloadOut + PAYLOAD_OFF_LOCK_RPPM, cfg.ppsLockRppm);
  writeU16LE(payloadOut + PAYLOAD_OFF_LOCK_MAD, cfg.ppsLockMadTicks);
  writeU16LE(payloadOut + PAYLOAD_OFF_UNLOCK_RPPM, cfg.ppsUnlockRppm);
  writeU16LE(payloadOut + PAYLOAD_OFF_UNLOCK_MAD, cfg.ppsUnlockMadTicks);
  payloadOut[PAYLOAD_OFF_LOCK_COUNT] = cfg.ppsLockCount;
  payloadOut[PAYLOAD_OFF_UNLOCK_COUNT] = cfg.ppsUnlockCount;
  writeU16LE(payloadOut + PAYLOAD_OFF_HOLDOVER_MS, cfg.ppsHoldoverMs);
  writeU16LE(payloadOut + PAYLOAD_OFF_STALE_MS, cfg.ppsStaleMs);
  writeU16LE(payloadOut + PAYLOAD_OFF_ISR_STALE_MS, cfg.ppsIsrStaleMs);
  writeU16LE(payloadOut + PAYLOAD_OFF_CFG_REEMIT_MS, cfg.ppsConfigReemitDelayMs);
  writeU16LE(payloadOut + PAYLOAD_OFF_ACQUIRE_MIN_MS, cfg.ppsAcquireMinMs);
  writeU32LE(payloadOut + PAYLOAD_OFF_METROLOGY_GRACE_MS, cfg.ppsMetrologyGraceMs);
}

static void decodePayload(const uint8_t* payload, TunableConfig& cfgOut) {
  cfgOut = {};
  cfgOut.ppsFastShift = payload[PAYLOAD_OFF_FAST_SHIFT];
  cfgOut.ppsSlowShift = payload[PAYLOAD_OFF_SLOW_SHIFT];
  cfgOut.ppsBlendLoPpm = readU16LE(payload + PAYLOAD_OFF_BLEND_LO);
  cfgOut.ppsBlendHiPpm = readU16LE(payload + PAYLOAD_OFF_BLEND_HI);
  cfgOut.ppsLockRppm = readU16LE(payload + PAYLOAD_OFF_LOCK_RPPM);
  cfgOut.ppsLockMadTicks = readU16LE(payload + PAYLOAD_OFF_LOCK_MAD);
  cfgOut.ppsUnlockRppm = readU16LE(payload + PAYLOAD_OFF_UNLOCK_RPPM);
  cfgOut.ppsUnlockMadTicks = readU16LE(payload + PAYLOAD_OFF_UNLOCK_MAD);
  cfgOut.ppsLockCount = payload[PAYLOAD_OFF_LOCK_COUNT];
  cfgOut.ppsUnlockCount = payload[PAYLOAD_OFF_UNLOCK_COUNT];
  cfgOut.ppsHoldoverMs = readU16LE(payload + PAYLOAD_OFF_HOLDOVER_MS);
  cfgOut.ppsStaleMs = readU16LE(payload + PAYLOAD_OFF_STALE_MS);
  cfgOut.ppsIsrStaleMs = readU16LE(payload + PAYLOAD_OFF_ISR_STALE_MS);
  cfgOut.ppsConfigReemitDelayMs = readU16LE(payload + PAYLOAD_OFF_CFG_REEMIT_MS);
  cfgOut.ppsAcquireMinMs = readU16LE(payload + PAYLOAD_OFF_ACQUIRE_MIN_MS);
  cfgOut.ppsMetrologyGraceMs = readU32LE(payload + PAYLOAD_OFF_METROLOGY_GRACE_MS);
}

static bool validateSemantics(const TunableConfig& cfg) {
  if (cfg.ppsFastShift < PPS_SHIFT_MIN || cfg.ppsFastShift > PPS_SHIFT_MAX) return false;
  if (cfg.ppsSlowShift < PPS_SHIFT_MIN || cfg.ppsSlowShift > PPS_SHIFT_MAX) return false;
  if (cfg.ppsSlowShift < cfg.ppsFastShift) return false;

  if (cfg.ppsBlendHiPpm <= cfg.ppsBlendLoPpm) return false;
  if (cfg.ppsBlendHiPpm > 20000U || cfg.ppsBlendLoPpm > 20000U) return false;

  if (cfg.ppsLockRppm > 20000U || cfg.ppsUnlockRppm > 20000U) return false;
  if (cfg.ppsUnlockRppm < cfg.ppsLockRppm) return false;

  if (cfg.ppsLockMadTicks > 20000U || cfg.ppsUnlockMadTicks > 20000U) return false;
  if (cfg.ppsUnlockMadTicks < cfg.ppsLockMadTicks) return false;

  if (cfg.ppsLockCount < PPS_LOCK_COUNT_MIN || cfg.ppsLockCount > PPS_LOCK_COUNT_MAX) return false;
  if (cfg.ppsUnlockCount < 1U || cfg.ppsUnlockCount > PPS_LOCK_COUNT_MAX) return false;

  if (cfg.ppsHoldoverMs == 0U) return false;
  if (cfg.ppsStaleMs == 0U || cfg.ppsStaleMs > 30000U) return false;
  if (cfg.ppsIsrStaleMs == 0U || cfg.ppsIsrStaleMs > 30000U) return false;
  if (cfg.ppsConfigReemitDelayMs == 0U) return false;
  if (cfg.ppsAcquireMinMs == 0U) return false;
  if (cfg.ppsMetrologyGraceMs == 0U || cfg.ppsMetrologyGraceMs > 86400000UL) return false;

  return true;
}

static bool isSeqNewer(uint32_t a, uint32_t b) {
  return (int32_t)(a - b) > 0;
}

static void eepromReadBytes(uint8_t addr, uint8_t* out, uint8_t len) {
  for (uint8_t i = 0; i < len; ++i) {
    out[i] = EEPROM.read((int)addr + (int)i);
  }
}

static void eepromUpdateBytes(uint8_t addr, const uint8_t* src, uint8_t len) {
  for (uint8_t i = 0; i < len; ++i) {
    EEPROM.update((int)addr + (int)i, src[i]);
  }
}

static DecodedRecord readRecordAt(uint8_t addr) {
  DecodedRecord out = {};
  out.code = EepromSlotCode::Ncm;

  uint8_t header[EEPROM_HEADER_SIZE];
  uint8_t payload[EEPROM_PAYLOAD_SIZE];
  eepromReadBytes(addr, header, EEPROM_HEADER_SIZE);

  if (readU16LE(header + HEADER_OFF_MAGIC) != EEPROM_RECORD_MAGIC) {
    out.code = EepromSlotCode::Mag;
    return out;
  }
  if (header[HEADER_OFF_SCHEMA] != EEPROM_CONFIG_VERSION_CURRENT) {
    out.code = EepromSlotCode::Ver;
    return out;
  }
  if (header[HEADER_OFF_PAYLOAD_LEN] != EEPROM_PAYLOAD_SIZE) {
    out.code = EepromSlotCode::Len;
    return out;
  }
  if (readU16LE(header + HEADER_OFF_LAYOUT_ID) != EEPROM_LAYOUT_ID) {
    out.code = EepromSlotCode::Lay;
    return out;
  }
  if (header[HEADER_OFF_COMMITTED] != EEPROM_COMMITTED_MARKER) {
    out.code = EepromSlotCode::Ncm;
    return out;
  }

  eepromReadBytes((uint8_t)(addr + EEPROM_HEADER_SIZE), payload, EEPROM_PAYLOAD_SIZE);
  const uint16_t expectedCrc = readU16LE(header + HEADER_OFF_PAYLOAD_CRC);
  const uint16_t actualCrc = computeCRC16(payload, EEPROM_PAYLOAD_SIZE);
  if (actualCrc != expectedCrc) {
    out.code = EepromSlotCode::Crc;
    return out;
  }

  decodePayload(payload, out.cfg);
  if (!validateSemantics(out.cfg)) {
    out.code = EepromSlotCode::Sem;
    return out;
  }

  out.seq = readU32LE(header + HEADER_OFF_SEQ);
  out.cfg.seq = out.seq;
  out.valid = true;
  out.code = EepromSlotCode::Ok;
  return out;
}

static uint8_t pickSaveAddr(bool validA, uint32_t seqA, bool validB, uint32_t seqB) {
  if (validA && validB) {
    return isSeqNewer(seqA, seqB) ? EEPROM_SLOT_NANO_B_ADDR : EEPROM_SLOT_NANO_A_ADDR;
  }
  if (validA) return EEPROM_SLOT_NANO_B_ADDR;
  if (validB) return EEPROM_SLOT_NANO_A_ADDR;
  return EEPROM_SLOT_NANO_A_ADDR;
}

} // namespace

uint16_t computeCRC16(const uint8_t* data, size_t len) {
  uint16_t crc = 0x0000;
  while (len--) {
    crc ^= (uint16_t)(*data++ << 8);
    for (uint8_t i = 0; i < 8; i++) {
      crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : (crc << 1);
    }
  }
  return crc;
}

const EepromLoadDiag& getEepromLoadDiag() {
  return gLoadDiag;
}

TunableConfig getCurrentConfig() {
  TunableConfig cfg = {};
  for (const TunableDescriptor* it = tunableRegistryBegin(); it != tunableRegistryEnd(); ++it) {
    it->writeToConfig(cfg);
  }
  cfg.seq = currentSeq;
  return cfg;
}

void applyConfig(const TunableConfig &cfg) {
  for (const TunableDescriptor* it = tunableRegistryBegin(); it != tunableRegistryEnd(); ++it) {
    it->applyFromConfig(cfg);
  }

  Tunables::normalizePpsTunables();
}

bool loadConfig(TunableConfig &out) {
  const DecodedRecord a = readRecordAt(EEPROM_SLOT_NANO_A_ADDR);
  const DecodedRecord b = readRecordAt(EEPROM_SLOT_NANO_B_ADDR);

  gLoadDiag.source = 'D';
  gLoadDiag.slotA = a.code;
  gLoadDiag.slotB = b.code;
  gLoadDiag.schema = EEPROM_CONFIG_VERSION_CURRENT;
  gLoadDiag.sequence = 0U;
  gLoadDiag.hasSequence = false;

  if (a.valid && !b.valid) {
    out = a.cfg;
    currentSeq = a.seq;
    gLoadDiag.source = 'A';
    gLoadDiag.sequence = a.seq;
    gLoadDiag.hasSequence = true;
    return true;
  }
  if (!a.valid && b.valid) {
    out = b.cfg;
    currentSeq = b.seq;
    gLoadDiag.source = 'B';
    gLoadDiag.sequence = b.seq;
    gLoadDiag.hasSequence = true;
    return true;
  }
  if (a.valid && b.valid) {
    if (isSeqNewer(b.seq, a.seq)) {
      out = b.cfg;
      currentSeq = b.seq;
      gLoadDiag.source = 'B';
      gLoadDiag.sequence = b.seq;
    } else {
      out = a.cfg;
      currentSeq = a.seq;
      gLoadDiag.source = 'A';
      gLoadDiag.sequence = a.seq;
    }
    gLoadDiag.hasSequence = true;
    return true;
  }

  currentSeq = 0U;
  return false;
}

void saveConfig(TunableConfig cfg) {
  uint8_t payload[EEPROM_PAYLOAD_SIZE];
  encodePayload(cfg, payload);
  if (!validateSemantics(cfg)) {
    return;
  }

  const DecodedRecord a = readRecordAt(EEPROM_SLOT_NANO_A_ADDR);
  const DecodedRecord b = readRecordAt(EEPROM_SLOT_NANO_B_ADDR);
  const uint8_t addr = pickSaveAddr(a.valid, a.seq, b.valid, b.seq);

  const uint32_t baseSeq = (a.valid && b.valid) ? (isSeqNewer(a.seq, b.seq) ? a.seq : b.seq)
                        : (a.valid ? a.seq : (b.valid ? b.seq : currentSeq));
  const uint32_t nextSeq = baseSeq + 1U;

  uint8_t header[EEPROM_HEADER_SIZE] = {};
  writeU16LE(header + HEADER_OFF_MAGIC, EEPROM_RECORD_MAGIC);
  header[HEADER_OFF_SCHEMA] = EEPROM_CONFIG_VERSION_CURRENT;
  header[HEADER_OFF_PAYLOAD_LEN] = EEPROM_PAYLOAD_SIZE;
  writeU16LE(header + HEADER_OFF_LAYOUT_ID, EEPROM_LAYOUT_ID);
  writeU32LE(header + HEADER_OFF_SEQ, nextSeq);
  writeU16LE(header + HEADER_OFF_PAYLOAD_CRC, computeCRC16(payload, EEPROM_PAYLOAD_SIZE));
  header[HEADER_OFF_COMMITTED] = EEPROM_UNCOMMITTED_MARKER;
  header[HEADER_OFF_RESERVED0] = 0U;
  header[HEADER_OFF_RESERVED1] = 0U;
  header[HEADER_OFF_RESERVED2] = 0U;

  // Transactional write order: uncommitted header, payload, committed marker last.
  eepromUpdateBytes(addr, header, EEPROM_HEADER_SIZE);
  eepromUpdateBytes((uint8_t)(addr + EEPROM_HEADER_SIZE), payload, EEPROM_PAYLOAD_SIZE);
  EEPROM.update((int)addr + (int)HEADER_OFF_COMMITTED, EEPROM_COMMITTED_MARKER);

  currentSeq = nextSeq;
}
