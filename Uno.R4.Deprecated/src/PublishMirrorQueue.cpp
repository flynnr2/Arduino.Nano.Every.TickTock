#include "PublishMirrorQueue.h"

#include <string.h>

#include "Config.h"

namespace PublishMirrorQueue {

#if ENABLE_PUBLISH_MIRROR_QUEUE

namespace {

constexpr uint8_t QUEUE_CAPACITY = 8;

static Item queueBuf[QUEUE_CAPACITY];
static uint8_t head = 0;
static uint8_t tail = 0;
static uint8_t depth = 0;
static uint8_t highWater = 0;
static unsigned long enqueuedCount = 0;
static unsigned long dequeuedCount = 0;
static unsigned long dropCount = 0;
static uint32_t lastSeq = 0;

static void noteHighWater() {
  if (depth > highWater) {
    highWater = depth;
  }
}

} // namespace

#endif // ENABLE_PUBLISH_MIRROR_QUEUE

void service() {
  // Skeleton only for now. Future publish transport should dequeue at a bounded,
  // low-priority rate and must not block this foreground loop.
}

bool enqueue(const char* line, size_t len, RecordKind kind, uint32_t seq) {
#if ENABLE_PUBLISH_MIRROR_QUEUE
  if (!line || len == 0 || len > sizeof(Item::line)) {
    return false;
  }
  if (depth >= QUEUE_CAPACITY) {
    dropCount++;
    return false;
  }

  Item& slot = queueBuf[tail];
  slot.seq = seq;
  slot.kind = kind;
  slot.len = len;
  memcpy(slot.line, line, len);

  tail = (uint8_t)((tail + 1) % QUEUE_CAPACITY);
  depth++;
  noteHighWater();
  enqueuedCount++;
  lastSeq = seq;
  return true;
#else
  (void)line;
  (void)len;
  (void)kind;
  (void)seq;
  return false;
#endif
}

bool dequeue(Item& out) {
#if ENABLE_PUBLISH_MIRROR_QUEUE
  if (depth == 0) {
    return false;
  }
  out = queueBuf[head];
  head = (uint8_t)((head + 1) % QUEUE_CAPACITY);
  depth--;
  dequeuedCount++;
  return true;
#else
  (void)out;
  return false;
#endif
}

Snapshot snapshot() {
#if ENABLE_PUBLISH_MIRROR_QUEUE
  Snapshot s = {};
  s.enabled = ENABLE_PUBLISH_MIRROR_QUEUE;
  s.depth = depth;
  s.high_water = highWater;
  s.enqueued = enqueuedCount;
  s.dequeued = dequeuedCount;
  s.drops = dropCount;
  s.last_seq = lastSeq;
  return s;
#else
  Snapshot s = {};
  s.enabled = false;
  return s;
#endif
}

} // namespace PublishMirrorQueue
