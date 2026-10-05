#pragma once

#include <stddef.h>
#include <stdint.h>

#include "RecordSerializer.h"

namespace PublishMirrorQueue {

enum class RecordKind : uint8_t {
  CanonicalSwing = 0,
  CanonicalPps
};

struct Item {
  uint32_t seq;
  RecordKind kind;
  size_t len;
  char line[RecordSerializer::CANONICAL_SWING_RECORD_MAX_LEN];
};

struct Snapshot {
  bool enabled;
  uint8_t depth;
  uint8_t high_water;
  unsigned long enqueued;
  unsigned long dequeued;
  unsigned long drops;
  uint32_t last_seq;
};

void service();
bool enqueue(const char* line, size_t len, RecordKind kind, uint32_t seq);
bool dequeue(Item& out);
Snapshot snapshot();

} // namespace PublishMirrorQueue
