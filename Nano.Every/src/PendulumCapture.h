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

// Keep this alias so ISR capture code has one local name; source of truth lives in Config.h.
constexpr uint8_t CAPTURE_EDGE_BUFFER_SIZE = RING_SIZE_IR_SENSOR;
constexpr uint8_t CAPTURE_PPS_RING_SIZE = RING_SIZE_PPS;

static_assert(CAPTURE_EDGE_BUFFER_SIZE > 0U &&
                  (CAPTURE_EDGE_BUFFER_SIZE & (CAPTURE_EDGE_BUFFER_SIZE - 1U)) == 0U,
              "CAPTURE_EDGE_BUFFER_SIZE must be a non-zero power-of-two for mask arithmetic");
static_assert(CAPTURE_EDGE_BUFFER_SIZE == (RING_SIZE_SWING_ROWS * 4U),
              "IR edge ring must remain 4x swing-row ring depth");


void captureMarkHardwareInitialized();
void captureResetAndReinit();
void captureResetState();

bool captureEdgeAvailable();
EdgeEvent capturePopEdge();

bool capturePpsAvailable();
PpsCapture capturePopPps();

// Usage contract for coherent TCB0 readers:
// - ISR-only helper exists privately in PendulumCapture.cpp and must not use ATOMIC_BLOCK.
// - Foreground/main-loop code must call the public *_MainLoop() / *64() APIs below.
// Foreground-safe coherent 32-bit read (uses ATOMIC_BLOCK internally).
uint32_t tcb0NowCoherentMainLoop();
// Foreground-safe coherent 64-bit read (uses ATOMIC_BLOCK internally).
uint64_t tcb0NowCoherent64();

uint32_t captureDroppedEvents();
uint32_t capturePpsSeen();
void captureRecordDroppedEvent();
