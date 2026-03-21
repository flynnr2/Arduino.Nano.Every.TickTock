#pragma once

#include <stdint.h>

#include "Config.h"

struct EdgeEvent {
  uint32_t ticks;
  uint8_t  type;
};

struct PpsCapture {
  uint32_t edge32;
  uint32_t now32;
  uint16_t ovf;
  uint16_t cap16;
  uint16_t cnt;
  uint16_t latency16;
};

constexpr uint8_t CAPTURE_EDGE_BUFFER_SIZE = 64;
constexpr uint8_t CAPTURE_PPS_RING_SIZE = RING_SIZE_PPS;

struct CaptureDiagnosticsSnapshot {
  uint32_t pps_seen;
  uint32_t dropped_events;
  uint32_t last_pps_edge_capture;
  uint32_t pps_isr_count;
  uint32_t tcb0_wrap_detected;
  uint32_t coherent_ovf_flag_seen_count;
  uint32_t coherent_ovf_applied_count;
  uint32_t pps_latency_sum;
  uint32_t pps_latency16_wrap_risk;
  uint16_t pps_latency_last;
  uint16_t pps_latency_min;
  uint16_t pps_latency_max;
  uint16_t pps_latency_count;
  uint16_t pps_latency16_max;
  uint16_t isr_last_tcb0_ticks;
  uint16_t isr_last_tcb1_ticks;
  uint16_t isr_last_tcb2_ticks;
  uint16_t max_isr_tcb0_ticks;
  uint16_t max_isr_tcb1_ticks;
  uint16_t max_isr_tcb2_ticks;
  uint16_t tcb0_ovf;
  uint8_t capt_pending;
};

void captureResetState();

bool captureEdgeAvailable();
EdgeEvent capturePopEdge();

bool capturePpsAvailable();
PpsCapture capturePopPps();

// Exposes the firmware's coherent TCB0 monotonic counters for platform timing.
uint32_t tcb0NowCoherent();
uint64_t tcb0NowCoherent64();

uint32_t captureDroppedEvents();
uint32_t capturePpsSeen();
void captureRecordDroppedEvent();
CaptureDiagnosticsSnapshot captureDiagnosticsSnapshot();
