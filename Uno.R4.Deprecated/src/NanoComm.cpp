#include "NanoComm.h"
#include "Display.h"
#include "DiagLog.h"
#include "SDLogger.h"
#include "PendulumCommands.h"
#include "PendulumProtocolReceiver.h"
#include "ProgmemCompat.h"
#include <ctype.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace NanoComm {
PendulumSampleState currentSample = {};
static CanonicalSwingSample latestCanonicalSwing = {};
static CanonicalPpsSample latestCanonicalPps = {};
static bool hasCanonicalSwingSample = false, hasCanonicalPpsSample = false;
static unsigned long lastCanonicalSwingMs = 0, lastCanonicalPpsMs = 0;
static bool streaming = false, protocolError = false;
static bool startupMetadataLogged = false;
static unsigned long startupSyncMs = 0, nextStartupReplayMs = 0;
static constexpr uint8_t CMD_QUEUE_LEN = 16, CACHE_LEN = 16;
static constexpr uint16_t CMD_TIMEOUT_MS = 250, NANO_STARTUP_TIMEOUT_MS = 6500;
static constexpr uint16_t STARTUP_REPLAY_INTERVAL_MS = 500, STARTUP_REPLAY_SLOW_MS = 5000;
static constexpr uint16_t RX_LINE_STALE_MS = 250;
static constexpr uint8_t MAX_LINES_PER_SERVICE = 48;
static constexpr uint8_t INGEST_EVENT_QUEUE_LEN = 56;
static constexpr uint32_t MIN_VALID_FREQ_HZ = 1000UL;

enum class CommandKind : uint8_t { Get, Set, ResetDefaults, EmitMeta };
struct NanoCommand { CommandKind kind; char param[24]; char value[24]; };
struct ParamCache { const char* param; char value[24]; bool valid; };
static const char* kNanoParams[CACHE_LEN] = {
  PARAM_PPS_FAST_SHIFT, PARAM_PPS_SLOW_SHIFT, PARAM_PPS_BLEND_LO_PPM,
  PARAM_PPS_BLEND_HI_PPM, PARAM_PPS_LOCK_R_PPM, PARAM_PPS_LOCK_MAD_TICKS,
  PARAM_PPS_UNLOCK_R_PPM, PARAM_PPS_UNLOCK_MAD_TICKS, PARAM_PPS_LOCK_COUNT,
  PARAM_PPS_UNLOCK_COUNT, PARAM_PPS_HOLDOVER_MS, PARAM_PPS_STALE_MS,
  PARAM_PPS_ISR_STALE_MS, PARAM_PPS_CFG_REEMIT_DELAY_MS,
  PARAM_PPS_ACQUIRE_MIN_MS, PARAM_PPS_METROLOGY_GRACE_MS
};
static ParamCache paramCache[CACHE_LEN] = {};
static NanoCommand cmdQueue[CMD_QUEUE_LEN] = {};
static uint8_t cmdHead = 0, cmdTail = 0, cmdQueuePeakDepth = 0;
static bool cmdInFlight = false, refreshRequested = false;
static NanoCommand activeCmd = {};
static unsigned long cmdSentMs = 0;
static char lineBuf[NANO_LINE_MAX] = {};
static size_t lineLen = 0;
static bool lineOverflow = false;
static unsigned long lastNanoByteMs = 0, partialLineActivityMs = 0;
static bool backlogFaultActive = false;
static unsigned long pressureFaultSinceMs = 0, pressureClearSinceMs = 0;
static unsigned long stalePartialLineDrops = 0, invalidStatusLineDrops = 0;
static unsigned long invalidCanonicalPpsLineDrops = 0;
static bool rxOverflowLogged = false, cfgContractLogged = false;
static IngestEvent ingestQueue[INGEST_EVENT_QUEUE_LEN] = {};
static uint8_t ingestQueueHead = 0, ingestQueueTail = 0;
static uint8_t ingestQueueCount = 0, ingestQueueHighWaterMark = 0;
static unsigned long ingestQueueDrops = 0;
static char parserScratchBuf[NANO_LINE_MAX] = {};
static bool parserScratchInUse = false;
struct ParserScratchGuard {
  bool locked = false;
  char* acquire() { if (parserScratchInUse) return nullptr; parserScratchInUse = locked = true; return parserScratchBuf; }
  ~ParserScratchGuard() { if (locked) parserScratchInUse = false; }
};
static void queueIngestEvent(const IngestEvent& event) {
  if (ingestQueueCount >= INGEST_EVENT_QUEUE_LEN) { ++ingestQueueDrops; return; }
  ingestQueue[ingestQueueTail] = event;
  ingestQueueTail = uint8_t((ingestQueueTail + 1) % INGEST_EVENT_QUEUE_LEN);
  ++ingestQueueCount;
  if (ingestQueueCount > ingestQueueHighWaterMark) ingestQueueHighWaterMark = ingestQueueCount;
}

static bool readCompleteLineFromNano(char* out, size_t outLen) {
  if (!out || outLen == 0) return false;
  const unsigned long now = millis();
  if (lineLen > 0 && (unsigned long)(now - lastNanoByteMs) > RX_LINE_STALE_MS) {
    lineLen = 0;
    lineOverflow = false;
    partialLineActivityMs = 0;
    ++stalePartialLineDrops;
  }
  while (NANO_SERIAL.available()) {
    int raw = NANO_SERIAL.read();
    if (raw < 0) break;
    lastNanoByteMs = millis();
    partialLineActivityMs = lastNanoByteMs;
    char c = (char)raw;

    if (c == '\n') {
      if (!lineOverflow) {
        lineBuf[lineLen] = '\0';
        while (lineLen > 0 && lineBuf[lineLen - 1] == '\r') {
          lineBuf[--lineLen] = '\0';
        }
        strncpy(out, lineBuf, outLen - 1);
        out[outLen - 1] = '\0';
      } else {
        if (!rxOverflowLogged) {
          char msg[80];
          snprintf(msg, sizeof(msg), "Nano RX overflow: line>%u bytes", (unsigned)(sizeof(lineBuf) - 1));
          Display::scrollLog(msg);
          rxOverflowLogged = true;
        }
        out[0] = '\0';
      }
      lineLen = 0;
      partialLineActivityMs = 0;
      bool overflowed = lineOverflow;
      lineOverflow = false;
      return !overflowed;
    }

    if (lineOverflow) continue;

    if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    } else {
      lineOverflow = true;
    }
  }
  return false;
}

static BacklogHealth computeBacklogHealth() {
  const unsigned long now = millis();
  const uint8_t serialAvailable = (uint8_t)NANO_SERIAL.available();
  const bool partialLineStaleNow =
      lineLen > 0 &&
      partialLineActivityMs != 0 &&
      (unsigned long)(now - partialLineActivityMs) >= NANO_BACKLOG_PARTIAL_STALE_MS;
  const bool queueWarnNow = ingestQueueCount >= NANO_BACKLOG_QUEUE_WARN_COUNT;
  const bool queueFaultNow = ingestQueueCount >= NANO_BACKLOG_QUEUE_FAULT_COUNT;
  const bool serialWarnNow = serialAvailable >= NANO_BACKLOG_SERIAL_WARN_BYTES;
  const bool serialFaultNow = serialAvailable >= NANO_BACKLOG_SERIAL_FAULT_BYTES;
  const bool pressureFaultNow = queueFaultNow || serialFaultNow || partialLineStaleNow;
  const bool pressureClearNow =
      ingestQueueCount <= NANO_BACKLOG_QUEUE_CLEAR_COUNT &&
      serialAvailable <= NANO_BACKLOG_SERIAL_CLEAR_BYTES &&
      !partialLineStaleNow;

  if (pressureFaultNow) {
    pressureClearSinceMs = 0;
    if (pressureFaultSinceMs == 0) pressureFaultSinceMs = now;
    if ((unsigned long)(now - pressureFaultSinceMs) >= NANO_BACKLOG_FAULT_ASSERT_MS) {
      backlogFaultActive = true;
    }
  } else {
    pressureFaultSinceMs = 0;
  }

  if (backlogFaultActive) {
    if (pressureClearNow) {
      if (pressureClearSinceMs == 0) pressureClearSinceMs = now;
      if ((unsigned long)(now - pressureClearSinceMs) >= NANO_BACKLOG_FAULT_CLEAR_MS) {
        backlogFaultActive = false;
      }
    } else {
      pressureClearSinceMs = 0;
    }
  }

  const bool degradedNow = queueWarnNow || serialWarnNow || partialLineStaleNow;
  if (backlogFaultActive) return BacklogHealth::Fault;
  return degradedNow ? BacklogHealth::Degraded : BacklogHealth::Ok;
}

static bool isNumericToken(const char* tok) {
  if (!tok || !*tok) return false;
  if (*tok == '-' || *tok == '+') tok++;
  if (!*tok) return false;
  for (const char* p = tok; *p; ++p) {
    if (!isdigit((unsigned char)*p)) return false;
  }
  return true;
}

static bool tokenMatchesTag(const char* line, const char* tag) {
  if (!line || !tag) return false;
  size_t tagLen = strlen(tag);
  return strncmp(line, tag, tagLen) == 0 &&
         (line[tagLen] == ',' || line[tagLen] == '\0');
}

static char* nextCsvToken(char** cursor) {
  if (!cursor || !*cursor) return nullptr;
  char* token = *cursor;
  char* comma = strchr(token, ',');
  if (comma) {
    *comma = '\0';
    *cursor = comma + 1;
  } else {
    *cursor = nullptr;
  }
  return token;
}


static int findCacheIndex(const char* param) {
  for (uint8_t i = 0; i < CACHE_LEN; ++i) {
    if (strcmp(kNanoParams[i], param) == 0) return i;
  }
  return -1;
}

static void cacheSet(const char* param, const char* value) {
  int idx = findCacheIndex(param);
  if (idx < 0) return;
  strncpy(paramCache[idx].value, value, sizeof(paramCache[idx].value) - 1);
  paramCache[idx].value[sizeof(paramCache[idx].value) - 1] = '\0';
  paramCache[idx].valid = true;
}

static void cacheInvalidate(const char* param) {
  int idx = findCacheIndex(param);
  if (idx < 0) return;
  paramCache[idx].value[0] = '\0';
  paramCache[idx].valid = false;
}

static bool commandMatches(const NanoCommand& cmd, CommandKind kind, const char* param) {
  if (cmd.kind != kind) return false;
  if (!param || !param[0]) return cmd.param[0] == '\0';
  return strcmp(cmd.param, param) == 0;
}

static bool commandPending(CommandKind kind, const char* param) {
  if (cmdInFlight && commandMatches(activeCmd, kind, param)) return true;
  for (uint8_t i = cmdHead; i != cmdTail; i = (uint8_t)((i + 1) % CMD_QUEUE_LEN)) {
    if (commandMatches(cmdQueue[i], kind, param)) return true;
  }
  return false;
}

static bool commandKindPending(CommandKind kind) {
  if (cmdInFlight && activeCmd.kind == kind) return true;
  for (uint8_t i = cmdHead; i != cmdTail; i = (uint8_t)((i + 1) % CMD_QUEUE_LEN)) {
    if (cmdQueue[i].kind == kind) return true;
  }
  return false;
}

static bool queueCommand(CommandKind kind, const char* param, const char* value) {
  if ((kind == CommandKind::Get || kind == CommandKind::Set) && (!param || !param[0])) return false;
  if (commandPending(kind, param)) return true;

  uint8_t nextTail = (uint8_t)((cmdTail + 1) % CMD_QUEUE_LEN);
  if (nextTail == cmdHead) return false;

  NanoCommand& cmd = cmdQueue[cmdTail];
  cmd.kind = kind;
  if (param && param[0]) {
    strncpy(cmd.param, param, sizeof(cmd.param) - 1);
    cmd.param[sizeof(cmd.param) - 1] = '\0';
  } else {
    cmd.param[0] = '\0';
  }
  if (kind == CommandKind::Set && value) {
    strncpy(cmd.value, value, sizeof(cmd.value) - 1);
    cmd.value[sizeof(cmd.value) - 1] = '\0';
  } else {
    cmd.value[0] = '\0';
  }

  cmdTail = nextTail;
  const uint8_t depth = (cmdTail >= cmdHead) ? (uint8_t)(cmdTail - cmdHead)
                                             : (uint8_t)(CMD_QUEUE_LEN - cmdHead + cmdTail);
  if (depth > cmdQueuePeakDepth) cmdQueuePeakDepth = depth;
  return true;
}

static bool popCommand(NanoCommand& out) {
  if (cmdHead == cmdTail) return false;
  out = cmdQueue[cmdHead];
  cmdHead = (uint8_t)((cmdHead + 1) % CMD_QUEUE_LEN);
  return true;
}


static void dispatchNextCommand() {
  if (cmdInFlight || !popCommand(activeCmd)) return;
  char cmd[80];
  switch (activeCmd.kind) {
    case CommandKind::Get: snprintf(cmd, sizeof(cmd), "%s %s", CMD_GET, activeCmd.param); break;
    case CommandKind::Set: snprintf(cmd, sizeof(cmd), "%s %s %s", CMD_SET, activeCmd.param, activeCmd.value); break;
    case CommandKind::ResetDefaults: snprintf(cmd, sizeof(cmd), "%s %s", CMD_RESET, CMD_RESET_DEFAULTS); break;
    case CommandKind::EmitMeta: snprintf(cmd, sizeof(cmd), "%s %s", CMD_EMIT, CMD_EMIT_META); break;
  }
  NANO_SERIAL.println(cmd); cmdInFlight = true; cmdSentMs = millis();
}
static void rejectContract(const char* reason) {
  if (!protocolError) { Display::scrollLog(reason); SDLogger::stopLogging(); }
  protocolError = true;
}

static bool parseStatusCodeToken(const char* token, StatusCode& out) {
  if (!token || !*token) return false;
  if (isNumericToken(token)) {
    long raw = strtol(token, nullptr, 10);
    if (raw < (long)StatusCode::Ok || raw > (long)StatusCode::ProgressUpdate) return false;
    out = (StatusCode)raw;
    return true;
  }
  for (uint8_t code = (uint8_t)StatusCode::Ok;
       code <= (uint8_t)StatusCode::ProgressUpdate;
       ++code) {
    if (strcmp(token, statusCodeToStr((StatusCode)code)) == 0) {
      out = (StatusCode)code;
      return true;
    }
  }
  return false;
}

static bool parseStatusLine(const char* line, StatusCode& code, char* tokens[], size_t& tokenCount) {
  tokenCount = 0;
  if (!tokenMatchesTag(line, TAG_STS)) return false;
  // Returned token pointers alias parserScratchBuf and remain valid until the
  // next parser entry point acquires and overwrites the shared scratch buffer.

  ParserScratchGuard scratch;
  char* parseBuf = scratch.acquire();
  if (!parseBuf) return false;
  size_t n = strnlen(line, sizeof(parserScratchBuf));
  if (n >= sizeof(parserScratchBuf)) return false;
  memcpy(parseBuf, line, n);
  parseBuf[n] = '\0';

  char* ctx = nullptr;
  char* tok = strtok_r(parseBuf, ",", &ctx);
  if (!tok || strcmp(tok, TAG_STS) != 0) return false;

  tok = strtok_r(nullptr, ",", &ctx);
  if (!tok || !parseStatusCodeToken(tok, code)) return false;

  while (tokenCount < 8 && (tok = strtok_r(nullptr, ",", &ctx)) != nullptr) {
    tokens[tokenCount++] = tok;
  }
  return true;
}

static bool statusTokensContain(char* const tokens[], size_t tokenCount, const char* needle) {
  if (!needle || !*needle) return false;
  for (size_t i = 0; i < tokenCount; ++i) {
    if (tokens[i] && strcmp(tokens[i], needle) == 0) return true;
  }
  return false;
}

static bool refreshCachedParamsFromStatus(char* tokens[], size_t tokenCount) {
  bool refreshed = false;
  for (size_t i = 0; i + 1 < tokenCount; ++i) {
    if (findCacheIndex(tokens[i]) >= 0) {
      cacheSet(tokens[i], tokens[i + 1]);
      refreshed = true;
    }
  }
  return refreshed;
}

static bool statusMatchesActiveCommand(StatusCode code, char* tokens[], size_t tokenCount) {
  if (!cmdInFlight) return false;
  switch (activeCmd.kind) {
    case CommandKind::Get:
      if (statusTokensContain(tokens, tokenCount, activeCmd.param)) return true;
      if (statusTokensContain(tokens, tokenCount, CMD_GET)) return true;
      break;
    case CommandKind::Set:
      if (statusTokensContain(tokens, tokenCount, activeCmd.param)) return true;
      if (statusTokensContain(tokens, tokenCount, CMD_SET)) return true;
      break;
    case CommandKind::ResetDefaults:
      if (statusTokensContain(tokens, tokenCount, CMD_RESET) ||
          statusTokensContain(tokens, tokenCount, CMD_RESET_DEFAULTS)) {
        return true;
      }
      break;
    case CommandKind::EmitMeta:
      // Only treat STS,OK,emit,meta as a successful EmitMeta acknowledgement.
      // This avoids clearing cmdInFlight on Nano errors like STS,UNKNOWN_COMMAND,emit.
      return code == StatusCode::Ok &&
             tokenCount >= 2 &&
             strcmp(tokens[0], CMD_EMIT) == 0 &&
             strcmp(tokens[1], CMD_EMIT_META) == 0;
    default:
      break;
  }
  return tokenCount == 0 && code != StatusCode::ProgressUpdate;
}

static void maybeCompleteRefresh() {
  if (!refreshRequested) return;
  for (uint8_t i = 0; i < CACHE_LEN; ++i) {
    if (!paramCache[i].valid) return;
  }
  refreshRequested = false;
}


static void handleStatusLine(const char* line) {
  StatusCode code = StatusCode::InternalError;
  char* tokens[8] = {}; size_t count = 0;
  if (!parseStatusLine(line, code, tokens, count)) { ++invalidStatusLineDrops; return; }
  if (code == StatusCode::ProgressUpdate && count >= 2 &&
      strcmp(tokens[0], STS_FAMILY_SCHEMA) == 0 &&
      strncmp(tokens[1], "sts=", 4) == 0) {
    uint32_t version = 0;
    if (!parseUint32Dec(tokens[1] + 4, version) || version != STS_SCHEMA_VERSION) {
      rejectContract("Nano STS schema mismatch; recording halted");
      return;
    }
  }
  SDLogger::logStatusLine(line);
  const bool refreshed = refreshCachedParamsFromStatus(tokens, count);
  maybeCompleteRefresh();
  if (!statusMatchesActiveCommand(code, tokens, count) || code == StatusCode::ProgressUpdate) return;
  if (code == StatusCode::Ok) {
    if (activeCmd.kind == CommandKind::Set && !refreshed) { cacheSet(activeCmd.param, activeCmd.value); maybeCompleteRefresh(); }
    else if (activeCmd.kind == CommandKind::ResetDefaults) { invalidateCachedParams(); requestRefreshAll(); }
  }
  cmdInFlight = false;
}
static bool copyCfgString(char* out, size_t size, const char* value) {
  if (strlen(value) >= size) return false;
  strcpy(out, value); return true;
}
static bool sameContract(const NanoSessionConfig& a, const NanoSessionConfig& b) {
  return a.protocol_version == b.protocol_version && a.nominal_hz == b.nominal_hz &&
      strcmp(a.firmware, b.firmware) == 0 &&
      strcmp(a.canonical_swing_tag, b.canonical_swing_tag) == 0 &&
      strcmp(a.canonical_pps_tag, b.canonical_pps_tag) == 0 &&
      strcmp(a.canonical_swing_schema_id, b.canonical_swing_schema_id) == 0 &&
      strcmp(a.canonical_pps_schema_id, b.canonical_pps_schema_id) == 0;
}
static bool parseConfigLine(const char* line) {
  ParserScratchGuard guard; char* buf = guard.acquire();
  if (!buf) return false;
  const size_t n = strnlen(line, sizeof(parserScratchBuf));
  if (n >= sizeof(parserScratchBuf)) return false;
  memcpy(buf, line, n + 1);
  char* cursor = buf;
  char* tag = nextCsvToken(&cursor);
  if (!tag || strcmp(tag, TAG_CFG) != 0) return false;
  NanoSessionConfig next = {};
  uint8_t seen = 0;
  char* token = nullptr;
  while ((token = nextCsvToken(&cursor)) != nullptr) {
    char* eq = strchr(token, '=');
    if (!eq || eq == token || !eq[1]) return false;
    *eq++ = 0;
    if (validateProtocolCfgValue(token, eq) != CfgValidationResult::Ok) return false;
    uint8_t bit = 0;
    if (strcmp(token, CFG_KEY_PROTOCOL_VERSION) == 0) { bit = 1; next.protocol_version = uint16_t(strtoul(eq, nullptr, 10)); }
    else if (strcmp(token, CFG_KEY_NOMINAL_HZ) == 0) { bit = 2; next.nominal_hz = strtoul(eq, nullptr, 10); }
    else if (strcmp(token, CFG_KEY_CANONICAL_SWING_TAG) == 0) { bit = 4; if (!copyCfgString(next.canonical_swing_tag, sizeof(next.canonical_swing_tag), eq)) return false; }
    else if (strcmp(token, CFG_KEY_CANONICAL_SWING_SCHEMA) == 0) { bit = 8; if (!copyCfgString(next.canonical_swing_schema_id, sizeof(next.canonical_swing_schema_id), eq)) return false; }
    else if (strcmp(token, CFG_KEY_CANONICAL_PPS_TAG) == 0) { bit = 16; if (!copyCfgString(next.canonical_pps_tag, sizeof(next.canonical_pps_tag), eq)) return false; }
    else if (strcmp(token, CFG_KEY_CANONICAL_PPS_SCHEMA) == 0) { bit = 32; if (!copyCfgString(next.canonical_pps_schema_id, sizeof(next.canonical_pps_schema_id), eq)) return false; }
    else if (strcmp(token, CFG_KEY_FIRMWARE_VERSION) == 0) { bit = 64; if (!copyCfgString(next.firmware, sizeof(next.firmware), eq)) return false; }
    if (!bit || (seen & bit)) return false;
    seen |= bit;
  }
  if (seen != 127) return false;
  next.config_received = true;
  NanoSessionConfig& session = currentSample.session;
  if (session.config_received) {
    if (!sameContract(session, next)) rejectContract("Nano CFG changed; recording halted");
    return !protocolError;
  }
  session = next;
  return true;
}
static uint32_t fnv1a32(const char* text) {
  uint32_t hash = 2166136261u;
  while (*text) { hash ^= uint8_t(*text++); hash *= 16777619u; }
  return hash;
}
static bool parseSchemaLine(const char* line) {
  if (!currentSample.session.config_received || protocolError) return false;
  ParserScratchGuard guard; char* buf = guard.acquire();
  if (!buf) return false;
  size_t n = strnlen(line, sizeof(parserScratchBuf));
  if (n >= sizeof(parserScratchBuf)) return false;
  memcpy(buf, line, n + 1);
  char* cursor = buf;
  char* tag = nextCsvToken(&cursor);
  char* stream = nextCsvToken(&cursor);
  char* id = nextCsvToken(&cursor);
  const char* payload = cursor;
  if (!tag || strcmp(tag, TAG_SCH) != 0 || !stream || !id || !payload || !*payload) return false;
  bool swing = strcmp(stream, TAG_CSW) == 0;
  if (!swing && strcmp(stream, TAG_CPS) != 0) return false;
  const char* expectedId = swing ? CANONICAL_SWING_SCHEMA_ID : CANONICAL_PPS_SCHEMA_ID;
  const char* expectedPayload = swing ? CANONICAL_SWING_SCHEMA : CANONICAL_PPS_SCHEMA;
  const char* cfgId = swing ? currentSample.session.canonical_swing_schema_id : currentSample.session.canonical_pps_schema_id;
  if (strcmp(id, expectedId) != 0 || strcmp(id, cfgId) != 0 || cmpRamToFlash(payload, expectedPayload) != 0) return false;
  const uint32_t hash = fnv1a32(payload);
  NanoSessionConfig& session = currentSample.session;
  uint32_t& previous = swing ? session.canonical_swing_schema_payload_hash : session.canonical_pps_schema_payload_hash;
  bool& received = swing ? session.canonical_swing_schema_received : session.canonical_pps_schema_received;
  if (received && previous != hash) { rejectContract("Nano SCH changed; recording halted"); return false; }
  previous = hash; received = true; return true;
}
static bool parseCaptureLine(const char* line, bool swing) {
  if (!metadataReady()) return false;
  ParserScratchGuard guard; char* buf = guard.acquire();
  if (!buf) return false;
  const size_t n = strnlen(line, sizeof(parserScratchBuf));
  if (n >= sizeof(parserScratchBuf) || n == 0 || line[n-1] == ',') return false;
  memcpy(buf, line, n + 1);
  char* cursor = buf;
  char* tag = nextCsvToken(&cursor);
  if (!tag || strcmp(tag, swing ? TAG_CSW : TAG_CPS) != 0) return false;
  const uint8_t count = swing ? 9 : 8;
  uint32_t values[9] = {};
  for (uint8_t i = 0; i < count; ++i) {
    char* token = nextCsvToken(&cursor);
    if (!token || !parseUint32Dec(token, values[i])) return false;
  }
  if (cursor != nullptr) return false;
  IngestEvent event = {};
  if (swing) {
    CanonicalSwingSample& s = event.payload.swing;
    s.seq=values[0]; s.edge0_tcb0=values[1]; s.edge1_tcb0=values[2];
    s.edge2_tcb0=values[3]; s.edge3_tcb0=values[4]; s.edge4_tcb0=values[5];
    s.drop_ir=values[6]; s.drop_pps=values[7]; s.drop_swing=values[8];
    event.type=IngestEventType::CanonicalSwing;
    latestCanonicalSwing=s; hasCanonicalSwingSample=true;
    lastCanonicalSwingMs=millis(); currentSample.session.canonical_swing_received=true;
  } else {
    if (values[2] > HOLDOVER || values[4] > UINT16_MAX || values[5] > UINT16_MAX) return false;
    CanonicalPpsSample& p = event.payload.pps;
    p.seq=values[0]; p.edge_tcb0=values[1]; p.gps_status=GpsStatus(values[2]);
    p.holdover_age_ms=values[3]; p.cap16=uint16_t(values[4]);
    p.latency16=uint16_t(values[5]); p.now32=values[6]; p.drop_pps=values[7];
    event.type=IngestEventType::CanonicalPps;
    latestCanonicalPps=p; hasCanonicalPpsSample=true;
    lastCanonicalPpsMs=millis(); currentSample.session.canonical_pps_received=true;
  }
  queueIngestEvent(event); return true;
}
void parseIncomingLine(const char* line) {
  if (!line || !*line) return;
  if (tokenMatchesTag(line, TAG_STS)) { handleStatusLine(line); return; }
  if (tokenMatchesTag(line, TAG_CFG)) {
    if (!parseConfigLine(line)) {
      if (currentSample.session.config_received) rejectContract("Nano CFG conflict; recording halted");
      else if (!cfgContractLogged) { Display::scrollLog(F("Nano CFG invalid")); cfgContractLogged=true; }
    }
    return;
  }
  if (tokenMatchesTag(line, TAG_SCH)) {
    if (!parseSchemaLine(line) && currentSample.session.config_received) {
      rejectContract("Nano SCH conflict; recording halted");
    }
    return;
  }
  if (tokenMatchesTag(line, TAG_CSW)) { parseCaptureLine(line, true); return; }
  if (tokenMatchesTag(line, TAG_CPS) && !parseCaptureLine(line, false)) ++invalidCanonicalPpsLineDrops;
}

void service() {
  if (!streaming) return;
  char line[NANO_LINE_MAX];
  uint8_t processed=0;
  while (processed < MAX_LINES_PER_SERVICE && ingestQueueCount < INGEST_EVENT_QUEUE_LEN) {
    if (!readCompleteLineFromNano(line, sizeof(line))) break;
    if (*line) { parseIncomingLine(line); ++processed; }
  }
  if (cmdInFlight && millis() - cmdSentMs >= CMD_TIMEOUT_MS) cmdInFlight=false;
  if (!metadataReady() && !protocolError) {
    const unsigned long now=millis();
    if (!cmdInFlight && int32_t(now-nextStartupReplayMs)>=0) {
      NANO_SERIAL.println("emit startup");
      nextStartupReplayMs=now+(now-startupSyncMs < NANO_STARTUP_TIMEOUT_MS ? STARTUP_REPLAY_INTERVAL_MS : STARTUP_REPLAY_SLOW_MS);
    }
  } else if (metadataReady()) {
    if (!startupMetadataLogged) { SDLogger::logUnoEvent("ingest.metadata", "ready"); startupMetadataLogged=true; SDLogger::onMetadataReady(); }
    dispatchNextCommand();
  }
}
void readStartup() {
  currentSample={}; streaming=true; protocolError=false;
  latestCanonicalSwing={}; latestCanonicalPps={};
  hasCanonicalSwingSample=hasCanonicalPpsSample=false;
  lastCanonicalSwingMs=lastCanonicalPpsMs=0;
  lineLen=0; lineOverflow=false; lastNanoByteMs=partialLineActivityMs=0;
  backlogFaultActive=false; pressureFaultSinceMs=pressureClearSinceMs=0;
  stalePartialLineDrops=invalidStatusLineDrops=invalidCanonicalPpsLineDrops=0;
  rxOverflowLogged=cfgContractLogged=false;
  ingestQueueHead=ingestQueueTail=ingestQueueCount=ingestQueueHighWaterMark=0; ingestQueueDrops=0;
  cmdHead=cmdTail=cmdQueuePeakDepth=0; cmdInFlight=false; refreshRequested=false;
  for (uint8_t i=0;i<CACHE_LEN;++i) { paramCache[i].param=kNanoParams[i]; paramCache[i].valid=false; paramCache[i].value[0]=0; }
  startupSyncMs=nextStartupReplayMs=millis(); startupMetadataLogged=false;
}
bool metadataReady() {
  const NanoSessionConfig& s=currentSample.session;
  return !protocolError && s.config_received && s.canonical_swing_schema_received && s.canonical_pps_schema_received;
}
bool streamingStarted() { return streaming; }
bool hasProtocolError() { return protocolError; }
bool requestGet(const char* param) { return queueCommand(CommandKind::Get,param,""); }
bool requestSet(const char* param,const char* value) { return queueCommand(CommandKind::Set,param,value ? value : ""); }
bool requestResetDefaults() { invalidateCachedParams(); refreshRequested=true; return queueCommand(CommandKind::ResetDefaults,nullptr,""); }
bool requestEmitMeta() { return queueCommand(CommandKind::EmitMeta,CMD_EMIT_META,""); }
void requestRefreshAll() {
  bool queued=false;
  for (uint8_t i=0;i<CACHE_LEN;++i) if (!paramCache[i].valid || !refreshRequested)
    queued=queueCommand(CommandKind::Get,kNanoParams[i],"") || queued;
  if (queued) refreshRequested=true;
}
bool getCachedParam(const char* param,char* out,size_t len) {
  int i=findCacheIndex(param); if (i<0 || !out || !len || !paramCache[i].valid) return false;
  strncpy(out,paramCache[i].value,len-1); out[len-1]=0; return true;
}
bool hasAllCachedParams() { for (uint8_t i=0;i<CACHE_LEN;++i) if (!paramCache[i].valid) return false; return true; }
void invalidateCachedParams() { for (uint8_t i=0;i<CACHE_LEN;++i) cacheInvalidate(kNanoParams[i]); refreshRequested=false; }
bool isRefreshInProgress() { return refreshRequested && (commandKindPending(CommandKind::Get) || commandKindPending(CommandKind::ResetDefaults)); }
uint8_t commandQueueDepth() { return cmdTail>=cmdHead ? cmdTail-cmdHead : CMD_QUEUE_LEN-cmdHead+cmdTail; }
uint8_t commandQueuePeakDepth() { return cmdQueuePeakDepth; }
bool hasConfig() { return currentSample.session.config_received; }
bool canonicalSwingAvailable() { return hasCanonicalSwingSample; }
bool canonicalPpsAvailable() { return hasCanonicalPpsSample; }
bool getCanonicalSwingSample(CanonicalSwingSample& out) { if (!hasCanonicalSwingSample) return false; out=latestCanonicalSwing; return true; }
bool getCanonicalPpsSample(CanonicalPpsSample& out) { if (!hasCanonicalPpsSample) return false; out=latestCanonicalPps; return true; }
uint32_t canonicalSwingAgeMs() { return hasCanonicalSwingSample ? millis()-lastCanonicalSwingMs : AGE_UNKNOWN_MS; }
uint32_t canonicalPpsAgeMs() { return hasCanonicalPpsSample ? millis()-lastCanonicalPpsMs : AGE_UNKNOWN_MS; }
bool latestPpsStatus(LatestPpsStatus& out) {
  out={}; if (!hasCanonicalPpsSample) return false;
  out.available=true; out.status=latestCanonicalPps.gps_status;
  out.age_ms=canonicalPpsAgeMs(); out.holdover_age_ms=latestCanonicalPps.holdover_age_ms; return true;
}
bool latestSwingStatus(LatestSwingStatus& out) {
  out={}; if (!hasCanonicalSwingSample) return false;
  out.available=true; out.age_ms=canonicalSwingAgeMs(); return true;
}
bool hasValidNominalHz(const PendulumSampleState& s) { return s.session.nominal_hz >= MIN_VALID_FREQ_HZ; }
float effectiveHz(const PendulumSampleState& s) { return hasValidNominalHz(s) ? float(s.session.nominal_hz) : 0.0f; }
float effectiveHz() { return effectiveHz(currentSample); }
uint32_t ticksToMicrosAtHz(uint32_t ticks,float hz) { if (hz<=0) return 0; double us=double(ticks)*1000000.0/hz; return us>=UINT32_MAX ? UINT32_MAX : uint32_t(us+0.5); }
int32_t ticksToMicrosAtHz(int32_t ticks,float hz) { if (hz<=0) return 0; double us=double(ticks)*1000000.0/hz; if (us>=INT32_MAX) return INT32_MAX; if (us<=INT32_MIN) return INT32_MIN; return int32_t(us>=0 ? us+0.5 : us-0.5); }
uint32_t ticksToMicros(uint32_t ticks,const PendulumSampleState& s) { return ticksToMicrosAtHz(ticks,effectiveHz(s)); }
uint32_t ticksToMicros(uint32_t ticks) { return ticksToMicros(ticks,currentSample); }
float bpmForPeriodAtHz(uint32_t ticks,float hz) { return ticks && hz>0 ? 60.0f*hz/float(ticks) : 0; }
uint32_t ticksToUnits(uint32_t ticks) { return ticks; }
const char* getDataUnitsLabel() { return "cycles"; }
bool dequeueIngestEvent(IngestEvent& out) {
  if (!ingestQueueCount) return false;
  out=ingestQueue[ingestQueueHead]; ingestQueueHead=uint8_t((ingestQueueHead+1)%INGEST_EVENT_QUEUE_LEN); --ingestQueueCount; return true;
}
uint8_t ingestQueueDepth() { return ingestQueueCount; }
uint8_t ingestQueueHighWater() { return ingestQueueHighWaterMark; }
bool hasPendingIngestWork() { return ingestQueueCount || lineLen || NANO_SERIAL.available()>0; }
BacklogHealth getBacklogHealth() { return computeBacklogHealth(); }
bool hasBacklogFault() { return getBacklogHealth()==BacklogHealth::Fault; }
bool hasIngestBacklog() { return hasPendingIngestWork(); }
unsigned long ingestEventDrops() { return ingestQueueDrops; }
unsigned long staleLineDrops() { return stalePartialLineDrops; }
unsigned long invalidStatusDrops() { return invalidStatusLineDrops; }
unsigned long invalidCanonicalPpsDrops() { return invalidCanonicalPpsLineDrops; }
} // namespace NanoComm
