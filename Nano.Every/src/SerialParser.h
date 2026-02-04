#pragma once

#include <Arduino.h>
#include "PendulumProtocol.h"

constexpr size_t   CSV_LINE_MAX       = 128;     // max CSV line length

// ENABLE_METRICS — serial stats output on USB Serial every METRICS_PERIOD_MS.
#define ENABLE_METRICS    1
constexpr uint32_t METRICS_PERIOD_MS = 5000u;


#ifndef DATA_SERIAL
#define DATA_SERIAL Serial1
#endif

#ifndef DEBUG_SERIAL
#define DEBUG_SERIAL Serial
#endif

#ifndef CMD_SERIAL
#define CMD_SERIAL Serial1
#endif

#if defined(DEBUG_SERIAL)
// Helper that directs debug output to the active port. When the main
// serial port or command port is also the debug port, we avoid flushing to
// keep things moving quickly. If a dedicated debug port is used, each line is
// flushed so it is emitted immediately.
inline Print& debugPort() {
  if (&DEBUG_SERIAL == &DATA_SERIAL) return static_cast<Print&>(DATA_SERIAL);
  if (&DEBUG_SERIAL == &CMD_SERIAL)  return static_cast<Print&>(CMD_SERIAL);
  return static_cast<Print&>(DEBUG_SERIAL);
}

template <typename T>
inline void debugPrint(const T& v) {
  debugPort().print(v);
}

template <typename T, typename U>
inline void debugPrint(const T& v, const U& u) {
  debugPort().print(v, u);
}

inline void debugPrintln() {
  debugPort().println();
  if (&DEBUG_SERIAL != &DATA_SERIAL && &DEBUG_SERIAL != &CMD_SERIAL)
    DEBUG_SERIAL.flush();
}

template <typename T>
inline void debugPrintln(const T& v) {
  debugPort().println(v);
  if (&DEBUG_SERIAL != &DATA_SERIAL && &DEBUG_SERIAL != &CMD_SERIAL)
    DEBUG_SERIAL.flush();
}

template <typename T, typename U>
inline void debugPrintln(const T& v, const U& u) {
  debugPort().println(v, u);
  if (&DEBUG_SERIAL != &DATA_SERIAL && &DEBUG_SERIAL != &CMD_SERIAL)
    DEBUG_SERIAL.flush();
}

#define DBG_PRINT(...)   debugPrint(__VA_ARGS__)
#define DBG_PRINTLN(...) debugPrintln(__VA_ARGS__)
#else
#define DBG_PRINT(...)
#define DBG_PRINTLN(...)
#endif

#if ENABLE_METRICS
extern volatile uint8_t  maxFill;
extern volatile uint32_t csvLineTrunc;
extern volatile uint32_t serialTrunc;
#endif

void processSerialCommands();
void queueCSVLine(const char* buf, int len);
void sendSample(const PendulumSample &s);
void sendStatus(StatusCode code, const char* text);
void reportMetrics();
void printCsvHeader();
void handleHelp(const char* arg1);          // arg1 may be nullptr
bool isHelpCommand(const char* cmd);        // "?" or "help" (case-insensitive)
