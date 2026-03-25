#include "PlatformTime.h"

#include "PendulumCapture.h"

#if DISABLE_ARDUINO_TCB3_TIMEBASE
#include <avr/interrupt.h>
#include <avr/io.h>

#if !defined(__AVR_ATmega4809__)
#error "DISABLE_ARDUINO_TCB3_TIMEBASE is currently supported only on ATmega4809 targets"
#endif
#endif

namespace {
#if !USE_ARDUINO_TIMEBASE
static_assert((MAIN_CLOCK_HZ % 1000UL) == 0UL, "Custom platformMillis/platformDelayMs require MAIN_CLOCK_HZ divisible by 1000");
constexpr uint64_t TICKS_PER_MS = (uint64_t)(MAIN_CLOCK_HZ / 1000UL);
static uint64_t s_last_platform_ms = 0;
static uint32_t s_platform_ms_backstep_count = 0;
#endif
} // namespace

uint32_t platformTicks32() {
#if USE_ARDUINO_TIMEBASE
  return millis();
#else
  return (uint32_t)platformTicks64();
#endif
}

uint64_t platformTicks64() {
#if USE_ARDUINO_TIMEBASE
  return (uint64_t)millis();
#else
  return tcb0NowCoherent64();
#endif
}

uint32_t platformMillis() {
#if USE_ARDUINO_TIMEBASE
  return millis();
#else
  const uint64_t now_ms = platformTicks64() / TICKS_PER_MS;
  if (now_ms < s_last_platform_ms) {
    s_platform_ms_backstep_count++;
  } else {
    s_last_platform_ms = now_ms;
  }
  return (uint32_t)now_ms;
#endif
}

uint32_t platformMillisBackstepCount() {
#if USE_ARDUINO_TIMEBASE
  return 0;
#else
  return s_platform_ms_backstep_count;
#endif
}

void platformDelayMs(uint32_t ms) {
#if USE_ARDUINO_TIMEBASE
  delay(ms);
#else
  const uint32_t start_ms = platformMillis();
  while ((uint32_t)(platformMillis() - start_ms) < ms) {
  }
#endif
}

void disableArduinoTimebaseTCB3IfConfigured() {
#if DISABLE_ARDUINO_TCB3_TIMEBASE
  // Nano Every / megaAVR core timebase notes:
  // - This is a one-shot boot-time shutdown that must run before pendulumSetup()
  //   or any other subsystem that expects the final Arduino/custom timebase state.
  // - The stock Nano Every / megaAVR core uses TCB3 as the millis()/delay() ISR source,
  //   while this firmware's custom timebase comes from the coherent TCB0 timeline.
  // - This low-level TCB3 register path therefore assumes the ATmega4809 peripheral map
  //   and the current Arduino Nano Every core timer assignment; fail fast elsewhere.
  const uint8_t saved_sreg = SREG;
  cli();

  TCB3.INTCTRL = 0;
  TCB3.INTFLAGS = TCB_CAPT_bm;
  TCB3.CTRLA = 0;

  SREG = saved_sreg;
#endif
}
