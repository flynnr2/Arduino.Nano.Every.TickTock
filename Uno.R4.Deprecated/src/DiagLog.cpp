#include "DiagLog.h"
#include "Display.h"

namespace DiagLog {

struct CooldownEntry {
  unsigned long lastEmittedMs = 0;
  bool seen = false;
};

static CooldownEntry g_cooldowns[(size_t)MessageId::Count];

static bool shouldEmit(MessageId id, unsigned long cooldownMs) {
  const size_t index = static_cast<size_t>(id);
  if (index >= (size_t)MessageId::Count) return true;
  CooldownEntry &entry = g_cooldowns[index];
  const unsigned long now = millis();
  if (!entry.seen) {
    entry.seen = true;
    entry.lastEmittedMs = now;
    return true;
  }
  if ((unsigned long)(now - entry.lastEmittedMs) >= cooldownMs) {
    entry.lastEmittedMs = now;
    return true;
  }
  return false;
}

void emit(Severity severity, const char* msg) {
  if (!severityEnabled(severity)) return;
  Display::scrollLog(msg);
}

void emit(Severity severity, const __FlashStringHelper* msg) {
  if (!severityEnabled(severity)) return;
  Display::scrollLog(msg);
}

bool emitCooldown(Severity severity, MessageId id, const char* msg, unsigned long cooldownMs) {
  if (!severityEnabled(severity)) return false;
  if (!shouldEmit(id, cooldownMs)) return false;
  emit(severity, msg);
  return true;
}

bool emitCooldown(Severity severity, MessageId id, const __FlashStringHelper* msg, unsigned long cooldownMs) {
  if (!severityEnabled(severity)) return false;
  if (!shouldEmit(id, cooldownMs)) return false;
  emit(severity, msg);
  return true;
}

void resetCooldown(MessageId id) {
  const size_t index = static_cast<size_t>(id);
  if (index >= (size_t)MessageId::Count) return;
  g_cooldowns[index] = {};
}

void resetAllCooldowns() {
  for (size_t i = 0; i < (size_t)MessageId::Count; ++i) {
    g_cooldowns[i] = {};
  }
}

} // namespace DiagLog
