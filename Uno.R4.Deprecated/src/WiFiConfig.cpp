#include "WiFiConfig.h"
#include "WiFiStorage.h"
#include "Display.h"
#include "DiagLog.h"
#include "SDLogger.h"
#include "ServiceTelemetry.h"
#include <cstring>
#include <cstdio>
#include <Arduino.h>

namespace WiFiConfig {

static char g_ssid[MAX_SSID_LEN] = {0};
static char g_pass[MAX_PASS_LEN] = {0};
static char g_apPass[MAX_PASS_LEN] = {0};
static bool pendingReconnect = false;
static bool provisioning = false;
static unsigned long lastReconnectAttemptMs = 0;
static uint32_t g_networkGeneration = 0;
static uint8_t g_runtimeStaRetryAttempts = 0;
static unsigned long g_nextStaRetryMs = 0;
static bool g_runtimeStaRecoveryActive = false;
static bool g_manualRetryRequested = false;
static unsigned long lastApStartMs = 0;
static unsigned long wifiServiceNowMs = 0;
static constexpr unsigned long WIFI_DIAG_HOLD_COOLDOWN_MS = 4000;
static constexpr unsigned long WIFI_DIAG_RETRY_ATTEMPT_COOLDOWN_MS = 4000;


static inline void logDiagInfo(const __FlashStringHelper* msg) {
#if ENABLE_DIAG_INFO
  Display::scrollLog(msg);
#else
  (void)msg;
#endif
}

static inline void logDiagInfo(const char* msg) {
#if ENABLE_DIAG_INFO
  Display::scrollLog(msg);
#else
  (void)msg;
#endif
}

static NetworkState g_state = NetworkState::NoNetwork;

enum class PendingAction : uint8_t {
  None,
  StartAP,
  ConnectSTA,
};

struct PendingTransition {
  PendingAction action = PendingAction::None;
  bool wifiEnded = false;
  bool wifiBeginCalled = false;
  unsigned long beginMs = 0;
  unsigned long timeoutMs = 0;
};

static PendingTransition g_transition;

static void ensureApPassword();
static bool isApStatus(int status);
static bool timeReached(uint32_t now, uint32_t deadline);
static void transitionTo(NetworkState next, const char* reason = nullptr);
static void scheduleReconnectOnNextService();
static void cancelPendingTransition();
static bool startApTransition();
static bool stepApTransition();
static bool startStaTransition();
static bool stepStaTransition();
static bool transitionActive();
const char* stateName(NetworkState s);

static bool isApStatus(int status) {
  return status == WL_AP_LISTENING || status == WL_AP_CONNECTED;
}


static bool validIp(IPAddress ip) { return ip != IPAddress(0,0,0,0); }

static void transitionTo(NetworkState next, const char* reason) {
  if (g_state == next) return;
  NetworkState old = g_state;
  g_state = next;
  char evt[160] = {0};
  snprintf(evt, sizeof(evt), "state,from,%s,to,%s,reason,%s,status,%d",
           stateName(old), stateName(next), reason ? reason : "", WiFi.status());
  SDLogger::logUnoEvent("wifi.state", evt);
  if (reason && reason[0] != '\0') {
    char msg[64] = {0};
    snprintf(msg, sizeof(msg), "WiFi state: %s", reason);
    logDiagInfo(msg);
  }
}

static void scheduleReconnectOnNextService() {
  pendingReconnect = true;
  lastReconnectAttemptMs = millis() - WIFI_RECONNECT_INTERVAL_MS;
}

static void cancelPendingTransition() {
  g_transition = PendingTransition{};
}

static bool transitionActive() {
  return g_transition.action != PendingAction::None;
}
static bool timeReached(uint32_t now, uint32_t deadline) {
  return static_cast<int32_t>(now - deadline) >= 0;
}

const char* ssid() { return g_ssid; }
bool isApMode() {
  return g_state == NetworkState::APRunning && isApStatus(WiFi.status());
}
bool isProvisioning() { return provisioning; }

void setProvisioning(bool enable) {
  if (enable == provisioning) return;
  provisioning = enable;
  cancelPendingTransition();
  g_nextStaRetryMs = millis();
  g_runtimeStaRecoveryActive = false;
  g_manualRetryRequested = false;
  if (provisioning) {
    logDiagInfo(F("Provisioning enabled"));
    transitionTo(NetworkState::NoNetwork, "provisioning enabled");
    wifiServiceNowMs = millis();
    startApTransition();
  } else {
    logDiagInfo(F("Provisioning disabled; reconnecting"));
    transitionTo(NetworkState::NoNetwork, "provisioning disabled");
    requestReconnect();
  }
}

void requestReconnect() {
  cancelPendingTransition();
  g_nextStaRetryMs = millis();
  g_manualRetryRequested = true;
  if (g_state == NetworkState::WiFiDisabledForLogging) {
    g_runtimeStaRetryAttempts = 0;
    g_runtimeStaRecoveryActive = false;
    g_nextStaRetryMs = millis();
    SDLogger::logUnoEvent("wifi.policy", "event,manual_retry_clear_disabled,reason,operator_request");
    transitionTo(NetworkState::NoNetwork, "manual reconnect clears disabled");
  }
  scheduleReconnectOnNextService();
  if (!provisioning) { transitionTo(NetworkState::NoNetwork, "reconnect requested"); }
}

void setCredentials(const char* newSsid, const char* newPass, bool connectNow) {
  if (newSsid) {
    strncpy(g_ssid, newSsid, sizeof(g_ssid));
    g_ssid[sizeof(g_ssid) - 1] = 0;
  }
  if (newPass) {
    strncpy(g_pass, newPass, sizeof(g_pass));
    g_pass[sizeof(g_pass) - 1] = 0;
  }
  if (connectNow && provisioning) {
    provisioning = false;
  }
  if (connectNow || !provisioning) {
    scheduleReconnectOnNextService();
  }
}

static void ensureApPassword() {
  if (g_apPass[0]) return;
  if (std::strlen(AP_PASS) > 0) {
    strncpy(g_apPass, AP_PASS, sizeof(g_apPass));
    g_apPass[sizeof(g_apPass) - 1] = 0;
    return;
  }
  static const char alphabet[] = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  randomSeed((uint32_t)micros() ^ (uint32_t)millis());
  const size_t passLen = 12;
  for (size_t i = 0; i < passLen && i < sizeof(g_apPass) - 1; i++) {
    g_apPass[i] = alphabet[random(sizeof(alphabet) - 1)];
  }
  g_apPass[passLen] = 0;
}

static bool startApTransition() {
  if (g_transition.action != PendingAction::None) {
    return false;
  }
  const unsigned long now = wifiServiceNowMs;
  if (lastApStartMs != 0 && now - lastApStartMs < WIFI_AP_RESTART_BACKOFF_MS) {
    return false;
  }
  lastApStartMs = now;
  transitionTo(NetworkState::TryingAP, "starting AP");
  ensureApPassword();
  ServiceTelemetry::noteWiFiApStartAttempt();
  g_transition.action = PendingAction::StartAP;
  g_transition.beginMs = now;
  g_transition.timeoutMs = WIFI_AP_START_TIMEOUT_MS;
  return true;
}

static bool stepApTransition() {
  if (g_transition.action != PendingAction::StartAP) {
    return false;
  }
  const unsigned long now = wifiServiceNowMs;
  if (!g_transition.wifiEnded) {
    WiFi.end();
    g_transition.wifiEnded = true;
    g_transition.beginMs = now;
    return true;
  }

  if (!g_transition.wifiBeginCalled) {
    if ((unsigned long)(now - g_transition.beginMs) < WIFI_AP_END_DELAY_MS) {
      return true;
    }
    WiFi.beginAP(AP_SSID, g_apPass);
    g_transition.wifiBeginCalled = true;
    g_transition.beginMs = now;
    return true;
  }

  if ((unsigned long)(now - g_transition.beginMs) < WIFI_AP_START_DELAY_MS) {
    return true;
  }

  const int status = WiFi.status();
  if (isApStatus(status)) {
    transitionTo(NetworkState::APRunning, "AP started");
    if (validIp(WiFi.localIP())) { g_networkGeneration++; }
    const unsigned long elapsedMs = now - g_transition.beginMs;
    ServiceTelemetry::noteWiFiApStartResult(true, elapsedMs);
    IPAddress ip = WiFi.localIP();
    char ipBuf[24] = {0};
    snprintf(ipBuf, sizeof(ipBuf), "%u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]);
    logDiagInfo("AP Mode: " AP_SSID);
    char apPassMsg[80] = {0};
    snprintf(apPassMsg, sizeof(apPassMsg), "AP Pass: %s", g_apPass);
    logDiagInfo(apPassMsg);
    char ipMsg[32] = {0};
    snprintf(ipMsg, sizeof(ipMsg), "IP: %s", ipBuf);
    logDiagInfo(ipMsg);
    cancelPendingTransition();
    return true;
  }

  if ((unsigned long)(now - g_transition.beginMs) >= g_transition.timeoutMs) {
    const unsigned long elapsedMs = now - g_transition.beginMs;
    ServiceTelemetry::noteWiFiApStartResult(false, elapsedMs);
    transitionTo(NetworkState::NoNetwork, "AP start failed");
    cancelPendingTransition();
  }
  return true;
}

static bool startStaTransition() {
  if (g_transition.action != PendingAction::None) {
    return false;
  }
  if (!g_ssid[0]) {
    return false;
  }
  transitionTo(NetworkState::TryingSTA, "trying STA connect");
  ServiceTelemetry::noteWiFiStaAttempt();
  g_transition.action = PendingAction::ConnectSTA;
  g_transition.beginMs = wifiServiceNowMs;
  g_transition.timeoutMs = WIFI_CONNECT_TIMEOUT_MS;
  return true;
}

static bool stepStaTransition() {
  if (g_transition.action != PendingAction::ConnectSTA) {
    return false;
  }
  const unsigned long now = wifiServiceNowMs;
  if (!g_transition.wifiEnded) {
    // For UNO R4 WiFi / WiFiS3, normal STA recovery is more reliable when we
    // avoid WiFi.end(). The standalone recovery sketch successfully recovers
    // using WiFi.disconnect() + WiFi.begin() + server.begin().
    WiFi.disconnect();
    SDLogger::logUnoEvent("wifi.recovery", "event,WIFI_DISCONNECT");
    g_transition.wifiEnded = true;
    g_transition.beginMs = now;
    return true;
  }

  if (!g_transition.wifiBeginCalled) {
    if ((unsigned long)(now - g_transition.beginMs) < WIFI_AP_END_DELAY_MS) {
      return true;
    }
    SDLogger::logUnoEvent("wifi.recovery", "event,WIFI_BEGIN");
    WiFi.begin(g_ssid, g_pass);
    g_transition.wifiBeginCalled = true;
    g_transition.beginMs = now;
    return true;
  }

  const int status = WiFi.status();
  if (status == WL_CONNECTED) {
    if (!validIp(WiFi.localIP())) {
      transitionTo(NetworkState::STAConnectedNoIP, "STA connected no IP");
      if ((unsigned long)(now - g_transition.beginMs) >= g_transition.timeoutMs) {
        ServiceTelemetry::noteWiFiStaResult(false, now - g_transition.beginMs);
        cancelPendingTransition();
        return false;
      }
      return true;
    }
    transitionTo(NetworkState::STAConnected, "STA connected");
    g_networkGeneration++;
    SDLogger::logUnoEvent("wifi.recovery", "event,WIFI_CONNECTED");
    SDLogger::logUnoValue("wifi.recovery", "NETWORK_GENERATION", (long)g_networkGeneration);
    g_runtimeStaRetryAttempts = 0;
    g_runtimeStaRecoveryActive = false;
    const unsigned long elapsedMs = now - g_transition.beginMs;
    ServiceTelemetry::noteWiFiStaResult(true, elapsedMs);
    IPAddress ip = WiFi.localIP();
    char okMsg[40] = {0};
    snprintf(okMsg, sizeof(okMsg), "WiFi OK: %u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]);
    logDiagInfo(okMsg);
    pendingReconnect = false;
    cancelPendingTransition();
    return true;
  }

  if ((unsigned long)(now - g_transition.beginMs) >= g_transition.timeoutMs) {
    const unsigned long elapsedMs = now - g_transition.beginMs;
    ServiceTelemetry::noteWiFiStaResult(false, elapsedMs);
    cancelPendingTransition();
    return false;
  }
  return true;
}

void begin() {
  ensureApPassword();
  WiFiStorage::readCredentials(g_ssid, g_pass);
  transitionTo(NetworkState::NoNetwork, "begin");

  if (g_ssid[0] == '\0' && WIFI_SSID[0] != '\0') {
    strncpy(g_ssid, WIFI_SSID, sizeof(g_ssid));
    g_ssid[sizeof(g_ssid)-1] = 0;
    strncpy(g_pass, WIFI_PASS, sizeof(g_pass));
    g_pass[sizeof(g_pass)-1] = 0;
  }

  // WiFiS3 begin() normally busy-polls association for ten seconds. Issue
  // the modem command only; service() observes association on later passes.
  WiFi.setTimeout(0);
  wifiServiceNowMs = millis();
  pendingReconnect = false;
  g_runtimeStaRecoveryActive = false;
  g_manualRetryRequested = false;
  g_nextStaRetryMs = 0;
  cancelPendingTransition();
  if (provisioning || !g_ssid[0]) {
    startApTransition();
  } else {
    startStaTransition();
  }
}

void service() {
  unsigned long now = millis();
  wifiServiceNowMs = now;
  int status = WiFi.status();
  bool haveCreds = g_ssid[0] != '\0';

  // An explicit reconnect (including new credentials) must take precedence
  // over a still-usable old association and any automatic retry hold.
  if (g_manualRetryRequested && haveCreds && !provisioning) {
    g_manualRetryRequested = false;
    g_runtimeStaRecoveryActive = false;
    g_runtimeStaRetryAttempts = 0;
    SDLogger::logUnoEvent("wifi.policy", "event,manual_retry_start");
    startStaTransition();
    return;
  }

  if (transitionActive()) {
    if (g_transition.action == PendingAction::StartAP) { stepApTransition(); return; }
    if (g_transition.action == PendingAction::ConnectSTA) {
      if (stepStaTransition()) return;
      if (g_runtimeStaRecoveryActive || g_manualRetryRequested) {
        char failEvt[128] = {0};
        snprintf(failEvt, sizeof(failEvt),
                 "event,runtime_retry_failed,attempt,%u,next_hold_ms,%lu",
                 (unsigned)g_runtimeStaRetryAttempts,
                 (unsigned long)WIFI_RUNTIME_STA_RETRY_SPACING_MS);
        SDLogger::logUnoEvent("wifi.policy", failEvt);
        if (g_runtimeStaRetryAttempts >= WIFI_RUNTIME_STA_MAX_RETRIES) {
          transitionTo(NetworkState::WiFiDisabledForLogging, "runtime retries exhausted");
          SDLogger::logUnoEvent("wifi.policy", "event,disabled_for_logging,reason,runtime_sta_retries_exhausted,attempts,3");
          SDLogger::logUnoEvent("wifi.recovery", "event,WIFI_RECOVERY_GAVE_UP");
          g_runtimeStaRecoveryActive = false;
        } else {
          transitionTo(NetworkState::STAQuietHold, "runtime retry failed hold");
          g_runtimeStaRecoveryActive = true;
          g_nextStaRetryMs = now + (unsigned long)WIFI_RUNTIME_STA_RETRY_SPACING_MS;
        }
      } else {
        transitionTo(NetworkState::STAQuietHold, "boot sta failed hold");
        g_runtimeStaRetryAttempts = 0;
        g_runtimeStaRecoveryActive = true;
        g_nextStaRetryMs = now + (unsigned long)WIFI_RUNTIME_STA_INITIAL_HOLD_MS;
      }
      g_manualRetryRequested = false;
      return;
    }
  }

  if (provisioning) { if (g_state != NetworkState::APRunning || !isApStatus(status)) startApTransition(); return; }

  if (!haveCreds) { if (g_state != NetworkState::APRunning) startApTransition(); return; }

  if (g_state == NetworkState::WiFiDisabledForLogging) return;
  if (staUsable()) {
    const NetworkState recoveredFrom = g_state;
    if (g_state != NetworkState::STAConnected) {
      transitionTo(NetworkState::STAConnected, "sta usable after hold/recovery");
      g_networkGeneration++;
      IPAddress ip = WiFi.localIP();
      char recoveryEvt[128] = {0};
      if (g_runtimeStaRecoveryActive || g_runtimeStaRetryAttempts > 0) {
        snprintf(recoveryEvt, sizeof(recoveryEvt),
                 "event,runtime_retry_success,attempt,%u,ip,%u.%u.%u.%u",
                 (unsigned)g_runtimeStaRetryAttempts, ip[0], ip[1], ip[2], ip[3]);
      } else {
        snprintf(recoveryEvt, sizeof(recoveryEvt),
                 "event,sta_recovered_passive,from,%s,attempts,%u,ip,%u.%u.%u.%u",
                 stateName(recoveredFrom), (unsigned)g_runtimeStaRetryAttempts,
                 ip[0], ip[1], ip[2], ip[3]);
      }
      SDLogger::logUnoEvent("wifi.policy", recoveryEvt);
    } else if (g_runtimeStaRecoveryActive || g_runtimeStaRetryAttempts > 0) {
      IPAddress ip = WiFi.localIP();
      char okEvt[112] = {0};
      snprintf(okEvt, sizeof(okEvt),
               "event,runtime_retry_success,attempt,%u,ip,%u.%u.%u.%u",
               (unsigned)g_runtimeStaRetryAttempts, ip[0], ip[1], ip[2], ip[3]);
      SDLogger::logUnoEvent("wifi.policy", okEvt);
    }
    g_runtimeStaRetryAttempts = 0;
    g_runtimeStaRecoveryActive = false;
    g_manualRetryRequested = false;
    return;
  }

  if (!g_runtimeStaRecoveryActive && !g_manualRetryRequested &&
      (g_state == NetworkState::STAConnected || g_state == NetworkState::STAConnectedNoIP)) {
    transitionTo(NetworkState::STAQuietHold, "runtime sta lost hold");
    g_runtimeStaRetryAttempts = 0;
    g_runtimeStaRecoveryActive = true;
    g_nextStaRetryMs = now + (unsigned long)WIFI_RUNTIME_STA_INITIAL_HOLD_MS;
    SDLogger::logUnoEvent("wifi.policy", "event,runtime_loss_hold,reason,sta_lost,hold_ms,300000");
    SDLogger::logUnoEvent("wifi.recovery", "event,WIFI_LOST");
    SDLogger::logUnoEvent("wifi.recovery", "event,WIFI_RECOVERY_QUIET_BEGIN");
    return;
  }

  const bool allowRetry = timeReached(now, g_nextStaRetryMs) && (g_runtimeStaRecoveryActive || g_manualRetryRequested);
  if (!allowRetry) {
    transitionTo(NetworkState::STAQuietHold, "retry hold");
    DiagLog::emitCooldown(DiagLog::Severity::Warn, DiagLog::MessageId::WiFiRetryHold, F("WiFi recovery: retry hold"), WIFI_DIAG_HOLD_COOLDOWN_MS);
    return;
  }

  lastReconnectAttemptMs = now;
  if (g_runtimeStaRecoveryActive) {
    if (g_runtimeStaRetryAttempts >= WIFI_RUNTIME_STA_MAX_RETRIES) {
      transitionTo(NetworkState::WiFiDisabledForLogging, "runtime retries exhausted");
      SDLogger::logUnoEvent("wifi.policy", "event,disabled_for_logging,reason,runtime_sta_retries_exhausted,attempts,3");
      SDLogger::logUnoEvent("wifi.recovery", "event,WIFI_RECOVERY_GAVE_UP");
      g_manualRetryRequested = false;
      g_runtimeStaRecoveryActive = false;
      return;
    }
    g_runtimeStaRetryAttempts++;
    char startEvt[96] = {0};
    snprintf(startEvt, sizeof(startEvt), "event,runtime_retry_start,attempt,%u,max,%u",
             (unsigned)g_runtimeStaRetryAttempts, (unsigned)WIFI_RUNTIME_STA_MAX_RETRIES);
    SDLogger::logUnoEvent("wifi.policy", startEvt);
    SDLogger::logUnoEvent("wifi.recovery", "event,WIFI_RECOVERY_ATTEMPT");
    DiagLog::emitCooldown(DiagLog::Severity::Warn, DiagLog::MessageId::WiFiRecoveryAttempt, F("WiFi recovery: retry attempt"), WIFI_DIAG_RETRY_ATTEMPT_COOLDOWN_MS);
  } else if (g_manualRetryRequested) {
    SDLogger::logUnoEvent("wifi.policy", "event,manual_retry_start");
    SDLogger::logUnoEvent("wifi.recovery", "event,WIFI_RECOVERY_ATTEMPT");
    DiagLog::emitCooldown(DiagLog::Severity::Warn, DiagLog::MessageId::WiFiRecoveryAttempt, F("WiFi recovery: retry attempt"), WIFI_DIAG_RETRY_ATTEMPT_COOLDOWN_MS);
  }
  g_manualRetryRequested = false;
  startStaTransition();
}


const char* stateName(NetworkState s) {
  switch (s) {
    case NetworkState::NoNetwork: return "NoNetwork";
    case NetworkState::TryingSTA: return "TryingSTA";
    case NetworkState::STAConnectedNoIP: return "STAConnectedNoIP";
    case NetworkState::STAConnected: return "STAConnected";
    case NetworkState::TryingAP: return "TryingAP";
    case NetworkState::APRunning: return "APRunning";
    case NetworkState::Backoff: return "Backoff";
    case NetworkState::APHold: return "APHold";
    case NetworkState::STAQuietHold: return "STAQuietHold";
    case NetworkState::WiFiDisabledForLogging: return "WiFiDisabledForLogging";
  }
  return "Unknown";
}

NetworkState state() { return g_state; }

bool staUsable() { return WiFi.status() == WL_CONNECTED && validIp(WiFi.localIP()); }
bool isStaConnected() { return WiFi.status() == WL_CONNECTED; }

bool networkReadyForHttp() {
  if (g_state == NetworkState::APRunning) {
    return isApStatus(WiFi.status()) && validIp(WiFi.localIP());
  }
  if (g_state != NetworkState::STAConnected) return false;
  if (staRetryHoldRemainingMs(millis()) > 0) return false;
  return staUsable();
}

uint32_t networkGeneration() { return g_networkGeneration; }
bool recoveryInProgress() {
  return g_runtimeStaRecoveryActive || transitionActive() || g_manualRetryRequested;
}

bool inRecoveryHold() {
  return g_state == NetworkState::Backoff ||
         g_state == NetworkState::APHold ||
         g_state == NetworkState::STAQuietHold ||
         g_state == NetworkState::WiFiDisabledForLogging;
}

bool networkWorkAllowed() {
  if (g_state == NetworkState::APRunning || g_state == NetworkState::TryingAP) return true;
  if (g_state == NetworkState::WiFiDisabledForLogging) return false;
  if (staRetryHoldRemainingMs(millis()) > 0) return false;
  return true;
}

uint32_t staRetryHoldRemainingMs(uint32_t nowMs) {
  if (timeReached(nowMs, g_nextStaRetryMs)) return 0;
  return g_nextStaRetryMs - nowMs;
}

uint8_t staRetryFailureCount() { return g_runtimeStaRetryAttempts; }

} // namespace WiFiConfig
