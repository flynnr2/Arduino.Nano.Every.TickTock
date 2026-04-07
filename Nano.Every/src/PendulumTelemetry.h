#pragma once

#include "Config.h"
#include "PpsValidator.h"
#include "FreqDiscipliner.h"
#include "DisciplinedTime.h"

#include <stdint.h>

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
