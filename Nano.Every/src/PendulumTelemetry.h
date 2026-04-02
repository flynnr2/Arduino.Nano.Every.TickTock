#pragma once

#include "Config.h"
#include "PpsValidator.h"
#include "FreqDiscipliner.h"

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
