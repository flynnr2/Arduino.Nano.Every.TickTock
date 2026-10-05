#include "ServiceTelemetry.h"

#include <Arduino.h>

namespace ServiceTelemetry {

static Snapshot metrics = {0};
static unsigned long lastLoopStartUs = 0;
static unsigned long lastNanoServiceUs = 0;
static bool nanoServiceSeen = false;

static void updateMax(unsigned long& target, unsigned long value) {
  if (value > target) {
    target = value;
  }
}

void noteLoopStart(unsigned long nowMicros, unsigned long nowMs) {
  if (lastLoopStartUs != 0) {
    const unsigned long intervalUs = nowMicros - lastLoopStartUs;
    metrics.last_loop_interval_us = intervalUs;
    updateMax(metrics.max_loop_interval_us, intervalUs);
  }
  lastLoopStartUs = nowMicros;
  metrics.loop_count++;
  (void)nowMs;
}


bool noteLoopGapEvent(unsigned long nowMs) {
  metrics.loop_gap_event_count++;
  metrics.last_loop_gap_event_ms = nowMs;
  return true;
}

void noteLoopEnd(unsigned long elapsedUs) {
  updateMax(metrics.max_loop_body_us, elapsedUs);
}

void noteNanoService(unsigned long elapsedUs, unsigned long nowMs) {
  metrics.nano_service_calls++;
  updateMax(metrics.nano_service_max_us, elapsedUs);
  const unsigned long startUs = micros() - elapsedUs;
  if (nanoServiceSeen) {
    const unsigned long gapUs = startUs - lastNanoServiceUs;
    updateMax(metrics.nano_service_max_gap_us, gapUs);
    updateMax(metrics.nano_service_max_gap_ms, gapUs / 1000UL);
  }
  lastNanoServiceUs = startUs;
  nanoServiceSeen = true;
  (void)nowMs;
}

void noteWiFiService(unsigned long elapsedUs) {
  metrics.wifi_service_calls++;
  updateMax(metrics.wifi_service_max_us, elapsedUs);
}

void noteHttpService(unsigned long elapsedUs) {
  metrics.http_service_calls++;
  updateMax(metrics.http_service_max_us, elapsedUs);
}

void noteSdService(unsigned long elapsedUs) {
  metrics.sd_service_calls++;
  updateMax(metrics.sd_service_max_us, elapsedUs);
}
void noteHttpClientService(unsigned long elapsedUs) {
  updateMax(metrics.http_client_max_us, elapsedUs);
}

void noteWiFiServiceSkipped() {
  metrics.wifi_service_skipped++;
}

void noteHttpServiceSkipped() {
  metrics.http_service_skipped++;
}

void noteWiFiStaAttempt() {
  metrics.wifi_sta_attempts++;
}

void noteWiFiStaResult(bool success, unsigned long durationMs) {
  metrics.wifi_sta_last_duration_ms = durationMs;
  updateMax(metrics.wifi_sta_max_duration_ms, durationMs);
  if (success) {
    metrics.wifi_sta_success++;
  } else {
    metrics.wifi_sta_failures++;
  }
}

void noteWiFiApStartAttempt() {
  metrics.wifi_ap_start_attempts++;
}

void noteWiFiApStartResult(bool success, unsigned long durationMs) {
  metrics.wifi_ap_last_duration_ms = durationMs;
  updateMax(metrics.wifi_ap_max_duration_ms, durationMs);
  if (success) {
    metrics.wifi_ap_start_success++;
  } else {
    metrics.wifi_ap_start_failures++;
  }
}

unsigned long nanoServiceMaxGapUs() { return metrics.nano_service_max_gap_us; }

Snapshot snapshot() {
  return metrics;
}

} // namespace ServiceTelemetry
