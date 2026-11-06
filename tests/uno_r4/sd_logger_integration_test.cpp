#include <assert.h>
#include <string.h>

#include "sd_logger_host_stubs.h"
#include "../../Uno.R4.Deprecated/src/SDLogger.h"

namespace HostSd {
  std::map<std::string, Entry> files;
  std::set<std::string> openFailures;
  std::map<std::string, size_t> nextWriteLimit;
  std::map<std::string, uint32_t> openAttempts;
  bool mounted = false;
  bool beginSucceeds = true;
  uint32_t nowMs = 0;
  uint32_t nowUs = 0;
}

FakeSDClass SD;
FakeWiFi WiFi;

namespace NanoComm {
  bool metadata = true;
  bool pendingWork = false;
}

static void resetLogger(bool metadataReady = true) {
  SDLogger::stopLogging();
  HostSd::reset();
  NanoComm::metadata = metadataReady;
  NanoComm::pendingWork = false;
  SDLogger::begin();
}

static void testMetadataStartsRecording() {
  resetLogger(false);
  assert(!SDLogger::startLogging(SDLogger::LogMode::Continuous));
  assert(!SDLogger::isLogging());
  NanoComm::metadata = true;
  assert(SDLogger::onMetadataReady());
  assert(SDLogger::ready());
  assert(SDLogger::snapshot().canonicalSwing.active);
  assert(SDLogger::snapshot().canonicalPps.active);
}

static void testBudgetCountsSuppression() {
  resetLogger();
  assert(SDLogger::startLogging(SDLogger::LogMode::Continuous));
  for (uint8_t i = 0; i < SDLogger::detail::DIAGNOSTIC_BURST_ROWS + 1U; ++i)
    SDLogger::logUnoEvent("storm", "same-state diagnostic");
  assert(SDLogger::snapshot().diagnosticsSuppressed == 1);
}

static void testDailyCanonicalNamesStayWithinEightDotThree() {
  resetLogger();
  SDLogger::setLogMode(SDLogger::LogMode::Daily);
  assert(SDLogger::startLogging(SDLogger::LogMode::Daily));
  assert(strlen(SDLogger::getActiveCanonicalSwingFilename()) <= 12);
  assert(strlen(SDLogger::getActiveCanonicalPpsFilename()) <= 12);
}

static void testCanonicalRestartNamesAndLegacyMigration() {
  resetLogger();
  SDLogger::setStartupPolicy(SDLogger::LogStartupPolicy::ArchiveAndStartFresh);
  assert(SDLogger::startLogging(SDLogger::LogMode::Continuous));
  assert(SDLogger::restartLogging(true));
  assert(strcmp(SDLogger::getActiveCanonicalSwingFilename(), "PCSW0001.CSV") == 0);
  assert(strcmp(SDLogger::getActiveCanonicalPpsFilename(), "PCPS0001.CSV") == 0);
  assert(HostSd::files.count("STS0001.CSV") == 1);
  assert(HostSd::files.count("UNO0001.CSV") == 1);
  assert(SDLogger::restartLogging(true));
  assert(strcmp(SDLogger::getActiveCanonicalSwingFilename(), "PCSW0002.CSV") == 0);
  assert(strcmp(SDLogger::getActiveCanonicalPpsFilename(), "PCPS0002.CSV") == 0);

  resetLogger();
  for (const char* name : {"pcsw.csv", "pcps.csv", "S0000001.CSV", "P0000001.CSV",
                          "sts.csv", "uno.csv", "STS1.CSV", "UNO1.CSV"}) {
    HostSd::files[name].bytes = "old\r\n";
    HostSd::files[name].size = 5;
  }
  SDLogger::setStartupPolicy(SDLogger::LogStartupPolicy::ArchiveAndStartFresh);
  assert(SDLogger::startLogging(SDLogger::LogMode::Continuous));
  assert(strcmp(SDLogger::getActiveCanonicalSwingFilename(), "PCSW0002.CSV") == 0);
  assert(strcmp(SDLogger::getActiveCanonicalPpsFilename(), "PCPS0002.CSV") == 0);
  assert(HostSd::files.count("STS0002.CSV") == 1);
  assert(HostSd::files.count("UNO0002.CSV") == 1);
  assert(HostSd::files["S0000001.CSV"].bytes == "old\r\n");
  assert(HostSd::files["UNO1.CSV"].bytes == "old\r\n");
}

static void assertCanonicalSet(unsigned sequence) {
  char name[13];
  snprintf(name, sizeof(name), "PCSW%04u.CSV", sequence);
  assert(strcmp(SDLogger::getActiveCanonicalSwingFilename(), name) == 0);
  snprintf(name, sizeof(name), "PCPS%04u.CSV", sequence);
  assert(strcmp(SDLogger::getActiveCanonicalPpsFilename(), name) == 0);
  for (const char* stem : {"PCSW", "PCPS", "UNO", "STS"}) {
    snprintf(name, sizeof(name), "%s%04u.CSV", stem, sequence);
    assert(HostSd::files.count(name) == 1);
    assert(!HostSd::files[name].bytes.empty());
    assert(HostSd::files[name].bytes.back() == '\n');
  }
}

static void testEachCanonicalSizeTriggerRotatesWholeSet() {
  static const char row[] = "1,2\r\n";
  for (bool pps : {false, true}) {
    resetLogger();
    assert(SDLogger::startLogging(SDLogger::LogMode::Continuous));
    HostSd::files[pps ? "pcps.csv" : "pcsw.csv"].size = SDLogger::MEASUREMENT_SEGMENT_BYTES - 2;
    const auto oldFiles = HostSd::files;
    assert(pps ? SDLogger::logCanonicalPpsSerialized(row, sizeof(row) - 1)
               : SDLogger::logCanonicalSwingSerialized(row, sizeof(row) - 1));
    assertCanonicalSet(1);
    SDLogger::logUnoEvent("set", "new");
    SDLogger::logStatusLine("new");
    assert(SDLogger::logCanonicalPpsSerialized(row, sizeof(row) - 1));
    assert(SDLogger::logCanonicalSwingSerialized(row, sizeof(row) - 1));
    for (const char* name : {"pcps.csv", "pcsw.csv", "uno.csv", "sts.csv"}) {
      assert(HostSd::files[name].bytes == oldFiles.at(name).bytes);
      assert(HostSd::files[name].size == oldFiles.at(name).size);
    }
    assert(HostSd::files["UNO0001.CSV"].bytes.find("new") != std::string::npos);
    assert(HostSd::files["STS0001.CSV"].bytes.find("new") != std::string::npos);
  }

}

static void testCanonicalSetsNeverRecycleAndSkipAnyOccupiedMember() {
  resetLogger();
  assert(SDLogger::startLogging(SDLogger::LogMode::Continuous));
  HostSd::files["UNO0001.CSV"].bytes = "retained\n";
  HostSd::files["UNO0001.CSV"].size = 9;
  HostSd::files["P0000002.CSV"].bytes = "legacy\n";
  HostSd::files["P0000002.CSV"].size = 7;
  for (unsigned sequence = 3; sequence <= 8; ++sequence) {
    assert(SDLogger::restartLogging(true));
    assertCanonicalSet(sequence);
    SDLogger::logUnoEvent("retained", "set");
    SDLogger::logStatusLine("retained set");
  }
  assert(HostSd::files["UNO0001.CSV"].bytes == "retained\n");
  assert(HostSd::files["P0000002.CSV"].bytes == "legacy\n");
  assert(HostSd::files["UNO0003.CSV"].bytes.find("retained") != std::string::npos);
  assert(HostSd::files["STS0003.CSV"].bytes.find("retained") != std::string::npos);
}

static void testCanonicalPartialWritesEndWholeSetAndRecoveryStaysAligned() {
  static const char row[] = "1,2\r\n";
  for (const char* failed : {"pcps.csv", "pcsw.csv", "uno.csv", "sts.csv"}) {
    resetLogger();
    assert(SDLogger::startLogging(SDLogger::LogMode::Continuous));
    HostSd::advance(10); // The first diagnostic write (timestamp) exceeds one byte.
    HostSd::nextWriteLimit[failed] = 1;
    if (strcmp(failed, "pcps.csv") == 0) assert(!SDLogger::logCanonicalPpsSerialized(row, sizeof(row) - 1));
    if (strcmp(failed, "pcsw.csv") == 0) assert(!SDLogger::logCanonicalSwingSerialized(row, sizeof(row) - 1));
    if (strcmp(failed, "uno.csv") == 0) SDLogger::logUnoEvent("partial", "event");
    if (strcmp(failed, "sts.csv") == 0) SDLogger::logStatusLine("partial");
    const auto oldFiles = HostSd::files;
    const auto& snapshot = SDLogger::snapshot();
    assert(!snapshot.canonicalPps.active && !snapshot.canonicalSwing.active);
    assert(!snapshot.status.active && !snapshot.uno.active);
    assert(!SDLogger::logCanonicalPpsSerialized(row, sizeof(row) - 1));
    HostSd::advance(5000);
    SDLogger::service();
    assertCanonicalSet(1);
    for (const auto& entry : oldFiles) assert(HostSd::files[entry.first].bytes == entry.second.bytes);
    assert(HostSd::files[failed].bytes.back() != '\n');
  }
}

static void testCanonicalOpenFailureRetriesSameSet() {
  for (const char* failed : {"PCPS0001.CSV", "PCSW0001.CSV", "UNO0001.CSV", "STS0001.CSV"}) {
    resetLogger();
    assert(SDLogger::startLogging(SDLogger::LogMode::Continuous));
    HostSd::openFailures.insert(failed);
    assert(SDLogger::restartLogging(true));
    assert(strcmp(SDLogger::getActiveCanonicalPpsFilename(), "PCPS0001.CSV") == 0);
    assert(HostSd::files.count(failed) == 0);
    HostSd::openFailures.clear();
    HostSd::advance(5000);
    SDLogger::service();
    assertCanonicalSet(1);
    assert(SDLogger::snapshot().canonicalPps.active);
    assert(HostSd::files.count("PCSW0002.CSV") == 0);
  }
}

static void testCanonicalDailyRolloverMovesDiagnosticsToo() {
  resetLogger();
  HostSd::advance(1);
  assert(SDLogger::startLogging(SDLogger::LogMode::Daily));
  const std::string firstSwing = SDLogger::getActiveCanonicalSwingFilename();
  const std::string firstPps = SDLogger::getActiveCanonicalPpsFilename();
  const auto oldFiles = HostSd::files;
  HostSd::advance(86400000);
  // A diagnostic can be the first record on the new day.
  SDLogger::logStatusLine("new day");
  assert(firstSwing != SDLogger::getActiveCanonicalSwingFilename());
  assert(firstPps != SDLogger::getActiveCanonicalPpsFilename());
  std::string status = SDLogger::getActiveCanonicalPpsFilename();
  status[0] = 'T';
  std::string uno = status;
  uno[0] = 'U';
  assert(HostSd::files[status].bytes.find("new day") != std::string::npos);
  assert(HostSd::files.count(uno) == 1);
  for (const auto& entry : oldFiles) assert(HostSd::files[entry.first].bytes == entry.second.bytes);
}

static void testCanonicalUnknownModePreservesExistingDiagnostics() {
  resetLogger(false);
  HostSd::files["UNO0001.CSV"].bytes = "retained\n";
  HostSd::files["UNO0001.CSV"].size = 9;
  assert(!SDLogger::startLogging(SDLogger::LogMode::Continuous));
  assert(HostSd::files["UNO0001.CSV"].bytes == "retained\n");
  NanoComm::metadata = true;
  assert(SDLogger::onMetadataReady());
  assert(HostSd::files["UNO0001.CSV"].bytes == "retained\n");
}

static void testCanonicalRemountAndStopPreserveSet() {
  resetLogger();
  assert(SDLogger::startLogging(SDLogger::LogMode::Continuous));
  assert(SDLogger::restartLogging(true));
  const auto oldFiles = HostSd::files;
  HostSd::mounted = false;
  HostSd::advance(1500);
  SDLogger::service();
  assert(SDLogger::sdHealth() == SDLogger::SdHealth::Fault);
  HostSd::advance(3000);
  SDLogger::service();
  assertCanonicalSet(1);
  assert(HostSd::files.count("PCPS0002.CSV") == 0);
  assert(HostSd::files["PCSW0001.CSV"].bytes == oldFiles.at("PCSW0001.CSV").bytes);

  HostSd::mounted = false;
  HostSd::advance(1500);
  SDLogger::service();
  // Simulate a partial tail discovered during card recovery.
  HostSd::files["STS0001.CSV"].bytes += "partial";
  HostSd::files["STS0001.CSV"].size += 7;
  HostSd::advance(3000);
  SDLogger::service();
  assertCanonicalSet(2);
  assert(HostSd::files["STS0001.CSV"].bytes.back() == 'l');
  static const char row[] = "1,2\n";
  HostSd::nextWriteLimit["PCPS0002.CSV"] = 1;
  assert(!SDLogger::logCanonicalPpsSerialized(row, sizeof(row) - 1));
  SDLogger::stopLogging();
  HostSd::advance(5000);
  SDLogger::service();
  assert(!SDLogger::isLogging());
  assert(HostSd::files.count("PCPS0003.CSV") == 0);
}

static void testCanonicalHeaderFailureAndBoundedAllocation() {
  for (const char* failed : {"pcps.csv", "pcsw.csv", "uno.csv", "sts.csv"}) {
    resetLogger();
    HostSd::nextWriteLimit[failed] = 1;
    assert(!SDLogger::startLogging(SDLogger::LogMode::Continuous));
    HostSd::advance(5000);
    SDLogger::service();
    assertCanonicalSet(1);
    assert(HostSd::files[failed].bytes.size() == 1);
  }

  resetLogger();
  assert(SDLogger::startLogging(SDLogger::LogMode::Continuous));
  for (unsigned sequence = 1; sequence <= 8; ++sequence) {
    char name[13];
    snprintf(name, sizeof(name), "STS%04u.CSV", sequence);
    HostSd::files[name].bytes = "retained\n";
    HostSd::files[name].size = 9;
  }
  assert(!SDLogger::restartLogging(true));
  assert(!SDLogger::isLogging());
  assert(HostSd::files.count("PCPS0009.CSV") == 0);
  HostSd::advance(5000);
  SDLogger::service();
  assertCanonicalSet(9);
  assert(HostSd::files["STS0001.CSV"].bytes == "retained\n");
}

static void testCanonicalColdRestartPoliciesUseWholeSets() {
  static const char row[] = "1,2\n";
  for (auto policy : {SDLogger::LogStartupPolicy::Overwrite,
                      SDLogger::LogStartupPolicy::Append,
                      SDLogger::LogStartupPolicy::ArchiveAndStartFresh}) {
    resetLogger();
    assert(SDLogger::startLogging(SDLogger::LogMode::Continuous));
    assert(SDLogger::logCanonicalPpsSerialized(row, sizeof(row) - 1));
    assert(SDLogger::logCanonicalSwingSerialized(row, sizeof(row) - 1));
    SDLogger::logUnoEvent("original", "base");
    SDLogger::logStatusLine("original base");
    assert(SDLogger::restartLogging(true));
    assertCanonicalSet(1);
    const auto beforeBoot = HostSd::files;
    SDLogger::stopLogging();
    SDLogger::begin(); // Restart volatile state, retaining the card's files.
    SDLogger::setStartupPolicy(policy);
    assert(SDLogger::startLogging(SDLogger::LogMode::Continuous));
    if (policy != SDLogger::LogStartupPolicy::Overwrite) {
      assertCanonicalSet(2);
    } else {
      assert(strcmp(SDLogger::getActiveCanonicalPpsFilename(), "pcps.csv") == 0);
      assert(strcmp(SDLogger::getActiveCanonicalSwingFilename(), "pcsw.csv") == 0);
    }
    for (const char* name : {"pcps.csv", "pcsw.csv", "uno.csv", "sts.csv"}) {
      if (policy == SDLogger::LogStartupPolicy::Overwrite) {
        assert(HostSd::files[name].bytes.size() < beforeBoot.at(name).bytes.size());
      } else {
        assert(HostSd::files[name].bytes == beforeBoot.at(name).bytes);
      }
    }
    for (const char* name : {"PCPS0001.CSV", "PCSW0001.CSV", "UNO0001.CSV", "STS0001.CSV"}) {
      assert(HostSd::files[name].bytes == beforeBoot.at(name).bytes);
    }
  }
  // One existing member is enough to occupy the base set under Append.
  resetLogger();
  HostSd::files["uno.csv"].bytes = "old\n";
  HostSd::files["uno.csv"].size = 4;
  SDLogger::setStartupPolicy(SDLogger::LogStartupPolicy::Append);
  assert(SDLogger::startLogging(SDLogger::LogMode::Continuous));
  assertCanonicalSet(1);
  assert(HostSd::files["uno.csv"].bytes == "old\n");
  assert(HostSd::files.count("pcps.csv") == 0);
}

int main() {
  testCanonicalColdRestartPoliciesUseWholeSets();
  testEachCanonicalSizeTriggerRotatesWholeSet();
  testCanonicalSetsNeverRecycleAndSkipAnyOccupiedMember();
  testCanonicalPartialWritesEndWholeSetAndRecoveryStaysAligned();
  testCanonicalOpenFailureRetriesSameSet();
  testCanonicalDailyRolloverMovesDiagnosticsToo();
  testCanonicalUnknownModePreservesExistingDiagnostics();
  testCanonicalRemountAndStopPreserveSet();
  testCanonicalHeaderFailureAndBoundedAllocation();
  testMetadataStartsRecording();
  testBudgetCountsSuppression();
  testDailyCanonicalNamesStayWithinEightDotThree();
  testCanonicalRestartNamesAndLegacyMigration();
  return 0;
}
