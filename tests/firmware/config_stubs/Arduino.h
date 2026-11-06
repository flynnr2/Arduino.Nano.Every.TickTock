#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#define LED_BUILTIN 13
#define PROGMEM
struct __FlashStringHelper;
#define F(s) reinterpret_cast<const __FlashStringHelper*>(s)
class Print {
public:
 virtual ~Print() {}
 virtual size_t write(uint8_t) { return 1; }
 void print(const char* s) { while (*s) write(*s++); }
 void print(const __FlashStringHelper* s) { print(reinterpret_cast<const char*>(s)); }
 void print(unsigned long n) { char s[24]; snprintf(s, sizeof s, "%lu", n); print(s); }
 void print(unsigned n) { print((unsigned long)n); }
 template<class T> void println(T s) { print(s); println(); }
 void println() { write('\n'); }
};
extern Print Serial;
