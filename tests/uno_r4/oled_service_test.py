#!/usr/bin/env python3
"""Exercise actual OLED scheduler and diagnostic code with a checked fake bus."""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[2]
source = (root / 'Uno.R4.Deprecated/src/Display.cpp').read_text()
service = source[source.index('static OledTransfer transfer;'):source.index('void observeSwing(')]
code = r'''
#include "OledTransfer.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_ADDR 0x3d
#define WIRE_DEFAULT_TIMEOUT_US 100000
constexpr int SDA=18, SCL=19;
uint32_t now=0, us=0;
uint32_t millis() { return now; }
uint32_t micros() { return us; }
int digitalRead(int) { return 1; }
namespace NanoComm { bool pending=false; bool hasPendingIngestWork(){return pending;} }
namespace SDLogger {
std::vector<std::string> categories, messages;
void logUnoEvent(const char* c, const char* m) { categories.push_back(c);messages.push_back(m); }
}
struct Bus {
 unsigned calls=0, timeout=100000, bytes=0;
 uint8_t error=0, address=0;
 void setWireTimeout(unsigned n) { timeout=n; }
 void beginTransmission(uint8_t a) { address=a;bytes=0; }
 size_t write(uint8_t) { ++bytes;return 1; }
 size_t write(const uint8_t*,size_t n) { bytes+=n;return n; }
 uint8_t endTransmission() {
   assert(timeout==10000 && bytes<=32); ++calls;us+=3000;return error;
 }
} Wire, Wire1;
namespace I2cBus {
enum class Bus { Oled, Sensors };
constexpr uint32_t TIMEOUT_US=10000;
bool allowed[2] = {true, true};
unsigned services[2] = {}, observations[2] = {};
bool service(Bus bus) { ++services[(int)bus]; return allowed[(int)bus]; }
void observe(Bus bus) { ++observations[(int)bus]; }
int sda(Bus bus) { return bus == Bus::Oled ? 1 : 0; }
int scl(Bus bus) { return bus == Bus::Oled ? 1 : 0; }
const char* resultName(uint8_t value) {
  if (value == 0) return "responding";
  if (value == 255) return "not probed";
  if (value == 254) return "bus suspended";
  return "NACK";
}
uint8_t probe(Bus bus, uint8_t address) {
  if (!service(bus)) return 254;
  auto& wire = bus == Bus::Oled ? Wire : Wire1;
  wire.setWireTimeout(10000);
  wire.beginTransmission(address);
  const uint8_t result = wire.endTransmission();
  wire.setWireTimeout(TIMEOUT_US);
  observe(bus);
  return result;
}
}
namespace Display {
struct Panel { uint8_t bytes[1024]={}; uint8_t* getBuffer(){return bytes;} } display;
void update();
void begin();
''' + service + r'''
void update() { transfer.begin(display.getBuffer(), millis()); }
void begin() { displayReady=true; }
}
int main() {
 using namespace Display;
 displayReady=true;
 // A suspended Wire1 must not prevent OLED transfers on Wire.
 I2cBus::allowed[1]=false;
 now=1000;service(true);
 assert(Wire.calls==0 && transfer.metrics.deferred==1);
 assert(I2cBus::services[0]==0 && I2cBus::services[1]==0);
 service(true);assert(transfer.metrics.deferred==1);
 now=2000;service(false);
 assert(Wire.calls==1 && transfer.active());
 service(true);assert(Wire.calls==1);
 for(unsigned i=0;i<47;++i) {
   unsigned old=Wire.calls;service(false);assert(Wire.calls==old+1);
 }
 assert(!transfer.active() && transfer.metrics.completed==1);
 assert(Wire.timeout==I2cBus::TIMEOUT_US);
 now=3000;unsigned before=Wire.calls;service(false);
 assert(Wire.calls==before && transfer.metrics.unchanged==1);
 now=30000;service(true);
 assert(SDLogger::categories.size()==2 && probeIndex==0 && Wire.calls==before);
 assert(SDLogger::messages[1].find("Wire OLED_3d=not probed SDA=1 SCL=1")!=std::string::npos);
 assert(SDLogger::messages[1].find("Wire1 SHT41_44=not probed BMP280_77=not probed SDA=0 SCL=0")!=std::string::npos);
 I2cBus::allowed[1]=true;
 service(false);assert(Wire.address==0x3d && Wire.calls==before+1);
 I2cBus::allowed[0]=false;
 service(false);assert(Wire1.address==0x44 && Wire1.calls==1 && Wire.calls==before+1);
 service(false);assert(Wire1.address==0x77 && Wire1.calls==2 && Wire.calls==before+1);
 assert(probeIndex==3);
 // Suspended OLED stays transaction-free while Wire1 diagnostics progressed.
 now=31000;display.bytes[0]=1;
 for(unsigned i=0;i<100;++i) service(false);
 assert(Wire.calls==before+1);
 I2cBus::allowed[0]=true;
 Wire.error=5;service(false);
 assert(transfer.metrics.failed==1 && errorSda==1 && errorScl==1);
 assert(Wire.timeout==I2cBus::TIMEOUT_US);
 now=60000;service(true);
 assert(SDLogger::messages[2].find("fail,1")!=std::string::npos);
 assert(SDLogger::messages[3].find("age_ms,30000")!=std::string::npos);
 for(const auto& s:SDLogger::messages) assert(s.size()<279);
 puts("OLED scheduler, bounded transfers, backlog deferral, probes and diagnostics passed");
}
'''
with tempfile.TemporaryDirectory(prefix='oled-service-') as directory:
    cpp = Path(directory) / 'test.cpp'
    cpp.write_text(code)
    executable = Path(directory) / 'test'
    subprocess.run(['c++','-std=c++11','-Wall','-Wextra','-Werror','-I',str(root/'Uno.R4.Deprecated/src'),
                    str(cpp),'-o',str(executable)],check=True)
    subprocess.run([str(executable)],check=True)
