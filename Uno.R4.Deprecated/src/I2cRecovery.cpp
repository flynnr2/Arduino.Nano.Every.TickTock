#include "I2cRecovery.h"
#include "I2cRecoveryState.h"
#include "SDLogger.h"
#include <Arduino.h>
#include <Wire.h>
#include <cstdio>

namespace I2cBus {
namespace {
I2cRecovery::State states[2];
TwoWire& controller(Bus bus) { return bus == Bus::Oled ? Wire : Wire1; }
int sdaPin(Bus bus) { return bus == Bus::Oled ? WIRE_SDA_PIN : WIRE1_SDA_PIN; }
int sclPin(Bus bus) { return bus == Bus::Oled ? WIRE_SCL_PIN : WIRE1_SCL_PIN; }
const char* name(Bus bus) { return bus == Bus::Oled ? "Wire OLED" : "Wire1 sensors"; }
const char* level(bool high) { return high ? "high" : "low"; }
void restore(Bus bus) {
  controller(bus).begin();
  controller(bus).setClock(bus == Bus::Oled ? OLED_CLOCK_HZ : SENSOR_CLOCK_HZ);
  controller(bus).setWireTimeout(TIMEOUT_US);
}
struct IO {
  Bus bus;
  bool sda() { return I2cBus::sda(bus); }
  bool scl() { return I2cBus::scl(bus); }
  void releaseController() { controller(bus).end(); }
  void restoreController() { restore(bus); }
  void releaseSda() { pinMode(sdaPin(bus), INPUT); }
  void releaseScl() { pinMode(sclPin(bus), INPUT); }
  // Set the output latch LOW before enabling output. Release uses INPUT;
  // external pull-ups provide HIGH. Never enable a push-pull HIGH output.
  void lowSda() { digitalWrite(sdaPin(bus), LOW); pinMode(sdaPin(bus), OUTPUT); }
  void lowScl() { digitalWrite(sclPin(bus), LOW); pinMode(sclPin(bus), OUTPUT); }
  void delayUs(uint32_t us) { delayMicroseconds(us); }
  void report(I2cRecovery::Event event, const I2cRecovery::Outcome& o) {
    using I2cRecovery::Event;
    const char* status = "unknown";
    switch (event) {
      case Event::Detected: status = "stuck bus detected"; break;
      case Event::Attempt: status = "recovery attempt"; break;
      case Event::Recovered: status = "recovered; stability pending"; break;
      case Event::StillFailed: status = "still failed"; break;
      case Event::Exhausted: status = "recovery exhausted"; break;
      case Event::ManualRecovery: status = "spontaneous/manual recovery"; break;
      case Event::Rearmed: status = "stable recovery; budget re-armed"; break;
    }
    char message[280];
    if (event == Event::Attempt) {
      snprintf(message, sizeof(message),
          "%s: %s %u/2; pulses=%u STOP=%s; clock=%s; restored SDA=%s SCL=%s; lines=%s; devices=not checked (see bus probes)",
          name(bus), status, unsigned(o.attempt), unsigned(o.pulses),
          o.stop ? "succeeded" : "not completed",
          o.clockBlocked ? "held low; clock recovery could not proceed" : "available",
          level(o.sdaHigh), level(o.sclHigh), o.linesReleased ? "released" : "not released");
    } else {
      snprintf(message, sizeof(message),
          "%s: %s; SDA=%s SCL=%s; lines=%s; devices=not checked (see bus probes)",
          name(bus), status, level(o.sdaHigh), level(o.sclHigh),
          o.linesReleased ? "released" : "not released");
    }
    if (SDLogger::isLogging()) SDLogger::logUnoEvent("i2c.recovery", message);
  }
};
}
void begin() { restore(Bus::Oled); restore(Bus::Sensors); }
int sda(Bus bus) { return digitalRead(sdaPin(bus)); }
int scl(Bus bus) { return digitalRead(sclPin(bus)); }
bool service(Bus bus) {
  IO io{bus};
  return states[static_cast<uint8_t>(bus)].service(millis(), io);
}
void observe(Bus bus) { (void)service(bus); }
uint8_t probe(Bus bus, uint8_t address) {
  if (!service(bus)) return 254;
  TwoWire& wire = controller(bus);
  wire.beginTransmission(address);
  const uint8_t result = wire.endTransmission();
  observe(bus);
  return result;
}
const char* resultName(uint8_t result) {
  switch (result) {
    case 0: return "responding";
    case 1: return "buffer error";
    case 2: return "address NACK/absent";
    case 3: return "data NACK";
    case 4: return "controller error";
    case 5: return "timeout";
    case 6: return "controller not initialized";
    case 254: return "suspended for bus recovery";
    default: return "not probed";
  }
}
}
