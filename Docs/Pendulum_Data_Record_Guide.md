# Pendulum Data Record Guide

This guide explains the pendulum sample record emitted by the Nano Every firmware, what each field means, and how to interpret the data during analysis.

Authoritative schema source in firmware:
- `Nano.Every/src/PendulumProtocol.h` (`TAG_*`, `SAMPLE_SCHEMA`, `PendulumSample`)

## Document status

Keep this document: it is the best user-facing explanation of the **DERIVED** pendulum sample schema (`HDR_PART` + `SMP`).

Important runtime note: current firmware is pinned to **CANONICAL** emit mode by default (`ACTIVE_EMIT_MODE = EMIT_MODE_CANONICAL` in `PendulumProtocol.h`). That means default output is `SCH` + `CSW`/`CPS`, not `HDR_PART` + `SMP`, unless emit-mode selection is changed at build time.

---

## 1) Record families on the serial stream (DERIVED mode)

In DERIVED mode, a run emits three line families that should be consumed together:

- `CFG,...` — session metadata (`nominal_hz`, `sample_tag`, `sample_schema`, `adj_semantics_version`)
- `HDR_PART,...` — segmented ordered declarations of sample columns
- `SMP,...` — one pendulum sample row per completed full swing, matching the latest complete `HDR_PART` sequence

**Important:** parsers should trust the latest complete `HDR_PART` sequence for column order and not hard-code positions.

---

## 2) Current sample schema (DERIVED mode)

For the current reduced firmware, the sample header declaration is:

```text
HDR_PART,1,4,tick,tick_block,tock,tock_block
HDR_PART,2,4,tick_adj,tick_block_adj,tock_adj,tock_block_adj
HDR_PART,3,4,tick_total_adj_direct,tick_total_adj_diag,tock_total_adj_direct,tock_total_adj_diag
HDR_PART,4,4,tick_total_f_hat_hz,tock_total_f_hat_hz,gps_status,holdover_age_ms,dropped,adj_diag,adj_comp_diag,pps_seq_row
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


### Provenance note for derived schema

In `raw_cycles_hz_v7` derived mode, per-interval `*_start_tag` / `*_end_tag` provenance fields are no longer emitted.
Use `adj_diag`, `adj_comp_diag`, and `*_total_adj_diag` to qualify data quality and boundary-crossing behavior.

### Frequency and state context

- `tick_total_f_hat_hz` / `tock_total_f_hat_hz` — row-context disciplined frequency estimate used for each half-swing total (ticks/sec)
- `gps_status` — PPS state code:
  - `0` = `NO_PPS`
  - `1` = `ACQUIRING`
  - `2` = `LOCKED`
  - `3` = `HOLDOVER`
- `holdover_age_ms` — elapsed holdover age in ms

Use these fields as quality/context indicators, not as replacements for interval columns.

### Quality / diagnostics

- `dropped` — cumulative dropped capture events from ring overflow

Canonical diagnostic bit tables:

#### `adj_diag` (component-adjustment diagnostics)

| Bit | Mask (hex/dec) | Name | Meaning when set |
|---|---:|---|---|
| 0 | `0x01` / 1 | `ADJ_DIAG_TICK_CROSSED` | `tick` interval crossed a PPS boundary |
| 1 | `0x02` / 2 | `ADJ_DIAG_TICK_BLOCK_CROSSED` | `tick_block` interval crossed a PPS boundary |
| 2 | `0x04` / 4 | `ADJ_DIAG_TOCK_CROSSED` | `tock` interval crossed a PPS boundary |
| 3 | `0x08` / 8 | `ADJ_DIAG_TOCK_BLOCK_CROSSED` | `tock_block` interval crossed a PPS boundary |
| 4 | `0x10` / 16 | `ADJ_DIAG_MISSING_SCALE` | At least one component used missing-scale handling |
| 5 | `0x20` / 32 | `ADJ_DIAG_DEGRADED_FALLBACK` | At least one component used degraded fallback |
| 6 | `0x40` / 64 | `ADJ_DIAG_MULTI_BOUNDARY` | At least one component crossed >1 PPS boundary |
| 7 | `0x80` / 128 | *(reserved)* | Must be `0` in current schema |

#### `*_total_adj_diag` (direct-total diagnostics)

| Bit | Mask (hex/dec) | Name | Meaning when set |
|---|---:|---|---|
| 0 | `0x01` / 1 | `DIRECT_ADJ_DIAG_CROSSED` | Direct total interval crossed a PPS boundary |
| 1 | `0x02` / 2 | `DIRECT_ADJ_DIAG_MISSING_SCALE` | Missing-scale handling occurred in direct total adjustment |
| 2 | `0x04` / 4 | `DIRECT_ADJ_DIAG_DEGRADED_FALLBACK` | Degraded fallback occurred in direct total adjustment |
| 3 | `0x08` / 8 | `DIRECT_ADJ_DIAG_MULTI_BOUNDARY` | Direct total interval crossed >1 PPS boundary |
| 4–7 | `0x10`..`0x80` | *(reserved)* | Must be `0` in current schema |

- `adj_comp_diag` — packed per-component degradation flags (unsigned 16-bit):
  - slot order: `tick`, `tick_block`, `tock`, `tock_block`
  - each slot uses 3 bits:
    - bit0 in slot: missing PPS scale
    - bit1 in slot: degraded fallback used
    - bit2 in slot: crossed more than one PPS boundary
  - decode:
    - `slot_mask = (adj_comp_diag >> (3 * slot_index)) & 0x7`
    - slot indices: `0=tick`, `1=tick_block`, `2=tock`, `3=tock_block`

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
6. For beam/amplitude-proxy studies, use component fields (`tick_adj`, `tick_block_adj`, `tock_adj`, `tock_block_adj`) and compare odd/even asymmetry over windows.

---

## 5) Interpretation rules (short version)

- Prefer `*_total_adj_direct` for full half-swing and period metrics; use component `*_adj` fields when you specifically need sub-interval analysis.
- Always read and persist `adj_semantics_version`; bump-sensitive analysis logic must key off that value, not only field names.
- Treat `tick_total_f_hat_hz/tock_total_f_hat_hz` as row context/diagnostics, not as a direct replacement for corrected intervals.
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
