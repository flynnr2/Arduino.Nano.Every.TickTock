#include "Config.h"

#include <Arduino.h>
#include <math.h>
#include <string.h>

#include "PendulumProtocol.h"
#include "SerialParser.h"
#include "StatusTelemetry.h"
#include "StsHeader.h"
#include "PpsValidator.h"
#include "FreqDiscipliner.h"

static inline uint32_t ppm_from_frac(float f){ if (f<0) f=-f; return (uint32_t)lroundf(f*1.0e6f);}

namespace {
char status_line_buf[CSV_LINE_MAX];

char* prepare_status_line_buf() {
  memset(status_line_buf, 0, sizeof(status_line_buf));
  return status_line_buf;
}

void emit_sts_build_header() {
  char* line = prepare_status_line_buf();
  int n = snprintf(line,
                   CSV_PAYLOAD_MAX,
                   "build,git=%s,dirty=%s,utc=%s,board=NanoEvery,mcu=ATmega4809,f_cpu=%lu,baud=%lu,cfg_ver=%u",
                   GIT_SHA,
                   StsHeader::dirtyField(),
                   BUILD_UTC,
                   (unsigned long)F_CPU,
                   (unsigned long)SERIAL_BAUD_NANO,
                   (unsigned int)StsHeader::CFG_SCHEMA_VER);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

void emit_sts_schema_header() {
  char* line = prepare_status_line_buf();
#if ENABLE_STS_GPS_DEBUG
  #if ENABLE_STS_GPS_SNAP
  int n = snprintf(line, CSV_PAYLOAD_MAX, "schema,gps=%u,gps_health=%u,gps_snap=%u,court=%u",
                   (unsigned int)StsHeader::GPS_SCHEMA_VER,
                   (unsigned int)StsHeader::GPS_HEALTH_SCHEMA_VER,
                   (unsigned int)StsHeader::GPS_SNAP_SCHEMA_VER,
                   (unsigned int)StsHeader::COURT_SCHEMA_VER);
  #else
  int n = snprintf(line, CSV_PAYLOAD_MAX, "schema,gps=%u,gps_health=%u,court=%u",
                   (unsigned int)StsHeader::GPS_SCHEMA_VER,
                   (unsigned int)StsHeader::GPS_HEALTH_SCHEMA_VER,
                   (unsigned int)StsHeader::COURT_SCHEMA_VER);
  #endif
#else
  int n = snprintf(line, CSV_PAYLOAD_MAX, "schema");
#endif
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

void emit_sts_flags_header() {
  char* line = prepare_status_line_buf();
  int n = snprintf(line,
                   CSV_PAYLOAD_MAX,
                   "flags,sts_verbose=%u,gps_dbg_verbose=%u,crlf=%u,evsys_pps=%u,evsys_ir=%u,median3=%u,sts_diag=%u",
                   (unsigned int)StsHeader::FLAG_STS_VERBOSE,
                   (unsigned int)StsHeader::FLAG_GPS_DBG_VERBOSE,
                   (unsigned int)StsHeader::FLAG_CRLF,
                   (unsigned int)StsHeader::FLAG_EVSYS_PPS,
                   (unsigned int)StsHeader::FLAG_EVSYS_IR,
                   (unsigned int)StsHeader::FLAG_MEDIAN3_DEFAULT,
                   (unsigned int)StsHeader::FLAG_STS_DIAG);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

void emit_sts_tunables_header() {
  char* line = prepare_status_line_buf();
  int n = snprintf(line,
                   CSV_PAYLOAD_MAX,
                   "tun1,fastShift=%u,slowShift=%u,blendLo=%u,blendHi=%u,lockRppm=%u,lockMadTicks=%u,lockN=%u,unlockRppm=%u,unlockMadTicks=%u,unlockN=%u,holdoverMs=%u,jump_ppm=%lu,hardTicks=%lu",
                   (unsigned int)Tunables::ppsFastShiftActive(),
                   (unsigned int)Tunables::ppsSlowShiftActive(),
                   (unsigned int)Tunables::ppsBlendLoPpmActive(),
                   (unsigned int)Tunables::ppsBlendHiPpmActive(),
                   (unsigned int)FreqDiscipliner::lockFrequencyErrorThresholdPpm(),
                   (unsigned int)FreqDiscipliner::lockMadThresholdTicks(),
                   (unsigned int)FreqDiscipliner::lockConsecutiveGoodSamplesRequired(),
                   (unsigned int)Tunables::ppsUnlockRppmActive(),
                   (unsigned int)Tunables::ppsUnlockJppmActive(),
                   (unsigned int)Tunables::ppsUnlockCountActive(),
                   (unsigned int)Tunables::ppsHoldoverMsActive(),
                   (unsigned long)ppm_from_frac(Tunables::correctionJumpThresh),
                   (unsigned long)PpsValidator::kHardTicks());
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);

  n = snprintf(line,
               CSV_PAYLOAD_MAX,
               "tun2,k_valid=%u,r_disc_ppm=%u,mad_disc_ticks=%u,staleMs=%u,isrStaleMs=%u,acqMinMs=%u,cfgReemitMs=%u,br_note=dt16-vs-dt32_lo16",
               (unsigned int)PpsValidator::kValid(),
               (unsigned int)FreqDiscipliner::lockFrequencyErrorThresholdPpm(),
               (unsigned int)FreqDiscipliner::lockMadThresholdTicks(),
               (unsigned int)Tunables::ppsStaleMsActive(),
               (unsigned int)Tunables::ppsIsrStaleMsActive(),
               (unsigned int)Tunables::ppsAcquireMinMsActive(),
               (unsigned int)Tunables::ppsConfigReemitDelayMsActive());
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}

} // namespace

#if PPS_TUNING_TELEMETRY
void emitPpsTuningConfigSnapshot() {
  char line[CSV_PAYLOAD_MAX];
  const int n = snprintf(line,
                         sizeof(line),
                         "TUNE_CFG,lockR=%u,lockJ=%u,lockN=%u,unlockR=%u,unlockJ=%u,unlockN=%u",
                         (unsigned int)Tunables::ppsLockRppmActive(),
                         (unsigned int)Tunables::ppsLockJppmActive(),
                         (unsigned int)Tunables::ppsLockCountActive(),
                         (unsigned int)Tunables::ppsUnlockRppmActive(),
                         (unsigned int)Tunables::ppsUnlockJppmActive(),
                         (unsigned int)Tunables::ppsUnlockCountActive());
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);
}
#else
void emitPpsTuningConfigSnapshot() {}
#endif

void emitStatusPpsConfig() {
  char* line = prepare_status_line_buf();
  const uint32_t ref = (uint32_t)F_CPU;
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
}

void emitStatusBootHeaders() {
  emit_sts_build_header();
  emit_sts_schema_header();
  emit_sts_flags_header();
  emit_sts_tunables_header();
  emitStatusPpsConfig();
}

void emitResetCause() {
  const uint8_t rstfr = RSTCTRL.RSTFR;
  char flags[48];
  size_t pos = 0;

  auto append_flag = [&](const char* flag) {
    if (pos >= sizeof(flags) - 1) return;
    if (pos != 0 && pos < sizeof(flags) - 1) flags[pos++] = '|';
    for (size_t i = 0; flag[i] != '\0' && pos < sizeof(flags) - 1; ++i) {
      flags[pos++] = flag[i];
    }
    flags[pos] = '\0';
  };

  flags[0] = '\0';
  if (rstfr & RSTCTRL_PORF_bm) append_flag("PORF");
  if (rstfr & RSTCTRL_BORF_bm) append_flag("BORF");
  if (rstfr & RSTCTRL_EXTRF_bm) append_flag("EXTRF");
  if (rstfr & RSTCTRL_WDRF_bm) append_flag("WDRF");
  if (rstfr & RSTCTRL_SWRF_bm) append_flag("SWRF");
  if (rstfr & RSTCTRL_UPDIRF_bm) append_flag("UPDIRF");
  if (pos == 0) append_flag("NONE");

  char line[96];
  const int n = snprintf(line, sizeof(line), "rstfr,raw=0x%02X,flags=%s", (unsigned int)rstfr, flags);
  if (n > 0) sendStatus(StatusCode::ProgressUpdate, line);

  RSTCTRL.RSTFR = rstfr;
}
