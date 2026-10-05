#pragma once

#include <Arduino.h>
#include "Config.h"

namespace DiagLog {

enum class Severity : uint8_t {
  Error = 0,
  Warn,
  Info,
  Trace
};

enum class MessageId : uint8_t {
  NanoHdrRecoveryPausedRefresh = 0,
  NanoHdrRecoveryRetry,
  NanoHdrPartIgnoredMode,
  NanoHdrPartRestart,
  NanoHdrPartReassemblyFailed,
  NanoStartupEmitRequest,
  NanoStartupSmpWaitHeaderLock,
  NanoStartupTimeoutPartial,
  NanoRecoveryBeginImmediateEmitMeta,
  NanoHdrPartTimeoutWaitLock,
  WiFiRecoveryAttempt,
  WiFiRetryHold,
  IngestSeqGap,
  Count
};

constexpr bool severityEnabled(Severity severity) {
  switch (severity) {
    case Severity::Error:
    case Severity::Warn:
      return true;
    case Severity::Info:
      return ENABLE_DIAG_INFO != 0;
    case Severity::Trace:
      return (ENABLE_DIAG_PROTOCOL_VERBOSE != 0) || (ENABLE_DIAG_RECOVERY_TRACE != 0);
    default:
      return true;
  }
}
// Enablement model:
// - ERROR and WARN are always enabled at compile-time and runtime.
// - INFO is compile-time gated by ENABLE_DIAG_INFO.
// - TRACE is compile-time gated by ENABLE_DIAG_PROTOCOL_VERBOSE or ENABLE_DIAG_RECOVERY_TRACE.
// Runtime behavior:
// - emit*() checks severityEnabled() before routing to Display::scrollLog().
// - emitCooldown*() applies per-MessageId suppression only after severity gate passes.

void emit(Severity severity, const char* msg);
void emit(Severity severity, const __FlashStringHelper* msg);

bool emitCooldown(Severity severity, MessageId id, const char* msg, unsigned long cooldownMs);
bool emitCooldown(Severity severity, MessageId id, const __FlashStringHelper* msg, unsigned long cooldownMs);

void resetCooldown(MessageId id);
void resetAllCooldowns();

} // namespace DiagLog
