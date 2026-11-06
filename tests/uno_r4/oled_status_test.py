#!/usr/bin/env python3
"""Run production OLED rows, ticker and rotation against a pixel-checked panel.
Set OLED_SNAPSHOT_PATH to save representative rows for optional visual review.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = root / 'Uno.R4.Deprecated/src'
display = (src / 'Display.cpp').read_text()
ticker = display[display.index('// All screens share'):display.index('void begin()')]
update = display[display.index('// Fixed-size fault tracking'):display.rindex('} // namespace Display')]
code = r'''
#include "PendulumSampleState.h"
#include "PendulumProtocolReceiver.h"
#include "PeriodDisplayState.h"
#include "OledScreenRotation.h"
#include "OledTimebaseFormat.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <ctime>
#include <cmath>
#include <cfloat>
#include <cstdlib>
#include <fstream>
#define SENSORS_HOST_TEST
#include "Sensors.h"
constexpr int SSD1306_WHITE=1, SSD1306_BLACK=0, SCREEN_WIDTH=128, SCREEN_HEIGHT=64;
uint32_t now=1000;
uint32_t millis() { return now; }
namespace UnoTunables { uint16_t oledShortMinutes=5, oledLongMinutes=60; }
namespace Sensors { HealthSnapshot state={}; const HealthSnapshot& health() { return state; } }
namespace SDLogger {
bool active=true, mounted=true;
bool isLogging() { return active; } bool ready() { return mounted; }
time_t epoch=1789120483; time_t currentEpoch() { return epoch; }
}
namespace NanoComm {
PendulumSampleState currentSample;
bool error=false, havePps=false;
bool hasProtocolError() { return error; }
CanonicalSwingSample swing={};
bool getCanonicalSwingSample(CanonicalSwingSample& s) { s=swing; return true; }
constexpr uint32_t AGE_UNKNOWN_MS=UINT32_MAX;
struct LatestPpsStatus { uint32_t age_ms=0, holdover_age_ms=0; GpsStatus status=LOCKED; };
LatestPpsStatus pps;
bool latestPpsStatus(LatestPpsStatus& out) { out=pps; return havePps; }
uint32_t swingAge=AGE_UNKNOWN_MS, ppsAge=AGE_UNKNOWN_MS;
uint32_t canonicalSwingAgeMs() { return swingAge; }
uint32_t canonicalPpsAgeMs() { return ppsAge; }
}
namespace Display {
bool displayReady=true;
struct Transfer {
  bool busy=false;
  bool active() { return busy; }
  void begin(const uint8_t*, uint32_t) {}
} transfer;
PeriodDisplayState periodState;
struct Panel {
  std::string rows[8];
  int row=0, x=0;
  void clearDisplay() { for(auto& s:rows) s.clear(); }
  void setTextSize(int n) { assert(n==1); }
  void setTextWrap(bool wrap) { assert(!wrap); }
  void fillRect(int left,int y,int width,int h,int) {
    assert(left==0 && width==128 && y>=0 && y+h<=64);
    for(int r=y/8;r<(y+h)/8;++r) rows[r].clear();
  }
  void setTextColor(int,int=0) {}
  void setCursor(int left,int y) { assert(left>=0 && y>=0 && y+8<=64); x=left;row=y/8; }
  void write(char c) {
    assert(x+6<=SCREEN_WIDTH); x+=6;
    rows[row]+=c; assert(rows[row].size()<=21);
  }
  void print(const char* text) { for(;*text;++text) write(*text); }
  uint8_t* getBuffer() { return nullptr; }
} display;
''' + ticker + update + r'''
void clearUi() {
  rowsInitialized=false; bodyRefreshRequested=true;
  screenRotation=OledScreenRotation{};
  tickerRefreshRequested=true; tickerMessageCount=0;
  tickerMessageIndex=tickerCharOffset=nextAlertSlot=0;
  for(auto& alert:tickerAlerts) alert=TickerAlert{};
  for(auto& fault:activeFaults) fault=nullptr;
  previousFaultMask=0; dropsSeen=false; dropWarning=false;
  periodState=PeriodDisplayState{};
  NanoComm::error=NanoComm::havePps=false;
  NanoComm::pps=NanoComm::LatestPpsStatus{};
  NanoComm::swingAge=NanoComm::ppsAge=NanoComm::AGE_UNKNOWN_MS;
  SDLogger::active=SDLogger::mounted=true;
  Sensors::state=Sensors::HealthSnapshot{};
}
bool tickerHas(const std::string& text) {
  for(size_t i=0;i<tickerMessageCount;++i)
    if(std::string(tickerMessages[i]).find(text)!=std::string::npos) return true;
  return false;
}
void snapshot(const char* title) {
  const char* path=std::getenv("OLED_SNAPSHOT_PATH");
  if(!path) return;
  std::ofstream out(path,std::ios::app);
  out<<title<<"\n";
  for(const auto& row:display.rows) out<<row<<"\n";
  out<<"\n";
}
}
int main() {
 using namespace Display;
 // Test the exact 30/15/15-second boundaries, delayed service and rollover.
 for(uint32_t start : {1000u,0xfffffff0u}) {
   OledScreenRotation rotation;
   assert(!rotation.update(start) && !rotation.supplemental());
   assert(!rotation.update(start+29999u));
   assert(rotation.update(start+30000u) && rotation.supplemental());
   assert(!rotation.update(start+44999u));
   assert(rotation.update(start+45000u) && rotation.timebase());
   assert(!rotation.update(start+59999u));
   assert(rotation.update(start+60000u) && !rotation.timebase() && !rotation.supplemental());
   assert(rotation.update(start+165000u) && rotation.timebase());
   assert(rotation.update(start+180000u) && !rotation.timebase());
 }
 for(int repetition=0;repetition<2;++repetition) {
   clearUi(); update();
   assert(display.rows[0]=="2026-09-11 09:54:43Z");
   assert(display.rows[1]=="P S: waiting" && display.rows[6]=="dB L: waiting");
   assert(display.rows[7]=="STATUS: OK");
   SDLogger::active=false; update(); assert(display.rows[0]=="! SD LOG OFF");
   assert(tickerHas("SD LOG OFF"));
   NanoComm::error=true; update(); assert(display.rows[0]=="! SERIAL ERROR");
   now+=2000; update(); assert(display.rows[0]=="! SD LOG OFF");
   NanoComm::error=false; SDLogger::active=true; SDLogger::mounted=false;
   update(); assert(display.rows[0]=="! SD LOG NOT READY");
   SDLogger::mounted=true; update(); assert(display.rows[0][0]!='!');
   ++NanoComm::swing.drop_ir;
   update(); assert(display.rows[0]=="! NANO DROPS RISING");
   assert(tickerHas("DROPS RISING"));
   now+=12000; update(); assert(display.rows[0][0]!='!');
   update(); assert(display.rows[0][0]!='!');
 }
 clearUi();
 NanoComm::swingAge=4000; NanoComm::ppsAge=4000; update();
 assert(tickerHas("SWING FEED STALE") && tickerHas("PPS FEED STALE"));
 now+=30000; update();
 assert(tickerHas("SWING FEED STALE") && tickerHas("PPS FEED STALE"));
 NanoComm::swingAge=NanoComm::ppsAge=0; update(); assert(display.rows[0][0]!='!');
 Sensors::state.bmp280.state=Sensors::HealthState::Offline; update();
 assert(display.rows[0]=="! BMP SENSOR FAULT");
 now+=30000; update(); assert(tickerHas("BMP SENSOR FAULT"));
 Sensors::state.bmp280.state=Sensors::HealthState::Ready; update();
 assert(display.rows[0][0]!='!');
 NanoComm::havePps=true; NanoComm::pps.status=NO_PPS; update();
 assert(tickerHas("GPS NO PPS"));
 now+=30000; update(); assert(tickerHas("GPS NO PPS"));
 NanoComm::pps.status=LOCKED; NanoComm::pps.age_ms=5000; update();
 assert(tickerHas("PPS STALE"));
 NanoComm::pps.age_ms=0; update(); assert(display.rows[0][0]!='!');
 // Common clock retains its cadence; fault appearance and recovery bypass it.
 SDLogger::epoch=0; update(); assert(display.rows[0]=="UTC: waiting for sync");
 SDLogger::epoch=1789120483; update(); const auto clock=display.rows[0];
 SDLogger::epoch+=1; update(); assert(display.rows[0]==clock);
 SDLogger::epoch+=15; update(); assert(display.rows[0]!=clock);
 // Capture display and nonmutating rendering.
 clearUi(); update(); CanonicalSwingSample sample={};
 sample.edge1_tcb0=15000000; sample.edge2_tcb0=16000000;
 sample.edge3_tcb0=31000160; sample.edge4_tcb0=32000000;
 periodState.observeSwing(sample,16000000,now); bodyRefreshRequested=true; update();
 assert(display.rows[1].find("2000000.0us")!=std::string::npos);
 assert(display.rows[3].find("30.00000BPM")!=std::string::npos);
 snapshot("Rating: capture learning");
 unsigned char estimator[sizeof(periodState)], captured[sizeof(NanoComm::currentSample)];
 memcpy(estimator,&periodState,sizeof(periodState));
 memcpy(captured,&NanoComm::currentSample,sizeof(NanoComm::currentSample));
 const uint32_t start=now;
 now=start+30000; update(); assert(screenRotation.supplemental());
 now=start+45000; update(); assert(screenRotation.timebase());
 now=start+60000; update(); assert(display.rows[1]=="P S: stale");
 transfer.busy=true; now+=30000; const auto retained=display.rows[1]; update();
 assert(display.rows[1]==retained); transfer.busy=false; update();
 assert(!memcmp(estimator,&periodState,sizeof(periodState)));
 assert(!memcmp(captured,&NanoComm::currentSample,sizeof(NanoComm::currentSample)));
 clearUi();
 for(unsigned i=0;i<32;++i) {
   ++sample.seq; sample.edge0_tcb0=sample.edge4_tcb0;
   sample.edge1_tcb0=sample.edge0_tcb0+15000000;
   sample.edge2_tcb0=sample.edge0_tcb0+16000000;
   sample.edge3_tcb0=sample.edge0_tcb0+31000160;
   sample.edge4_tcb0=sample.edge0_tcb0+32000000;
   now+=2000; periodState.observeSwing(sample,16000000,now);
 }
 update(); assert(display.rows[1].find("2000000.00us")!=std::string::npos);
 snapshot("Rating: capture precision");
 NanoComm::currentSample.temperature_C=21.25f;
 NanoComm::currentSample.humidity_pct=53.75f;
 NanoComm::currentSample.pressure_hPa=1013.25f;
 NanoComm::havePps=true; NanoComm::pps.status=HOLDOVER; NanoComm::pps.holdover_age_ms=999999;
 Sensors::state.sht4x.state=Sensors::HealthState::Ready;
 Sensors::state.bmp280.state=Sensors::HealthState::Ready;
 now+=30000; update();
 assert(display.rows[1]=="T:21.2C RH:53.8%");
 assert(display.rows[2]=="P:1013.2hPa");
 assert(display.rows[3]=="GPS:HLD HAG:999s");
 assert(display.rows[5]=="LOG:ON SD:OK S:R B:R");
 snapshot("Supplemental: healthy GPS");
 auto before=periodState.estimator().horizon(0).periodUs;
 configureRating(5,60); update(); assert(periodState.estimator().horizon(0).periodUs==before);
 UnoTunables::oledShortMinutes=30; UnoTunables::oledLongMinutes=360;
 configureRating(30,360); update(); assert(display.rows[6]=="EWMA S:30m L:360m");
 for(float v : {FLT_MAX,-FLT_MAX,0.0f,NAN,INFINITY}) {
   NanoComm::currentSample.temperature_C=v; NanoComm::currentSample.humidity_pct=v;
   NanoComm::currentSample.pressure_hPa=v; bodyRefreshRequested=true; update();
   assert(display.rows[1].find("C RH:")!=std::string::npos && display.rows[1].back()=='%');
   assert(display.rows[2].substr(display.rows[2].size()-3)=="hPa");
 }
 NanoComm::pps.holdover_age_ms=UINT32_MAX; bodyRefreshRequested=true; update();
 assert(display.rows[3]=="GPS:HLD HAG:4294967s");
 SDLogger::active=false; bodyRefreshRequested=true; update();
 snapshot("Supplemental: logging fault");
 // Transient alert expiry uses elapsed unsigned time, including expiry at zero.
 clearUi(); now=UINT32_MAX-11999u; enqueueTickerAlert("rollover",12000);
 rebuildTickerMessages(); assert(tickerHas("rollover"));
 now+=11999u; rebuildTickerMessages(); assert(tickerHas("rollover"));
 ++now; rebuildTickerMessages(); assert(!tickerHas("rollover"));
 // An expired long alert must not leave a stale offset on its replacement.
 enqueueTickerAlert("123456789012345678901TAIL",12000); rebuildTickerMessages();
 advanceTicker(tickerMessages[tickerMessageIndex]); assert(tickerCharOffset==21);
 now+=12000; rebuildTickerMessages(); renderTicker(tickerMessages[tickerMessageIndex]);
 assert(tickerCharOffset==0 && display.rows[7]=="STATUS: OK");
 // Ring text is owned once; a full ring cannot evict persistent warnings.
 Sensors::state.bmp280.state=Sensors::HealthState::Offline; update();
 for(size_t i=0;i<MAX_TICKER_ALERTS+2;++i) {
   char message[48]; snprintf(message,sizeof(message),"transient %u",unsigned(i));
   enqueueTickerAlert(message,12000);
 }
 rebuildTickerMessages(); assert(tickerMessageCount==MAX_TICKER_ALERTS+1);
 assert(tickerHas("BMP SENSOR FAULT"));
 for(size_t i=0;i<MAX_TICKER_ALERTS;++i) {
   bool owned=false;
   for(size_t j=0;j<tickerMessageCount;++j) if(tickerMessages[j]==tickerAlerts[i].text) owned=true;
   assert(owned);
 }
 now+=12000; update(); assert(tickerHas("BMP SENSOR FAULT") && !tickerHas("transient"));
 // Fault updates do not prematurely repaint slower body rows.
 clearUi(); update(); auto draw=lastBodyDrawMs;
 now+=1000; NanoComm::error=true; update(); assert(display.rows[0]=="! SERIAL ERROR");
 assert(lastBodyDrawMs==draw); now+=1000; update(); assert(lastBodyDrawMs==now);
 auto tickerDraw=lastTickerMs; now=tickerDraw+3999; update(); assert(lastTickerMs==tickerDraw);
 ++now; update(); assert(lastTickerMs==tickerDraw+4000);
 // Drop fault expiry itself is safe at rollover.
 clearUi(); now=UINT32_MAX-10; update(); ++NanoComm::swing.drop_pps; update();
 assert(display.rows[0]=="! NANO DROPS RISING");
 now+=12000; update(); assert(display.rows[0][0]!='!');
 puts("OLED production rows/pixel bounds, rotation, alert ownership/expiry, faults and nonmutating rendering passed");
}
'''
with tempfile.TemporaryDirectory(prefix='pendulum-oled-') as directory:
    cpp = Path(directory) / 'test.cpp'
    cpp.write_text(code)
    exe = Path(directory) / 'test'
    subprocess.run(['c++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                    '-I', str(src), str(cpp), '-o', str(exe)], check=True)
    snapshot = os.environ.get('OLED_SNAPSHOT_PATH')
    if snapshot:
        Path(snapshot).write_text('')
    subprocess.run([str(exe)], check=True)
