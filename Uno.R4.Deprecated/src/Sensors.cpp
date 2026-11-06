#include "Sensors.h"
#include "I2cRecovery.h"
#include "NanoComm.h"
#include "SDLogger.h"
#include <cmath>
#include <cstdio>

namespace Sensors {

namespace {

constexpr uint8_t kFailuresBeforeReinit = 3;
constexpr uint32_t kPressureConstantSuspectMs = 600000UL;
constexpr float kPressureChangeEpsilonHpa = 0.01f;

// Adafruit SHT4x 1.0.5 deletes these protected objects in begin() but leaves
// their pointers dangling if the subsequent I2C/reset step fails. Clear them
// before every attempt so a later retry cannot delete the same object twice.
class RecoverableSht4x : public Adafruit_SHT4x {
 public:
  bool begin(TwoWire *theWire = &Wire1) {
    if (temp_sensor) {
      delete temp_sensor;
      temp_sensor = nullptr;
    }
    if (humidity_sensor) {
      delete humidity_sensor;
      humidity_sensor = nullptr;
    }
    return Adafruit_SHT4x::begin(theWire);
  }
};

struct ChannelState {
  bool initialized = false;
  bool hasSuccess = false;
  bool lastReadFailed = false;
  uint16_t consecutiveFailures = 0;
  uint16_t initFailures = 0;
  uint8_t retryAttempt = 0;
  uint32_t lastSuccessMs = 0;
  uint32_t lastReadAttemptMs = 0;
  uint32_t nextAttemptMs = 0;
  HealthState lastLoggedState = HealthState::Initializing;
  bool hasLoggedState = false;
};

struct PressureTracker {
  bool suspect = false;
  bool valueKnown = false;
  bool lastLoggedSuspect = false;
  uint32_t constantSinceMs = 0;
  float lastHpa = NAN;
};

Adafruit_BMP280 bmp(&Wire1);
RecoverableSht4x sht4;
ChannelState shtState;
ChannelState bmpState;
PressureTracker pressureTracker;
HealthSnapshot healthSnapshot = {};
float cachedTemperature = NAN;
float cachedHumidity = NAN;
float cachedPressure = NAN;
bool preferSht = true;
bool begun = false;

bool finiteShtReading(float temperature, float humidity) {
  return std::isfinite(temperature) && std::isfinite(humidity) &&
         temperature >= -40.0f && temperature <= 125.0f &&
         humidity >= 0.0f && humidity <= 100.0f;
}

bool readTemperatureHumidity(float &temperature, float &humidity) {
  sensors_event_t hum, temp;
  if (!sht4.getEvent(&hum, &temp)) return false;
  temperature = temp.temperature;
  humidity = hum.relative_humidity;
  return finiteShtReading(temperature, humidity);
}

bool readPressure(float &pressure) {
  const float pressurePa = bmp.readPressure();
  if (!std::isfinite(pressurePa)) return false;
  pressure = pressurePa / 100.0f;
  // BMP280's specified range rejects corrupt bus values as well as NAN/INF.
  return std::isfinite(pressure) && pressure >= 300.0f && pressure <= 1100.0f;
}

bool isFresh(const ChannelState &channel, uint32_t nowMs) {
  return detail::isFresh(nowMs, channel.lastSuccessMs, channel.hasSuccess,
                         SENSOR_PERIOD_MS);
}

HealthState currentState(const ChannelState &channel, uint32_t nowMs) {
  if (!channel.initialized) {
    return channel.retryAttempt == 0 ? HealthState::Initializing : HealthState::Offline;
  }
  if (!channel.hasSuccess) {
    return channel.lastReadFailed ? HealthState::Degraded : HealthState::Initializing;
  }
  if (!isFresh(channel, nowMs)) return HealthState::Stale;
  return channel.lastReadFailed ? HealthState::Degraded : HealthState::Ready;
}

const char* channelName(bool isSht) {
  return isSht ? "sht4x" : "bmp280";
}

void logTransition(ChannelState &channel, bool isSht, uint32_t nowMs) {
  const HealthState state = currentState(channel, nowMs);
  const bool suspect = !isSht && pressureTracker.suspect;
  if (channel.hasLoggedState && channel.lastLoggedState == state &&
      (isSht || pressureTracker.lastLoggedSuspect == suspect)) return;

  // Do not write until SDLogger's files are actually open. Preserve the
  // unlogged state so the first safe opportunity records it once.
  if (!SDLogger::isLogging()) return;

  char event[118];
  snprintf(event, sizeof(event),
           "sensor,%s,state,%s,fresh,%d,read_failures,%u,init_failures,%u,suspect,%d",
           channelName(isSht), healthStateName(state), isFresh(channel, nowMs) ? 1 : 0,
           (unsigned)channel.consecutiveFailures, (unsigned)channel.initFailures,
           suspect ? 1 : 0);
  SDLogger::logUnoEvent("sensor.health", event);
  channel.lastLoggedState = state;
  if (!isSht) pressureTracker.lastLoggedSuspect = suspect;
  channel.hasLoggedState = true;
}

void scheduleRetry(ChannelState &channel, uint32_t nowMs) {
  if (channel.retryAttempt < 255) ++channel.retryAttempt;
  channel.nextAttemptMs = nowMs + detail::retryBackoffMs(channel.retryAttempt);
}

void markReadFailure(ChannelState &channel, uint32_t nowMs) {
  channel.lastReadFailed = true;
  if (channel.consecutiveFailures < 0xFFFFu) ++channel.consecutiveFailures;
  channel.lastReadAttemptMs = nowMs;
  if (channel.consecutiveFailures >= kFailuresBeforeReinit) {
    channel.initialized = false;
    scheduleRetry(channel, nowMs);
  }
}

void notePressure(float pressure, uint32_t nowMs) {
  if (!pressureTracker.valueKnown ||
      std::fabs(pressure - pressureTracker.lastHpa) > kPressureChangeEpsilonHpa) {
    pressureTracker.valueKnown = true;
    pressureTracker.lastHpa = pressure;
    pressureTracker.constantSinceMs = nowMs;
    pressureTracker.suspect = false;
    return;
  }

  if ((uint32_t)(nowMs - pressureTracker.constantSinceMs) >=
      kPressureConstantSuspectMs) {
    // Constant pressure remains a valid reading; this is diagnostic only.
    pressureTracker.suspect = true;
  }
}

void initializeSht(uint32_t nowMs) {
  if (sht4.begin(&Wire1)) {
    sht4.setPrecision(SHT4X_HIGH_PRECISION);
    sht4.setHeater(SHT4X_NO_HEATER);
    shtState.initialized = true;
    shtState.lastReadFailed = false;
    shtState.consecutiveFailures = 0;
    shtState.retryAttempt = 0;
    shtState.lastReadAttemptMs = nowMs - SENSOR_PERIOD_MS;
  } else {
    if (shtState.initFailures < 0xFFFFu) ++shtState.initFailures;
    scheduleRetry(shtState, nowMs);
  }
}

void initializeBmp(uint32_t nowMs) {
  // Adafruit BMP280 3.0.0 pauses for 100 ms after a successful begin().
  // poll() permits only this one recovery action after ingest has headroom.
  if (bmp.begin()) {
    bmpState.initialized = true;
    bmpState.lastReadFailed = false;
    bmpState.consecutiveFailures = 0;
    bmpState.retryAttempt = 0;
    bmpState.lastReadAttemptMs = nowMs - SENSOR_PERIOD_MS;
  } else {
    if (bmpState.initFailures < 0xFFFFu) ++bmpState.initFailures;
    scheduleRetry(bmpState, nowMs);
  }
}

void serviceSht(uint32_t nowMs) {
  if (!shtState.initialized) {
    initializeSht(nowMs);
    return;
  }
  float temperature = NAN;
  float humidity = NAN;
  if (readTemperatureHumidity(temperature, humidity)) {
    cachedTemperature = temperature;
    cachedHumidity = humidity;
    shtState.hasSuccess = true;
    shtState.lastSuccessMs = nowMs;
    shtState.lastReadAttemptMs = nowMs;
    shtState.lastReadFailed = false;
    shtState.consecutiveFailures = 0;
  } else {
    markReadFailure(shtState, nowMs);
  }
}

void serviceBmp(uint32_t nowMs) {
  if (!bmpState.initialized) {
    initializeBmp(nowMs);
    return;
  }
  float pressure = NAN;
  if (readPressure(pressure)) {
    cachedPressure = pressure;
    bmpState.hasSuccess = true;
    bmpState.lastSuccessMs = nowMs;
    bmpState.lastReadAttemptMs = nowMs;
    bmpState.lastReadFailed = false;
    bmpState.consecutiveFailures = 0;
    notePressure(pressure, nowMs);
  } else {
    markReadFailure(bmpState, nowMs);
  }
}

bool due(const ChannelState &channel, uint32_t nowMs) {
  if (!channel.initialized) return detail::timeReached(nowMs, channel.nextAttemptMs);
  return (uint32_t)(nowMs - channel.lastReadAttemptMs) >= SENSOR_PERIOD_MS;
}

ChannelHealth snapshot(const ChannelState &channel, uint32_t nowMs) {
  return {
      currentState(channel, nowMs),
      channel.initialized,
      isFresh(channel, nowMs),
      (&channel == &bmpState) && pressureTracker.suspect,
      channel.consecutiveFailures,
      channel.initFailures,
      channel.lastSuccessMs,
      channel.nextAttemptMs,
  };
}

void refreshSnapshot(uint32_t nowMs) {
  healthSnapshot.sht4x = snapshot(shtState, nowMs);
  healthSnapshot.bmp280 = snapshot(bmpState, nowMs);
}

} // namespace

void begin() {
  // Do not touch I2C in setup. poll() waits for ingest headroom and performs
  // one permitted operation. Device-library I2C latency remains hardware-dependent.
  shtState = ChannelState{};
  bmpState = ChannelState{};
  pressureTracker = PressureTracker{};
  cachedTemperature = NAN;
  cachedHumidity = NAN;
  cachedPressure = NAN;
  preferSht = true;
  begun = true;
  const uint32_t nowMs = millis();
  shtState.nextAttemptMs = nowMs;
  bmpState.nextAttemptMs = nowMs;
  refreshSnapshot(nowMs);
}

void poll() {
  if (!begun) return;
  const uint32_t nowMs = millis();
  logTransition(shtState, true, nowMs);
  logTransition(bmpState, false, nowMs);
  refreshSnapshot(nowMs);

  // Do not add I2C latency while serial input still needs to be ingested.
  if (NanoComm::hasPendingIngestWork()) return;

  if (!I2cBus::service(I2cBus::Bus::Sensors)) return;

  const bool shtDue = due(shtState, nowMs);
  const bool bmpDue = due(bmpState, nowMs);
  if (!shtDue && !bmpDue) return;

  if (shtDue && (!bmpDue || preferSht)) {
    serviceSht(nowMs);
    preferSht = false;
  } else {
    serviceBmp(nowMs);
    preferSht = true;
  }

  I2cBus::observe(I2cBus::Bus::Sensors);
  logTransition(shtState, true, nowMs);
  logTransition(bmpState, false, nowMs);
  refreshSnapshot(nowMs);
}

void getLatest(float &temperature, float &humidity, float &pressure) {
  const uint32_t nowMs = millis();
  if (isFresh(shtState, nowMs)) {
    temperature = cachedTemperature;
    humidity = cachedHumidity;
  } else {
    temperature = NAN;
    humidity = NAN;
  }
  pressure = isFresh(bmpState, nowMs) ? cachedPressure : NAN;
}

const HealthSnapshot& health() {
  refreshSnapshot(millis());
  return healthSnapshot;
}

const char* healthStateName(HealthState state) {
  switch (state) {
    case HealthState::Initializing: return "initializing";
    case HealthState::Ready: return "ready";
    case HealthState::Degraded: return "degraded";
    case HealthState::Stale: return "stale";
    case HealthState::Offline: return "offline";
  }
  return "unknown";
}

void scanI2C() {
  // Scan the Qwiic sensor bus, separate from normal health recovery.
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    if (NanoComm::hasPendingIngestWork()) return;
    if (I2cBus::probe(I2cBus::Bus::Sensors, addr) == 0) ++found;
  }
  if (SDLogger::isLogging()) {
    SDLogger::logUnoValue("sensor.i2c", "Wire1_devices_found", found);
  }
}

} // namespace Sensors
