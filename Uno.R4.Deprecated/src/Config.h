#pragma once

#include "Common.h"
#include "PendulumProtocol.h"
#include "PendulumCsvLayout.h"
#include "OledRatingConfig.h"

#if __has_include("secrets.h")
#include "secrets.h"
#endif
#ifndef WIFI_SSID
#define WIFI_SSID ""
#endif
#ifndef WIFI_PASS
#define WIFI_PASS ""
#endif
#ifndef AP_PASS
#define AP_PASS "TickTock"
#endif

// Display settings
#define SCREEN_WIDTH   128
#define SCREEN_HEIGHT   64
#define OLED_RESET      -1
#define OLED_ADDR       0x3D

#ifndef ENABLE_PENDULUM_ADJ_PROVENANCE
#endif

#ifndef ENABLE_LED_MATRIX_STATUS
#define ENABLE_LED_MATRIX_STATUS 1
#endif

#ifndef LED_STATUS_UPDATE_MS
#define LED_STATUS_UPDATE_MS 250u
#endif

#ifndef LED_MATRIX_FLIP_X
#define LED_MATRIX_FLIP_X 0
#endif

#ifndef LED_MATRIX_FLIP_Y
#define LED_MATRIX_FLIP_Y 0
#endif

#ifndef LED_MATRIX_STARTUP_TEST_ENABLED
#define LED_MATRIX_STARTUP_TEST_ENABLED 1
#endif

#ifndef LED_MATRIX_STARTUP_TEST_STEP_MS
#define LED_MATRIX_STARTUP_TEST_STEP_MS 750u
#endif

#ifndef LED_MATRIX_STARTUP_TEST_GLYPHS
#define LED_MATRIX_STARTUP_TEST_GLYPHS 1
#endif

#ifndef LED_FAULT_CODE_HOLD_MS
#define LED_FAULT_CODE_HOLD_MS 1500u
#endif

#ifndef LED_FAULT_CLEAR_LINGER_MS
#define LED_FAULT_CLEAR_LINGER_MS 3000u
#endif

#ifndef LED_FAULT_OVERRIDE_ENABLED
#define LED_FAULT_OVERRIDE_ENABLED 1
#endif

#ifndef LED_FAULT_CYCLE_ACTIVE
#define LED_FAULT_CYCLE_ACTIVE 1
#endif

#ifndef LED_FAULT_SHOW_ONLY_HIGHEST_PRIORITY
#define LED_FAULT_SHOW_ONLY_HIGHEST_PRIORITY 0
#endif

#ifndef LED_HEARTBEAT_ENABLED
#define LED_HEARTBEAT_ENABLED 1
#endif

#ifndef LED_BACKLOG_BAR_ENABLED
#define LED_BACKLOG_BAR_ENABLED 0
#endif

#ifndef LED_SKIP_WHEN_BACKLOG_CRITICAL
#define LED_SKIP_WHEN_BACKLOG_CRITICAL 1
#endif

#ifndef LED_BACKLOG_CRITICAL_PCT
#define LED_BACKLOG_CRITICAL_PCT 80u
#endif

// Sensor polling cadence
constexpr uint32_t SENSOR_PERIOD_MS = 1000; // environmental sensor read interval

// Inert legacy statistics defaults retained for EEPROM compatibility.
constexpr uint16_t DEFAULT_STATS_WINDOW  = 64;
constexpr uint32_t DEFAULT_ROLLING_MS    = 300000UL;
constexpr int32_t  DEFAULT_BLOCK_JUMP_US = 500;

// SD settings
#define SD_CS_PIN          10
#define LOG_FILENAME       "pendulum.csv"
constexpr size_t LOG_FILENAME_LEN = 20;    // includes null terminator
constexpr bool   LOG_DAILY_DEFAULT   = false;
constexpr bool   LOG_ENABLED_DEFAULT = true;
constexpr bool   LOG_APPEND_DEFAULT  = false;
constexpr uint8_t LOG_STARTUP_POLICY_APPEND = 0;
constexpr uint8_t LOG_STARTUP_POLICY_OVERWRITE = 1;
constexpr uint8_t LOG_STARTUP_POLICY_ARCHIVE = 2;
constexpr uint8_t LOG_STARTUP_POLICY_DEFAULT = LOG_STARTUP_POLICY_OVERWRITE;
constexpr int8_t SD_CARD_DETECT_PIN = -1; // set >=0 when wired; -1 disables hardware card-detect
constexpr bool   SD_CARD_DETECT_INSERTED_LOW = true;
constexpr unsigned long SD_HEALTH_PROBE_MS = 1500;
constexpr unsigned long SD_REMOUNT_RETRY_MS = 3000;

// Serial from Nano Every (baud rate in PendulumProtocol.h)
#define SERIAL_TIMEOUT_MS  50
// Wire RX line buffer for a single incoming serial line (CFG/STS/SCH/CSW/CPS transport).
#define NANO_LINE_MAX      384
#define NANO_SERIAL        Serial1

// Backlog health thresholds used for user-facing status/fault reporting.
// These are intentionally less sensitive than "any pending ingest work", which
// remains a scheduling hint for keeping ingest/SD priority high.
#ifndef NANO_BACKLOG_QUEUE_WARN_COUNT
#define NANO_BACKLOG_QUEUE_WARN_COUNT 2u
#endif
#ifndef NANO_BACKLOG_QUEUE_FAULT_COUNT
#define NANO_BACKLOG_QUEUE_FAULT_COUNT 6u
#endif
#ifndef NANO_BACKLOG_QUEUE_CLEAR_COUNT
#define NANO_BACKLOG_QUEUE_CLEAR_COUNT 1u
#endif

#ifndef NANO_BACKLOG_SERIAL_WARN_BYTES
#define NANO_BACKLOG_SERIAL_WARN_BYTES 32u
#endif
#ifndef NANO_BACKLOG_SERIAL_FAULT_BYTES
#define NANO_BACKLOG_SERIAL_FAULT_BYTES 48u
#endif
#ifndef NANO_BACKLOG_SERIAL_CLEAR_BYTES
#define NANO_BACKLOG_SERIAL_CLEAR_BYTES 8u
#endif

#ifndef NANO_BACKLOG_PARTIAL_STALE_MS
#define NANO_BACKLOG_PARTIAL_STALE_MS 1000u
#endif
#ifndef NANO_BACKLOG_FAULT_ASSERT_MS
#define NANO_BACKLOG_FAULT_ASSERT_MS 3000u
#endif
#ifndef NANO_BACKLOG_FAULT_CLEAR_MS
#define NANO_BACKLOG_FAULT_CLEAR_MS 5000u
#endif

// EEPROM layout (512 bytes total)
// 0 - 127   : UnoConfig (slot A)
// 128 - 255 : UnoConfig (slot B)
// 256 - 383: WiFi credentials (slot 0)
// 384 - 511: WiFi credentials (slot 1)
#define EEPROM_SIZE            512
#define MAX_SSID_LEN            32
#define MAX_PASS_LEN            64

// EEPROM slots for Uno-specific configurations
constexpr int EEPROM_UNO_SLOT_A_ADDR        = 0;                                        // UnoConfig slot A
constexpr int EEPROM_UNO_SLOT_B_ADDR        = EEPROM_UNO_SLOT_A_ADDR + 128;              // UnoConfig slot B
constexpr int EEPROM_WIFI_SLOT_SIZE         = 128;                                     // reserve 128 bytes per WiFi slot
constexpr int EEPROM_WIFI_SLOT0_ADDR        = 256;                                     // WiFi credentials slot 0
constexpr int EEPROM_WIFI_SLOT1_ADDR        = EEPROM_WIFI_SLOT0_ADDR + EEPROM_WIFI_SLOT_SIZE; // WiFi credentials slot 1
static_assert(EEPROM_WIFI_SLOT1_ADDR + EEPROM_WIFI_SLOT_SIZE <= EEPROM_SIZE, "WiFi slots must fit EEPROM");

// WiFi
#define WIFI_CONNECT_TIMEOUT_MS 10000
#define AP_SSID                 "PendulumLoggerSetup"
#define HTTP_PORT               80
constexpr unsigned WIFI_AP_RESTART_BACKOFF_MS = 15000; // minimum delay between AP restarts
constexpr unsigned WIFI_AP_DROP_GRACE_MS = 30000;      // grace period before restarting AP after drop
constexpr unsigned WIFI_RUNTIME_STA_INITIAL_HOLD_MS = 300000; // 5-minute quiet hold after runtime STA loss before first retry
constexpr unsigned WIFI_RUNTIME_STA_RETRY_SPACING_MS = 300000; // 5-minute hold between failed runtime STA retry attempts
constexpr uint8_t WIFI_RUNTIME_STA_MAX_RETRIES = 3; // bounded runtime retry attempts after a drop before disabling WiFi for logging
constexpr unsigned WIFI_AP_END_DELAY_MS   = 100;   // wait after WiFi.end() before starting AP
constexpr unsigned WIFI_AP_START_DELAY_MS = 1500;  // allow AP mode to initialize
constexpr unsigned WIFI_AP_START_TIMEOUT_MS = 5000; // timeout while waiting for AP mode
constexpr unsigned WIFI_CONNECT_RETRY_MS  = 250;   // interval between WiFi status checks
constexpr unsigned WIFI_RECONNECT_INTERVAL_MS = 30000; // interval between reconnect attempts
constexpr unsigned long HTTP_SERVICE_BUDGET_US = 1000000UL; // 1000ms budget to tolerate full-page HTTP responses without triggering recovery actions

constexpr bool ENABLE_BOOT_I2C_SCAN = false; // set true for bring-up diagnostics

// SD flush throttling
const uint16_t FLUSH_EVERY_N = 32;
const unsigned long FLUSH_EVERY_MS = 5000;

// RAM monitor thresholds
#define RAM_WARN_THRESHOLD   4000   // bytes
#define RAM_CRIT_THRESHOLD   2000

// Debug timing internals (low-level service/runtime cadence tracing)
#define DEBUG_TIMING 0


// Higher-level diagnostics switches.
#ifndef ENABLE_DIAG_INFO
#define ENABLE_DIAG_INFO 1
#endif

#ifndef ENABLE_DIAG_PROTOCOL_VERBOSE
#define ENABLE_DIAG_PROTOCOL_VERBOSE 0
#endif

#ifndef ENABLE_DIAG_RECOVERY_TRACE
#define ENABLE_DIAG_RECOVERY_TRACE 0
#endif

#ifndef ENABLE_DIAG_SERVICE_HEARTBEAT
#define ENABLE_DIAG_SERVICE_HEARTBEAT 0
#endif

// Publish mirror (future network publish) skeleton.
// Keep disabled by default; when enabled, this path must remain best-effort and
// must never block or back-pressure ingest/SD logging.
constexpr bool ENABLE_PUBLISH_MIRROR_QUEUE = false;


namespace UnoTunables {
  // Inert legacy values: preserve saved configuration compatibility.
  extern uint16_t statsWindowSize;
  extern uint32_t rollingWindowMs;
  extern int32_t  blockJumpUs;

  extern uint16_t oledShortMinutes;
  extern uint16_t oledLongMinutes;

  extern bool     logDaily;
  extern bool     logEnabled;
  extern bool     logAppend;
  extern uint8_t  logStartupPolicy;
  extern char     logBaseName[LOG_FILENAME_LEN];
}

struct UnoConfig {
  // Reserved legacy statistics fields; keep the EEPROM layout stable.
  uint16_t statsWindowSize;
  uint32_t rollingWindowMs;
  int32_t  blockJumpUs;
  bool     logDaily;
  bool     logEnabled;
  bool     logAppend;
  uint8_t  logStartupPolicy;
  char     logBaseName[LOG_FILENAME_LEN];
  uint16_t oledShortMinutes;
  uint16_t oledLongMinutes;
};
