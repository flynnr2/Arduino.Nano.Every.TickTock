#include "../../Uno.R4.Deprecated/src/OledRatingEstimator.h"
#include <cassert>
#include <cstring>
#include <cstdio>
#include <string>
#include <limits>
static bool close(double a, double b, double tolerance = 1e-8) { return std::fabs(a - b) < tolerance; }
static std::string row(const OledRatingEstimator& state, uint8_t n) {
  char out[22]; state.formatRow(out, sizeof(out), n, false); return out;
}
int main() {
  static_assert(sizeof(OledRatingEstimator) <= 256, "Rating estimator must stay constant and small");
  OledRatingEstimator e;
  assert(e.minutes(0) == 5 && e.minutes(1) == 60);
  assert(close(OledRatingEstimator::alpha(300, 5), 0.5));
  assert(close(OledRatingEstimator::alpha(3600, 60), 0.5));
  e.observe(1000000, -100);
  // A step to two seconds for 300 measured seconds halves initial error.
  for (unsigned i = 0; i < 150; ++i) e.observe(2000000, 100);
  assert(close(e.horizon(0).periodUs, 1500000, 1e-7));
  assert(close(e.horizon(0).blockUs, 0));
  assert(close(e.horizon(0).bpm(), 40)); // A separate BPM EWMA would give 45.
  assert(e.horizon(1).periodUs < e.horizon(0).periodUs);
  assert(!e.learning(0) && e.learning(1));
  for (unsigned i = 150; i < 1800; ++i) e.observe(2000000, 100);
  assert(close(e.horizon(1).periodUs, 1500000, 1e-6));
  assert(!e.learning(1));
  assert(!e.configure(5, 60));
  assert(e.available());
  assert(e.configure(1, 15));
  assert(!e.available());
  assert(e.minutes(0) == 1 && e.minutes(1) == 15);

  e.observe(1998476.12, -0.00001);
  assert(row(e, 0) == "P S~ 1998476.1us");
  assert(row(e, 4) == "dB S~ +0.0us");
  for (unsigned i = 0; i < 30; ++i) e.observe(1998476.12, -0.00001);
  assert(!e.horizon(0).periodPrecision.fine); // Not yet sixty measured seconds.
  e.observe(1998476.12, -0.00001);
  assert(e.horizon(0).periodPrecision.fine);
  assert(row(e, 0) == "P S  1998476.12us");
  assert(row(e, 4) == "dB S  +0.00us");
  assert(row(e, 2).find("BPM") != std::string::npos);
  // Quiet changes near the thresholds do not toggle an established digit.
  OledRatingEstimator::Precision p;
  const double movementWeight = -std::expm1(-2.0 / 30.0);
  for (unsigned i = 0; i < 30; ++i) p.observe(0, 2, 0.01, movementWeight);
  assert(p.fine);
  for (unsigned i = 0; i < 100; ++i) { p.observe(i % 2 ? 0.006 : 0.004, 2, 0.01, movementWeight); assert(p.fine); }
  p.observe(1, 2, 0.01, movementWeight); assert(p.fine);
  p.observe(1, 2, 0.01, movementWeight); assert(p.fine);
  p.observe(1, 2, 0.01, movementWeight); assert(!p.fine);
  for (unsigned i = 0; i < 29; ++i) { p.observe(0, 2, 0.01, movementWeight); assert(!p.fine); }
  for (unsigned i = 0; i < 5; ++i) e.observe(2000000, 200);
  assert(!e.horizon(0).periodPrecision.fine);
  assert(!e.horizon(0).blockPrecision.fine);

  // Cover row lengths, complete units, signed values and magnitude fallback.
  for (double value : {0.00001, 2.0, 1998476.12, 2147483647000.0, 1e100}) {
    e.reset(); e.observe(value, -value / 2);
    for (unsigned i = 0; i < 6; ++i) {
      std::string text = row(e, i);
      assert(text.size() <= 21);
      assert(text.find(i / 2 == 1 ? "BPM" : "us") != std::string::npos || text.find("range") != std::string::npos);
    }
  }
  // Signed raw block limits still fit at minimum supported nominal frequency.
  for (double sign : {-1.0, 1.0}) {
    e.reset(); e.observe(17179869180000.0, sign * 4294967295000.0);
    const std::string extreme = row(e, 4);
    assert(extreme.size() == 21);
    assert(extreme == (sign > 0 ? "dB S~+4294967295000us" : "dB S~-4294967295000us"));
  }
  e.reset(); e.observe(1e100, 1e100);
  assert(row(e, 0) == "P S: range");
  assert(row(e, 4) == "dB S: range");
  e.reset(); e.observe(2000000, -1);
  const OledRatingEstimator copy = e;
  for (unsigned i = 0; i < 6; ++i) row(e, i);
  assert(!memcmp(&copy, &e, sizeof(e)));
  char guarded[] = {'a', 'b', 'c'};
  e.formatRow(guarded + 1, 1, 0, false);
  assert(guarded[0] == 'a' && guarded[1] == '\0' && guarded[2] == 'c');
  e.formatRow(nullptr, 0, 0, false);
  assert(!e.observe(std::numeric_limits<double>::infinity(), 1));
  assert(!e.available());
  assert(!e.observe(2000000, std::numeric_limits<double>::quiet_NaN()));
  puts("Rating EWMA half-life, convergence, reciprocal BPM, signed block, maturity, adaptive precision and fit checks passed");
}
