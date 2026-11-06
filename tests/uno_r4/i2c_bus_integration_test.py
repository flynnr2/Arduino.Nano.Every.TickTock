#!/usr/bin/env python3
"""Compile the hardware adapter with checked GPIO/controller mocks."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
ARDUINO = r'''
#pragma once
#include <stdint.h>
constexpr int INPUT=0, OUTPUT=1, LOW=0;
constexpr int WIRE_SDA_PIN=18, WIRE_SCL_PIN=19;
constexpr int WIRE1_SDA_PIN=27, WIRE1_SCL_PIN=26;
uint32_t millis();
int digitalRead(int pin);
void digitalWrite(int pin, int value);
void pinMode(int pin, int mode);
void delayMicroseconds(uint32_t us);
'''
WIRE = r'''
#pragma once
#include <stdint.h>
struct TwoWire {
  unsigned begins=0, ends=0, probes=0, clock=0, timeout=0;
  bool enabled=false;
  uint8_t result=2;
  void begin() { ++begins; enabled=true; }
  void end() { ++ends; enabled=false; }
  void setClock(unsigned n) { clock=n; }
  void setWireTimeout(unsigned n) { timeout=n; }
  void beginTransmission(uint8_t) { ++probes; }
  uint8_t endTransmission() { return result; }
};
extern TwoWire Wire, Wire1;
'''
LOGGER = r'''
#pragma once
namespace SDLogger {
bool isLogging();
void logUnoEvent(const char*, const char*);
}
'''
TEST = r'''
#include "I2cRecovery.h"
#include "Arduino.h"
#include "Wire.h"
#include <cassert>
#include <cstring>
#include <string>
#include <vector>
TwoWire Wire, Wire1;
uint32_t now=0, elapsed=0;
int modes[32]={}, latches[32]={};
bool held[32]={};
unsigned writes[32]={}, releases[32]={};
std::vector<std::string> logs;
uint32_t millis() { return now; }
int digitalRead(int pin) { return !held[pin] && modes[pin] == INPUT; }
void digitalWrite(int pin, int value) {
  assert(value==LOW);  // No pin can ever be actively driven high.
  assert(!(pin==WIRE_SDA_PIN || pin==WIRE_SCL_PIN ? Wire : Wire1).enabled);
  latches[pin]=value; ++writes[pin];
}
void pinMode(int pin, int mode) {
  assert(!(pin==WIRE_SDA_PIN || pin==WIRE_SCL_PIN ? Wire : Wire1).enabled);
  if(mode==OUTPUT) assert(writes[pin] && latches[pin]==LOW);
  if(mode==INPUT && modes[pin]==OUTPUT) {
    ++releases[pin];
    if(pin==WIRE1_SCL_PIN) held[WIRE1_SDA_PIN]=false;
  }
  modes[pin]=mode;
}
void delayMicroseconds(uint32_t us) { elapsed+=us; }
namespace SDLogger {
bool isLogging() { return true; }
void logUnoEvent(const char* category, const char* message) {
  assert(std::strcmp(category,"i2c.recovery")==0);
  logs.emplace_back(message);
}
}
bool logged(const char* needle) {
  for(const auto& line:logs) if(line.find(needle)!=std::string::npos) return true;
  return false;
}
int main() {
  using I2cBus::Bus;
  I2cBus::begin();
  assert(Wire.begins==1 && Wire1.begins==1);
  assert(Wire.clock==400000 && Wire1.clock==100000);
  for(unsigned i=0;i<100;++i) {
    assert(I2cBus::probe(Bus::Sensors,0x44)==2);
    assert(I2cBus::probe(Bus::Oled,0x3d)==2);
  }
  assert(Wire.ends==0 && Wire1.ends==0 && logs.empty());
  assert(std::strstr(I2cBus::resultName(2),"NACK"));
  held[WIRE1_SDA_PIN]=true;
  assert(I2cBus::sda(Bus::Sensors)==0 && I2cBus::sda(Bus::Oled)==1);
  assert(!I2cBus::service(Bus::Sensors));
  assert(Wire1.ends==0);
  ++now;
  assert(I2cBus::service(Bus::Sensors));
  assert(Wire1.ends==1 && Wire1.begins==2 && Wire.ends==0);
  assert(Wire1.clock==100000 && Wire1.timeout==I2cBus::TIMEOUT_US);
  assert(writes[WIRE_SDA_PIN]==0 && writes[WIRE_SCL_PIN]==0);
  assert(logged("Wire1 sensors: stuck bus detected; SDA=low SCL=high"));
  assert(logged("recovery attempt 1/2; pulses=1 STOP=succeeded"));
  assert(logged("lines=released; devices=not checked"));
  assert(elapsed<1000);
  // Held-low OLED clock cannot prevent Wire1 use or trigger GPIO pulses.
  held[WIRE_SCL_PIN]=true;
  assert(!I2cBus::service(Bus::Oled));
  ++now;
  assert(!I2cBus::service(Bus::Oled));
  assert(writes[WIRE_SDA_PIN]==0 && writes[WIRE_SCL_PIN]==0);
  assert(logged("clock=held low; clock recovery could not proceed"));
  now+=1000;
  assert(!I2cBus::service(Bus::Oled));
  assert(Wire.ends==2 && logged("Wire OLED: recovery exhausted"));
  assert(Wire.clock==400000 && Wire.timeout==I2cBus::TIMEOUT_US);
  const auto count=logs.size();
  for(unsigned i=0;i<100;++i) {
    now+=1000;
    assert(I2cBus::probe(Bus::Oled,0x3d)==254);
    assert(I2cBus::probe(Bus::Sensors,0x44)==2);
  }
  assert(Wire.ends==2 && Wire1.ends==1);
  // The only additional transition is Wire1's stable re-arm.
  assert(logs.size()<=count+1);
  assert(elapsed<2000);
}
'''
with tempfile.TemporaryDirectory(prefix="i2c-adapter-") as directory:
    temp = Path(directory)
    for name in ("I2cRecovery.cpp", "I2cRecovery.h", "I2cRecoveryState.h"):
        (temp / name).write_text((ROOT / "Uno.R4.Deprecated/src" / name).read_text())
    for name, source in (("Arduino.h", ARDUINO), ("Wire.h", WIRE),
                         ("SDLogger.h", LOGGER), ("test.cpp", TEST)):
        (temp / name).write_text(source)
    executable = temp / "test"
    subprocess.run(["c++", "-std=c++11", "-Wall", "-Wextra", "-Werror", "-I", str(temp),
                    str(temp / "I2cRecovery.cpp"), str(temp / "test.cpp"),
                    "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
print("I2C adapter routing, open-drain GPIO, bounded recovery and readable logs passed")
