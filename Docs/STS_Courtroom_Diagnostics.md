# STS Courtroom Diagnostics

## What `dbstep` means
`dbstep` indicates a ±65,536 jump in quantized bridge term (`db`) while the PPS sample class remains `OK` and bridge residual `|br|` is small. This pattern is a classic signature of one lost 16-bit wrap in the reconstructed 32-bit timeline.

The emitted line is self-contained:
- timing: `tms`, `dm` (`dt32`), `dc` (`dt16_mod`), `db`, `br`
- coherent capture context: `now`, `prev`, `ovf`, `cnt0`, `if0`, `cap`, `cnt2`, `lat`
- sequencing and risk context: `edge_seq`, `gap_last`, `gap_max`
- masking/starvation context: `cli_max`, `cli_tag`, `isr0_max`, `isr1_max`, `isr2_max`

## Proof patterns from logs

### 1) Missed wraps from long interrupt masking / ISR starvation
Strong evidence chain:
1. `dbstep` spikes (`evt` increases).
2. `court.gap131072` increments and/or `gap_max > 131072`.
3. `court.cli_max` grows, especially with `cli_gt65536` or `cli_gt131072` increments.
4. `court.pps_backlog_max` or `miss_sus` increases around same interval.

If (2) and (3) co-occur near `dbstep`, this is smoking-gun timing starvation.

### 2) Coherent read issues
Check:
- `court.coh_retry`, `court.coh_seen`, `court.coh_applied`
- `court.backstep`

Interpretation:
- rising `coh_retry` with stable `backstep=0` means expected rescue path is active.
- any non-zero / increasing `backstep` means true monotonicity regressions and should be treated as correctness defects.

### 3) PPS capture issues (bounce, missed ISR, backlog)
Use `gap_evt` + `court`:
- bounce/double-trigger: `gap_evt` with very small `dc` and nearby `cap_prev`/`cap_cur`, plus `dup_sus` increase.
- missed ISR/service gaps: `court.miss_sus` and `pps_backlog_max` increase.
- threshold tightness/modulo anomalies: `court.gap_hist` mass shifts between bins while `pps_cfg` thresholds remain static.

## How to run

### PPS-only (pendulum stopped)
1. Boot with STS diagnostics enabled at compile time (`STS_DIAG=2`).
2. Leave PPS connected, pendulum blocked/stopped.
3. Capture STS overnight.
4. Focus on `court`, `dbstep`, `gap_evt`, `tcb0_health`, `gps_health`.

### Pendulum-running overnight
1. Same build (`STS_DIAG=2`).
2. Run with normal pendulum motion overnight.
3. Compare `court`/`dbstep` rates to PPS-only run.

## Analysis checklist (exact fields)
Compute these from logs:
1. `dbstep_rate = Δdbstep / Δtime` from `court`.
2. `wrap_risk_rate = Δgap65536 / Δtime`, `smoking_gun_rate = Δgap131072 / Δtime`.
3. `max_service_gap = max(gap_max)`.
4. `cli_tail`: `cli_max`, `cli_gt65536`, `cli_gt131072`, `cli_unbal`.
5. coherent health: `coh_retry`, `coh_seen`, `coh_applied`, `backstep`.
6. ISR pressure: `isr0_max`, `isr1_max`, `isr2_max`.
7. PPS pipeline: `pps_isr - pps_proc`, `pps_backlog_max`, `dup_sus`, `miss_sus`.
8. GAP morphology: `gap_hist` bins and `gap_evt.cap_prev/cap_cur/dc`.

## Conservative cleanup review (Part E)
- Keep existing `gps_health2`, `tcb0_health`, and `gps_decision` lines (still independently useful for triage).
- Candidate for future gating only: `pps_int` line, because `court` + `dbstep` now carry richer overlapping data.
- No removals were made in this pass; behavior-preserving diagnostics were prioritized.
