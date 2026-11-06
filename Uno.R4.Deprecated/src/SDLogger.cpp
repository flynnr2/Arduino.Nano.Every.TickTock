#include "SDLogger.h"
#ifdef SDLOGGER_HOST_TEST
#include "sd_logger_host_stubs.h"
#else
#include <SD.h>
#include "Display.h"
#include "DiagLog.h"
#include "NanoComm.h"
#include "PendulumCsvLayout.h"
#include "PendulumProtocol.h"
#include "ProgmemCompat.h"
#include "WiFiConfig.h"
#include "WiFiCompat.h"
#endif
#include <limits.h>
#include <stdlib.h>
#include <string.h>

namespace SDLogger {

static File canonicalSwingFile;
static File canonicalPpsFile;
static File stsFile;
static File unoFile;
static bool sdReady = false;
static SdHealth sdHealthState = SdHealth::Fault;
static bool loggingEnabled = false;
static bool recordingRequested = false;
static bool auxLoggingEnabled = false;
static bool pendulumLoggingEnabled = false;
static LogStartupPolicy startupPolicy = LogStartupPolicy::Overwrite;
static LogMode logMode = LogMode::Continuous;
static char baseFile[LOG_FILENAME_LEN] = LOG_FILENAME;
static char currentFile[LOG_FILENAME_LEN] = LOG_FILENAME;
static char activeSwingFilename[LOG_FILENAME_LEN] = {0};
static char activePpsFilename[LOG_FILENAME_LEN] = {0};
static uint32_t nextCanonicalSet = 1;
static bool canonicalSetSelected = false;
static bool canonicalSetNeedsFresh = false;
static uint16_t stsWritesSinceFlush = 0;
static unsigned long stsLastFlushMs = 0;
static uint16_t unoWritesSinceFlush = 0;
static unsigned long unoLastFlushMs = 0;
static bool stsOpenWarned = false;
static bool unoOpenWarned = false;
static uint16_t canonicalSwingWritesSinceFlush = 0;
static uint16_t canonicalPpsWritesSinceFlush = 0;
static unsigned long canonicalSwingLastFlushMs = 0;
static unsigned long canonicalPpsLastFlushMs = 0;

enum StreamIndex : uint8_t {
  STREAM_CANONICAL_SWING = 0,
  STREAM_CANONICAL_PPS,
  STREAM_STATUS,
  STREAM_UNO,
  STREAM_COUNT
};

static Snapshot loggerSnapshot = {};
static detail::DiagnosticBudget statusBudget = {};
static detail::DiagnosticBudget unoBudget = {};
static uint32_t diagnosticSegmentBytes = 0;
static uint8_t pendingStreamRetryMask = 0;
static bool measurementIoFailureSeen = false;
static bool auxiliaryIoFailureSeen = false;
static unsigned long nextStreamRetryMs = 0;
static const unsigned long STREAM_RETRY_MS = 5000UL;
static const uint8_t RETRY_SWING = 1U << 1;
static const uint8_t RETRY_PPS = 1U << 2;
static const uint8_t RETRY_DIAGNOSTICS = 1U << 3;

static StreamSnapshot& metricsFor(StreamIndex stream) {
  switch (stream) {
    case STREAM_CANONICAL_SWING: return loggerSnapshot.canonicalSwing;
    case STREAM_CANONICAL_PPS: return loggerSnapshot.canonicalPps;
    case STREAM_STATUS: return loggerSnapshot.status;
    case STREAM_UNO:
    default: return loggerSnapshot.uno;
  }
}

static time_t ntpEpoch = 0;
static unsigned long ntpSyncMs = 0;
static unsigned long nextNtpAttemptMs = 0;
static const unsigned long NTP_RETRY_MS   = 15000;
static const unsigned long NTP_RESYNC_MS  = 3600000UL;
static int32_t activeDayIndex = -1;
static uint32_t fallbackDayIndex = 0;
static unsigned long fallbackDayStartMs = 0;
static unsigned long lastSdProbeMs = 0;
static unsigned long lastSdRemountAttemptMs = 0;
static bool recoverLoggingRequested = false;


static void closeAuxFiles();
static void closePendulumFiles();
static void refreshLoggingState();
static void noteWriteDuration(StreamIndex stream, unsigned long startedUs);
static void maybeEscalateAllStreamsFault();
static bool openCanonicalSet(LogStartupPolicy policy, bool resume);
static bool rotateCanonicalSet();
static void checkRollover();

static uint8_t retryBitFor(StreamIndex stream) {
  switch (stream) {
    case STREAM_CANONICAL_SWING: return RETRY_SWING;
    case STREAM_CANONICAL_PPS: return RETRY_PPS;
    default: return RETRY_DIAGNOSTICS;
  }
}

static void scheduleStreamRetry(StreamIndex stream) {
  pendingStreamRetryMask |= retryBitFor(stream);
  nextStreamRetryMs = millis() + STREAM_RETRY_MS;
}

static bool timeIsValid(time_t t) { return t > 1600000000; }
SdHealth sdHealth() { return sdHealthState; }

const Snapshot& snapshot() {
  loggerSnapshot.canonicalSwing.active = (bool)canonicalSwingFile;
  loggerSnapshot.canonicalPps.active = (bool)canonicalPpsFile;
  loggerSnapshot.status.active = (bool)stsFile;
  loggerSnapshot.uno.active = (bool)unoFile;
  return loggerSnapshot;
}

static bool cardDetectPinPresent() { return SD_CARD_DETECT_PIN >= 0; }

static bool cardDetectInserted() {
  if (!cardDetectPinPresent()) return true;
  const int level = digitalRead(SD_CARD_DETECT_PIN);
  return SD_CARD_DETECT_INSERTED_LOW ? (level == LOW) : (level == HIGH);
}

static void markSdFault(SdFaultReason reason) {
  loggerSnapshot.cardFaults++;
  loggerSnapshot.lastCardFault = reason;
  if (sdHealthState != SdHealth::Fault) {
    Display::scrollLog(F("SD fault"));
  }
  sdReady = false;
  sdHealthState = SdHealth::Fault;
  recoverLoggingRequested = recordingRequested;
  closePendulumFiles();
  closeAuxFiles();
  SD.end();
  loggingEnabled = false;
  auxLoggingEnabled = false;
  pendulumLoggingEnabled = false;
}

static bool writeCanonicalHeader(File& file, const char* schemaProgmemFallback) {
  char schema[CANONICAL_SCHEMA_PAYLOAD_MAX_LEN] = {0};
  copyFlashToRam(schema, schemaProgmemFallback, sizeof(schema));
  const size_t schemaLen = strlen(schema);
  static const char suffix[] = ",temperature_C,humidity_pct,pressure_hPa\r\n";
  return file.write(reinterpret_cast<const uint8_t*>(schema), schemaLen) == schemaLen &&
         file.write(reinterpret_cast<const uint8_t*>(suffix), sizeof(suffix) - 1) == sizeof(suffix) - 1;
}

static void buildCanonicalFilename(char* out, size_t len, const char* dailySuffix, const char* continuousName) {
  if (logMode == LogMode::Daily) {
    const char prefix = strstr(dailySuffix, "csw") ? 'S' : 'P';
    bool numericDate = strlen(currentFile) >= 8;
    for (uint8_t i = 0; numericDate && i < 8; ++i) numericDate = isdigit(currentFile[i]);
    if (numericDate) {
      // Prefix plus YYMMDD stays within the 8.3 component limit.
      snprintf(out, len, "%c%.6s.CSV", prefix, currentFile + 2);
    } else {
      snprintf(out, len, "%c%.7s.CSV", prefix, currentFile);
    }
    return;
  }
  strncpy(out, continuousName, len - 1);
  out[len - 1] = '\0';
}

static bool validFilename(const char* fn) {
  if (!fn || !*fn) return false;
  size_t len = strlen(fn);
  if (len >= LOG_FILENAME_LEN) return false;
  for (size_t i=0;i<len;i++) {
    char c = fn[i];
    if (!(isalnum(c) || c=='_' || c=='-' || c=='.')) return false;
  }
  return true;
}

bool isValidFilename(const char* fn) { return validFilename(fn); }

// Arduino SD 1.3.0 does not expose rename.  Archive-and-start-fresh therefore
// selects a new 8.3-safe measurement filename instead of synchronously copying
// the old file.  Existing measurement segments are never recycled.


static bool fileHasIncompleteTail(const char* filename) {
  if (!filename || !SD.exists(filename)) return false;
  File file = SD.open(filename, FILE_READ);
  if (!file) return true;
  const uint32_t size = file.size();
  if (size == 0) {
    file.close();
    return false;
  }
  const bool positioned = file.seek(size - 1U);
  const int lastByte = positioned ? file.read() : -1;
  file.close();
  return lastByte != '\n';
}



bool ready() {
  return pendulumLoggingEnabled;
}
bool isLogging() {
  return loggingEnabled;
}

static void resetFallbackClock() {
  fallbackDayStartMs = millis();
  fallbackDayIndex = 0;
}

static void attemptNtpSync() {
  unsigned long now = millis();
  if ((int32_t)(now - nextNtpAttemptMs) < 0) return;
  if (NanoComm::hasPendingIngestWork()) return;
  nextNtpAttemptMs = now + NTP_RETRY_MS;
  if (WiFi.status() != WL_CONNECTED) return;

  const time_t candidate = WiFi.getTime();
  if (timeIsValid(candidate)) {
    ntpEpoch = candidate;
    ntpSyncMs = millis();
    fallbackDayIndex = (uint32_t)(candidate / 86400UL);
    fallbackDayStartMs = millis() - (unsigned long)((candidate % 86400UL) * 1000UL);
    DiagLog::emit(DiagLog::Severity::Info, F("NTP sync ok"));
    return;
  }
  if (!timeIsValid(ntpEpoch)) {
    DiagLog::emit(DiagLog::Severity::Info, F("NTP retry"));
  }
}

bool hasTimeSync() { return timeIsValid(ntpEpoch); }

time_t currentEpoch() {
  if (!hasTimeSync()) return 0;
  unsigned long elapsed = (millis() - ntpSyncMs) / 1000UL;
  return ntpEpoch + (time_t)elapsed;
}

unsigned long secondsSinceLastSync() {
  if (!hasTimeSync()) return ULONG_MAX;
  return (millis() - ntpSyncMs) / 1000UL;
}

static int32_t computeDayIndex(time_t epochNow) {
  if (timeIsValid(epochNow)) {
    return (int32_t)(epochNow / 86400UL);
  }
  unsigned long nowMs = millis();
  if (fallbackDayStartMs == 0) resetFallbackClock();
  while (nowMs - fallbackDayStartMs >= 86400000UL) {
    fallbackDayStartMs += 86400000UL;
    ++fallbackDayIndex;
  }
  return (int32_t)fallbackDayIndex;
}

static void buildDailyFilename(char* out, size_t len, time_t epochNow, int32_t dayIndex) {
  if (timeIsValid(epochNow)) {
    struct tm* tmNow = gmtime(&epochNow);
    if (tmNow) {
      snprintf(out, len, "%04d%02d%02d.csv", tmNow->tm_year + 1900, tmNow->tm_mon + 1, tmNow->tm_mday);
      return;
    }
  }
  snprintf(out, len, "day%04ld.csv", (long)(dayIndex % 10000L));
}



static void buildCanonicalDiagnosticFilename(bool status, char* out, size_t len) {
  if (strncmp(activePpsFilename, "PCPS", 4) == 0) {
    snprintf(out, len, "%s%s", status ? "STS" : "UNO", activePpsFilename + 4);
  } else if (activePpsFilename[0] == 'P') {
    // Date/fallback names share their suffix with the PPS member.
    snprintf(out, len, "%c%s", status ? 'T' : 'U', activePpsFilename + 1);
  } else {
    snprintf(out, len, "%s", status ? "sts.csv" : "uno.csv");
  }
}

static uint32_t fileSizeByName(const char* filename) {
  if (!SD.exists(filename)) return 0;
  File file = SD.open(filename, FILE_READ);
  if (!file) return DIAGNOSTIC_SEGMENT_BYTES;
  const uint32_t size = file.size();
  file.close();
  return size;
}











static bool openStatusFile() {
  if (stsFile) {
    stsFile.flush();
    stsFile.close();
  }

  char statusName[13];
  buildCanonicalDiagnosticFilename(true, statusName, sizeof(statusName));
  stsFile = SD.open(statusName, FILE_WRITE);
  if (!stsFile) {
    metricsFor(STREAM_STATUS).openFailures++;
    scheduleStreamRetry(STREAM_STATUS);
    if (!stsOpenWarned) {
      Display::scrollLog(F("STS log open fail"));
      stsOpenWarned = true;
    }
    return false;
  }

  if (stsFile.size() == 0) {
    static const char header[] = "ts_ms,raw\r\n";
    if (stsFile.write(reinterpret_cast<const uint8_t*>(header), sizeof(header) - 1) != sizeof(header) - 1) {
      metricsFor(STREAM_STATUS).writeFailures++;
      auxiliaryIoFailureSeen = true;
      stsFile.close();
      stsFile = File();
      scheduleStreamRetry(STREAM_STATUS);
      return false;
    }
    stsFile.flush();
  }
  stsWritesSinceFlush = 0;
  stsLastFlushMs = millis();
  return true;
}

static bool openUnoFile() {
  if (unoFile) {
    unoFile.flush();
    unoFile.close();
  }

  char unoFilename[13];
  buildCanonicalDiagnosticFilename(false, unoFilename, sizeof(unoFilename));
  unoFile = SD.open(unoFilename, FILE_WRITE);
  if (!unoFile) {
    metricsFor(STREAM_UNO).openFailures++;
    scheduleStreamRetry(STREAM_UNO);
    if (!unoOpenWarned) {
      Display::scrollLog(F("UNO log open fail"));
      unoOpenWarned = true;
    }
    return false;
  }

  if (unoFile.size() == 0) {
    static const char header[] = "ts_ms,epoch,category,key,value,msg\r\n";
    if (unoFile.write(reinterpret_cast<const uint8_t*>(header), sizeof(header) - 1) != sizeof(header) - 1) {
      metricsFor(STREAM_UNO).writeFailures++;
      auxiliaryIoFailureSeen = true;
      unoFile.close();
      unoFile = File();
      scheduleStreamRetry(STREAM_UNO);
      return false;
    }
    unoFile.flush();
  }
  unoWritesSinceFlush = 0;
  unoLastFlushMs = millis();
  return true;
}



static void closeAuxFiles() {
  if (stsFile) {
    stsFile.flush();
    stsFile.close();
  }
  if (unoFile) {
    unoFile.flush();
    unoFile.close();
  }
  stsFile = File();
  unoFile = File();
  auxLoggingEnabled = false;
}

static void refreshLoggingState() {
  auxLoggingEnabled = (bool)stsFile || (bool)unoFile;
  pendulumLoggingEnabled = (bool)canonicalSwingFile || (bool)canonicalPpsFile;
  loggingEnabled = auxLoggingEnabled || pendulumLoggingEnabled;
}

static void maybeEscalateAllStreamsFault() {
  if (!sdReady || !recordingRequested || !measurementIoFailureSeen) return;
  bool measurementUnavailable = false;
  measurementUnavailable = !canonicalSwingFile && !canonicalPpsFile;
  if (measurementUnavailable && (auxiliaryIoFailureSeen || (!stsFile && !unoFile))) {
    markSdFault(SdFaultReason::AllStreamsUnavailable);
  }
}

static void flushAuxFile(File& file,
                         uint16_t& writesSinceFlushCounter,
                         unsigned long& lastFlushTimestampMs,
                         unsigned long now,
                         bool forceFlush) {
  if (!file) return;
  if (!forceFlush && writesSinceFlushCounter == 0) return;
  if (!forceFlush && (now - lastFlushTimestampMs) < FLUSH_EVERY_MS) return;

  file.flush();
  writesSinceFlushCounter = 0;
  lastFlushTimestampMs = now;
}

static void closePendulumFiles() {
  if (canonicalSwingFile) {
    canonicalSwingFile.flush();
    canonicalSwingFile.close();
  }
  if (canonicalPpsFile) {
    canonicalPpsFile.flush();
    canonicalPpsFile.close();
  }
  canonicalSwingFile = File();
  canonicalPpsFile = File();
  pendulumLoggingEnabled = false;
}

// A canonical segment is four files. Reserve the suffix across every member,
// including legacy names, before creating any of them. Never recycle a set.
static bool selectFreshCanonicalSet() {
  for (uint8_t attempt = 0; attempt < 8; ++attempt) {
    const uint32_t sequence = nextCanonicalSet++;
    if (nextCanonicalSet > 9999UL) nextCanonicalSet = 1;
    const char* stems[] = {"PCSW", "PCPS", "UNO", "STS"};
    bool occupied = false;
    // Match the active filename buffers even when the compiler cannot infer
    // the four-digit sequence bound across calls. The 8.3 naming policy stays.
    char name[LOG_FILENAME_LEN];
    for (const char* stem : stems) {
      snprintf(name, sizeof(name), "%s%04lu.CSV", stem, (unsigned long)sequence);
      occupied |= SD.exists(name);
    }
    const char legacyPrefixes[] = {'S', 'P'};
    for (char prefix : legacyPrefixes) {
      snprintf(name, sizeof(name), "%c%07lu.CSV", prefix, (unsigned long)sequence);
      occupied |= SD.exists(name);
    }
    const char* legacyStems[] = {"UNO", "STS"};
    for (const char* stem : legacyStems) {
      snprintf(name, sizeof(name), "%s%lu.CSV", stem, (unsigned long)sequence);
      occupied |= SD.exists(name);
    }
    if (occupied) continue;
    snprintf(activeSwingFilename, sizeof(activeSwingFilename), "PCSW%04lu.CSV", (unsigned long)sequence);
    snprintf(activePpsFilename, sizeof(activePpsFilename), "PCPS%04lu.CSV", (unsigned long)sequence);
    canonicalSetSelected = true;
    canonicalSetNeedsFresh = false;
    return true;
  }
  return false;
}

static void deferCanonicalSet(bool fresh) {
  canonicalSetNeedsFresh |= fresh;
  closePendulumFiles();
  closeAuxFiles();
  refreshLoggingState();
  scheduleStreamRetry(STREAM_CANONICAL_SWING);
}

static bool openCanonicalMember(File& file, StreamIndex stream, const char* name,
                                const char* schema) {
  file = SD.open(name, FILE_WRITE);
  if (!file) {
    metricsFor(stream).openFailures++;
    measurementIoFailureSeen = true;
    scheduleStreamRetry(stream);
    return false;
  }
  if (file.size() == 0 && !writeCanonicalHeader(file, schema)) {
    metricsFor(stream).writeFailures++;
    canonicalSetNeedsFresh = true;
    file.close();
    file = File();
    scheduleStreamRetry(stream);
    return false;
  }
  file.flush();
  return true;
}

static bool openCanonicalSet(LogStartupPolicy policy, bool resume) {
  closePendulumFiles();
  closeAuxFiles();
  if (!resume || !canonicalSetSelected) {
    buildCanonicalFilename(activeSwingFilename, sizeof(activeSwingFilename), "_csw.csv", "pcsw.csv");
    buildCanonicalFilename(activePpsFilename, sizeof(activePpsFilename), "_cps.csv", "pcps.csv");
    canonicalSetSelected = true;
    if (!resume) canonicalSetNeedsFresh = false;
  }
  char activeUnoFilename[13];
  char activeStatusFilename[13];
  buildCanonicalDiagnosticFilename(false, activeUnoFilename, sizeof(activeUnoFilename));
  buildCanonicalDiagnosticFilename(true, activeStatusFilename, sizeof(activeStatusFilename));
  const char* names[] = {activeSwingFilename, activePpsFilename,
                         activeUnoFilename, activeStatusFilename};
  uint8_t existing = 0;
  uint32_t sizes[4] = {};
  for (uint8_t i = 0; i < 4; ++i) {
    if (!SD.exists(names[i])) continue;
    ++existing;
    if (policy == LogStartupPolicy::Overwrite && !resume) {
      if (!SD.remove(names[i])) {
        deferCanonicalSet(true);
        return false;
      }
    } else {
      sizes[i] = fileSizeByName(names[i]);
      canonicalSetNeedsFresh |= fileHasIncompleteTail(names[i]);
    }
  }
  canonicalSetNeedsFresh |= sizes[0] >= MEASUREMENT_SEGMENT_BYTES ||
                            sizes[1] >= MEASUREMENT_SEGMENT_BYTES ||
                            !detail::recordFits(sizes[2], sizes[3], DIAGNOSTIC_SEGMENT_BYTES);
  // A cold append has no trusted session identity for existing CSV members.
  // Start a fresh set so a different Nano contract cannot share a recording.
  canonicalSetNeedsFresh |= !resume && existing != 0 &&
      policy != LogStartupPolicy::Overwrite;
  if (canonicalSetNeedsFresh && !selectFreshCanonicalSet()) {
    deferCanonicalSet(true);
    return false;
  }

  pendingStreamRetryMask = 0;
  auxiliaryIoFailureSeen = false;
  const bool swingOk = openCanonicalMember(canonicalSwingFile, STREAM_CANONICAL_SWING,
                                           activeSwingFilename, CANONICAL_SWING_SCHEMA);
  const bool ppsOk = openCanonicalMember(canonicalPpsFile, STREAM_CANONICAL_PPS,
                                         activePpsFilename, CANONICAL_PPS_SCHEMA);
  const bool statusOk = openStatusFile();
  const bool unoOk = openUnoFile();
  // A header short write, like a data short write, ends the whole set.
  if (canonicalSetNeedsFresh || auxiliaryIoFailureSeen) {
    auxiliaryIoFailureSeen = false;
    deferCanonicalSet(true);
    return false;
  }
  diagnosticSegmentBytes = (stsFile ? stsFile.size() : 0) + (unoFile ? unoFile.size() : 0);
  canonicalSwingWritesSinceFlush = canonicalPpsWritesSinceFlush = 0;
  canonicalSwingLastFlushMs = canonicalPpsLastFlushMs = millis();
  refreshLoggingState();
  if (swingOk || ppsOk) measurementIoFailureSeen = false;
  if (!swingOk || !ppsOk || !statusOk || !unoOk) {
    Display::scrollLog(F("Canonical set incomplete; retrying"));
    maybeEscalateAllStreamsFault();
  }
  return loggingEnabled;
}

static bool rotateCanonicalSet() {
  canonicalSetNeedsFresh = true;
  const bool opened = openCanonicalSet(LogStartupPolicy::Append, true);
  if (opened) {
    loggerSnapshot.measurementRotations++;
    loggerSnapshot.diagnosticRotations++;
  }
  return opened;
}



static bool openLogFile(const char* fname, LogStartupPolicy policy, bool resume = false) {
  if (fname && fname != currentFile && validFilename(fname)) {
    strncpy(currentFile, fname, LOG_FILENAME_LEN - 1);
    currentFile[LOG_FILENAME_LEN - 1] = 0;
  }
  if (!sdReady) {
    loggingEnabled = false;
    recoverLoggingRequested = recordingRequested;
    closePendulumFiles();
    closeAuxFiles();
    Display::scrollLog(F("SD not ready"));
    return false;
  }
  stsOpenWarned = false;
  unoOpenWarned = false;
  if (!NanoComm::metadataReady()) return false;
  return openCanonicalSet(policy, resume);
}

void begin() {
  if (cardDetectPinPresent()) {
    pinMode(SD_CARD_DETECT_PIN, INPUT_PULLUP);
  }
  loggerSnapshot = Snapshot();
  recordingRequested = false;
  recoverLoggingRequested = false;
  pendingStreamRetryMask = 0;
  measurementIoFailureSeen = false;
  auxiliaryIoFailureSeen = false;
  nextStreamRetryMs = 0;
  diagnosticSegmentBytes = 0;
  nextCanonicalSet = 1;
  canonicalSetSelected = false;
  canonicalSetNeedsFresh = false;
  activeSwingFilename[0] = '\0';
  activePpsFilename[0] = '\0';
  const unsigned long now = millis();
  statusBudget.reset(now);
  unoBudget.reset(now);
  nextNtpAttemptMs = now;
  lastSdProbeMs = now;
  lastSdRemountAttemptMs = now;
  closePendulumFiles();
  closeAuxFiles();
  if (!SD.begin(SD_CS_PIN)) {
    Display::scrollLog(F("SD init failed"));
    sdReady = false;
    sdHealthState = cardDetectInserted() ? SdHealth::Fault : SdHealth::Missing;
    loggingEnabled = false;
    auxLoggingEnabled = false;
    pendulumLoggingEnabled = false;
    return;
  }
  sdReady = true;
  sdHealthState = SdHealth::Mounted;
  loggingEnabled = false;
  startupPolicy = static_cast<LogStartupPolicy>(LOG_STARTUP_POLICY_DEFAULT);
  strncpy(baseFile, LOG_FILENAME, LOG_FILENAME_LEN - 1);
  baseFile[LOG_FILENAME_LEN - 1] = 0;
  strncpy(currentFile, baseFile, LOG_FILENAME_LEN - 1);
  currentFile[LOG_FILENAME_LEN - 1] = 0;
  resetFallbackClock();
  attemptNtpSync();
  closePendulumFiles();
  closeAuxFiles();
}

void setFilename(const char *fname) {
  if (validFilename(fname)) {
    strncpy(baseFile, fname, LOG_FILENAME_LEN - 1);
    baseFile[LOG_FILENAME_LEN - 1] = 0;
  }
}

void setAppendMode(bool append) {
  startupPolicy = append ? LogStartupPolicy::Append : LogStartupPolicy::Overwrite;
}

void setStartupPolicy(LogStartupPolicy policy) {
  if (policy == LogStartupPolicy::Append ||
      policy == LogStartupPolicy::Overwrite ||
      policy == LogStartupPolicy::ArchiveAndStartFresh) {
    startupPolicy = policy;
  }
}

void setLogMode(LogMode mode) { logMode = mode; }

const char* getFilename() { return baseFile; }
const char* getActiveFilename() { return currentFile; }
const char* getActiveCanonicalSwingFilename() { return activeSwingFilename; }
const char* getActiveCanonicalPpsFilename() { return activePpsFilename; }
bool getAppendMode() { return startupPolicy == LogStartupPolicy::Append; }
LogStartupPolicy getStartupPolicy() { return startupPolicy; }
LogMode getLogMode() { return logMode; }

bool startLogging(const char* fname, bool append) {
  recordingRequested = true;
  setLogMode(LogMode::Continuous);
  setFilename(fname);
  setAppendMode(append);
  activeDayIndex = -1;
  return openLogFile(baseFile, startupPolicy);
}

bool startLogging(LogMode mode, bool forceNewFile) {
  recordingRequested = true;
  logMode = mode;
  char target[LOG_FILENAME_LEN];
  LogStartupPolicy effectivePolicy = startupPolicy;

  if (logMode == LogMode::Daily) {
    time_t epochNow = currentEpoch();
    int32_t dayIndex = computeDayIndex(epochNow);
    buildDailyFilename(target, sizeof(target), epochNow, dayIndex);
    bool newDay = (activeDayIndex >= 0 && dayIndex != activeDayIndex);
    if (forceNewFile) effectivePolicy = LogStartupPolicy::ArchiveAndStartFresh;
    else if (newDay) effectivePolicy = LogStartupPolicy::Append;
    activeDayIndex = dayIndex;
  } else {
    strncpy(target, baseFile, sizeof(target) - 1);
    target[sizeof(target) - 1] = 0;
    if (forceNewFile) effectivePolicy = LogStartupPolicy::ArchiveAndStartFresh;
    activeDayIndex = -1;
  }

  return openLogFile(target, effectivePolicy);
}

bool restartLogging(bool forceNewFile) {
  closePendulumFiles();
  closeAuxFiles();
  return startLogging(logMode, forceNewFile);
}

bool onMetadataReady() {
  if (!sdReady || !recordingRequested || !NanoComm::metadataReady()) return false;
  if (pendulumLoggingEnabled) return true;
  return openLogFile(currentFile, startupPolicy);
}

void stopLogging() {
  closePendulumFiles();
  closeAuxFiles();
  loggingEnabled = false;
  auxLoggingEnabled = false;
  pendulumLoggingEnabled = false;
  recordingRequested = false;
  recoverLoggingRequested = false;
  pendingStreamRetryMask = 0;
}



static void checkRollover() {
  if (!pendulumLoggingEnabled && !auxLoggingEnabled) return;
  if (logMode != LogMode::Daily) return;
  time_t epochNow = currentEpoch();
  int32_t dayIndex = computeDayIndex(epochNow);
  if (activeDayIndex < 0) {
    activeDayIndex = dayIndex;
    return;
  }
  if (dayIndex != activeDayIndex) {
    char target[LOG_FILENAME_LEN];
    buildDailyFilename(target, sizeof(target), epochNow, dayIndex);
    activeDayIndex = dayIndex;
    // A day change selects the new date's paths, not the old active paths.
    openLogFile(target, LogStartupPolicy::Append, false);
  }
}

static void noteWriteDuration(StreamIndex stream, unsigned long startedUs) {
  const uint32_t duration = micros() - startedUs;
  StreamSnapshot& metrics = metricsFor(stream);
  if (duration > metrics.maxWriteDurationUs) metrics.maxWriteDurationUs = duration;
}

static bool ensureMeasurementCapacity(StreamIndex stream, File& file, size_t recordBytes) {
  if (detail::recordFits(file.size(), recordBytes, MEASUREMENT_SEGMENT_BYTES)) return true;
  if (recordBytes > MEASUREMENT_SEGMENT_BYTES) return false;
  (void)stream;
  return rotateCanonicalSet() && file;
}

static bool writeSerializedRecord(File& file,
                                  StreamIndex stream,
                                  const char* line,
                                  size_t len,
                                  uint16_t& writesSinceFlushCounter,
                                  unsigned long& lastFlushTimestampMs) {
  if (!line || len == 0) return false;
  if (!file) return false;

  const unsigned long startedUs = micros();
  if (!ensureMeasurementCapacity(stream, file, len)) {
    metricsFor(stream).rowsLost++;
    noteWriteDuration(stream, startedUs);
    return false;
  }

  const size_t written = file.write(reinterpret_cast<const uint8_t*>(line), len);
  if (written != len) {
    StreamSnapshot& metrics = metricsFor(stream);
    metrics.rowsLost++;
    metrics.writeFailures++;
    measurementIoFailureSeen = true;
    deferCanonicalSet(true);
    noteWriteDuration(stream, startedUs);
    return false;
  }

  writesSinceFlushCounter++;
  const unsigned long now = millis();
  const bool doFlush = (writesSinceFlushCounter >= FLUSH_EVERY_N) ||
                       ((now - lastFlushTimestampMs) >= FLUSH_EVERY_MS);
  if (doFlush) {
    file.flush();
    writesSinceFlushCounter = 0;
    lastFlushTimestampMs = now;
  }
  measurementIoFailureSeen = false;
  noteWriteDuration(stream, startedUs);
  return true;
}



bool logCanonicalSwingSerialized(const char* line, size_t len) {
  if (!recordingRequested) return false;
  if (!pendulumLoggingEnabled || !canonicalSwingFile) {
    metricsFor(STREAM_CANONICAL_SWING).rowsLost++;
    return false;
  }
  checkRollover();
  if (!pendulumLoggingEnabled || !canonicalSwingFile) {
    metricsFor(STREAM_CANONICAL_SWING).rowsLost++;
    return false;
  }
  return writeSerializedRecord(canonicalSwingFile, STREAM_CANONICAL_SWING, line, len, canonicalSwingWritesSinceFlush, canonicalSwingLastFlushMs);
}

bool logCanonicalPpsSerialized(const char* line, size_t len) {
  if (!recordingRequested) return false;
  if (!pendulumLoggingEnabled || !canonicalPpsFile) {
    metricsFor(STREAM_CANONICAL_PPS).rowsLost++;
    return false;
  }
  checkRollover();
  if (!pendulumLoggingEnabled || !canonicalPpsFile) {
    metricsFor(STREAM_CANONICAL_PPS).rowsLost++;
    return false;
  }
  return writeSerializedRecord(canonicalPpsFile, STREAM_CANONICAL_PPS, line, len, canonicalPpsWritesSinceFlush, canonicalPpsLastFlushMs);
}

static size_t escapedCsvLength(const char* value) {
  size_t len = 0;
  if (!value) return 0;
  for (const char* p = value; *p; ++p) len += (*p == '"') ? 2U : 1U;
  return len;
}

static size_t unsignedLength(unsigned long value) {
  size_t len = 1;
  while (value >= 10UL) {
    value /= 10UL;
    ++len;
  }
  return len;
}



static bool ensureDiagnosticCapacity(size_t recordBytes) {
  if (detail::recordFits(diagnosticSegmentBytes, recordBytes, DIAGNOSTIC_SEGMENT_BYTES)) return true;
  if (recordBytes > DIAGNOSTIC_SEGMENT_BYTES) return false;
  if (!rotateCanonicalSet()) return false;
  return detail::recordFits(diagnosticSegmentBytes, recordBytes, DIAGNOSTIC_SEGMENT_BYTES);
}

static bool writeBytes(File& file, const char* value, size_t len) {
  if (len == 0) return true;
  return file.write(reinterpret_cast<const uint8_t*>(value), len) == len;
}

static bool writeText(File& file, const char* value) {
  return !value || writeBytes(file, value, strlen(value));
}

static bool writeEscapedCsv(File& file, const char* value) {
  if (!value) return true;
  for (const char* p = value; *p; ++p) {
    if (*p == '"') {
      if (!writeBytes(file, "\"\"", 2)) return false;
    } else if (file.write(static_cast<uint8_t>(*p)) != 1) {
      return false;
    }
  }
  return true;
}

static bool writeUnsigned(File& file, unsigned long value) {
  char number[11];
  const int len = snprintf(number, sizeof(number), "%lu", value);
  return len > 0 && writeBytes(file, number, static_cast<size_t>(len));
}

static void finishDiagnosticWrite(StreamIndex stream,
                                  File& file,
                                  size_t recordBytes,
                                  bool ok,
                                  uint16_t& writesSinceFlushCounter,
                                  unsigned long& lastFlushTimestampMs,
                                  unsigned long now,
                                  unsigned long startedUs) {
  if (!ok) {
    StreamSnapshot& metrics = metricsFor(stream);
    metrics.rowsLost++;
    metrics.writeFailures++;
    auxiliaryIoFailureSeen = true;
    auxiliaryIoFailureSeen = false;
    deferCanonicalSet(true);
    noteWriteDuration(stream, startedUs);
    return;
  }

  diagnosticSegmentBytes += static_cast<uint32_t>(recordBytes);
  writesSinceFlushCounter++;
  if (writesSinceFlushCounter >= FLUSH_EVERY_N ||
      (now - lastFlushTimestampMs) >= FLUSH_EVERY_MS) {
    file.flush();
    writesSinceFlushCounter = 0;
    lastFlushTimestampMs = now;
  }
  noteWriteDuration(stream, startedUs);
}

void logStatusLine(const char* rawLine) {
  if (!rawLine) return;
  if (!recordingRequested) return;
  checkRollover();
  if (!stsFile) {
    metricsFor(STREAM_STATUS).rowsLost++;
    return;
  }
  const unsigned long now = millis();
  if (!statusBudget.allow(now)) {
    loggerSnapshot.diagnosticsSuppressed++;
    return;
  }
  const size_t recordBytes = unsignedLength(now) + 1U + 1U + escapedCsvLength(rawLine) + 1U + 2U;
  if (!ensureDiagnosticCapacity(recordBytes) || !stsFile) {
    metricsFor(STREAM_STATUS).rowsLost++;
    return;
  }

  const unsigned long startedUs = micros();
  bool ok = writeUnsigned(stsFile, now) &&
            writeBytes(stsFile, ",\"", 2) &&
            writeEscapedCsv(stsFile, rawLine) &&
            writeBytes(stsFile, "\"\r\n", 3);
  finishDiagnosticWrite(STREAM_STATUS, stsFile, recordBytes, ok,
                        stsWritesSinceFlush, stsLastFlushMs, now, startedUs);
}

void logUnoLine(const char* category, const char* key, const char* value) {
  if (!recordingRequested) return;
  checkRollover();
  if (!unoFile) {
    metricsFor(STREAM_UNO).rowsLost++;
    return;
  }
  const unsigned long now = millis();
  if (!unoBudget.allow(now)) {
    loggerSnapshot.diagnosticsSuppressed++;
    return;
  }
  const time_t epochNow = currentEpoch();
  const unsigned long epochValue = timeIsValid(epochNow) ? static_cast<unsigned long>(epochNow) : 0UL;
  const char* safeCategory = category ? category : "";
  const char* safeKey = key ? key : "";
  const char* safeValue = value ? value : "";
  const size_t recordBytes = unsignedLength(now) + unsignedLength(epochValue) +
                             strlen(safeCategory) + strlen(safeKey) + strlen(safeValue) + 7U;
  if (!ensureDiagnosticCapacity(recordBytes) || !unoFile) {
    metricsFor(STREAM_UNO).rowsLost++;
    return;
  }

  const unsigned long startedUs = micros();
  const bool ok = writeUnsigned(unoFile, now) && writeBytes(unoFile, ",", 1) &&
                  writeUnsigned(unoFile, epochValue) && writeBytes(unoFile, ",", 1) &&
                  writeText(unoFile, safeCategory) && writeBytes(unoFile, ",", 1) &&
                  writeText(unoFile, safeKey) && writeBytes(unoFile, ",", 1) &&
                  writeText(unoFile, safeValue) && writeBytes(unoFile, ",\r\n", 3);
  finishDiagnosticWrite(STREAM_UNO, unoFile, recordBytes, ok,
                        unoWritesSinceFlush, unoLastFlushMs, now, startedUs);
}

void logUnoEvent(const char* category, const char* message) {
  if (!recordingRequested) return;
  checkRollover();
  if (!unoFile) {
    metricsFor(STREAM_UNO).rowsLost++;
    return;
  }
  const unsigned long now = millis();
  if (!unoBudget.allow(now)) {
    loggerSnapshot.diagnosticsSuppressed++;
    return;
  }
  const time_t epochNow = currentEpoch();
  const unsigned long epochValue = timeIsValid(epochNow) ? static_cast<unsigned long>(epochNow) : 0UL;
  const char* safeCategory = category ? category : "";
  const size_t recordBytes = unsignedLength(now) + unsignedLength(epochValue) +
                             strlen(safeCategory) + escapedCsvLength(message) + 9U;
  if (!ensureDiagnosticCapacity(recordBytes) || !unoFile) {
    metricsFor(STREAM_UNO).rowsLost++;
    return;
  }

  const unsigned long startedUs = micros();
  const bool ok = writeUnsigned(unoFile, now) && writeBytes(unoFile, ",", 1) &&
                  writeUnsigned(unoFile, epochValue) && writeBytes(unoFile, ",", 1) &&
                  writeText(unoFile, safeCategory) && writeBytes(unoFile, ",,,\"", 4) &&
                  writeEscapedCsv(unoFile, message) && writeBytes(unoFile, "\"\r\n", 3);
  finishDiagnosticWrite(STREAM_UNO, unoFile, recordBytes, ok,
                        unoWritesSinceFlush, unoLastFlushMs, now, startedUs);
}

void logUnoValue(const char* category, const char* key, long value) {
  char buf[24];
  snprintf(buf, sizeof(buf), "%ld", value);
  logUnoLine(category, key, buf);
}

static void serviceOneStreamRetry(unsigned long now) {
  if (!recordingRequested || !pendingStreamRetryMask || !NanoComm::metadataReady()) return;
  if (int32_t(now-nextStreamRetryMs)<0 || NanoComm::hasPendingIngestWork()) return;
  nextStreamRetryMs=now+STREAM_RETRY_MS;
  if (canonicalSetNeedsFresh) rotateCanonicalSet();
  else openCanonicalSet(LogStartupPolicy::Append, true);
}

void service() {
  unsigned long now = millis();
  if (cardDetectPinPresent() && !cardDetectInserted()) {
    if (sdHealthState != SdHealth::Missing) {
      DiagLog::emit(DiagLog::Severity::Info, F("SD removed"));
      sdHealthState = SdHealth::Missing;
      sdReady = false;
      recoverLoggingRequested = recordingRequested;
      closePendulumFiles();
      closeAuxFiles();
      SD.end();
      loggingEnabled = false;
      auxLoggingEnabled = false;
      pendulumLoggingEnabled = false;
    }
  }
  if (!sdReady || sdHealthState == SdHealth::Missing || sdHealthState == SdHealth::Fault) {
    if (!cardDetectInserted()) return;
    if ((unsigned long)(now - lastSdRemountAttemptMs) >= SD_REMOUNT_RETRY_MS) {
      lastSdRemountAttemptMs = now;
      sdHealthState = SdHealth::Recovering;
      if (SD.begin(SD_CS_PIN)) {
        sdReady = true;
        sdHealthState = SdHealth::Mounted;
        DiagLog::emit(DiagLog::Severity::Info, F("SD remounted"));
        if (recoverLoggingRequested) {
          // Recovery is append-only for the exact active session paths.  It
          // must never replay overwrite/archive startup policy.
          const bool recovered = openLogFile(currentFile, LogStartupPolicy::Append, true);
          if (recovered) {
            logUnoEvent("sd.state", "remounted_resume");
          }
          recoverLoggingRequested = false;
        }
      } else {
        sdHealthState = SdHealth::Fault;
      }
    }
    return;
  }
  if ((unsigned long)(now - lastSdProbeMs) >= SD_HEALTH_PROBE_MS) {
    lastSdProbeMs = now;
    File probe = SD.open("/");
    if (!probe) {
      markSdFault(SdFaultReason::RootProbeOpen);
      return;
    }
    probe.close();
  }
  if (!hasTimeSync() || (now - ntpSyncMs) > NTP_RESYNC_MS) {
    attemptNtpSync();
  }
  serviceOneStreamRetry(now);
  if (loggingEnabled && logMode == LogMode::Daily) {
    checkRollover();
  }
  flushAuxFile(canonicalSwingFile, canonicalSwingWritesSinceFlush, canonicalSwingLastFlushMs, now, false);
  flushAuxFile(canonicalPpsFile, canonicalPpsWritesSinceFlush, canonicalPpsLastFlushMs, now, false);
  flushAuxFile(stsFile, stsWritesSinceFlush, stsLastFlushMs, now, false);
  flushAuxFile(unoFile, unoWritesSinceFlush, unoLastFlushMs, now, false);
}

void flushAuxFiles() {
  const unsigned long now = millis();
  flushAuxFile(stsFile, stsWritesSinceFlush, stsLastFlushMs, now, true);
  flushAuxFile(unoFile, unoWritesSinceFlush, unoLastFlushMs, now, true);
}

} // namespace SDLogger
