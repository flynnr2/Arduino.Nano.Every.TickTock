#include "IngestOrchestrator.h"

#include "NanoComm.h"
#include "DiagLog.h"
#include "SDLogger.h"
#include "RecordSerializer.h"
#include "PublishMirrorQueue.h"
#include "Sensors.h"
#include "WiFiConfig.h"
#include "StatusDisplay.h"
#include "Display.h"
#include <cstdio>
#include <cstring>

namespace IngestOrchestrator {
namespace {
struct SeqTracker { bool seen=false; uint32_t last=0; };
static SeqTracker pcpsSeq;
static SeqTracker pcswSeq;
static uint32_t pcpsGapCount = 0;
static uint32_t pcswGapCount = 0;
static uint32_t pcpsMissing = 0;
static uint32_t pcswMissing = 0;
static uint32_t lastGapMs = 0;
static constexpr unsigned long SEQ_GAP_DIAG_COOLDOWN_MS = 5000;
static void noteSeqGap(const char* stream, SeqTracker& tracker, uint32_t seq) {
  const uint32_t step = seq - tracker.last;
  if (!tracker.seen || step == 0 || step >= 0x80000000UL) {
    char msg[100] = {0};
    const char* event = !tracker.seen ? "joined" : (step == 0 ? "duplicate" : "restart_or_reorder");
    snprintf(msg, sizeof(msg), "stream,%s,event,%s,seq,%lu", stream, event, (unsigned long)seq);
    SDLogger::logUnoEvent("ingest.sequence", msg);
    // A backwards sequence may be an independent Nano restart. Refresh
    // metadata without resetting ingestion or inventing a missing-record count.
    if (tracker.seen && step >= 0x80000000UL) NanoComm::requestEmitMeta();
  } else if (step > 1) {
    char msg[180] = {0};
    const uint32_t missing = step - 1;
    snprintf(msg, sizeof(msg),
             "stream,%s,prev_seq,%lu,curr_seq,%lu,missing,%lu,wifi_state,%s,http_enabled,%d",
             stream,
             (unsigned long)tracker.last,
             (unsigned long)seq,
             (unsigned long)missing,
             WiFiConfig::stateName(WiFiConfig::state()),
             WiFiConfig::networkReadyForHttp() ? 1 : 0);
    SDLogger::logUnoEvent("ingest.seq_gap", msg);
    DiagLog::emitCooldown(DiagLog::Severity::Warn,
                          DiagLog::MessageId::IngestSeqGap,
                          F("Ingest sequence gap detected"),
                          SEQ_GAP_DIAG_COOLDOWN_MS);
    if (strcmp(stream, "PCPS") == 0) {
      ++pcpsGapCount;
      pcpsMissing += missing;
    } else if (strcmp(stream, "PCSW") == 0) {
      ++pcswGapCount;
      pcswMissing += missing;
    }
    lastGapMs = millis();
  }
  tracker.seen = true;
  tracker.last = seq;
}
}

static void attachLatestEnvironment() {
  float t, h, p;
  Sensors::getLatest(t, h, p);
  NanoComm::currentSample.temperature_C = t;
  NanoComm::currentSample.humidity_pct = h;
  NanoComm::currentSample.pressure_hPa = p;
}

void service() {
  static uint32_t publishSeq = 0;
  NanoComm::IngestEvent event = {};
  while (NanoComm::dequeueIngestEvent(event)) {
    switch (event.type) {
      case NanoComm::IngestEventType::CanonicalSwing: {
        noteSeqGap("PCSW", pcswSeq, event.payload.swing.seq);
        Display::observeSwing(event.payload.swing);
        attachLatestEnvironment();

        char row[RecordSerializer::CANONICAL_SWING_RECORD_MAX_LEN] = {0};
        size_t rowLen = 0;
        if (RecordSerializer::serializeCanonicalSwing(event.payload.swing,
                                                      NanoComm::currentSample.temperature_C,
                                                      NanoComm::currentSample.humidity_pct,
                                                      NanoComm::currentSample.pressure_hPa,
                                                      row,
                                                      sizeof(row),
                                                      rowLen)) {
          if (SDLogger::logCanonicalSwingSerialized(row, rowLen)) {
            statusDisplayNoteLoggedRow(millis());
            PublishMirrorQueue::enqueue(row,
                                       rowLen,
                                       PublishMirrorQueue::RecordKind::CanonicalSwing,
                                       ++publishSeq);
          }
        }
        break;
      }
      case NanoComm::IngestEventType::CanonicalPps: {
        noteSeqGap("PCPS", pcpsSeq, event.payload.pps.seq);
        Display::observePps(event.payload.pps);
        attachLatestEnvironment();

        char row[RecordSerializer::CANONICAL_PPS_RECORD_MAX_LEN] = {0};
        size_t rowLen = 0;
        if (RecordSerializer::serializeCanonicalPps(event.payload.pps,
                                                    NanoComm::currentSample.temperature_C,
                                                    NanoComm::currentSample.humidity_pct,
                                                    NanoComm::currentSample.pressure_hPa,
                                                    row,
                                                    sizeof(row),
                                                    rowLen)) {
          if (SDLogger::logCanonicalPpsSerialized(row, rowLen)) {
            statusDisplayNoteLoggedRow(millis());
            PublishMirrorQueue::enqueue(row,
                                       rowLen,
                                       PublishMirrorQueue::RecordKind::CanonicalPps,
                                       ++publishSeq);
          }
        }
        break;
      }
      default:
        break;
    }
  }
}

uint32_t pcpsSeqGapCount() { return pcpsGapCount; }
uint32_t pcswSeqGapCount() { return pcswGapCount; }
uint32_t pcpsMissingTotal() { return pcpsMissing; }
uint32_t pcswMissingTotal() { return pcswMissing; }
uint32_t lastPcpsSeq() { return pcpsSeq.last; }
uint32_t lastPcswSeq() { return pcswSeq.last; }
bool hadRecentGap(uint32_t withinMs) {
  if (pcpsGapCount == 0 && pcswGapCount == 0) return false;
  return (uint32_t)(millis() - lastGapMs) <= withinMs;
}

} // namespace IngestOrchestrator
