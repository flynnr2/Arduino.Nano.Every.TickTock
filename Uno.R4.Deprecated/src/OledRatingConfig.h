#pragma once

#include <stdint.h>

// Display-only EWMA half-lives, expressed in intuitive whole minutes.
namespace OledRatingConfig {
constexpr uint16_t DEFAULT_SHORT_MINUTES = 5;
constexpr uint16_t DEFAULT_LONG_MINUTES = 60;
constexpr uint16_t MIN_SHORT_MINUTES = 1;
constexpr uint16_t MAX_SHORT_MINUTES = 30;
constexpr uint16_t MIN_LONG_MINUTES = 15;
constexpr uint16_t MAX_LONG_MINUTES = 360;

inline bool valid(uint16_t shortMinutes, uint16_t longMinutes) {
  return shortMinutes >= MIN_SHORT_MINUTES && shortMinutes <= MAX_SHORT_MINUTES &&
         longMinutes >= MIN_LONG_MINUTES && longMinutes <= MAX_LONG_MINUTES &&
         longMinutes >= 2U * shortMinutes;
}

inline void sanitize(uint16_t& shortMinutes, uint16_t& longMinutes) {
  if (shortMinutes < MIN_SHORT_MINUTES) shortMinutes = MIN_SHORT_MINUTES;
  if (shortMinutes > MAX_SHORT_MINUTES) shortMinutes = MAX_SHORT_MINUTES;
  if (longMinutes < MIN_LONG_MINUTES) longMinutes = MIN_LONG_MINUTES;
  if (longMinutes > MAX_LONG_MINUTES) longMinutes = MAX_LONG_MINUTES;
  if (longMinutes < 2U * shortMinutes) longMinutes = 2U * shortMinutes;
}

// Strict decimal parsing rejects signs, fractional values, and overflow before
// narrowing. HTTP callers additionally reject values outside each field's range.
inline bool parseMinutes(const char* text, uint16_t& result) {
  if (!text || !*text) return false;
  uint32_t value = 0;
  for (; *text; ++text) {
    if (*text < '0' || *text > '9') return false;
    value = value * 10U + static_cast<uint32_t>(*text - '0');
    if (value > MAX_LONG_MINUTES) return false;
  }
  result = static_cast<uint16_t>(value);
  return true;
}
} // namespace OledRatingConfig
