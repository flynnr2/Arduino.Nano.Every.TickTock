#include "Config.h"

#include <Arduino.h>
#include <avr/pgmspace.h>
// Fallback for cores that don’t define FPSTR
#ifndef FPSTR
  #define FPSTR(p) (reinterpret_cast<const __FlashStringHelper *>(p))
#endif
#include <string.h>
#include <stdlib.h>
#include "PendulumProtocol.h"
#include "EEPROMConfig.h"
#include "PendulumCore.h"
#include "SerialParser.h"
#include "AtomicUtils.h"

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

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_CORR_JUMP);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print(Tunables::correctionJumpThresh, 6);
    CMD_SERIAL.println(F("    e.g. `set correctionJumpThresh 0.50`"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_EMA_SHIFT);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsEmaShift);
    CMD_SERIAL.println(F("    e.g. `set ppsEmaShift 6` (higher=smoother, slower)"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_FAST_SHIFT);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsFastShift);
    CMD_SERIAL.println(F("    e.g. `set ppsFastShift 3` (lower=faster)"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_SLOW_SHIFT);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsSlowShift);
    CMD_SERIAL.println(F("    e.g. `set ppsSlowShift 8` (higher=smoother)"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_HAMPEL_WIN);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsHampelWin);
    CMD_SERIAL.println(F("    e.g. `set ppsHampelWin 7` (odd 5..9)"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_HAMPEL_KX100);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsHampelKx100);
    CMD_SERIAL.println(F("    e.g. `set ppsHampelKx100 300` (k=3.00)"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_MEDIAN3);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print(Tunables::ppsMedian3 ? 1 : 0);
    CMD_SERIAL.println(F("    e.g. `set ppsMedian3 1` (0/1)"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_BLEND_LO_PPM);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsBlendLoPpm);
    CMD_SERIAL.println(F("    e.g. `set ppsBlendLoPpm 5`"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_BLEND_HI_PPM);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsBlendHiPpm);
    CMD_SERIAL.println(F("    e.g. `set ppsBlendHiPpm 200`"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_LOCK_R_PPM);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsLockRppm);
    CMD_SERIAL.println(F("    e.g. `set ppsLockRppm 50`"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_LOCK_J_PPM);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsLockJppm);
    CMD_SERIAL.println(F("    e.g. `set ppsLockJppm 20`"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_UNLOCK_R_PPM);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsUnlockRppm);
    CMD_SERIAL.println(F("    e.g. `set ppsUnlockRppm 200`"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_UNLOCK_J_PPM);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsUnlockJppm);
    CMD_SERIAL.println(F("    e.g. `set ppsUnlockJppm 100`"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_UNLOCK_COUNT);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsUnlockCount);
    CMD_SERIAL.println(F("    e.g. `set ppsUnlockCount 3`"));

    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_PPS_HOLDOVER_MS);
    CMD_SERIAL.print(F(": ")); CMD_SERIAL.print((unsigned)Tunables::ppsHoldoverMs);
    CMD_SERIAL.println(F("    e.g. `set ppsHoldoverMs 1500`"));


    CMD_SERIAL.print(F("  ")); CMD_SERIAL.print(PARAM_DATA_UNITS);
    CMD_SERIAL.print(F(": "));
    switch (Tunables::dataUnits) {
      case DataUnits::RawCycles:  CMD_SERIAL.print(F("raw_cycles"));  break;
      case DataUnits::AdjustedMs: CMD_SERIAL.print(F("adjusted_ms")); break;
      case DataUnits::AdjustedUs: CMD_SERIAL.print(F("adjusted_us")); break;
      case DataUnits::AdjustedNs: CMD_SERIAL.print(F("adjusted_ns")); break;
      default: CMD_SERIAL.print(F("?")); break;
    }
    CMD_SERIAL.println(F("    e.g. `set dataUnits adjusted_us`"));
  }

  // was: void handleHelp(const char* arg1) { ... }
  void helpImpl(const char* arg1) {
    if (!arg1 || *arg1 == '\0') { list_commands(); return; }
    if (strcasecmp(arg1, "tunables") == 0) { show_tunables(); return; }
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
        if (strcasecmp(token, CMD_HELP) == 0 || strcmp(token, "?") == 0) {
          char* arg1 = strtok_r(NULL, " ", &save);
          handleHelp(arg1);
        } else if (strcasecmp(token, CMD_STATS) == 0) {
          reportMetrics();
        } else if (strcasecmp(token, CMD_GET) == 0) {
          char *name = strtok_r(NULL, " ", &save);
          if (name) {
            bool ok = true;
            bool isFloat = false;
            bool isString = false;
            const char* sv = nullptr;
            unsigned long v = 0;
            float fv = 0.0f;
            if      (strcasecmp(name, PARAM_CORR_JUMP) == 0)     { fv = Tunables::correctionJumpThresh; isFloat = true; }
            else if (strcasecmp(name, PARAM_PPS_EMA_SHIFT) == 0)  v = Tunables::ppsEmaShift;
            else if (strcasecmp(name, PARAM_PPS_FAST_SHIFT)   == 0) v  = Tunables::ppsFastShift;
            else if (strcasecmp(name, PARAM_PPS_SLOW_SHIFT)   == 0) v  = Tunables::ppsSlowShift;
            else if (strcasecmp(name, PARAM_PPS_HAMPEL_WIN)   == 0) v  = Tunables::ppsHampelWin;
            else if (strcasecmp(name, PARAM_PPS_HAMPEL_KX100) == 0) v  = Tunables::ppsHampelKx100;
            else if (strcasecmp(name, PARAM_PPS_MEDIAN3)      == 0) v  = (Tunables::ppsMedian3 ? 1 : 0);
            else if (strcasecmp(name, PARAM_PPS_BLEND_LO_PPM) == 0) v  = Tunables::ppsBlendLoPpm;
            else if (strcasecmp(name, PARAM_PPS_BLEND_HI_PPM) == 0) v  = Tunables::ppsBlendHiPpm;
            else if (strcasecmp(name, PARAM_PPS_LOCK_R_PPM)   == 0) v  = Tunables::ppsLockRppm;
            else if (strcasecmp(name, PARAM_PPS_LOCK_J_PPM)   == 0) v  = Tunables::ppsLockJppm;
            else if (strcasecmp(name, PARAM_PPS_UNLOCK_R_PPM) == 0) v  = Tunables::ppsUnlockRppm;
            else if (strcasecmp(name, PARAM_PPS_UNLOCK_J_PPM) == 0) v  = Tunables::ppsUnlockJppm;
            else if (strcasecmp(name, PARAM_PPS_UNLOCK_COUNT) == 0) v  = Tunables::ppsUnlockCount;
            else if (strcasecmp(name, PARAM_PPS_HOLDOVER_MS)  == 0) v  = Tunables::ppsHoldoverMs;
            else if (strcasecmp(name, PARAM_DATA_UNITS) == 0) {
              isString = true;
              switch (Tunables::dataUnits) {
                case DataUnits::RawCycles:   sv = "raw_cycles"; break;
                case DataUnits::AdjustedMs:  sv = "adjusted_ms"; break;
                case DataUnits::AdjustedUs:  sv = "adjusted_us"; break;
                case DataUnits::AdjustedNs:  sv = "adjusted_ns"; break;
              }
            } else ok = false;
            if (ok) {
              CMD_SERIAL.print("get: ");
              CMD_SERIAL.print(name);
              CMD_SERIAL.print(F(" = "));
              if (isFloat) CMD_SERIAL.println(fv, 6);
              else if (isString) CMD_SERIAL.println(sv);
              else CMD_SERIAL.println(v);
            } else {
              CMD_SERIAL.println(F("ERROR: unknown parameter"));
              sendStatus(StatusCode::InvalidParam, "unknown parameter");
            }
          } else {
            CMD_SERIAL.println(F("ERROR: get requires <param>"));
            sendStatus(StatusCode::InvalidParam, "get requires <param>");
          }
        } else if (strcasecmp(token, CMD_SET) == 0) {
          char *name = strtok_r(NULL, " ", &save);
          char *val  = strtok_r(NULL, " ", &save);
          if (name && val) {
            bool ok = true;
            bool isFloat = false;
            bool isString = false;
            unsigned long v = strtoul(val, NULL, 10);
            float fv = static_cast<float>(strtod(val, NULL));  // strof() not implemented & atof(val) less "safe"
            if      (strcasecmp(name, PARAM_CORR_JUMP) == 0)     { Tunables::correctionJumpThresh = fv; isFloat = true; }
            else if (strcasecmp(name, PARAM_PPS_EMA_SHIFT) == 0)  Tunables::ppsEmaShift = v;
            else if (strcasecmp(name, PARAM_PPS_FAST_SHIFT)   == 0)  Tunables::ppsFastShift   = (uint8_t) v;
            else if (strcasecmp(name, PARAM_PPS_SLOW_SHIFT)   == 0) { Tunables::ppsSlowShift   = (uint8_t) v; Tunables::ppsEmaShift = Tunables::ppsSlowShift; }
            else if (strcasecmp(name, PARAM_PPS_HAMPEL_WIN)   == 0)  Tunables::ppsHampelWin   = (uint8_t) v;
            else if (strcasecmp(name, PARAM_PPS_HAMPEL_KX100) == 0)  Tunables::ppsHampelKx100 = (uint16_t) v;
            else if (strcasecmp(name, PARAM_PPS_MEDIAN3)      == 0)  Tunables::ppsMedian3     = (v != 0);
            else if (strcasecmp(name, PARAM_PPS_BLEND_LO_PPM) == 0)  Tunables::ppsBlendLoPpm  = (uint16_t) v;
            else if (strcasecmp(name, PARAM_PPS_BLEND_HI_PPM) == 0)  Tunables::ppsBlendHiPpm  = (uint16_t) v;
            else if (strcasecmp(name, PARAM_PPS_LOCK_R_PPM)   == 0)  Tunables::ppsLockRppm    = (uint16_t) v;
            else if (strcasecmp(name, PARAM_PPS_LOCK_J_PPM)   == 0)  Tunables::ppsLockJppm    = (uint16_t) v;
            else if (strcasecmp(name, PARAM_PPS_UNLOCK_R_PPM) == 0)  Tunables::ppsUnlockRppm  = (uint16_t) v;
            else if (strcasecmp(name, PARAM_PPS_UNLOCK_J_PPM) == 0)  Tunables::ppsUnlockJppm  = (uint16_t) v;
            else if (strcasecmp(name, PARAM_PPS_UNLOCK_COUNT) == 0)  Tunables::ppsUnlockCount = (uint8_t)  v;
            else if (strcasecmp(name, PARAM_PPS_HOLDOVER_MS)  == 0)  Tunables::ppsHoldoverMs  = (uint16_t) v;
            else if (strcasecmp(name, PARAM_DATA_UNITS) == 0) {
              DataUnits du;
              if      (strcasecmp(val, "raw_cycles") == 0)    du = DataUnits::RawCycles;
              else if (strcasecmp(val, "adjusted_ms") == 0)   du = DataUnits::AdjustedMs;
              else if (strcasecmp(val, "adjusted_us") == 0)   du = DataUnits::AdjustedUs;
              else if (strcasecmp(val, "adjusted_ns") == 0)   du = DataUnits::AdjustedNs;
              else ok = false;
              if (ok) { Tunables::dataUnits = du; isString = true; }
            } else ok = false;
            if (ok) {
              CMD_SERIAL.print("set: ");
              CMD_SERIAL.print(name);
              CMD_SERIAL.print(F(" = "));
              if (isFloat) CMD_SERIAL.println(fv, 6);
              else if (isString) CMD_SERIAL.println(val);
              else CMD_SERIAL.println(v);
              saveConfig(getCurrentConfig());
              if (strcasecmp(name, PARAM_DATA_UNITS) == 0) headerPending = true;
            } else {
              CMD_SERIAL.println(F("ERROR: unknown parameter"));
              sendStatus(StatusCode::InvalidParam, "unknown parameter");
            }
          } else {
            CMD_SERIAL.println(F("ERROR: set requires <param> and <value>"));
            sendStatus(StatusCode::InvalidParam, "set requires <param> and <value>");
          }
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

void queueCSVLine(const char* buf, int len) {
  if (len <= 0) return;
  if (len >= (int)CSV_LINE_MAX) {
    len = (int)CSV_LINE_MAX - 1;
    char* mutableBuf = const_cast<char*>(buf);
    mutableBuf[len - 1] = '\n';
    mutableBuf[len] = '\0';
#if ENABLE_METRICS
    csvLineTrunc++;
#endif
  }
  size_t written = DATA_SERIAL.write((const uint8_t*)buf, len);
  digitalWrite(ledPin, HIGH);
  delay(5);
  digitalWrite(ledPin, LOW);
#if ENABLE_METRICS
  if (written != (size_t)len) serialTrunc++;
#endif
}

void sendStatus(StatusCode code, const char* text) {
  const char* codeStr = statusCodeToStr(code);
  if (!text) text = "";
  int len = snprintf(lineBuf, CSV_LINE_MAX, "%s,%s,%s\n", TAG_STS, codeStr, text);
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
  uint32_t nowMs = millis();
  if (nowMs - lastMetricsMs >= METRICS_PERIOD_MS) {
    lastMetricsMs = nowMs;
    uint32_t dropped = atomicRead32(droppedEvents);
    char msg[96];
    snprintf(msg, sizeof(msg), "fill=%u,drop=%lu,serTrunc=%lu,csvTrunc=%lu",
             maxFill,
             (unsigned long)dropped,
             (unsigned long)serialTrunc,
             (unsigned long)csvLineTrunc);
    sendStatus(StatusCode::ProgressUpdate, msg);
    serialTrunc = 0;
    csvLineTrunc = 0;
    maxFill = 0;
  }
#endif
}
