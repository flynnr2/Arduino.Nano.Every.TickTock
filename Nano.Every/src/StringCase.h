#pragma once

inline char asciiToLower(char c) {
  return (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
}

inline bool equalsIgnoreCaseAscii(const char* lhs, const char* rhs) {
  if (!lhs || !rhs) return false;
  while (true) {
    const char a = asciiToLower(*lhs++);
    const char b = asciiToLower(*rhs++);
    if (a != b) return false;
    if (a == '\0') return true;
  }
}
