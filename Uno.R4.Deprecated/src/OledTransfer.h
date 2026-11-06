#pragma once
#include <stdint.h>
#include <stddef.h>

// One checked transaction per foreground service, with no second framebuffer.
class OledTransfer {
 public:
  static constexpr size_t FRAME_BYTES = 1024;
  static constexpr size_t PAGE_BYTES = 128;
  static constexpr size_t CHUNK_BYTES = 31; // + control byte fits a 32-byte Wire buffer.
  struct Metrics {
    uint32_t attempts = 0, completed = 0, failed = 0, deferred = 0;
    uint32_t lastOkMs = 0, maxFrameMs = 0;
    uint32_t unchanged = 0, pagesSent = 0;
    uint8_t lastError = 0;
  } metrics;
  bool active() const { return active_; }
  void invalidate() { valid_ = 0; }
  void begin(const uint8_t* buffer, uint32_t now) {
    if (active_) return;
    remaining_ = 0;
    for (uint8_t page = 0; page < 8; ++page) {
      if (!(valid_ & (1u << page)) || hashes_[page] != hash(buffer + page * PAGE_BYTES))
        remaining_ |= 1u << page;
    }
    if (!remaining_) { ++metrics.unchanged; return; }
    selectPage();
    active_ = true; command_ = true; offset_ = 0; startedMs_ = now;
    ++metrics.attempts;
  }
  template<class Clock, class Writer>
  void step(const uint8_t* buffer, Clock clock, Writer write) {
    if (!active_) return;
    const uint8_t address[] = {0x21, 0, 127, 0x22, page_, page_};
    const size_t count = PAGE_BYTES - offset_ < CHUNK_BYTES
        ? PAGE_BYTES - offset_ : CHUNK_BYTES;
    const uint8_t error = command_ ? write(0x00, address, sizeof(address))
                                  : write(0x40, buffer + page_ * PAGE_BYTES + offset_, count);
    if (error) {
      valid_ &= ~(1u << page_); // A partial page must be resent, even if content reverts.
      metrics.lastError = error; ++metrics.failed; active_ = false; return;
    }
    if (command_) { command_ = false; return; }
    offset_ += count;
    if (offset_ == PAGE_BYTES) {
      hashes_[page_] = hash(buffer + page_ * PAGE_BYTES);
      valid_ |= 1u << page_;
      remaining_ &= ~(1u << page_);
      ++metrics.pagesSent;
      if (remaining_) { selectPage(); return; }
      const uint32_t now = clock();
      active_ = false; ++metrics.completed; metrics.lastOkMs = now;
      const uint32_t duration = now - startedMs_;
      if (duration > metrics.maxFrameMs) metrics.maxFrameMs = duration;
    }
  }
 private:
  static uint32_t hash(const uint8_t* page) {
    // CRC32 page fingerprints use 32 bytes instead of another 1024-byte image.
    uint32_t crc = 0xffffffffu;
    for (size_t i = 0; i < PAGE_BYTES; ++i) {
      crc ^= page[i];
      for (uint8_t bit = 0; bit < 8; ++bit)
        crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
  }
  void selectPage() {
    page_ = 0;
    while (!(remaining_ & (1u << page_))) ++page_;
    offset_ = 0; command_ = true;
  }
  uint32_t hashes_[8] = {};
  uint32_t startedMs_ = 0;
  size_t offset_ = 0;
  uint8_t page_ = 0, valid_ = 0, remaining_ = 0;
  bool active_ = false, command_ = false;
};
