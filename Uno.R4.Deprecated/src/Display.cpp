#include "PendulumProtocolReceiver.h"
#include "OledTimebaseFormat.h"
#include "Display.h"
#include "I2cRecovery.h"
#include "PeriodDisplayState.h"
#include "OledTransfer.h"
#include "SDLogger.h"
#include "Sensors.h"
#include "OledScreenRotation.h"
#include <avr/pgmspace.h>
#include <cstdio>
#include <cstring>
#include <cmath>

namespace Display {

static Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET,
                               I2cBus::OLED_CLOCK_HZ, I2cBus::OLED_CLOCK_HZ);
static PeriodDisplayState periodState;
static OledTransfer transfer;
static bool displayReady = false;
static_assert(SCREEN_WIDTH * SCREEN_HEIGHT / 8 == OledTransfer::FRAME_BYTES,
              "OLED transfer geometry must match framebuffer");
static uint32_t lastFrameMs = 0, lastHealthMs = 0, maxTransactionUs = 0;
static uint32_t lastFullFrameMs = 0;
static uint32_t probeCompletedMs = 0;
static uint32_t lastDeferredMs = 0;
static uint8_t probeIndex = 3, probeResults[3] = {255, 255, 255};
static int8_t errorSda = -1, errorScl = -1;
constexpr uint32_t OLED_HEALTH_MS = 30000;
constexpr uint32_t OLED_TIMEOUT_US = 10000;

static uint8_t writeI2c(uint8_t address, uint8_t control,
                        const uint8_t* data, size_t count) {
  const uint32_t started = micros();
  Wire.setWireTimeout(OLED_TIMEOUT_US);
  Wire.beginTransmission(address);
  bool buffered = true;
  if (data) buffered = Wire.write(control) == 1 && Wire.write(data, count) == count;
  uint8_t result = Wire.endTransmission();
  if (!buffered && result == 0) result = 1;
  Wire.setWireTimeout(I2cBus::TIMEOUT_US);
  const uint32_t elapsed = micros() - started;
  if (elapsed > maxTransactionUs) maxTransactionUs = elapsed;
  if (result) { errorSda = digitalRead(SDA); errorScl = digitalRead(SCL); }
  I2cBus::observe(I2cBus::Bus::Oled);
  return result;
}

// Keep diagnostic formatting off the normal drawing/transfer stack.
static void __attribute__((noinline)) logHealth(uint32_t now) {
  if (uint32_t(now - lastHealthMs) >= OLED_HEALTH_MS) {
    lastHealthMs = now;
    const auto& m = transfer.metrics;
    char message[280];
    snprintf(message, sizeof(message),
        "ready,%u,try,%lu,ok,%lu,fail,%lu,defer,%lu,active,%u,ok_age_ms,%lu,frame_max_ms,%lu,tx_max_us,%lu,last_err,%u,same,%lu,pages,%lu",
        displayReady ? 1u : 0u, (unsigned long)m.attempts, (unsigned long)m.completed,
        (unsigned long)m.failed, (unsigned long)m.deferred, transfer.active() ? 1u : 0u,
        (unsigned long)(m.completed ? now - m.lastOkMs : UINT32_MAX),
        (unsigned long)m.maxFrameMs, (unsigned long)maxTransactionUs, unsigned(m.lastError),
        (unsigned long)m.unchanged, (unsigned long)m.pagesSent);
    SDLogger::logUnoEvent("oled.health", message);
    // Each address is probed on its actual controller; NACK is not a stuck bus.
    snprintf(message, sizeof(message),
        "Wire OLED_3d=%s SDA=%d SCL=%d error_SDA=%d error_SCL=%d; Wire1 SHT41_44=%s BMP280_77=%s SDA=%d SCL=%d; pending=%u age_ms,%lu",
        I2cBus::resultName(probeResults[0]),
        I2cBus::sda(I2cBus::Bus::Oled), I2cBus::scl(I2cBus::Bus::Oled), errorSda, errorScl,
        I2cBus::resultName(probeResults[1]), I2cBus::resultName(probeResults[2]),
        I2cBus::sda(I2cBus::Bus::Sensors), I2cBus::scl(I2cBus::Bus::Sensors),
        probeIndex < 3 ? 1u : 0u,
        (unsigned long)(probeCompletedMs ? now - probeCompletedMs : UINT32_MAX));
    SDLogger::logUnoEvent("i2c.health", message);
    // Do not restart an incomplete round if sustained backlog delays probing.
    if (probeIndex == 3) probeIndex = 0;
  }
}

void service(bool ingestPending) {
  const uint32_t now = millis();
  logHealth(now);
  const bool frameDue = displayReady && !transfer.active() && uint32_t(now - lastFrameMs) >= 1000;
  if (ingestPending || NanoComm::hasPendingIngestWork()) {
    // Count seconds with deferred work, not millions of individual loop skips.
    if ((transfer.active() || frameDue) && uint32_t(now - lastDeferredMs) >= 1000) {
      ++transfer.metrics.deferred; lastDeferredMs = now;
    }
    return;
  }
  if (probeIndex < 3) {
    static const uint8_t addresses[] = {OLED_ADDR, 0x44, 0x77};
    const auto bus = probeIndex == 0 ? I2cBus::Bus::Oled : I2cBus::Bus::Sensors;
    probeResults[probeIndex] = I2cBus::probe(bus, addresses[probeIndex]);
    if (probeIndex == 0 && probeResults[0] != 0) transfer.invalidate();
    ++probeIndex;
    if (probeIndex == 3) probeCompletedMs = millis();
    return;
  }
  if (!I2cBus::service(I2cBus::Bus::Oled)) { transfer.invalidate(); return; }
  if (!displayReady) { begin(); return; }
  if (frameDue) {
    // Periodic full refresh bounds fingerprint-collision or lost-panel-state
    // effects without an extra framebuffer; it still yields between chunks.
    if (uint32_t(now - lastFullFrameMs) >= 60000) {
      transfer.invalidate(); lastFullFrameMs = now;
    }
    update(); lastFrameMs = millis();
  }
  if (NanoComm::hasPendingIngestWork()) return;
  if (transfer.active()) {
    transfer.step(display.getBuffer(), []() { return millis(); },
        [](uint8_t control, const uint8_t* bytes, size_t count) {
          return writeI2c(OLED_ADDR, control, bytes, count);
        });
  }
}

void observeSwing(const CanonicalSwingSample& sample) {
  periodState.observeSwing(sample, NanoComm::currentSample.session.nominal_hz, millis());
}
void observePps(const CanonicalPpsSample& sample) {
  periodState.observePps(sample, NanoComm::currentSample.session.nominal_hz, millis());
}

// All screens share the top fault/date row and bottom warning ticker.
constexpr size_t SCROLL_LINE_LEN = 48;
constexpr size_t TICKER_TEXT_LIMIT = SCROLL_LINE_LEN;
constexpr uint8_t FONT_HEIGHT = 8;
static_assert(OledTimebaseFormat::ROW_PIXELS <= SCREEN_WIDTH, "Timebase rows must fit");
static_assert(8 * OledTimebaseFormat::ROW_HEIGHT <= SCREEN_HEIGHT, "Timebase height must fit");
constexpr uint8_t OLED_ROWS = SCREEN_HEIGHT / FONT_HEIGHT;
constexpr uint8_t STATUS_ROWS = 7;
constexpr uint8_t TICKER_ROW = STATUS_ROWS;
constexpr uint8_t TICKER_CHAR_WIDTH = 6;
constexpr uint8_t TICKER_VISIBLE_CHARS = SCREEN_WIDTH / TICKER_CHAR_WIDTH;
constexpr uint32_t TICKER_INTERVAL_MS = 4000;
constexpr uint32_t TICKER_ALERT_TTL_MS = 12000;
constexpr uint32_t CANONICAL_STALE_ALERT_MS = 3000;
constexpr uint32_t PPS_FRESH_MS = 5000;
constexpr size_t MAX_TICKER_ALERTS = 6;
constexpr size_t MAX_ACTIVE_FAULTS = 9;
constexpr size_t MAX_TICKER_MESSAGES = MAX_TICKER_ALERTS + MAX_ACTIVE_FAULTS;
static uint32_t lastTickerMs = 0;
static bool tickerRefreshRequested = true;
static size_t tickerMessageIndex = 0, tickerCharOffset = 0;
struct TickerAlert {
  char text[SCROLL_LINE_LEN] = {0};
  uint32_t createdMs = 0, ttlMs = 0;
  bool active = false;
};
static TickerAlert tickerAlerts[MAX_TICKER_ALERTS];
static size_t nextAlertSlot = 0;
// Transient alerts own their text; active fault strings are immutable literals.
static const char* activeFaults[MAX_ACTIVE_FAULTS] = {};
static const char* tickerMessages[MAX_TICKER_MESSAGES];
static size_t tickerMessageCount = 0;
static bool cswStaleAlerted = false, cpsStaleAlerted = false;
static_assert(OLED_ROWS == 8 && STATUS_ROWS + 1 == OLED_ROWS,
              "Two-screen layout requires eight 8-pixel text rows");
static_assert(TICKER_VISIBLE_CHARS == 21, "Row formatters require 21 columns");
static uint8_t min_u8(uint8_t a, uint8_t b) { return a < b ? a : b; }
static void enqueueTickerAlert(const char *msg, uint32_t ttlMs) {
  auto& alert = tickerAlerts[nextAlertSlot];
  strncpy(alert.text, msg, sizeof(alert.text) - 1);
  alert.text[sizeof(alert.text) - 1] = 0;
  alert.createdMs = millis();
  alert.ttlMs = ttlMs;
  alert.active = true;
  nextAlertSlot = (nextAlertSlot + 1) % MAX_TICKER_ALERTS;
  tickerRefreshRequested = true;
  tickerMessageIndex = tickerCharOffset = 0;
}
static void purgeExpiredAlerts(uint32_t now) {
  for (auto& alert : tickerAlerts)
    if (alert.active && alert.ttlMs && uint32_t(now - alert.createdMs) >= alert.ttlMs)
      alert.active = false;
}
static void rebuildTickerMessages() {
  const char* previous = tickerMessageCount ? tickerMessages[tickerMessageIndex] : nullptr;
  purgeExpiredAlerts(millis());
  size_t count = 0;
  // Active conditions survive transient TTL and cannot be evicted by log bursts.
  for (const char* fault : activeFaults) if (fault) tickerMessages[count++] = fault;
  for (const auto& alert : tickerAlerts) if (alert.active) tickerMessages[count++] = alert.text;
  if (!count) tickerMessages[count++] = "STATUS: OK";
  if (tickerMessageIndex >= count) tickerMessageIndex = 0;
  tickerMessageCount = count;
  if (tickerMessages[tickerMessageIndex] != previous ||
      tickerCharOffset >= strlen(tickerMessages[tickerMessageIndex])) tickerCharOffset = 0;
}

static void advanceTicker(const char *msg) {
  // Hold each readable window for four seconds instead of crawling a character
  // at a time. Rebuild only at these boundaries, keeping changing ages stable.
  if (tickerCharOffset + TICKER_VISIBLE_CHARS < strnlen(msg, TICKER_TEXT_LIMIT))
    tickerCharOffset += TICKER_VISIBLE_CHARS;
  else {
    tickerCharOffset = 0;
    tickerMessageIndex = (tickerMessageIndex + 1) % tickerMessageCount;
  }
}

static void renderTicker(const char *msg) {
  display.fillRect(0, TICKER_ROW * FONT_HEIGHT, SCREEN_WIDTH, FONT_HEIGHT,
                   SSD1306_WHITE);
  display.setCursor(0, TICKER_ROW * FONT_HEIGHT);
  display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);

  const uint8_t msgLen = (uint8_t)strnlen(msg, TICKER_TEXT_LIMIT);
  const uint8_t visible = min_u8(TICKER_VISIBLE_CHARS, msgLen);
  const uint8_t start = min_u8(tickerCharOffset, msgLen);
  const uint8_t end = min_u8(start + visible, msgLen);
  for (uint8_t i = start; i < end; ++i) {
    display.write(msg[i]);
  }

  display.setTextColor(SSD1306_WHITE);
}

static void checkCanonicalFeedAlerts() {
  const uint32_t cswAge = NanoComm::canonicalSwingAgeMs();
  const uint32_t cpsAge = NanoComm::canonicalPpsAgeMs();

  cswStaleAlerted = cswAge != NanoComm::AGE_UNKNOWN_MS && cswAge >= CANONICAL_STALE_ALERT_MS;
  cpsStaleAlerted = cpsAge != NanoComm::AGE_UNKNOWN_MS && cpsAge >= CANONICAL_STALE_ALERT_MS;
}

void begin() {
  if (!I2cBus::service(I2cBus::Bus::Oled)) return;
  Wire.setWireTimeout(OLED_TIMEOUT_US);
  displayReady = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR, false, false);
  I2cBus::observe(I2cBus::Bus::Oled);
  Wire.setWireTimeout(I2cBus::TIMEOUT_US);
  display.setTextSize(1);
  display.setTextWrap(false);
  display.setTextColor(SSD1306_WHITE);
}

void showSplash() {
  if (!displayReady) return;
  display.clearDisplay();
  display.setCursor(0, 0);
  display.println(F("Pendulum Monitor"));
  transfer.begin(display.getBuffer(), millis());
}

void scrollLog(const char *m) {
  enqueueTickerAlert(m, TICKER_ALERT_TTL_MS);
}

void scrollLog(const String &msg) { scrollLog(msg.c_str()); }

void scrollLog(const __FlashStringHelper *fmsg) {
  char msg[SCROLL_LINE_LEN] = {0};
  strncpy_P(msg, reinterpret_cast<PGM_P>(fmsg), sizeof(msg) - 1);
  msg[sizeof(msg) - 1] = 0;
  scrollLog(msg);
}

// Fixed-size fault tracking replaces the unused scrollback copy (292 bytes).
static uint32_t previousDrops[3] = {}, dropsChangedMs = 0;
static bool dropsSeen = false;
static bool dropWarning = false;
static uint16_t previousFaultMask = 0;
static uint32_t faultChangedMs = 0;

static const char* mainFault(uint32_t now) {
  uint32_t drops[3] = {};
  CanonicalSwingSample swing = {};
  const bool haveSwing = NanoComm::getCanonicalSwingSample(swing);
  if (haveSwing) {
    drops[0] = swing.drop_ir; drops[1] = swing.drop_pps; drops[2] = swing.drop_swing;
  }
  if (dropsSeen && haveSwing) for (unsigned i = 0; i < 3; ++i) {
    if (drops[i] > previousDrops[i]) { dropsChangedMs = now; dropWarning = true; }
  }
  dropsSeen = haveSwing;
  memcpy(previousDrops, drops, sizeof(drops));
  if (dropWarning && uint32_t(now - dropsChangedMs) >= TICKER_ALERT_TTL_MS) dropWarning = false;
  const auto& sensors = Sensors::health();
  const auto badSensor = [](const Sensors::ChannelHealth& h) {
    return h.state == Sensors::HealthState::Degraded ||
           h.state == Sensors::HealthState::Stale || h.state == Sensors::HealthState::Offline;
  };
  NanoComm::LatestPpsStatus pps = {};
  const bool havePps = NanoComm::latestPpsStatus(pps);
  const bool ppsStale = havePps &&
      pps.age_ms != NanoComm::AGE_UNKNOWN_MS && pps.age_ms >= PPS_FRESH_MS;
  const char* faults[] = {
    NanoComm::hasProtocolError() ? "! SERIAL ERROR" : nullptr,
    !SDLogger::isLogging() ? "! SD LOG OFF" : (!SDLogger::ready() ? "! SD LOG NOT READY" : nullptr),
    cswStaleAlerted ? "! SWING FEED STALE" : nullptr,
    cpsStaleAlerted ? "! PPS FEED STALE" : nullptr,
    dropWarning ? "! NANO DROPS RISING" : nullptr,
    badSensor(sensors.sht4x) ? "! SHT SENSOR FAULT" : nullptr,
    badSensor(sensors.bmp280) ? "! BMP SENSOR FAULT" : nullptr,
    strcmp(periodState.timebase(now), "STALE") == 0 ? "! PERIOD STALE" : nullptr,
    ppsStale && !cpsStaleAlerted ? "! PPS STALE" :
        (havePps && pps.status == NO_PPS ? "! GPS NO PPS" : nullptr)
  };
  static_assert(sizeof(faults) / sizeof(faults[0]) == MAX_ACTIVE_FAULTS, "Fault pool size");
  bool faultTextChanged = false;
  for (size_t i = 0; i < MAX_ACTIVE_FAULTS; ++i)
    if (activeFaults[i] != faults[i]) faultTextChanged = true;
  memcpy(activeFaults, faults, sizeof(faults));
  uint16_t mask = 0;
  unsigned count = 0;
  for (unsigned i = 0; i < sizeof(faults) / sizeof(faults[0]); ++i)
    if (faults[i]) { mask |= 1u << i; ++count; }
  if (mask != previousFaultMask || faultTextChanged) {
    previousFaultMask = mask; faultChangedMs = now;
    tickerRefreshRequested = true;
    tickerMessageIndex = tickerCharOffset = 0;
  }
  if (!count) return nullptr;
  unsigned selected = (uint32_t(now - faultChangedMs) / 2000) % count;
  for (const char* fault : faults) if (fault && selected-- == 0) return fault;
  return nullptr;
}

// Retain rows in the existing framebuffer; screen changes repaint only the body.
static bool rowsInitialized = false, bodyRefreshRequested = true;
static uint32_t lastBodyDrawMs = 0;
static uint32_t displayedClockSlot = UINT32_MAX;
static const char* displayedFault = nullptr;
static OledScreenRotation screenRotation;

void configureRating(uint16_t shortMinutes, uint16_t longMinutes) {
  periodState.configure(shortMinutes, longMinutes);
  bodyRefreshRequested = true;
}

static char healthCode(Sensors::HealthState state) {
  switch (state) {
    case Sensors::HealthState::Ready: return 'R';
    case Sensors::HealthState::Degraded: return 'D';
    case Sensors::HealthState::Stale: return 'S';
    case Sensors::HealthState::Offline: return 'X';
    default: return 'I';
  }
}
// Keep the unit outside the bounded numeric field; reduce precision before
// resorting to scientific notation, never cut a number or its unit offscreen.
static void environmentalValue(char* out, size_t size, double value, uint8_t width) {
  if (!std::isfinite(value)) { snprintf(out, size, "--"); return; }
  char number[48];
  snprintf(number, sizeof(number), "%.1f", value);
  if (strlen(number) > width) snprintf(number, sizeof(number), "%.2g", value);
  if (strlen(number) > width) snprintf(number, sizeof(number), "%.1g", value);
  snprintf(out, size, "%s", number);
}
static void formatSupplementalRow(char* out, size_t size, uint8_t row, uint32_t now) {
  const auto& sample = NanoComm::currentSample;
  char a[16], b[16];
  switch (row) {
    case 0:
      environmentalValue(a, sizeof(a), sample.temperature_C, 6);
      environmentalValue(b, sizeof(b), sample.humidity_pct, 6);
      snprintf(out, size, "T:%sC RH:%s%%", a, b);
      break;
    case 1:
      environmentalValue(a, sizeof(a), sample.pressure_hPa, 14);
      snprintf(out, size, "P:%shPa", a);
      break;
    case 2: {
      NanoComm::LatestPpsStatus pps = {};
      const bool available = NanoComm::latestPpsStatus(pps);
      if (!available || pps.age_ms == NanoComm::AGE_UNKNOWN_MS)
        snprintf(out, size, "GPS:--- AGE:--s");
      else {
        const bool stale = pps.age_ms >= PPS_FRESH_MS;
        const bool holdover = !stale && pps.status == HOLDOVER;
        snprintf(out, size, "GPS:%s %s:%lus",
                 stale ? "STL" : gpsStatusToShortStr(pps.status),
                 holdover ? "HAG" : "AGE",
                 (unsigned long)((holdover ? pps.holdover_age_ms : pps.age_ms) / 1000));
      }
      break;
    }
    case 3:
      snprintf(out, size, "TIMEBASE:%s", periodState.shortTimebase(now));
      break;
    case 4: {
      const auto& health = Sensors::health();
      snprintf(out, size, "LOG:%s SD:%s S:%c B:%c",
               SDLogger::isLogging() ? "ON" : "OFF", SDLogger::ready() ? "OK" : "--",
               healthCode(health.sht4x.state), healthCode(health.bmp280.state));
      break;
    }
    default:
      snprintf(out, size, "EWMA S:%um L:%um", unsigned(UnoTunables::oledShortMinutes),
               unsigned(UnoTunables::oledLongMinutes));
      break;
  }
}

void update() {
  if (!displayReady || transfer.active()) return;
  const uint32_t now = millis();
  if (!rowsInitialized) display.clearDisplay();
  display.setTextSize(1);
  display.setTextWrap(false);
  char line[22] = {0};
  auto setRow = [](uint8_t row) { display.setCursor(0, row * FONT_HEIGHT); };
  checkCanonicalFeedAlerts();
  const char* fault = mainFault(now);
  const time_t epoch = SDLogger::currentEpoch();
  const uint32_t clockSlot = epoch ? uint32_t(epoch / 15) : 0;
  if (!rowsInitialized || fault != displayedFault || (!fault && clockSlot != displayedClockSlot)) {
    display.fillRect(0, 0, SCREEN_WIDTH, FONT_HEIGHT, fault ? SSD1306_WHITE : SSD1306_BLACK);
    setRow(0);
    if (fault) {
      display.setTextColor(SSD1306_BLACK, SSD1306_WHITE);
      display.print(fault);
      display.setTextColor(SSD1306_WHITE);
    } else {
      // Cached NTP clock only; no network request while drawing.
      const struct tm* utc = epoch ? gmtime(&epoch) : nullptr;
      if (utc) strftime(line, sizeof(line), "%Y-%m-%d %H:%M:%SZ", utc);
      else snprintf(line, sizeof(line), "UTC: waiting for sync");
      display.print(line);
      displayedClockSlot = clockSlot;
    }
    displayedFault = fault;
  }
  const bool screenChanged = screenRotation.update(now);
  const bool supplemental = screenRotation.supplemental();
  if (!rowsInitialized || screenChanged || bodyRefreshRequested ||
      uint32_t(now - lastBodyDrawMs) >= (supplemental ? 10000u : 2000u)) {
    for (uint8_t row = 0; row < 6; ++row) {
      display.fillRect(0, (row + 1) * FONT_HEIGHT, SCREEN_WIDTH, FONT_HEIGHT, SSD1306_BLACK);
      setRow(row + 1);
      if (screenRotation.timebase()) OledTimebaseFormat::row(line, sizeof(line), row, periodState.timebaseClock(), now);
      else if (supplemental) formatSupplementalRow(line, sizeof(line), row, now);
      else periodState.formatRatingRow(line, sizeof(line), row, now);
      display.print(line);
    }
    lastBodyDrawMs = now;
    bodyRefreshRequested = false;
  }
  if (!rowsInitialized || tickerRefreshRequested || uint32_t(now - lastTickerMs) >= TICKER_INTERVAL_MS) {
    if (rowsInitialized && !tickerRefreshRequested && tickerMessageCount)
      advanceTicker(tickerMessages[tickerMessageIndex]);
    rebuildTickerMessages();
    if (tickerMessageCount) renderTicker(tickerMessages[tickerMessageIndex]);
    lastTickerMs = now;
    tickerRefreshRequested = false;
  }
  rowsInitialized = true;
  transfer.begin(display.getBuffer(), now);
}

} // namespace Display
