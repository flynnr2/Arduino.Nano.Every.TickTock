#include "TunableCommands.h"

#include "Config.h"
#include "EEPROMConfig.h"
#include "PendulumCommands.h"
#include "PendulumProtocol.h"
#include "PendulumCore.h"
#include "SerialParser.h"
#include "StatusTelemetry.h"
#include "StringCase.h"
#include "TunableRegistry.h"

#include <Arduino.h>

#ifndef FPSTR
  #define FPSTR(p) (reinterpret_cast<const __FlashStringHelper*>(p))
#endif

namespace {

const char kMsgGetPrefix[] PROGMEM = "get: ";
const char kMsgSetPrefix[] PROGMEM = "set: ";

class FixedBufferPrint : public Print {
 public:
  FixedBufferPrint(char* buffer, size_t capacity)
      : buffer_(buffer), capacity_(capacity), length_(0), truncated_(false) {
    if (buffer_ && capacity_ > 0) {
      buffer_[0] = '\0';
    }
  }

  size_t write(uint8_t c) override {
    if (!buffer_ || capacity_ == 0) return 0;
    if ((length_ + 1U) >= capacity_) {
      truncated_ = true;
      return 0;
    }
    buffer_[length_++] = (char)c;
    buffer_[length_] = '\0';
    return 1;
  }

  bool truncated() const { return truncated_; }

 private:
  char* buffer_;
  size_t capacity_;
  size_t length_;
  bool truncated_;
};

void printUnknownParameterError() {
  CMD_SERIAL.println(F("ERROR: unknown parameter"));
  sendStatus(StatusCode::InvalidParam, "unknown parameter");
}

void emitInternalFormatError(const __FlashStringHelper* detail, const char* statusDetail) {
  CMD_SERIAL.print(F("ERROR: "));
  CMD_SERIAL.println(detail);
  sendStatus(StatusCode::InternalError, statusDetail);
}

void printProgmemText(const char* text_P) {
  CMD_SERIAL.print(FPSTR(text_P));
}

void emitTunableCommandAck(const char* action,
                          const TunableDescriptor* descriptor = nullptr) {
  if (!action) return;

  char* line = tryAcquireFormatBuffer(FormatBufferOwner::SerialParser);
  if (!line) {
    emitInternalFormatError(F("tunable ack buffer unavailable"), "tunable ack buffer unavailable");
    return;
  }
  if (!descriptor) {
    const int n = snprintf(line, CSV_PAYLOAD_MAX, "%s,%s", action, CMD_RESET_DEFAULTS);
    if (n > 0 && n < (int)CSV_PAYLOAD_MAX) {
      sendStatusFromOwnedBuffer(FormatBufferOwner::SerialParser, StatusCode::Ok, line);
    } else if (n >= (int)CSV_PAYLOAD_MAX) {
      emitInternalFormatError(F("tunable ack truncated (reset)"), "tunable ack truncated (reset)");
    }
    releaseFormatBuffer(FormatBufferOwner::SerialParser);
    return;
  }

  char valueBuf[24];
  FixedBufferPrint valueOut(valueBuf, sizeof(valueBuf));
  descriptor->printCurrent(valueOut);
  if (valueOut.truncated()) {
    releaseFormatBuffer(FormatBufferOwner::SerialParser);
    emitInternalFormatError(F("tunable value truncated"), "tunable value truncated");
    return;
  }

  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "%s,%s,%s",
                         action,
                         descriptor->cliName,
                         valueBuf);
  if (n > 0 && n < (int)CSV_PAYLOAD_MAX) {
    sendStatusFromOwnedBuffer(FormatBufferOwner::SerialParser, StatusCode::Ok, line);
  } else if (n >= (int)CSV_PAYLOAD_MAX) {
    emitInternalFormatError(F("tunable ack truncated"), "tunable ack truncated");
  }
  releaseFormatBuffer(FormatBufferOwner::SerialParser);
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

  printProgmemText(kMsgGetPrefix);
  CMD_SERIAL.print(descriptor->cliName);
  CMD_SERIAL.print(F(" = "));
  descriptor->printCurrent(CMD_SERIAL);
  CMD_SERIAL.println();
  emitTunableCommandAck(STS_TUNABLES_GET, descriptor);
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
  printProgmemText(kMsgSetPrefix);
  CMD_SERIAL.print(descriptor->cliName);
  CMD_SERIAL.print(F(" = "));
  descriptor->printCurrent(CMD_SERIAL);
  CMD_SERIAL.println();
  saveConfig(getCurrentConfig());
  emitTunableCommandAck(STS_TUNABLES_SET, descriptor);
}

void handleResetCommand(char* action, bool& headerPending) {
  if (!action) {
    CMD_SERIAL.println(F("ERROR: reset requires <action>"));
    sendStatus(StatusCode::InvalidParam, "reset requires <action>");
    return;
  }

  if (!equalsIgnoreCaseAscii(action, CMD_RESET_DEFAULTS)) {
    CMD_SERIAL.println(F("ERROR: reset supports only 'defaults'"));
    sendStatus(StatusCode::InvalidParam, "reset supports only defaults");
    return;
  }

  Tunables::restoreDefaults();
  saveConfig(getCurrentConfig());
  resetRuntimeStateAfterTunablesChange();
  emitTunableCommandAck(STS_TUNABLES_RESET);
  emitStatusTunables();
  emitStatusSampleConfig();
  emitStatusPpsConfig();
#if PPS_TUNING_TELEMETRY
  emitPpsTuningConfigSnapshot();
#endif
  printCsvHeader();
  headerPending = false;
  CMD_SERIAL.println(F("reset: defaults restored from firmware and saved to EEPROM"));
}
