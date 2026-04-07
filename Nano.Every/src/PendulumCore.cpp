#include "Config.h"

#include <Arduino.h>
#include <stdio.h>
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
#include "PendulumTelemetry.h"

static_assert(sizeof(uint16_t) == 2, "Expected 16-bit capture modulo domain");
static_assert(sizeof(uint32_t) == 4, "Expected 32-bit PPS tick domain");

static inline uint32_t elapsed32(uint32_t now, uint32_t then) {
  return (uint32_t)(now - then);
}

static inline uint16_t sub16(uint16_t a, uint16_t b) {
  return (uint16_t)(a - b);
}

volatile uint32_t lastPpsCapture             = 0;        // last PPS capture tick count

GpsStatus gpsStatus = GpsStatus::NO_PPS;

static PpsValidator gPpsValidator;
static FreqDiscipliner gFreqDiscipliner;
static DisciplinedTime gDisciplinedTime;
// Cached active PPS interval used by shared fast/slow consumers.
static uint64_t pps_delta_active = (uint32_t)MAIN_CLOCK_HZ;

// Telemetry/window formatting is isolated in PendulumTelemetry.cpp for
// clearer module boundaries and lower PendulumCore churn.


static bool metadata_reemit_pending = false;
static bool startup_output_ready = false;
static bool startup_replay_requested = false;
static bool command_activity_seen = false;
static bool startup_auto_retry_pending = false;
static uint32_t startup_ready_ms = 0;
static bool pps_primed = false;
static uint32_t pps_seen_prev = 0;
static uint32_t last_pps_isr_change_ms = 0;
static uint32_t last_pps_processed_ms = 0;
static uint32_t pps_last_edge32 = 0;
static uint16_t lastPpsCap16 = 0;
static uint32_t last_mem_telemetry_ms = 0;
#if ENABLE_PERIODIC_SERIAL_DIAG_STS
static uint32_t last_serial_diag_telemetry_ms = 0;
#endif
#if ENABLE_PPS_BASELINE_TELEMETRY
static uint32_t pps_base_seq = 0;
#endif
#if ENABLE_PROFILING && DUAL_PPS_PROFILING
static uint32_t dual_pps_matched_pairs = 0;
static uint32_t dual_pps_skipped_pairs = 0;
static uint32_t dual_pps_last_matched_tcb1_seq = 0;
#endif

void resetRuntimeStateAfterTunablesChange() {
  gpsStatus = GpsStatus::NO_PPS;
  captureResetAndReinit();
  gPpsValidator.reset();
  gFreqDiscipliner.reset((uint32_t)MAIN_CLOCK_HZ);
  gDisciplinedTime.begin((uint32_t)MAIN_CLOCK_HZ);
  pps_delta_active = (uint32_t)MAIN_CLOCK_HZ;
  lastPpsCapture = 0;
  pps_primed = false;
  pps_seen_prev = 0;
  last_pps_isr_change_ms = 0;
  last_pps_processed_ms = 0;
  pps_last_edge32 = 0;
  lastPpsCap16 = 0;
  startup_output_ready = false;
  startup_replay_requested = false;
  command_activity_seen = false;
  startup_auto_retry_pending = false;
  startup_ready_ms = 0;
  last_mem_telemetry_ms = 0;
#if ENABLE_PERIODIC_SERIAL_DIAG_STS
  last_serial_diag_telemetry_ms = 0;
#endif
  ppsAdjustReset((uint32_t)MAIN_CLOCK_HZ);
#if ENABLE_PPS_BASELINE_TELEMETRY
  pps_base_seq = 0;
#endif
#if ENABLE_PROFILING && DUAL_PPS_PROFILING
  dual_pps_matched_pairs = 0;
  dual_pps_skipped_pairs = 0;
  dual_pps_last_matched_tcb1_seq = 0;
#endif
}

#if ENABLE_PROFILING && DUAL_PPS_PROFILING
void getDualPpsRuntimeCounters(DualPpsRuntimeCounters& out) {
  DualPpsProfilingCounters seen = {};
  captureReadDualPpsSeenCounters(seen);
  out.matched_pairs = dual_pps_matched_pairs;
  out.unpaired_tcb1_rising = (seen.tcb1_rising_seen > dual_pps_matched_pairs)
                                 ? (seen.tcb1_rising_seen - dual_pps_matched_pairs)
                                 : 0U;
  out.unpaired_tcb2_rising = (seen.tcb2_rising_seen > dual_pps_matched_pairs)
                                 ? (seen.tcb2_rising_seen - dual_pps_matched_pairs)
                                 : 0U;
  out.skipped_pairs = dual_pps_skipped_pairs;
}
#endif

void emitMetadataNow() {
  if (!startup_output_ready) {
    // Queue metadata until startup emission has completed.
    metadata_reemit_pending = true;
    return;
  }
  // emit meta stays intentionally narrow for parser/schema recovery only.
  // Emit only the metadata contract in strict order:
  //   1) CFG metadata row
  //   2) active schema/header declaration (SCH in CANONICAL, HDR_PART in DERIVED)
  emitStatusSampleConfig();
  printCsvHeader();
}

void emitStartupNow() {
  if (!startup_output_ready) {
    // Defer parser-recovery metadata until startup emission has completed.
    metadata_reemit_pending = true;
    return;
  }
  // Authoritative full startup replay contract (safe to re-emit within one boot):
  //   1) reset-cause / boot-sequence status
  //   2) startup status rows (build/schema/flags/clock/tunables/CFG/PPS config)
  //   3) schema/header declarations (SCH in CANONICAL, HDR_PART in DERIVED)
  emitResetCause();
  emitStatusBootHeaders();
  printCsvHeader();
}

void noteSerialCommandActivity() { command_activity_seen = true; }

void noteExplicitStartupReplayRequest() {
  command_activity_seen = true;
  startup_replay_requested = true;
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
  startup_ready_ms = platformMillis();
  startup_auto_retry_pending = true;

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

static void process_pps(uint8_t budget_remaining) {
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
    gDisciplinedTime.sync(gFreqDiscipliner, false, now_ms);
    gpsStatus = GpsStatus::NO_PPS;
  }

  // Re-emit startup metadata exactly once from the runtime path after startup
  // settles, so consumers can recover if the initial boot burst was missed.
  // Keep this deterministic/minimal (CFG + HDR_PART only) to avoid recovery noise.
  const bool startup_reemit_due =
      now_ms >= (uint32_t)Tunables::ppsConfigReemitDelayMsActive();
  if (startup_output_ready && metadata_reemit_pending && startup_reemit_due) {
    emitMetadataNow();
    metadata_reemit_pending = false;
  }

  // Backup only: one bounded full startup replay retry after boot.
  // Suppress once host command traffic appears or host explicitly requests replay.
  if (startup_output_ready && startup_auto_retry_pending) {
    if (command_activity_seen || startup_replay_requested) {
      startup_auto_retry_pending = false;
    } else if (elapsed32(now_ms, startup_ready_ms) >= (uint32_t)STARTUP_FULL_REPLAY_RETRY_DELAY_MS) {
      emitStartupNow();
      startup_auto_retry_pending = false;
    }
  }

  while (budget_remaining > 0U && capturePpsAvailable()) {
    budget_remaining--;
    PpsCapture cap = capturePopPps();
    uint32_t t = cap.edge32;
    if (startup_output_ready && ACTIVE_EMIT_MODE == EmitMode::CANONICAL) {
      CanonicalPpsSample canonicalPps{};
      canonicalPps.seq = cap.seq;
      canonicalPps.edge_tcb0 = cap.edge32;
      canonicalPps.now32 = cap.now32;
      canonicalPps.cap16 = cap.cap16;
      canonicalPps.latency16 = cap.latency16;
      canonicalPps.gps_status = gpsStatus;
      canonicalPps.holdover_age_ms = gFreqDiscipliner.holdoverAgeMs();
      canonicalPps.drop_pps = captureDroppedPpsEvents();
      sendCanonicalPpsSample(canonicalPps);
    }

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

#if ENABLE_PROFILING && DUAL_PPS_PROFILING
    DualPpsTcb1RisingSnapshot tcb1_rising = {};
    if (startup_output_ready && captureReadDualPpsTcb1RisingSnapshot(tcb1_rising)) {
      if (tcb1_rising.rise_seq == cap.rise_seq && tcb1_rising.rise_seq != dual_pps_last_matched_tcb1_seq) {
        const int32_t delta_ccmp = (int32_t)tcb1_rising.cap16 - (int32_t)cap.cap16;
        const int32_t delta_ext = (int32_t)(tcb1_rising.edge32 - cap.edge32);
        emit_dual_pps_edge_telemetry(tcb1_rising.rise_seq,
                                     cap.cap16,
                                     tcb1_rising.cap16,
                                     delta_ccmp,
                                     cap.edge32,
                                     tcb1_rising.edge32,
                                     delta_ext);
        dual_pps_matched_pairs++;
        dual_pps_last_matched_tcb1_seq = tcb1_rising.rise_seq;
      } else if (tcb1_rising.rise_seq != cap.rise_seq) {
        dual_pps_skipped_pairs++;
      }
    } else if (startup_output_ready) {
      dual_pps_skipped_pairs++;
    }
#endif

    PpsValidator::SampleClass cls = gPpsValidator.classify(pps_dt32_ticks, false);
    gPpsValidator.observe(cls, pps_dt32_ticks, now_ms);

    const bool pps_valid = gPpsValidator.isValid();
    const bool anomaly = (cls != PpsValidator::SampleClass::OK);
    const FreqDiscipliner::DiscState prev_disc_state = gFreqDiscipliner.state();
    const DisciplinedTime::ExportMode prev_export_mode = gDisciplinedTime.exportMode();
    gFreqDiscipliner.observe(cls, pps_valid, pps_dt32_ticks, now_ms, anomaly);
    gDisciplinedTime.sync(gFreqDiscipliner, pps_valid, now_ms);

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

    pps_delta_active = gDisciplinedTime.ticksPerSecond();
    ppsAdjustOnPpsFinalized(prev_edge32,
                            t,
                            applied_for_completed_second,
                            pps_delta_active ? (uint32_t)pps_delta_active : (uint32_t)MAIN_CLOCK_HZ);
#if PPS_TUNING_TELEMETRY
    if (startup_output_ready) {
      tune_push_sample(gFreqDiscipliner.state(), cls, pps_valid, gFreqDiscipliner);
      const FreqDiscipliner::DiscState curr_disc_state = gFreqDiscipliner.state();
      const DisciplinedTime::ExportMode curr_export_mode = gDisciplinedTime.exportMode();
      if (curr_disc_state != prev_disc_state) {
        uint8_t streak = gFreqDiscipliner.transitionStreak();
        if (streak == 0U) {
          streak = (curr_disc_state == FreqDiscipliner::DiscState::DISCIPLINED) ?
                   gFreqDiscipliner.lockStreak() :
                   gFreqDiscipliner.unlockStreak();
        }
        emit_tune_event(prev_disc_state, curr_disc_state, now_ms, gFreqDiscipliner, streak);
      }
      if (curr_export_mode != prev_export_mode) {
        emit_metrology_mode_event(prev_export_mode,
                                  curr_export_mode,
                                  curr_disc_state,
                                  now_ms,
                                  gDisciplinedTime.ticksPerSecond(),
                                  gFreqDiscipliner.lastGoodSlow());
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
  process_pps(PPS_PROCESS_BUDGET_PER_LOOP);
  swingAssemblerProcessEdges();

  uint8_t swing_budget = SWING_PROCESS_BUDGET_PER_LOOP;
  while (swing_budget > 0U && swingAssemblerAvailable()) {
    swing_budget--;
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
    sample.tick_total_f_hat_hz = gDisciplinedTime.ticksPerSecond();
    sample.tock_total_f_hat_hz = gDisciplinedTime.ticksPerSecond();
    sample.holdover_age_ms = gFreqDiscipliner.holdoverAgeMs();
    sample.gps_status     = gpsStatus;
    sample.dropped_events = captureDroppedEvents();
    sample.adj_diag       = fs.adj_diag;
    sample.adj_comp_diag  = fs.adj_comp_diag;
    sample.pps_seq_row    = fs.pps_seq_row;

    if (!startup_output_ready) {
      continue;
    }
    if (ACTIVE_EMIT_MODE == EmitMode::DERIVED) {
      sendSample(sample);
    } else {
      CanonicalSwingSample canonical{};
      canonical.seq = fs.swing_seq;
      canonical.edge0_tcb0 = fs.edge0_tcb0;
      canonical.edge1_tcb0 = fs.edge1_tcb0;
      canonical.edge2_tcb0 = fs.edge2_tcb0;
      canonical.edge3_tcb0 = fs.edge3_tcb0;
      canonical.edge4_tcb0 = fs.edge4_tcb0;
      canonical.drop_ir = captureDroppedIrEvents();
      canonical.drop_pps = captureDroppedPpsEvents();
      canonical.drop_swing = captureDroppedSwingRows();
      canonical.adj_diag = fs.adj_diag;
      canonical.adj_comp_diag = fs.adj_comp_diag;
      sendCanonicalSwingSample(canonical);
    }
  }

#if ENABLE_MEMORY_TELEMETRY_STS
  if (startup_output_ready &&
      (uint32_t)(now_ms - last_mem_telemetry_ms) >= (uint32_t)MEMORY_TELEMETRY_PERIOD_MS) {
    last_mem_telemetry_ms = now_ms;
    emitStatusMemoryTelemetry(true);
  }
#endif
#if ENABLE_PERIODIC_SERIAL_DIAG_STS
  if (startup_output_ready &&
      (uint32_t)(now_ms - last_serial_diag_telemetry_ms) >= (uint32_t)SERIAL_DIAG_PERIOD_MS) {
    last_serial_diag_telemetry_ms = now_ms;
    emitStatusSerialDiagnostics();
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
