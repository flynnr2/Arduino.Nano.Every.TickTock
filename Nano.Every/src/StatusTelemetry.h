#pragma once
#include <stddef.h>

void latchResetCauseOnceAtBoot();
uint8_t getLatchedResetCause();
void formatLatchedResetFlags(char* out, size_t outLen);
bool shouldResetBootSeqFromLatchedCause();
uint16_t advanceBootSequenceForBoot();
void emitResetCause();
void emitResetCauseOncePerBoot();
void emitStatusBootHeaders();
void emitStatusTunables();
void emitStatusPpsConfig();
void emitStatusSampleConfig();
void emitStatusClockDiagnostics();
void emitStatusSerialDiagnostics();
void emitPpsTuningConfigSnapshot();
void setBootSequenceForTelemetry(uint16_t bootSeq);
uint16_t getBootSequenceForTelemetry();
