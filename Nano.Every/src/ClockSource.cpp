#include "ClockSource.h"

#include "Config.h"

#if USE_EXTCLK_MAIN
#include <avr/interrupt.h>
#include <avr/io.h>
#include <avr/xmega.h>

#if !defined(__AVR_ATmega4809__)
#error "USE_EXTCLK_MAIN is currently supported only on ATmega4809 targets"
#endif
#endif

void configureMainClockIfConfigured() {
#if USE_EXTCLK_MAIN
  // Nano Every / ATmega4809 EXTCLK notes:
  // - PA0 (Arduino D2) becomes the driven external main clock input.
  // - The external clock must already be present before this runs.
  // - This is a one-shot boot-time handoff; do not attempt runtime switching.
  // - The external source frequency must match the build's F_CPU/MAIN_CLOCK_HZ.
  const uint8_t saved_sreg = SREG;
  cli();

  // Clear the reset-default prescaler so CLK_PER tracks the selected main clock.
  _PROTECTED_WRITE(CLKCTRL.MCLKCTRLB, 0x00);

  // Switch CLK_MAIN to the driven external clock on EXTCLK (PA0 / D2).
  _PROTECTED_WRITE(CLKCTRL.MCLKCTRLA, CLKCTRL_CLKSEL_EXTCLK_gc);

  SREG = saved_sreg;
#endif
}
