#include "../../Uno.R4.Deprecated/src/PeriodDisplayState.h"
#include <cassert>
#include <cstring>
#include <cstdio>
#include <string>
static std::string row(const PeriodDisplayState& s, uint32_t now, uint8_t r = 0) {
  char out[22]; s.formatRatingRow(out, sizeof(out), r, now); return out;
}
int main() {
  static_assert(sizeof(PeriodDisplayState) <= 384, "Display statistics must stay small");
  PeriodDisplayState state;
  assert(row(state, 0) == "P S: waiting");
  CanonicalSwingSample s = {};
  for (unsigned i = 0; i < 45; ++i) {
    s.seq = i;
    s.edge0_tcb0 = i * 32000000u;
    s.edge1_tcb0 = s.edge0_tcb0 + 15000000;
    s.edge2_tcb0 = s.edge0_tcb0 + 16000000;
    s.edge3_tcb0 = s.edge0_tcb0 + 31000000;
    s.edge4_tcb0 = s.edge0_tcb0 + 32000000;
    state.observeSwing(s, 16000000, i * 2000);
  }
  assert(row(state, 88000) == "P S~ 2000000.00us");
  assert(!strcmp(state.timebase(88000), "NOMINAL"));
  state.observeSwing(s, 16000000, 89000); // Duplicate does not refresh.
  assert(!strcmp(state.timebase(98000), "STALE"));
  s.seq += 2;
  state.observeSwing(s, 16000000, 90000);
  assert(state.estimator().horizon(0).elapsedSeconds == 0);
  s.edge1_tcb0 = s.edge0_tcb0;
  ++s.seq;
  state.observeSwing(s, 16000000, 92000);
  assert(!strcmp(state.timebase(92000), "WAIT"));

  state = PeriodDisplayState{};
  CanonicalPpsSample p = {};
  p.gps_status = LOCKED;
  state.observePps(p, 16000000, 0);
  p.seq = 1; p.edge_tcb0 = 16001600;
  state.observePps(p, 16000000, 1000);
  s = CanonicalSwingSample{};
  s.edge1_tcb0 = 15000000; s.edge2_tcb0 = 16001600;
  s.edge3_tcb0 = 31000000; s.edge4_tcb0 = 32003200;
  state.observeSwing(s, 16000000, 2000);
  assert(row(state, 2000) == "P S~ 2000000.0us");
  assert(!strcmp(state.timebase(2000), "PPS"));
  assert(std::fabs(state.estimator().horizon(0).blockUs - (-1600.0 * 1000000.0 / 16001600.0)) < 1e-8);
  ++s.seq; s.edge0_tcb0 = s.edge4_tcb0;
  s.edge1_tcb0 = s.edge0_tcb0 + 15000000;
  s.edge2_tcb0 = s.edge0_tcb0 + 16000000;
  s.edge3_tcb0 = s.edge0_tcb0 + 31000000;
  s.edge4_tcb0 = s.edge0_tcb0 + 32000000;
  state.observeSwing(s, 16000000, 6000); // PPS scale has expired.
  assert(!strcmp(state.timebase(6000), "NOMINAL"));
  assert(state.estimator().horizon(0).elapsedSeconds == 0);
  state.configure(2, 20);
  assert(!strcmp(state.timebase(6000), "WAIT"));
  assert(row(state, 6000) == "P S: waiting");
  // Byte-for-byte proof that formatting is read-only, including stale renders.
  const PeriodDisplayState copy = state;
  for (unsigned i = 0; i < 6; ++i) { row(state, 6000, i); row(state, 20000, i); }
  assert(!memcmp(&copy, &state, sizeof(state)));
  // Canonical conversion consumes the same precise blend exposed to diagnostics.
  PeriodDisplayState causal;
  p = CanonicalPpsSample{};
  p.gps_status = LOCKED;
  causal.observePps(p, 16000000, 0);
  ++p.seq; p.edge_tcb0 += 16000000;
  causal.observePps(p, 16000000, 1000);
  ++p.seq; p.edge_tcb0 += 16001000;
  causal.observePps(p, 16000000, 2000);
  s = CanonicalSwingSample{};
  s.edge1_tcb0 = 15000000; s.edge2_tcb0 = 16000000;
  s.edge3_tcb0 = 31000000; s.edge4_tcb0 = 32000000;
  causal.observeSwing(s, 16000000, 2000);
  const double first = 32000000.0 * 1000000 / causal.timebaseClock().blendedHz();
  assert(std::fabs(causal.estimator().horizon(0).periodUs - first) < 1e-8);
  assert(std::fabs(first - 32000000.0 * 1000000 / 16001000) > 100);
  ++p.seq; p.edge_tcb0 += 16000000;
  causal.observePps(p, 16000000, 3000);
  ++s.seq; s.edge0_tcb0 += 32000000; s.edge1_tcb0 += 32000000;
  s.edge2_tcb0 += 32000000; s.edge3_tcb0 += 32000000; s.edge4_tcb0 += 32000000;
  causal.observeSwing(s, 16000000, 4000);
  const double second = 32000000.0 * 1000000 / causal.timebaseClock().blendedHz();
  for (uint8_t i = 0; i < 2; ++i) {
    const double alpha = OledRatingEstimator::alpha(second / 1000000, causal.estimator().minutes(i));
    assert(std::fabs(causal.estimator().horizon(i).periodUs - (first + alpha * (second - first))) < 1e-8);
  }
  puts("Capture rating, PPS calibration, gap, duplicate and wrap checks passed");
}
