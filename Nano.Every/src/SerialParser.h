#pragma once

#include <Arduino.h>
#include "Config.h"
#include "PendulumProtocol.h"

// STS/log sizing strategy:
// - Keep CSV_LINE_MAX modest for RAM and serial throughput.
// - Size STS message bodies against CSV_PAYLOAD_MAX so the current status and
//   progress records still fit once the STS prefix and line ending are added.
constexpr size_t   CSV_LINE_MAX       = 256;     // hard cap for a fully emitted CSV line (including STS wrapper)
constexpr size_t   CSV_STS_WRAP_MAX   = 32;      // reserved room for "STS,<code>," + terminator/newline
constexpr size_t   CSV_PAYLOAD_MAX    = CSV_LINE_MAX - CSV_STS_WRAP_MAX;


#ifndef DATA_SERIAL
#define DATA_SERIAL Serial
#endif

#ifndef CMD_SERIAL
#define CMD_SERIAL Serial
#endif

void processSerialCommands();
void queueCSVLine(const char* buf, int len);
void sendTaggedCsvLine(const char* tag, const char* text);
void sendSample(const PendulumSample &s);
void sendStatus(StatusCode code, const char* text);
void printCsvHeader();
void handleHelp(const char* arg1);          // arg1 may be nullptr
