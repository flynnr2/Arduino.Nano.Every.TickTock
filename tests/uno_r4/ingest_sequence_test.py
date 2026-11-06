#!/usr/bin/env python3
"""Run production sequence accounting with deterministic logging/clock fakes."""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[2]
source = (root/'Uno.R4.Deprecated/src/IngestOrchestrator.cpp').read_text()
start = source.index('struct SeqTracker')
end = source.index('\n}\n\nstatic void attachLatestEnvironment', start)
code = r'''
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cassert>
#include <string>
#define F(x) x
uint32_t clockMs=0; uint32_t millis() { return clockMs; }
std::string lastCategory,lastEvent;
namespace SDLogger { void logUnoEvent(const char* c, const char* e) { lastCategory=c;lastEvent=e; } }
namespace NanoComm { int refresh=0; bool requestEmitMeta() { ++refresh;return true; } }
namespace WiFiConfig { int state(){return 0;} const char* stateName(int){return "STAConnected";} bool networkReadyForHttp(){return true;} }
namespace DiagLog { enum class Severity{Warn}; enum class MessageId{IngestSeqGap}; void emitCooldown(Severity, MessageId, const char*, unsigned long) {} }
''' + source[start:end] + r'''
int main() {
  noteSeqGap("PCPS",pcpsSeq,63097);
  assert(lastEvent.find("joined")!=std::string::npos && pcpsMissing==0);
  noteSeqGap("PCPS",pcpsSeq,63113);
  assert(pcpsGapCount==1 && pcpsMissing==15);
  noteSeqGap("PCSW",pcswSeq,31574); noteSeqGap("PCSW",pcswSeq,31582);
  assert(pcswGapCount==1 && pcswMissing==7);
  noteSeqGap("PCPS",pcpsSeq,1); // Independent Nano restart.
  assert(pcpsMissing==15 && NanoComm::refresh==1);
  assert(lastEvent.find("restart_or_reorder")!=std::string::npos);
  noteSeqGap("PCPS",pcpsSeq,1);
  assert(lastEvent.find("duplicate")!=std::string::npos && pcpsMissing==15);
  pcpsSeq.last=0xffffffffu; noteSeqGap("PCPS",pcpsSeq,0);
  assert(pcpsMissing==15 && NanoComm::refresh==1);
  pcpsSeq.last=0xfffffffeu; noteSeqGap("PCPS",pcpsSeq,1);
  assert(pcpsMissing==17 && pcpsGapCount==2);
  puts("independent startup, sequence gaps, restart, duplicate and wrap tests passed");
}
'''
with tempfile.TemporaryDirectory(prefix='pendulum-sequence-') as d:
    cpp=Path(d)/'test.cpp';cpp.write_text(code)
    exe=Path(d)/'test'
    subprocess.run(['c++','-std=c++11','-Wall','-Wextra','-Werror',str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
