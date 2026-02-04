#pragma once

#include <stdint.h>

#if PROTECT_SHARED_READS
#include <util/atomic.h>
#endif

inline uint32_t atomicRead32(volatile uint32_t &v) {
#if PROTECT_SHARED_READS
  uint32_t tmp;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { tmp = v; }
  return tmp;
#else
  return v;
#endif
}
