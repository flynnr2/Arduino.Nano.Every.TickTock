#pragma once

#include "Config.h"

#include <stddef.h>
#include <stdint.h>

struct RestartBreadcrumbsPrevBoot {
  bool valid;
  bool prev_vlm_seen;
  uint32_t prev_last_ms;
  uint32_t prev_mainloop_hb;
  uint32_t prev_pps_isr_hb;
  // Same uptime-ms domain as prev_last_ms/prev_last_pps_processed_ms (PREV_BOOT pisr=...).
  // Latched at first foreground observation of new PPS ISR activity.
  uint32_t prev_last_pps_isr_change_ms;
  uint32_t prev_last_pps_processed_ms;
  uint32_t prev_last_good_pps_seq;
  uint32_t prev_last_good_edge_tcb0;
  uint32_t prev_last_good_now32;
  uint8_t prev_last_mclkstatus;
  uint8_t prev_last_loop_phase;
  uint8_t prev_flags;
  uint8_t prev_pps_diag;
};

enum RestartBreadcrumbFlag : uint8_t {
  RESTART_BC_FLAG_MCLK_UNEXPECTED = 0x01,
  RESTART_BC_FLAG_PPS_STALE = 0x02,
  RESTART_BC_FLAG_LOOP_GAP = 0x04,
  RESTART_BC_FLAG_PPS_FROZEN_ADV = 0x08,
};

enum RestartBreadcrumbLoopPhase : uint8_t {
  RESTART_BC_LOOP_PHASE_UNKNOWN = 0,
  RESTART_BC_LOOP_PHASE_LOOP_TOP = 1,
  RESTART_BC_LOOP_PHASE_SERIAL_COMMANDS = 2,
  RESTART_BC_LOOP_PHASE_PPS_PROCESS = 3,
  RESTART_BC_LOOP_PHASE_SWING_EDGE_SCAN = 4,
  RESTART_BC_LOOP_PHASE_SWING_POP_EMIT = 5,
  RESTART_BC_LOOP_PHASE_PERIODIC_TELEMETRY = 6,
};

#if ENABLE_RESTART_BREADCRUMBS
void restartBreadcrumbsInitAtBoot();
void restartBreadcrumbsMainloopTick(uint32_t now_ms);
void restartBreadcrumbsNotifyPpsIsrEdge(uint32_t observed_ms, uint32_t edge_count_delta);
void restartBreadcrumbsNotifyPpsProcessed(uint32_t now_ms);
void restartBreadcrumbsNotifyAcceptedPpsSample(uint32_t pps_seq, uint32_t edge_tcb0, uint32_t now32, uint32_t now_ms);
void restartBreadcrumbsSetFlag(uint8_t flag_mask);
void restartBreadcrumbsSetLoopPhase(uint8_t phase);
void restartBreadcrumbsInitVlmEarly();
bool restartBreadcrumbsVlmArmed();
void restartBreadcrumbsNotifyVlmEventFromIsr();
RestartBreadcrumbsPrevBoot restartBreadcrumbsPrevBootSnapshot();
bool restartBreadcrumbsFormatPrevBootLine(char* out, size_t out_len);
size_t restartBreadcrumbsRetainedSizeBytes();
#else
void restartBreadcrumbsInitAtBoot();
void restartBreadcrumbsMainloopTick(uint32_t now_ms);
void restartBreadcrumbsNotifyPpsIsrEdge(uint32_t observed_ms, uint32_t edge_count_delta);
void restartBreadcrumbsNotifyPpsProcessed(uint32_t now_ms);
void restartBreadcrumbsNotifyAcceptedPpsSample(uint32_t pps_seq, uint32_t edge_tcb0, uint32_t now32, uint32_t now_ms);
void restartBreadcrumbsSetFlag(uint8_t flag_mask);
void restartBreadcrumbsSetLoopPhase(uint8_t phase);
void restartBreadcrumbsInitVlmEarly();
bool restartBreadcrumbsVlmArmed();
void restartBreadcrumbsNotifyVlmEventFromIsr();
RestartBreadcrumbsPrevBoot restartBreadcrumbsPrevBootSnapshot();
bool restartBreadcrumbsFormatPrevBootLine(char* out, size_t out_len);
size_t restartBreadcrumbsRetainedSizeBytes();
#endif
