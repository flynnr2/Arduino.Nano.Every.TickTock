#pragma once

#include <avr/io.h>

constexpr uint8_t EVCTRL_CAPTURE_EDGE_HIGH_TO_LOW = TCB_CAPTEI_bm | TCB_EDGE_bm | TCB_FILTER_bm;
constexpr uint8_t EVCTRL_CAPTURE_EDGE_LOW_TO_HIGH = TCB_CAPTEI_bm | TCB_FILTER_bm;
