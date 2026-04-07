#include "PpsAdjust.h"

namespace {

struct PpsSpanEntry {
  uint32_t pps_seq;
  uint32_t start_tick32;
  uint32_t span_ticks;
  uint32_t applied_hz;
  uint64_t scale_q32;
  uint8_t valid;
};

constexpr uint8_t kPpsSpanRingSize = PPS_SCALE_RING_SIZE;
static_assert((kPpsSpanRingSize & (kPpsSpanRingSize - 1U)) == 0U, "PPS_SCALE_RING_SIZE must be power-of-two");
static_assert(kPpsSpanRingSize <= 32U, "PPS scale ring should remain compact on Nano Every");
static_assert(sizeof(PpsSpanEntry) <= 32U, "PpsSpanEntry grew unexpectedly; revisit SRAM budget");

PpsSpanEntry s_span_ring[kPpsSpanRingSize];
static_assert(sizeof(s_span_ring) <= 1024U, "PPS scale ring exceeds SRAM budget");
uint8_t s_span_head = 0;
uint8_t s_span_fill = 0;

bool s_primed = false;
uint32_t s_nominal_hz = (uint32_t)MAIN_CLOCK_HZ;
uint32_t s_current_seq = 0;
uint32_t s_current_start32 = 0;
uint64_t s_current_scale_q32 = 0;
uint32_t s_current_applied_hz = (uint32_t)MAIN_CLOCK_HZ;

static inline uint8_t span_mask(uint8_t v) {
  return (uint8_t)(v & (kPpsSpanRingSize - 1U));
}

static inline uint32_t elapsed32(uint32_t now, uint32_t then) {
  return (uint32_t)(now - then);
}

static uint64_t hz_to_scale_q32(uint32_t hz) {
  if (hz == 0U) hz = s_nominal_hz ? s_nominal_hz : 1U;
  const uint64_t nominal_hz = s_nominal_hz ? (uint64_t)s_nominal_hz : 1ULL;
  const uint64_t numerator = (nominal_hz << 32);
  return (numerator + ((uint64_t)hz / 2ULL)) / (uint64_t)hz;
}

static bool find_span_by_seq(uint32_t seq, PpsSpanEntry* out) {
  for (uint8_t i = 0; i < s_span_fill; ++i) {
    const uint8_t idx = span_mask((uint8_t)(s_span_head - 1U - i));
    const PpsSpanEntry& e = s_span_ring[idx];
    if (e.valid && e.pps_seq == seq) {
      if (out) *out = e;
      return true;
    }
  }
  return false;
}

static bool find_span_by_tick(uint32_t edge32, PpsTaggedStamp* out) {
  if (!s_primed) return false;

  for (uint8_t i = 0; i < s_span_fill; ++i) {
    const uint8_t idx = span_mask((uint8_t)(s_span_head - 1U - i));
    const PpsSpanEntry& e = s_span_ring[idx];
    if (!e.valid || e.span_ticks == 0U) continue;
    const uint32_t dt = elapsed32(edge32, e.start_tick32);
    if (dt < e.span_ticks) {
      out->pps_seq = e.pps_seq;
      out->ticks_into_sec = dt;
      return true;
    }
  }

  // Current in-progress PPS second (duration not finalized yet).
  const uint32_t current_dt = elapsed32(edge32, s_current_start32);
  const uint32_t max_current_window = (s_nominal_hz > 0U) ? (s_nominal_hz * 2UL) : (2UL * (uint32_t)MAIN_CLOCK_HZ);
  if (current_dt <= max_current_window) {
    out->pps_seq = s_current_seq;
    out->ticks_into_sec = current_dt;
    return true;
  }

  return false;
}

static bool find_scale_by_seq(uint32_t seq, uint64_t* scale_q32) {
  if (seq == s_current_seq && s_primed) {
    *scale_q32 = s_current_scale_q32;
    return true;
  }
  PpsSpanEntry e{};
  if (!find_span_by_seq(seq, &e)) return false;
  *scale_q32 = e.scale_q32;
  return true;
}

static bool span_ticks_for_seq(uint32_t seq, uint32_t* span_ticks) {
  PpsSpanEntry e{};
  if (!find_span_by_seq(seq, &e)) return false;
  *span_ticks = e.span_ticks;
  return true;
}

static bool applied_hz_for_seq(uint32_t seq, uint32_t* applied_hz) {
  if (seq == s_current_seq && s_primed) {
    if (!applied_hz) return false;
    *applied_hz = s_current_applied_hz;
    return true;
  }

  PpsSpanEntry e{};
  if (!find_span_by_seq(seq, &e)) return false;
  if (!applied_hz) return false;
  *applied_hz = e.applied_hz;
  return true;
}

}  // namespace

void ppsAdjustReset(uint32_t nominal_hz) {
  s_nominal_hz = nominal_hz ? nominal_hz : (uint32_t)MAIN_CLOCK_HZ;
  s_span_head = 0;
  s_span_fill = 0;
  s_primed = false;
  s_current_seq = 0;
  s_current_start32 = 0;
  s_current_scale_q32 = hz_to_scale_q32(s_nominal_hz);
  s_current_applied_hz = s_nominal_hz;
  for (uint8_t i = 0; i < kPpsSpanRingSize; ++i) {
    s_span_ring[i].valid = 0;
  }
}

void ppsAdjustOnPpsPrimed(uint32_t first_edge32, uint32_t active_hz) {
  s_primed = true;
  s_current_seq = 0;
  s_current_start32 = first_edge32;
  s_current_applied_hz = active_hz ? active_hz : s_nominal_hz;
  s_current_scale_q32 = hz_to_scale_q32(s_current_applied_hz);
}

void ppsAdjustOnPpsFinalized(uint32_t prev_edge32,
                             uint32_t curr_edge32,
                             uint32_t applied_hz_for_completed_second,
                             uint32_t next_active_hz) {
  if (!s_primed) {
    ppsAdjustOnPpsPrimed(prev_edge32, applied_hz_for_completed_second);
  }

  PpsSpanEntry& slot = s_span_ring[s_span_head];
  slot.pps_seq = s_current_seq;
  slot.start_tick32 = prev_edge32;
  slot.span_ticks = elapsed32(curr_edge32, prev_edge32);
  slot.applied_hz = applied_hz_for_completed_second ? applied_hz_for_completed_second : s_nominal_hz;
  slot.scale_q32 = hz_to_scale_q32(applied_hz_for_completed_second);
  slot.valid = 1U;
  s_span_head = span_mask((uint8_t)(s_span_head + 1U));
  if (s_span_fill < kPpsSpanRingSize) s_span_fill++;

  s_current_seq++;
  s_current_start32 = curr_edge32;
  s_current_applied_hz = next_active_hz ? next_active_hz : s_nominal_hz;
  s_current_scale_q32 = hz_to_scale_q32(s_current_applied_hz);
}

bool ppsAdjustTagTick(uint32_t edge32, PpsTaggedStamp* out) {
  if (!out) return false;
  return find_span_by_tick(edge32, out);
}

bool ppsAdjustLookupSeq(uint32_t seq, uint32_t* span_ticks, uint32_t* applied_hz) {
  bool any = false;
  if (span_ticks) {
    if (span_ticks_for_seq(seq, span_ticks)) {
      any = true;
    } else {
      return false;
    }
  }
  if (applied_hz) {
    uint32_t hz = 0U;
    if (!applied_hz_for_seq(seq, &hz)) return false;
    *applied_hz = hz;
    any = true;
  }
  return any;
}

bool ppsAdjustIntervalToNominalTicks(const PpsTaggedStamp& start,
                                     const PpsTaggedStamp& end,
                                     uint32_t raw_ticks,
                                     uint32_t* adjusted_ticks,
                                     uint8_t* diag_bits,
                                     uint8_t crossed_bit) {
  if (!adjusted_ticks || !diag_bits) return false;

  uint8_t diag = 0U;
  uint64_t adj_total = 0ULL;
  uint32_t remaining = raw_ticks;
  const uint32_t seq_delta = end.pps_seq - start.pps_seq;

  if (seq_delta > 0U) diag |= crossed_bit;
  if (seq_delta > 1U) diag |= ADJ_DIAG_MULTI_BOUNDARY;

  for (uint32_t i = 0; i <= seq_delta; ++i) {
    const uint32_t seq = start.pps_seq + i;
    uint32_t seg_ticks = 0U;

    if (seq_delta == 0U) {
      seg_ticks = raw_ticks;
    } else if (i == 0U) {
      uint32_t span = 0U;
      if (!span_ticks_for_seq(seq, &span) || start.ticks_into_sec > span) {
        diag |= (ADJ_DIAG_MISSING_SCALE | ADJ_DIAG_DEGRADED_FALLBACK);
        *diag_bits |= diag;
        *adjusted_ticks = raw_ticks;
        return false;
      }
      seg_ticks = span - start.ticks_into_sec;
    } else if (i == seq_delta) {
      seg_ticks = end.ticks_into_sec;
    } else {
      uint32_t span = 0U;
      if (!span_ticks_for_seq(seq, &span)) {
        diag |= (ADJ_DIAG_MISSING_SCALE | ADJ_DIAG_DEGRADED_FALLBACK);
        *diag_bits |= diag;
        *adjusted_ticks = raw_ticks;
        return false;
      }
      seg_ticks = span;
    }

    if (seg_ticks > remaining) seg_ticks = remaining;
    remaining -= seg_ticks;

    uint64_t scale_q32 = 0ULL;
    if (!find_scale_by_seq(seq, &scale_q32)) {
      diag |= (ADJ_DIAG_MISSING_SCALE | ADJ_DIAG_DEGRADED_FALLBACK);
      *diag_bits |= diag;
      *adjusted_ticks = raw_ticks;
      return false;
    }

    const uint64_t prod = (uint64_t)seg_ticks * scale_q32;
    adj_total += (prod + 0x80000000ULL) >> 32;
  }

  if (remaining != 0U) {
    diag |= (ADJ_DIAG_MISSING_SCALE | ADJ_DIAG_DEGRADED_FALLBACK);
    *diag_bits |= diag;
    *adjusted_ticks = raw_ticks;
    return false;
  }

  if (adj_total > 0xFFFFFFFFULL) adj_total = 0xFFFFFFFFULL;
  *adjusted_ticks = (uint32_t)adj_total;
  *diag_bits |= diag;
  return true;
}

bool ppsAdjustIntervalToNominal16Mhz(const PpsTaggedStamp& start,
                                     const PpsTaggedStamp& end,
                                     uint32_t raw_ticks,
                                     uint32_t* adjusted_ticks,
                                     uint8_t* diag_bits,
                                     uint8_t crossed_bit) {
  return ppsAdjustIntervalToNominalTicks(start, end, raw_ticks, adjusted_ticks, diag_bits, crossed_bit);
}

uint32_t ppsAdjustCurrentSeq() {
  return s_current_seq;
}
