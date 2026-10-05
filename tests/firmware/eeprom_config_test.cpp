#include <cassert>
#include <string>
#include <vector>
#include "EEPROM.h"
#include "EEPROMConfig.h"
#include "TunableCommands.h"
#include "TunableRegistry.h"
#include "SerialParser.h"
FakeEeprom EEPROM;
Print Serial;
static StatusCode status;
static std::string message;
static char buffer[CSV_PAYLOAD_MAX];
char* tryAcquireFormatBuffer(FormatBufferOwner) { return buffer; }
void releaseFormatBuffer(FormatBufferOwner) {}
void sendStatus(StatusCode code, const char* text, EmissionReliability) { status = code; message = text; }
void sendStatusFromOwnedBuffer(FormatBufferOwner, StatusCode code, char* text, EmissionReliability r) { sendStatus(code,text,r); }
void emitPpsTuningConfigSnapshot() {}
void resetRuntimeStateAfterTunablesChange() {}
void emitStatusTunables() {}
void emitStatusSampleConfig() {}
void emitStatusPpsConfig() {}
void printCsvHeader() {}
static unsigned eepromReports = 0;
static EepromLoadDiag reportedEeprom;
void emitEepromLoadStatus() { ++eepromReports; reportedEeprom = getEepromLoadDiag(); }
static bool startup_output_ready = true, metadata_reemit_pending = false;
void emitResetCause() {}
void emitStatusBootHeaders() {}
#include "startup_replay_under_test.inc"
void set(const char* name, const char* value) {
 std::vector<char> n(name, name+strlen(name)+1), v(value, value+strlen(value)+1);
 handleSetCommand(n.data(), v.data());
}
int main() {
 EEPROM.reset(); Tunables::restoreDefaults();
 TunableConfig cfg=getCurrentConfig(), loaded;
 assert(validateConfig(cfg));
 char action[] = "eeprom";
 handleRepairCommand(action); // Failed repair still reports current health.
 assert(status==StatusCode::InternalError && eepromReports==1);
 assert(reportedEeprom.source=='D');
 emitStartupNow();
 assert(eepromReports==2 && reportedEeprom.source=='D');
 startup_output_ready=false;
 emitStartupNow();
 assert(eepromReports==2 && metadata_reemit_pending);
 startup_output_ready=true;
 assert(!repairEeprom()); // No valid source: no writes, no invented defaults.
 assert(saveConfig(cfg));
 assert(repairEeprom());
 assert(getEepromLoadDiag().slotA==EepromSlotCode::Ok && getEepromLoadDiag().slotB==EepromSlotCode::Ok);
 const auto seq=getEepromLoadDiag().sequence;
 assert(repairEeprom() && getEepromLoadDiag().sequence==seq); // idempotent
 // Reproduce sem with a correct CRC and impossible shift, retaining good B.
 EEPROM.bytes[16]=0;
 const auto crc=computeCRC16(EEPROM.bytes+16,30);
 EEPROM.bytes[10]=crc & 255; EEPROM.bytes[11]=crc >> 8;
 assert(getEepromLoadDiag().slotA==EepromSlotCode::Sem);
 uint8_t good[64]; memcpy(good,EEPROM.bytes+64,64);
 assert(repairEeprom());
 assert(memcmp(good,EEPROM.bytes+64,64)==0);
 handleRepairCommand(action);
 assert(status==StatusCode::Ok && message=="repair,eeprom" && eepromReports==3);
 assert(reportedEeprom.slotA==EepromSlotCode::Ok && reportedEeprom.slotB==EepromSlotCode::Ok);
 emitStartupNow();
 assert(eepromReports==4 && reportedEeprom.slotA==EepromSlotCode::Ok);
 assert(reportedEeprom.sequence==getEepromLoadDiag().sequence);
 assert(getEepromLoadDiag().slotA==EepromSlotCode::Ok);
 // Every interruption point preserves the newest complete record, including
 // cuts while rewriting an already committed older slot's sequence/header.
 uint8_t saved[256]; memcpy(saved,EEPROM.bytes,256);
 cfg.ppsFastShift=4;
 for (int cut=0;cut<50;++cut) {
   memcpy(EEPROM.bytes,saved,256); EEPROM.updates=0; EEPROM.cut=cut;
   const bool success=saveConfig(cfg);
   assert(loadConfig(loaded));
   assert(loaded.ppsFastShift==(success ? 4 : 3));
   assert(memcmp(EEPROM.bytes,saved,64)==0); // source A is untouched
 }
 EEPROM.cut=-1; memcpy(EEPROM.bytes,saved,256);
 // Stuck payload and committed-marker cells fail verification and preserve RAM.
 for (int stuck : {64+16,64+12}) {
   memcpy(EEPROM.bytes,saved,256); EEPROM.stuck=stuck;
   set("ppsFastShift","4");
   assert(status==StatusCode::InternalError);
   assert(Tunables::ppsFastShift==3);
   assert(loadConfig(loaded) && loaded.ppsFastShift==3);
 }
 EEPROM.stuck=-1;
 for (const auto& pair : std::vector<std::pair<const char*,const char*>>{
   {"ppsFastShift","0"},{"ppsSlowShift","2"},{"ppsUnlockCount","100"},
   {"ppsStaleMs","30001"},{"ppsHoldoverMs","0"},{"ppsCfgReemitDelayMs","0"},
   {"ppsBlendLoPpm","150"},{"ppsUnlockRppm","1"},{"ppsLockMadTicks","901"},
   {"ppsFastShift","-1"},{"ppsFastShift","+3"},{"ppsFastShift"," 3"},
   {"ppsFastShift","999999999999999999999999999999"}}) {
   const auto before=getCurrentConfig();
   set(pair.first,pair.second);
   assert(status==StatusCode::InvalidValue);
   const auto after=getCurrentConfig();
   assert(after.ppsFastShift==before.ppsFastShift && after.ppsSlowShift==before.ppsSlowShift);
   assert(validateConfig(after));
 }
 set("ppsFastShift","4"); assert(status==StatusCode::Ok);
 assert(loadConfig(loaded) && loaded.ppsFastShift==4);
 set("ppsMetrologyGraceMs","120000");
#if PPS_TUNING_TELEMETRY
 assert(status==StatusCode::Ok);
 set("ppsMetrologyGraceMs","86400001"); assert(status==StatusCode::InvalidValue);
#else
 assert(status==StatusCode::InvalidValue && message.find("inactive")!=std::string::npos);
#endif
}
