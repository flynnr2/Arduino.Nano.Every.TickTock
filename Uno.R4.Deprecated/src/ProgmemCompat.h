#pragma once

// Ownership: shared utility/plumbing header.
// Sync rule: this file may be shared between repos if useful, but it is not part
// of the pendulum wire or command contract.
// Scope: flash/RAM string-table compatibility helpers only.

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifdef __AVR__
#include <avr/pgmspace.h>
#endif

inline const char* flashStrAt(const char* const* table, uint8_t index) {
#ifdef __AVR__
  return reinterpret_cast<const char*>(pgm_read_ptr(&table[index]));
#else
  return table[index];
#endif
}

inline size_t flashStrLen(const char* text) {
#ifdef __AVR__
  return text ? strlen_P(text) : 0;
#else
  return text ? strlen(text) : 0;
#endif
}

inline int cmpRamToFlash(const char* ramText, const char* flashText) {
  if (!ramText || !flashText) return (ramText == flashText) ? 0 : 1;
#ifdef __AVR__
  return strcmp_P(ramText, flashText);
#else
  return strcmp(ramText, flashText);
#endif
}

inline int cmpRamToFlashN(const char* ramText, const char* flashText, size_t n) {
  if (!ramText || !flashText) return (ramText == flashText) ? 0 : 1;
#ifdef __AVR__
  return strncmp_P(ramText, flashText, n);
#else
  return strncmp(ramText, flashText, n);
#endif
}

inline void copyFlashToRam(void* dst, const char* flashSrc, size_t len) {
#ifdef __AVR__
  memcpy_P(dst, flashSrc, len);
#else
  memcpy(dst, flashSrc, len);
#endif
}
