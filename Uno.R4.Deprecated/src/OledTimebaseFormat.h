#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include "CanonicalTiming.h"

// Adafruit GFX classic font at text size 1: 5 glyph pixels + 1 spacing,
// 8 pixels high. Four label cells + 17 numeric cells = 126 pixels.
namespace OledTimebaseFormat {
constexpr unsigned CHAR_WIDTH = 6;
constexpr unsigned ROW_HEIGHT = 8;
constexpr unsigned ROW_CHARS = 21;
constexpr unsigned ROW_PIXELS = ROW_CHARS * CHAR_WIDTH;
inline void row(char* out, size_t size, uint8_t row,
                const CanonicalTiming::PpsClock& clock, uint32_t now) {
  if (row == 0) { snprintf(out, size, "TIMEBASE Hz"); return; }
  if (row >= 1 && row <= 3) {
    const char* label = row == 1 ? "EST" : row == 2 ? "20s" : "1h";
    if (!clock.initialized()) { snprintf(out, size, "%-3s %17s", label, "--"); return; }
    const double hz = row == 1 ? clock.blendedHz() : row == 2 ? clock.fastHz() : clock.slowHz();
    // All accepted uint32_t frequencies fit: ten digits, point, six decimals.
    snprintf(out, size, "%-3s %17.6f", label, hz);
    return;
  }
  if (row == 5) { snprintf(out, size, "PPS %s", clock.status(now)); return; }
  if (size) out[0] = '\0';
}
} // namespace OledTimebaseFormat
