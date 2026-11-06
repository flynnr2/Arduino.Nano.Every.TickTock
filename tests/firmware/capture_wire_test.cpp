// Production formatters with only transport and format-buffer ownership replaced.
#include <cassert>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>
#include "SerialParser.h"
#include "ProgmemCompat.h"

static char buffer[CSV_LINE_MAX];
static std::vector<std::string> lines;
static constexpr uint8_t EEPROM_CONFIG_VERSION_CURRENT = 1;
char* tryAcquireFormatBuffer(FormatBufferOwner) { return buffer; }
char* tryAcquireFormatBufferInternal(FormatBufferOwner, EmissionReliability) { return buffer; }
void releaseFormatBuffer(FormatBufferOwner) {}
char* prepareStatusLineBuf() { return buffer; }
void releaseStatusLineBuf() {}
bool queueCSVLine(const char* line, int size, EmissionReliability) {
  assert(size > 0 && size < int(CSV_LINE_MAX));
  assert(line[size - 1] == '\n');
  lines.emplace_back(line, size);
  return true;
}
void sendStatusFromOwnedBuffer(FormatBufferOwner, StatusCode code, char* text,
                               EmissionReliability) {
  lines.emplace_back(std::string("STS,") + statusCodeToStr(code) + "," + text + "\n");
}

#include "capture_wire_under_test.inc"

int main() {
  emitSchemaHeader();
  emitStatusSampleConfig();
  printCsvHeader();
  CanonicalSwingSample swing{};
  swing.seq = UINT32_MAX;
  swing.edge0_tcb0 = UINT32_MAX - 20;
  swing.edge1_tcb0 = UINT32_MAX - 10;
  swing.edge2_tcb0 = 0;
  swing.edge3_tcb0 = 10;
  swing.edge4_tcb0 = 20;
  swing.drop_ir = UINT32_MAX;
  swing.drop_pps = UINT32_MAX;
  swing.drop_swing = UINT32_MAX;
  assert(sendCanonicalSwingSample(swing));
  CanonicalPpsSample pps{};
  pps.seq = UINT32_MAX;
  pps.edge_tcb0 = UINT32_MAX - 100;
  pps.gps_status = HOLDOVER;
  pps.holdover_age_ms = UINT32_MAX;
  pps.cap16 = UINT16_MAX;
  pps.latency16 = UINT16_MAX;
  pps.now32 = UINT32_MAX;
  pps.drop_pps = UINT32_MAX;
  assert(sendCanonicalPpsSample(pps));
  for (const auto& line : lines) std::cout << line;
}
