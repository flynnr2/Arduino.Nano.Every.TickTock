#include "ClockSource.h"

#include "Config.h"

#if USE_EXTCLK_MAIN
#include <avr/interrupt.h>
#include <avr/io.h>
#include <avr/xmega.h>
#include <util/delay_basic.h>

#if !defined(__AVR_ATmega4809__)
#error "USE_EXTCLK_MAIN is currently supported only on ATmega4809 targets"
#endif
#endif

namespace {
ExtClockHandoffDiagnostics s_extclk_handoff_diag = {};

#if USE_EXTCLK_MAIN
void preSwitchDelayMs(uint16_t delay_ms) {
  if (delay_ms == 0U) return;

  // Early-boot deterministic delay that does not depend on millis()/timers.
  constexpr uint16_t loops_per_ms = static_cast<uint16_t>(F_CPU / 4000UL);
  for (uint16_t i = 0; i < delay_ms; ++i) {
    _delay_loop_2(loops_per_ms);
  }
}
#endif
} // namespace

void configureMainClockIfConfigured() {
#if USE_EXTCLK_MAIN
  // Nano Every / ATmega4809 EXTCLK notes:
  // - PA0 (Arduino D2) becomes the driven external main clock input.
  // - The external clock must already be present before this runs.
  // - This is a one-shot boot-time handoff; do not attempt runtime switching.
  // - The external source frequency must match the build's F_CPU/MAIN_CLOCK_HZ.
  preSwitchDelayMs(static_cast<uint16_t>(EXTCLK_PRESWITCH_DELAY_MS));

  const uint8_t saved_sreg = SREG;
  cli();

  // Clear the reset-default prescaler so CLK_PER tracks the selected main clock.
  _PROTECTED_WRITE(CLKCTRL.MCLKCTRLB, 0x00);

  // Switch CLK_MAIN to the driven external clock on EXTCLK (PA0 / D2).
  _PROTECTED_WRITE(CLKCTRL.MCLKCTRLA, CLKCTRL_CLKSEL_EXTCLK_gc);

  SREG = saved_sreg;

  uint16_t poll_count = 0;
  bool sosc_clear = false;
  for (; poll_count < static_cast<uint16_t>(EXTCLK_SOSC_CLEAR_POLL_ITERATIONS); ++poll_count) {
    if ((CLKCTRL.MCLKSTATUS & CLKCTRL_SOSC_bm) == 0) {
      sosc_clear = true;
      break;
    }
  }

  s_extclk_handoff_diag.preSwitchDelayApplied = (EXTCLK_PRESWITCH_DELAY_MS > 0U) ? 1U : 0U;
  s_extclk_handoff_diag.soscClearPollSucceeded = sosc_clear ? 1U : 0U;
  s_extclk_handoff_diag.soscClearPollIterations = poll_count;
  s_extclk_handoff_diag.mclkctrla = CLKCTRL.MCLKCTRLA;
  s_extclk_handoff_diag.mclkctrlb = CLKCTRL.MCLKCTRLB;
  s_extclk_handoff_diag.mclkstatus = CLKCTRL.MCLKSTATUS;
#endif
}

ExtClockHandoffDiagnostics getExtClockHandoffDiagnostics() {
  return s_extclk_handoff_diag;
}
