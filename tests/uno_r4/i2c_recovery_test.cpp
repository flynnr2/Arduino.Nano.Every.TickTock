#include "../../Uno.R4.Deprecated/src/I2cRecoveryState.h"
#include <cassert>
#include <cstdio>
#include <vector>
using namespace I2cRecovery;
struct Fake {
  bool heldSda = false, heldScl = false, driveSda = false, driveScl = false;
  unsigned releases = 0, restores = 0, pulses = 0, delay = 0, reads = 0;
  unsigned clockLowCalls = 0;
  unsigned releaseOnAttempt = 1, releaseOnPulse = 3;
  bool holdClockOnRestore = false;
  std::vector<Event> events;
  std::vector<Outcome> outcomes;
  bool sda() { ++reads; return !heldSda && !driveSda; }
  bool scl() { ++reads; return !heldScl && !driveScl; }
  void releaseController() { ++releases; pulses = 0; }
  void restoreController() { ++restores; if (holdClockOnRestore) heldScl = true; }
  void releaseSda() { driveSda = false; }
  void releaseScl() {
    if (driveScl && !driveSda && !heldScl) {
      ++pulses;
      if (releases >= releaseOnAttempt && pulses >= releaseOnPulse) heldSda = false;
    }
    driveScl = false;
  }
  void lowSda() { driveSda = true; }
  void lowScl() { ++clockLowCalls; driveScl = true; }
  void delayUs(uint32_t n) { delay += n; }
  void report(Event e, const Outcome& o) { events.push_back(e); outcomes.push_back(o); }
  unsigned count(Event e) const {
    unsigned n = 0; for (Event recorded : events) if (recorded == e) ++n; return n;
  }
  Outcome attempt() const {
    for (unsigned i = events.size(); i; --i)
      if (events[i-1] == Event::Attempt) return outcomes[i-1];
    assert(false); return Outcome();
  }
};
int main() {
  static_assert(sizeof(State) <= 20, "Fixed, small per-bus state");
  {
    State state; Fake io; io.heldSda = true;
    assert(!state.service(0, io)); assert(io.releases == 0);
    assert(state.service(1, io)); assert(io.releases == 1 && io.restores == 1);
    assert(io.attempt().pulses == 3 && io.attempt().stop);
    assert(io.attempt().linesReleased && !io.attempt().clockBlocked);
    assert(state.attempts() == 1);
    state.service(1000, io); assert(state.attempts() == 1);
    state.service(1001, io); state.service(2001, io);
    assert(state.attempts() == 0 && io.count(Event::Rearmed) == 1);
    io.heldSda = true; state.service(2002, io); state.service(2003, io);
    assert(io.releases == 2 && state.attempts() == 1); // New episode.
  }
  {
    State state; Fake io; io.heldSda = true; io.releaseOnAttempt = 2;
    state.service(0, io); assert(!state.service(1, io));
    assert(io.attempt().pulses == 9 && !io.attempt().stop);
    assert(io.clockLowCalls == 9); // No tenth clock for impossible STOP.
    for (unsigned t = 2; t < 1001; ++t) assert(!state.service(t, io));
    assert(io.releases == 1); assert(state.service(1001, io));
    assert(io.releases == 2 && io.attempt().attempt == 2);
    state.service(2001, io); state.service(3001, io); assert(!state.attempts());
  }
  {
    State state; Fake io; io.heldSda = true; io.releaseOnAttempt = 99;
    state.service(0, io); state.service(1, io); state.service(1001, io);
    assert(state.exhausted());
    for (unsigned t = 1002; t < 90000; ++t) assert(!state.service(t, io));
    assert(io.releases == 2 && io.count(Event::Exhausted) == 1);
    unsigned reads = io.reads; state.service(90001, io); assert(reads == io.reads);
    io.heldSda = false; assert(!state.service(91001, io));
    assert(!state.service(92001, io)); assert(state.service(93001, io));
    assert(!state.attempts() && io.count(Event::ManualRecovery) == 1);
    io.heldSda = true; state.service(93002, io); state.service(93003, io);
    assert(io.releases == 3); // Stable manual release re-arms budget.
  }
  {
    State state; Fake io; io.heldScl = true;
    state.service(0, io); assert(!state.service(1, io));
    assert(io.attempt().clockBlocked && io.attempt().pulses == 0);
    assert(!io.attempt().stop && io.delay <= 110);
    state.service(1001, io); assert(state.exhausted() && io.delay <= 220);
  }
  {
    State state; Fake io; io.heldSda = true; io.holdClockOnRestore = true;
    state.service(0, io); assert(!state.service(1, io));
    assert(io.attempt().stop && !io.attempt().linesReleased);
    assert(!io.attempt().sclHigh && io.count(Event::Recovered) == 0);
    assert(io.restores == 1); // STOP alone is not restored-controller verification.
  }
  {
    // Device ACK/NACK is intentionally absent from this bus-health interface.
    State state; Fake io;
    for (unsigned t = 0; t < 100000; ++t) assert(state.service(t, io));
    assert(io.events.empty() && io.releases == 0);
    io.heldSda = true; assert(!state.service(100000, io));
    io.heldSda = false; assert(state.service(100001, io));
    assert(io.events.empty()); // A transient idle sample does not trigger recovery.
  }
  {
    State wire, wire1; Fake oled, sensors; sensors.heldSda = true;
    sensors.releaseOnAttempt = 99;
    for (unsigned t = 0; t < 100000; ++t) {
      assert(wire.service(t, oled)); wire1.service(t, sensors);
    }
    assert(sensors.releases == 2 && oled.releases == 0 && oled.events.empty());
  }
  {
    State state; Fake io; io.heldSda = true;
    state.service(0, io); assert(state.service(1, io));
    io.heldSda = true; state.service(2, io); state.service(3, io);
    assert(io.releases == 1); assert(state.service(1001, io));
    io.heldSda = true; assert(!state.service(1002, io));
    for (unsigned t = 1003; t < 100000; ++t) state.service(t, io);
    assert(io.releases == 2 && state.exhausted()); // Flapping never re-arms.
  }
  {
    State state; Fake io; io.heldSda = true; io.releaseOnAttempt = 2;
    state.service(0xfffffff0u, io); state.service(0xfffffff1u, io);
    assert(!state.service(500, io)); assert(state.service(1000, io));
  }
  puts("I2C recovery: success, retry, exhaustion, clock-low, NACK, isolation, stable re-arm and wrap passed");
}
