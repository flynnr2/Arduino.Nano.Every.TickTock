#include "TunableCommands.h"

#include "Config.h"
#include "EEPROMConfig.h"
#include "PendulumProtocol.h"
#include "SerialParser.h"
#include "StatusTelemetry.h"
#include "TunableRegistry.h"

#include <Arduino.h>

namespace {

void printUnknownParameterError() {
  CMD_SERIAL.println(F("ERROR: unknown parameter"));
  sendStatus(StatusCode::InvalidParam, "unknown parameter");
}

} // namespace

void handleGetCommand(char* name) {
  if (!name) {
    CMD_SERIAL.println(F("ERROR: get requires <param>"));
    sendStatus(StatusCode::InvalidParam, "get requires <param>");
    return;
  }

  const TunableDescriptor* descriptor = findTunableDescriptor(name);
  if (!descriptor) {
    printUnknownParameterError();
    return;
  }

  CMD_SERIAL.print("get: ");
  CMD_SERIAL.print(descriptor->cliName);
  CMD_SERIAL.print(F(" = "));
  descriptor->printCurrent(CMD_SERIAL);
  CMD_SERIAL.println();
}

void handleSetCommand(char* name, char* val, bool& headerPending) {
  if (!name || !val) {
    CMD_SERIAL.println(F("ERROR: set requires <param> and <value>"));
    sendStatus(StatusCode::InvalidParam, "set requires <param> and <value>");
    return;
  }

  const TunableDescriptor* descriptor = findTunableDescriptor(name);
  if (!descriptor) {
    printUnknownParameterError();
    return;
  }

  bool tuningChanged = false;
  if (!descriptor->parseAndSet(val, tuningChanged, headerPending)) {
    return;
  }

  Tunables::normalizePpsTunables();
#if PPS_TUNING_TELEMETRY
  if (tuningChanged) emitPpsTuningConfigSnapshot();
#endif
  CMD_SERIAL.print("set: ");
  CMD_SERIAL.print(descriptor->cliName);
  CMD_SERIAL.print(F(" = "));
  descriptor->printCurrent(CMD_SERIAL);
  CMD_SERIAL.println();
  saveConfig(getCurrentConfig());
}
