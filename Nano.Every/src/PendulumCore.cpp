#include "Config.h"

#include <Arduino.h>
#include "PendulumProtocol.h"
#include "SerialParser.h"
#include "EEPROMConfig.h"
#include "PendulumCore.h"
#include "PendulumCapture.h"
#include "SwingAssembler.h"
#include "StatusTelemetry.h"
#include "CaptureInit.h"
#include "PpsValidator.h"
#include "FreqDiscipliner.h"
#include "DisciplinedTime.h"
#include "PlatformTime.h"
#include "PpsFreshness.h"
#include "PpsAdjust.h"
#include "MemoryTelemetry.h"

static_assert(sizeof(uint16_t) == 2, "Expected 16-bit capture modulo domain");
static_assert(sizeof(uint32_t) == 4, "Expected 32-bit PPS tick domain");

static inline uint32_t elapsed32(uint32_t now, uint32_t then) {
  return (uint32_t)(now - then);
}

volatile uint32_t lastPpsCapture             = 0;        // last PPS capture tick count

GpsStatus gpsStatus = GpsStatus::NO_PPS;

static PpsValidator gPpsValidator;
static FreqDiscipliner gFreqDiscipliner;
static DisciplinedTime gDisciplinedTime;

static uint32_t pps_delta_inst = (uint32_t)MAIN_CLOCK_HZ;
// Cached active PPS interval used by shared fast/slow consumers.
static uint64_t pps_delta_active = (uint32_t)MAIN_CLOCK_HZ;

// Quality metrics
static uint32_t pps_R_ppm = 0;   // |fast - slow| / slow in ppm
static uint32_t pps_J_ticks = 0; // robust jitter metric as MAD residual ticks

#if ENABLE_PPS_BASELINE_TELEMETRY
static inline uint8_t pps_class_compact_code(PpsValidator::SampleClass cls) {
  switch (cls) {
    case PpsValidator::SampleClass::OK: return 0;
    case PpsValidator::SampleClass::GAP: return 1;
    case PpsValidator::SampleClass::DUP: return 2;
    case PpsValidator::SampleClass::HARD_GLITCH: return 3;
    default: return 2;
  }
}

static void emit_pps_baseline_telemetry(uint32_t seq,
                                        uint32_t now_ms,
                                        uint32_t dt32_ticks,
                                        PpsValidator::SampleClass cls,
                                        bool pps_valid,
                                        const FreqDiscipliner& discipliner,
                                        uint16_t latency16,
                                        uint16_t cap16) {
  // Compact baseline PPS schema for long PPS-only runs (telemetry-only; compile-time gated):
  // PPS_BASE,q,m,d,ef,es,eh,pf,ps,pa,c,v,s,r,js,ja,lg,ub,l,cp
  // (all integer key=value fields).
  // Keep comfortably below STS payload limits; skip send if the formatted payload exceeds this guard.
  static constexpr uint16_t PPS_BASELINE_SAFE_PAYLOAD_MAX = 220;

  char line[CSV_PAYLOAD_MAX];
  const int n = snprintf(line,
                         sizeof(line),
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
    sendStatus(StatusCode::ProgressUpdate, line);
  }
}
#endif


// 16-bit wrap-safe subtract
static inline uint16_t sub16(uint16_t a, uint16_t b) { return (uint16_t)(a - b); }


#if PPS_TUNING_TELEMETRY
static constexpr uint8_t TUNE_WIN_SIZE = 32;
static uint32_t tune_r_samples[TUNE_WIN_SIZE] = {};
static uint32_t tune_js_samples[TUNE_WIN_SIZE] = {};
static uint32_t tune_ja_samples[TUNE_WIN_SIZE] = {};
// Keep sort scratch buffers off AVR stack to preserve headroom in telemetry paths.
static uint32_t tune_r_sorted[TUNE_WIN_SIZE];
static uint32_t tune_js_sorted[TUNE_WIN_SIZE];
static uint32_t tune_ja_sorted[TUNE_WIN_SIZE];
static uint8_t tune_win_fill = 0;
static uint8_t tune_ok_count = 0;
static uint8_t tune_val_count = 0;
static uint8_t tune_lock_pass_count = 0;
static uint8_t tune_unlock_breach_count = 0;

static inline const char* tune_state_name(FreqDiscipliner::DiscState s) {
  switch (s) {
    case FreqDiscipliner::DiscState::ACQUIRE: return "ACQUIRE";
    case FreqDiscipliner::DiscState::DISCIPLINED: return "DISCIPLINED";
    case FreqDiscipliner::DiscState::HOLDOVER: return "HOLDOVER";
    default: return "FREE_RUN";
  }
}

static uint32_t percentile_u32(uint32_t* values, uint8_t n, uint8_t percentile) {
  for (uint8_t i = 1; i < n; i++) {
    const uint32_t v = values[i];
    uint8_t j = i;
    while (j > 0 && values[j - 1] > v) {
      values[j] = values[j - 1];
      j--;
    }
    values[j] = v;
  }
  const uint8_t idx = (uint8_t)(((uint16_t)(n - 1U) * percentile + 99U) / 100U);
  return values[idx];
}

static void emit_tune_window(FreqDiscipliner::DiscState state) {
  if (tune_win_fill == 0U) return;

  uint32_t r_max = tune_r_samples[0];
  uint32_t js_max = tune_js_samples[0];
  uint32_t ja_max = tune_ja_samples[0];

  for (uint8_t i = 0; i < tune_win_fill; ++i) {
    const uint32_t rv = tune_r_samples[i];
    const uint32_t jsv = tune_js_samples[i];
    const uint32_t jav = tune_ja_samples[i];
    tune_r_sorted[i] = rv;
    tune_js_sorted[i] = jsv;
    tune_ja_sorted[i] = jav;
    if (rv > r_max) r_max = rv;
    if (jsv > js_max) js_max = jsv;
    if (jav > ja_max) ja_max = jav;
  }

  const uint32_t r95 = percentile_u32(tune_r_sorted, tune_win_fill, 95U);
  const uint32_t js95 = percentile_u32(tune_js_sorted, tune_win_fill, 95U);
  const uint32_t ja95 = percentile_u32(tune_ja_sorted, tune_win_fill, 95U);

  char line[CSV_PAYLOAD_MAX];
  const int n = snprintf(line,
                         sizeof(line),
                         "TUNE_WIN,state=%s,win=%u,ok=%u,val=%u,R95=%lu,Rmax=%lu,JS95=%lu,JSmax=%lu,JA95=%lu,JAmax=%lu,LP=%u,UB=%u",
                         tune_state_name(state),
                         (unsigned int)tune_win_fill,
                         (unsigned int)tune_ok_count,
                         (unsigned int)tune_val_count,
                         (unsigned long)r95,
                         (unsigned long)r_max,
                         (unsigned long)js95,
                         (unsigned long)js_max,
                         (unsigned long)ja95,
                         (unsigned long)ja_max,
                         (unsigned int)tune_lock_pass_count,
                         (unsigned int)tune_unlock_breach_count);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);

  tune_win_fill = 0;
  tune_ok_count = 0;
  tune_val_count = 0;
  tune_lock_pass_count = 0;
  tune_unlock_breach_count = 0;
}

static void tune_push_sample(FreqDiscipliner::DiscState state,
                             PpsValidator::SampleClass cls,
                             bool pps_valid,
                             const FreqDiscipliner& discipliner) {
  const uint32_t r_ppm = discipliner.rPpm();
  tune_r_samples[tune_win_fill] = r_ppm;
  tune_js_samples[tune_win_fill] = discipliner.slowMadTicks();
  tune_ja_samples[tune_win_fill] = discipliner.appliedMadTicks();
  tune_win_fill++;

  if (cls == PpsValidator::SampleClass::OK) tune_ok_count++;
  if (pps_valid) tune_val_count++;

  const bool lock_pass =
      ((discipliner.lockPassMask() & FreqDiscipliner::lockPassRequiredMask()) ==
       FreqDiscipliner::lockPassRequiredMask()) &&
      (discipliner.madTicks() < FreqDiscipliner::lockMadThresholdTicks());
  if (lock_pass) tune_lock_pass_count++;

  const bool unlock_breach = (discipliner.unlockBreachMask() != 0U);
  if (unlock_breach) tune_unlock_breach_count++;

  if (tune_win_fill >= TUNE_WIN_SIZE) emit_tune_window(state);
}

static void emit_tune_event(FreqDiscipliner::DiscState from,
                            FreqDiscipliner::DiscState to,
                            uint32_t now_ms,
                            const FreqDiscipliner& discipliner,
                            uint8_t streak) {
  const uint8_t lock_mask = discipliner.lockPassMask();
  const uint8_t unlock_mask = discipliner.unlockBreachMask();
  char line[CSV_PAYLOAD_MAX];
  const int n = snprintf(line,
                         sizeof(line),
                         "TUNE_EVT,from=%s,to=%s,t=%lu,R=%lu,es=%ld,ea=%ld,ps=%lu,pa=%lu,js=%lu,ja=%lu,lfg=%u,lse=%u,lsm=%u,lae=%u,lam=%u,lan=%u,uae=%u,uam=%u,uan=%u,streak=%u",
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
                         (unsigned int)((unlock_mask & (1U << 5)) ? 1U : 0U),
                         (unsigned int)streak);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}
#endif

static bool metadata_reemit_pending = false;
static bool startup_output_ready = false;
static bool pps_primed = false;
static uint32_t pps_seen_prev = 0;
static uint32_t last_pps_isr_change_ms = 0;
static uint32_t last_pps_processed_ms = 0;
static uint32_t pps_last_edge32 = 0;
static uint16_t lastPpsCap16 = 0;
static uint32_t last_mem_telemetry_ms = 0;
#if ENABLE_PPS_BASELINE_TELEMETRY
static uint32_t pps_base_seq = 0;
#endif

void resetRuntimeStateAfterTunablesChange() {
  gpsStatus = GpsStatus::NO_PPS;
  captureResetAndReinit();
  gPpsValidator.reset();
  gFreqDiscipliner.reset((uint32_t)MAIN_CLOCK_HZ);
  gDisciplinedTime.begin((uint32_t)MAIN_CLOCK_HZ);
  pps_delta_inst = (uint32_t)MAIN_CLOCK_HZ;
  pps_delta_active = (uint32_t)MAIN_CLOCK_HZ;
  pps_R_ppm = 0;
  pps_J_ticks = 0;
  lastPpsCapture = 0;
  pps_primed = false;
  pps_seen_prev = 0;
  last_pps_isr_change_ms = 0;
  last_pps_processed_ms = 0;
  pps_last_edge32 = 0;
  lastPpsCap16 = 0;
  startup_output_ready = false;
  last_mem_telemetry_ms = 0;
  ppsAdjustReset((uint32_t)MAIN_CLOCK_HZ);
#if ENABLE_PPS_BASELINE_TELEMETRY
  pps_base_seq = 0;
#endif
}

void emitMetadataNow() {
  if (!startup_output_ready) {
    // Queue metadata until startup emission has completed.
    metadata_reemit_pending = true;
    return;
  }
  // Recovery/startup metadata must be deterministic and minimal for parser lock.
  // Emit only the startup contract in strict order:
  //   1) CFG metadata row
  //   2) HDR literal sample schema
  emitStatusSampleConfig();
  printCsvHeader();
}

void emitStartupNow() {
  if (!startup_output_ready) {
    // Defer parser-recovery metadata until startup emission has completed.
    metadata_reemit_pending = true;
    return;
  }
  emitResetCause();
  emitStatusBootHeaders();
  printCsvHeader();
}

void pendulumSetup() {
  latchResetCauseOnceAtBoot();
  advanceBootSequenceForBoot();

  pinMode(ledPin, OUTPUT);

  gpsStatus = GpsStatus::NO_PPS;
  DATA_SERIAL.begin(SERIAL_BAUD_NANO);
  if (&CMD_SERIAL != &DATA_SERIAL) {
    CMD_SERIAL.begin(SERIAL_BAUD_NANO);
  }

  TunableConfig cfg = {};
  if (loadConfig(cfg)) {
    applyConfig(cfg);
  }

  resetRuntimeStateAfterTunablesChange();
#if ENABLE_MEMORY_TELEMETRY_STS
  memoryTelemetryInitAtBoot();
#endif

  metadata_reemit_pending = true;
#if PPS_TUNING_TELEMETRY
  // Startup snapshot should not be emitted until startup_output_ready is set.
#endif
  cli();
  evsys_init();
  tcb0_init_free_running();
  tcb1_init_IR_capt();
  tcb2_init_PPS_capt();
  captureMarkHardwareInitialized();
  sei();

  platformDelayMs((uint32_t)STARTUP_SERIAL_SETTLE_MS);
  startup_output_ready = true;

  emitResetCauseOncePerBoot();
  sendStatus(StatusCode::ProgressUpdate, "Begin setup() ...");
#if ENABLE_MEMORY_TELEMETRY_STS
  memoryTelemetrySample();
#endif
  emitStatusBootHeaders();
#if PPS_TUNING_TELEMETRY
  emitPpsTuningConfigSnapshot();
#endif
  sendStatus(StatusCode::ProgressUpdate, "... end setup()");
  printCsvHeader();
  metadata_reemit_pending = true;
}

static void process_pps() {
  const uint32_t now_ms = platformMillis();
  const uint32_t pps_seen_now = capturePpsSeen();
  if (pps_seen_now != pps_seen_prev) {
    pps_seen_prev = pps_seen_now;
    last_pps_isr_change_ms = now_ms;
  }

  // Freshness is tracked in two domains:
  // - ppsIsrStaleMs: raw ISR edge activity via capturePpsSeen()
  // - ppsStaleMs: processed PPS samples making it through the main-loop queue
  PpsFreshnessInputs pps_freshness_inputs;
  pps_freshness_inputs.now_ms = now_ms;
  pps_freshness_inputs.last_pps_isr_change_ms = last_pps_isr_change_ms;
  pps_freshness_inputs.last_pps_processed_ms = last_pps_processed_ms;
  pps_freshness_inputs.pps_isr_stale_ms = Tunables::ppsIsrStaleMsActive();
  pps_freshness_inputs.pps_processing_stale_ms = Tunables::ppsStaleMsActive();
  pps_freshness_inputs.pps_primed = pps_primed;
  const PpsFreshnessResult pps_freshness = evaluatePpsFreshness(pps_freshness_inputs);
  if (pps_freshness.shouldResetToNoPps()) {
    gPpsValidator.reset();
    gFreqDiscipliner.observe(PpsValidator::SampleClass::GAP, false, (uint32_t)MAIN_CLOCK_HZ, now_ms, true);
    gDisciplinedTime.sync(gFreqDiscipliner, false);
    gpsStatus = GpsStatus::NO_PPS;
  }

  // Re-emit startup metadata exactly once from the runtime path after startup
  // settles, so consumers can recover if the initial boot burst was missed.
  // Keep this deterministic/minimal (CFG + HDR only) to avoid recovery noise.
  const bool startup_reemit_due =
      now_ms >= (uint32_t)Tunables::ppsConfigReemitDelayMsActive();
  if (startup_output_ready && metadata_reemit_pending && startup_reemit_due) {
    emitMetadataNow();
    metadata_reemit_pending = false;
  }

  while (capturePpsAvailable()) {
    PpsCapture cap = capturePopPps();
    uint32_t t = cap.edge32;

    if (!pps_primed) {
      pps_last_edge32 = t;
      pps_primed = true;
      lastPpsCapture = t;
      lastPpsCap16 = cap.cap16;
      last_pps_processed_ms = now_ms;
      ppsAdjustOnPpsPrimed(t, pps_delta_active ? (uint32_t)pps_delta_active : (uint32_t)MAIN_CLOCK_HZ);
      continue;
    }

    // Two distinct PPS interval domains:
    // - pps_dt32_ticks: full 32-bit elapsed ticks across PPS edges (~MAIN_CLOCK_HZ per second;
    //                   ~16,000,000 in the default 16 MHz build),
    //                   used as the primary validator/discipliner input
    // - pps_dt16_mod:   16-bit capture modulo delta, retained only as compact timing telemetry
    const uint32_t prev_edge32 = pps_last_edge32;
    const uint16_t prev_cap16 = lastPpsCap16;
    uint32_t pps_dt32_ticks = elapsed32(t, prev_edge32);
    uint16_t pps_dt16_mod = sub16(cap.cap16, prev_cap16);
    const uint32_t applied_for_completed_second =
        pps_delta_active ? (uint32_t)pps_delta_active : (uint32_t)MAIN_CLOCK_HZ;

    pps_last_edge32 = t;
    lastPpsCapture = t;
    lastPpsCap16 = cap.cap16;
    last_pps_processed_ms = now_ms;

    PpsValidator::SampleClass cls = gPpsValidator.classify(pps_dt32_ticks, false);
    gPpsValidator.observe(cls, pps_dt32_ticks, now_ms);

    const bool pps_valid = gPpsValidator.isValid();
    const bool anomaly = (cls != PpsValidator::SampleClass::OK);
    const FreqDiscipliner::DiscState prev_disc_state = gFreqDiscipliner.state();
    gFreqDiscipliner.observe(cls, pps_valid, pps_dt32_ticks, now_ms, anomaly);
    gDisciplinedTime.sync(gFreqDiscipliner, pps_valid);

#if ENABLE_PPS_BASELINE_TELEMETRY
    if (startup_output_ready) {
      emit_pps_baseline_telemetry(++pps_base_seq,
                                  now_ms,
                                  pps_dt32_ticks,
                                  cls,
                                  pps_valid,
                                  gFreqDiscipliner,
                                  cap.latency16,
                                  cap.cap16);
    }
#endif

    pps_delta_inst = pps_dt32_ticks;
    pps_delta_active = gDisciplinedTime.ticksPerSecond();
    ppsAdjustOnPpsFinalized(prev_edge32,
                            t,
                            applied_for_completed_second,
                            pps_delta_active ? (uint32_t)pps_delta_active : (uint32_t)MAIN_CLOCK_HZ);
    pps_R_ppm = gFreqDiscipliner.rPpm();
    pps_J_ticks = gFreqDiscipliner.madTicks();

#if PPS_TUNING_TELEMETRY
    if (startup_output_ready) {
      tune_push_sample(gFreqDiscipliner.state(), cls, pps_valid, gFreqDiscipliner);
      const FreqDiscipliner::DiscState curr_disc_state = gFreqDiscipliner.state();
      if (curr_disc_state != prev_disc_state) {
        uint8_t streak = gFreqDiscipliner.transitionStreak();
        if (streak == 0U) {
          streak = (curr_disc_state == FreqDiscipliner::DiscState::DISCIPLINED) ?
                   gFreqDiscipliner.lockStreak() :
                   gFreqDiscipliner.unlockStreak();
        }
        emit_tune_event(prev_disc_state, curr_disc_state, now_ms, gFreqDiscipliner, streak);
      }
    }
#endif

    switch (gFreqDiscipliner.state()) {
      case FreqDiscipliner::DiscState::DISCIPLINED: gpsStatus = GpsStatus::LOCKED; break;
      case FreqDiscipliner::DiscState::ACQUIRE: gpsStatus = GpsStatus::ACQUIRING; break;
      case FreqDiscipliner::DiscState::HOLDOVER: gpsStatus = GpsStatus::HOLDOVER; break;
      default: gpsStatus = GpsStatus::NO_PPS; break;
    }
  }
}

void pendulumLoop() {
  const uint32_t now_ms = platformMillis();
#if ENABLE_MEMORY_TELEMETRY_STS
  memoryTelemetrySample();
#endif
  processSerialCommands();
  process_pps();
  swingAssemblerProcessEdges();

  while (swingAssemblerAvailable()) {
    FullSwing fs = swingAssemblerPop();

    PendulumSample sample{};
    sample.tick       = fs.tick;
    sample.tick_adj   = fs.tick_adj;
    sample.tick_block = fs.tick_block;
    sample.tick_block_adj = fs.tick_block_adj;
    sample.tick_total_adj_direct = fs.tick_total_adj_direct;
    sample.tick_total_adj_diag   = fs.tick_total_adj_diag;
    sample.tock       = fs.tock;
    sample.tock_adj   = fs.tock_adj;
    sample.tock_block = fs.tock_block;
    sample.tock_block_adj = fs.tock_block_adj;
    sample.tock_total_adj_direct = fs.tock_total_adj_direct;
    sample.tock_total_adj_diag   = fs.tock_total_adj_diag;
    sample.f_inst_hz      = pps_delta_inst ? pps_delta_inst : (uint32_t)MAIN_CLOCK_HZ;
    sample.f_hat_hz       = pps_delta_active ? (uint32_t)pps_delta_active : (uint32_t)MAIN_CLOCK_HZ;
    sample.holdover_age_ms = gFreqDiscipliner.holdoverAgeMs();
    sample.r_ppm          = pps_R_ppm;
    sample.j_ticks        = pps_J_ticks;
    sample.gps_status     = gpsStatus;
    sample.dropped_events = captureDroppedEvents();
    sample.adj_diag       = fs.adj_diag;
#if ENABLE_PENDULUM_ADJ_PROVENANCE
    sample.pps_seq_row    = fs.pps_seq_row;
#endif

    if (!startup_output_ready) {
      continue;
    }
    sendSample(sample);
  }

#if ENABLE_MEMORY_TELEMETRY_STS
  if (startup_output_ready &&
      (uint32_t)(now_ms - last_mem_telemetry_ms) >= (uint32_t)MEMORY_TELEMETRY_PERIOD_MS) {
    last_mem_telemetry_ms = now_ms;
    emitStatusMemoryTelemetry(true);
  }
#endif
#if ENABLE_PERIODIC_FLUSH
  static uint32_t s_last_flush_ms = 0;
  if ((uint32_t)(now_ms - s_last_flush_ms) >= FLUSH_PERIOD_MS) {
    s_last_flush_ms = now_ms;
    DATA_SERIAL.flush();
  }
#endif
}
