#pragma once

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <time.h>

#define F(value) value
#define FILE_READ 0
#define FILE_WRITE 1
#define INPUT_PULLUP 2
#define LOW 0
#define HIGH 1
#define WL_CONNECTED 3

#define SD_CS_PIN 10
#define LOG_FILENAME "pendulum.csv"
constexpr size_t LOG_FILENAME_LEN = 20;
constexpr uint8_t LOG_STARTUP_POLICY_DEFAULT = 1;
constexpr int8_t SD_CARD_DETECT_PIN = -1;
constexpr bool SD_CARD_DETECT_INSERTED_LOW = true;
constexpr unsigned long SD_HEALTH_PROBE_MS = 1500;
constexpr unsigned long SD_REMOUNT_RETRY_MS = 3000;
constexpr uint16_t FLUSH_EVERY_N = 32;
constexpr unsigned long FLUSH_EVERY_MS = 5000;
constexpr size_t CANONICAL_SCHEMA_PAYLOAD_MAX_LEN = 128;
static const char CANONICAL_SWING_SCHEMA[] = "seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing";
static const char CANONICAL_PPS_SCHEMA[] = "seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps";

namespace HostSd {
  struct Entry {
    uint32_t size = 0;
    std::string bytes;
  };

  extern std::map<std::string, Entry> files;
  extern std::set<std::string> openFailures;
  extern std::map<std::string, size_t> nextWriteLimit;
  extern std::map<std::string, uint32_t> openAttempts;
  extern bool mounted;
  extern bool beginSucceeds;
  extern uint32_t nowMs;
  extern uint32_t nowUs;

  inline void reset() {
    files.clear();
    openFailures.clear();
    nextWriteLimit.clear();
    openAttempts.clear();
    mounted = false;
    beginSucceeds = true;
    nowMs = 0;
    nowUs = 0;
  }

  inline void advance(uint32_t ms) {
    nowMs += ms;
    nowUs += ms * 1000U;
  }
}

inline unsigned long millis() { return HostSd::nowMs; }
inline unsigned long micros() { return ++HostSd::nowUs; }
inline void pinMode(int, int) {}
inline int digitalRead(int) { return HIGH; }

class File {
 public:
  File() : entry_(nullptr), position_(0), open_(false) {}
  File(HostSd::Entry* entry, const std::string& name) : entry_(entry), name_(name), position_(0), open_(true) {}

  size_t write(const uint8_t* data, size_t len) {
    if (!open_ || !entry_ || !HostSd::mounted) return 0;
    size_t accepted = len;
    std::map<std::string, size_t>::iterator limit = HostSd::nextWriteLimit.find(name_);
    if (limit != HostSd::nextWriteLimit.end()) {
      accepted = std::min(accepted, limit->second);
      HostSd::nextWriteLimit.erase(limit);
    }
    entry_->size += static_cast<uint32_t>(accepted);
    if (entry_->bytes.size() < 4096) {
      const size_t keep = std::min(accepted, static_cast<size_t>(4096 - entry_->bytes.size()));
      entry_->bytes.append(reinterpret_cast<const char*>(data), keep);
    }
    return accepted;
  }

  size_t write(uint8_t value) { return write(&value, 1); }
  uint32_t size() const { return entry_ ? entry_->size : 0; }
  bool seek(uint32_t position) {
    if (!open_ || !entry_ || position > entry_->size) return false;
    position_ = position;
    return true;
  }
  int read() {
    if (!open_ || !entry_ || position_ >= entry_->size) return -1;
    if (position_ < entry_->bytes.size()) return static_cast<unsigned char>(entry_->bytes[position_++]);
    ++position_;
    return '\n';
  }
  void flush() {}
  void close() { open_ = false; }
  operator bool() { return open_ && entry_ != nullptr; }

 private:
  HostSd::Entry* entry_;
  std::string name_;
  uint32_t position_;
  bool open_;
};

class FakeSDClass {
 public:
  bool begin(uint8_t = SD_CS_PIN) {
    HostSd::mounted = HostSd::beginSucceeds;
    return HostSd::mounted;
  }
  void end() { HostSd::mounted = false; }
  File open(const char* filename, uint8_t mode = FILE_READ) {
    const std::string name = filename ? filename : "";
    HostSd::openAttempts[name]++;
    if (!HostSd::mounted || HostSd::openFailures.count(name)) return File();
    if (name == "/") return File(&root_, name);
    std::map<std::string, HostSd::Entry>::iterator found = HostSd::files.find(name);
    if (found == HostSd::files.end()) {
      if (mode != FILE_WRITE) return File();
      found = HostSd::files.insert(std::make_pair(name, HostSd::Entry())).first;
    }
    return File(&found->second, name);
  }
  bool exists(const char* filename) const {
    return HostSd::mounted && HostSd::files.count(filename ? filename : "") != 0;
  }
  bool remove(const char* filename) {
    if (!HostSd::mounted) return false;
    HostSd::files.erase(filename ? filename : "");
    return true;
  }
 private:
  HostSd::Entry root_;
};

extern FakeSDClass SD;

namespace Display {
  inline void scrollLog(const char*) {}
}

namespace DiagLog {
  enum class Severity : uint8_t { Info = 0 };
  inline void emit(Severity, const char*) {}
}

namespace NanoComm {
  extern bool metadata;
  extern bool pendingWork;
  inline bool metadataReady() { return metadata; }
  inline bool hasPendingIngestWork() { return pendingWork; }
}

class FakeWiFi {
 public:
  int status() const { return 0; }
  time_t getTime() const { return 0; }
};
extern FakeWiFi WiFi;

inline void copyFlashToRam(char* out, const char* source, size_t len) {
  if (!out || len == 0) return;
  std::strncpy(out, source ? source : "", len - 1);
  out[len - 1] = '\0';
}
