#pragma once

#include <stdint.h>

#include "Config.h"

struct EdgeEvent {
  uint32_t ticks;
  uint8_t  type;
};

struct PpsCapture {
  uint32_t seq;
  uint32_t edge32;
  uint32_t now32;
  uint16_t ovf;
  uint16_t cap16;
  uint16_t cnt;
  uint16_t latency16;
#if ENABLE_PROFILING && DUAL_PPS_PROFILING
  uint32_t rise_seq;
#endif
};

#if ENABLE_TCB_LATENCY_DIAG
enum TcbLatencyEdgeKind : uint8_t {
  TCB_LATENCY_EDGE_TICK = 0,
  TCB_LATENCY_EDGE_TOCK = 1,
  TCB_LATENCY_EDGE_PPS  = 2,
};

struct TcbLatencyTraceEvent {
  uint32_t seq_or_edge_index;
  uint32_t edge32;
  uint16_t cap16;
  uint16_t cnt16;
  uint16_t latency16;
  uint8_t tcb;
  uint8_t edge_kind;
};
#endif

#if ENABLE_PROFILING && DUAL_PPS_PROFILING
struct DualPpsTcb1RisingSnapshot {
  uint32_t rise_seq;
  uint32_t edge32;
  uint16_t cap16;
};

struct DualPpsProfilingCounters {
  uint32_t tcb1_rising_seen;
  uint32_t tcb2_rising_seen;
};
#endif

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

bool captureTryPopEdge(EdgeEvent* out);

bool captureTryPopPps(PpsCapture* out);
#if ENABLE_TCB_LATENCY_DIAG
bool captureTryPopTcbLatencyTrace(TcbLatencyTraceEvent* out);
uint32_t captureDroppedTcbLatencyTraceEvents();
#endif

// Usage contract for coherent TCB0 readers:
// - ISR-only helper exists privately in PendulumCapture.cpp and must not use ATOMIC_BLOCK.
// - Foreground/main-loop code must call the public *_MainLoop() / *64() APIs below.
// Foreground-safe coherent 32-bit read (uses ATOMIC_BLOCK internally).
uint32_t tcb0NowCoherentMainLoop();
// Foreground-safe coherent 64-bit read (uses ATOMIC_BLOCK internally).
uint64_t tcb0NowCoherent64();

uint32_t captureDroppedEvents();
uint32_t captureDroppedIrEvents();
uint32_t captureDroppedPpsEvents();
uint32_t captureDroppedSwingRows();
uint32_t capturePpsSeen();
void captureRecordDroppedEvent();
void captureRecordSwingRowDrop();

#if ENABLE_PROFILING && DUAL_PPS_PROFILING
bool captureReadDualPpsTcb1RisingSnapshot(DualPpsTcb1RisingSnapshot& out);
void captureReadDualPpsSeenCounters(DualPpsProfilingCounters& out);
#endif
