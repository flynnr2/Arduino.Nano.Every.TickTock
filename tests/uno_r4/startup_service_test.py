#!/usr/bin/env python3
"""Exercise production framing, metadata gate and retry pacing with a fake serial link."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'Uno.R4.Deprecated/src/NanoComm.cpp').read_text()


def extract(signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end].replace('unsigned long', 'uint32_t')


code = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <string>
#include <vector>
#define F(x) x
uint32_t clockMs=0;
uint32_t millis() { return clockMs; }
struct SerialFake {
  std::string input; size_t pos=0; std::vector<std::string> sent;
  int available() { return int(input.size()-pos); }
  int read() { return available() ? (unsigned char)input[pos++] : -1; }
  void println(const char* s) { sent.emplace_back(s); }
} NANO_SERIAL;
namespace Display { void scrollLog(const char*) {} }
namespace SDLogger {
  int readyLogs=0, opens=0;
  void logUnoEvent(const char*,const char*) { ++readyLogs; }
  bool onMetadataReady() { ++opens; return true; }
}
constexpr int NANO_LINE_MAX=128, RX_LINE_STALE_MS=250, MAX_LINES_PER_SERVICE=48;
constexpr int INGEST_EVENT_QUEUE_LEN=56, CMD_TIMEOUT_MS=250;
constexpr uint32_t NANO_STARTUP_TIMEOUT_MS=6500;
constexpr uint32_t STARTUP_REPLAY_INTERVAL_MS=500, STARTUP_REPLAY_SLOW_MS=5000;
char lineBuf[NANO_LINE_MAX]={}; size_t lineLen=0;
bool lineOverflow=false, rxOverflowLogged=false, streaming=true, protocolError=false;
uint32_t lastNanoByteMs=0, partialLineActivityMs=0, stalePartialLineDrops=0;
bool cmdInFlight=false, startupMetadataLogged=false;
uint32_t cmdSentMs=0, startupSyncMs=0, nextStartupReplayMs=0;
uint8_t ingestQueueCount=0;
struct NanoSessionConfig {
  bool config_received=false, canonical_swing_schema_received=false,
       canonical_pps_schema_received=false;
};
struct { NanoSessionConfig session; } currentSample;
int normalCommands=0; void dispatchNextCommand() { ++normalCommands; }
std::vector<std::string> accepted;
bool metadataReady();
void parseIncomingLine(const char* line) {
  if (!strcmp(line,"CFG")) currentSample.session.config_received=true;
  if (!strcmp(line,"SCH-CSW")) currentSample.session.canonical_swing_schema_received=true;
  if (!strcmp(line,"SCH-CPS")) currentSample.session.canonical_pps_schema_received=true;
  if ((!strncmp(line,"CSW,",4) || !strncmp(line,"CPS,",4)) && metadataReady()) accepted.emplace_back(line);
}
'''
code += extract('bool metadataReady()') + '\n'
code += extract('static bool readCompleteLineFromNano(') + '\n'
code += extract('void service()') + '\n'
code += r'''
int main() {
  NANO_SERIAL.input="partial\nCSW,0\nCFG\nSCH-CSW\nCPS,0\nSCH-CPS\nCSW,1\nCPS,1\n";
  service();
  assert(metadataReady());
  assert(accepted.size()==2 && accepted[0]=="CSW,1" && accepted[1]=="CPS,1");
  assert(SDLogger::readyLogs==1 && SDLogger::opens==1);
  NANO_SERIAL.input += "CFG\nSCH-CSW\nSCH-CPS\nCPS,2\n";
  service(); assert(accepted.size()==3 && SDLogger::opens==1);
  ingestQueueCount=INGEST_EVENT_QUEUE_LEN;
  NANO_SERIAL.input += "CPS,3\n";
  service(); assert(accepted.size()==3);
  ingestQueueCount=0; service(); assert(accepted.size()==4);
  currentSample.session.canonical_pps_schema_received=false;
  clockMs=1; service(); assert(NANO_SERIAL.sent.size()==1);
  clockMs=499; service(); assert(NANO_SERIAL.sent.size()==1);
  clockMs=501; service(); assert(NANO_SERIAL.sent.size()==2);
  clockMs=7000; service(); assert(NANO_SERIAL.sent.size()==3);
  startupSyncMs=nextStartupReplayMs=0xfffffff0u; clockMs=0xfffffff0u;
  service(); const auto before=NANO_SERIAL.sent.size();
  clockMs=100; service(); assert(NANO_SERIAL.sent.size()==before);
  clockMs=484; service(); assert(NANO_SERIAL.sent.size()==before+1);
  puts("startup metadata gate, replay, buffering and millis wrap passed");
}
'''
assert 'delay(' not in extract('void readStartup()')
with tempfile.TemporaryDirectory(prefix='pendulum-startup-') as temp:
    cpp = Path(temp) / 'test.cpp'
    cpp.write_text(code)
    exe = Path(temp) / 'test'
    subprocess.run(['c++', '-std=c++11', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
