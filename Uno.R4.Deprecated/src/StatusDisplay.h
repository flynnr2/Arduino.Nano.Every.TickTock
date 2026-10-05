#pragma once

#include <Arduino.h>

#include "Config.h"

enum class LedFaultCode : uint8_t { None = 0, Sd, Nano, Backlog, Pps, Wifi, Upload, Count };
enum class LedHealthState : uint8_t { Disabled = 0, Good, Degraded, Fault };

struct LedStatusSnapshot {
  LedHealthState nano;
  LedHealthState sd;
  LedHealthState pps;
  LedHealthState wifi;
  LedHealthState upload;
  uint8_t backlog_pct;
  bool row_logged_pulse;
  uint32_t last_logged_row_ms;
  bool sd_fault_active;
  bool nano_fault_active;
  bool backlog_fault_active;
  bool pps_fault_active;
  bool wifi_fault_active;
  bool upload_fault_active;
};

void statusDisplayBegin();
LedStatusSnapshot buildLedStatusSnapshot(uint32_t now_ms);
void statusDisplayNoteLoggedRow(uint32_t now_ms);
void statusDisplayService(const LedStatusSnapshot& snapshot, uint32_t now_ms);
