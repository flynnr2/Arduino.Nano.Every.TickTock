#pragma once
#include <stdint.h>
namespace SDLogger {
bool isValidFilename(const char* name);
const char* getFilename();
}
namespace Display {
void configureRating(uint16_t shortMinutes, uint16_t longMinutes);
}
