#pragma once

#include <avr/io.h>

constexpr uint8_t EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH = TCB_CAPTEI_bm | TCB_FILTER_bm;
constexpr uint8_t EVCTRL_CAPTURE_EDGE_HIGH_TO_LOW = EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH | TCB_EDGE_bm;
constexpr uint8_t EVCTRL_PPS_CAPTURE = TCB_CAPTEI_bm;

// Shared by initial setup, reset, and timestamp reconstruction.
constexpr uint8_t TCB0_ENABLE = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;
constexpr uint8_t TCB1_ENABLE = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;
constexpr uint8_t TCB2_ENABLE = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;
static_assert((TCB0_ENABLE & TCB_CLKSEL_gm) == TCB_CLKSEL_CLKDIV1_gc &&
              (TCB1_ENABLE & TCB_CLKSEL_gm) == TCB_CLKSEL_CLKDIV1_gc &&
              (TCB2_ENABLE & TCB_CLKSEL_gm) == TCB_CLKSEL_CLKDIV1_gc,
              "Capture reconstruction requires all timers at CLK_PER / 1");
static_assert(((EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH ^ EVCTRL_CAPTURE_EDGE_HIGH_TO_LOW) & TCB_FILTER_bm) == 0,
              "Both IR polarities must use the same filter setting");

// ATmega4808/4809 section 21.3.3.3: FILTER adds four system-clock cycles.
// With the clock contract above, these are four shared TCB0 timeline ticks.
constexpr uint8_t captureFilterDelayTicks(uint8_t evctrl) {
  return (evctrl & TCB_FILTER_bm) ? 4U : 0U;
}
