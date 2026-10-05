#pragma once
#include <stdint.h>

// Foreground only, after ingestion has headroom and between transactions.
namespace I2cBus {
enum class Bus : uint8_t { Oled, Sensors };
constexpr uint32_t OLED_CLOCK_HZ = 400000;
constexpr uint32_t SENSOR_CLOCK_HZ = 100000;
constexpr uint32_t TIMEOUT_US = 10000;
void begin();
bool service(Bus bus);
void observe(Bus bus);
uint8_t probe(Bus bus, uint8_t address);
const char* resultName(uint8_t result);
int sda(Bus bus);
int scl(Bus bus);
}
