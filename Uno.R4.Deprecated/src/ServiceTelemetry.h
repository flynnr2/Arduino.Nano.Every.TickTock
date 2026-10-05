#pragma once

#include <stdint.h>

namespace ServiceTelemetry {

struct Snapshot {
  unsigned long loop_count;
  unsigned long max_loop_interval_us;
  unsigned long last_loop_interval_us;
  unsigned long loop_gap_event_count;
  unsigned long last_loop_gap_event_ms;
  unsigned long max_loop_body_us;
  unsigned long nano_service_calls;
  unsigned long nano_service_max_us;
  unsigned long nano_service_max_gap_ms;
  unsigned long nano_service_max_gap_us;
  unsigned long wifi_service_calls;
  unsigned long wifi_service_max_us;
  unsigned long wifi_service_skipped;
  unsigned long wifi_sta_attempts;
  unsigned long wifi_sta_success;
  unsigned long wifi_sta_failures;
  unsigned long wifi_sta_last_duration_ms;
  unsigned long wifi_sta_max_duration_ms;
  unsigned long wifi_ap_start_attempts;
  unsigned long wifi_ap_start_success;
  unsigned long wifi_ap_start_failures;
  unsigned long wifi_ap_last_duration_ms;
  unsigned long wifi_ap_max_duration_ms;
  unsigned long http_service_calls;
  unsigned long http_service_max_us;
  unsigned long http_service_skipped;
  unsigned long sd_service_calls;
  unsigned long sd_service_max_us;
  unsigned long http_client_max_us;
};

void noteLoopStart(unsigned long nowMicros, unsigned long nowMs);
bool noteLoopGapEvent(unsigned long nowMs);
void noteLoopEnd(unsigned long elapsedUs);
void noteNanoService(unsigned long elapsedUs, unsigned long nowMs);
void noteWiFiService(unsigned long elapsedUs);
void noteHttpService(unsigned long elapsedUs);
void noteSdService(unsigned long elapsedUs);
void noteHttpClientService(unsigned long elapsedUs);
void noteWiFiServiceSkipped();
void noteHttpServiceSkipped();
void noteWiFiStaAttempt();
void noteWiFiStaResult(bool success, unsigned long durationMs);
void noteWiFiApStartAttempt();
void noteWiFiApStartResult(bool success, unsigned long durationMs);
Snapshot snapshot();
unsigned long nanoServiceMaxGapUs();

} // namespace ServiceTelemetry
