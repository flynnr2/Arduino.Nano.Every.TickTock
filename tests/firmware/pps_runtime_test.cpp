// The test runner supplies the actual production globals, reset and process_pps
// functions. Only capture I/O, serial emission, and wall-clock reads are mocked.
#include <cassert>
#include <deque>
#include <vector>
#include "Config.h"
#include "PendulumProtocol.h"
#include "PendulumCapture.h"
#include "PpsValidator.h"
#include "PpsEdgeFilter.h"
#include "FreqDiscipliner.h"
#include "DisciplinedTime.h"
#include "PpsFreshness.h"

static uint32_t test_ms, seen;
static std::deque<PpsCapture> queue;
static std::vector<CanonicalPpsSample> raw;
struct Baseline { uint32_t interval; PpsValidator::SampleClass cls; };
static std::vector<Baseline> baseline;
uint32_t platformMillis() { return test_ms; }
uint32_t capturePpsSeen() { return seen; }
uint32_t captureDroppedPpsEvents() { return 0; }
void captureResetAndReinit() { queue.clear(); seen = 0; }
bool captureTryPopPps(PpsCapture* cap) {
  if (queue.empty()) return false;
  *cap = queue.front();
  queue.pop_front();
  return true;
}
bool sendCanonicalPpsSample(const CanonicalPpsSample& cap) {
  raw.push_back(cap);
  return true;
}
void emitMetadataNow() {}
void emitStartupNow() {}
void emit_pps_baseline_telemetry(uint32_t, uint32_t, uint32_t interval,
                                PpsValidator::SampleClass cls, bool,
                                const FreqDiscipliner&, uint16_t, uint16_t) {
  baseline.push_back(Baseline{interval, cls});
}

#include "pps_runtime_under_test.inc"

static const uint64_t Hz = MAIN_CLOCK_HZ;
static void reset() {
  resetRuntimeStateAfterTunablesChange();
  raw.clear();
  baseline.clear();
  test_ms = 0;
  startup_output_ready = true;
}
static void feed(uint64_t tick) {
  PpsCapture cap{};
  cap.seq = ++seen;
  cap.edge32 = (uint32_t)tick;
  cap.now32 = cap.edge32 + 151;
  cap.cap16 = (uint16_t)tick;
  cap.latency16 = 151;
  test_ms = (uint32_t)(tick * 1000 / Hz);
  queue.push_back(cap);
  process_pps(8);
  assert(last_pps_processed_ms == test_ms);
  assert(raw.size() == seen);
  assert(raw.back().seq == cap.seq);
  assert(raw.back().edge_tcb0 == cap.edge32);
  assert(raw.back().now32 == cap.now32);
  assert(raw.back().cap16 == cap.cap16);
  assert(raw.back().latency16 == cap.latency16);
}
static uint64_t warm(uint64_t start = 0) {
  reset();
  for (unsigned i = 0; i <= 70; ++i) feed(start + i * Hz);
  assert(gPpsValidator.isValid());
  assert(gPpsValidator.referenceTicks() == Hz);
  assert(gFreqDiscipliner.state() == FreqDiscipliner::DiscState::DISCIPLINED);
  return start + 70 * Hz;
}
static void extra_capture(uint32_t offset, uint64_t start = 0) {
  uint64_t t = warm(start);
  feed(t + offset);
  assert(gPpsValidator.referenceTicks() == Hz);
  assert(gFreqDiscipliner.state() == FreqDiscipliner::DiscState::DISCIPLINED);
  assert(gFreqDiscipliner.fast() == Hz && gFreqDiscipliner.slow() == Hz);
#if ENABLE_PPS_BASELINE_TELEMETRY
  assert(baseline.back().interval == offset);
  assert(baseline.back().cls != PpsValidator::SampleClass::OK);
#endif
  feed(t + Hz);
  assert(gPpsValidator.okStreak() > 60);
  assert(gPpsValidator.isValid());
  assert(gFreqDiscipliner.fast() == Hz && gFreqDiscipliner.slow() == Hz);
#if ENABLE_PPS_BASELINE_TELEMETRY
  assert(baseline.back().interval == Hz - offset);
#endif
}
int main() {
  // Every raw capture is emitted, including rejected extras and counter wraps.
  for (uint32_t offset : {0U, 1U, 16000U, 3200000U, 6400000U, 9600000U,
                          14400000U, 15979999U}) {
    extra_capture(offset);
    extra_capture(offset, 0xFFFFFFFFULL - 70 * Hz - Hz / 2);
  }

  uint64_t t = warm();
  for (uint32_t offset : {1U, 16000U, 6400000U, 9600000U}) feed(t + offset);
  assert(gPpsValidator.referenceTicks() == Hz);
  feed(t + Hz);
  assert(gPpsValidator.okStreak() > 60);

  // Early extras affect neither lock health nor the frequency estimate.
  reset();
  const uint64_t drifted_hz = Hz + 8000;
  for (unsigned i = 0; i <= 70; ++i) feed(i * drifted_hz);
  t = 70 * drifted_hz;
  const uint32_t old_fast = gFreqDiscipliner.fast();
  const uint32_t old_slow = gFreqDiscipliner.slow();
  assert(old_fast != Hz);
  feed(t + Hz * 4 / 10);
  feed(t + Hz * 6 / 10);
  assert(gPpsValidator.isValid());
  assert(gFreqDiscipliner.fast() == old_fast && gFreqDiscipliner.slow() == old_slow);
  feed(t + drifted_hz);
  assert(gPpsValidator.okStreak() > 60);

  reset();
  feed(0);
  feed(Hz * 4 / 10);
  for (unsigned i = 1; i <= 8; ++i) feed(i * Hz);
  assert(gPpsValidator.referenceTicks() == Hz && gPpsValidator.isValid());

  // Missing pulses and a phase jump require a plausible pair for recovery.
  t = warm();
  feed(t + 2 * Hz);
  assert(gPpsValidator.health().gap == 1);
  assert(gFreqDiscipliner.fast() == Hz && gFreqDiscipliner.slow() == Hz);
  feed(t + 3 * Hz);
  assert(gPpsValidator.okStreak() == 1);
  for (unsigned i = 4; i <= 8; ++i) feed(t + i * Hz);
  assert(gPpsValidator.isValid());

  t = warm();
  feed(t + Hz + PpsValidator::kHardTicks() + 1);
  feed(t + 2 * Hz + PpsValidator::kHardTicks() + 1);
  assert(gPpsValidator.referenceTicks() == Hz);
  assert(gPpsValidator.okStreak() == 1);

  for (int offset : {-20000, 20000}) {
    t = warm();
    feed(t + Hz + offset);
    assert(gPpsValidator.okStreak() > 1);
  }

  // Freshness clears health across an outage longer than a complete wrap.
  t = warm();
  test_ms += 300000;
  process_pps(8);
  assert(!pps_primed);
  assert(gpsStatus == GpsStatus::NO_PPS);
  feed(t + 300 * Hz);
  assert(!gPpsValidator.isValid());
  for (unsigned i = 301; i <= 310; ++i) feed(t + i * Hz);
  assert(gPpsValidator.isValid());

  t = warm();
  const uint64_t resumed = t + (1ULL << 32) - 3 * Hz;
  test_ms += 260000;
  process_pps(8);
  feed(resumed);
  feed(resumed + Hz);
  assert(gPpsValidator.okStreak() == 1);
  for (unsigned i = 2; i <= 8; ++i) feed(resumed + i * Hz);
  assert(gPpsValidator.referenceTicks() == Hz && gPpsValidator.isValid());
}
