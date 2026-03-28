# Pendulum Data Record Guide

This guide explains the pendulum sample record emitted by the Nano Every firmware, what each field means, and how to interpret the data during analysis.

Authoritative schema source in firmware:
- `Nano.Every/src/PendulumProtocol.h` (`TAG_*`, `SAMPLE_SCHEMA`, `PendulumSample`)

---

## 1) Record families on the serial stream

A run emits three line families that should be consumed together:

- `CFG,...` — session metadata (`nominal_hz`, `sample_tag`, `sample_schema`)
- `HDR,...` — the exact ordered list of sample columns
- `SMP,...` — one pendulum sample row per completed full swing, matching the latest `HDR`

**Important:** parsers should trust the latest `HDR` line for column order and not hard-code positions.

---

## 2) Current sample schema

For the current reduced firmware, the sample header is:

```text
HDR,tick,tick_adj,tick_block,tick_block_adj,tock,tock_adj,tock_block,tock_block_adj,f_inst_hz,f_hat_hz,gps_status,holdover_age_ms,r_ppm,j_ticks,dropped,adj_diag
```

If `ENABLE_PENDULUM_ADJ_PROVENANCE=1` at build time, one extra field is appended:

```text
...,pps_seq_row
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

These are PPS-aware corrected intervals in nominal-clock-equivalent ticks (nominally 16 MHz on default Nano Every builds).

For end-user timing analysis, these `*_adj` fields are the authoritative intervals to use.

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
- `adj_diag` — bitmask about interval-adjustment quality:
  - bit0: `tick` crossed a PPS boundary
  - bit1: `tick_block` crossed a PPS boundary
  - bit2: `tock` crossed a PPS boundary
  - bit3: `tock_block` crossed a PPS boundary
  - bit4: missing PPS scale
  - bit5: degraded fallback used
  - bit6: crossed more than one PPS boundary

### Optional provenance

- `pps_seq_row` (optional build mode) — PPS sequence id associated with final interval closure for the row.

---

## 4) Recommended analysis workflow

1. Parse and retain `CFG` + `HDR` before processing `SMP`.
2. Use `nominal_hz` from `CFG` for converting ticks to seconds:
   - `seconds = ticks / nominal_hz`
3. Use `tick_adj` and `tock_adj` as primary half-swing intervals.
4. Build full-period observables from adjacent half-swings:
   - `period_ticks_adj = tick_adj + tock_adj`
5. Gate/annotate data quality using:
   - `gps_status` (prefer `LOCKED` for best stability)
   - `dropped` (detect acquisition stress/overflow windows)
   - `adj_diag` bits 4/5/6 (flag degraded adjustments)
   - `r_ppm` and `j_ticks` (monitor discipliner noise/outlier windows)
6. For beam/amplitude-proxy studies, use `tick_block_adj`/`tock_block_adj` and compare odd/even asymmetry over windows.

---

## 5) Interpretation rules (short version)

- Prefer `*_adj` over raw fields for end-user timing metrics.
- Treat `f_hat_hz` as row context/diagnostics, not as a direct replacement for corrected intervals.
- Never mix schemas blindly across runs; key your parser to `HDR` and `sample_schema`.
- If `dropped` increases or `adj_diag` shows degraded modes, mark those windows in downstream statistics.

---

## 6) Minimal worked example

Given:
- `nominal_hz = 16000000`
- `tick_adj = 7872380`
- `tock_adj = 7871914`

Then:
- half-swing times:
  - `tick_s = 7872380 / 16000000 = 0.49202375 s`
  - `tock_s = 7871914 / 16000000 = 0.491994625 s`
- full-period estimate:
  - `period_s = (7872380 + 7871914) / 16000000 = 0.984018375 s`

