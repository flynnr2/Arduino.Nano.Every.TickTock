#!/usr/bin/env python3
"""Exercise WiFiConfig's real non-blocking reconnect state machine with stubs."""

from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "Uno.R4.Deprecated" / "src"

CONFIG = r'''
#pragma once
#include <stdint.h>
#define MAX_SSID_LEN 64
#define MAX_PASS_LEN 64
#define WIFI_SSID ""
#define WIFI_PASS ""
#define AP_PASS "test-pass"
#define AP_SSID "test-ap"
#define ENABLE_DIAG_INFO 0
constexpr unsigned WIFI_AP_RESTART_BACKOFF_MS = 1u;
constexpr unsigned WIFI_RUNTIME_STA_INITIAL_HOLD_MS = 300000u;
constexpr unsigned WIFI_RUNTIME_STA_RETRY_SPACING_MS = 300000u;
constexpr uint8_t WIFI_RUNTIME_STA_MAX_RETRIES = 3u;
constexpr unsigned WIFI_AP_END_DELAY_MS = 100u;
constexpr unsigned WIFI_AP_START_DELAY_MS = 0u;
constexpr unsigned WIFI_AP_START_TIMEOUT_MS = 5000u;
constexpr unsigned WIFI_CONNECT_TIMEOUT_MS = 10000u;
constexpr unsigned WIFI_RECONNECT_INTERVAL_MS = 30000u;
'''

WIFI = r'''
#pragma once
#include <stdint.h>
struct IPAddress {
  uint8_t b[4];
  IPAddress(uint8_t a=0, uint8_t c=0, uint8_t d=0, uint8_t e=0) : b{a,c,d,e} {}
  uint8_t operator[](unsigned i) const { return b[i]; }
  bool operator!=(const IPAddress& other) const {
    for (unsigned i = 0; i < 4; ++i) if (b[i] != other.b[i]) return true;
    return false;
  }
};
constexpr int WL_DISCONNECTED = 0;
constexpr int WL_CONNECTED = 3;
constexpr int WL_AP_LISTENING = 7;
constexpr int WL_AP_CONNECTED = 8;
class WiFiMock {
 public:
  int currentStatus = WL_CONNECTED;
  IPAddress currentIp = IPAddress(192, 168, 1, 9);
  bool connectWithIp = true;
  unsigned beginCalls = 0;
  unsigned disconnectCalls = 0;
  void setTimeout(unsigned long) {}
  int status() const { return currentStatus; }
  IPAddress localIP() const { return currentIp; }
  void disconnect() { ++disconnectCalls; currentStatus = WL_DISCONNECTED; currentIp = IPAddress(); }
  void end() { currentStatus = WL_DISCONNECTED; currentIp = IPAddress(); }
  void begin(const char*, const char*) { ++beginCalls; currentStatus = WL_CONNECTED; currentIp = connectWithIp ? IPAddress(10,0,0,2) : IPAddress(); }
  void beginAP(const char*, const char*) { currentStatus = WL_AP_LISTENING; currentIp = IPAddress(192,168,4,1); }
};
extern WiFiMock WiFi;
'''

ARDUINO = r'''
#pragma once
#include <stdint.h>
struct __FlashStringHelper {};
#define F(x) reinterpret_cast<const __FlashStringHelper*>(x)
uint32_t millis();
uint32_t micros();
void randomSeed(uint32_t);
long random(long);
'''

TEST = r'''
#include "WiFiConfig.h"
#include <assert.h>
uint32_t g_now = 0;
uint32_t millis() { return g_now; }
uint32_t micros() { return g_now * 1000u; }
void randomSeed(uint32_t) {}
long random(long) { return 0; }
WiFiMock WiFi;

static void advance(uint32_t ms) { g_now += ms; }
static void completeStaTransition() {
  WiFiConfig::service();             // disconnect
  advance(100u);
  WiFiConfig::service();             // begin
  WiFiConfig::service();             // observe connected
}

int main() {
  WiFiConfig::begin();
  completeStaTransition();
  assert(WiFiConfig::state() == WiFiConfig::NetworkState::STAConnected);
  const unsigned initialBegins = WiFi.beginCalls;

  // An explicit retry must not be swallowed by the usable old association.
  WiFiConfig::requestReconnect();
  WiFiConfig::service();
  assert(WiFiConfig::state() == WiFiConfig::NetworkState::TryingSTA);
  completeStaTransition();
  assert(WiFi.beginCalls == initialBegins + 1);
  assert(WiFiConfig::state() == WiFiConfig::NetworkState::STAConnected);

  // A connected interface with no IP remains transitional, then enters the
  // quiet hold after the bounded timeout instead of flapping to connected.
  WiFi.connectWithIp = false;
  WiFiConfig::requestReconnect();
  WiFiConfig::service();
  WiFiConfig::service();
  advance(100u);
  WiFiConfig::service();
  WiFiConfig::service();
  assert(WiFiConfig::state() == WiFiConfig::NetworkState::STAConnectedNoIP);
  advance(10000u);
  WiFiConfig::service();
  assert(WiFiConfig::state() == WiFiConfig::NetworkState::STAQuietHold);

  // Provisioning cancels the STA path, starts AP asynchronously, and makes
  // the local configuration listener eligible once AP receives its address.
  WiFiConfig::setProvisioning(true);
  WiFiConfig::service();              // WiFi.end
  advance(100u);
  WiFiConfig::service();              // beginAP
  WiFiConfig::service();              // observe AP
  assert(WiFiConfig::state() == WiFiConfig::NetworkState::APRunning);
  assert(WiFiConfig::networkReadyForHttp());
  return 0;
}
'''


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="wifi-reconnect-") as temp_dir:
        temp = Path(temp_dir)
        for name in ("WiFiConfig.cpp", "WiFiConfig.h"):
            (temp / name).write_text((SRC / name).read_text())
        (temp / "Config.h").write_text(CONFIG)
        (temp / "WiFiCompat.h").write_text(WIFI)
        (temp / "Arduino.h").write_text(ARDUINO)
        (temp / "WiFiStorage.h").write_text(
            '#pragma once\nnamespace WiFiStorage { inline void readCredentials(char* s, char* p) { s[0]=\'n\'; s[1]=0; p[0]=0; } }\n')
        (temp / "Display.h").write_text('#pragma once\n#include "Arduino.h"\nnamespace Display { inline void scrollLog(const char*) {} inline void scrollLog(const __FlashStringHelper*) {} }\n')
        (temp / "DiagLog.h").write_text('#pragma once\nnamespace DiagLog { enum class Severity { Warn }; enum class MessageId { WiFiRetryHold, WiFiRecoveryAttempt }; inline void emitCooldown(Severity, MessageId, const __FlashStringHelper*, unsigned long) {} }\n')
        (temp / "SDLogger.h").write_text('#pragma once\nnamespace SDLogger { inline void logUnoEvent(const char*, const char*) {} inline void logUnoValue(const char*, const char*, long) {} }\n')
        (temp / "ServiceTelemetry.h").write_text('#pragma once\nnamespace ServiceTelemetry { inline void noteWiFiApStartAttempt() {} inline void noteWiFiApStartResult(bool, unsigned long) {} inline void noteWiFiStaAttempt() {} inline void noteWiFiStaResult(bool, unsigned long) {} }\n')
        (temp / "wifi_reconnect.cpp").write_text(TEST)
        output = temp / "wifi_reconnect"
        subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(temp),
                        str(temp / "WiFiConfig.cpp"), str(temp / "wifi_reconnect.cpp"), "-o", str(output)], check=True)
        subprocess.run([str(output)], check=True)


if __name__ == "__main__":
    main()
