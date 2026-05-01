#include "RestartBreadcrumbs.h"

#include "Config.h"

#include <Arduino.h>
#include <stdio.h>
#include <util/atomic.h>

#if ENABLE_RESTART_BREADCRUMBS

namespace {

constexpr uint16_t RESTART_BC_MAGIC = 0xBCA5;
constexpr uint16_t RESTART_BC_VERSION = 1U;
constexpr uint8_t RESTART_BC_COMMITTED = 0xA5U;
constexpr uint8_t RESTART_BC_WRITING = 0xFFU;
constexpr uint32_t RESTART_BC_SNAPSHOT_PERIOD_MS = 250U;
constexpr uint32_t RESTART_BC_LOOP_GAP_FLAG_MS = 200U;
constexpr uint32_t RESTART_BC_FROZEN_ADVANCE_THRESHOLD_MS = 500U;
constexpr uint8_t RESTART_BC_PPS_DIAG_FROZEN_ADV = 0x01U;

struct RestartBreadcrumbsPayload {
  uint8_t prev_vlm_seen;
  uint32_t prev_last_ms;
  uint32_t prev_mainloop_hb;
  uint32_t prev_pps_isr_hb;
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

struct RestartBreadcrumbsRetainedRecord {
  uint16_t magic;
  uint16_t version;
  uint32_t generation;
  RestartBreadcrumbsPayload payload;
  uint16_t checksum;
  uint8_t committed;
};

struct RestartBreadcrumbsRetainedSlots {
  RestartBreadcrumbsRetainedRecord slot[2];
};

static_assert(sizeof(RestartBreadcrumbsPayload) <= 44U, "Restart breadcrumbs payload should stay compact");
static_assert(sizeof(RestartBreadcrumbsRetainedRecord) <= 52U,
              "Restart breadcrumbs retained record should stay compact");

RestartBreadcrumbsRetainedSlots g_restart_bc_noinit __attribute__((section(".noinit")));
RestartBreadcrumbsPrevBoot g_prev_boot = {};
bool g_prev_boot_line_emitted = false;

struct RestartBreadcrumbsIsrSharedState {
  volatile uint32_t mainloop_hb = 0; // ISR+main (32-bit: access inside ATOMIC_BLOCK).
  volatile uint32_t pps_isr_hb = 0; // ISR+main (32-bit: access inside ATOMIC_BLOCK).
  volatile uint32_t last_pps_isr_change_ms = 0; // ISR+main (32-bit: access inside ATOMIC_BLOCK).
  volatile uint8_t flags = 0; // ISR+main.
  volatile uint8_t last_loop_phase = RESTART_BC_LOOP_PHASE_UNKNOWN; // ISR+main.
  volatile uint8_t vlm_seen = 0; // ISR+main.
};

struct RestartBreadcrumbsIsrSharedSnapshot {
  uint32_t mainloop_hb = 0;
  uint32_t pps_isr_hb = 0;
  uint32_t last_pps_isr_change_ms = 0;
  uint8_t flags = 0;
  uint8_t last_loop_phase = RESTART_BC_LOOP_PHASE_UNKNOWN;
  uint8_t vlm_seen = 0;
};

struct RestartBreadcrumbsMainState {
  bool vlm_armed = false; // main-only.
  uint32_t last_pps_processed_ms = 0; // main-only.
  uint32_t last_snapshot_ms = 0; // main-only.
  uint32_t last_mainloop_tick_ms = 0; // main-only.
  uint32_t generation = 0; // main-only.
  uint8_t active_slot = 0; // main-only.
  uint8_t pps_diag = 0; // main-only.
  uint32_t last_good_pps_seq = 0; // main-only.
  uint32_t last_good_edge_tcb0 = 0; // main-only.
  uint32_t last_good_now32 = 0; // main-only.
  uint32_t last_good_sample_ms = 0; // main-only.
};

RestartBreadcrumbsIsrSharedState g_isr_shared_state = {};
RestartBreadcrumbsMainState g_main_state = {};

RestartBreadcrumbsIsrSharedSnapshot readIsrSharedSnapshot() {
  RestartBreadcrumbsIsrSharedSnapshot snapshot;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    snapshot.mainloop_hb = g_isr_shared_state.mainloop_hb;
    snapshot.pps_isr_hb = g_isr_shared_state.pps_isr_hb;
    snapshot.last_pps_isr_change_ms = g_isr_shared_state.last_pps_isr_change_ms;
    snapshot.flags = g_isr_shared_state.flags;
    snapshot.last_loop_phase = g_isr_shared_state.last_loop_phase;
    snapshot.vlm_seen = g_isr_shared_state.vlm_seen;
  }
  return snapshot;
}

void resetIsrSharedState() {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    g_isr_shared_state.mainloop_hb = 0;
    g_isr_shared_state.pps_isr_hb = 0;
    g_isr_shared_state.last_pps_isr_change_ms = 0;
    g_isr_shared_state.flags = 0;
    g_isr_shared_state.last_loop_phase = RESTART_BC_LOOP_PHASE_UNKNOWN;
    g_isr_shared_state.vlm_seen = 0;
  }
}

uint16_t checksumBytes(const uint8_t* data, size_t len) {
  uint16_t sum = 0xA5A5u;
  for (size_t i = 0; i < len; ++i) {
    sum = (uint16_t)(sum ^ data[i]);
    sum = (uint16_t)((sum << 5) | (sum >> 11));
    sum = (uint16_t)(sum + 0x9E37u);
  }
  return sum;
}

uint16_t recordChecksum(const RestartBreadcrumbsRetainedRecord& rec) {
  const uint16_t a = checksumBytes(reinterpret_cast<const uint8_t*>(&rec.magic), sizeof(rec.magic));
  const uint16_t b = checksumBytes(reinterpret_cast<const uint8_t*>(&rec.version), sizeof(rec.version));
  const uint16_t c = checksumBytes(reinterpret_cast<const uint8_t*>(&rec.generation), sizeof(rec.generation));
  const uint16_t d = checksumBytes(reinterpret_cast<const uint8_t*>(&rec.payload), sizeof(rec.payload));
  return (uint16_t)(a ^ b ^ c ^ d);
}

bool generationIsNewer(uint32_t candidate, uint32_t reference) {
  if (candidate == reference) return false;
  const uint32_t delta = candidate - reference;
  return delta < 0x80000000UL;
}

bool validateRecord(const RestartBreadcrumbsRetainedRecord& rec) {
  // A slot is valid only after the final committed marker lands.
  if (rec.committed != RESTART_BC_COMMITTED) return false;
  if (rec.magic != RESTART_BC_MAGIC || rec.version != RESTART_BC_VERSION) return false;
  return rec.checksum == recordChecksum(rec);
}

inline bool mclkStatusUnexpected(uint8_t mclkstatus) {
#if USE_EXTCLK_MAIN
  return (mclkstatus & CLKCTRL_SOSC_bm) != 0U;
#else
  (void)mclkstatus;
  return false;
#endif
}

void writeRetainedSnapshot(uint32_t now_ms, uint8_t mclkstatus) {
  const uint8_t target_slot = (uint8_t)(g_main_state.active_slot ^ 1U);
  RestartBreadcrumbsRetainedRecord& rec = g_restart_bc_noinit.slot[target_slot];
  const RestartBreadcrumbsIsrSharedSnapshot isr_shared = readIsrSharedSnapshot();

  // Commit protocol: invalidate target slot first, write full record, commit marker last.
  rec.committed = RESTART_BC_WRITING;
  rec.magic = RESTART_BC_MAGIC;
  rec.version = RESTART_BC_VERSION;
  rec.generation = g_main_state.generation + 1U;
  rec.payload.prev_vlm_seen = isr_shared.vlm_seen;
  rec.payload.prev_last_ms = now_ms;
  rec.payload.prev_mainloop_hb = isr_shared.mainloop_hb;
  rec.payload.prev_pps_isr_hb = isr_shared.pps_isr_hb;
  rec.payload.prev_last_pps_isr_change_ms = isr_shared.last_pps_isr_change_ms;
  rec.payload.prev_last_pps_processed_ms = g_main_state.last_pps_processed_ms;
  rec.payload.prev_last_good_pps_seq = g_main_state.last_good_pps_seq;
  rec.payload.prev_last_good_edge_tcb0 = g_main_state.last_good_edge_tcb0;
  rec.payload.prev_last_good_now32 = g_main_state.last_good_now32;
  rec.payload.prev_last_mclkstatus = mclkstatus;
  rec.payload.prev_last_loop_phase = isr_shared.last_loop_phase;
  rec.payload.prev_flags = isr_shared.flags;
  rec.payload.prev_pps_diag = g_main_state.pps_diag;
  rec.checksum = recordChecksum(rec);
  rec.committed = RESTART_BC_COMMITTED;
  g_main_state.generation = rec.generation;
  g_main_state.active_slot = target_slot;
}

void clearVlmInterruptFlag() {
#if defined(BOD_VLMIF_bm)
  BOD.INTFLAGS = BOD_VLMIF_bm;
#endif
}

} // namespace

void restartBreadcrumbsInitAtBoot() {
  const bool slot0_valid = validateRecord(g_restart_bc_noinit.slot[0]);
  const bool slot1_valid = validateRecord(g_restart_bc_noinit.slot[1]);
  const RestartBreadcrumbsRetainedRecord* selected = nullptr;
  uint8_t selected_slot = 0;

  // Boot-time selection: prefer the newest valid generation, tolerate wrap.
  if (slot0_valid && slot1_valid) {
    const bool slot1_newer = generationIsNewer(g_restart_bc_noinit.slot[1].generation,
                                               g_restart_bc_noinit.slot[0].generation);
    selected_slot = slot1_newer ? 1U : 0U;
    selected = &g_restart_bc_noinit.slot[selected_slot];
  } else if (slot0_valid) {
    selected_slot = 0U;
    selected = &g_restart_bc_noinit.slot[0];
  } else if (slot1_valid) {
    selected_slot = 1U;
    selected = &g_restart_bc_noinit.slot[1];
  }

  g_prev_boot = {};
  g_prev_boot.valid = (selected != nullptr);
  if (selected) {
    g_prev_boot.prev_vlm_seen = selected->payload.prev_vlm_seen != 0U;
    g_prev_boot.prev_last_ms = selected->payload.prev_last_ms;
    g_prev_boot.prev_mainloop_hb = selected->payload.prev_mainloop_hb;
    g_prev_boot.prev_pps_isr_hb = selected->payload.prev_pps_isr_hb;
    g_prev_boot.prev_last_pps_isr_change_ms = selected->payload.prev_last_pps_isr_change_ms;
    g_prev_boot.prev_last_pps_processed_ms = selected->payload.prev_last_pps_processed_ms;
    g_prev_boot.prev_last_good_pps_seq = selected->payload.prev_last_good_pps_seq;
    g_prev_boot.prev_last_good_edge_tcb0 = selected->payload.prev_last_good_edge_tcb0;
    g_prev_boot.prev_last_good_now32 = selected->payload.prev_last_good_now32;
    g_prev_boot.prev_last_mclkstatus = selected->payload.prev_last_mclkstatus;
    g_prev_boot.prev_last_loop_phase = selected->payload.prev_last_loop_phase;
    g_prev_boot.prev_flags = selected->payload.prev_flags;
    g_prev_boot.prev_pps_diag = selected->payload.prev_pps_diag;
    g_main_state.generation = selected->generation;
    g_main_state.active_slot = selected_slot;
    g_main_state.pps_diag = selected->payload.prev_pps_diag;
    g_main_state.last_good_pps_seq = selected->payload.prev_last_good_pps_seq;
    g_main_state.last_good_edge_tcb0 = selected->payload.prev_last_good_edge_tcb0;
    g_main_state.last_good_now32 = selected->payload.prev_last_good_now32;
    g_main_state.last_good_sample_ms = selected->payload.prev_last_pps_processed_ms;
  } else {
    g_prev_boot.prev_vlm_seen = false;
    g_prev_boot.prev_last_loop_phase = RESTART_BC_LOOP_PHASE_UNKNOWN;
    g_main_state.generation = 0;
    g_main_state.active_slot = 0;
    g_main_state.pps_diag = 0;
    g_main_state.last_good_pps_seq = 0;
    g_main_state.last_good_edge_tcb0 = 0;
    g_main_state.last_good_now32 = 0;
    g_main_state.last_good_sample_ms = 0;
  }

  resetIsrSharedState();
  g_main_state.last_pps_processed_ms = 0;
  g_main_state.last_snapshot_ms = 0;
  g_main_state.last_mainloop_tick_ms = 0;
  g_main_state.vlm_armed = false;

  writeRetainedSnapshot(0U, CLKCTRL.MCLKSTATUS);
  g_prev_boot_line_emitted = false;
}

void restartBreadcrumbsMainloopTick(uint32_t now_ms) {
  if (g_main_state.last_mainloop_tick_ms != 0U &&
      (uint32_t)(now_ms - g_main_state.last_mainloop_tick_ms) > RESTART_BC_LOOP_GAP_FLAG_MS) {
    restartBreadcrumbsSetFlag(RESTART_BC_FLAG_LOOP_GAP);
  }
  g_main_state.last_mainloop_tick_ms = now_ms;

  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    g_isr_shared_state.mainloop_hb++;
  }

  if ((uint32_t)(now_ms - g_main_state.last_snapshot_ms) < RESTART_BC_SNAPSHOT_PERIOD_MS) {
    return;
  }
  g_main_state.last_snapshot_ms = now_ms;

  const uint8_t mclkstatus = CLKCTRL.MCLKSTATUS;
  if (mclkStatusUnexpected(mclkstatus)) {
    restartBreadcrumbsSetFlag(RESTART_BC_FLAG_MCLK_UNEXPECTED);
  }

  writeRetainedSnapshot(now_ms, mclkstatus);
}

void restartBreadcrumbsNotifyPpsIsrEdge(uint32_t observed_ms, uint32_t edge_count_delta) {
  // `pisr` must share the same long-running uptime-ms domain as PREV_BOOT `ms` and `pproc`.
  // We therefore latch the earliest foreground uptime-ms at which new PPS ISR activity is
  // observed (via capturePpsSeen delta), not the wrapped 32-bit edge tick timeline.
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    g_isr_shared_state.pps_isr_hb += edge_count_delta;
    g_isr_shared_state.last_pps_isr_change_ms = observed_ms;
  }
}

void restartBreadcrumbsNotifyPpsProcessed(uint32_t now_ms) {
  g_main_state.last_pps_processed_ms = now_ms;
}

void restartBreadcrumbsNotifyAcceptedPpsSample(uint32_t pps_seq, uint32_t edge_tcb0, uint32_t now32, uint32_t now_ms) {
  const bool had_good = g_main_state.last_good_sample_ms != 0U;
  if (had_good &&
      (uint32_t)(now_ms - g_main_state.last_good_sample_ms) >= RESTART_BC_FROZEN_ADVANCE_THRESHOLD_MS &&
      (uint32_t)(now32 - g_main_state.last_good_now32) > 0U &&
      (pps_seq == g_main_state.last_good_pps_seq || edge_tcb0 == g_main_state.last_good_edge_tcb0)) {
    g_main_state.pps_diag = (uint8_t)(g_main_state.pps_diag | RESTART_BC_PPS_DIAG_FROZEN_ADV);
    restartBreadcrumbsSetFlag(RESTART_BC_FLAG_PPS_FROZEN_ADV);
  }

  g_main_state.last_good_pps_seq = pps_seq;
  g_main_state.last_good_edge_tcb0 = edge_tcb0;
  g_main_state.last_good_now32 = now32;
  g_main_state.last_good_sample_ms = now_ms;
}

void restartBreadcrumbsSetFlag(uint8_t flag_mask) {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    g_isr_shared_state.flags = (uint8_t)(g_isr_shared_state.flags | flag_mask);
  }
}

void restartBreadcrumbsSetLoopPhase(uint8_t phase) {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    g_isr_shared_state.last_loop_phase = phase;
  }
}

RestartBreadcrumbsPrevBoot restartBreadcrumbsPrevBootSnapshot() {
  return g_prev_boot;
}

bool restartBreadcrumbsFormatPrevBootLine(char* out, size_t out_len) {
  if (!out || out_len == 0U) return false;
  if (g_prev_boot_line_emitted) return false;
  const RestartBreadcrumbsPrevBoot prev = restartBreadcrumbsPrevBootSnapshot();
  int n = 0;
  if (!prev.valid) {
    n = snprintf(out, out_len, "PREV_BOOT,valid=0");
  } else {
    n = snprintf(out,
                 out_len,
                 "PREV_BOOT,ms=%lu,mhb=%lu,phb=%lu,pisr=%lu,pproc=%lu,lgq=%lu,lge=%lu,lgn=%lu,pfz=%u,vlm=%u,mclk=0x%02X,lph=0x%02X,fl=0x%02X",
                 (unsigned long)prev.prev_last_ms,
                 (unsigned long)prev.prev_mainloop_hb,
                 (unsigned long)prev.prev_pps_isr_hb,
                 (unsigned long)prev.prev_last_pps_isr_change_ms,
                 (unsigned long)prev.prev_last_pps_processed_ms,
                 (unsigned long)prev.prev_last_good_pps_seq,
                 (unsigned long)prev.prev_last_good_edge_tcb0,
                 (unsigned long)prev.prev_last_good_now32,
                 (unsigned int)((prev.prev_pps_diag & RESTART_BC_PPS_DIAG_FROZEN_ADV) ? 1U : 0U),
                 (unsigned int)(prev.prev_vlm_seen ? 1U : 0U),
                 (unsigned int)prev.prev_last_mclkstatus,
                 (unsigned int)prev.prev_last_loop_phase,
                 (unsigned int)prev.prev_flags);
  }
  if (n > 0) {
    g_prev_boot_line_emitted = true;
    return true;
  }
  return false;
}

size_t restartBreadcrumbsRetainedSizeBytes() {
  return sizeof(RestartBreadcrumbsRetainedSlots);
}

void restartBreadcrumbsNotifyVlmEventFromIsr() {
  g_isr_shared_state.vlm_seen = 1U;
}

void restartBreadcrumbsInitVlmEarly() {
  g_main_state.vlm_armed = false;
#if defined(BOD_VLM_vect) && defined(BOD_VLMIE_bm) && defined(BOD_VLMIF_bm) && defined(BOD_VLMLVL_gm) && \
    defined(BOD_VLMLVL0_bm)
  // VLM requires BOD fuse enable; if fuse-disabled this may not arm and stays false.
  constexpr uint8_t kVlmLevel = BOD_VLMLVL0_bm;
  BOD.VLMCTRLA = (uint8_t)((BOD.VLMCTRLA & (uint8_t)(~BOD_VLMLVL_gm)) | kVlmLevel);
  clearVlmInterruptFlag();
  BOD.INTCTRL = (uint8_t)(BOD.INTCTRL | BOD_VLMIE_bm);
  g_main_state.vlm_armed = (BOD.INTCTRL & BOD_VLMIE_bm) != 0U;
#endif
}

bool restartBreadcrumbsVlmArmed() {
  return g_main_state.vlm_armed;
}

#if defined(BOD_VLM_vect)
ISR(BOD_VLM_vect) {
  restartBreadcrumbsNotifyVlmEventFromIsr();
  clearVlmInterruptFlag();
}
#endif

#else

void restartBreadcrumbsInitAtBoot() {}

void restartBreadcrumbsMainloopTick(uint32_t now_ms) {
  (void)now_ms;
}

void restartBreadcrumbsNotifyPpsIsrEdge(uint32_t observed_ms, uint32_t edge_count_delta) {
  (void)observed_ms;
  (void)edge_count_delta;
}

void restartBreadcrumbsNotifyPpsProcessed(uint32_t now_ms) {
  (void)now_ms;
}

void restartBreadcrumbsNotifyAcceptedPpsSample(uint32_t pps_seq, uint32_t edge_tcb0, uint32_t now32, uint32_t now_ms) {
  (void)pps_seq;
  (void)edge_tcb0;
  (void)now32;
  (void)now_ms;
}

void restartBreadcrumbsSetFlag(uint8_t flag_mask) {
  (void)flag_mask;
}

void restartBreadcrumbsSetLoopPhase(uint8_t phase) {
  (void)phase;
}

void restartBreadcrumbsInitVlmEarly() {}

bool restartBreadcrumbsVlmArmed() {
  return false;
}

void restartBreadcrumbsNotifyVlmEventFromIsr() {}

RestartBreadcrumbsPrevBoot restartBreadcrumbsPrevBootSnapshot() {
  return RestartBreadcrumbsPrevBoot{};
}

bool restartBreadcrumbsFormatPrevBootLine(char* out, size_t out_len) {
  (void)out;
  (void)out_len;
  return false;
}

size_t restartBreadcrumbsRetainedSizeBytes() {
  return 0U;
}

#endif
