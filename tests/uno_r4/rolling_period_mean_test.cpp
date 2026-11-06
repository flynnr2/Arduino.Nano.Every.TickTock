#include "../../Uno.R4.Deprecated/src/RollingPeriodMean.h"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <deque>

int main() {
  RollingPeriodMean mean;
  std::deque<double> expected;
  assert(!mean.ready() && mean.count() == 0);
  // Independent brute-force oracle over many rollovers, including fractional
  // values and the ten complete impulse cycles in each full window.
  for (unsigned i = 0; i < 10000; ++i) {
    const double value = (int(i % 15) - 7) * 200.125 + i * .125;
    expected.push_back(value);
    if (expected.size() > 150) expected.pop_front();
    mean.observe(value);
    double sum = 0;
    for (double sample : expected) sum += sample;
    assert(std::fabs(mean.mean() - sum / expected.size()) < .00001);
    assert(mean.count() == expected.size());
    assert(mean.ready() == (i >= 149));
  }
  mean.reset();
  assert(!mean.ready() && mean.count() == 0);
  for (unsigned i = 0; i < 300; ++i) {
    mean.observe(-12.25);
    assert(mean.mean() == -12.25); // No values retained across reset.
  }
  puts("Equal-weight mean, 150-swing boundary, repeated rollover and reset passed");
}
