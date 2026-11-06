#include "../../Uno.R4.Deprecated/src/CanonicalTiming.h"
#include <cassert>
#include <cstdio>
#include <initializer_list>
#include <cmath>
#include <cstring>

int main() {
  CanonicalTiming::Intervals result = {};
  CanonicalSwingSample swing = {};
  swing.edge0_tcb0 = 0xffff0000u;
  swing.edge1_tcb0 = swing.edge0_tcb0 + 490000;
  swing.edge2_tcb0 = swing.edge1_tcb0 + 10100;
  swing.edge3_tcb0 = swing.edge2_tcb0 + 489900;
  swing.edge4_tcb0 = swing.edge3_tcb0 + 10000;
  assert(CanonicalTiming::intervals(swing, result));
  assert(result.period == 1000000 && result.beatDelta == 100 && result.blockDelta == 100);
  swing.edge2_tcb0 = swing.edge1_tcb0;
  assert(!CanonicalTiming::intervals(swing, result));
  swing.edge2_tcb0 = swing.edge1_tcb0 - 1;
  assert(!CanonicalTiming::intervals(swing, result));

  CanonicalTiming::PpsClock clock;
  CanonicalPpsSample pps = {};
  pps.seq = 0xffffffffu;
  pps.edge_tcb0 = 0xffff0000u;
  pps.gps_status = LOCKED;
  assert(!clock.observe(pps, 1000000, 1000));
  assert(clock.correctedHz(1000000, 1000) == 0);
  ++pps.seq;
  pps.edge_tcb0 += 1000100;
  assert(!clock.observe(pps, 1000000, 2000));
  assert(clock.correctedHz(1000000, 2000) == 1000100);
  assert(clock.correctedHz(2000000, 2000) == 0);
  clock.observe(pps, 1000000, 6000); // Duplicate must not refresh freshness.
  assert(clock.correctedHz(1000000, 6999) == 1000100);
  assert(clock.correctedHz(1000000, 7000) == 0);
  pps.seq += 2;
  pps.edge_tcb0 += 2000200;
  clock.observe(pps, 1000000, 8000);
  assert(clock.correctedHz(1000000, 8000) == 0);
  ++pps.seq;
  pps.edge_tcb0 += 1000100;
  clock.observe(pps, 1000000, 9000);
  assert(clock.correctedHz(1000000, 9000) == 1000100);
  ++pps.seq;
  pps.edge_tcb0 += 500; // Glitch, not a second.
  clock.observe(pps, 1000000, 10000);
  assert(clock.correctedHz(1000000, 10000) == 1000100); // Short rejection retains scale.
  assert(!strcmp(clock.status(10000), "REJECTED"));
  pps.seq = 1;
  assert(clock.observe(pps, 1000000, 11000));
  assert(clock.correctedHz(1000000, 11000) == 0);
  for (GpsStatus status : {NO_PPS, HOLDOVER, ACQUIRING}) {
    ++pps.seq;
    pps.edge_tcb0 += 1000100;
    pps.gps_status = status;
    clock.observe(pps, 1000000, 12000);
    assert(clock.correctedHz(1000000, 12000) == 0);
  }
  pps.gps_status = LOCKED;
  ++pps.seq;
  pps.edge_tcb0 += 1000100;
  clock.observe(pps, 1000000, 0xffffff00u);
  assert(clock.correctedHz(1000000, 0xffffff00u) == 0); // Both endpoints must be locked.
  ++pps.seq;
  pps.edge_tcb0 += 1000100;
  clock.observe(pps, 1000000, 0xffffff80u);
  assert(clock.correctedHz(1000000, 1000) == 1000100); // millis wrap.
  assert(clock.correctedHz(1000000, 5000) == 0);
  // Exact half-lives, blending on every sample, and outlier attenuation.
  CanonicalTiming::PpsClock smoothed;
  CanonicalPpsSample sample = {};
  sample.gps_status = LOCKED;
  uint32_t now = 0;
  smoothed.observe(sample, 16000000, now);
  auto next = [&](uint32_t ticks) {
    ++sample.seq;
    sample.edge_tcb0 += ticks;
    now += 1000;
    smoothed.observe(sample, 16000000, now);
  };
  next(16000000);
  assert(smoothed.fastHz() == 16000000 && smoothed.slowHz() == 16000000);
  for (unsigned i = 1; i <= 3600; ++i) {
    next(16001000);
    assert(smoothed.blendedHz() == 0.75 * smoothed.fastHz() + 0.25 * smoothed.slowHz());
    assert(smoothed.correctedHz(16000000, now) == smoothed.blendedHz());
    if (i == 1) {
      assert(smoothed.blendedHz() - 16000000 < 26); // A 1000Hz input moves EST only ~25.6Hz.
      assert(smoothed.fastHz() - 16000000 > 100 * (smoothed.slowHz() - 16000000));
    }
    if (i == 20) assert(std::fabs(smoothed.fastHz() - 16000500) < 1e-6);
  }
  assert(std::fabs(smoothed.slowHz() - 16000500) < 1e-5);
  assert(std::fabs(smoothed.fastHz() - 16001000) < 1e-6);
  for (unsigned i = 0; i < 36000; ++i) next(16001000);
  assert(std::fabs(smoothed.blendedHz() - 16001000) < 0.13);

  const double fast = smoothed.fastHz(), slow = smoothed.slowHz();
  const uint32_t acceptedAt = now;
  next(500); // Grossly anomalous locked observation.
  assert(smoothed.fastHz() == fast && smoothed.slowHz() == slow);
  assert(smoothed.observationHz() == 16001000);
  for (GpsStatus status : {NO_PPS, HOLDOVER, ACQUIRING}) {
    sample.gps_status = status;
    next(16001000);
    assert(smoothed.fastHz() == fast && smoothed.slowHz() == slow);
  }
  assert(smoothed.correctedHz(16000000, acceptedAt + 4999) > 0);
  assert(smoothed.correctedHz(16000000, acceptedAt + 5000) == 0);
  assert(!strcmp(smoothed.status(acceptedAt + 5000), "STALE"));
  sample.gps_status = LOCKED;
  next(16001000); // Previous endpoint is not locked.
  assert(smoothed.fastHz() == fast && smoothed.slowHz() == slow);
  sample.seq += 2;
  next(48003000); // Gap cannot become an observation or advance the EWMA.
  assert(smoothed.fastHz() == fast && smoothed.slowHz() == slow);
  ++sample.drop_pps;
  next(16001000); // Drop counter change also rejects.
  assert(smoothed.fastHz() == fast && smoothed.slowHz() == slow);
  next(16001000); // Recovery continues retained states, not fresh initialization.
  assert(std::fabs(smoothed.slowHz() -
      (slow + CanonicalTiming::PpsClock::alpha(3600) * (16001000 - slow))) < 1e-8);
  assert(!strcmp(smoothed.status(now), "LOCKED"));
  const double retained = smoothed.blendedHz();
  ++sample.seq;
  next(32002000); // One missing PPS inside the five-second retention window.
  assert(smoothed.blendedHz() == retained);
  assert(smoothed.correctedHz(16000000, now) == retained);
  assert(!strcmp(smoothed.status(now), "REJECTED"));
  const CanonicalTiming::PpsClock snapshot = smoothed;
  smoothed.status(now + 5000); smoothed.fastHz(); smoothed.slowHz(); smoothed.blendedHz();
  assert(!memcmp(&snapshot, &smoothed, sizeof(smoothed))); // Diagnostics are read-only.
  puts("canonical intervals, clock correction, gaps, invalid pulses and wrap passed");
}
