#include <assert.h>
#include <stdint.h>

#include "../../Uno.R4.Deprecated/src/SDLogger.h"

static void testRecordLimits() {
  using SDLogger::detail::recordFits;
  assert(recordFits(0, SDLogger::DIAGNOSTIC_SEGMENT_BYTES,
                    SDLogger::DIAGNOSTIC_SEGMENT_BYTES));
  assert(recordFits(SDLogger::DIAGNOSTIC_SEGMENT_BYTES - 10, 10,
                    SDLogger::DIAGNOSTIC_SEGMENT_BYTES));
  assert(!recordFits(SDLogger::DIAGNOSTIC_SEGMENT_BYTES - 9, 10,
                     SDLogger::DIAGNOSTIC_SEGMENT_BYTES));
  assert(!recordFits(0, static_cast<size_t>(SDLogger::MEASUREMENT_SEGMENT_BYTES) + 1U,
                     SDLogger::MEASUREMENT_SEGMENT_BYTES));
}

static void testBudgetBurstRefillAndMillisWrap() {
  SDLogger::detail::DiagnosticBudget budget = {};
  budget.reset(1000);
  for (uint8_t i = 0; i < SDLogger::detail::DIAGNOSTIC_BURST_ROWS; ++i) {
    assert(budget.allow(1000));
  }
  assert(!budget.allow(1000));
  assert(!budget.allow(1999));
  assert(budget.allow(2000));
  assert(!budget.allow(2000));

  budget.reset(UINT32_MAX - 499U);
  for (uint8_t i = 0; i < SDLogger::detail::DIAGNOSTIC_BURST_ROWS; ++i) {
    assert(budget.allow(UINT32_MAX - 499U));
  }
  assert(!budget.allow(499));
  assert(budget.allow(500));
}

int main() {
  testRecordLimits();
  testBudgetBurstRefillAndMillisWrap();
  return 0;
}
