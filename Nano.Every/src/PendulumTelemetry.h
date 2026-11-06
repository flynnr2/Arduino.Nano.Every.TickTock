#pragma once

#include "Config.h"
#include "PpsValidator.h"
#include "FreqDiscipliner.h"
#include "DisciplinedTime.h"

#include <stdint.h>
#if ENABLE_TCB_LATENCY_DIAG
#include "PendulumCapture.h"
#endif

void emit_pps_baseline_telemetry(uint32_t seq,
                                 uint32_t now_ms,
                                 uint32_t dt32_ticks,
                                 PpsValidator::SampleClass cls,
                                 bool pps_valid,
                                 const FreqDiscipliner& discipliner,
                                 uint16_t latency16,
                                 uint16_t cap16);

void tune_push_sample(FreqDiscipliner::DiscState state,
                      PpsValidator::SampleClass cls,
                      bool pps_valid,
                      const FreqDiscipliner& discipliner);

void emit_tune_event(FreqDiscipliner::DiscState from,
                     FreqDiscipliner::DiscState to,
                     uint32_t now_ms,
                     const FreqDiscipliner& discipliner,
                     uint8_t streak);

void emit_metrology_mode_event(DisciplinedTime::ExportMode from,
                               DisciplinedTime::ExportMode to,
                               FreqDiscipliner::DiscState trust_state,
                               uint32_t now_ms,
                               uint32_t f_hat_hz,
                               uint32_t anchor_hz);

#if ENABLE_PROFILING && DUAL_PPS_PROFILING
void emit_dual_pps_edge_telemetry(uint32_t q,
                                  uint16_t tcb2_ccmp,
                                  uint16_t tcb1_ccmp,
                                  int32_t delta_ccmp,
                                  uint32_t tcb2_ext,
                                  uint32_t tcb1_ext,
                                  int32_t delta_ext);
#endif

#if ENABLE_TCB_LATENCY_DIAG
void emit_tcb_latency_trace_event(uint32_t now_ms, const TcbLatencyTraceEvent& event);
void emit_tcb_latency_summary(uint32_t now_ms,
                              uint32_t dropped,
                              uint32_t tick_n, uint16_t tick_last, uint16_t tick_min, uint16_t tick_max, uint32_t tick_spikes,
                              uint32_t tock_n, uint16_t tock_last, uint16_t tock_min, uint16_t tock_max, uint32_t tock_spikes,
                              uint32_t pps_n, uint16_t pps_last, uint16_t pps_min, uint16_t pps_max, uint32_t pps_spikes);
#endif
