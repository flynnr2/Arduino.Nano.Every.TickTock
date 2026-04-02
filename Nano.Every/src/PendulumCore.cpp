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

static uint32_t dominantAppliedHzForTotalInterval(uint64_t start_tag, uint64_t end_tag) {
  if (!ppsTagIsValid(start_tag) || !ppsTagIsValid(end_tag)) {
    return (uint32_t)MAIN_CLOCK_HZ;
  }

  const uint32_t start_seq = ppsTagSeq(start_tag);
  const uint32_t end_seq = ppsTagSeq(end_tag);
  if (end_seq < start_seq) {
    return (uint32_t)MAIN_CLOCK_HZ;
  }

  const uint32_t start_ticks = ppsTagTicksIntoSec(start_tag);
  const uint32_t end_ticks = ppsTagTicksIntoSec(end_tag);
  const uint32_t seq_delta = end_seq - start_seq;

  if (seq_delta == 0U) {
    uint32_t hz = 0U;
    return ppsAdjustLookupSeq(start_seq, nullptr, &hz) ? hz : (uint32_t)MAIN_CLOCK_HZ;
  }

  if (seq_delta == 1U) {
    uint32_t start_span = 0U;
    uint32_t start_hz = 0U;
    uint32_t end_hz = 0U;
    if (!ppsAdjustLookupSeq(start_seq, &start_span, &start_hz)) {
      return (uint32_t)MAIN_CLOCK_HZ;
    }
    if (!ppsAdjustLookupSeq(end_seq, nullptr, &end_hz)) {
      return (uint32_t)MAIN_CLOCK_HZ;
    }
    if (start_ticks > start_span) {
      return (uint32_t)MAIN_CLOCK_HZ;
    }

    const uint32_t start_share = start_span - start_ticks;
    const uint32_t end_share = end_ticks;
    return (start_share >= end_share) ? start_hz : end_hz;
  }

  return (uint32_t)MAIN_CLOCK_HZ;
}

volatile uint32_t lastPpsCapture             = 0;        // last PPS capture tick count

GpsStatus gpsStatus = GpsStatus::NO_PPS;

static PpsValidator gPpsValidator;
static FreqDiscipliner gFreqDiscipliner;
static DisciplinedTime gDisciplinedTime;
// Cached active PPS interval used by shared fast/slow consumers.
static uint64_t pps_delta_active = (uint32_t)MAIN_CLOCK_HZ;

// Quality metrics
static uint32_t pps_R_ppm = 0;   // |fast - slow| / slow in ppm
static uint32_t pps_J_ticks = 0; // robust jitter metric as MAD residual ticks

// Telemetry/window formatting is isolated in PendulumTelemetry.cpp for
// clearer module boundaries and lower PendulumCore churn.


static bool metadata_reemit_pending = false;
static bool startup_output_ready = false;
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

void resetRuntimeStateAfterTunablesChange() {
  gpsStatus = GpsStatus::NO_PPS;
  captureResetAndReinit();
  gPpsValidator.reset();
  gFreqDiscipliner.reset((uint32_t)MAIN_CLOCK_HZ);
  gDisciplinedTime.begin((uint32_t)MAIN_CLOCK_HZ);
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
#if ENABLE_PERIODIC_SERIAL_DIAG_STS
  last_serial_diag_telemetry_ms = 0;
#endif
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
  //   2) HDR_PART segmented sample schema declaration
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
    gDisciplinedTime.sync(gFreqDiscipliner, false);
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

  while (budget_remaining > 0U && capturePpsAvailable()) {
    budget_remaining--;
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
  process_pps(PPS_PROCESS_BUDGET_PER_LOOP);
  swingAssemblerProcessEdges();

  uint8_t swing_budget = SWING_PROCESS_BUDGET_PER_LOOP;
  while (swing_budget > 0U && swingAssemblerAvailable()) {
    swing_budget--;
    FullSwing fs = swingAssemblerPop();

    PendulumSample sample{};
    sample.tick       = fs.tick;
    sample.tick_adj   = fs.tick_adj;
    sample.tick_start_tag = fs.tick_start_tag;
    sample.tick_end_tag = fs.tick_end_tag;
    sample.tick_block = fs.tick_block;
    sample.tick_block_adj = fs.tick_block_adj;
    sample.tick_block_start_tag = fs.tick_block_start_tag;
    sample.tick_block_end_tag = fs.tick_block_end_tag;
    sample.tick_total_adj_direct = fs.tick_total_adj_direct;
    sample.tick_total_adj_diag   = fs.tick_total_adj_diag;
    sample.tick_total_start_tag = fs.tick_total_start_tag;
    sample.tick_total_end_tag = fs.tick_total_end_tag;
    sample.tock       = fs.tock;
    sample.tock_adj   = fs.tock_adj;
    sample.tock_start_tag = fs.tock_start_tag;
    sample.tock_end_tag = fs.tock_end_tag;
    sample.tock_block = fs.tock_block;
    sample.tock_block_adj = fs.tock_block_adj;
    sample.tock_block_start_tag = fs.tock_block_start_tag;
    sample.tock_block_end_tag = fs.tock_block_end_tag;
    sample.tock_total_adj_direct = fs.tock_total_adj_direct;
    sample.tock_total_adj_diag   = fs.tock_total_adj_diag;
    sample.tock_total_start_tag = fs.tock_total_start_tag;
    sample.tock_total_end_tag = fs.tock_total_end_tag;
    sample.tick_total_f_hat_hz = dominantAppliedHzForTotalInterval(fs.tick_total_start_tag,
                                                                    fs.tick_total_end_tag);
    sample.tock_total_f_hat_hz = dominantAppliedHzForTotalInterval(fs.tock_total_start_tag,
                                                                    fs.tock_total_end_tag);
    sample.holdover_age_ms = gFreqDiscipliner.holdoverAgeMs();
    sample.r_ppm          = pps_R_ppm;
    sample.j_ticks        = pps_J_ticks;
    sample.gps_status     = gpsStatus;
    sample.dropped_events = captureDroppedEvents();
    sample.adj_diag       = fs.adj_diag;
    sample.adj_comp_diag  = fs.adj_comp_diag;
    sample.pps_seq_row    = fs.pps_seq_row;

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
