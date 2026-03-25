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


void captureMarkHardwareInitialized();
void captureResetAndReinit();
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
