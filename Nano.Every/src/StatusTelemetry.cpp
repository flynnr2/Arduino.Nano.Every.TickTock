#include "Config.h"

#include <Arduino.h>
#include <string.h>

#include "PendulumProtocol.h"
#include "SerialParser.h"
#include "StatusTelemetry.h"
#include "StsHeader.h"
#include "PpsValidator.h"
#include "FreqDiscipliner.h"


namespace {
char status_line_buf[CSV_LINE_MAX];

char* prepareStatusLineBuf() {
  memset(status_line_buf, 0, sizeof(status_line_buf));
  return status_line_buf;
}

void emitBuildHeader() {
  char* line = prepareStatusLineBuf();
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "build,git=%s,dirty=%s,utc=%s,board=NanoEvery,mcu=ATmega4809,f_cpu=%lu,main_clock=%s,main_clock_hz=%lu,baud=%lu",
                         GIT_SHA,
                         StsHeader::dirtyField(),
                         BUILD_UTC,
                         (unsigned long)F_CPU,
                         StsHeader::mainClockSourceField(),
                         (unsigned long)MAIN_CLOCK_HZ,
                         (unsigned long)SERIAL_BAUD_NANO);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

void emitSchemaHeader() {
  char* line = prepareStatusLineBuf();
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "schema,sts=%u,sample=%s,eeprom=%u",
                         (unsigned int)StsHeader::STS_SCHEMA_VER,
                         StsHeader::SAMPLE_SCHEMA_ID,
                         (unsigned int)EEPROM_CONFIG_VERSION_CURRENT);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

void emitFlagsHeader() {
  char* line = prepareStatusLineBuf();
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "flags,timebase=%s,sharedReads=%u,disableTcb3=%u,ppsTuning=%u,ppsBase=%u,periodicFlush=%u,ledActivity=%u",
                         StsHeader::timebaseField(),
                         (unsigned int)StsHeader::sharedReadsField(),
                         (unsigned int)StsHeader::disableTcb3Field(),
                         (unsigned int)StsHeader::ppsTuningTelemetryField(),
                         (unsigned int)StsHeader::ppsBaselineTelemetryField(),
                         (unsigned int)StsHeader::periodicFlushField(),
                         (unsigned int)StsHeader::ledActivityField());
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

} // namespace

#if PPS_TUNING_TELEMETRY
void emitPpsTuningConfigSnapshot() {
  char line[CSV_PAYLOAD_MAX];
  const int n = snprintf(line,
                         sizeof(line),
                         "TUNE_CFG,lockR=%u,lockMadTicks=%u,lockN=%u,unlockR=%u,unlockMadTicks=%u,unlockN=%u",
                         (unsigned int)Tunables::ppsLockRppmActive(),
                         (unsigned int)Tunables::ppsLockMadTicksActive(),
                         (unsigned int)Tunables::ppsLockCountActive(),
                         (unsigned int)Tunables::ppsUnlockRppmActive(),
                         (unsigned int)Tunables::ppsUnlockMadTicksActive(),
                         (unsigned int)Tunables::ppsUnlockCountActive());
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}
#else
void emitPpsTuningConfigSnapshot() {}
#endif

void emitStatusPpsConfig() {
  char* line = prepareStatusLineBuf();
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
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);

  n = snprintf(line,
               CSV_PAYLOAD_MAX,
               "pps_freshness,%s=queued_sample_processing,%s=isr_edge_activity",
               PARAM_PPS_STALE_MS,
               PARAM_PPS_ISR_STALE_MS);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

void emitStatusSampleConfig() {
  char* line = prepareStatusLineBuf();
  // CFG carries metadata (including schema ID). The literal sample columns are emitted by HDR.
  const int n = snprintf(line,
                         CSV_PAYLOAD_MAX,
                         "cfg,nominal_hz=%lu,sample_tag=%s,sample_schema=%s",
                         (unsigned long)MAIN_CLOCK_HZ,
                         TAG_SMP,
                         StsHeader::SAMPLE_SCHEMA_ID);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);

  char cfgLine[CSV_PAYLOAD_MAX];
  const int cfg_n = snprintf(cfgLine,
                             sizeof(cfgLine),
                             "nominal_hz=%lu,sample_tag=%s,sample_schema=%s",
                             (unsigned long)MAIN_CLOCK_HZ,
                             TAG_SMP,
                             StsHeader::SAMPLE_SCHEMA_ID);
  if (cfg_n > 0) sendTaggedCsvLine(TAG_CFG, cfgLine);
}

void emitStatusBootHeaders() {
  emitBuildHeader();
  emitSchemaHeader();
  emitFlagsHeader();
  emitStatusTunables();
  emitStatusSampleConfig();
  emitStatusPpsConfig();
}

void emitResetCause() {
  const uint8_t rstfr = RSTCTRL.RSTFR;
  char flags[48];
  size_t pos = 0;

  auto appendFlag = [&](const char* flag) {
    if (pos >= sizeof(flags) - 1) return;
    if (pos != 0 && pos < sizeof(flags) - 1) flags[pos++] = '|';
    for (size_t i = 0; flag[i] != '\0' && pos < sizeof(flags) - 1; ++i) {
      flags[pos++] = flag[i];
    }
    flags[pos] = '\0';
  };

  flags[0] = '\0';
  if (rstfr & RSTCTRL_PORF_bm) appendFlag("PORF");
  if (rstfr & RSTCTRL_BORF_bm) appendFlag("BORF");
  if (rstfr & RSTCTRL_EXTRF_bm) appendFlag("EXTRF");
  if (rstfr & RSTCTRL_WDRF_bm) appendFlag("WDRF");
  if (rstfr & RSTCTRL_SWRF_bm) appendFlag("SWRF");
  if (rstfr & RSTCTRL_UPDIRF_bm) appendFlag("UPDIRF");
  if (pos == 0) appendFlag("NONE");

  char line[96];
  const int n = snprintf(line, sizeof(line), "rstfr,raw=0x%02X,flags=%s", (unsigned int)rstfr, flags);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);

  RSTCTRL.RSTFR = rstfr;
}

void emitStatusTunables() {
  char* line = prepareStatusLineBuf();
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
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);

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
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);

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
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}
