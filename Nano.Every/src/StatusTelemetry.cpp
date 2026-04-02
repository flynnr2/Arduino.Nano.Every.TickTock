#include "Config.h"

#include <Arduino.h>
#include <stdio.h>
#include <string.h>

#include "PendulumProtocol.h"
#include "SerialParser.h"
#include "StatusTelemetry.h"
#include "StsHeader.h"
#include "PpsValidator.h"
#include "FreqDiscipliner.h"
#include "MemoryTelemetry.h"


namespace {
uint16_t g_boot_sequence_for_telemetry = 0;
bool g_reset_cause_latched = false;
uint8_t g_latched_rstfr = 0;
bool g_reset_cause_emitted_this_boot = false;

// Forensic boot sequence tracking:
// - Retained in .noinit to survive software/watchdog/external resets.
// - Reseeded only on fresh sessions (POR/BOR/UPDI), or if cookie is invalid.
uint16_t g_boot_seq_noinit __attribute__((section(".noinit")));
uint16_t g_boot_seq_cookie_noinit __attribute__((section(".noinit")));
constexpr uint16_t BOOT_SEQ_COOKIE = 0xB007;

// StatusTelemetry formatting concurrency contract:
// - Non-ISR boot/status emitters invoked from setup/main loop.
// - May overlap with SerialParser (owner 1), PendulumCore (owner 3), and
//   MemoryTelemetry (owner 4) formatting attempts.
// - This module uses owner id 2 (FormatBufferOwner::StatusTelemetry).
char* prepareStatusLineBuf() {
  return tryAcquireFormatBuffer(FormatBufferOwner::StatusTelemetry);
}

void releaseStatusLineBuf() {
  releaseFormatBuffer(FormatBufferOwner::StatusTelemetry);
}

void emitBuildHeader() {
  char* line = prepareStatusLineBuf();
  if (!line) return;
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "build,git=%s,dirty=%s,utc=%s,fw=%s,board=NanoEvery,mcu=ATmega4809,f_cpu=%lu,main_clock=%s,main_clock_hz=%lu,baud=%lu",
                         GIT_SHA,
                         StsHeader::dirtyField(),
                         BUILD_UTC,
                         FW_VERSION,
                         (unsigned long)F_CPU,
                         StsHeader::mainClockSourceField(),
                         (unsigned long)MAIN_CLOCK_HZ,
                         (unsigned long)SERIAL_BAUD_NANO);
  if (n > 0) {
    sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry,
                              StatusCode::ProgressUpdate,
                              line,
                              EmissionReliability::Required);
  }
  releaseStatusLineBuf();
}

void emitSchemaHeader() {
  char* line = prepareStatusLineBuf();
  if (!line) return;
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "%s,sts=%u,sample=%s,eeprom=%u,%s=%u,%s=%s",
                         STS_FAMILY_SCHEMA,
                         (unsigned int)STS_SCHEMA_VERSION,
                         SAMPLE_SCHEMA_ID,
                         (unsigned int)EEPROM_CONFIG_VERSION_CURRENT,
                         CFG_KEY_ADJ_SEMANTICS_VERSION,
                         (unsigned int)ADJ_SEMANTICS_VERSION,
                         CFG_KEY_HDR_MODE,
                         HDR_MODE_ACTIVE);
  if (n > 0) {
    sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry,
                              StatusCode::ProgressUpdate,
                              line,
                              EmissionReliability::Required);
  }
  releaseStatusLineBuf();
}

void emitFlagsHeader() {
  char* line = prepareStatusLineBuf();
  if (!line) return;
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "flags,timebase=%s,sharedReads=%u,disableTcb3=%u,ppsTuning=%u,ppsBase=%u,clockDiagSts=%u,periodicFlush=%u,periodicSerialDiag=%u,ledActivity=%u",
                         StsHeader::timebaseField(),
                         (unsigned int)StsHeader::sharedReadsField(),
                         (unsigned int)StsHeader::disableTcb3Field(),
                         (unsigned int)StsHeader::ppsTuningTelemetryField(),
                         (unsigned int)StsHeader::ppsBaselineTelemetryField(),
                         (unsigned int)StsHeader::clockDiagStsField(),
                         (unsigned int)StsHeader::periodicFlushField(),
                         (unsigned int)ENABLE_PERIODIC_SERIAL_DIAG_STS,
                         (unsigned int)StsHeader::ledActivityField());
  if (n > 0) {
    sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry,
                              StatusCode::ProgressUpdate,
                              line,
                              EmissionReliability::Required);
  }
  releaseStatusLineBuf();
}

} // namespace

void latchResetCauseOnceAtBoot() {
  if (g_reset_cause_latched) return;
  const uint8_t rstfr = RSTCTRL.RSTFR;
  g_latched_rstfr = rstfr;
  RSTCTRL.RSTFR = rstfr; // Clear exactly once after the single boot-time read.
  g_reset_cause_latched = true;
  g_reset_cause_emitted_this_boot = false;
}

uint8_t getLatchedResetCause() {
  return g_latched_rstfr;
}

void setBootSequenceForTelemetry(uint16_t bootSeq) {
  g_boot_sequence_for_telemetry = bootSeq;
}

uint16_t getBootSequenceForTelemetry() {
  return g_boot_sequence_for_telemetry;
}

void formatLatchedResetFlags(char* out, size_t outLen) {
  if (!out || outLen == 0) return;
  const uint8_t rstfr = getLatchedResetCause();
  size_t pos = 0;

  auto appendFlag = [&](const char* flag) {
    if (pos >= outLen - 1) return;
    if (pos != 0 && pos < outLen - 1) out[pos++] = '|';
    for (size_t i = 0; flag[i] != '\0' && pos < outLen - 1; ++i) {
      out[pos++] = flag[i];
    }
    out[pos] = '\0';
  };

  out[0] = '\0';
  if (rstfr & RSTCTRL_PORF_bm) appendFlag("PORF");
  if (rstfr & RSTCTRL_BORF_bm) appendFlag("BORF");
  if (rstfr & RSTCTRL_EXTRF_bm) appendFlag("EXTRF");
  if (rstfr & RSTCTRL_WDRF_bm) appendFlag("WDRF");
  if (rstfr & RSTCTRL_SWRF_bm) appendFlag("SWRF");
  if (rstfr & RSTCTRL_UPDIRF_bm) appendFlag("UPDIRF");
  if (pos == 0) appendFlag("NONE");
}

bool shouldResetBootSeqFromLatchedCause() {
  const uint8_t rstfr = getLatchedResetCause();
  return (rstfr & (RSTCTRL_PORF_bm | RSTCTRL_BORF_bm | RSTCTRL_UPDIRF_bm)) != 0;
}

uint16_t advanceBootSequenceForBoot() {
  const bool fresh_session = shouldResetBootSeqFromLatchedCause();
  if (fresh_session || g_boot_seq_cookie_noinit != BOOT_SEQ_COOKIE) {
    g_boot_seq_noinit = 0;
  }
  g_boot_seq_cookie_noinit = BOOT_SEQ_COOKIE;

  if (g_boot_seq_noinit == 0xFFFFu) g_boot_seq_noinit = 0;
  g_boot_seq_noinit = (uint16_t)(g_boot_seq_noinit + 1u);
  setBootSequenceForTelemetry(g_boot_seq_noinit);
  return g_boot_seq_noinit;
}

#if PPS_TUNING_TELEMETRY
void emitPpsTuningConfigSnapshot() {
  // Stack-sensitive telemetry section: reuse shared formatter buffer (main-loop only).
  char* line = prepareStatusLineBuf();
  if (!line) return;
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "TUNE_CFG,lockR=%u,lockMadTicks=%u,lockN=%u,unlockR=%u,unlockMadTicks=%u,unlockN=%u",
                         (unsigned int)Tunables::ppsLockRppmActive(),
                         (unsigned int)Tunables::ppsLockMadTicksActive(),
                         (unsigned int)Tunables::ppsLockCountActive(),
                         (unsigned int)Tunables::ppsUnlockRppmActive(),
                         (unsigned int)Tunables::ppsUnlockMadTicksActive(),
                         (unsigned int)Tunables::ppsUnlockCountActive());
  if (n > 0) {
    sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry,
                              StatusCode::ProgressUpdate,
                              line,
                              EmissionReliability::Required);
  }
  releaseStatusLineBuf();
}
#else
void emitPpsTuningConfigSnapshot() {}
#endif

void emitStatusPpsConfig() {
  char* line = prepareStatusLineBuf();
  if (!line) return;
  const uint32_t ref = (uint32_t)MAIN_CLOCK_HZ;
  const uint32_t min_ticks = PpsValidator::minOkTicks(ref);
  const uint32_t max_ticks = PpsValidator::maxOkTicks(ref);
  int n = snprintf(line,
                   CSV_PAYLOAD_MAX,
                   "pps_cfg,ref=%lu,min=%lu,max=%lu,seed_n2_max10=%u,seed_cons100=%u,seed_need=%u,reseed_need=%u,hard_ticks=%lu,dup_num10=%u,ok_min_num10=%u,ok_max_num10=%u,gap_num10=%u,ratio_den10=%u",
                   (unsigned long)ref,
                   (unsigned long)min_ticks,
                   (unsigned long)max_ticks,
                   (unsigned int)PpsValidator::seedNear2xMaxNum10(),
                   (unsigned int)PpsValidator::seedConsistencyNum100(),
                   (unsigned int)PpsValidator::startupSeedRequired(),
                   (unsigned int)PpsValidator::recoverySeedRequired(),
                   (unsigned long)PpsValidator::kHardTicks(),
                   (unsigned int)PpsValidator::dupNum10(),
                   (unsigned int)PpsValidator::okMinNum10(),
                   (unsigned int)PpsValidator::okMaxNum10(),
                   (unsigned int)PpsValidator::gapNum10(),
                   (unsigned int)PpsValidator::ratioDen10());
  if (n > 0) sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry, StatusCode::ProgressUpdate, line);

  n = snprintf(line,
               CSV_PAYLOAD_MAX,
               "pps_freshness,%s=queued_sample_processing,%s=isr_edge_activity",
               PARAM_PPS_STALE_MS,
               PARAM_PPS_ISR_STALE_MS);
  if (n > 0) sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry, StatusCode::ProgressUpdate, line);
  releaseStatusLineBuf();
}

void emitStatusSampleConfig() {
  char* line = prepareStatusLineBuf();
  if (!line) return;
  // CFG carries metadata (including schema ID). Literal sample columns are emitted by HDR_PART sequence.
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "%s,%s=%u,%s=%lu,%s=%s,%s=%s,%s=%u,%s=%s,fw=%s",
                         STS_FAMILY_CFG,
                         CFG_KEY_PROTOCOL_VERSION,
                         (unsigned int)PROTOCOL_VERSION,
                         CFG_KEY_NOMINAL_HZ,
                         (unsigned long)MAIN_CLOCK_HZ,
                         CFG_KEY_SAMPLE_TAG,
                         TAG_SMP,
                         CFG_KEY_SAMPLE_SCHEMA,
                         SAMPLE_SCHEMA_ID,
                         CFG_KEY_ADJ_SEMANTICS_VERSION,
                         (unsigned int)ADJ_SEMANTICS_VERSION,
                         CFG_KEY_HDR_MODE,
                         HDR_MODE_ACTIVE,
                         FW_VERSION);
  if (n > 0) sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry, StatusCode::ProgressUpdate, line);

  const int cfg_n = snprintf(line,
                             CSV_PAYLOAD_MAX,
                             "%s=%u,%s=%lu,%s=%s,%s=%s,%s=%u,%s=%s,fw=%s",
                             CFG_KEY_PROTOCOL_VERSION,
                             (unsigned int)PROTOCOL_VERSION,
                             CFG_KEY_NOMINAL_HZ,
                             (unsigned long)MAIN_CLOCK_HZ,
                             CFG_KEY_SAMPLE_TAG,
                             TAG_SMP,
                             CFG_KEY_SAMPLE_SCHEMA,
                             SAMPLE_SCHEMA_ID,
                             CFG_KEY_ADJ_SEMANTICS_VERSION,
                             (unsigned int)ADJ_SEMANTICS_VERSION,
                             CFG_KEY_HDR_MODE,
                             HDR_MODE_ACTIVE,
                             FW_VERSION);
  if (cfg_n > 0) {
    const size_t payload_len = strnlen(line, CSV_PAYLOAD_MAX);
    constexpr size_t cfg_tag_len = sizeof(TAG_CFG) - 1U;
    constexpr size_t cfg_prefix_len = cfg_tag_len + 1U; // "CFG,"
    if (payload_len < CSV_PAYLOAD_MAX &&
        cfg_prefix_len + payload_len + 1U < CSV_LINE_MAX) {
      memmove(line + cfg_prefix_len, line, payload_len);
      memcpy(line, TAG_CFG, cfg_tag_len);
      line[cfg_tag_len] = ',';
      const size_t tagged_len = cfg_prefix_len + payload_len;
      line[tagged_len] = '\n';
      line[tagged_len + 1U] = '\0';
      queueCSVLine(line, static_cast<int>(tagged_len + 1U), EmissionReliability::Required);
    }
  }
  releaseStatusLineBuf();
}

#if ENABLE_CLOCK_DIAG_STS
void emitStatusClockDiagnostics() {
  const uint8_t rstfr = getLatchedResetCause();
  const uint8_t osccfg = FUSE.OSCCFG;
  const uint8_t mclkctrla = CLKCTRL.MCLKCTRLA;
  const uint8_t mclkctrlb = CLKCTRL.MCLKCTRLB;
  const uint8_t mclkstatus = CLKCTRL.MCLKSTATUS;
  const uint8_t osc20mcaliba = CLKCTRL.OSC20MCALIBA;
  const uint8_t osc20mcalibb = CLKCTRL.OSC20MCALIBB;

  const int8_t osc16err3v = (int8_t)SIGROW.OSC16ERR3V;
  const int8_t osc16err5v = (int8_t)SIGROW.OSC16ERR5V;
  const int8_t osc20err3v = (int8_t)SIGROW.OSC20ERR3V;
  const int8_t osc20err5v = (int8_t)SIGROW.OSC20ERR5V;

  const uint8_t devid0 = SIGROW.DEVICEID0;
  const uint8_t devid1 = SIGROW.DEVICEID1;
  const uint8_t devid2 = SIGROW.DEVICEID2;

  // Stack-sensitive telemetry section: reuse shared formatter buffer (main-loop only).
  char* line = prepareStatusLineBuf();
  if (!line) return;
  int n = snprintf(line,
                   CSV_PAYLOAD_MAX,
                   "clkdiag_core,f_cpu=%lu,rstfr=0x%02X,osccfg=0x%02X,mclkctrla=0x%02X,mclkctrlb=0x%02X,mclkstatus=0x%02X,osc20mcaliba=0x%02X,osc20mcalibb=0x%02X",
                   (unsigned long)F_CPU,
                   (unsigned int)rstfr,
                   (unsigned int)osccfg,
                   (unsigned int)mclkctrla,
                   (unsigned int)mclkctrlb,
                   (unsigned int)mclkstatus,
                   (unsigned int)osc20mcaliba,
                   (unsigned int)osc20mcalibb);
  if (n > 0) sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry, StatusCode::ProgressUpdate, line);

  n = snprintf(line,
               CSV_PAYLOAD_MAX,
               "clkdiag_err,osc16err3v=%d,osc16err5v=%d,osc20err3v=%d,osc20err5v=%d",
               (int)osc16err3v,
               (int)osc16err5v,
               (int)osc20err3v,
               (int)osc20err5v);
  if (n > 0) sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry, StatusCode::ProgressUpdate, line);

  n = snprintf(line,
               CSV_PAYLOAD_MAX,
               "clkdiag_id,deviceid=0x%02X%02X%02X",
               (unsigned int)devid2,
               (unsigned int)devid1,
               (unsigned int)devid0);
  if (n > 0) sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry, StatusCode::ProgressUpdate, line);
  releaseStatusLineBuf();
}
#else
void emitStatusClockDiagnostics() {}
#endif

void emitStatusSerialDiagnostics() {
  char* line = prepareStatusLineBuf();
  if (!line) return;
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "serial_diag,fmt_acq_fail=%lu,fmt_acq_fail_required=%lu,queue_reject=%lu,tx_reentry_drop=%lu,required_drop=%lu",
                         (unsigned long)serialFormatAcquireFailures(),
                         (unsigned long)serialRequiredFormatAcquireFailures(),
                         (unsigned long)serialQueueRejectsInvalidArgs(),
                         (unsigned long)serialTxReentryDrops(),
                         (unsigned long)serialRequiredDrops());
  if (n > 0) {
    sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry,
                              StatusCode::ProgressUpdate,
                              line,
                              EmissionReliability::Required);
  }
  releaseStatusLineBuf();
}

void emitStatusBootHeaders() {
  emitBuildHeader();
  emitSchemaHeader();
  emitFlagsHeader();
#if ENABLE_MEMORY_TELEMETRY_STS
  emitStatusMemoryTelemetry(false);
#endif
  emitStatusClockDiagnostics();
  emitStatusSerialDiagnostics();
  emitStatusTunables();
  emitStatusSampleConfig();
  emitStatusPpsConfig();
}

void emitResetCause() {
  if (g_reset_cause_emitted_this_boot) return;
  g_reset_cause_emitted_this_boot = true;

  const uint8_t rstfr = getLatchedResetCause();
  char flags[48];
  formatLatchedResetFlags(flags, sizeof(flags));
  // Stack-sensitive telemetry section: reuse shared formatter buffer (main-loop only).
  char* line = prepareStatusLineBuf();
  if (!line) return;
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "rstfr,boot_seq=%u,raw=0x%02X,flags=%s",
                         (unsigned int)g_boot_sequence_for_telemetry,
                         (unsigned int)rstfr,
                         flags);
  if (n > 0) sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry, StatusCode::ProgressUpdate, line);
  releaseStatusLineBuf();
}

void emitResetCauseOncePerBoot() { emitResetCause(); }

void emitStatusTunables() {
  char* line = prepareStatusLineBuf();
  if (!line) return;
  int n = snprintf(line,
                   CSV_PAYLOAD_MAX,
                   "%s,%u,%s,%u,%s,%u,%s,%u,%s,%u",
                   PARAM_PPS_FAST_SHIFT,
                   (unsigned int)Tunables::ppsFastShiftActive(),
                   PARAM_PPS_SLOW_SHIFT,
                   (unsigned int)Tunables::ppsSlowShiftActive(),
                   PARAM_PPS_BLEND_LO_PPM,
                   (unsigned int)Tunables::ppsBlendLoPpmActive(),
                   PARAM_PPS_BLEND_HI_PPM,
                   (unsigned int)Tunables::ppsBlendHiPpmActive(),
                   PARAM_PPS_LOCK_R_PPM,
                   (unsigned int)Tunables::ppsLockRppmActive());
  if (n > 0) sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry, StatusCode::ProgressUpdate, line);

  n = snprintf(line,
               CSV_PAYLOAD_MAX,
               "%s,%u,%s,%u,%s,%u,%s,%u,%s,%u",
               PARAM_PPS_LOCK_MAD_TICKS,
               (unsigned int)Tunables::ppsLockMadTicksActive(),
               PARAM_PPS_UNLOCK_R_PPM,
               (unsigned int)Tunables::ppsUnlockRppmActive(),
               PARAM_PPS_UNLOCK_MAD_TICKS,
               (unsigned int)Tunables::ppsUnlockMadTicksActive(),
               PARAM_PPS_LOCK_COUNT,
               (unsigned int)Tunables::ppsLockCountActive(),
               PARAM_PPS_UNLOCK_COUNT,
               (unsigned int)Tunables::ppsUnlockCountActive());
  if (n > 0) sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry, StatusCode::ProgressUpdate, line);

  n = snprintf(line,
               CSV_PAYLOAD_MAX,
               "%s,%u,%s,%u,%s,%u,%s,%u,%s,%u",
               PARAM_PPS_HOLDOVER_MS,
               (unsigned int)Tunables::ppsHoldoverMsActive(),
               PARAM_PPS_STALE_MS,
               (unsigned int)Tunables::ppsStaleMsActive(),
               PARAM_PPS_ISR_STALE_MS,
               (unsigned int)Tunables::ppsIsrStaleMsActive(),
               PARAM_PPS_CFG_REEMIT_DELAY_MS,
               (unsigned int)Tunables::ppsConfigReemitDelayMsActive(),
               PARAM_PPS_ACQUIRE_MIN_MS,
               (unsigned int)Tunables::ppsAcquireMinMsActive());
  if (n > 0) sendStatusFromOwnedBuffer(FormatBufferOwner::StatusTelemetry, StatusCode::ProgressUpdate, line);
  releaseStatusLineBuf();
}
