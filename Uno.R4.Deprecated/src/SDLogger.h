#pragma once
#include <stddef.h>
#include <stdint.h>
#include <time.h>

namespace SDLogger {
  enum class LogMode : uint8_t { Continuous = 0, Daily = 1 };
  enum class LogStartupPolicy : uint8_t { Append = 0, Overwrite = 1, ArchiveAndStartFresh = 2 };
  enum class SdHealth : uint8_t { Mounted = 0, Missing = 1, Recovering = 2, Fault = 3 };
  enum class SdFaultReason : uint8_t { None = 0, RootProbeOpen = 1, AllStreamsUnavailable = 2 };

  // Either limit advances the entire four-file capture set.
  constexpr uint32_t DIAGNOSTIC_SEGMENT_BYTES = 8UL * 1024UL * 1024UL;
  constexpr uint32_t MEASUREMENT_SEGMENT_BYTES = 256UL * 1024UL * 1024UL;

  struct StreamSnapshot {
    uint32_t rowsLost;
    uint32_t writeFailures;
    uint32_t openFailures;
    uint32_t maxWriteDurationUs;
    bool active;
  };

  struct Snapshot {
    StreamSnapshot canonicalSwing;
    StreamSnapshot canonicalPps;
    StreamSnapshot status;
    StreamSnapshot uno;
    uint32_t diagnosticsSuppressed;
    uint32_t diagnosticRotations;
    uint32_t measurementRotations;
    uint32_t cardFaults;
    SdFaultReason lastCardFault;
  };

  namespace detail {
    constexpr uint8_t DIAGNOSTIC_BURST_ROWS = 64;
    constexpr uint32_t DIAGNOSTIC_REFILL_MS = 1000UL;

    struct DiagnosticBudget {
      uint32_t lastRefillMs;
      uint8_t tokens;

      void reset(uint32_t nowMs) {
        lastRefillMs = nowMs;
        tokens = DIAGNOSTIC_BURST_ROWS;
      }

      bool allow(uint32_t nowMs) {
        const uint32_t elapsed = nowMs - lastRefillMs;
        const uint32_t refill = elapsed / DIAGNOSTIC_REFILL_MS;
        if (refill > 0) {
          const uint32_t replenished = static_cast<uint32_t>(tokens) + refill;
          tokens = static_cast<uint8_t>(replenished > DIAGNOSTIC_BURST_ROWS
                                          ? DIAGNOSTIC_BURST_ROWS
                                          : replenished);
          lastRefillMs += refill * DIAGNOSTIC_REFILL_MS;
        }
        if (tokens == 0) return false;
        --tokens;
        return true;
      }
    };

    constexpr bool recordFits(uint32_t used, size_t recordBytes, uint32_t limit) {
      return recordBytes <= limit && used <= limit - static_cast<uint32_t>(recordBytes);
    }


  }

  void begin();
  void service();

  bool startLogging(const char* fname, bool append);
  bool startLogging(LogMode mode, bool forceNewFile = false);
  bool restartLogging(bool forceNewFile);
  bool onMetadataReady();
  void stopLogging();

  void setFilename(const char* fname);
  void setAppendMode(bool append);
  void setStartupPolicy(LogStartupPolicy policy);
  void setLogMode(LogMode mode);

  const char* getFilename();           // configured base filename (continuous)
  const char* getActiveFilename();     // currently open file path
  const char* getActiveCanonicalSwingFilename();
  const char* getActiveCanonicalPpsFilename();
  bool getAppendMode();
  LogStartupPolicy getStartupPolicy();
  LogMode getLogMode();

  bool isLogging();
  bool ready();
  SdHealth sdHealth();
  const Snapshot& snapshot();

  bool hasTimeSync();
  time_t currentEpoch();
  unsigned long secondsSinceLastSync();

  bool isValidFilename(const char* fn);

  bool logCanonicalSwingSerialized(const char* line, size_t len);
  bool logCanonicalPpsSerialized(const char* line, size_t len);
  void logStatusLine(const char* rawLine);
  void logUnoLine(const char* category, const char* key, const char* value);
  void logUnoEvent(const char* category, const char* message);
  void logUnoValue(const char* category, const char* key, long value);
  void flushAuxFiles();
}
