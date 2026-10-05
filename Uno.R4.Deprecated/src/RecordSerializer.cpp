#include "RecordSerializer.h"

#include <stdarg.h>
#include <stdio.h>

namespace RecordSerializer {

static bool formatRow(char* out, size_t outSize, size_t& outLen, const char* fmt, ...) {
  if (!out || outSize == 0 || !fmt) return false;

  va_list args;
  va_start(args, fmt);
  const int written = vsnprintf(out, outSize, fmt, args);
  va_end(args);

  if (written <= 0 || static_cast<size_t>(written) >= outSize) {
    outLen = 0;
    if (outSize > 0) out[0] = '\0';
    return false;
  }

  outLen = static_cast<size_t>(written);
  return true;
}

bool serializeCanonicalSwing(const CanonicalSwingSample& sample,
                             float temperatureC,
                             float humidityPct,
                             float pressureHpa,
                             char* out,
                             size_t outSize,
                             size_t& outLen) {
  return formatRow(out,
                   outSize,
                   outLen,
                   "%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%.2f,%.2f,%.2f\n",
                   static_cast<unsigned long>(sample.seq),
                   static_cast<unsigned long>(sample.edge0_tcb0),
                   static_cast<unsigned long>(sample.edge1_tcb0),
                   static_cast<unsigned long>(sample.edge2_tcb0),
                   static_cast<unsigned long>(sample.edge3_tcb0),
                   static_cast<unsigned long>(sample.edge4_tcb0),
                   static_cast<unsigned long>(sample.drop_ir),
                   static_cast<unsigned long>(sample.drop_pps),
                   static_cast<unsigned long>(sample.drop_swing),
                   temperatureC,
                   humidityPct,
                   pressureHpa);
}

bool serializeCanonicalPps(const CanonicalPpsSample& sample,
                           float temperatureC,
                           float humidityPct,
                           float pressureHpa,
                           char* out,
                           size_t outSize,
                           size_t& outLen) {
  return formatRow(out,
                   outSize,
                   outLen,
                   "%lu,%lu,%u,%lu,%u,%u,%lu,%lu,%.2f,%.2f,%.2f\n",
                   static_cast<unsigned long>(sample.seq),
                   static_cast<unsigned long>(sample.edge_tcb0),
                   static_cast<unsigned int>(sample.gps_status),
                   static_cast<unsigned long>(sample.holdover_age_ms),
                   static_cast<unsigned int>(sample.cap16),
                   static_cast<unsigned int>(sample.latency16),
                   static_cast<unsigned long>(sample.now32),
                   static_cast<unsigned long>(sample.drop_pps),
                   temperatureC,
                   humidityPct,
                   pressureHpa);
}

} // namespace RecordSerializer
