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
static_assert(CSV_LINE_MAX > HDR_LINE_MIN_ENCODED_LEN,
              "CSV_LINE_MAX must fit the full encoded HDR line plus NUL terminator");
static_assert(CSV_STS_WRAP_MAX >= CSV_STS_WRAP_WORST_CASE,
              "CSV_STS_WRAP_MAX is too small for worst-case STS wrapper length");


#ifndef DATA_SERIAL
#define DATA_SERIAL Serial1
#endif

#ifndef CMD_SERIAL
#define CMD_SERIAL Serial1
#endif

void processSerialCommands();
void queueCSVLine(const char* buf, int len);
void sendTaggedCsvLine(const char* tag, const char* text);
void sendSample(const PendulumSample &s);
void sendStatus(StatusCode code, const char* text);
void printCsvHeader();
void handleHelp(const char* arg1);          // arg1 may be nullptr
