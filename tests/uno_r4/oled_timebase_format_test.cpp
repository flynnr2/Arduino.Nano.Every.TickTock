#include "../../Uno.R4.Deprecated/src/OledTimebaseFormat.h"
#include <cassert>
#include <cstring>
#include <string>
#include <cstdio>
#include <initializer_list>

static std::string row(const CanonicalTiming::PpsClock& clock, unsigned r, uint32_t now) {
  char out[80]; // Detect overflow in the format, not merely buffer truncation.
  OledTimebaseFormat::row(out, sizeof(out), r, clock, now);
  assert(strlen(out) * OledTimebaseFormat::CHAR_WIDTH <= 128);
  assert((r + 1) * OledTimebaseFormat::ROW_HEIGHT + 8 <= 56);
  char panel[22];
  OledTimebaseFormat::row(panel, sizeof(panel), r, clock, now);
  assert(!strcmp(out, panel));
  return out;
}
int main() {
  for(uint32_t hz : {1000u,16000000u,UINT32_MAX}) {
    CanonicalTiming::PpsClock clock;
    for(unsigned r=0;r<6;++r) row(clock,r,0);
    CanonicalPpsSample p={}; p.gps_status=LOCKED;
    clock.observe(p,hz,0);
    p.seq=1; p.edge_tcb0=hz; clock.observe(p,hz,1000);
    for(unsigned r=0;r<6;++r) row(clock,r,1000);
    assert(row(clock,5,1000)=="PPS LOCKED");
    assert(row(clock,5,6000)=="PPS STALE");
    // A noninteger EWMA must render the authoritative state, not the raw interval.
    if (hz == 16000000u) {
      ++p.seq; p.edge_tcb0 += hz + 29; clock.observe(p,hz,2000);
      assert(clock.blendedHz() != hz + 29);
    }
    char expected[80];
    const double values[]={clock.blendedHz(),clock.fastHz(),clock.slowHz()};
    const char* labels[]={"EST","20s","1h"};
    for(unsigned r=1;r<=3;++r) {
      snprintf(expected,sizeof(expected),"%-3s %17.6f",labels[r-1],values[r-1]);
      assert(row(clock,r,1000)==expected);
    }
    for(auto status : {NO_PPS,HOLDOVER,ACQUIRING,LOCKED}) {
      ++p.seq; p.edge_tcb0+=hz; p.gps_status=status;
      clock.observe(p,hz,2000); row(clock,5,2000);
    }
  }
  puts("Timebase rows fit 128x64 at actual 6x8 font metrics without truncation");
}
