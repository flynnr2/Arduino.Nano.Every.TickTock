// --------------------------------------------------------------
// Nano Every GPS-Disciplined Pendulum Beat Timer with Tick/Tock Analysis
// Optimized for Arduino Nano Every (ATmega4809) with minimal onboard computation
// Focused on raw timing only — all analysis (asymmetry, amplitude, etc.) is offloaded to a secondary processor
// --------------------------------------------------------------

// --------------------------------------------------------------
// TODO
// --------------------------------------------------------------

// --------------------------------------------------------------
// Refactor / Optimisation Ideas (consolidated)
// --------------------------------------------------------------
// Diagnostics & Reliability
//  - Enable watchdog; feed it once per main lop after critical work (SD/Serial flush).
//  - Heartbeat pin: OK=1Hz blink, Low RAM=double blink, GPS lost=fast blink.
//  - Log reset cause (RSTCTRL.RSTFR) on boot to detect WDT/BOD resets.
//
// Serial Output
//  - Flush batch if USB disconnects (`!DATA_SERIAL.dtr()`).
//
// Compile-Time Hygiene
//  - Build with `-Wall -Wextra -Werror` and LTO (`-flto`) to catch regressions & shrink flash.
//
// Testing / Host Harness
//  - `#ifdef HOST_TEST` path to compile ISR logic as normal functions for unit tests (wrap math, debounce, parsing).
//
// GPS
//  - Add an XO/TCXO/VCTCXO to hold frequency if GPS drops; fall back to it when PPS missing.
//  - Read GPS sentences from USB -> TTL cable (compile time switch vs. DEBUGGING output?).
//
// Dual IR sensors to measure pendulum speed directly.

#include "src/EEPROMConfig.h"
#include "src/PendulumProtocol.h"
#include "src/PendulumCore.h"
#include "src/SerialParser.h"

void setup() {
  delay(5000);
  pendulumSetup();
  digitalWrite(ledPin, HIGH);
  delay(5000);
  digitalWrite(ledPin, LOW);
}

void loop() {
  pendulumLoop();
}
