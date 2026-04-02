# Pendulum Data Record Guide

This guide explains the pendulum sample record emitted by the Nano Every firmware, what each field means, and how to interpret the data during analysis.

Authoritative schema source in firmware:
- `Nano.Every/src/PendulumProtocol.h` (`TAG_*`, `SAMPLE_SCHEMA`, `PendulumSample`)

---

## 1) Record families on the serial stream

A run emits three line families that should be consumed together:

- `CFG,...` — session metadata (`nominal_hz`, `sample_tag`, `sample_schema`, `adj_semantics_version`)
- `HDR_PART,...` — segmented ordered declarations of sample columns
- `SMP,...` — one pendulum sample row per completed full swing, matching the latest complete `HDR_PART` sequence

**Important:** parsers should trust the latest complete `HDR_PART` sequence for column order and not hard-code positions.

---

## 2) Current sample schema

For the current reduced firmware, the sample header declaration is:

```text
HDR_PART,1,4,tick,tick_start_tag,tick_end_tag,tick_block,tick_block_start_tag,tick_block_end_tag,tock,tock_start_tag,tock_end_tag,tock_block,tock_block_start_tag,tock_block_end_tag
HDR_PART,2,4,tick_adj,tick_block_adj,tock_adj,tock_block_adj
HDR_PART,3,4,tick_total_adj_direct,tick_total_adj_diag,tick_total_start_tag,tick_total_end_tag,tock_total_adj_direct,tock_total_adj_diag,tock_total_start_tag,tock_total_end_tag
HDR_PART,4,4,f_inst_hz,f_hat_hz,gps_status,holdover_age_ms,r_ppm,j_ticks,dropped,adj_diag,adj_comp_diag,pps_seq_row
```

All duration/count fields are integer tick counts.

---

## 3) Field-by-field meaning and analysis use

### Core swing timing (raw)

- `tick` — unblocked half-swing duration (one direction), raw TCB0 ticks
- `tock` — opposite-direction unblocked half-swing duration, raw TCB0 ticks
- `tick_block` — beam-block duration during `tick`, raw TCB0 ticks
- `tock_block` — beam-block duration during `tock`, raw TCB0 ticks

Use these when you want **capture-source truth** and are explicitly modeling local oscillator behavior.

### Core swing timing (PPS-adjusted)

- `tick_adj`
- `tock_adj`
- `tick_block_adj`
- `tock_block_adj`
- `tick_total_adj_direct`
- `tock_total_adj_direct`

These are PPS-aware corrected intervals in nominal-clock-equivalent ticks (nominally 16 MHz on default Nano Every builds).

Interpretation split:

- `tick_adj` / `tick_block_adj` / `tock_adj` / `tock_block_adj` stay authoritative for component/sub-interval studies.
- `tick_total_adj_direct` / `tock_total_adj_direct` are the authoritative full half-swing adjusted intervals and should generally be preferred for period-level observables over summing separately adjusted component pieces.

`adj_semantics_version` (from `CFG` and `STS schema/cfg` metadata) declares this authority split and the matching diagnostic interpretation rules. Treat it as a wire-visible compatibility key for adjusted-field meaning.


### Per-interval PPS provenance tags (compact offline replay)

Each interval now has `*_start_tag` / `*_end_tag` fields (for `tick_block`, `tick`, `tock_block`, `tock`, and direct totals).

Tag encoding (unsigned 64-bit integer):

- `tag = (pps_seq << 25) | ticks_into_sec`
- `pps_seq = tag >> 25`
- `ticks_into_sec = tag & ((1 << 25) - 1)`
- `tag = 18446744073709551615` (`2^64 - 1`) means tag unavailable (`ppsAdjustTagTick()` failed).

Reconstruction semantics (exact):

For any interval `I` with raw length `raw_I` and tags `I_start_tag` / `I_end_tag`:

1. Decode start/end `(seq_s, off_s)` and `(seq_e, off_e)`.
2. PPS segment count is `seq_e - seq_s + 1` (modulo 32-bit sequence arithmetic).
3. Segment decomposition in raw ticks:
   - If `seq_s == seq_e`: one segment of `raw_I`.
   - Else:
     - first segment = `span(seq_s) - off_s`
     - middle segment(s) = full `span(seq)` for `seq_s < seq < seq_e`
     - final segment = `off_e`
4. Adjusted replay uses the per-segment scale for each `seq` and rounds each segment independently before summing, matching firmware behavior.

This is the same segmentation logic used by `adjust_interval_or_fallback()` / `adjust_composite_interval_or_fallback()`, but now directly reconstructable offline from the emitted row.

Boundary-crossing examples:

- Example A (single PPS crossing):
  - `tick_start_tag` decodes to `(1000, 15,900,000)`
  - `tick_end_tag` decodes to `(1001, 110,000)`
  - Segments: `span(1000)-15,900,000` and `110,000`.

- Example B (multi-boundary crossing):
  - `tock_total_start_tag` -> `(2000, 15,950,000)`
  - `tock_total_end_tag` -> `(2002, 50,000)`
  - Segments: tail of 2000, full 2001, head of 2002.
  - This corresponds to multi-boundary diagnostics (`ADJ_DIAG_MULTI_BOUNDARY` / `DIRECT_ADJ_DIAG_MULTI_BOUNDARY`).

### Frequency and state context

- `f_inst_hz` — latest accepted PPS interval estimate (ticks/sec)
- `f_hat_hz` — row-context disciplined frequency estimate (ticks/sec)
- `gps_status` — PPS state code:
  - `0` = `NO_PPS`
  - `1` = `ACQUIRING`
  - `2` = `LOCKED`
  - `3` = `HOLDOVER`
- `holdover_age_ms` — elapsed holdover age in ms

Use these fields as quality/context indicators, not as replacements for interval columns.

### Quality / diagnostics

- `r_ppm` — fast/slow discipliner disagreement metric (ppm)
- `j_ticks` — robust jitter metric (MAD residual ticks)
- `dropped` — cumulative dropped capture events from ring overflow
- `adj_diag` — compact row-level bitmask about component-adjustment quality:
  - bit0: `tick` crossed a PPS boundary
  - bit1: `tick_block` crossed a PPS boundary
  - bit2: `tock` crossed a PPS boundary
  - bit3: `tock_block` crossed a PPS boundary
  - bit4: missing PPS scale
  - bit5: degraded fallback used
  - bit6: crossed more than one PPS boundary
- `adj_comp_diag` — packed per-component degradation flags (unsigned 16-bit):
  - slot order: `tick`, `tick_block`, `tock`, `tock_block`
  - each slot uses 3 bits:
    - bit0 in slot: missing PPS scale
    - bit1 in slot: degraded fallback used
    - bit2 in slot: crossed more than one PPS boundary
  - decode:
    - `slot_mask = (adj_comp_diag >> (3 * slot_index)) & 0x7`
    - slot indices: `0=tick`, `1=tick_block`, `2=tock`, `3=tock_block`
- `tick_total_adj_diag` / `tock_total_adj_diag` — direct-composite diagnostic bitmasks for `*_total_adj_direct`:
  - bit0: crossed a PPS boundary
  - bit1: missing PPS scale
  - bit2: degraded fallback used
  - bit3: crossed more than one PPS boundary

Diagnostic authority split:
- `adj_diag` applies only to component `*_adj` fields.
- `adj_comp_diag` refines only component degradation (`missing/degraded/multi`) and removes ambiguity about which component triggered row-level bits 4/5/6 in `adj_diag`.
- `*_total_adj_diag` applies only to direct-total `*_total_adj_direct` fields.
- Consumers should interpret both families according to `adj_semantics_version`.

### Provenance

- `pps_seq_row` — PPS sequence id associated with final interval closure for the row.

---

## 4) Recommended analysis workflow

1. Parse and retain `CFG` + complete `HDR_PART` sequence before processing `SMP`.
2. Use `nominal_hz` from `CFG` for converting ticks to seconds:
   - `seconds = ticks / nominal_hz`
3. Use `tick_total_adj_direct` and `tock_total_adj_direct` as primary half-swing intervals.
4. Build full-period observables from adjacent half-swings:
   - `period_ticks_adj = tick_total_adj_direct + tock_total_adj_direct`
5. Gate/annotate data quality using:
   - `gps_status` (prefer `LOCKED` for best stability)
   - `dropped` (detect acquisition stress/overflow windows)
   - `adj_diag` bits 4/5/6 (row-level presence of component degradation)
   - `adj_comp_diag` slots to identify exactly which component(s) were degraded
   - `tick_total_adj_diag` / `tock_total_adj_diag` bits 1/2/3 (direct-composite degradation)
   - `r_ppm` and `j_ticks` (monitor discipliner noise/outlier windows)
6. For beam/amplitude-proxy studies, use component fields (`tick_adj`, `tick_block_adj`, `tock_adj`, `tock_block_adj`) and compare odd/even asymmetry over windows.

---

## 5) Interpretation rules (short version)

- Prefer `*_total_adj_direct` for full half-swing and period metrics; use component `*_adj` fields when you specifically need sub-interval analysis.
- Always read and persist `adj_semantics_version`; bump-sensitive analysis logic must key off that value, not only field names.
- Treat `f_hat_hz` as row context/diagnostics, not as a direct replacement for corrected intervals.
- Never mix schemas blindly across runs; key your parser to `HDR_PART` sequence + `sample_schema`.
- If `dropped` increases or either diagnostic family (`adj_diag`, `*_total_adj_diag`) shows degraded modes, mark those windows in downstream statistics.
- For component studies, hard-gate any row where the relevant `adj_comp_diag` slot has missing-scale/degraded bits set; soft-gate or downweight multi-boundary slots depending on your estimator sensitivity.

## 6) Adjusted semantics versioning policy

- `adj_semantics_version` must be incremented whenever either of the following changes:
  - authority meaning split between component `*_adj` and direct-total `*_total_adj_direct`
  - interpretation of `adj_diag` and/or `*_total_adj_diag` bits
- This bump is required even when field names and column order remain unchanged.
- If a run omits `adj_semantics_version`, treat adjusted-path interpretation as unknown/legacy and avoid silently applying modern assumptions.

---

## 7) Minimal worked example

Given:
- `nominal_hz = 16000000`
- `tick_total_adj_direct = 7872380`
- `tock_total_adj_direct = 7871914`

Then:
- half-swing times:
  - `tick_s = 7872380 / 16000000 = 0.49202375 s`
  - `tock_s = 7871914 / 16000000 = 0.491994625 s`
- full-period estimate:
  - `period_s = (7872380 + 7871914) / 16000000 = 0.984018375 s`
