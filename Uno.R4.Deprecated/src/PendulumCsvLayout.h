#pragma once

// Uno appends environmental readings to each Nano capture stream.
#include <stddef.h>
#include "PendulumProtocol.h"

static constexpr size_t PENDULUM_ENV_FIELD_COUNT = 3;
static constexpr char PENDULUM_ENV_FIELDS[] = "temperature_C,humidity_pct,pressure_hPa";
static constexpr size_t CANONICAL_SWING_CSV_FIELD_COUNT = 9 + PENDULUM_ENV_FIELD_COUNT;
static constexpr size_t CANONICAL_PPS_CSV_FIELD_COUNT = 8 + PENDULUM_ENV_FIELD_COUNT;
