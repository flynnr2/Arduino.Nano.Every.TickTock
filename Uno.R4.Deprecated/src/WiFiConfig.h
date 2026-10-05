#pragma once
#include "WiFiCompat.h"
#include "Config.h"

namespace WiFiConfig {
  enum class NetworkState : uint8_t {
    NoNetwork = 0,
    TryingSTA,
    STAConnectedNoIP,
    STAConnected,
    TryingAP,
    APRunning,
    Backoff,
    APHold,
    STAQuietHold,
    WiFiDisabledForLogging
  };
  void begin();
  void service();
  const char* ssid();
  bool isApMode();
  bool staUsable();
  bool networkReadyForHttp();
  bool isStaConnected();
  uint32_t networkGeneration();
  bool recoveryInProgress();
  NetworkState state();
  const char* stateName(NetworkState s);
  bool inRecoveryHold();
  bool networkWorkAllowed();
  uint32_t staRetryHoldRemainingMs(uint32_t nowMs);
  uint8_t staRetryFailureCount();
  bool isProvisioning();
  void setProvisioning(bool enable);
  void requestReconnect();
  void setCredentials(const char* newSsid, const char* newPass, bool connectNow = false);
}
