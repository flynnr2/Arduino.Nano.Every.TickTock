#include "Config.h"

#include <util/atomic.h>

#include "PendulumCapture.h"
#include "PendulumProtocol.h"
#include "PpsAdjust.h"
#include "SwingAssembler.h"

namespace {

static inline uint32_t elapsed32(uint32_t now, uint32_t then) {
  return (uint32_t)(now - then);
}

constexpr uint8_t SWING_RING_SIZE = RING_SIZE_SWING_ROWS;
static_assert((SWING_RING_SIZE & (SWING_RING_SIZE - 1U)) == 0U, "SWING_RING_SIZE must be power-of-two for mask arithmetic");

FullSwing swing_buf[SWING_RING_SIZE];
volatile uint8_t swing_head = 0;
volatile uint8_t swing_tail = 0;

// SRAM guardrails: rows are produced at pendulum cadence (much slower than edge ISR
// cadence), so this queue should stay compact on ATmega4809.
static_assert(sizeof(FullSwing) <= 160U, "FullSwing grew unexpectedly; revisit SRAM budget");
static_assert(sizeof(swing_buf) <= 2048U, "Swing row ring exceeds SRAM budget");

static inline uint8_t swing_mask(uint8_t v) { return v & (SWING_RING_SIZE - 1); }

static inline void swing_push(const FullSwing &s) {
  uint8_t n = swing_mask(swing_head + 1);
  if (n != swing_tail) {
    swing_buf[swing_head] = s;
    swing_head = n;
  } else {
    captureRecordDroppedEvent();
  }
}

static inline uint64_t pack_pps_tag(const PpsTaggedStamp& stamp) {
  return ppsTagPack(stamp.pps_seq, stamp.ticks_into_sec);
}

static inline uint64_t make_pps_tag_or_invalid(uint32_t edge_tick32) {
  PpsTaggedStamp stamp{};
  if (!ppsAdjustTagTick(edge_tick32, &stamp)) {
    return ppsTagInvalid();
  }
  return pack_pps_tag(stamp);
}

static void adjust_interval_or_fallback(uint32_t start_tick32,
                                        uint32_t end_tick32,
                                        uint32_t raw_ticks,
                                        uint8_t crossed_bit,
                                        uint32_t* adj_out,
                                        uint8_t* diag_bits,
                                        uint8_t* component_diag_out) {
  if (component_diag_out) *component_diag_out = 0U;
  PpsTaggedStamp start{};
  PpsTaggedStamp end{};
  if (!ppsAdjustTagTick(start_tick32, &start) || !ppsAdjustTagTick(end_tick32, &end)) {
    *adj_out = raw_ticks;
    const uint8_t component_diag = static_cast<uint8_t>(ADJ_DIAG_MISSING_SCALE | ADJ_DIAG_DEGRADED_FALLBACK);
    *diag_bits |= component_diag;
    if (component_diag_out) *component_diag_out = component_diag;
    return;
  }

  uint32_t adjusted = raw_ticks;
  uint8_t component_diag = 0U;
  if (!ppsAdjustIntervalToNominal16Mhz(start, end, raw_ticks, &adjusted, &component_diag, crossed_bit)) {
    *adj_out = raw_ticks;
    *diag_bits |= component_diag;
    if (component_diag_out) *component_diag_out = component_diag;
    return;
  }
  *adj_out = adjusted;
  *diag_bits |= component_diag;
  if (component_diag_out) *component_diag_out = component_diag;
}

static uint8_t component_index_for_crossed_bit(uint8_t crossed_bit) {
  switch (crossed_bit) {
    case ADJ_DIAG_TICK_CROSSED: return ADJ_COMPONENT_TICK;
    case ADJ_DIAG_TICK_BLOCK_CROSSED: return ADJ_COMPONENT_TICK_BLOCK;
    case ADJ_DIAG_TOCK_CROSSED: return ADJ_COMPONENT_TOCK;
    case ADJ_DIAG_TOCK_BLOCK_CROSSED: return ADJ_COMPONENT_TOCK_BLOCK;
    default: return ADJ_COMPONENT_TICK;
  }
}

static void stamp_component_degradation_diag(uint8_t crossed_bit,
                                             uint8_t diag_bits,
                                             uint16_t* adj_comp_diag) {
  if (!adj_comp_diag) return;
  const uint8_t component_index = component_index_for_crossed_bit(crossed_bit);
  if (diag_bits & ADJ_DIAG_MISSING_SCALE) {
    *adj_comp_diag |= adjComponentDiagBit(component_index, 0U);
  }
  if (diag_bits & ADJ_DIAG_DEGRADED_FALLBACK) {
    *adj_comp_diag |= adjComponentDiagBit(component_index, 1U);
  }
  if (diag_bits & ADJ_DIAG_MULTI_BOUNDARY) {
    *adj_comp_diag |= adjComponentDiagBit(component_index, 2U);
  }
}

static inline void stamp_interval_provenance(uint32_t start_tick32,
                                             uint32_t end_tick32,
                                             uint64_t* start_tag_out,
                                             uint64_t* end_tag_out) {
  if (start_tag_out) *start_tag_out = make_pps_tag_or_invalid(start_tick32);
  if (end_tag_out) *end_tag_out = make_pps_tag_or_invalid(end_tick32);
}

static void adjust_composite_interval_or_fallback(uint32_t start_tick32,
                                                  uint32_t end_tick32,
                                                  uint32_t raw_ticks,
                                                  uint8_t crossed_bit,
                                                  uint32_t* adj_out,
                                                  uint8_t* diag_bits) {
  uint8_t component_diag = 0U;
  adjust_interval_or_fallback(start_tick32,
                              end_tick32,
                              raw_ticks,
                              crossed_bit,
                              adj_out,
                              &component_diag,
                              nullptr);
  if (component_diag & crossed_bit) {
    *diag_bits |= DIRECT_ADJ_DIAG_CROSSED;
  }
  if (component_diag & ADJ_DIAG_MISSING_SCALE) {
    *diag_bits |= DIRECT_ADJ_DIAG_MISSING_SCALE;
  }
  if (component_diag & ADJ_DIAG_DEGRADED_FALLBACK) {
    *diag_bits |= DIRECT_ADJ_DIAG_DEGRADED_FALLBACK;
  }
  if (component_diag & ADJ_DIAG_MULTI_BOUNDARY) {
    *diag_bits |= DIRECT_ADJ_DIAG_MULTI_BOUNDARY;
  }
}

} // namespace

bool swingAssemblerAvailable() {
  return swing_tail != swing_head;
}

FullSwing swingAssemblerPop() {
  FullSwing s;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    s = swing_buf[swing_tail];
    swing_tail = swing_mask(swing_tail + 1);
  }
  return s;
}

void swingAssemblerProcessEdges() {
  static uint8_t swing_state = 0;
  static uint32_t last_ts = 0;
  static uint32_t tick_half_start_ts = 0;
  static uint32_t tock_half_start_ts = 0;
  static FullSwing curr;

  while (captureEdgeAvailable()) {
    EdgeEvent e = capturePopEdge();
    uint8_t component_diag = 0U;

    switch (swing_state) {
      case 0:
        if (e.type == 0) {
          last_ts = e.ticks;
          tick_half_start_ts = e.ticks;
          curr.adj_diag = 0U;
          curr.adj_comp_diag = 0U;
          curr.tick_total_adj_diag = 0U;
          curr.tock_total_adj_diag = 0U;
          curr.pps_seq_row = 0U;
          swing_state = 1;
        }
        break;
      case 1:
        if (e.type == 1) {
          curr.tick_block = elapsed32(e.ticks, last_ts);
          stamp_interval_provenance(last_ts, e.ticks, &curr.tick_block_start_tag, &curr.tick_block_end_tag);
          adjust_interval_or_fallback(last_ts,
                                      e.ticks,
                                      curr.tick_block,
                                      ADJ_DIAG_TICK_BLOCK_CROSSED,
                                      &curr.tick_block_adj,
                                      &curr.adj_diag,
                                      &component_diag);
          stamp_component_degradation_diag(ADJ_DIAG_TICK_BLOCK_CROSSED, component_diag, &curr.adj_comp_diag);
          last_ts = e.ticks;
          swing_state = 2;
        }
        break;
      case 2:
        if (e.type == 0) {
          curr.tick = elapsed32(e.ticks, last_ts);
          stamp_interval_provenance(last_ts, e.ticks, &curr.tick_start_tag, &curr.tick_end_tag);
          adjust_interval_or_fallback(last_ts,
                                      e.ticks,
                                      curr.tick,
                                      ADJ_DIAG_TICK_CROSSED,
                                      &curr.tick_adj,
                                      &curr.adj_diag,
                                      &component_diag);
          stamp_component_degradation_diag(ADJ_DIAG_TICK_CROSSED, component_diag, &curr.adj_comp_diag);
          // Component-level *_adj fields remain authoritative for sub-interval studies.
          // Direct composite *_total_adj_direct fields are authoritative for full
          // half-swing timing and should generally be preferred over sums of separately
          // adjusted pieces when building higher-level timing observables.
          const uint32_t tick_total_raw = elapsed32(e.ticks, tick_half_start_ts);
          stamp_interval_provenance(tick_half_start_ts,
                                    e.ticks,
                                    &curr.tick_total_start_tag,
                                    &curr.tick_total_end_tag);
          adjust_composite_interval_or_fallback(tick_half_start_ts,
                                                e.ticks,
                                                tick_total_raw,
                                                ADJ_DIAG_TICK_CROSSED,
                                                &curr.tick_total_adj_direct,
                                                &curr.tick_total_adj_diag);
          tock_half_start_ts = e.ticks;
          last_ts = e.ticks;
          swing_state = 3;
        }
        break;
      case 3:
        if (e.type == 1) {
          curr.tock_block = elapsed32(e.ticks, last_ts);
          stamp_interval_provenance(last_ts, e.ticks, &curr.tock_block_start_tag, &curr.tock_block_end_tag);
          adjust_interval_or_fallback(last_ts,
                                      e.ticks,
                                      curr.tock_block,
                                      ADJ_DIAG_TOCK_BLOCK_CROSSED,
                                      &curr.tock_block_adj,
                                      &curr.adj_diag,
                                      &component_diag);
          stamp_component_degradation_diag(ADJ_DIAG_TOCK_BLOCK_CROSSED, component_diag, &curr.adj_comp_diag);
          last_ts = e.ticks;
          swing_state = 4;
        }
        break;
      case 4:
        if (e.type == 0) {
          curr.tock = elapsed32(e.ticks, last_ts);
          stamp_interval_provenance(last_ts, e.ticks, &curr.tock_start_tag, &curr.tock_end_tag);
          adjust_interval_or_fallback(last_ts,
                                      e.ticks,
                                      curr.tock,
                                      ADJ_DIAG_TOCK_CROSSED,
                                      &curr.tock_adj,
                                      &curr.adj_diag,
                                      &component_diag);
          stamp_component_degradation_diag(ADJ_DIAG_TOCK_CROSSED, component_diag, &curr.adj_comp_diag);
          const uint32_t tock_total_raw = elapsed32(e.ticks, tock_half_start_ts);
          stamp_interval_provenance(tock_half_start_ts,
                                    e.ticks,
                                    &curr.tock_total_start_tag,
                                    &curr.tock_total_end_tag);
          adjust_composite_interval_or_fallback(tock_half_start_ts,
                                                e.ticks,
                                                tock_total_raw,
                                                ADJ_DIAG_TOCK_CROSSED,
                                                &curr.tock_total_adj_direct,
                                                &curr.tock_total_adj_diag);
          PpsTaggedStamp row_stamp{};
          curr.pps_seq_row = ppsAdjustTagTick(e.ticks, &row_stamp) ? row_stamp.pps_seq : ppsAdjustCurrentSeq();
          swing_push(curr);
          last_ts = e.ticks;
          tick_half_start_ts = e.ticks;
          curr.adj_diag = 0U;
          curr.adj_comp_diag = 0U;
          curr.tick_total_adj_diag = 0U;
          curr.tock_total_adj_diag = 0U;
          swing_state = 1;
        }
        break;
    }
  }
}
