#include "PendulumTelemetry.h"

#include "PendulumProtocol.h"
#include "SerialParser.h"

#include <stdio.h>

namespace {

char* telemetryEmitLineBuf() {
  return tryAcquireFormatBuffer(FormatBufferOwner::PendulumCore);
}

void telemetryReleaseLineBuf() {
  releaseFormatBuffer(FormatBufferOwner::PendulumCore);
}

#if ENABLE_PPS_BASELINE_TELEMETRY
uint8_t pps_class_compact_code(PpsValidator::SampleClass cls) {
  switch (cls) {
    case PpsValidator::SampleClass::OK: return 0;
    case PpsValidator::SampleClass::GAP: return 1;
    case PpsValidator::SampleClass::DUP: return 2;
    case PpsValidator::SampleClass::HARD_GLITCH: return 3;
    default: return 2;
  }
}
#endif

#if PPS_TUNING_TELEMETRY
struct TuneWindowWorkspace {
  uint32_t r_samples[PPS_TUNE_WIN_SIZE] = {};
  uint32_t js_samples[PPS_TUNE_WIN_SIZE] = {};
  uint32_t ja_samples[PPS_TUNE_WIN_SIZE] = {};
  uint8_t win_fill = 0;
  uint8_t ok_count = 0;
  uint8_t val_count = 0;
  uint8_t lock_pass_count = 0;
  uint8_t unlock_breach_count = 0;
};

TuneWindowWorkspace gTuneWindow;
constexpr size_t kTuneWindowWorkspaceRawSize =
    sizeof(uint32_t) * 3U * PPS_TUNE_WIN_SIZE + sizeof(uint8_t) * 5U;
constexpr size_t kTuneWindowWorkspaceExpectedSize =
    ((kTuneWindowWorkspaceRawSize + alignof(TuneWindowWorkspace) - 1U) /
     alignof(TuneWindowWorkspace)) *
    alignof(TuneWindowWorkspace);
static_assert(sizeof(TuneWindowWorkspace) == kTuneWindowWorkspaceExpectedSize,
              "TuneWindowWorkspace size changed; revisit PPS tuning telemetry SRAM budget");

const char* tune_state_name(FreqDiscipliner::DiscState s) {
  switch (s) {
    case FreqDiscipliner::DiscState::ACQUIRE: return "ACQUIRE";
    case FreqDiscipliner::DiscState::DISCIPLINED: return "DISCIPLINED";
    case FreqDiscipliner::DiscState::HOLDOVER: return "HOLDOVER";
    default: return "FREE_RUN";
  }
}

void swap_u32(uint32_t& a, uint32_t& b) {
  const uint32_t tmp = a;
  a = b;
  b = tmp;
}

uint32_t select_kth_u32(uint32_t* values, uint8_t n, uint8_t k) {
  uint8_t left = 0;
  uint8_t right = (uint8_t)(n - 1U);

  while (left < right) {
    const uint8_t pivot_index = (uint8_t)(left + ((right - left) >> 1));
    const uint32_t pivot = values[pivot_index];
    swap_u32(values[pivot_index], values[right]);

    uint8_t store = left;
    for (uint8_t i = left; i < right; ++i) {
      if (values[i] < pivot) {
        swap_u32(values[store], values[i]);
        ++store;
      }
    }

    swap_u32(values[right], values[store]);

    if (k == store) return values[store];
    if (k < store) {
      right = (uint8_t)(store - 1U);
    } else {
      left = (uint8_t)(store + 1U);
    }
  }

  return values[left];
}

uint32_t percentile_u32_exact(uint32_t* source, uint8_t n, uint8_t percentile) {
  const uint8_t idx = (uint8_t)(((uint16_t)(n - 1U) * percentile + 99U) / 100U);
  return select_kth_u32(source, n, idx);
}

void emit_tune_window(FreqDiscipliner::DiscState state) {
  if (gTuneWindow.win_fill == 0U) return;

  uint32_t r_max = gTuneWindow.r_samples[0];
  uint32_t js_max = gTuneWindow.js_samples[0];
  uint32_t ja_max = gTuneWindow.ja_samples[0];

  for (uint8_t i = 0; i < gTuneWindow.win_fill; ++i) {
    const uint32_t rv = gTuneWindow.r_samples[i];
    const uint32_t jsv = gTuneWindow.js_samples[i];
    const uint32_t jav = gTuneWindow.ja_samples[i];
    if (rv > r_max) r_max = rv;
    if (jsv > js_max) js_max = jsv;
    if (jav > ja_max) ja_max = jav;
  }

  const uint32_t r95 = percentile_u32_exact(gTuneWindow.r_samples, gTuneWindow.win_fill, 95U);
  const uint32_t js95 = percentile_u32_exact(gTuneWindow.js_samples, gTuneWindow.win_fill, 95U);
  const uint32_t ja95 = percentile_u32_exact(gTuneWindow.ja_samples, gTuneWindow.win_fill, 95U);

  char* line = telemetryEmitLineBuf();
  if (!line) return;
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "TUNE_WIN,state=%s,win=%u,ok=%u,val=%u,R95=%lu,Rmax=%lu,JS95=%lu,JSmax=%lu,JA95=%lu,JAmax=%lu,LP=%u,UB=%u",
                         tune_state_name(state),
                         (unsigned int)gTuneWindow.win_fill,
                         (unsigned int)gTuneWindow.ok_count,
                         (unsigned int)gTuneWindow.val_count,
                         (unsigned long)r95,
                         (unsigned long)r_max,
                         (unsigned long)js95,
                         (unsigned long)js_max,
                         (unsigned long)ja95,
                         (unsigned long)ja_max,
                         (unsigned int)gTuneWindow.lock_pass_count,
                         (unsigned int)gTuneWindow.unlock_breach_count);
  if (n > 0) {
    sendStatusFromOwnedBuffer(FormatBufferOwner::PendulumCore, StatusCode::ProgressUpdate, line);
  }
  telemetryReleaseLineBuf();

  gTuneWindow.win_fill = 0;
  gTuneWindow.ok_count = 0;
  gTuneWindow.val_count = 0;
  gTuneWindow.lock_pass_count = 0;
  gTuneWindow.unlock_breach_count = 0;
}
#endif

}  // namespace

void emit_pps_baseline_telemetry(uint32_t seq,
                                 uint32_t now_ms,
                                 uint32_t dt32_ticks,
                                 PpsValidator::SampleClass cls,
                                 bool pps_valid,
                                 const FreqDiscipliner& discipliner,
                                 uint16_t latency16,
                                 uint16_t cap16) {
#if ENABLE_PPS_BASELINE_TELEMETRY
  static constexpr uint16_t PPS_BASELINE_SAFE_PAYLOAD_MAX = 220;

  char* line = telemetryEmitLineBuf();
  if (!line) return;
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "PPS_BASE,q=%lu,m=%lu,d=%lu,ef=%ld,es=%ld,eh=%ld,pf=%lu,ps=%lu,pa=%lu,c=%u,v=%u,s=%u,r=%lu,js=%lu,ja=%lu,lg=%u,ub=%u,l=%u,cp=%u",
                         (unsigned long)seq,
                         (unsigned long)now_ms,
                         (unsigned long)dt32_ticks,
                         (long)discipliner.fastErrTicks(),
                         (long)discipliner.slowErrTicks(),
                         (long)discipliner.appliedErrTicks(),
                         (unsigned long)discipliner.fastErrPpm(),
                         (unsigned long)discipliner.slowErrPpm(),
                         (unsigned long)discipliner.appliedErrPpm(),
                         (unsigned int)pps_class_compact_code(cls),
                         (unsigned int)(pps_valid ? 1U : 0U),
                         (unsigned int)discipliner.state(),
                         (unsigned long)discipliner.rPpm(),
                         (unsigned long)discipliner.slowMadTicks(),
                         (unsigned long)discipliner.appliedMadTicks(),
                         (unsigned int)discipliner.lockPassMask(),
                         (unsigned int)discipliner.unlockBreachMask(),
                         (unsigned int)latency16,
                         (unsigned int)cap16);
  if (n > 0 && n <= (int)PPS_BASELINE_SAFE_PAYLOAD_MAX) {
    sendStatusFromOwnedBuffer(FormatBufferOwner::PendulumCore, StatusCode::ProgressUpdate, line);
  }
  telemetryReleaseLineBuf();
#else
  (void)seq;
  (void)now_ms;
  (void)dt32_ticks;
  (void)cls;
  (void)pps_valid;
  (void)discipliner;
  (void)latency16;
  (void)cap16;
#endif
}

void tune_push_sample(FreqDiscipliner::DiscState state,
                      PpsValidator::SampleClass cls,
                      bool pps_valid,
                      const FreqDiscipliner& discipliner) {
#if PPS_TUNING_TELEMETRY
  const uint32_t r_ppm = discipliner.rPpm();
  gTuneWindow.r_samples[gTuneWindow.win_fill] = r_ppm;
  gTuneWindow.js_samples[gTuneWindow.win_fill] = discipliner.slowMadTicks();
  gTuneWindow.ja_samples[gTuneWindow.win_fill] = discipliner.appliedMadTicks();
  gTuneWindow.win_fill++;

  if (cls == PpsValidator::SampleClass::OK) gTuneWindow.ok_count++;
  if (pps_valid) gTuneWindow.val_count++;

  const bool lock_pass =
      ((discipliner.lockPassMask() & FreqDiscipliner::lockPassRequiredMask()) ==
       FreqDiscipliner::lockPassRequiredMask()) &&
      (discipliner.madTicks() < FreqDiscipliner::lockMadThresholdTicks());
  if (lock_pass) gTuneWindow.lock_pass_count++;

  const bool unlock_breach = (discipliner.unlockBreachMask() != 0U);
  if (unlock_breach) gTuneWindow.unlock_breach_count++;

  if (gTuneWindow.win_fill >= PPS_TUNE_WIN_SIZE) emit_tune_window(state);
#else
  (void)state;
  (void)cls;
  (void)pps_valid;
  (void)discipliner;
#endif
}

void emit_tune_event(FreqDiscipliner::DiscState from,
                     FreqDiscipliner::DiscState to,
                     uint32_t now_ms,
                     const FreqDiscipliner& discipliner,
                     uint8_t streak) {
#if PPS_TUNING_TELEMETRY
  const uint8_t lock_mask = discipliner.lockPassMask();
  const uint8_t unlock_mask = discipliner.unlockBreachMask();
  char* line = telemetryEmitLineBuf();
  if (!line) return;
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "TUNE_EVT,from=%s,to=%s,t=%lu,R=%lu,es=%ld,ea=%ld,ps=%lu,pa=%lu,js=%lu,ja=%lu,lfg=%u,lse=%u,lsm=%u,lae=%u,lam=%u,lan=%u,uae=%u,uam=%u,use=%u,usm=%u,urg=%u,uan=%u,streak=%u",
                         tune_state_name(from),
                         tune_state_name(to),
                         (unsigned long)(now_ms / 1000UL),
                         (unsigned long)discipliner.rPpm(),
                         (long)discipliner.slowErrTicks(),
                         (long)discipliner.appliedErrTicks(),
                         (unsigned long)discipliner.slowErrPpm(),
                         (unsigned long)discipliner.appliedErrPpm(),
                         (unsigned long)discipliner.slowMadTicks(),
                         (unsigned long)discipliner.appliedMadTicks(),
                         (unsigned int)((lock_mask & (1U << 4)) ? 1U : 0U),
                         (unsigned int)((lock_mask & (1U << 2)) ? 1U : 0U),
                         (unsigned int)((lock_mask & (1U << 3)) ? 1U : 0U),
                         (unsigned int)((lock_mask & (1U << 0)) ? 1U : 0U),
                         (unsigned int)((lock_mask & (1U << 1)) ? 1U : 0U),
                         (unsigned int)((lock_mask & (1U << 5)) ? 1U : 0U),
                         (unsigned int)((unlock_mask & (1U << 0)) ? 1U : 0U),
                         (unsigned int)((unlock_mask & (1U << 1)) ? 1U : 0U),
                         (unsigned int)((unlock_mask & (1U << 2)) ? 1U : 0U),
                         (unsigned int)((unlock_mask & (1U << 3)) ? 1U : 0U),
                         (unsigned int)((unlock_mask & (1U << 4)) ? 1U : 0U),
                         (unsigned int)((unlock_mask & (1U << 5)) ? 1U : 0U),
                         (unsigned int)streak);
  if (n > 0) {
    sendStatusFromOwnedBuffer(FormatBufferOwner::PendulumCore, StatusCode::ProgressUpdate, line);
  }
  telemetryReleaseLineBuf();
#else
  (void)from;
  (void)to;
  (void)now_ms;
  (void)discipliner;
  (void)streak;
#endif
}

void emit_metrology_mode_event(DisciplinedTime::ExportMode from,
                               DisciplinedTime::ExportMode to,
                               FreqDiscipliner::DiscState trust_state,
                               uint32_t now_ms,
                               uint32_t f_hat_hz,
                               uint32_t anchor_hz) {
#if PPS_TUNING_TELEMETRY
  char* line = telemetryEmitLineBuf();
  if (!line) return;
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "METRO_MODE_EVT,from=%s,to=%s,trust=%s,t=%lu,f_hat=%lu,anchor=%lu",
                         DisciplinedTime::exportModeName(from),
                         DisciplinedTime::exportModeName(to),
                         tune_state_name(trust_state),
                         (unsigned long)(now_ms / 1000UL),
                         (unsigned long)f_hat_hz,
                         (unsigned long)anchor_hz);
  if (n > 0) {
    sendStatusFromOwnedBuffer(FormatBufferOwner::PendulumCore, StatusCode::ProgressUpdate, line);
  }
  telemetryReleaseLineBuf();
#else
  (void)from;
  (void)to;
  (void)trust_state;
  (void)now_ms;
  (void)f_hat_hz;
  (void)anchor_hz;
#endif
}

#if ENABLE_PROFILING && DUAL_PPS_PROFILING
void emit_dual_pps_edge_telemetry(uint32_t q,
                                  uint16_t tcb2_ccmp,
                                  uint16_t tcb1_ccmp,
                                  int32_t delta_ccmp,
                                  uint32_t tcb2_ext,
                                  uint32_t tcb1_ext,
                                  int32_t delta_ext) {
  char* line = telemetryEmitLineBuf();
  if (!line) return;
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "DUAL_PPS_EDGE,q=%lu,tcb2_ccmp=%u,tcb1_ccmp=%u,delta_ccmp=%ld,tcb2_ext=%lu,tcb1_ext=%lu,delta_ext=%ld",
                         (unsigned long)q,
                         (unsigned int)tcb2_ccmp,
                         (unsigned int)tcb1_ccmp,
                         (long)delta_ccmp,
                         (unsigned long)tcb2_ext,
                         (unsigned long)tcb1_ext,
                         (long)delta_ext);
  if (n > 0) {
    sendStatusFromOwnedBuffer(FormatBufferOwner::PendulumCore, StatusCode::ProgressUpdate, line);
  }
  telemetryReleaseLineBuf();
}
#endif
