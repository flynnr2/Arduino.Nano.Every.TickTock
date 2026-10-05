#include "StatusDisplay.h"
#include "NanoComm.h"
#include "SDLogger.h"
#include "WiFiConfig.h"

static constexpr uint32_t PPS_FRESH_MS = 5000UL;

LedStatusSnapshot buildLedStatusSnapshot(uint32_t) {
  LedStatusSnapshot snap = {};
  NanoComm::LatestSwingStatus swing = {};
  NanoComm::LatestPpsStatus pps = {};
  const bool haveSwing = NanoComm::latestSwingStatus(swing);
  const bool swingAgeKnown = haveSwing && swing.age_ms != NanoComm::AGE_UNKNOWN_MS;
  snap.nano = (!haveSwing || !swingAgeKnown || swing.age_ms >= 10000UL) ? LedHealthState::Fault
              : (swing.age_ms < 5000UL ? LedHealthState::Good : LedHealthState::Degraded);

  const SDLogger::SdHealth sdh = SDLogger::sdHealth();
  snap.sd = (sdh == SDLogger::SdHealth::Mounted) ? LedHealthState::Good :
            (sdh == SDLogger::SdHealth::Recovering ? LedHealthState::Degraded : LedHealthState::Fault);
  if (sdh == SDLogger::SdHealth::Mounted && UnoTunables::logEnabled && NanoComm::metadataReady()) {
    const SDLogger::Snapshot& logs = SDLogger::snapshot();
    const bool measurementsActive = logs.canonicalSwing.active && logs.canonicalPps.active;
    if (!measurementsActive) snap.sd = LedHealthState::Fault;
    else if (!logs.uno.active || !logs.status.active) snap.sd = LedHealthState::Degraded;
  }

  const bool havePps = NanoComm::latestPpsStatus(pps);
  const bool ppsFresh = havePps && pps.age_ms != NanoComm::AGE_UNKNOWN_MS && pps.age_ms < PPS_FRESH_MS;
  if (!havePps || !ppsFresh || pps.status == NO_PPS) snap.pps = LedHealthState::Fault;
  else if (pps.status == LOCKED) snap.pps = LedHealthState::Good;
  else snap.pps = LedHealthState::Degraded;

  const WiFiConfig::NetworkState ws = WiFiConfig::state();
  if (WiFiConfig::networkReadyForHttp() || ws == WiFiConfig::NetworkState::APRunning) snap.wifi = LedHealthState::Good;
  else if (ws == WiFiConfig::NetworkState::TryingSTA || ws == WiFiConfig::NetworkState::TryingAP || ws == WiFiConfig::NetworkState::Backoff || ws == WiFiConfig::NetworkState::APHold || ws == WiFiConfig::NetworkState::STAQuietHold) snap.wifi = LedHealthState::Degraded;
  else snap.wifi = LedHealthState::Fault;

  snap.upload = LedHealthState::Disabled;

  const NanoComm::BacklogHealth backlogHealth = NanoComm::getBacklogHealth();
  snap.backlog_pct = (backlogHealth == NanoComm::BacklogHealth::Fault) ? 100u : (backlogHealth == NanoComm::BacklogHealth::Degraded ? 40u : 0u);
  snap.row_logged_pulse = false;
  snap.last_logged_row_ms = 0;
  snap.sd_fault_active = snap.sd == LedHealthState::Fault;
  snap.nano_fault_active = snap.nano == LedHealthState::Fault;
  snap.backlog_fault_active = backlogHealth == NanoComm::BacklogHealth::Fault;
  snap.pps_fault_active = snap.pps == LedHealthState::Fault;
  snap.wifi_fault_active = snap.wifi == LedHealthState::Fault;
  snap.upload_fault_active = snap.upload == LedHealthState::Fault;
  return snap;
}

#if ENABLE_LED_MATRIX_STATUS
#include <Arduino_LED_Matrix.h>

namespace {
ArduinoLEDMatrix matrix;
bool displayAvailable = false;
uint32_t nextUpdateMs = 0;
uint32_t lastFaultSwitchMs = 0;
uint8_t faultCycleIndex = 0;
uint16_t loggedRowCount = 0;
uint16_t consumedLoggedRowCount = 0;
uint8_t heartbeatX = 0;
struct FaultState { bool active; uint32_t last_seen_ms; };
FaultState faults[(uint8_t)LedFaultCode::Count] = {};
static constexpr uint8_t LED_LOGICAL_W = 12;
static constexpr uint8_t LED_LOGICAL_H = 8;
static uint8_t frame[LED_LOGICAL_H][LED_LOGICAL_W];

enum LedStatusSlot : uint8_t {
  LED_SLOT_NANO = 0,
  LED_SLOT_SD = 1,
  LED_SLOT_BACKLOG = 2,
  LED_SLOT_PPS = 3,
  LED_SLOT_WIFI = 4,
  LED_SLOT_UPLOAD = 5
};
struct SlotOrigin { uint8_t x; uint8_t y; };
static constexpr SlotOrigin kSlotOrigins[6] = {
  {1, 1},  // Nano
  {5, 1},  // SD
  {9, 1},  // Backlog
  {1, 5},  // PPS
  {5, 5},  // WiFi
  {9, 5},  // Upload
};

static_assert((LED_MATRIX_FLIP_X == 0) || (LED_MATRIX_FLIP_X == 1), "LED_MATRIX_FLIP_X must be 0 or 1.");
static_assert((LED_MATRIX_FLIP_Y == 0) || (LED_MATRIX_FLIP_Y == 1), "LED_MATRIX_FLIP_Y must be 0 or 1.");

static void clearFrame() { memset(frame, 0, sizeof(frame)); }
static void setPixelLogical(uint8_t logicalX, uint8_t logicalY, bool on) {
  if (logicalX >= LED_LOGICAL_W || logicalY >= LED_LOGICAL_H) return;
  uint8_t px = logicalX;
  uint8_t py = logicalY;
#if LED_MATRIX_FLIP_X
  px = (uint8_t)(LED_LOGICAL_W - 1u - px);
#endif
#if LED_MATRIX_FLIP_Y
  py = (uint8_t)(LED_LOGICAL_H - 1u - py);
#endif
  frame[py][px] = on ? 1u : 0u;
}
static void fillBlock2x2Logical(uint8_t x, uint8_t y) { for (uint8_t yy = 0; yy < 2; ++yy) for (uint8_t xx = 0; xx < 2; ++xx) setPixelLogical(x + xx, y + yy, true); }
static void faultMark2x2Logical(uint8_t x, uint8_t y, bool on) { if (!on) return; setPixelLogical(x, y, true); setPixelLogical(x + 1, y + 1, true); setPixelLogical(x + 1, y, true); setPixelLogical(x, y + 1, true); }
static bool isOnFast(uint32_t nowMs) { return ((nowMs / 250u) & 1u) == 0; }
static bool isOnSlow(uint32_t nowMs) { return ((nowMs / 750u) & 1u) == 0; }
static void renderHealth(LedHealthState st, uint8_t x, uint8_t y, uint32_t nowMs) {
  switch (st) { case LedHealthState::Good: fillBlock2x2Logical(x, y); break; case LedHealthState::Degraded: if (isOnSlow(nowMs)) fillBlock2x2Logical(x, y); break; case LedHealthState::Fault: faultMark2x2Logical(x, y, isOnFast(nowMs)); break; default: break; }
}
static LedHealthState backlogStateFromPercent(uint8_t backlogPct) {
  if (backlogPct >= 100u) return LedHealthState::Fault;
  if (backlogPct > 0u) return LedHealthState::Degraded;
  return LedHealthState::Good;
}
static void emitFrame() {
  matrix.renderBitmap(frame, LED_LOGICAL_H, LED_LOGICAL_W);
}
static void markFault(LedFaultCode code, bool active, uint32_t nowMs) {
  const uint8_t idx = (uint8_t)code;
  if (idx == 0 || idx >= (uint8_t)LedFaultCode::Count) return;
  faults[idx].active = active;
  if (active) faults[idx].last_seen_ms = nowMs;
}
static bool displayable(LedFaultCode code, uint32_t nowMs) {
  const FaultState& s = faults[(uint8_t)code];
  if (s.active) return true;
  if (LED_FAULT_CLEAR_LINGER_MS == 0u) return false;
  return (uint32_t)(nowMs - s.last_seen_ms) <= LED_FAULT_CLEAR_LINGER_MS;
}
static LedFaultCode pickFault(uint32_t nowMs) {
  const LedFaultCode order[] = {LedFaultCode::Sd, LedFaultCode::Nano, LedFaultCode::Backlog, LedFaultCode::Pps, LedFaultCode::Wifi, LedFaultCode::Upload};
  LedFaultCode visible[6]; uint8_t count = 0;
  for (LedFaultCode c : order) if (displayable(c, nowMs)) visible[count++] = c;
  if (!count) return LedFaultCode::None;
  if (LED_FAULT_SHOW_ONLY_HIGHEST_PRIORITY || !LED_FAULT_CYCLE_ACTIVE || count == 1) return visible[0];
  if ((uint32_t)(nowMs - lastFaultSwitchMs) >= LED_FAULT_CODE_HOLD_MS) { lastFaultSwitchMs = nowMs; faultCycleIndex = (uint8_t)((faultCycleIndex + 1u) % count); }
  if (faultCycleIndex >= count) faultCycleIndex = 0;
  return visible[faultCycleIndex];
}
static void drawGlyphRows(const uint16_t rows[LED_LOGICAL_H]) {
  for (uint8_t y = 0; y < LED_LOGICAL_H; ++y) {
    const uint16_t bits = rows[y];
    for (uint8_t x = 0; x < LED_LOGICAL_W; ++x) {
      const uint16_t mask = (uint16_t)(1u << (LED_LOGICAL_W - 1u - x));
      if ((bits & mask) != 0u) setPixelLogical(x, y, true);
    }
  }
}
static void drawGlyph(LedFaultCode code) {
  static const uint16_t glyphS[LED_LOGICAL_H] = {0b001111111100,0b011000000110,0b011000000000,0b001111111000,0b000000001100,0b000000001100,0b011000011000,0b001111110000};
  static const uint16_t glyphN[LED_LOGICAL_H] = {0b011000000110,0b011100000110,0b011110000110,0b011011000110,0b011001100110,0b011000110110,0b011000011110,0b011000001110};
  static const uint16_t glyphB[LED_LOGICAL_H] = {0b011111111000,0b011000001100,0b011000001100,0b011111111000,0b011000001100,0b011000001100,0b011000001100,0b011111111000};
  static const uint16_t glyphP[LED_LOGICAL_H] = {0b011111111000,0b011000001100,0b011000001100,0b011111111000,0b011000000000,0b011000000000,0b011000000000,0b011000000000};
  static const uint16_t glyphW[LED_LOGICAL_H] = {0b011000000110,0b011000000110,0b011000110110,0b011000110110,0b011011001110,0b011011001110,0b011110000110,0b011100000110};
  static const uint16_t glyphU[LED_LOGICAL_H] = {0b011000000110,0b011000000110,0b011000000110,0b011000000110,0b011000000110,0b011000000110,0b011000000110,0b001111111100};
  switch (code) { case LedFaultCode::Sd: drawGlyphRows(glyphS); break; case LedFaultCode::Nano: drawGlyphRows(glyphN); break; case LedFaultCode::Backlog: drawGlyphRows(glyphB); break; case LedFaultCode::Pps: drawGlyphRows(glyphP); break; case LedFaultCode::Wifi: drawGlyphRows(glyphW); break; case LedFaultCode::Upload: drawGlyphRows(glyphU); break; default: break; }
}
static SlotOrigin slotOrigin(LedStatusSlot slot) { return kSlotOrigins[(uint8_t)slot]; }
// Animation is optional foreground work; never delay live serial ingestion.
static uint8_t startupFrame = 0;
static uint32_t nextStartupFrameMs = 0;
static bool serviceStartupOrientationTest(uint32_t nowMs) {
#if LED_MATRIX_STARTUP_TEST_ENABLED
  const uint8_t frames = LED_MATRIX_STARTUP_TEST_GLYPHS ? 13 : 7;
  if (startupFrame > frames) return false;
  if ((int32_t)(nowMs - nextStartupFrameMs) < 0) return true;
  nextStartupFrameMs = nowMs + LED_MATRIX_STARTUP_TEST_STEP_MS;
  clearFrame();
  if (startupFrame < 6) {
    const SlotOrigin o = kSlotOrigins[startupFrame];
    fillBlock2x2Logical(o.x, o.y);
  } else if (startupFrame == 6) {
    for (uint8_t idx = 0; idx < 6; ++idx) {
      const SlotOrigin o = kSlotOrigins[idx];
      fillBlock2x2Logical(o.x, o.y);
    }
  } else if (startupFrame < frames) {
    static const LedFaultCode glyphs[] = {LedFaultCode::Sd, LedFaultCode::Nano,
      LedFaultCode::Backlog, LedFaultCode::Pps, LedFaultCode::Wifi, LedFaultCode::Upload};
    drawGlyph(glyphs[startupFrame - 7]);
  }
  emitFrame();
  ++startupFrame;
  return true;
#else
  (void)nowMs;
  return false;
#endif
}
}

void statusDisplayBegin() {
  displayAvailable = matrix.begin();
  startupFrame = 0;
  nextStartupFrameMs = millis();
}
void statusDisplayNoteLoggedRow(uint32_t) { ++loggedRowCount; }
void statusDisplayService(const LedStatusSnapshot& s, uint32_t nowMs) {
  if (!displayAvailable) return;
  markFault(LedFaultCode::Sd, s.sd_fault_active, nowMs); markFault(LedFaultCode::Nano, s.nano_fault_active, nowMs); markFault(LedFaultCode::Backlog, s.backlog_fault_active, nowMs); markFault(LedFaultCode::Pps, s.pps_fault_active, nowMs); markFault(LedFaultCode::Wifi, s.wifi_fault_active, nowMs); markFault(LedFaultCode::Upload, s.upload_fault_active, nowMs);
  if (LED_SKIP_WHEN_BACKLOG_CRITICAL && s.backlog_pct >= LED_BACKLOG_CRITICAL_PCT) return;
  if ((int32_t)(nowMs - nextUpdateMs) < 0) return;
  nextUpdateMs = nowMs + LED_STATUS_UPDATE_MS;

  LedFaultCode f = LED_FAULT_OVERRIDE_ENABLED ? pickFault(nowMs) : LedFaultCode::None;
  if (f == LedFaultCode::None && serviceStartupOrientationTest(nowMs)) return;
  clearFrame();
  if (f != LedFaultCode::None) {
    drawGlyph(f);
    emitFrame();
    return;
  }
  const SlotOrigin nano = slotOrigin(LED_SLOT_NANO);
  const SlotOrigin sd = slotOrigin(LED_SLOT_SD);
  const SlotOrigin backlog = slotOrigin(LED_SLOT_BACKLOG);
  const SlotOrigin pps = slotOrigin(LED_SLOT_PPS);
  const SlotOrigin wifi = slotOrigin(LED_SLOT_WIFI);
  const SlotOrigin upload = slotOrigin(LED_SLOT_UPLOAD);
  renderHealth(s.nano, nano.x, nano.y, nowMs);
  renderHealth(s.sd, sd.x, sd.y, nowMs);
  renderHealth(backlogStateFromPercent(s.backlog_pct), backlog.x, backlog.y, nowMs);
  renderHealth(s.pps, pps.x, pps.y, nowMs);
  renderHealth(s.wifi, wifi.x, wifi.y, nowMs);
  renderHealth(s.upload, upload.x, upload.y, nowMs);
  if (LED_HEARTBEAT_ENABLED && consumedLoggedRowCount != loggedRowCount) { consumedLoggedRowCount = loggedRowCount; heartbeatX = (uint8_t)((heartbeatX + 1u) % 12u); }
  if (LED_HEARTBEAT_ENABLED) setPixelLogical(heartbeatX, 7, true);
  emitFrame();
}

#else
void statusDisplayBegin() {}
void statusDisplayNoteLoggedRow(uint32_t) {}
void statusDisplayService(const LedStatusSnapshot&, uint32_t) {}
#endif
