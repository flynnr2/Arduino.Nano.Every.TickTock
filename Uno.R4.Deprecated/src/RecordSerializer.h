#pragma once

#include <stddef.h>
#include <stdint.h>

#include "PendulumSampleState.h"

namespace RecordSerializer {

constexpr size_t CANONICAL_SWING_RECORD_MAX_LEN = 256;
constexpr size_t CANONICAL_PPS_RECORD_MAX_LEN = 224;

bool serializeCanonicalSwing(const CanonicalSwingSample& sample,
                             float temperatureC,
                             float humidityPct,
                             float pressureHpa,
                             char* out,
                             size_t outSize,
                             size_t& outLen);

bool serializeCanonicalPps(const CanonicalPpsSample& sample,
                           float temperatureC,
                           float humidityPct,
                           float pressureHpa,
                           char* out,
                           size_t outSize,
                           size_t& outLen);

} // namespace RecordSerializer
