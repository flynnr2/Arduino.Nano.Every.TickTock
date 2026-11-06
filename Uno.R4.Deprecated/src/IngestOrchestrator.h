#pragma once
#include <stdint.h>

namespace IngestOrchestrator {

void service();
uint32_t pcpsSeqGapCount();
uint32_t pcswSeqGapCount();
uint32_t pcpsMissingTotal();
uint32_t pcswMissingTotal();
uint32_t lastPcpsSeq();
uint32_t lastPcswSeq();
bool hadRecentGap(uint32_t withinMs);

} // namespace IngestOrchestrator
