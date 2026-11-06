#pragma once
#include <stdint.h>

// Foreground-only idle-bus guard. Device NACKs are deliberately not an input:
// released lines establish bus health independently of device presence.
namespace I2cRecovery {
enum class Event : uint8_t {
  Detected, Attempt, Recovered, StillFailed, Exhausted, ManualRecovery, Rearmed
};
struct Outcome {
  uint8_t attempt = 0, pulses = 0;
  bool sdaHigh = false, sclHigh = false;
  bool stop = false, clockBlocked = false, linesReleased = false;
};
class State {
 public:
  bool allowsTransactions() const { return allowed_; }
  uint8_t attempts() const { return attempts_; }
  bool exhausted() const { return attempts_ == 2 && !allowed_; }

  // IO only pulls pins LOW or releases them to external pull-ups. Controller
  // restore must restore the configured clock too. All calls are synchronous.
  template<class IO> bool service(uint32_t now, IO& io) {
    if (phase_ == Phase::Exhausted && uint32_t(now - tick_) < 30000)
      return false;
    const bool sda = io.sda(), scl = io.scl();
    const bool high = sda && scl;
    if (phase_ == Phase::Healthy) {
      if (!high) { phase_ = Phase::Suspect; tick_ = now; allowed_ = false; }
      return allowed_;
    }
    if (phase_ == Phase::Suspect) {
      if (high) {
        if (!attempts_) { phase_ = Phase::Healthy; allowed_ = true; }
        else startVerification(now, true);
        return allowed_;
      }
      if (uint32_t(now - tick_) < 1) return false;
      Outcome o = levels(sda, scl); io.report(Event::Detected, o);
      if (attempts_ == 2) { exhaust(now, io, o); return false; }
      phase_ = Phase::Waiting;
      // First attempt immediately; subsequent attempts honor original spacing.
    }
    if (phase_ == Phase::Waiting) {
      if (high) { startVerification(now, false); return false; }
      if (attempts_ && uint32_t(now - attemptMs_) < 1000) return false;
      ++attempts_; attemptMs_ = now;
      Outcome o = recover(io); o.attempt = attempts_;
      io.report(Event::Attempt, o);
      if (o.linesReleased) {
        startVerification(now, true); io.report(Event::Recovered, o);
      } else if (attempts_ == 2) exhaust(now, io, o);
      else io.report(Event::StillFailed, o);
      return allowed_;
    }
    if (phase_ == Phase::Exhausted) {
      tick_ = now;
      if (high) startVerification(now, false);
      return false;
    }
    // Three released-line observations spaced at least one second apart;
    // any low restarts failure handling without resetting the attempt budget.
    if (phase_ == Phase::Verifying) {
      if (!high) {
        allowed_ = false;
        if (attempts_ == 2) {
          Outcome o = levels(sda, scl); exhaust(now, io, o);
        }
        else { phase_ = Phase::Suspect; tick_ = now; }
        return false;
      }
      if (uint32_t(now - tick_) >= 1000) {
        tick_ = now;
        if (++goodChecks_ >= 3) {
          Outcome o = levels(sda, scl);
          io.report(allowed_ ? Event::Rearmed : Event::ManualRecovery, o);
          attempts_ = 0; phase_ = Phase::Healthy; allowed_ = true;
        }
      }
    }
    return allowed_;
  }
 private:
  enum class Phase : uint8_t { Healthy, Suspect, Waiting, Exhausted, Verifying };
  Phase phase_ = Phase::Healthy;
  uint32_t tick_ = 0, attemptMs_ = 0;
  uint8_t attempts_ = 0, goodChecks_ = 0;
  bool allowed_ = true;
  Outcome levels(bool sda, bool scl) const {
    Outcome o; o.attempt = attempts_; o.sdaHigh = sda; o.sclHigh = scl;
    o.linesReleased = sda && scl; return o;
  }
  void startVerification(uint32_t now, bool allow) {
    phase_ = Phase::Verifying; tick_ = now; goodChecks_ = 1; allowed_ = allow;
  }
  template<class IO> void exhaust(uint32_t now, IO& io, const Outcome& o) {
    phase_ = Phase::Exhausted; tick_ = now; allowed_ = false;
    io.report(Event::Exhausted, o);
  }
  template<class IO> static bool clockHigh(IO& io) {
    for (uint8_t i = 0; i < 20; ++i) {
      if (io.scl()) return true;
      io.delayUs(5);
    }
    return io.scl();
  }
  template<class IO> Outcome recover(IO& io) {
    Outcome o;
    io.releaseController(); io.releaseSda(); io.releaseScl();
    if (!clockHigh(io)) o.clockBlocked = true;
    else {
      while (!io.sda() && o.pulses < 9) {
        io.lowScl(); io.delayUs(5); io.releaseScl();
        if (!clockHigh(io)) { o.clockBlocked = true; break; }
        ++o.pulses; io.delayUs(5);
      }
      if (!o.clockBlocked && io.sda()) {
        // A slave still holding SDA LOW cannot accept STOP. Do not add a
        // tenth recovery clock trying to force it; retain the failed episode.
        // Once SDA is free, the following SCL edge only prepares STOP.
        // SDA falls while SCL is LOW, then rises with SCL HIGH: a STOP.
        io.lowScl(); io.lowSda(); io.delayUs(5); io.releaseScl();
        if (clockHigh(io)) {
          io.delayUs(5); io.releaseSda(); io.delayUs(5);
          o.stop = io.scl() && io.sda();
        } else o.clockBlocked = true;
      }
    }
    io.releaseSda(); io.releaseScl();
    io.restoreController(); io.delayUs(5);
    o.sdaHigh = io.sda(); o.sclHigh = io.scl();
    o.linesReleased = o.sdaHigh && o.sclHigh;
    return o;
  }
};
} // namespace I2cRecovery
