#pragma once
#include <stddef.h>
#include <stdint.h>

void latchResetCauseOnceAtBoot();
uint8_t getLatchedResetCause();
uint8_t getLateObservedResetCause();
bool latchedResetCauseUsesEarlyCapture();
bool latchedResetCauseEarlyValid();
bool latchedResetCauseMismatch();
void formatLatchedResetFlags(char* out, size_t outLen);
void formatResetFlagsFromRaw(uint8_t rstfr, char* out, size_t outLen);
bool shouldResetBootSeqFromLatchedCause();
uint16_t advanceBootSequenceForBoot();
void emitResetCause();
void emitResetCauseOncePerBoot();
void emitSetupEntryNoneIfLatchedResetFlagsNone();
void emitStatusBootHeaders();
void emitStatusTunables();
void emitStatusPpsConfig();
void emitStatusSampleConfig();
void emitStatusClockDiagnostics();
void emitEepromLoadStatus();
void emitStatusSerialDiagnostics();
void emitPpsTuningConfigSnapshot();
void setBootSequenceForTelemetry(uint16_t bootSeq);
uint16_t getBootSequenceForTelemetry();
