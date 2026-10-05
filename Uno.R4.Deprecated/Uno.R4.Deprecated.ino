#include "src/I2cRecovery.h"
// --------------------------------------------------------------
// UNO R4 WiFi Pendulum Logger
// Companion to Nano Every GPS-disciplined pendulum timer
// UNO R4 handles WiFi/AP config, SD logging, OLED UI, env sensors & status
// --------------------------------------------------------------

#include <EEPROM.h>
#include <string.h>
#include <stdio.h>
#include <WiFiS3.h>
#include "src/Config.h"
#include "src/PendulumProtocol.h"
#include "src/WiFiConfig.h"
#include "src/WiFiStorage.h"
#include "src/HttpServer.h"
#include "src/SDLogger.h"
#include "src/Sensors.h"
#include "src/Display.h"
#include "src/NanoComm.h"
#include "src/IngestOrchestrator.h"
#include "src/PublishMirrorQueue.h"
#include "src/MemoryMonitor.h"
#include "src/EEPROMConfig.h"
#include "src/ServiceTelemetry.h"
#include "src/StatusDisplay.h"
#if __has_include(<WDT.h>)
#include <WDT.h>
#define UNO_HAS_WATCHDOG 1
#else
#define UNO_HAS_WATCHDOG 0
#endif

enum LoopPhase : uint8_t {
  PHASE_BOOT, PHASE_LOOP_TOP, PHASE_READ_NANO, PHASE_PARSE_NANO, PHASE_WIFI_STATUS, PHASE_WRITE_SD,
  PHASE_OLED_UPDATE, PHASE_MATRIX_UPDATE, PHASE_WATCHDOG_FEED, PHASE_IDLE
};
static LoopPhase g_phase = PHASE_BOOT;
static LoopPhase g_prevPhase = PHASE_BOOT;
static unsigned long g_phaseChangedMs = 0;
enum class NetworkQuarantineReason : uint8_t {
  None = 0,
  StartupGrace,
  SeqGap,
  LoopGap,
  HttpFault,
  WifiFault,
  Manual
};
enum class RuntimeNetworkState : uint8_t {
  StartupDelay = 0,
  IdleDisconnected,
  TryingSTA,
  STAConnected,
  HttpServing,
  Quarantined,
  Backoff,
  GiveUp
};
struct NetworkRuntime {
  bool quarantined = false;
  unsigned long cooldownUntilMs = 0;
  NetworkQuarantineReason reason = NetworkQuarantineReason::None;
  unsigned long startupMs = 0;
  unsigned long lastLoopGapQuarantineMs = 0;
  unsigned long lastSeqGapHandledMs = 0;
  uint32_t lastHandledSeqGapTotal = 0;
  uint32_t wifiRetryCount = 0;
  uint32_t wifiBurstCount = 0;
  unsigned long wifiBackoffUntilMs = 0;
  unsigned long lastWifiDropMs = 0;
  unsigned long lastHttpBeginMs = 0;
  unsigned long lastHttpClientMs = 0;
  uint32_t networkSkipPressureCount = 0;
  uint32_t networkSkipQuarantineCount = 0;
  uint32_t tsOuterSkipPressure = 0;
  uint32_t tsOuterSkipBacklog = 0;
  uint32_t tsOuterSkipNetworkNotAllowed = 0;
  uint32_t tsOuterSkipQuarantine = 0;
  uint32_t tsOuterSkipWifiNotReady = 0;
  RuntimeNetworkState state = RuntimeNetworkState::StartupDelay;
};
static NetworkRuntime g_net;
static int g_prevWiFiStatus = WL_IDLE_STATUS;
static constexpr unsigned long STARTUP_INGEST_GRACE_MS = 30000UL;
static constexpr unsigned long LOOP_GAP_QUARANTINE_US = 1500000UL;
static constexpr unsigned long LOOP_GAP_REARM_MS = 120000UL;
static constexpr unsigned long LOOP_GAP_COOLDOWN_MS = 300000UL;
static constexpr unsigned long SEQ_GAP_NETWORK_QUIET_MS = 60000UL;
static constexpr unsigned long NETWORK_RAM_CRITICAL_BYTES = 2200UL;
static constexpr unsigned long NETWORK_RAM_WARN_BYTES = 3000UL;
static constexpr unsigned long WIFI_DROP_STABILIZE_MS = 5UL * 60UL * 1000UL;
static constexpr uint8_t WIFI_RETRY_BURST_MAX = 3;
static constexpr unsigned long WIFI_RETRY_SPACING_MS = 60000UL;
static constexpr unsigned long WIFI_LONG_QUIET_MS = 30UL * 60UL * 1000UL;
static constexpr uint8_t WIFI_BURSTS_BEFORE_GIVEUP = 2;
static inline const char* quarantineReasonName(NetworkQuarantineReason reason) {
  switch (reason) {
    case NetworkQuarantineReason::StartupGrace: return "startup_grace";
    case NetworkQuarantineReason::SeqGap: return "seq_gap";
    case NetworkQuarantineReason::LoopGap: return "loop_gap";
    case NetworkQuarantineReason::HttpFault: return "http_fault";
    case NetworkQuarantineReason::WifiFault: return "wifi_fault";
    case NetworkQuarantineReason::Manual: return "manual";
    default: return "none";
  }
}
static inline bool timeReached(unsigned long nowMs, unsigned long deadlineMs) {
  return (int32_t)(nowMs - deadlineMs) >= 0;
}
static inline bool networkIsQuarantined(unsigned long nowMs) {
  if (!g_net.quarantined) return false;
  if (timeReached(nowMs, g_net.cooldownUntilMs)) {
    g_net.quarantined = false;
    g_net.reason = NetworkQuarantineReason::None;
    SDLogger::logUnoEvent("network", "event,quarantine_exit");
    return false;
  }
  return true;
}
static inline bool enterNetworkQuarantine(NetworkQuarantineReason reason, unsigned long nowMs, unsigned long durationMs, bool allowExtend) {
  const unsigned long deadline = nowMs + durationMs;
  if (!g_net.quarantined || timeReached(nowMs, g_net.cooldownUntilMs)) {
    g_net.quarantined = true;
    g_net.reason = reason;
    g_net.cooldownUntilMs = deadline;
    char evt[128] = {0};
    snprintf(evt, sizeof(evt), "event,quarantine_enter,reason,%s,cooldown_ms,%lu", quarantineReasonName(reason), durationMs);
    SDLogger::logUnoEvent("network", evt);
    return true;
  }
  if (allowExtend && reason != g_net.reason && (int32_t)(deadline - g_net.cooldownUntilMs) > 0) {
    g_net.cooldownUntilMs = deadline;
    g_net.reason = reason;
    return true;
  }
  return false;
}
static inline void setPhase(LoopPhase p) {
  g_prevPhase = g_phase;
  g_phase = p;
  g_phaseChangedMs = millis();
}
static inline bool networkAllowed(unsigned long nowMs, bool backlogPending) {
  return !backlogPending && !networkIsQuarantined(nowMs);
}
static inline bool systemPressureHigh(unsigned long nowMs, bool backlogPending) {
  if (backlogPending) return true;
  if (IngestOrchestrator::hadRecentGap(15000UL)) return true;
  if (ServiceTelemetry::snapshot().last_loop_interval_us > LOOP_GAP_QUARANTINE_US) return true;
  if ((unsigned long)MemoryMonitor::freeRam() < NETWORK_RAM_CRITICAL_BYTES) return true;
  (void)nowMs;
  return false;
}

static uint8_t optionalStartupStep = 0;
static bool startupSnapshotLogged = false;
static bool nanoCfgSnapshotLogged = false;
static void serviceIngest() {
  const unsigned long startedUs = micros();
  NanoComm::service();
  ServiceTelemetry::noteNanoService(micros() - startedUs, millis());
  IngestOrchestrator::service();
}

static void logStartupSnapshot(const char* phase) {
  SDLogger::logUnoLine("startup", "phase", phase ? phase : "unknown");
  SDLogger::logUnoLine("startup.build", "build_date", __DATE__);
  SDLogger::logUnoLine("startup.build", "build_time", __TIME__);
  SDLogger::logUnoValue("startup.build", "sts_schema_version", STS_SCHEMA_VERSION);
  SDLogger::logUnoValue("startup.build", "swing_csv_field_count", (long)CANONICAL_SWING_CSV_FIELD_COUNT);
  SDLogger::logUnoValue("startup.build", "pps_csv_field_count", (long)CANONICAL_PPS_CSV_FIELD_COUNT);
  serviceIngest();

  SDLogger::logUnoValue("startup.log", "enabled", UnoTunables::logEnabled ? 1 : 0);
  SDLogger::logUnoValue("startup.log", "daily", UnoTunables::logDaily ? 1 : 0);
  SDLogger::logUnoValue("startup.log", "append", UnoTunables::logAppend ? 1 : 0);
  SDLogger::logUnoValue("startup.log", "startup_policy", (long)UnoTunables::logStartupPolicy);
  SDLogger::logUnoLine("startup.log", "base_name", UnoTunables::logBaseName);
  serviceIngest();

  serviceIngest();

  SDLogger::logUnoValue("startup.wifi", "provisioning", WiFiConfig::isProvisioning() ? 1 : 0);
  SDLogger::logUnoValue("startup.wifi", "is_ap_mode", WiFiConfig::isApMode() ? 1 : 0);
  SDLogger::logUnoValue("startup.wifi", "has_runtime_ssid", (WiFiConfig::ssid() && WiFiConfig::ssid()[0]) ? 1 : 0);
  SDLogger::logUnoValue("startup.wifi", "has_fw_default_ssid", WIFI_SSID[0] ? 1 : 0);
}

static void logNanoCfgSnapshot() {
  char schemaHashBuf[16] = {0};
  SDLogger::logUnoLine("startup.nano_cfg", "phase", "post_startup_read");
  SDLogger::logUnoValue("startup.nano_cfg", "config_received", NanoComm::currentSample.session.config_received ? 1 : 0);
  SDLogger::logUnoValue("startup.nano_cfg", "protocol_version", (long)NanoComm::currentSample.session.protocol_version);
  SDLogger::logUnoValue("startup.nano_cfg", "nominal_hz", (long)NanoComm::currentSample.session.nominal_hz);
  SDLogger::logUnoLine("startup.nano_cfg", "firmware", NanoComm::currentSample.session.firmware);
  serviceIngest();
  SDLogger::logUnoLine("startup.nano_cfg", "canonical_swing_tag", NanoComm::currentSample.session.canonical_swing_tag);
  SDLogger::logUnoLine("startup.nano_cfg", "canonical_swing_schema_id", NanoComm::currentSample.session.canonical_swing_schema_id);
  serviceIngest();
  snprintf(schemaHashBuf, sizeof(schemaHashBuf), "0x%08lx",
           (unsigned long)NanoComm::currentSample.session.canonical_swing_schema_payload_hash);
  SDLogger::logUnoLine("startup.nano_cfg", "canonical_swing_schema_hash", schemaHashBuf);
  SDLogger::logUnoLine("startup.nano_cfg", "canonical_pps_tag", NanoComm::currentSample.session.canonical_pps_tag);
  SDLogger::logUnoLine("startup.nano_cfg", "canonical_pps_schema_id", NanoComm::currentSample.session.canonical_pps_schema_id);
  snprintf(schemaHashBuf, sizeof(schemaHashBuf), "0x%08lx",
           (unsigned long)NanoComm::currentSample.session.canonical_pps_schema_payload_hash);
  SDLogger::logUnoLine("startup.nano_cfg", "canonical_pps_schema_hash", schemaHashBuf);
}


void setup() {
  setPhase(PHASE_BOOT);
  g_net.startupMs = millis();
  Serial.begin(115200);
  I2cBus::begin();

  NANO_SERIAL.begin(SERIAL_BAUD_NANO);
  NANO_SERIAL.setTimeout(SERIAL_TIMEOUT_MS);


  // On Uno R4, EEPROM.begin() does not expose a runtime failure status.
  // Keep startup logic honest: initialize and then attempt to load saved config.
  EEPROM.begin();
  bool configLoaded = false;
  UnoConfig ucfg = getCurrentUnoConfig();
  if (loadUnoConfig(ucfg)) {
    applyUnoConfig(ucfg);
    configLoaded = true;
  }
  Display::begin();
  Display::showSplash();
  SDLogger::begin();
  SDLogger::setLogMode(UnoTunables::logDaily ? SDLogger::LogMode::Daily : SDLogger::LogMode::Continuous);
  SDLogger::setFilename(UnoTunables::logBaseName);
  SDLogger::setStartupPolicy(static_cast<SDLogger::LogStartupPolicy>(UnoTunables::logStartupPolicy));

  // The Nano may already be transmitting. Request metadata asynchronously and
  // parse complete records through the same path used during steady operation.
  NanoComm::readStartup();
  if (UnoTunables::logEnabled) {
    SDLogger::startLogging(SDLogger::getLogMode(), false);
    if (SDLogger::snapshot().uno.active) {
      startupSnapshotLogged = true;
      logStartupSnapshot("joining_stream");
    }
  }
  serviceIngest();

  if (!configLoaded) {
    Display::scrollLog(F("No saved config; using defaults"));
    SDLogger::logUnoEvent("startup.config", "No saved config; using defaults");
  }
  // Optional devices are initialized one stage at a time from loop(), with
  // ingestion serviced between stages. No timed startup LED waits run here.
#if UNO_HAS_WATCHDOG
  WDT.begin(8000);
  SDLogger::logUnoEvent("watchdog", "enabled=1,timeout_ms=8000");
#else
  SDLogger::logUnoEvent("watchdog", "enabled=0,reason=WDT.h_unavailable");
#endif
}

void loop() {
  static constexpr unsigned long WIFI_SERVICE_INTERVAL_MS = 50;
  static constexpr unsigned long HTTP_SERVICE_INTERVAL_MS = 25;
  static constexpr uint8_t BACKLOG_RECOVERY_PASSES = 3;
  static constexpr unsigned long SERVICE_TELEMETRY_INTERVAL_MS = 10000;
  static unsigned long lastWiFiServiceMs = 0;
  static unsigned long lastHttpServiceMs = 0;
  static unsigned long lastServiceTelemetryMs = 0;

  const unsigned long loopStartUs = micros();
  const unsigned long loopStartMs = millis();
  ServiceTelemetry::noteLoopStart(loopStartUs, loopStartMs);
  setPhase(PHASE_LOOP_TOP);

  // Ingest is the primary data plane; keep serial service first.
  setPhase(PHASE_READ_NANO);
  unsigned long t0 = micros();
  NanoComm::service();
  ServiceTelemetry::noteNanoService(micros() - t0, millis());
  IngestOrchestrator::service();

  setPhase(PHASE_PARSE_NANO);
  bool backlogPendingWork = NanoComm::hasPendingIngestWork();
  if (backlogPendingWork) {
    // While Nano has pending bytes/ingest queue, prioritize data-plane catch-up.
    for (uint8_t pass = 0; pass < BACKLOG_RECOVERY_PASSES && NanoComm::hasPendingIngestWork(); ++pass) {
      t0 = micros();
      NanoComm::service();
      ServiceTelemetry::noteNanoService(micros() - t0, millis());
      IngestOrchestrator::service();
    }
    backlogPendingWork = NanoComm::hasPendingIngestWork();
  }

  if (!backlogPendingWork) {
    // Log files wait for validated CFG and both SCH declarations.
    if (!startupSnapshotLogged && SDLogger::snapshot().uno.active) {
      startupSnapshotLogged = true;
      logStartupSnapshot("logging_ready");
    }
    switch (optionalStartupStep) {
      case 0:
        Sensors::begin();
        statusDisplayBegin();
        ++optionalStartupStep;
        break;
      case 1:
        WiFiConfig::begin();
        ++optionalStartupStep;
        break;
      case 2:
        HttpServer::begin();
        ++optionalStartupStep;
        break;
      default:
        if (!nanoCfgSnapshotLogged && NanoComm::metadataReady() && SDLogger::snapshot().uno.active) {
          nanoCfgSnapshotLogged = true;
          logNanoCfgSnapshot();
        }
        break;
    }
    serviceIngest();
    backlogPendingWork = NanoComm::hasPendingIngestWork();
  }
  const unsigned long nowMs = millis();
  const bool dueWiFi = (unsigned long)(nowMs - lastWiFiServiceMs) >= WIFI_SERVICE_INTERVAL_MS;
  const bool dueHttp = (unsigned long)(nowMs - lastHttpServiceMs) >= HTTP_SERVICE_INTERVAL_MS;
  setPhase(PHASE_WIFI_STATUS);
  const bool runWiFiService = optionalStartupStep >= 2 && !backlogPendingWork && dueWiFi;
  if (runWiFiService) {
    t0 = micros();
    WiFiConfig::service();
    ServiceTelemetry::noteWiFiService(micros() - t0);
    lastWiFiServiceMs = nowMs;
    t0 = micros();
    NanoComm::service();
    ServiceTelemetry::noteNanoService(micros() - t0, millis());
    IngestOrchestrator::service();
    backlogPendingWork = NanoComm::hasPendingIngestWork();
  } else {
    ServiceTelemetry::noteWiFiServiceSkipped();
  }

  // Keep data-plane priority first, but still run HTTP service periodically when
  // backlog is clear so it can observe network loss/recovery and manage listener lifecycle.
  const bool runHttpLifecycle = optionalStartupStep >= 3 && dueHttp && networkAllowed(nowMs, backlogPendingWork);
  if (runHttpLifecycle) {
    t0 = micros();
    HttpServer::serviceLifecycle();
    ServiceTelemetry::noteHttpService(micros() - t0);
    lastHttpServiceMs = nowMs;
  } else {
    ServiceTelemetry::noteHttpServiceSkipped();
  }

  const bool runHttpClients = optionalStartupStep >= 3 && !backlogPendingWork && dueHttp && networkAllowed(nowMs, backlogPendingWork);
  if (runHttpClients) {
    t0 = micros();
    HttpServer::serviceClients();
    t0 = micros();
    NanoComm::service();
    ServiceTelemetry::noteNanoService(micros() - t0, millis());
    IngestOrchestrator::service();
    backlogPendingWork = NanoComm::hasPendingIngestWork();
  }

  setPhase(PHASE_WRITE_SD);
  t0 = micros();
  SDLogger::service();
  ServiceTelemetry::noteSdService(micros() - t0);

  // Future publish path remains explicitly low-priority and best-effort.
  const bool pressureHigh = systemPressureHigh(nowMs, backlogPendingWork);
  if (!networkAllowed(nowMs, backlogPendingWork)) {
    g_net.networkSkipQuarantineCount++;
  } else if (pressureHigh) {
    g_net.networkSkipPressureCount++;
  }
  if (!backlogPendingWork && !pressureHigh && WiFiConfig::networkWorkAllowed() && networkAllowed(nowMs, backlogPendingWork)) {
    PublishMirrorQueue::service();
  } else {
    if (backlogPendingWork) g_net.tsOuterSkipBacklog++;
    if (pressureHigh) g_net.tsOuterSkipPressure++;
    if (!networkAllowed(nowMs, backlogPendingWork)) g_net.tsOuterSkipNetworkNotAllowed++;
    if (networkIsQuarantined(nowMs)) g_net.tsOuterSkipQuarantine++;
    if (!WiFiConfig::networkWorkAllowed()) g_net.tsOuterSkipWifiNotReady++;
  }

  MemoryMonitor::poll();
  MemoryMonitor::serviceBlink();
  if (optionalStartupStep > 0) Sensors::poll();
  serviceIngest();
  backlogPendingWork = NanoComm::hasPendingIngestWork();

  if ((unsigned long)(nowMs - lastServiceTelemetryMs) >= SERVICE_TELEMETRY_INTERVAL_MS) {
#if ENABLE_DIAG_SERVICE_HEARTBEAT
    const ServiceTelemetry::Snapshot m = ServiceTelemetry::snapshot();
    char svc[220] = {0};
    snprintf(svc, sizeof(svc), "uptime_ms,%lu,loop_max_us,%lu,last_loop_gap_us,%lu,nano_backlog,%d", nowMs, m.max_loop_body_us, m.last_loop_interval_us, NanoComm::hasPendingIngestWork() ? 1 : 0);
    SDLogger::logUnoEvent("service.core", svc);
    snprintf(svc, sizeof(svc), "state,%s,status_raw,%d,usable,%d", WiFiConfig::stateName(WiFiConfig::state()), WiFi.status(), WiFiConfig::staUsable() ? 1 : 0);
    SDLogger::logUnoEvent("service.wifi", svc);
    snprintf(svc, sizeof(svc), "active,%d,http_service_max_us,%lu,last_begin_ms,%lu", HttpServer::isActive() ? 1 : 0, m.http_service_max_us, g_net.lastHttpBeginMs);
    SDLogger::logUnoEvent("service.http", svc);
    snprintf(svc, sizeof(svc), "wifi_status,%d,net_allowed,%d,quarantine,%d,pressure,%d,free_ram,%lu,warn_ram,%lu,backlog,%d,recent_gap,%d,last_loop_us,%lu", WiFi.status(), WiFiConfig::networkWorkAllowed() ? 1 : 0, networkIsQuarantined(nowMs) ? 1 : 0, pressureHigh ? 1 : 0, (unsigned long)MemoryMonitor::freeRam(), NETWORK_RAM_WARN_BYTES, backlogPendingWork ? 1 : 0, IngestOrchestrator::hadRecentGap(15000UL) ? 1 : 0, m.last_loop_interval_us);
    SDLogger::logUnoEvent("service.health", svc);
#endif
    lastServiceTelemetryMs = nowMs;
  }

  setPhase(PHASE_OLED_UPDATE);
  Display::service(backlogPendingWork);

  if (!backlogPendingWork) {
    setPhase(PHASE_MATRIX_UPDATE);
    LedStatusSnapshot ledSnapshot = buildLedStatusSnapshot(nowMs);
    statusDisplayService(ledSnapshot, nowMs);
  }

  const ServiceTelemetry::Snapshot m = ServiceTelemetry::snapshot();
  if (m.last_loop_interval_us > LOOP_GAP_QUARANTINE_US &&
      (unsigned long)(nowMs - g_net.lastLoopGapQuarantineMs) >= LOOP_GAP_REARM_MS) {
    enterNetworkQuarantine(NetworkQuarantineReason::LoopGap, nowMs, LOOP_GAP_COOLDOWN_MS, false);
    g_net.lastLoopGapQuarantineMs = nowMs;
    ServiceTelemetry::noteLoopGapEvent(nowMs);
  }
  const uint32_t seqGapTotal = IngestOrchestrator::pcpsSeqGapCount() + IngestOrchestrator::pcswSeqGapCount();
  if (seqGapTotal > g_net.lastHandledSeqGapTotal) {
    g_net.lastHandledSeqGapTotal = seqGapTotal;
    if ((unsigned long)(nowMs - g_net.startupMs) < STARTUP_INGEST_GRACE_MS) {
      SDLogger::logUnoEvent("network", "event,seq_gap,startup_grace,1");
    } else if ((unsigned long)(nowMs - g_net.lastSeqGapHandledMs) >= 5000UL) {
      enterNetworkQuarantine(NetworkQuarantineReason::SeqGap, nowMs, SEQ_GAP_NETWORK_QUIET_MS, false);
      g_net.lastSeqGapHandledMs = nowMs;
    }
  }
  const int wifiStatus = WiFi.status();
  if (g_prevWiFiStatus == WL_CONNECTED && wifiStatus != WL_CONNECTED) {
    g_net.lastWifiDropMs = nowMs;
    g_net.wifiBackoffUntilMs = nowMs + WIFI_DROP_STABILIZE_MS;
    g_net.state = RuntimeNetworkState::Backoff;
  } else if (wifiStatus == WL_CONNECTED) {
    g_net.state = HttpServer::isActive() ? RuntimeNetworkState::HttpServing : RuntimeNetworkState::STAConnected;
  } else if ((int32_t)(nowMs - g_net.wifiBackoffUntilMs) < 0) {
    g_net.state = RuntimeNetworkState::Backoff;
  } else {
    g_net.state = RuntimeNetworkState::IdleDisconnected;
  }
  g_prevWiFiStatus = wifiStatus;
  if (g_net.state == RuntimeNetworkState::IdleDisconnected && WiFiConfig::state() == WiFiConfig::NetworkState::TryingSTA) {
    g_net.wifiRetryCount++;
    if ((g_net.wifiRetryCount % WIFI_RETRY_BURST_MAX) == 0) {
      g_net.wifiBurstCount++;
      if (g_net.wifiBurstCount >= WIFI_BURSTS_BEFORE_GIVEUP) {
        g_net.wifiBackoffUntilMs = nowMs + WIFI_LONG_QUIET_MS;
        g_net.state = RuntimeNetworkState::GiveUp;
      } else {
        g_net.wifiBackoffUntilMs = nowMs + WIFI_RETRY_SPACING_MS;
      }
    }
  }

  setPhase(PHASE_WATCHDOG_FEED);
#if UNO_HAS_WATCHDOG
  WDT.refresh();
#endif

  ServiceTelemetry::noteLoopEnd(micros() - loopStartUs);
  setPhase(PHASE_IDLE);
}
