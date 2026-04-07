#pragma once

#include <Arduino.h>
#include "Config.h"
#include "PendulumProtocol.h"

// STS/log sizing strategy:
// - Keep CSV_LINE_MAX modest for RAM and serial throughput.
// - Size STS message bodies against CSV_PAYLOAD_MAX so the current status and
//   progress records still fit once the STS prefix and line ending are added.
constexpr size_t   CSV_LINE_MAX       = 384;     // hard cap for a fully emitted CSV line (including STS wrapper)
constexpr size_t   CSV_STS_WRAP_MAX   = 32;      // reserved room for "STS,<code>," + terminator/newline
constexpr size_t   CSV_PAYLOAD_MAX    = CSV_LINE_MAX - CSV_STS_WRAP_MAX;
constexpr size_t   CSV_STS_STATUS_TOKEN_MAX = 15;  // longest statusCodeToStr(...) token (e.g. "UNKNOWN_COMMAND")
constexpr size_t   CSV_STS_WRAP_WORST_CASE =
    (sizeof(TAG_STS) - 1) +     // "STS"
    1 +                         // comma between tag and status code
    CSV_STS_STATUS_TOKEN_MAX +  // status code token
    1 +                         // comma before message text payload
    1 +                         // trailing '\n' from sendTaggedCsvLine(...)
    1;                          // terminating '\0' in line buffer
// printCsvHeader() emits TAG_HDR_PART lines.
static_assert(CSV_LINE_MAX > HDR_PART_LINE_MAX_ENCODED_LEN,
              "CSV_LINE_MAX must fit the full encoded HDR_PART line plus NUL terminator");
static_assert(CSV_STS_WRAP_MAX >= CSV_STS_WRAP_WORST_CASE,
              "CSV_STS_WRAP_MAX is too small for worst-case STS wrapper length");
static_assert(CSV_PAYLOAD_MAX > 0, "CSV_PAYLOAD_MAX must leave room for STS wrapper");


#ifndef DATA_SERIAL
#define DATA_SERIAL Serial1
#endif

#ifndef CMD_SERIAL
#define CMD_SERIAL Serial1
#endif

enum class FormatBufferOwner : uint8_t {
  SerialParser = 1,
  StatusTelemetry = 2,
  PendulumCore = 3,
  MemoryTelemetry = 4,
};

enum class EmissionReliability : uint8_t {
  BestEffort = 0,
  Required = 1,
};

char* tryAcquireFormatBuffer(FormatBufferOwner owner);
void releaseFormatBuffer(FormatBufferOwner owner);
void sendStatusFromOwnedBuffer(FormatBufferOwner owner, StatusCode code, char* textBuf,
                               EmissionReliability reliability = EmissionReliability::BestEffort);

void processSerialCommands();
// queueCSVLine() is foreground-only (main loop/task context) and is not ISR-safe.
// Reentry is only supported within foreground callers; ISR callers must defer work.
bool queueCSVLine(const char* buf, int len,
                  EmissionReliability reliability = EmissionReliability::BestEffort);
void sendTaggedCsvLine(const char* tag, const char* text);
void sendSample(const PendulumSample &s);
void sendCanonicalSwingSample(const CanonicalSwingSample& s);
void sendCanonicalPpsSample(const CanonicalPpsSample& s);
void sendStatus(StatusCode code, const char* text,
                EmissionReliability reliability = EmissionReliability::BestEffort);
void printCsvHeader();
void handleHelp(const char* arg1);          // arg1 may be nullptr

// Lightweight serial/formatter diagnostics (atomic-safe uint32_t snapshots).
uint32_t serialFormatAcquireFailures();
uint32_t serialRequiredFormatAcquireFailures();
uint32_t serialQueueRejectsInvalidArgs();
uint32_t serialTxReentryDrops();
uint32_t serialRequiredDrops();
