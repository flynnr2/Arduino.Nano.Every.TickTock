#pragma once
#include "Common.h"
#include "PendulumSampleState.h"

namespace NanoComm {
static constexpr uint32_t AGE_UNKNOWN_MS = 0xFFFFFFFFu;
enum class BacklogHealth : uint8_t { Ok, Degraded, Fault };
enum class IngestEventType : uint8_t { CanonicalSwing, CanonicalPps };
struct IngestEvent {
  IngestEventType type;
  union Payload {
    CanonicalSwingSample swing;
    CanonicalPpsSample pps;
    Payload() {}
  } payload;
};
extern PendulumSampleState currentSample;
void service();
void parseIncomingLine(const char* line);
void readStartup();
bool metadataReady();
bool streamingStarted();
bool hasProtocolError();
bool requestGet(const char* param);
bool requestSet(const char* param, const char* value);
bool requestResetDefaults();
bool requestEmitMeta();
void requestRefreshAll();
bool getCachedParam(const char* param, char* out, size_t outLen);
bool hasAllCachedParams();
void invalidateCachedParams();
bool isRefreshInProgress();
uint8_t commandQueueDepth();
uint8_t commandQueuePeakDepth();
bool hasConfig();
bool canonicalSwingAvailable();
bool canonicalPpsAvailable();
bool getCanonicalSwingSample(CanonicalSwingSample& out);
bool getCanonicalPpsSample(CanonicalPpsSample& out);
uint32_t canonicalSwingAgeMs();
uint32_t canonicalPpsAgeMs();
struct LatestPpsStatus {
  bool available = false;
  GpsStatus status = NO_PPS;
  uint32_t age_ms = AGE_UNKNOWN_MS;
  uint32_t holdover_age_ms = 0;
};
struct LatestSwingStatus {
  bool available = false;
  uint32_t age_ms = AGE_UNKNOWN_MS;
};
bool latestPpsStatus(LatestPpsStatus& out);
bool latestSwingStatus(LatestSwingStatus& out);
bool hasValidNominalHz(const PendulumSampleState& sample);
float effectiveHz(const PendulumSampleState& sample);
float effectiveHz();
uint32_t ticksToMicrosAtHz(uint32_t ticks, float hz);
int32_t ticksToMicrosAtHz(int32_t ticks, float hz);
uint32_t ticksToMicros(uint32_t ticks, const PendulumSampleState& sample);
uint32_t ticksToMicros(uint32_t ticks);
float bpmForPeriodAtHz(uint32_t periodTicks, float hz);
uint32_t ticksToUnits(uint32_t ticks);
const char* getDataUnitsLabel();
bool dequeueIngestEvent(IngestEvent& out);
uint8_t ingestQueueDepth();
uint8_t ingestQueueHighWater();
bool hasPendingIngestWork();
BacklogHealth getBacklogHealth();
bool hasBacklogFault();
bool hasIngestBacklog();
unsigned long ingestEventDrops();
unsigned long staleLineDrops();
unsigned long invalidStatusDrops();
unsigned long invalidCanonicalPpsDrops();
}
