#include "Config.h"

#include <Arduino.h>
#include <util/atomic.h>
#include <string.h>
#include <stdlib.h>

#include "StringCase.h"
#include "PendulumProtocol.h"
#include "SerialParser.h"
#include "PendulumCore.h"
#include "TunableCommands.h"
#include "SerialHelp.h"

constexpr size_t CMD_BUFFER_SIZE = 64;      // serial command buffer length
// Buffer for accumulating one incoming command line.
static char cmdBuf[CMD_BUFFER_SIZE];
static uint8_t cmdIdx = 0;
static bool cmdOverflowed = false;
static bool headerPending = false;
// Central non-ISR formatting scratch buffer ownership:
// - SerialParser main loop formatter: owner id 1 (SerialParser)
// - Boot/status telemetry formatter: owner id 2 (StatusTelemetry)
// - PendulumCore telemetry formatter: owner id 3 (PendulumCore)
// - Memory telemetry formatter: owner id 4 (MemoryTelemetry)
// These contexts run on the main loop and may interleave, so ownership is
// arbitrated with tryAcquireFormatBuffer()/releaseFormatBuffer().
static char sharedFormatBuf[CSV_LINE_MAX];
static volatile uint8_t sharedFormatBufOwner = 0;
static volatile uint32_t s_formatAcquireFailures = 0;
static volatile uint32_t s_requiredFormatAcquireFailures = 0;
static volatile uint32_t s_queueRejectsInvalidArgs = 0;
static volatile uint32_t s_txReentryDrops = 0;
static volatile uint32_t s_requiredDrops = 0;

namespace {

bool appendChar(char* out, size_t outLen, size_t& pos, char c) {
  if (!out || outLen == 0 || pos + 1 >= outLen) return false;
  out[pos++] = c;
  out[pos] = '\0';
  return true;
}

bool appendCStr(char* out, size_t outLen, size_t& pos, const char* s) {
  if (!out || !s || outLen == 0) return false;
  while (*s) {
    if (pos + 1 >= outLen) return false;
    out[pos++] = *s++;
  }
  out[pos] = '\0';
  return true;
}

bool u64ToDec(char* out, size_t outLen, uint64_t v) {
  if (!out || outLen < 2U) return false;

  char rev[21];  // max uint64_t: 20 digits + NUL
  size_t n = 0;
  do {
    rev[n++] = static_cast<char>('0' + (v % 10ULL));
    v /= 10ULL;
  } while (v != 0ULL && n < sizeof(rev));

  if (n + 1U > outLen) return false;
  for (size_t i = 0; i < n; ++i) {
    out[i] = rev[n - 1U - i];
  }
  out[n] = '\0';
  return true;
}

bool appendU64(char* out, size_t outLen, size_t& pos, uint64_t v) {
  char numBuf[21];
  if (!u64ToDec(numBuf, sizeof(numBuf), v)) return false;
  return appendCStr(out, outLen, pos, numBuf);
}

bool appendU32(char* out, size_t outLen, size_t& pos, uint32_t v) {
  return appendU64(out, outLen, pos, static_cast<uint64_t>(v));
}

bool appendFieldSep(char* out, size_t outLen, size_t& pos) {
  return appendChar(out, outLen, pos, ',');
}

char* tryAcquireFormatBufferInternal(FormatBufferOwner owner, EmissionReliability reliability) {
  if (owner == static_cast<FormatBufferOwner>(0)) return nullptr;

  char* buf = nullptr;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (sharedFormatBufOwner == 0U) {
      sharedFormatBufOwner = static_cast<uint8_t>(owner);
      buf = sharedFormatBuf;
    } else {
      s_formatAcquireFailures++;
      if (reliability == EmissionReliability::Required) {
        s_requiredFormatAcquireFailures++;
      }
    }
  }
  if (buf) {
    memset(buf, 0, sizeof(sharedFormatBuf));
  }
  return buf;
}
} // namespace

char* tryAcquireFormatBuffer(FormatBufferOwner owner) {
  return tryAcquireFormatBufferInternal(owner, EmissionReliability::BestEffort);
}

void releaseFormatBuffer(FormatBufferOwner owner) {
  const uint8_t expected = static_cast<uint8_t>(owner);
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (sharedFormatBufOwner == expected) {
      sharedFormatBufOwner = 0U;
    }
  }
}

void processSerialCommands() {
  auto resetCommandState = []() {
    cmdIdx = 0;
    cmdOverflowed = false;
  };

  while (CMD_SERIAL.available()) {
    char c = CMD_SERIAL.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (cmdOverflowed) {
        CMD_SERIAL.println(F("ERROR: command too long"));
        sendStatus(StatusCode::InvalidValue, "command too long");
        resetCommandState();
        continue;
      }
      cmdBuf[cmdIdx] = '\0';
      char *save;
      char *token = strtok_r(cmdBuf, " ", &save);
      if (token) {
        bool stopProcessingCommand = false;
        auto rejectExtraArgs = [&](const __FlashStringHelper* message, const char* statusDetail) {
          if (strtok_r(NULL, " ", &save)) {
            CMD_SERIAL.println(message);
            sendStatus(StatusCode::InvalidParam, statusDetail);
            return true;
          }
          return false;
        };
        if (equalsIgnoreCaseAscii(token, CMD_HELP) || strcmp(token, "?") == 0) {
          char* arg1 = strtok_r(NULL, " ", &save);
          if (arg1 && rejectExtraArgs(F("ERROR: help takes at most one argument"),
                                      "help takes at most one argument")) {
            stopProcessingCommand = true;
          }
          if (!stopProcessingCommand) {
            handleHelp(arg1);
          }
        } else if (equalsIgnoreCaseAscii(token, CMD_GET)) {
          char *name = strtok_r(NULL, " ", &save);
          if (!name) {
            handleGetCommand(name);
            stopProcessingCommand = true;
          }
          if (!stopProcessingCommand &&
              rejectExtraArgs(F("ERROR: get requires exactly one parameter"),
                              "get requires exactly one parameter")) {
            stopProcessingCommand = true;
          }
          if (!stopProcessingCommand) {
            handleGetCommand(name);
          }
        } else if (equalsIgnoreCaseAscii(token, CMD_SET)) {
#if CLI_ALLOW_MUTATIONS
          char *name = strtok_r(NULL, " ", &save);
          char *val  = strtok_r(NULL, " ", &save);
          if (name && val &&
              rejectExtraArgs(F("ERROR: set requires exactly <param> <value>"),
                              "set requires exactly <param> <value>")) {
            stopProcessingCommand = true;
          }
          if (!stopProcessingCommand) {
            handleSetCommand(name, val, headerPending);
          }
#else
          CMD_SERIAL.println(F("ERROR: set is disabled by build policy"));
          sendStatus(StatusCode::InvalidParam, "set disabled by build policy");
#endif
        } else if (equalsIgnoreCaseAscii(token, CMD_RESET)) {
#if CLI_ALLOW_MUTATIONS
          char *action = strtok_r(NULL, " ", &save);
          if (action &&
              rejectExtraArgs(F("ERROR: reset requires exactly one action"),
                              "reset requires exactly one action")) {
            stopProcessingCommand = true;
          }
          if (!stopProcessingCommand) {
            handleResetCommand(action, headerPending);
          }
#else
          CMD_SERIAL.println(F("ERROR: reset is disabled by build policy"));
          sendStatus(StatusCode::InvalidParam, "reset disabled by build policy");
#endif
        } else if (equalsIgnoreCaseAscii(token, CMD_EMIT)) {
          char *sub = strtok_r(NULL, " ", &save);
          if (!sub) {
            CMD_SERIAL.println(F("ERROR: emit requires subcommand"));
            sendStatus(StatusCode::InvalidParam, "emit requires subcommand");
          } else if (!equalsIgnoreCaseAscii(sub, CMD_EMIT_META) &&
                     !equalsIgnoreCaseAscii(sub, CMD_EMIT_STARTUP)) {
            CMD_SERIAL.print(F("ERROR: unknown emit subcommand: "));
            CMD_SERIAL.println(sub);
            sendStatus(StatusCode::UnknownCommand, sub);
          } else {
            char *extra = strtok_r(NULL, " ", &save);
            if (extra) {
              CMD_SERIAL.println(F("ERROR: emit <meta|startup> takes no arguments"));
              sendStatus(StatusCode::InvalidParam, "emit <meta|startup> takes no arguments");
            } else {
              sendStatus(StatusCode::Ok,
                         equalsIgnoreCaseAscii(sub, CMD_EMIT_META) ? STS_EMIT_META : STS_EMIT_STARTUP);
              if (equalsIgnoreCaseAscii(sub, CMD_EMIT_META)) {
                emitMetadataNow();
              } else {
                emitStartupNow();
              }
            }
          }
        } else {
          CMD_SERIAL.println(F("ERROR: unknown command"));
          sendStatus(StatusCode::UnknownCommand, token);
        }
      }
      resetCommandState();
    } else if (cmdIdx < sizeof(cmdBuf)-1) {
      cmdBuf[cmdIdx++] = c;
    } else {
      cmdOverflowed = true;
    }
  }
}

namespace {

#if LED_ACTIVITY_ENABLE
  volatile uint8_t* s_ledOut = nullptr;
  uint8_t s_ledMask = 0;

  void initActivityLed() {
    if (s_ledOut) return;
    pinMode(LED_BUILTIN, OUTPUT);
    const uint8_t port = digitalPinToPort(LED_BUILTIN);
    s_ledMask = digitalPinToBitMask(LED_BUILTIN);
    s_ledOut = portOutputRegister(port);
  }

  inline void toggleActivityLed() {
    if (!s_ledOut) initActivityLed();
    if (s_ledOut) {
      *s_ledOut ^= s_ledMask;
    }
  }
#endif

} // namespace

// CONTRACT: Foreground-only API.
// queueCSVLine() must be called from main-loop/task context, never from an ISR.
// If ISR emission is needed, queue data in ISR and flush from foreground code.
bool queueCSVLine(const char* buf, int len, EmissionReliability reliability) {
  if (len <= 0 || !buf || len >= (int)CSV_LINE_MAX) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      s_queueRejectsInvalidArgs++;
      if (reliability == EmissionReliability::Required) {
        s_requiredDrops++;
      }
    }
    return false; // fail-closed: never emit truncated protocol lines
  }

#if defined(__AVR__) && defined(SREG) && defined(SREG_I)
  // Runtime hardening: on AVR, ISRs run with global interrupts disabled.
  // Reject calls when the I-bit is clear to prevent ISR/main reentry races.
  if ((SREG & _BV(SREG_I)) == 0U) {
    if (reliability == EmissionReliability::Required) {
      ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { s_requiredDrops++; }
    }
    return false;
  }
#endif

  // Atomic guard protects against accidental ISR/foreground or nested-entry races.
  static volatile uint8_t txGuard = 0U;
  bool guardAcquired = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (txGuard != 0U) {
      s_txReentryDrops++;
    } else {
      txGuard = 1U;
      guardAcquired = true;
    }
  }
  if (!guardAcquired) {
    if (reliability == EmissionReliability::Required) {
      ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { s_requiredDrops++; }
    }
    return false;
  }

  const int payloadLen = len;
  size_t writeLen = 0;
  size_t written = 0;

  do {
    writeLen = (size_t)payloadLen;
    written = DATA_SERIAL.write((const uint8_t*)buf, writeLen);

    bool needsNewline = true;
    if (payloadLen > 0 && buf[payloadLen - 1] == '\n') {
      needsNewline = false;
    }
    if (needsNewline) {
      const uint8_t nl = '\n';
      written += DATA_SERIAL.write(&nl, 1);
      writeLen += 1;
    }
  } while (false);

#if LED_ACTIVITY_ENABLE
  static_assert(LED_ACTIVITY_DIV > 0, "LED_ACTIVITY_DIV must be greater than 0");
  static_assert((LED_ACTIVITY_DIV & (LED_ACTIVITY_DIV - 1)) == 0,
                "LED_ACTIVITY_DIV must be a power of two");

  if (written == writeLen) {
    static uint8_t ledActivityCounter = 0;
    if (((++ledActivityCounter) & (LED_ACTIVITY_DIV - 1U)) == 0U) {
      toggleActivityLed();
    }
  }
#endif

  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    txGuard = 0U;
  }
  if (written != writeLen && reliability == EmissionReliability::Required) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { s_requiredDrops++; }
  }
  return written == writeLen;
}

uint32_t serialFormatAcquireFailures() {
  uint32_t value = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    value = s_formatAcquireFailures;
  }
  return value;
}

uint32_t serialRequiredFormatAcquireFailures() {
  uint32_t value = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    value = s_requiredFormatAcquireFailures;
  }
  return value;
}

uint32_t serialQueueRejectsInvalidArgs() {
  uint32_t value = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    value = s_queueRejectsInvalidArgs;
  }
  return value;
}

uint32_t serialTxReentryDrops() {
  uint32_t value = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    value = s_txReentryDrops;
  }
  return value;
}
uint32_t serialRequiredDrops() {
  uint32_t value = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    value = s_requiredDrops;
  }
  return value;
}
void sendTaggedCsvLine(const char* tag, const char* text) {
  if (!tag) return;
  if (!text) text = "";
  char* lineBuf = tryAcquireFormatBuffer(FormatBufferOwner::SerialParser);
  if (!lineBuf) return;

  int len = snprintf(lineBuf, CSV_LINE_MAX, "%s,%s\n", tag, text);
  if (len <= 0 || len >= (int)CSV_LINE_MAX) {
    releaseFormatBuffer(FormatBufferOwner::SerialParser);
    return;
  }
  queueCSVLine(lineBuf, len);
  releaseFormatBuffer(FormatBufferOwner::SerialParser);
}

void sendStatusFromOwnedBuffer(FormatBufferOwner owner, StatusCode code, char* textBuf, EmissionReliability reliability) {
  if (!textBuf) return;
  if (textBuf != sharedFormatBuf) return;
  const uint8_t expectedOwner = static_cast<uint8_t>(owner);
  if (expectedOwner == 0U || sharedFormatBufOwner != expectedOwner) return;

  const char* codeStr = statusCodeToStr(code);
  const size_t textLen = strnlen(textBuf, CSV_PAYLOAD_MAX);
  if (textLen >= CSV_PAYLOAD_MAX) return;

  char prefix[CSV_STS_WRAP_MAX];
  const int prefixLen = snprintf(prefix, sizeof(prefix), "%s,%s,", TAG_STS, codeStr);
  if (prefixLen <= 0 || prefixLen >= static_cast<int>(CSV_STS_WRAP_MAX)) return;

  const size_t totalLen = static_cast<size_t>(prefixLen) + textLen + 1U;
  if (totalLen >= CSV_LINE_MAX) return;

  if (textLen == 0U) {
    memcpy(textBuf, prefix, static_cast<size_t>(prefixLen));
    textBuf[prefixLen - 1] = '\n'; // replace trailing comma with newline
    textBuf[prefixLen] = '\0';
    queueCSVLine(textBuf, prefixLen, reliability);
    return;
  }

  memmove(textBuf + prefixLen, textBuf, textLen);
  memcpy(textBuf, prefix, static_cast<size_t>(prefixLen));
  textBuf[prefixLen + textLen] = '\n';
  textBuf[prefixLen + textLen + 1U] = '\0';
  queueCSVLine(textBuf, static_cast<int>(prefixLen + textLen + 1U), reliability);
}

void sendStatus(StatusCode code, const char* text, EmissionReliability reliability) {
  if (!text) text = "";
  char* lineBuf = tryAcquireFormatBufferInternal(FormatBufferOwner::SerialParser, reliability);
  if (!lineBuf) return;

  const size_t textLen = strnlen(text, CSV_PAYLOAD_MAX);
  if (textLen >= CSV_PAYLOAD_MAX) {
    releaseFormatBuffer(FormatBufferOwner::SerialParser);
    return;
  }
  memcpy(lineBuf, text, textLen);
  lineBuf[textLen] = '\0';
  sendStatusFromOwnedBuffer(FormatBufferOwner::SerialParser, code, lineBuf, reliability);
  releaseFormatBuffer(FormatBufferOwner::SerialParser);
}

void printCsvHeader() {
  char* lineBuf = nullptr;
  for (uint8_t attempt = 0; attempt < 3U && !lineBuf; ++attempt) {
    lineBuf = tryAcquireFormatBufferInternal(FormatBufferOwner::SerialParser,
                                             EmissionReliability::Required);
  }
  if (!lineBuf) return;

  // HDR_PART emission contract:
  // - emission order is deterministic by SAMPLE_SCHEMA_HDR_PARTS index (1..N on wire)
  // - payloads are semantic/readability groups, not canonical SMP serialization order
  // - hosts should reassemble/validate HDR_PART fields, then treat SAMPLE_SCHEMA
  //   as the only authoritative canonical ordering
  char schemaPartBuf[HDR_PART_MAX_PAYLOAD_LEN + 1U];
  for (uint8_t i = 0; i < HDR_SEGMENTED_PART_COUNT; ++i) {
    const char* schemaPart = flashStrAt(SAMPLE_SCHEMA_HDR_PARTS, i);
    const size_t schemaPartLen = flashStrLen(schemaPart);
    if (schemaPartLen == 0 || schemaPartLen >= sizeof(schemaPartBuf)) {
      releaseFormatBuffer(FormatBufferOwner::SerialParser);
      return;
    }
    copyFlashToRam(schemaPartBuf, schemaPart, schemaPartLen);
    schemaPartBuf[schemaPartLen] = '\0';

    int len = snprintf(lineBuf,
                       CSV_LINE_MAX,
                       "%s,%u,%u,%s\n",
                       TAG_HDR_PART,
                       static_cast<unsigned int>(i + 1U),
                       static_cast<unsigned int>(HDR_SEGMENTED_PART_COUNT),
                       schemaPartBuf);
    if (len <= 0 || len >= static_cast<int>(CSV_LINE_MAX)) {
      releaseFormatBuffer(FormatBufferOwner::SerialParser);
      return;
    }
    queueCSVLine(lineBuf, len, EmissionReliability::Required);
  }
  headerPending = false;
  releaseFormatBuffer(FormatBufferOwner::SerialParser);
}

void sendSample(const PendulumSample &s) {
  if (headerPending) {
    printCsvHeader();
  }

  char* lineBuf = tryAcquireFormatBuffer(FormatBufferOwner::SerialParser);
  if (!lineBuf) return;

  size_t pos = 0;
  uint8_t emittedFieldCount = 0;
  bool ok = true;
  ok = ok && appendCStr(lineBuf, CSV_LINE_MAX, pos, TAG_SMP);
  auto appendU32Field = [&](uint32_t v) {
    const bool fieldOk = appendFieldSep(lineBuf, CSV_LINE_MAX, pos) &&
                         appendU32(lineBuf, CSV_LINE_MAX, pos, v);
    ok = ok && fieldOk;
    if (fieldOk) ++emittedFieldCount;
  };
  auto appendU64Field = [&](uint64_t v) {
    const bool fieldOk = appendFieldSep(lineBuf, CSV_LINE_MAX, pos) &&
                         appendU64(lineBuf, CSV_LINE_MAX, pos, v);
    ok = ok && fieldOk;
    if (fieldOk) ++emittedFieldCount;
  };
  auto appendMaybeTagField = [&](uint64_t tag) {
#if SAMPLE_DIAGNOSTIC_DETAIL == 1
    // Reduced detail profile: keep canonical schema width/positions stable by
    // zero-filling provenance tag columns.
    appendU64Field(0);
#else
    appendU64Field(tag);
#endif
  };

  // SMP rows must follow canonical SAMPLE_SCHEMA / CsvField order exactly.
  static_assert(CF_COUNT == 34, "Update sendSample() ordering for schema changes");

  // CF_TICK .. CF_TICK_TOTAL_END_TAG
  appendU32Field(s.tick);
  appendU32Field(s.tick_adj);
  appendMaybeTagField(s.tick_start_tag);
  appendMaybeTagField(s.tick_end_tag);
  appendU32Field(s.tick_block);
  appendU32Field(s.tick_block_adj);
  appendMaybeTagField(s.tick_block_start_tag);
  appendMaybeTagField(s.tick_block_end_tag);
  appendU32Field(s.tick_total_adj_direct);
  appendU32Field(s.tick_total_adj_diag);
  appendMaybeTagField(s.tick_total_start_tag);
  appendMaybeTagField(s.tick_total_end_tag);

  // CF_TOCK .. CF_TOCK_TOTAL_END_TAG
  appendU32Field(s.tock);
  appendU32Field(s.tock_adj);
  appendMaybeTagField(s.tock_start_tag);
  appendMaybeTagField(s.tock_end_tag);
  appendU32Field(s.tock_block);
  appendU32Field(s.tock_block_adj);
  appendMaybeTagField(s.tock_block_start_tag);
  appendMaybeTagField(s.tock_block_end_tag);
  appendU32Field(s.tock_total_adj_direct);
  appendU32Field(s.tock_total_adj_diag);
  appendMaybeTagField(s.tock_total_start_tag);
  appendMaybeTagField(s.tock_total_end_tag);

  // CF_TICK_TOTAL_F_HAT_HZ .. CF_PPS_SEQ_ROW.
  appendU32Field(s.tick_total_f_hat_hz);
  appendU32Field(s.tock_total_f_hat_hz);
  appendU32Field(s.gps_status);
  appendU32Field(s.holdover_age_ms);
  appendU32Field(s.r_ppm);
  appendU32Field(s.j_ticks);
  appendU32Field(s.dropped_events);
  appendU32Field(s.adj_diag);
  appendU32Field(s.adj_comp_diag);
  appendU32Field(s.pps_seq_row);

  if (emittedFieldCount != CF_REQUIRED_COUNT) {
    releaseFormatBuffer(FormatBufferOwner::SerialParser);
    sendStatus(StatusCode::InternalError, "smp field_count mismatch");
    return;
  }

  ok = ok && appendChar(lineBuf, CSV_LINE_MAX, pos, '\n');
  const int len = static_cast<int>(pos);
  if (!ok || len <= 0 || len >= (int)CSV_LINE_MAX) {
    releaseFormatBuffer(FormatBufferOwner::SerialParser);
    return;
  }
  queueCSVLine(lineBuf, len);
  releaseFormatBuffer(FormatBufferOwner::SerialParser);
}
