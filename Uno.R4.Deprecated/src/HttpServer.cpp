#include "PendulumProtocolReceiver.h"
#include "HttpServer.h"
#include "WiFiStorage.h"
#include "SDLogger.h"
#include "Sensors.h"
#include "Display.h"
#include "MemoryMonitor.h"
#include "ServiceTelemetry.h"
#include "IngestOrchestrator.h"
#include "EEPROMConfig.h"
#include "NanoComm.h"
#include "PendulumCommands.h"
#include "WiFiConfig.h"
#include "PublishMirrorQueue.h"
#include "ArduinoHttpServer.h"
#include <SD.h>
#include <avr/pgmspace.h>
#include <Arduino.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <cstdio>

using ArduinoHttpServer::Method;
using ArduinoHttpServer::Request;
using ArduinoHttpServer::Response;
using HttpMethod = Method;
using HttpRequest = Request;
using HttpResponse = Response;

namespace HttpServer {

namespace {

struct HttpTelemetry {
  unsigned long total_requests = 0;
  unsigned long total_rejected = 0;
  unsigned long download_requests = 0;
  unsigned long download_rejected = 0;
};

static HttpTelemetry httpTelemetry;
static unsigned long serviceBudgetExceededCount = 0;


static void noteHttpRequest() { httpTelemetry.total_requests++; }

static void noteHttpRejected() { httpTelemetry.total_rejected++; }

static void sendBusyText(HttpResponse& response,
                         const __FlashStringHelper* status,
                         const __FlashStringHelper* body) {
  response.setStatusCode(status);
  response.setHeader(F("Content-Type"), F("text/plain"));
  response.setHeader(F("Connection"), F("close"));
  response.beginBody();
  response.print(body);
}

static SDLogger::LogStartupPolicy policyFromConfig(const UnoConfig& cfg) {
  switch (cfg.logStartupPolicy) {
    case LOG_STARTUP_POLICY_APPEND:
      return SDLogger::LogStartupPolicy::Append;
    case LOG_STARTUP_POLICY_ARCHIVE:
      return SDLogger::LogStartupPolicy::ArchiveAndStartFresh;
    case LOG_STARTUP_POLICY_OVERWRITE:
    default:
      return SDLogger::LogStartupPolicy::Overwrite;
  }
}

static uint8_t configValueFromPolicy(SDLogger::LogStartupPolicy policy) {
  switch (policy) {
    case SDLogger::LogStartupPolicy::Append:
      return LOG_STARTUP_POLICY_APPEND;
    case SDLogger::LogStartupPolicy::ArchiveAndStartFresh:
      return LOG_STARTUP_POLICY_ARCHIVE;
    case SDLogger::LogStartupPolicy::Overwrite:
    default:
      return LOG_STARTUP_POLICY_OVERWRITE;
  }
}

static const __FlashStringHelper* policyLabel(SDLogger::LogStartupPolicy policy) {
  switch (policy) {
    case SDLogger::LogStartupPolicy::Append:
      return F("Append");
    case SDLogger::LogStartupPolicy::ArchiveAndStartFresh:
      return F("Archive and start fresh");
    case SDLogger::LogStartupPolicy::Overwrite:
    default:
      return F("Overwrite");
  }
}

static bool parsePolicyValue(const char* value, uint8_t& out) {
  if (!value) return false;
  if (strcmp(value, "append") == 0 || strcmp(value, "0") == 0) {
    out = LOG_STARTUP_POLICY_APPEND;
    return true;
  }
  if (strcmp(value, "overwrite") == 0 || strcmp(value, "1") == 0) {
    out = LOG_STARTUP_POLICY_OVERWRITE;
    return true;
  }
  if (strcmp(value, "archive") == 0 || strcmp(value, "2") == 0) {
    out = LOG_STARTUP_POLICY_ARCHIVE;
    return true;
  }
  return false;
}

} // namespace


static void sendRedirect(HttpResponse& response, const char* location) {
  response.setStatusCode(F("303 See Other"));
  response.setHeader("Location", location);
  response.setHeader("Connection", "close");
  response.setHeader("Content-Length", "0");
  response.beginBody(0);
}

static const char HOME_PAGE[] PROGMEM = R"rawliteral(<!DOCTYPE html><html><head><meta charset="utf-8"><title>Home</title></head><body><h2>UNO R4 Pendulum Logger</h2><ul><li><a href='/uno'>UNO Tunables</a></li><li><a href='/wifi'>WiFi Config</a></li><li><a href='/nano'>Nano Tunables</a></li><li><a href='/logfiles'>Log Files</a></li></ul><hr><small>UNO R4 Pendulum Logger</small></body></html>)rawliteral";

static const char NOT_FOUND_PAGE[] PROGMEM = R"rawliteral(<!DOCTYPE html><html><head><meta charset="utf-8"><title>Not Found</title></head><body><h2>404 - Not Found</h2><p>The requested resource could not be located.</p><a href='/' aria-label='Return to home page'>Home</a><hr><small>UNO R4 Pendulum Logger</small></body></html>)rawliteral";

static const char* userDataUnitsLabel() { return NanoComm::getDataUnitsLabel(); }

static WiFiServer tcpServer(HTTP_PORT);
static ArduinoHttpServer::Server httpServer(tcpServer);
static bool routesRegistered = false;
static HttpServer::detail::ListenerRecovery listenerRecovery;
static uint32_t listenerRestartCount = 0;
static constexpr unsigned long HTTP_RESTART_STABLE_MS = 350;
static constexpr unsigned long HTTP_RESTART_BACKOFF_MS = 500;
static constexpr unsigned long HTTP_LISTENER_REFRESH_MS = 60000UL;
static constexpr unsigned long HTTP_NO_CLIENT_REFRESH_MS = 60000UL;
static unsigned long lastHttpBeginMs = 0;
static unsigned long lastClientAcceptedMs = 0;

static void logListenerInactive(const char* reason) {
  char evt[144] = {0};
  snprintf(evt, sizeof(evt), "event,HTTP_LISTENER_INACTIVE,reason,%s,restarts,%lu",
           reason, (unsigned long)listenerRestartCount);
  SDLogger::logUnoEvent("http.listener", evt);
}

static size_t urlDecode(char* s) {
  char* src = s;
  char* dst = s;
  while (*src) {
    if (*src == '+') { *dst++ = ' '; src++; }
    else if (*src == '%' && isxdigit((unsigned char)src[1]) && isxdigit((unsigned char)src[2])) {
      char a = toupper(src[1]);
      char b = toupper(src[2]);
      a = (a>='A') ? (a-'A'+10) : (a-'0');
      b = (b>='A') ? (b-'A'+10) : (b-'0');
      *dst++ = char(16*a + b);
      src += 3;
    } else {
      *dst++ = *src++;
    }
  }
  *dst = '\0';
  return static_cast<size_t>(dst - s);
}

static bool getParam(const char* source, const char* key, char* out, size_t outLen, bool* complete = nullptr) {
  if (complete) *complete = false;
  if (!out || outLen == 0 || !key || key[0] == '\0') return false;
  if (!source) return false;

  const size_t keyLen = strlen(key);
  const char* p = source;
  while (*p) {
    if ((p == source || p[-1] == '&') && strncmp(p, key, keyLen) == 0) {
      const char* valueStart = p + keyLen;
      size_t i = 0;
      while (valueStart[i] && valueStart[i] != '&' && i < outLen - 1) {
        out[i] = valueStart[i];
        ++i;
      }
      out[i] = '\0';
      const bool copiedAll = !valueStart[i] || valueStart[i] == '&';
      const size_t decodedLength = urlDecode(out);
      if (complete) *complete = copiedAll && decodedLength == strlen(out);
      return true;
    }
    ++p;
  }

  return false;
}

class QueryParams {
public:
  explicit QueryParams(const char* src) : source(src ? src : "") {}

  bool empty() const { return source[0] == '\0'; }

  bool copyValue(const char* key, char* out, size_t outLen) const {
    return getParam(source, key, out, outLen);
  }

  // Presence is returned separately from strict, untruncated decoding. An
  // invalid present value becomes empty so the numeric parser rejects it.
  bool copyExactValue(const char* key, char* out, size_t outLen) const {
    bool complete = false;
    const bool found = getParam(source, key, out, outLen, &complete);
    if (found && !complete && outLen) out[0] = 0;
    return found;
  }

  long toLong(const char* key, long defaultVal = 0) const {
    char buf[16] = {0};
    return copyValue(key, buf, sizeof(buf)) ? atol(buf) : defaultVal;
  }

  bool equals(const char* key, const char* value) const {
    char buf[32] = {0};
    return copyValue(key, buf, sizeof(buf)) && strcmp(buf, value) == 0;
  }

  bool flagEnabled(const char* key) const { return toLong(key, 0) != 0; }

private:
  const char* source;
};

static void setContentLengthHeader(HttpResponse& response, size_t length) {
  char contentLength[16] = {0};
  snprintf(contentLength, sizeof(contentLength), "%lu", static_cast<unsigned long>(length));
  response.setHeader("Content-Length", contentLength);
}

static void beginJsonResponse(HttpResponse& response) {
  response.setStatusCode(F("200 OK"));
  response.setHeader("Content-Type", "application/json");
  response.setHeader("Connection", "close");
  response.beginBody();
}

static void printJsonFloatOrNull(HttpResponse& response, float value, uint8_t digits) {
  if (!detail::jsonFinite(value)) {
    response.print(F("null"));
    return;
  }
  response.print(value, digits);
}

static void printJsonChannelHealth(HttpResponse& response,
                                   const Sensors::ChannelHealth& channel) {
  response.print(F("{\"state\":\""));
  response.print(Sensors::healthStateName(channel.state));
  response.print(F("\",\"initialized\":"));
  response.print(channel.initialized ? F("true") : F("false"));
  response.print(F(",\"fresh\":"));
  response.print(channel.fresh ? F("true") : F("false"));
  response.print(F(",\"suspect\":"));
  response.print(channel.suspect ? F("true") : F("false"));
  response.print(F(",\"failures\":"));
  response.print((unsigned int)channel.consecutiveFailures);
  response.print(F(",\"init_failures\":"));
  response.print((unsigned int)channel.initFailures);
  response.print(F(",\"last_success_ms\":"));
  response.print((unsigned long)channel.lastSuccessMs);
  response.print(F(",\"next_attempt_ms\":"));
  response.print((unsigned long)channel.nextAttemptMs);
  response.print(F("}"));
}

static void printJsonStreamHealth(HttpResponse& response,
                                  const SDLogger::StreamSnapshot& stream) {
  response.print(F("{\"active\":"));
  response.print(stream.active ? F("true") : F("false"));
  response.print(F(",\"rows_lost\":"));
  response.print((unsigned long)stream.rowsLost);
  response.print(F(",\"write_failures\":"));
  response.print((unsigned long)stream.writeFailures);
  response.print(F(",\"open_failures\":"));
  response.print((unsigned long)stream.openFailures);
  response.print(F(",\"max_write_us\":"));
  response.print((unsigned long)stream.maxWriteDurationUs);
  response.print(F("}"));
}

static const char* sdFaultReasonName(SDLogger::SdFaultReason reason) {
  switch (reason) {
    case SDLogger::SdFaultReason::RootProbeOpen: return "root_probe_open";
    case SDLogger::SdFaultReason::AllStreamsUnavailable: return "all_streams_unavailable";
    case SDLogger::SdFaultReason::None:
    default: return "none";
  }
}

static void appendJsonHealth(HttpResponse& response) {
  const Sensors::HealthSnapshot& sensorHealth = Sensors::health();
  const SDLogger::Snapshot& sdHealth = SDLogger::snapshot();

  response.print(F(",\"health\":{\"env\":{\"sht4x\":"));
  printJsonChannelHealth(response, sensorHealth.sht4x);
  response.print(F(",\"bmp280\":"));
  printJsonChannelHealth(response, sensorHealth.bmp280);
  response.print(F("},\"sd\":{\"ready\":"));
  response.print(SDLogger::ready() ? F("true") : F("false"));
  response.print(F(",\"logging\":"));
  response.print(SDLogger::isLogging() ? F("true") : F("false"));
  response.print(F(",\"streams\":{\"swing\":"));
  printJsonStreamHealth(response, sdHealth.canonicalSwing);
  response.print(F(",\"pps\":"));
  printJsonStreamHealth(response, sdHealth.canonicalPps);
  response.print(F(",\"status\":"));
  printJsonStreamHealth(response, sdHealth.status);
  response.print(F(",\"uno\":"));
  printJsonStreamHealth(response, sdHealth.uno);
  response.print(F("},\"paths\":{\"swing\":\""));
  response.print(SDLogger::getActiveCanonicalSwingFilename());
  response.print(F("\",\"pps\":\""));
  response.print(SDLogger::getActiveCanonicalPpsFilename());
  response.print(F("\"}"));
  response.print(F(",\"diagnostics_suppressed\":"));
  response.print((unsigned long)sdHealth.diagnosticsSuppressed);
  response.print(F(",\"diagnostic_rotations\":"));
  response.print((unsigned long)sdHealth.diagnosticRotations);
  response.print(F(",\"measurement_rotations\":"));
  response.print((unsigned long)sdHealth.measurementRotations);
  response.print(F(",\"card_faults\":"));
  response.print((unsigned long)sdHealth.cardFaults);
  response.print(F(",\"last_card_fault\":\""));
  response.print(sdFaultReasonName(sdHealth.lastCardFault));
  response.print(F("\""));
  response.print(F("},\"nano_service_max_gap_us\":"));
  response.print(ServiceTelemetry::nanoServiceMaxGapUs());
  response.print(F("}"));
}

static void sendProgmemHtml(HttpResponse& response, const char* bodyProgmem, int statusCode = 200) {
  size_t bodyLen = strlen_P((PGM_P)bodyProgmem);
  response.setStatusCode(statusCode);
  response.setHeader("Content-Type", "text/html");
  setContentLengthHeader(response, bodyLen);
  response.setHeader("Connection", "close");
  response.beginBody(bodyLen);
  response.write_P((PGM_P)bodyProgmem, bodyLen);
}

static void sendNotFound(HttpResponse& response) {
  sendProgmemHtml(response, NOT_FOUND_PAGE, 404);
}

static bool parseLongStrict(const char* text, long& out) {
  if (!text || text[0] == '\0') return false;
  char* end = nullptr;
  long parsed = strtol(text, &end, 10);
  if (end == text || (end && *end != '\0')) return false;
  out = parsed;
  return true;
}

static bool parseBool01(const char* text, bool& out) {
  long parsed = 0;
  if (!parseLongStrict(text, parsed)) return false;
  if (parsed == 0) { out = false; return true; }
  if (parsed == 1) { out = true; return true; }
  return false;
}

static void sendJSON(HttpResponse& response) {
  const auto& state = NanoComm::currentSample;
  CanonicalSwingSample swing = {};
  CanonicalPpsSample pps = {};
  const bool haveSwing = NanoComm::getCanonicalSwingSample(swing);
  const bool havePps = NanoComm::getCanonicalPpsSample(pps);
  beginJsonResponse(response);
  response.print(F("{\"config_received\":"));
  response.print(state.session.config_received ? F("true") : F("false"));
  response.print(F(",\"metadata_ready\":"));
  response.print(NanoComm::metadataReady() ? F("true") : F("false"));
  response.print(F(",\"protocol_error\":"));
  response.print(NanoComm::hasProtocolError() ? F("true") : F("false"));
  response.print(F(",\"protocol_version\":")); response.print((unsigned)state.session.protocol_version);
  response.print(F(",\"nominal_hz\":")); response.print((unsigned long)state.session.nominal_hz);
  response.print(F(",\"firmware\":\"")); response.print(state.session.firmware); response.print(F("\""));
  response.print(F(",\"canonical_swing_schema_id\":\"")); response.print(state.session.canonical_swing_schema_id); response.print(F("\""));
  response.print(F(",\"canonical_pps_schema_id\":\"")); response.print(state.session.canonical_pps_schema_id); response.print(F("\""));
  response.print(F(",\"canonical_swing_schema_received\":")); response.print(state.session.canonical_swing_schema_received ? F("true") : F("false"));
  response.print(F(",\"canonical_pps_schema_received\":")); response.print(state.session.canonical_pps_schema_received ? F("true") : F("false"));
  response.print(F(",\"swing_available\":")); response.print(haveSwing ? F("true") : F("false"));
  response.print(F(",\"pps_available\":")); response.print(havePps ? F("true") : F("false"));
  if (haveSwing) {
    response.print(F(",\"swing\":{\"seq\":")); response.print((unsigned long)swing.seq);
    response.print(F(",\"edge0_tcb0\":")); response.print((unsigned long)swing.edge0_tcb0);
    response.print(F(",\"edge1_tcb0\":")); response.print((unsigned long)swing.edge1_tcb0);
    response.print(F(",\"edge2_tcb0\":")); response.print((unsigned long)swing.edge2_tcb0);
    response.print(F(",\"edge3_tcb0\":")); response.print((unsigned long)swing.edge3_tcb0);
    response.print(F(",\"edge4_tcb0\":")); response.print((unsigned long)swing.edge4_tcb0);
    response.print(F(",\"drop_ir\":")); response.print((unsigned long)swing.drop_ir);
    response.print(F(",\"drop_pps\":")); response.print((unsigned long)swing.drop_pps);
    response.print(F(",\"drop_swing\":")); response.print((unsigned long)swing.drop_swing);
    response.print(F("}"));
  }
  if (havePps) {
    response.print(F(",\"pps\":{\"seq\":")); response.print((unsigned long)pps.seq);
    response.print(F(",\"edge_tcb0\":")); response.print((unsigned long)pps.edge_tcb0);
    response.print(F(",\"gps_status\":")); response.print((unsigned)pps.gps_status);
    response.print(F(",\"gps_status_text\":\"")); response.print(gpsStatusToStr(pps.gps_status)); response.print(F("\""));
    response.print(F(",\"holdover_age_ms\":")); response.print((unsigned long)pps.holdover_age_ms);
    response.print(F(",\"cap16\":")); response.print((unsigned)pps.cap16);
    response.print(F(",\"latency16\":")); response.print((unsigned)pps.latency16);
    response.print(F(",\"now32\":")); response.print((unsigned long)pps.now32);
    response.print(F(",\"drop_pps\":")); response.print((unsigned long)pps.drop_pps);
    response.print(F("}"));
  }
  detail::appendJsonEnvironmentalFields(response, state.temperature_C, state.humidity_pct, state.pressure_hPa);
  appendJsonHealth(response);
  response.println(F("}"));
}

static void handleJsonRequest(HttpRequest& request, HttpResponse& response) {
  (void)request;
  noteHttpRequest();
  sendJSON(response);
}

static void applyLoggerConfig(const UnoConfig& unoCfg) {
  SDLogger::setLogMode(unoCfg.logDaily ? SDLogger::LogMode::Daily : SDLogger::LogMode::Continuous);
  SDLogger::setStartupPolicy(policyFromConfig(unoCfg));
  SDLogger::setFilename(unoCfg.logBaseName);
  if (unoCfg.logEnabled) {
    SDLogger::startLogging(SDLogger::getLogMode(), false);
  } else {
    SDLogger::stopLogging();
  }
}

static void sendHomePage(HttpResponse& response) {
  sendProgmemHtml(response, HOME_PAGE);
}

static void handleHomePage(HttpRequest& request, HttpResponse& response) {
  (void)request;
  noteHttpRequest();
  sendHomePage(response);
}

static void renderLoggingPage(HttpResponse& response) {
  response.setStatusCode(F("200 OK"));
  response.setHeader("Content-Type", "text/html");
  response.setHeader("Connection", "close");
  response.println(F("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Logging</title></head><body>"));
  response.println(F("<h2>Logging Control</h2>"));
  response.print(F("<p>Status: "));
  response.print(SDLogger::isLogging() ? F("Logging") : F("Stopped"));
  response.print(F(" | SD: "));
  switch (SDLogger::sdHealth()) {
    case SDLogger::SdHealth::Mounted: response.print(F("Mounted")); break;
    case SDLogger::SdHealth::Missing: response.print(F("Missing")); break;
    case SDLogger::SdHealth::Recovering: response.print(F("Recovering")); break;
    case SDLogger::SdHealth::Fault: default: response.print(F("Fault")); break;
  }
  response.print(F(" | Mode: "));
  response.print(SDLogger::getLogMode() == SDLogger::LogMode::Daily ? F("Daily rollover") : F("Continuous"));
  response.print(F(" | Active file: "));
  response.print(SDLogger::getActiveFilename());
  response.print(F(" | Startup policy: "));
  response.print(policyLabel(SDLogger::getStartupPolicy()));
  response.println(F("</p>"));

  response.print(F("<p>Time sync: "));
  if (SDLogger::hasTimeSync()) {
    response.print(F("OK (age "));
    response.print(SDLogger::secondsSinceLastSync());
    response.println(F(" s)"));
  } else {
    response.println(F("waiting for NTP"));
  }
  response.println(F("</p>"));

  response.println(F("<h3>Settings</h3>"));
  response.println(F("<form action='/log' method='get'>"));
  response.println(F("Mode: <select name='mode'>"));
  response.print(F("<option value='continuous'")); if (SDLogger::getLogMode() == SDLogger::LogMode::Continuous) response.print(F(" selected")); response.println(F(">Continuous</option>"));
  response.print(F("<option value='daily'")); if (SDLogger::getLogMode() == SDLogger::LogMode::Daily) response.print(F(" selected")); response.println(F(">Daily rollover</option></select><br>"));
  response.print(F("Base filename (continuous): <input name='file' value='")); response.print(SDLogger::getFilename()); response.println(F("'><br>"));
  response.println(F("Startup policy: <select name='policy'>"));
  response.print(F("<option value='overwrite'")); if (SDLogger::getStartupPolicy() == SDLogger::LogStartupPolicy::Overwrite) response.print(F(" selected")); response.println(F(">Overwrite existing files</option>"));
  response.print(F("<option value='append'")); if (SDLogger::getStartupPolicy() == SDLogger::LogStartupPolicy::Append) response.print(F(" selected")); response.println(F(">Preserve files, start new set</option>"));
  response.print(F("<option value='archive'")); if (SDLogger::getStartupPolicy() == SDLogger::LogStartupPolicy::ArchiveAndStartFresh) response.print(F(" selected")); response.println(F(">Archive existing files, start fresh</option></select><br>"));
  response.println(F("<input type='submit' value='Save Settings'></form>"));

  response.println(F("<h3>Actions</h3>"));
  response.println(F("<form action='/log' method='get'><input type='hidden' name='log' value='1'><input type='submit' value='Start Logging'></form>"));
  response.println(F("<form action='/log' method='get'><input type='hidden' name='log' value='0'><input type='submit' value='Stop Logging'></form>"));
  response.println(F("<form action='/log' method='get'><input type='hidden' name='restart' value='1'><input type='submit' value='Restart (same file)'></form>"));
  response.println(F("<form action='/log' method='get'><input type='hidden' name='restart' value='new'><input type='submit' value='Restart with new file'></form>"));

  response.println(F("<p><a href='/logfiles'>Log files</a> | <a href='/' aria-label='Return to home page'>Home</a></p>"));
  response.println(F("<hr><small>UNO R4 Pendulum Logger</small></body></html>"));
}

static void handleLogRequest(HttpRequest& request, HttpResponse& response) {
  noteHttpRequest();
  QueryParams query(request.query());
  bool hasQuery = !query.empty();

  UnoConfig unoCfg = getCurrentUnoConfig();
  UnoConfig candidate = unoCfg;

  SDLogger::setLogMode(unoCfg.logDaily ? SDLogger::LogMode::Daily : SDLogger::LogMode::Continuous);
  SDLogger::setStartupPolicy(policyFromConfig(unoCfg));
  SDLogger::setFilename(unoCfg.logBaseName);

  bool startCmd = false, stopCmd = false, restartCmd = false, restartNew = false;
  bool dirty = false;
  char val[LOG_FILENAME_LEN] = {0};

  if (hasQuery) {
    char buf[32] = {0};
    if (query.copyValue("mode=", buf, sizeof(buf))) {
      if (strcmp(buf, "daily") == 0) {
        candidate.logDaily = true;
        dirty = true;
      } else if (strcmp(buf, "continuous") == 0) {
        candidate.logDaily = false;
        dirty = true;
      }
    }
    if (query.copyValue("append=", buf, sizeof(buf))) {
      bool parsed = false;
      if (parseBool01(buf, parsed)) {
        candidate.logAppend = parsed;
        candidate.logStartupPolicy = parsed ? LOG_STARTUP_POLICY_APPEND : LOG_STARTUP_POLICY_OVERWRITE;
        dirty = true;
      }
    }
    if (query.copyValue("policy=", buf, sizeof(buf))) {
      uint8_t parsedPolicy = LOG_STARTUP_POLICY_DEFAULT;
      if (parsePolicyValue(buf, parsedPolicy)) {
        candidate.logStartupPolicy = parsedPolicy;
        candidate.logAppend = (parsedPolicy == LOG_STARTUP_POLICY_APPEND);
        dirty = true;
      }
    }
    if (query.copyValue("file=", val, sizeof(val))) {
      strncpy(candidate.logBaseName, val, LOG_FILENAME_LEN);
      candidate.logBaseName[LOG_FILENAME_LEN-1] = 0;
      dirty = true;
    }
    if (query.copyValue("log=", buf, sizeof(buf))) {
      bool parsed = false;
      if (parseBool01(buf, parsed)) {
        startCmd = parsed;
        stopCmd = !parsed;
      }
    }
    if (query.copyValue("restart=", buf, sizeof(buf))) {
      restartCmd = true;
      restartNew = (strcmp(buf, "new") == 0);
    }
  }

  sanitizeUnoConfig(candidate);
  SDLogger::setLogMode(candidate.logDaily ? SDLogger::LogMode::Daily : SDLogger::LogMode::Continuous);
  SDLogger::setStartupPolicy(policyFromConfig(candidate));
  SDLogger::setFilename(candidate.logBaseName);

  if (restartCmd) {
    SDLogger::restartLogging(restartNew);
  } else if (startCmd) {
    SDLogger::startLogging(SDLogger::getLogMode(), restartNew);
  } else if (stopCmd) {
    SDLogger::stopLogging();
  }

  candidate.logEnabled = SDLogger::isLogging();
  candidate.logDaily   = (SDLogger::getLogMode() == SDLogger::LogMode::Daily);
  candidate.logAppend  = SDLogger::getAppendMode();
  candidate.logStartupPolicy = configValueFromPolicy(SDLogger::getStartupPolicy());
  strncpy(candidate.logBaseName, SDLogger::getFilename(), LOG_FILENAME_LEN);
  candidate.logBaseName[LOG_FILENAME_LEN-1] = 0;
  sanitizeUnoConfig(candidate);

  if (hasQuery && (dirty || startCmd || stopCmd || restartCmd)) {
    applyUnoConfig(candidate);
    saveUnoConfig(candidate);
  }

  renderLoggingPage(response);
}

static void handleLogFilesRequest(HttpRequest& request, HttpResponse& response) {
  (void)request;
  noteHttpRequest();
  response.setStatusCode(F("200 OK"));
  response.setHeader("Content-Type", "text/html");
  response.setHeader("Connection", "close");
  response.println(F("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Log Files</title></head><body>"));
  response.println(F("<h2>Log Files</h2>"));

  File root = SD.open("/");
  if (!root) {
    response.println(F("<p>SD not available.</p>"));
  } else {
    response.println(F("<ul>"));
    uint16_t listed = 0;
    static const uint16_t MAX_LOGFILE_LIST = 64;
    File entry = root.openNextFile();
    while (entry) {
      if (!entry.isDirectory()) {
        char nameBuf[LOG_FILENAME_LEN] = {0};
        const char* nm = entry.name();
        if (nm) {
          strncpy(nameBuf, nm, sizeof(nameBuf)-1);
        }
        if (nameBuf[0] && SDLogger::isValidFilename(nameBuf)) {
          if (listed >= MAX_LOGFILE_LIST) {
            entry.close();
            break;
          }
          response.print(F("<li>"));
          response.print("<a href='/download?file=");
          response.print(nameBuf);
          response.print("'>");
          response.print(nameBuf);
          response.print(F("</a> ("));
          response.print((unsigned long)entry.size());
          response.println(F(" bytes)</li>"));
          ++listed;
        }
      }
      File next = root.openNextFile();
      entry.close();
      entry = next;
    }
    response.println(F("</ul>"));
    root.close();
  }

  response.println(F("<p><a href='/uno'>UNO Tunables</a> | <a href='/' aria-label='Return to home page'>Home</a></p>"));
  response.println(F("<hr><small>UNO R4 Pendulum Logger</small></body></html>"));
}

static void handleDownloadRequest(HttpRequest& request, HttpResponse& response) {
  noteHttpRequest();
  httpTelemetry.download_requests++;
  if (SDLogger::isLogging()) {
    noteHttpRejected();
    httpTelemetry.download_rejected++;
    sendBusyText(response, F("503 Service Unavailable"), F("download disabled while logging is active"));
    return;
  }
  QueryParams query(request.query());
  char fname[LOG_FILENAME_LEN] = {0};
  if (!query.copyValue("file=", fname, sizeof(fname)) || !SDLogger::isValidFilename(fname)) {
    sendNotFound(response);
    return;
  }
  File f = SD.open(fname, FILE_READ);
  if (!f) {
    sendNotFound(response);
    return;
  }

  size_t fsize = f.size();
  response.setStatusCode(F("200 OK"));
  response.setHeader("Content-Type", "text/csv");
  char contentDisposition[64] = {0};
  snprintf(contentDisposition, sizeof(contentDisposition), "attachment; filename=\"%s\"", fname);
  response.setHeader("Content-Disposition", contentDisposition);
  response.setHeader("Connection", "close");
  char contentLength[16] = {0};
  snprintf(contentLength, sizeof(contentLength), "%lu", (unsigned long)fsize);
  response.setHeader("Content-Length", contentLength);
  response.beginBody(fsize);
  uint8_t buf[128];
  while (f.available()) {
    size_t n = f.read(buf, sizeof(buf));
    if (n) response.write(buf, n);
  }
  f.close();
}

static void handleWiFiConfigRequest(HttpRequest& request, HttpResponse& response) {
  noteHttpRequest();
  if (request.method() == HttpMethod::GET) {
    QueryParams query(request.query());
    if (!query.empty()) {
      WiFiConfig::setProvisioning(query.flagEnabled("prov="));
    }
  }

  if (request.method() == HttpMethod::POST) {
    QueryParams bodyParams(request.body());
    char ns[64] = {0};
    char np[64] = {0};
    if (bodyParams.copyValue("prov=", ns, sizeof(ns))) {
      const bool prov = (atoi(ns) != 0);
      WiFiConfig::setProvisioning(prov);
      if (!prov) {
        WiFiConfig::requestReconnect();
      }
    }
    bool connectNow = bodyParams.flagEnabled("connect=");
    bodyParams.copyValue("ssid=", ns, sizeof(ns));
    bodyParams.copyValue("pass=", np, sizeof(np));
    if (ns[0]) {
      WiFiStorage::saveCredentials(ns, np);
      WiFiConfig::setCredentials(ns, np, connectNow);
    }
    if (connectNow) {
      WiFiConfig::setProvisioning(false);
      WiFiConfig::requestReconnect();
    }
    sendRedirect(response, "/wifi?saved=1");
    return;
  }

  response.setStatusCode(F("200 OK"));
  response.setHeader("Content-Type", "text/html");
  response.setHeader("Connection", "close");
  response.println(F("<!DOCTYPE html><html><head><meta charset='utf-8'><title>WiFi Config</title></head><body>"));
  response.println(F("<h2>WiFi Configuration</h2>"));
  response.println(F("<form action='/wifi' method='post'>"));
  response.print(F("SSID: <input name='ssid' value='")); response.print(WiFiConfig::ssid()); response.println(F("'><br>"));
  response.println(F("Password: <input name='pass' type='password'><br><br>"));
  response.println(F("<button type='submit' name='connect' value='0'>Save (stay in AP)</button>"));
  response.println(F("<button type='submit' name='connect' value='1'>Save &amp; connect</button>"));
  response.println(F("</form>"));
  response.println(F("<a href='/' aria-label='Return to home page'>Home</a>"));
  response.println(F("<h3>Provisioning</h3>"));
  response.print(F("<p>Status: "));
  response.print(WiFiConfig::isProvisioning() ? F("ENABLED (AP forced on)") : F("DISABLED"));
  response.println(F("</p>"));
  response.println(F("<form action='/wifi' method='post'><input type='hidden' name='prov' value='1'><input type='submit' value='Enable provisioning'></form>"));
  response.println(F("<form action='/wifi' method='post'><input type='hidden' name='prov' value='0'><input type='submit' value='Disable provisioning &amp; reconnect'></form>"));
  response.println(F("<p><a href='/' aria-label='Return to home page'>Home</a></p>"));
  response.println(F("<hr><small>UNO R4 Pendulum Logger</small></body></html>"));
}

static void handleUnoRequest(HttpRequest& request, HttpResponse& response) {
  noteHttpRequest();
  const bool isPost = request.method() == HttpMethod::POST;
  QueryParams query(isPost ? request.body() : request.query());
  bool save = isPost && !query.empty();
  UnoConfig unoCfg = getCurrentUnoConfig();
  if (save) {
    const unsigned long postStartUs = micros();
    UnoConfig candidate = unoCfg;
    char val[32] = {0};
    bool dirty = false;
    bool validRating = true;
    bool ratingDirty = false;
    if (query.copyExactValue("oledShortMinutes=", val, sizeof(val))) {
      validRating = OledRatingConfig::parseMinutes(val, candidate.oledShortMinutes);
      ratingDirty = true;
    }
    if (query.copyExactValue("oledLongMinutes=", val, sizeof(val))) {
      validRating = OledRatingConfig::parseMinutes(val, candidate.oledLongMinutes) && validRating;
      ratingDirty = true;
    }
    if (!validRating || !OledRatingConfig::valid(candidate.oledShortMinutes, candidate.oledLongMinutes)) {
      noteHttpRejected();
      sendBusyText(response, F("400 Bad Request"),
                   F("OLED half-lives require whole minutes: short 1-30, long 15-360, long at least twice short. No settings saved."));
      return;
    }
    if (query.copyValue("logEnabled=", val, sizeof(val))) {
      candidate.logEnabled = (atoi(val) != 0);
      dirty = true;
    }
    if (query.copyValue("logDaily=", val, sizeof(val))) {
      candidate.logDaily = (atoi(val) != 0);
      dirty = true;
    }
    if (query.copyValue("logAppend=", val, sizeof(val))) {
      candidate.logAppend = (atoi(val) != 0);
      candidate.logStartupPolicy = candidate.logAppend ? LOG_STARTUP_POLICY_APPEND : LOG_STARTUP_POLICY_OVERWRITE;
      dirty = true;
    }
    if (query.copyValue("logStartupPolicy=", val, sizeof(val))) {
      uint8_t parsedPolicy = LOG_STARTUP_POLICY_DEFAULT;
      if (parsePolicyValue(val, parsedPolicy)) {
        candidate.logStartupPolicy = parsedPolicy;
        candidate.logAppend = (parsedPolicy == LOG_STARTUP_POLICY_APPEND);
        dirty = true;
      }
    }
    const unsigned long parseDoneUs = micros();
    if (dirty || ratingDirty) {
      sanitizeUnoConfig(candidate);
      const bool loggingChanged = candidate.logEnabled != unoCfg.logEnabled ||
          candidate.logDaily != unoCfg.logDaily ||
          candidate.logStartupPolicy != unoCfg.logStartupPolicy ||
          strcmp(candidate.logBaseName, unoCfg.logBaseName) != 0;
      applyUnoConfig(candidate);
      saveUnoConfig(candidate);
      if (loggingChanged) applyLoggerConfig(candidate);
    }
    const unsigned long applyDoneUs = micros();
    sendRedirect(response, "/uno?saved=1");
    const unsigned long respondDoneUs = micros();
    char evt[180] = {0};
    snprintf(evt, sizeof(evt), "http.post,/uno,parse_us,%lu,apply_us,%lu,respond_us,%lu,total_us,%lu,ok,1", parseDoneUs - postStartUs, applyDoneUs - parseDoneUs, respondDoneUs - applyDoneUs, respondDoneUs - postStartUs);
    SDLogger::logUnoEvent("http.post", evt);
    return;
  }

  sanitizeUnoConfig(unoCfg);

  response.setStatusCode(F("200 OK"));
  response.setHeader("Content-Type", "text/html");
  response.setHeader("Connection", "close");
  response.println(F("<!DOCTYPE html><html><head><meta charset='utf-8'><title>UNO Tunables</title></head><body>"));
  response.println(F("<h2>UNO Tunables</h2>"));
  response.println(F("<form action='/uno' method='post'>"));
  response.println(F("<h3>OLED rating</h3><p>EWMA half-lives in minutes. Long must be at least twice short. Display only.</p>"));
  response.print(F("Short: <input type='number' name='oledShortMinutes' min='1' max='30' step='1' value='")); response.print(unoCfg.oledShortMinutes); response.println(F("' required><br>"));
  response.print(F("Long: <input type='number' name='oledLongMinutes' min='15' max='360' step='1' value='")); response.print(unoCfg.oledLongMinutes); response.println(F("' required><br>"));
  response.println(F("<p>Defaults: 5 minutes / 60 minutes. Changing a half-life restarts the display estimates.</p>"));
  response.println(F("<h3>Logging</h3>"));
  response.print(F("enabled (0/1): <input name='logEnabled' value='")); response.print(unoCfg.logEnabled ? 1 : 0); response.println(F("'><br>"));
  response.print(F("daily mode (0/1): <input name='logDaily' value='")); response.print(unoCfg.logDaily ? 1 : 0); response.println(F("'><br>"));
  response.println(F("startup policy: <select name='logStartupPolicy'>"));
  response.print(F("<option value='1'")); if (unoCfg.logStartupPolicy == LOG_STARTUP_POLICY_OVERWRITE) response.print(F(" selected")); response.println(F(">Overwrite existing files</option>"));
  response.print(F("<option value='0'")); if (unoCfg.logStartupPolicy == LOG_STARTUP_POLICY_APPEND) response.print(F(" selected")); response.println(F(">Preserve files, start new set</option>"));
  response.print(F("<option value='2'")); if (unoCfg.logStartupPolicy == LOG_STARTUP_POLICY_ARCHIVE) response.print(F(" selected")); response.println(F(">Archive existing files, start fresh</option></select><br>"));

  response.println(F("<br>"));
  response.println(F("<input type='submit' value='Save'></form>"));
  response.println(F("<p>Configure WiFi on the <a href='/wifi'>WiFi page</a>.</p>"));
  response.println(F("<p><a href='/logfiles'>Log files</a></p>"));
  response.println(F("<a href='/' aria-label='Return to home page'>Home</a>"));
  response.println(F("<hr><small>UNO R4 Pendulum Logger</small></body></html>"));
}

static void renderResetPage(HttpResponse& response, const char* message = nullptr) {
  char defaultSsid[MAX_SSID_LEN] = {0};
  char defaultPass[MAX_PASS_LEN] = {0};
  WiFiStorage::getFirmwareDefaultCredentials(defaultSsid, defaultPass);

  response.setStatusCode(F("200 OK"));
  response.setHeader("Content-Type", "text/html");
  response.setHeader("Connection", "close");
  response.println(F("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Factory Reset</title></head><body>"));
  response.println(F("<h2>Factory Reset UNO + WiFi EEPROM Defaults</h2>"));
  response.println(F("<p>This rewrites EEPROM-backed settings using the values compiled into the firmware. Existing EEPROM contents are ignored.</p>"));
  response.println(F("<ul>"));
  response.println(F("<li>WiFi credentials are rewritten from <code>WIFI_SSID</code> / <code>WIFI_PASS</code>.</li>"));
  response.println(F("<li>WiFi reconnect is requested.</li>"));
  response.println(F("</ul>"));
  response.print(F("<p>Firmware default SSID: <code>"));
  response.print(defaultSsid[0] ? defaultSsid : "(empty)");
  response.println(F("</code></p>"));
  response.print(F("<p>Firmware default password: <code>"));
  response.print(defaultPass[0] ? "(configured)" : "(empty)");
  response.println(F("</code></p>"));
  if (message && message[0] != '\0') {
    response.print(F("<p><strong>"));
    response.print(message);
    response.println(F("</strong></p>"));
  }
  response.println(F("<form action='/reset' method='post'>"));
  response.println(F("<p>Type <code>RESET</code> to confirm: <input name='confirm' value=''></p>"));
  response.println(F("<button type='submit'>Factory reset UNO + WiFi EEPROM defaults</button>"));
  response.println(F("</form>"));
  response.println(F("<p><a href='/uno'>UNO Tunables</a> | <a href='/wifi'>WiFi Config</a> | <a href='/' aria-label='Return to home page'>Home</a></p>"));
  response.println(F("<hr><small>UNO R4 Pendulum Logger</small></body></html>"));
}

static void handleResetRequest(HttpRequest& request, HttpResponse& response) {
  noteHttpRequest();
  if (request.method() == HttpMethod::POST) {
    QueryParams bodyParams(request.body());
    char confirm[16] = {0};
    bodyParams.copyValue("confirm=", confirm, sizeof(confirm));
    if (strcmp(confirm, "RESET") != 0) {
      renderResetPage(response, "Reset not performed. Type RESET exactly to confirm.");
      return;
    }

    UnoConfig unoCfg;
    saveFactoryDefaults(unoCfg);
    applyUnoConfig(unoCfg);
    applyLoggerConfig(unoCfg);

    char ssid[MAX_SSID_LEN] = {0};
    char pass[MAX_PASS_LEN] = {0};
    WiFiStorage::getFirmwareDefaultCredentials(ssid, pass);
    WiFiStorage::saveFirmwareDefaultCredentials();
    WiFiConfig::setCredentials(ssid, pass, ssid[0] != '\0');
    if (ssid[0] != '\0') {
      WiFiConfig::setProvisioning(false);
      WiFiConfig::requestReconnect();
    } else {
      WiFiConfig::setProvisioning(true);
    }

    return;
  }

  renderResetPage(response);
}

static bool nanoSet(const char* param, const char* val) {
  return NanoComm::requestSet(param, val);
}

static void renderNanoLoadingPage(HttpResponse& response, bool refreshQueued, bool waitingForHeader) {
  response.setStatusCode(F("200 OK"));
  response.setHeader("Content-Type", "text/html");
  response.setHeader("Connection", "close");
  response.println(F("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Nano Tunables</title><meta http-equiv='refresh' content='2'></head><body>"));
  response.println(F("<h2>Nano Tunables</h2>"));
  if (waitingForHeader) {
    response.println(F("<p>Waiting for Nano startup metadata (CFG and both SCH declarations).</p>"));
  } else if (refreshQueued) {
    response.println(F("<p>Nano tunables refresh requested. This page will retry automatically once structured STS replies repopulate the cache.</p>"));
  } else {
    response.println(F("<p>Waiting for Nano tunables to finish refreshing. This page will retry automatically.</p>"));
  }
  response.println(F("<p>No placeholder zero values are shown while the Nano cache is incomplete.</p>"));
  response.println(F("<p><a href='/nano'>Retry now</a> | <a href='/' aria-label='Return to home page'>Home</a></p>"));
  response.println(F("<hr><small>UNO R4 Pendulum Logger</small></body></html>"));
}

static void renderNanoResetPage(HttpResponse& response, const char* message = nullptr) {
  response.setStatusCode(F("200 OK"));
  response.setHeader("Content-Type", "text/html");
  response.setHeader("Connection", "close");
  response.println(F("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Nano Reset</title></head><body>"));
  response.println(F("<h2>Reset Nano Tunables to Firmware Defaults</h2>"));
  response.println(F("<p>This sends <code>reset defaults</code> to the Nano only. It does not change UNO/shared EEPROM settings or WiFi credentials.</p>"));
  if (message && message[0] != '\0') {
    response.print(F("<p><strong>"));
    response.print(message);
    response.println(F("</strong></p>"));
  }
  response.println(F("<form action='/nano-reset' method='post'>"));
  response.println(F("<p>Type <code>NANO RESET</code> to confirm: <input name='confirm' value=''></p>"));
  response.println(F("<button type='submit'>Queue Nano defaults reset</button>"));
  response.println(F("</form>"));
  response.println(F("<p><a href='/nano'>Nano Tunables</a> | <a href='/uno'>UNO Tunables</a> | <a href='/wifi'>WiFi Config</a> | <a href='/' aria-label='Return to home page'>Home</a></p>"));
  response.println(F("<hr><small>UNO R4 Pendulum Logger</small></body></html>"));
}

static void handleNanoResetRequest(HttpRequest& request, HttpResponse& response) {
  noteHttpRequest();
  if (request.method() == HttpMethod::POST) {
    QueryParams bodyParams(request.body());
    char confirm[16] = {0};
    bodyParams.copyValue("confirm=", confirm, sizeof(confirm));
    if (strcmp(confirm, "NANO RESET") != 0) {
      renderNanoResetPage(response, "Nano reset not queued. Type NANO RESET exactly to confirm.");
      return;
    }

    if (NanoComm::requestResetDefaults()) {
      renderNanoResetPage(response, "Nano defaults reset queued. Return to /nano and wait for the refreshed values to load.");
    } else {
      renderNanoResetPage(response, "Nano defaults reset could not be queued right now. Try again after pending Nano commands finish.");
    }
    return;
  }

  renderNanoResetPage(response);
}

static void handleNanoRequest(HttpRequest& request, HttpResponse& response) {
  noteHttpRequest();
  const bool isPost = request.method() == HttpMethod::POST;
  const char* source = isPost ? request.body() : request.query();
  QueryParams params(source);
  bool save = !params.empty();
  char val[32];
  if (save) {
    const unsigned long postStartUs = micros();
    uint8_t queued = 0;
    if (params.copyValue("ppsFastShift=", val, sizeof(val)) && nanoSet(PARAM_PPS_FAST_SHIFT, val)) ++queued;
    if (params.copyValue("ppsSlowShift=", val, sizeof(val)) && nanoSet(PARAM_PPS_SLOW_SHIFT, val)) ++queued;
    if (params.copyValue("ppsBlendLoPpm=", val, sizeof(val)) && nanoSet(PARAM_PPS_BLEND_LO_PPM, val)) ++queued;
    if (params.copyValue("ppsBlendHiPpm=", val, sizeof(val)) && nanoSet(PARAM_PPS_BLEND_HI_PPM, val)) ++queued;
    if (params.copyValue("ppsLockRppm=", val, sizeof(val)) && nanoSet(PARAM_PPS_LOCK_R_PPM, val)) ++queued;
    if ((params.copyValue("ppsLockMadTicks=", val, sizeof(val)) || params.copyValue("ppsLockJppm=", val, sizeof(val))) && nanoSet(PARAM_PPS_LOCK_MAD_TICKS, val)) ++queued;
    if (params.copyValue("ppsUnlockRppm=", val, sizeof(val)) && nanoSet(PARAM_PPS_UNLOCK_R_PPM, val)) ++queued;
    if ((params.copyValue("ppsUnlockMadTicks=", val, sizeof(val)) || params.copyValue("ppsUnlockJppm=", val, sizeof(val))) && nanoSet(PARAM_PPS_UNLOCK_MAD_TICKS, val)) ++queued;
    if (params.copyValue("ppsLockCount=", val, sizeof(val)) && nanoSet(PARAM_PPS_LOCK_COUNT, val)) ++queued;
    if (params.copyValue("ppsUnlockCount=", val, sizeof(val)) && nanoSet(PARAM_PPS_UNLOCK_COUNT, val)) ++queued;
    if (params.copyValue("ppsHoldoverMs=", val, sizeof(val)) && nanoSet(PARAM_PPS_HOLDOVER_MS, val)) ++queued;
    if (params.copyValue("ppsStaleMs=", val, sizeof(val)) && nanoSet(PARAM_PPS_STALE_MS, val)) ++queued;
    if (params.copyValue("ppsIsrStaleMs=", val, sizeof(val)) && nanoSet(PARAM_PPS_ISR_STALE_MS, val)) ++queued;
    if (params.copyValue("ppsCfgReemitDelayMs=", val, sizeof(val)) && nanoSet(PARAM_PPS_CFG_REEMIT_DELAY_MS, val)) ++queued;
    if (params.copyValue("ppsAcquireMinMs=", val, sizeof(val)) && nanoSet(PARAM_PPS_ACQUIRE_MIN_MS, val)) ++queued;
    if (params.copyValue("ppsMetrologyGraceMs=", val, sizeof(val)) && nanoSet(PARAM_PPS_METROLOGY_GRACE_MS, val)) ++queued;
    (void)queued;
    sendRedirect(response, "/nano?saved=1");
    return;
  }

  if (!NanoComm::hasAllCachedParams()) {
    const bool waitingForHeader = !NanoComm::metadataReady();
    bool refreshQueued = false;
    if (!waitingForHeader && !NanoComm::isRefreshInProgress()) {
      NanoComm::requestRefreshAll();
      refreshQueued = true;
    }
    renderNanoLoadingPage(response, refreshQueued, waitingForHeader);
    return;
  }

  int ppsFastShift = 0;
  int ppsSlowShift = 0;
  int ppsBlendLoPpm = 0;
  int ppsBlendHiPpm = 0;
  int ppsLockRppm = 0;
  int ppsLockMadTicks = 0;
  int ppsUnlockRppm = 0;
  int ppsUnlockMadTicks = 0;
  int ppsLockCount = 0;
  int ppsUnlockCount = 0;
  int ppsHoldoverMs = 0;
  int ppsStaleMs = 0;
  int ppsIsrStaleMs = 0;
  int ppsCfgReemitDelayMs = 0;
  int ppsAcquireMinMs = 0;
  int ppsMetrologyGraceMs = 0;

  NanoComm::getCachedParam(PARAM_PPS_FAST_SHIFT, val, sizeof(val));  ppsFastShift = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_SLOW_SHIFT, val, sizeof(val));  ppsSlowShift = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_BLEND_LO_PPM, val, sizeof(val)); ppsBlendLoPpm = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_BLEND_HI_PPM, val, sizeof(val)); ppsBlendHiPpm = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_LOCK_R_PPM, val, sizeof(val));  ppsLockRppm = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_LOCK_MAD_TICKS, val, sizeof(val));  ppsLockMadTicks = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_UNLOCK_R_PPM, val, sizeof(val)); ppsUnlockRppm = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_UNLOCK_MAD_TICKS, val, sizeof(val)); ppsUnlockMadTicks = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_LOCK_COUNT, val, sizeof(val));  ppsLockCount = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_UNLOCK_COUNT, val, sizeof(val)); ppsUnlockCount = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_HOLDOVER_MS, val, sizeof(val));  ppsHoldoverMs = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_STALE_MS, val, sizeof(val));    ppsStaleMs = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_ISR_STALE_MS, val, sizeof(val)); ppsIsrStaleMs = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_CFG_REEMIT_DELAY_MS, val, sizeof(val)); ppsCfgReemitDelayMs = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_ACQUIRE_MIN_MS, val, sizeof(val)); ppsAcquireMinMs = atoi(val);
  NanoComm::getCachedParam(PARAM_PPS_METROLOGY_GRACE_MS, val, sizeof(val)); ppsMetrologyGraceMs = atoi(val);

  response.setStatusCode(F("200 OK"));
  response.setHeader("Content-Type", "text/html");
  response.setHeader("Connection", "close");
  response.println(F("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Nano Tunables</title></head><body>"));
  response.println(F("<h2>Nano Tunables</h2>"));
  response.println(F("<p>This page shows cached Nano values after they have been confirmed via structured STS replies.</p>"));
  response.println(F("<form action='/nano' method='post'>"));
  response.print(F("ppsFastShift: <input name='ppsFastShift' value='")); response.print(ppsFastShift); response.println(F("'><br>"));
  response.print(F("ppsSlowShift: <input name='ppsSlowShift' value='")); response.print(ppsSlowShift); response.println(F("'><br>"));
  response.print(F("ppsBlendLoPpm: <input name='ppsBlendLoPpm' value='")); response.print(ppsBlendLoPpm); response.println(F("'><br>"));
  response.print(F("ppsBlendHiPpm: <input name='ppsBlendHiPpm' value='")); response.print(ppsBlendHiPpm); response.println(F("'><br>"));
  response.print(F("ppsLockRppm: <input name='ppsLockRppm' value='")); response.print(ppsLockRppm); response.println(F("'><br>"));
  response.print(F("ppsLockMadTicks: <input name='ppsLockMadTicks' value='")); response.print(ppsLockMadTicks); response.println(F("'><br>"));
  response.print(F("ppsUnlockRppm: <input name='ppsUnlockRppm' value='")); response.print(ppsUnlockRppm); response.println(F("'><br>"));
  response.print(F("ppsUnlockMadTicks: <input name='ppsUnlockMadTicks' value='")); response.print(ppsUnlockMadTicks); response.println(F("'><br>"));
  response.print(F("ppsLockCount: <input name='ppsLockCount' value='")); response.print(ppsLockCount); response.println(F("'><br>"));
  response.print(F("ppsUnlockCount: <input name='ppsUnlockCount' value='")); response.print(ppsUnlockCount); response.println(F("'><br>"));
  response.print(F("ppsHoldoverMs: <input name='ppsHoldoverMs' value='")); response.print(ppsHoldoverMs); response.println(F("'><br>"));
  response.print(F("ppsStaleMs: <input name='ppsStaleMs' value='")); response.print(ppsStaleMs); response.println(F("'><br>"));
  response.print(F("ppsIsrStaleMs: <input name='ppsIsrStaleMs' value='")); response.print(ppsIsrStaleMs); response.println(F("'><br>"));
  response.print(F("ppsCfgReemitDelayMs: <input name='ppsCfgReemitDelayMs' value='")); response.print(ppsCfgReemitDelayMs); response.println(F("'><br>"));
  response.print(F("ppsAcquireMinMs: <input name='ppsAcquireMinMs' value='")); response.print(ppsAcquireMinMs); response.println(F("'><br>"));
  response.print(F("ppsMetrologyGraceMs: <input name='ppsMetrologyGraceMs' value='")); response.print(ppsMetrologyGraceMs); response.println(F("'><br>"));

  response.println(F("<input type='submit' value='Queue updates'></form>"));
  response.println(F("<a href='/' aria-label='Return to home page'>Home</a>"));
  response.println(F("<hr><small>UNO R4 Pendulum Logger</small></body></html>"));
}

static void handleUnknown(HttpRequest& request, HttpResponse& response) {
  (void)request;
  noteHttpRequest();
  noteHttpRejected();
  sendNotFound(response);
}

static void handleForceReconnectRequest(HttpRequest& request, HttpResponse& response) {
  (void)request;
  noteHttpRequest();
  response.setStatusCode(F("202 Accepted"));
  response.setHeader("Content-Type", "text/plain");
  response.setHeader("Connection", "close");
  response.beginBody();
  response.println(F("force_reconnect scheduled"));
  HttpServer::markListenerInactive("force_reconnect");
  WiFiConfig::requestReconnect();
}

  void begin() {
    beginRoutesOnce();
    restartListener();
  }

  void beginRoutesOnce() {
    if (routesRegistered) return;
    httpServer.on(Method::GET, "/", handleHomePage);
    httpServer.on(Method::GET, "/wifi", handleWiFiConfigRequest);
    httpServer.on(Method::POST, "/wifi", handleWiFiConfigRequest);
    httpServer.on(Method::GET, "/uno", handleUnoRequest);
    httpServer.on(Method::POST, "/uno", handleUnoRequest);
    httpServer.on(Method::GET, "/nano", handleNanoRequest);
    httpServer.on(Method::POST, "/nano", handleNanoRequest);
    httpServer.on(Method::GET, "/logfiles", handleLogFilesRequest);
    httpServer.on(Method::GET, "/download", handleDownloadRequest);
    httpServer.on(Method::GET, "/reset", handleResetRequest);
    httpServer.on(Method::POST, "/reset", handleResetRequest);
    httpServer.on(Method::GET, "/nano-reset", handleNanoResetRequest);
    httpServer.on(Method::POST, "/nano-reset", handleNanoResetRequest);
    httpServer.on(Method::GET, "/force_reconnect", handleForceReconnectRequest);
    httpServer.onNotFound(handleUnknown);
    routesRegistered = true;
  }

  bool isActive() {
    return listenerRecovery.active;
  }

  void markListenerInactive(const char* reason) {
    const uint32_t nowMs = millis();
    const bool transitioned = listenerRecovery.beginRecovery(
        WiFiConfig::networkGeneration(), nowMs, false);
    if (transitioned && reason) {
      logListenerInactive(reason);
    }
  }

  void restartListener() {
    if (!WiFiConfig::networkReadyForHttp() || !WiFiConfig::networkWorkAllowed()) {
      if (listenerRecovery.active || !listenerRecovery.outageLatched) {
        markListenerInactive("network_not_ready");
      }
      listenerRecovery.outageLatched = true;
      return;
    }
    listenerRestartCount++;
    httpServer.begin();
    const uint32_t nowMs = millis();
    lastHttpBeginMs = nowMs;
    const uint32_t generation = WiFiConfig::networkGeneration();
    listenerRecovery.markStarted(generation, nowMs);
    listenerRecovery.outageLatched = false;
    char evt[128] = {0};
    snprintf(evt, sizeof(evt), "event,HTTP_BEGIN,generation,%lu,restarts,%lu",
             (unsigned long)generation, (unsigned long)listenerRestartCount);
    SDLogger::logUnoEvent("http.listener", evt);
    SDLogger::logUnoEvent("http.listener", "event,HTTP_LISTENER_UP");
  }

  void serviceLifecycle() {
    beginRoutesOnce();
    const uint32_t nowMs = millis();
    const bool networkReady = WiFiConfig::networkReadyForHttp() &&
                              WiFiConfig::networkWorkAllowed();
    const detail::LifecycleAction action = listenerRecovery.service(
        nowMs, networkReady, WiFiConfig::networkGeneration(),
        HTTP_RESTART_STABLE_MS, HTTP_RESTART_BACKOFF_MS);
    switch (action) {
      case detail::LifecycleAction::NetworkNotReady:
        logListenerInactive("network_not_ready");
        return;
      case detail::LifecycleAction::NetworkReadyAfterOutage:
        SDLogger::logUnoEvent("http.listener", "event,network_ready_after_outage");
        break;
      case detail::LifecycleAction::GenerationChanged:
        logListenerInactive("network_generation_changed");
        break;
      case detail::LifecycleAction::RestartDue:
        restartListener();
        break;
      case detail::LifecycleAction::None:
      default:
        break;
    }
    if (listenerRecovery.active &&
        (unsigned long)(nowMs - lastHttpBeginMs) > HTTP_LISTENER_REFRESH_MS) {
      httpServer.begin();
      lastHttpBeginMs = nowMs;
      SDLogger::logUnoEvent("http.listener", "event,HTTP_LISTENER_RETRY,reason,periodic_refresh");
    }
  }

  void serviceClients() {
    if (!listenerRecovery.active) return;
    const unsigned long t0 = micros();
    httpServer.poll();
    const unsigned long elapsed = micros() - t0;
    ServiceTelemetry::noteHttpClientService(elapsed);
    if (elapsed > HTTP_SERVICE_BUDGET_US) {
      serviceBudgetExceededCount++;
      char evt[96] = {0};
      snprintf(evt, sizeof(evt),
               "event,service_budget_exceeded,count,%lu,elapsed_us,%lu,budget_us,%lu",
               serviceBudgetExceededCount, elapsed, HTTP_SERVICE_BUDGET_US);
      SDLogger::logUnoEvent("http.health", evt);
    }
  }

} // namespace HttpServer
