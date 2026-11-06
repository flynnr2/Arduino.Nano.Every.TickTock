#pragma once

#include <Adafruit_SSD1306.h>

#include "Config.h"
#include "NanoComm.h"
#include "PendulumProtocol.h"

namespace Display {
void begin();
void showSplash();
// Queue an on-device log line in the fixed-size status alert ring.
void scrollLog(const char *msg);
void scrollLog(const String &msg);
void scrollLog(const __FlashStringHelper *fmsg);
// Redraw the OLED using the row contract documented in Display.cpp.
void update();
// Display-only estimator half-lives in minutes; takes effect on next body draw.
void configureRating(uint16_t shortMinutes, uint16_t longMinutes);
// Called every loop, including while busy, to count deferrals and log health.
// Performs at most one short I2C transaction when ingestion is clear.
void service(bool ingestPending);
void observeSwing(const CanonicalSwingSample& sample);
void observePps(const CanonicalPpsSample& sample);
} // namespace Display
