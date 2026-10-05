#pragma once

#if __has_include(<WiFiS3.h>)
#include <WiFiS3.h>
#elif __has_include(<WiFi.h>)
#include <WiFi.h>
#else
#error "Neither <WiFiS3.h> nor <WiFi.h> was found. Install the Arduino WiFiS3 library / UNO R4 WiFi board package."
#endif
