#!/usr/bin/env python3
"""Compile the real Sensors.cpp against deterministic host sensor stubs."""

from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SENSOR_DIR = ROOT / "Uno.R4.Deprecated" / "src"

STUBS = r'''
#pragma once
#include <stdint.h>
#include <assert.h>

uint32_t millis();

struct TwoWire {};
struct WireMock : TwoWire {
  void beginTransmission(uint8_t) {}
  int endTransmission() { return 4; }
};
extern WireMock Wire, Wire1;

extern bool g_sht_begin_ok;
extern bool g_sht_read_ok;
extern float g_sht_temperature;
extern float g_sht_humidity;
extern unsigned g_sht_begin_calls;
extern unsigned g_sht_read_calls;
extern unsigned g_sht_double_deletes;
extern bool g_bmp_begin_ok;
extern float g_bmp_pressure_pa;
extern unsigned g_bmp_begin_calls;
extern unsigned g_bmp_read_calls;

struct sensors_event_t {
  float temperature;
  float relative_humidity;
};

enum { SHT4X_HIGH_PRECISION = 1, SHT4X_NO_HEATER = 0 };

struct FakeShtChild {
  bool destroyed = false;
  ~FakeShtChild() {
    if (destroyed) ++g_sht_double_deletes;
    destroyed = true;
  }
  static void operator delete(void*) noexcept {}
};

class Adafruit_SHT4x {
 public:
  bool begin(TwoWire* bus = &Wire) {
    assert(bus == &Wire1);
    ++g_sht_begin_calls;
    // Match the installed library's bug: deleted child pointers are left
    // dangling when initialization fails before replacement allocation.
    if (temp_sensor) delete temp_sensor;
    if (humidity_sensor) delete humidity_sensor;
    if (!g_sht_begin_ok) return false;
    temp_sensor = new FakeShtChild();
    humidity_sensor = new FakeShtChild();
    return true;
  }
  void setPrecision(int) {}
  void setHeater(int) {}
  bool getEvent(sensors_event_t* humidity, sensors_event_t* temperature) {
    ++g_sht_read_calls;
    humidity->relative_humidity = g_sht_humidity;
    temperature->temperature = g_sht_temperature;
    return g_sht_read_ok;
  }
 protected:
  FakeShtChild* temp_sensor = nullptr;
  FakeShtChild* humidity_sensor = nullptr;
};

class Adafruit_BMP280 {
 public:
  explicit Adafruit_BMP280(TwoWire* bus = &Wire) { assert(bus == &Wire1); }
  bool begin() { ++g_bmp_begin_calls; return g_bmp_begin_ok; }
  float readPressure() { ++g_bmp_read_calls; return g_bmp_pressure_pa; }
};
'''

NANO = r'''
#pragma once
namespace NanoComm {
extern bool g_backlog;
inline bool hasPendingIngestWork() { return g_backlog; }
}
'''

LOGGER = r'''
#pragma once
#include <stdint.h>
namespace SDLogger {
extern bool g_logging;
inline bool isLogging() { return g_logging; }
inline void logUnoEvent(const char*, const char*) {}
inline void logUnoValue(const char*, const char*, long) {}
}
'''

CONFIG = r'''
#pragma once
#include <stdint.h>
constexpr uint32_t SENSOR_PERIOD_MS = 1000u;
'''

RECOVERY = r'''
#pragma once
#include <stdint.h>
namespace I2cBus {
enum class Bus { Oled, Sensors };
extern bool allowed[2];
extern unsigned services[2], observations[2], probes[2];
inline bool service(Bus bus) { ++services[(int)bus]; return allowed[(int)bus]; }
inline void observe(Bus bus) { ++observations[(int)bus]; }
inline uint8_t probe(Bus bus, uint8_t) { ++probes[(int)bus]; return 2; }
}
'''

TEST = r'''
#include "Sensors.h"
#include "I2cRecovery.h"
#include <assert.h>
#include <cmath>
#include <stdint.h>

uint32_t g_now = 0;
uint32_t millis() { return g_now; }
WireMock Wire, Wire1;

bool g_sht_begin_ok = true;
bool g_sht_read_ok = true;
float g_sht_temperature = 21.5f;
float g_sht_humidity = 46.0f;
unsigned g_sht_begin_calls = 0;
unsigned g_sht_read_calls = 0;
unsigned g_sht_double_deletes = 0;
bool g_bmp_begin_ok = true;
float g_bmp_pressure_pa = 100000.0f;
unsigned g_bmp_begin_calls = 0;
unsigned g_bmp_read_calls = 0;

namespace I2cBus {
bool allowed[2] = {true, true};
unsigned services[2] = {}, observations[2] = {}, probes[2] = {};
}
namespace NanoComm { bool g_backlog = false; }
namespace SDLogger { bool g_logging = false; }

static void advance(uint32_t amount) { g_now += amount; }

int main() {
  // The stub reproduces the installed library's dangling-child behavior.
  // This proves the test would catch an unwrapped recovery retry.
  {
    Adafruit_SHT4x unsafe;
    g_sht_begin_ok = true;
    assert(unsafe.begin(&Wire1));
    g_sht_begin_ok = false;
    assert(!unsafe.begin(&Wire1));
    assert(!unsafe.begin(&Wire1));
    assert(g_sht_double_deletes == 2);
  }
  g_sht_double_deletes = 0;
  g_sht_begin_calls = 0;
  g_sht_begin_ok = true;

  // poll() remains inert until the startup coordinator schedules begin().
  Sensors::poll();
  assert(g_sht_begin_calls == 0 && g_bmp_begin_calls == 0);
  Sensors::begin();
  const Sensors::HealthSnapshot& scheduled = Sensors::health();
  assert(scheduled.sht4x.state == Sensors::HealthState::Initializing);
  assert(scheduled.bmp280.state == Sensors::HealthState::Initializing);

  // Backlog is a hard gate: begin() and a blocked poll touch no I2C device.
  NanoComm::g_backlog = true;
  Sensors::poll();
  assert(g_sht_begin_calls == 0 && g_bmp_begin_calls == 0);
  assert(I2cBus::services[1] == 0);
  NanoComm::g_backlog = false;

  // A suspended sensor bus cannot initialize devices; a blocked OLED bus
  // has no effect on sensors. Repeated polls never bypass the bus gate.
  I2cBus::allowed[1] = false;
  for (unsigned i = 0; i < 100; ++i) Sensors::poll();
  assert(g_sht_begin_calls == 0 && g_bmp_begin_calls == 0);
  assert(I2cBus::observations[1] == 0);
  I2cBus::allowed[1] = true;
  I2cBus::allowed[0] = false;

  // Each call runs one action, and one failed device cannot suppress the other.
  Sensors::poll();  // SHT init
  assert(g_sht_begin_calls == 1 && g_bmp_begin_calls == 0);
  Sensors::poll();  // BMP init
  assert(g_bmp_begin_calls == 1);
  Sensors::poll();  // SHT read
  Sensors::poll();  // BMP read
  const Sensors::HealthSnapshot& healthy = Sensors::health();
  assert(healthy.sht4x.state == Sensors::HealthState::Ready);
  assert(healthy.bmp280.state == Sensors::HealthState::Ready);

  // Non-finite SHT data is rejected without affecting BMP availability.
  advance(1000);
  g_sht_temperature = NAN;
  Sensors::poll();
  assert(Sensors::health().sht4x.state == Sensors::HealthState::Degraded);
  Sensors::poll();
  assert(Sensors::health().bmp280.state == Sensors::HealthState::Ready);
  g_sht_temperature = 21.5f;

  // Three BMP failures trigger only its reinitialization path. The last good
  // value remains usable for three periods, then is withheld as NAN.
  g_bmp_pressure_pa = NAN;
  for (int i = 0; i < 3; ++i) {
    advance(1000);
    Sensors::poll();  // SHT recovery/read
    Sensors::poll();  // BMP failed read
  }
  const Sensors::HealthSnapshot& offline = Sensors::health();
  assert(offline.bmp280.state == Sensors::HealthState::Offline);
  assert(offline.bmp280.consecutiveFailures == 3);
  assert(offline.sht4x.state == Sensors::HealthState::Ready);
  float temperature, humidity, pressure;
  Sensors::getLatest(temperature, humidity, pressure);
  assert(std::isfinite(pressure));
  advance(1);
  Sensors::getLatest(temperature, humidity, pressure);
  assert(std::isfinite(temperature) && std::isfinite(humidity));
  assert(std::isnan(pressure));

  // Retry initialization succeeds independently, then the first valid BMP
  // read restores its cache and health.
  g_bmp_pressure_pa = 100000.0f;
  advance(1000);
  Sensors::poll();  // SHT read
  Sensors::poll();  // BMP retry init
  Sensors::poll();  // BMP read
  assert(Sensors::health().bmp280.state == Sensors::HealthState::Ready);
  assert(Sensors::health().bmp280.consecutiveFailures == 0);

  // A long unchanged valid pressure becomes suspect only; it never becomes a
  // read failure or removes the available value.
  advance(600000);
  Sensors::poll();  // SHT read
  Sensors::poll();  // BMP read with same pressure
  const Sensors::HealthSnapshot& suspect = Sensors::health();
  assert(suspect.bmp280.state == Sensors::HealthState::Ready);
  assert(suspect.bmp280.suspect);
  assert(suspect.bmp280.consecutiveFailures == 0);
  Sensors::getLatest(temperature, humidity, pressure);
  assert(std::isfinite(pressure));

  // SHT read failures force a reinitialization. A failed reconnect followed
  // by a retry must not delete the previous child objects twice.
  g_sht_read_ok = false;
  for (int i = 0; i < 3; ++i) {
    advance(1000);
    Sensors::poll();  // SHT failed read
    Sensors::poll();  // BMP remains independently healthy
  }
  assert(Sensors::health().sht4x.state == Sensors::HealthState::Offline);
  g_sht_begin_ok = false;
  advance(1000);
  Sensors::poll();  // failed SHT reinitialization
  g_sht_begin_ok = true;
  g_sht_read_ok = true;
  advance(2000);
  Sensors::poll();  // BMP's due read gets one action first
  Sensors::poll();  // successful SHT reinitialization
  Sensors::poll();  // valid SHT read
  assert(Sensors::health().sht4x.state == Sensors::HealthState::Ready);
  assert(g_sht_double_deletes == 0);
  assert(I2cBus::services[0] == 0 && I2cBus::observations[0] == 0);
  assert(I2cBus::observations[1] == g_sht_begin_calls + g_bmp_begin_calls +
         g_sht_read_calls + g_bmp_read_calls);
  Sensors::scanI2C();
  assert(I2cBus::probes[0] == 0 && I2cBus::probes[1] == 126);
  return 0;
}
'''


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sensor-health-") as temp_dir:
        temp = Path(temp_dir)
        (temp / "Sensors.cpp").write_text((SENSOR_DIR / "Sensors.cpp").read_text())
        (temp / "Sensors.h").write_text((SENSOR_DIR / "Sensors.h").read_text())
        (temp / "SensorStub.h").write_text(STUBS)
        (temp / "Adafruit_BMP280.h").write_text('#include "SensorStub.h"\n')
        (temp / "Adafruit_SHT4x.h").write_text('#include "SensorStub.h"\n')
        (temp / "Config.h").write_text(CONFIG)
        (temp / "NanoComm.h").write_text(NANO)
        (temp / "I2cRecovery.h").write_text(RECOVERY)
        (temp / "SDLogger.h").write_text(LOGGER)
        (temp / "sensor_health_integration.cpp").write_text(TEST)
        executable = temp / "sensor_health_integration"
        subprocess.run([
            "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(temp),
            str(temp / "Sensors.cpp"), str(temp / "sensor_health_integration.cpp"),
            "-o", str(executable),
        ], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
