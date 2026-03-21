#include "Config.h"

#include <Arduino.h>
#include <avr/pgmspace.h>
// Fallback for cores that don’t define FPSTR
#ifndef FPSTR
  #define FPSTR(p) (reinterpret_cast<const __FlashStringHelper *>(p))
#endif
#include <string.h>
#include <stdlib.h>

#include "StringCase.h"
#include "PendulumProtocol.h"
#include "SerialParser.h"
#include "AtomicUtils.h"
#include "PlatformTime.h"
#include "PendulumCapture.h"
#include "TunableCommands.h"
#include "TunableRegistry.h"

// === HELP REGISTRY & HANDLERS ==============================================
namespace {

  struct CmdHelp {
    const char* name_P;     // PROGMEM
    const char* synopsis_P; // PROGMEM
    const char* usage_P;    // PROGMEM
    const char* category_P; // PROGMEM
  };

  // Categories
  const char CAT_core[]     PROGMEM = "core";
  const char CAT_tunables[] PROGMEM = "tunables";

  // Command text
  const char H_name[]     PROGMEM = "help";
  const char H_syn[]      PROGMEM = "Show help for commands or tunables";
  const char H_use[]      PROGMEM = "help [<command>|tunables]";

  const char S_name[]     PROGMEM = "stats";
  const char S_syn[]      PROGMEM = "Print running metrics";
  const char S_use[]      PROGMEM = "stats";

  const char G_name[]     PROGMEM = "get";
  const char G_syn[]      PROGMEM = "Read a tunable";
  const char G_use[]      PROGMEM = "get <param>";

  const char SET_name[]   PROGMEM = "set";
  const char SET_syn[]    PROGMEM = "Set a tunable";
  const char SET_use[]    PROGMEM = "set <param> <value>";

  // Registry (single source of truth)
  const CmdHelp HELP_REGISTRY[] PROGMEM = {
    { H_name,   H_syn,   H_use,   CAT_core     },
    { S_name,   S_syn,   S_use,   CAT_core     },
    { G_name,   G_syn,   G_use,   CAT_tunables },
    { SET_name, SET_syn, SET_use, CAT_tunables },
  };
  constexpr uint8_t HELP_N = sizeof(HELP_REGISTRY) / sizeof(HELP_REGISTRY[0]);
  constexpr uint8_t MAX_HELP_SUGGESTIONS = 5; // cap on similar command hints

  // PROGMEM printing helpers
  inline void print_P (const char* p)   { CMD_SERIAL.print(FPSTR(p)); }
  inline void println_P(const char* p)  { CMD_SERIAL.println(FPSTR(p)); }

  // Case-insensitive compare RAM vs PROGMEM
  bool equals_ci_P(const char* ram, const char* pgm) {
    if (!ram) return false;
    while (true) {
      char a = *ram++;
      char b = pgm_read_byte(pgm++);
      if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
      if (b >= 'A' && b <= 'Z') b = (char)(b - 'A' + 'a');
      if (a != b) return false;
      if (a == '\0') return true;
    }
  }

  // Prefix match RAM vs PROGMEM (for suggestions)
  bool starts_with_ci_P(const char* ram, const char* pgm) {
    if (!ram) return false;
    while (*ram) {
      char a = *ram++;
      char b = pgm_read_byte(pgm++);
      if (b == '\0') return false;
      if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
      if (b >= 'A' && b <= 'Z') b = (char)(b - 'A' + 'a');
      if (a != b) return false;
    }
    return true;
  }

  void read_entry(uint8_t i, CmdHelp& out) { memcpy_P(&out, &HELP_REGISTRY[i], sizeof(out)); }

  void list_commands() {
    CMD_SERIAL.println(F("Commands: name – synopsis"));
    for (uint8_t i = 0; i < HELP_N; ++i) {
      CmdHelp e; read_entry(i, e);
      CMD_SERIAL.print(F("  ")); print_P(e.name_P);
      CMD_SERIAL.print(F(" – ")); println_P(e.synopsis_P);
    }
    CMD_SERIAL.println(F("Tip: 'help <command>' or 'help tunables'"));
  }

  bool detail_for(const char* name) {
    for (uint8_t i = 0; i < HELP_N; ++i) {
      CmdHelp e; read_entry(i, e);
      if (equals_ci_P(name, e.name_P)) {
        CMD_SERIAL.print(F("name : "));  println_P(e.name_P);
        CMD_SERIAL.print(F("usage: "));  println_P(e.usage_P);
        CMD_SERIAL.print(F("desc : "));  println_P(e.synopsis_P);
        CMD_SERIAL.print(F("cat  : "));  println_P(e.category_P);
        return true;
      }
    }
    return false;
  }

  void suggest_similar(const char* name) {
    CMD_SERIAL.println(F("Did you mean:"));
    uint8_t shown = 0;
    for (uint8_t i = 0; i < HELP_N; ++i) {
      CmdHelp e; read_entry(i, e);
      if (starts_with_ci_P(name, e.name_P)) {
        CMD_SERIAL.print(F("  ")); println_P(e.name_P);
        if (++shown >= MAX_HELP_SUGGESTIONS) break; // stop after MAX_HELP_SUGGESTIONS matches
      }
    }
    if (!shown) CMD_SERIAL.println(F("  (no close matches)"));
  }

  void show_tunables() {
    CMD_SERIAL.println(F("Tunables (current / example usage)"));
    for (const TunableDescriptor* it = tunableRegistryBegin(); it != tunableRegistryEnd(); ++it) {
      CMD_SERIAL.print(F("  "));
      CMD_SERIAL.print(it->cliName);
      CMD_SERIAL.print(F(" ["));
      CMD_SERIAL.print(tunableTypeName(it->type));
      CMD_SERIAL.print(F("]: "));
      it->printCurrent(CMD_SERIAL);
      CMD_SERIAL.print(F("    e.g. `"));
      CMD_SERIAL.print(it->exampleText);
      CMD_SERIAL.print(F("`"));
      if (it->helpText && *it->helpText) {
        CMD_SERIAL.print(F(" ("));
        CMD_SERIAL.print(it->helpText);
        if (it->validationText && *it->validationText) {
          CMD_SERIAL.print(F("; "));
          CMD_SERIAL.print(it->validationText);
        }
        CMD_SERIAL.print(F(")"));
      } else if (it->validationText && *it->validationText) {
        CMD_SERIAL.print(F(" ("));
        CMD_SERIAL.print(it->validationText);
        CMD_SERIAL.print(F(")"));
      }
      CMD_SERIAL.println();
    }
  }

  // was: void handleHelp(const char* arg1) { ... }
  void helpImpl(const char* arg1) {
    if (!arg1 || *arg1 == '\0') { list_commands(); return; }
    if (equalsIgnoreCaseAscii(arg1, "tunables")) { show_tunables(); return; }
    if (!detail_for(arg1)) {
      CMD_SERIAL.print(F("No such command: ")); CMD_SERIAL.println(arg1);
      suggest_similar(arg1);
    }
  }
} // anon namespace
// ========================================================================

// Global symbol declared in SerialParser.h
void handleHelp(const char* arg1) {
  helpImpl(arg1);
}

constexpr size_t CMD_BUFFER_SIZE = 64;      // serial command buffer length
// Buffer for accumulating incoming command characters (CMD_BUFFER_SIZE)
static char cmdBuf[CMD_BUFFER_SIZE];
static uint8_t cmdIdx = 0;
static char lineBuf[CSV_LINE_MAX];
static bool headerPending = false;

#if ENABLE_METRICS
volatile uint8_t  maxFill = 0;
volatile uint32_t stsPayloadTrunc = 0;
volatile uint32_t csvLineTrunc = 0;
volatile uint32_t serialTrunc = 0;
#endif

void processSerialCommands() {
  while (CMD_SERIAL.available()) {
    char c = CMD_SERIAL.read();
    if (c == '\r') continue;
    if (c == '\n') {
      cmdBuf[cmdIdx] = '\0';
      char *save;
      char *token = strtok_r(cmdBuf, " ", &save);
      if (token) {
        if (equalsIgnoreCaseAscii(token, CMD_HELP) || strcmp(token, "?") == 0) {
          char* arg1 = strtok_r(NULL, " ", &save);
          handleHelp(arg1);
        } else if (equalsIgnoreCaseAscii(token, CMD_STATS)) {
          reportMetrics();
        } else if (equalsIgnoreCaseAscii(token, CMD_GET)) {
          char *name = strtok_r(NULL, " ", &save);
          handleGetCommand(name);
        } else if (equalsIgnoreCaseAscii(token, CMD_SET)) {
          char *name = strtok_r(NULL, " ", &save);
          char *val  = strtok_r(NULL, " ", &save);
          handleSetCommand(name, val, headerPending);
        } else {
          CMD_SERIAL.println(F("ERROR: unknown command"));
          sendStatus(StatusCode::UnknownCommand, token);
        }
      }
      cmdIdx = 0;
    } else if (cmdIdx < sizeof(cmdBuf)-1) {
      cmdBuf[cmdIdx++] = c;
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

void queueCSVLine(const char* buf, int len) {
  if (len <= 0 || !buf) return;

  static bool txInProgress = false;
  if (txInProgress) return;
  txInProgress = true;

  int payloadLen = len;
  if (payloadLen >= (int)CSV_LINE_MAX) {
    payloadLen = (int)CSV_LINE_MAX - 1;
#if ENABLE_METRICS
    csvLineTrunc++;
#endif
  }

  if (payloadLen < len) {
    const size_t payloadLenBounded = strnlen(buf, (size_t)payloadLen);
    payloadLen = (int)payloadLenBounded;
  }

  size_t writeLen = (size_t)payloadLen;
  size_t written = DATA_SERIAL.write((const uint8_t*)buf, writeLen);

  bool needsNewline = true;
  if (payloadLen > 0 && buf[payloadLen - 1] == '\n') {
    needsNewline = false;
  }
  if (needsNewline) {
    const uint8_t nl = '\n';
    written += DATA_SERIAL.write(&nl, 1);
    writeLen += 1;
  }

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

#if ENABLE_METRICS
  if (written != writeLen) serialTrunc++;
#endif
  txInProgress = false;
}
void sendStatus(StatusCode code, const char* text) {
  const char* codeStr = statusCodeToStr(code);
  if (!text) text = "";

  char payloadBuf[CSV_PAYLOAD_MAX];
  int payloadLen = snprintf(payloadBuf, sizeof(payloadBuf), "%s", text);
#if ENABLE_METRICS
  if (payloadLen >= (int)sizeof(payloadBuf)) stsPayloadTrunc++;
#endif

  int len = snprintf(lineBuf, CSV_LINE_MAX, "%s,%s,%s\n", TAG_STS, codeStr, payloadBuf);
  queueCSVLine(lineBuf, len);
}

void printCsvHeader() {
  DATA_SERIAL.flush();
  switch (Tunables::dataUnits) {
    case DataUnits::RawCycles: {
      const char* fields = "tick_cycles,tock_cycles,tick_block_cycles,tock_block_cycles,corr_inst_ppm,corr_blend_ppm,gps_status,dropped_events";
      int len = snprintf(lineBuf, CSV_LINE_MAX, "%s,%s\n", TAG_HDR, fields);
      queueCSVLine(lineBuf, len);
      break;
    }
    case DataUnits::AdjustedMs: {
      const char* fields = "tick_ms,tock_ms,tick_block_ms,tock_block_ms,corr_inst_ppm,corr_blend_ppm,gps_status,dropped_events";
      int len = snprintf(lineBuf, CSV_LINE_MAX, "%s,%s\n", TAG_HDR, fields);
      queueCSVLine(lineBuf, len);
      break;
    }
    case DataUnits::AdjustedUs: {
      const char* fields = "tick_us,tock_us,tick_block_us,tock_block_us,corr_inst_ppm,corr_blend_ppm,gps_status,dropped_events";
      int len = snprintf(lineBuf, CSV_LINE_MAX, "%s,%s\n", TAG_HDR, fields);
      queueCSVLine(lineBuf, len);
      break;
    }
    case DataUnits::AdjustedNs: {
      const char* fields = "tick_ns,tock_ns,tick_block_ns,tock_block_ns,corr_inst_ppm,corr_blend_ppm,gps_status,dropped_events";
      int len = snprintf(lineBuf, CSV_LINE_MAX, "%s,%s\n", TAG_HDR, fields);
      queueCSVLine(lineBuf, len);
      break;
    }
  }
  headerPending = false;
  DATA_SERIAL.flush();
}

void sendSample(const PendulumSample &s) {
  if (headerPending) {
    printCsvHeader();
  }

  int len = snprintf(lineBuf, CSV_LINE_MAX,
    "%s,%lu,%lu,%lu,%lu,%ld,%ld,%u,%u\n",
    dataUnitsTag(Tunables::dataUnits),
    (unsigned long)s.tick,
    (unsigned long)s.tock,
    (unsigned long)s.tick_block,
    (unsigned long)s.tock_block,
    (long)s.corr_inst_ppm,
    (long)s.corr_blend_ppm,
    (unsigned int)s.gps_status,
    (unsigned int)s.dropped_events);
  queueCSVLine(lineBuf, len);
}

void reportMetrics() {
#if ENABLE_METRICS
  static uint32_t lastMetricsMs = 0;
  uint32_t nowMs = platformMillis();
  if (nowMs - lastMetricsMs >= METRICS_PERIOD_MS) {
    lastMetricsMs = nowMs;
    uint32_t dropped = captureDroppedEvents();
    char msg[96];
    snprintf(msg, sizeof(msg), "fill=%u,drop=%lu,serTrunc=%lu,payTrunc=%lu,csvTrunc=%lu",
             maxFill,
             (unsigned long)dropped,
             (unsigned long)serialTrunc,
             (unsigned long)stsPayloadTrunc,
             (unsigned long)csvLineTrunc);
    sendStatus(StatusCode::ProgressUpdate, msg);
    serialTrunc = 0;
    stsPayloadTrunc = 0;
    csvLineTrunc = 0;
    maxFill = 0;
  }
#endif
}
