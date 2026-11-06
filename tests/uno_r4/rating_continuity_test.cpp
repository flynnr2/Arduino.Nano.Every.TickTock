#include "../../Uno.R4.Deprecated/src/PeriodDisplayState.h"
#include <cassert>
#include <cstring>
#include <cstdio>

static void learning(const PeriodDisplayState& state, uint32_t now) {
  char out[22];
  state.formatRatingRow(out, sizeof(out), 0, now);
  assert(state.estimator().learning(0));
  assert(strstr(out, "S~") != nullptr);
  assert(state.estimator().horizon(0).elapsedSeconds == 0);
}
static CanonicalSwingSample swing(uint32_t seq, uint32_t start) {
  CanonicalSwingSample s = {};
  s.seq = seq;
  s.edge0_tcb0 = start;
  s.edge1_tcb0 = start + 15000000;
  s.edge2_tcb0 = start + 16000000;
  s.edge3_tcb0 = start + 31000000;
  s.edge4_tcb0 = start + 32000000;
  return s;
}
int main() {
  uint32_t now = 0xffff0000u;
  PeriodDisplayState full;
  CanonicalSwingSample s = {};
  for (unsigned i = 0; i <= 150; ++i) {
    s = swing(i, i * 32000000u);
    full.observeSwing(s, 16000000, now);
    assert(!full.estimator().learning(0) == (i == 150));
    now += 2000;
  }
  // Replays cannot prolong freshness or change the window.
  full.observeSwing(s, 16000000, now + 7000);
  assert(!strcmp(full.timebase(now + 8000), "STALE"));
  char out[22];
  full.formatRatingRow(out, sizeof(out), 0, now + 8000);
  assert(!strcmp(out, "P S: stale"));
  for (unsigned fault = 0; fault < 7; ++fault) {
    PeriodDisplayState state = full;
    auto next = swing(s.seq + 1, s.edge4_tcb0);
    uint32_t at = now;
    uint32_t hz = 16000000;
    if (fault == 0) ++next.seq;
    if (fault == 1) next = swing(next.seq, next.edge0_tcb0 + 1);
    if (fault == 2) ++next.drop_ir;
    if (fault == 3) ++next.drop_swing;
    if (fault == 4) at += 8000; // Exactly ten seconds since last valid swing.
    if (fault == 5) hz = 16000001;
    if (fault == 6) {
      next.edge1_tcb0 = next.edge0_tcb0;
      state.observeSwing(next, hz, at);
      assert(!state.estimator().available());
      state.formatRatingRow(out, sizeof(out), 0, at);
      assert(!strcmp(out, "P S: waiting"));
      next = swing(next.seq + 1, next.edge4_tcb0);
      at += 2000;
    }
    state.observeSwing(next, hz, at);
    learning(state, at);
  }
  CanonicalPpsSample p = {};
  p.seq = 10;
  full.observePps(p, 16000000, now);
  p.seq = 1;
  full.observePps(p, 16000000, now + 1000);
  assert(!full.estimator().available()); // Capture timeline restarted.

  puts("Rating ingestion, duplicate, stale, wrap, gap, edge, drop, invalid, timebase resets passed");
}
