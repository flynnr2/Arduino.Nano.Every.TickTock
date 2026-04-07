#include "ResetCauseEarly.h"

#include <Arduino.h>

namespace {
// .noinit forensic breadcrumbs survive reset classes where SRAM is retained.
// Cookie + raw value are written from .init3 before setup()/constructors.
uint8_t g_rstfr_early_raw_noinit __attribute__((section(".noinit")));
uint16_t g_rstfr_early_cookie_noinit __attribute__((section(".noinit")));
uint16_t g_rstfr_early_count_noinit __attribute__((section(".noinit")));

constexpr uint16_t RSTFR_EARLY_COOKIE = 0xE17Fu;

// Very-early startup hook:
// - runs from AVR .init3, before normal runtime facilities are safe.
// - must avoid Serial/Arduino APIs/dynamic allocation/initialized globals.
// - captures and clears RSTCTRL.RSTFR immediately for forensic retention.
extern "C" void captureResetCauseVeryEarly(void)
    __attribute__((used, naked, section(".init3")));
extern "C" void captureResetCauseVeryEarly(void) {
  const uint8_t rstfr = RSTCTRL.RSTFR;
  g_rstfr_early_raw_noinit = rstfr;
  g_rstfr_early_cookie_noinit = RSTFR_EARLY_COOKIE;
  if (g_rstfr_early_count_noinit == 0xFFFFu) {
    g_rstfr_early_count_noinit = 0;
  }
  g_rstfr_early_count_noinit = static_cast<uint16_t>(g_rstfr_early_count_noinit + 1u);
  RSTCTRL.RSTFR = rstfr;
}

}  // namespace

void resetCauseEarlyInitNoopReference() {
  // Intentionally empty. Allows normal C++ modules to force-link this unit if
  // aggressive LTO dead-stripping ever changes .init-section reachability.
}

bool resetCauseEarlyValid() {
  return g_rstfr_early_cookie_noinit == RSTFR_EARLY_COOKIE;
}

uint8_t resetCauseEarlyRaw() {
  return g_rstfr_early_raw_noinit;
}

uint16_t resetCauseEarlyCaptureCount() {
  return g_rstfr_early_count_noinit;
}
