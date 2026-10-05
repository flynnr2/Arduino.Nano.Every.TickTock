#!/usr/bin/env python3
"""Compile the production v3 parser paths against the Nano-owned header."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'Uno.R4.Deprecated/src/NanoComm.cpp').read_text()


def extract(signature):
    start = source.index(signature)
    brace = source.index('{', start)
    level = 1
    end = brace + 1
    while level:
        level += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


functions = [
    'static bool tokenMatchesTag(', 'static char* nextCsvToken(',
    'static void rejectContract(', 'static bool copyCfgString(',
    'static bool sameContract(', 'static bool parseConfigLine(',
    'static uint32_t fnv1a32(', 'static bool parseSchemaLine(',
    'static bool parseCaptureLine(', 'void parseIncomingLine(',
    'bool metadataReady()',
]
code = r'''
#include "PendulumProtocolReceiver.h"
#include "PendulumSampleState.h"
#include "RecordSerializer.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <vector>
#define F(x) x
constexpr size_t NANO_LINE_MAX=384;
static char parserScratchBuf[NANO_LINE_MAX]={};
static bool parserScratchInUse=false, protocolError=false, cfgContractLogged=false;
struct ParserScratchGuard {
  bool locked=false;
  char* acquire() { if (parserScratchInUse) return nullptr; parserScratchInUse=locked=true; return parserScratchBuf; }
  ~ParserScratchGuard() { if (locked) parserScratchInUse=false; }
};
PendulumSampleState currentSample={};
CanonicalSwingSample latestCanonicalSwing={};
CanonicalPpsSample latestCanonicalPps={};
bool hasCanonicalSwingSample=false, hasCanonicalPpsSample=false;
unsigned long lastCanonicalSwingMs=0, lastCanonicalPpsMs=0, invalidCanonicalPpsLineDrops=0;
unsigned long millis() { return 1000; }
namespace Display { void scrollLog(const char*) {} }
namespace SDLogger { int stops=0; void stopLogging() { ++stops; } }
enum class IngestEventType : uint8_t { CanonicalSwing, CanonicalPps };
struct IngestEvent {
  IngestEventType type;
  union Payload { CanonicalSwingSample swing; CanonicalPpsSample pps; Payload() {} } payload;
};
std::vector<IngestEvent> events;
void queueIngestEvent(const IngestEvent& e) { events.push_back(e); }
void handleStatusLine(const char*) {}
bool metadataReady();
'''
# Dependencies are ordered so each extracted function is compiled exactly as used.
code += '\n'.join(extract(sig) for sig in functions[:-1])
code += '\n' + extract(functions[-1])
code += r'''
int main() {
  static_assert(PROTOCOL_VERSION == 3, "protocol v3");
  static_assert(STS_SCHEMA_VERSION == 5, "STS schema v5");
  assert(countCsvFields(CANONICAL_SWING_SCHEMA)==9);
  assert(countCsvFields(CANONICAL_PPS_SCHEMA)==8);
  const char* cfg="CFG,pv=3,nhz=16000000,cst=CSW,css=canonical_swing_v2,cpt=CPS,cps=canonical_pps_v1,fw=0.0.0-dev";
  parseIncomingLine("CSW,1,0,1,2,3,4,0,0,0");
  assert(events.empty());
  parseIncomingLine("CFG,pv=3,nhz=16000000,cst=CSW,css=canonical_swing_v2,cpt=CPS,cps=canonical_pps_v1");
  assert(!currentSample.session.config_received);
  parseIncomingLine(cfg);
  assert(currentSample.session.config_received && !metadataReady());
  parseIncomingLine("SCH,CSW,canonical_swing_v2,seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing");
  parseIncomingLine("CSW,1,0,1,2,3,4,0,0,0");
  assert(events.empty());
  parseIncomingLine("SCH,CPS,canonical_pps_v1,seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps");
  assert(metadataReady());
  parseIncomingLine("CSW,4294967295,4294967290,2,10,20,30,1,2,3");
  parseIncomingLine("CPS,4294967295,4294967290,2,0,65535,65535,10,2");
  assert(events.size()==2);
  assert(events[0].payload.swing.edge0_tcb0==4294967290u && events[0].payload.swing.drop_swing==3);
  assert(events[1].payload.pps.cap16==65535 && events[1].payload.pps.gps_status==LOCKED);
  char row[256]={}; size_t len=0;
  assert(RecordSerializer::serializeCanonicalSwing(events[0].payload.swing,21,50,1000,row,sizeof(row),len));
  assert(!strcmp(row,"4294967295,4294967290,2,10,20,30,1,2,3,21.00,50.00,1000.00\n"));
  parseIncomingLine("CSW,0,1,2,3,4,5,6,7,8,9");
  parseIncomingLine("CSW,0,1,2,3,4,5,6,7");
  parseIncomingLine("CPS,0,1,4,0,0,0,0,0");
  parseIncomingLine("CPS,0,1,2,0,65536,0,0,0");
  assert(events.size()==2);
  parseIncomingLine(cfg);
  assert(metadataReady() && events.size()==2 && SDLogger::stops==0);
  parseIncomingLine("CFG,pv=3,nhz=16000001,cst=CSW,css=canonical_swing_v2,cpt=CPS,cps=canonical_pps_v1,fw=0.0.0-dev");
  assert(protocolError && !metadataReady() && SDLogger::stops==1);
  parseIncomingLine("CSW,2,0,1,2,3,4,0,0,0");
  assert(events.size()==2);
  currentSample={}; protocolError=false; SDLogger::stops=0; events.clear();
  parseIncomingLine(cfg);
  parseIncomingLine("SCH,CSW,canonical_swing_v2,seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing");
  parseIncomingLine("SCH,CPS,canonical_pps_v1,seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps");
  parseIncomingLine("SCH,CSW,canonical_swing_v2,seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,wrong");
  assert(protocolError && SDLogger::stops==1);
  puts("v3 CFG/SCH gate, nine/eight-field parsing, replay, conflict and CSV passed");
}
'''
with tempfile.TemporaryDirectory(prefix='pendulum-protocol-') as temp:
    cpp = Path(temp) / 'test.cpp'
    cpp.write_text(code)
    exe = Path(temp) / 'test'
    subprocess.run(['c++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                    '-I', str(root / 'Uno.R4.Deprecated/src'), str(cpp),
                    str(root / 'Uno.R4.Deprecated/src/RecordSerializer.cpp'), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
