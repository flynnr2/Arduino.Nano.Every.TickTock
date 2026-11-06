#pragma once
#include <stdint.h>

// Uses an elapsed cycle anchor so millis rollover and delayed draws preserve
// phase without loops. Rotation is purely presentation; it never samples data.
class OledScreenRotation {
 public:
  bool update(uint32_t now) {
    if (!started_) { started_ = true; cycleStartMs_ = now; }
    uint32_t elapsed = now - cycleStartMs_;
    cycleStartMs_ += (elapsed / CYCLE_MS) * CYCLE_MS;
    const uint32_t phase = elapsed % CYCLE_MS;
    const uint8_t next = phase < RATING_MS ? 0 : phase < RATING_MS + STATUS_MS ? 1 : 2;
    const bool changed = next != screen_;
    screen_ = next;
    return changed;
  }
  bool supplemental() const { return screen_ == 1; }
  bool timebase() const { return screen_ == 2; }
  static constexpr uint32_t RATING_MS = 30000;
  static constexpr uint32_t STATUS_MS = 15000;
  static constexpr uint32_t TIMEBASE_MS = 15000;
  static constexpr uint32_t CYCLE_MS = RATING_MS + STATUS_MS + TIMEBASE_MS;
 private:
  uint32_t cycleStartMs_ = 0;
  bool started_ = false;
  uint8_t screen_ = 0;
};
