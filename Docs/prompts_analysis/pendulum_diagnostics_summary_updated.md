# Pendulum Analysis & GPS PPS Diagnostics Playbook (Current Repo / Current STS Schema)

This document is a **practical playbook** for two related but distinct activities:

1. **Pendulum analysis (core work):** estimating period, drift, stability, asymmetry, and impulse-cycle structure of the pendulum, using a disciplined timebase when available.
2. **GPS PPS diagnostics (debug work):** validating the PPS stream and disciplining pipeline, and diagnosing rare wrap / mapping / latency issues when something looks wrong.

The intent is that **pendulum analysis can run routinely**, while **PPS diagnostics are primarily for fault-finding** when lock, jitter, or timeline reconstruction looks suspicious.

---

## 1) Key concepts and state definitions

### 1.1 Tick domain vs seconds
Raw timestamps are in **MCU timer ticks / cycles**. Conversion to seconds requires an estimate of **ticks per second**.

- Prefer naming raw units as `*_ticks` or `*_cycles`.
- If legacy columns are named `*_us`, treat them as **ticks** unless proven otherwise.
- In the current pendulum CSV, the Nano emits raw-cycle fields `tick`, `tock`, `tick_block`, and `tock_block`. Convert them to time units off-device using the disciplined ticks-per-second context appropriate for your analysis.

### 1.2 PPS input validity vs discipline convergence
Do not conflate “PPS looks sane” with “the discipliner has converged”.

**PPS validity (`pps_valid`)** answers: *is PPS a sane 1 Hz reference right now?*
Typical sample classes / reasons include `OK`, `GAP`, `DUP`, `HARD_GLITCH`, and soft-outlier handling where applicable.

**Discipline state (`disc_state`)** answers: *is the ticks→seconds scale stable enough for high-confidence pendulum analysis?*
Current live states are typically:
- `FREE_RUN`
- `ACQUIRE`
- `DISCIPLINED`
- `HOLDOVER`

**Rule of thumb**
- Use `pps_valid` to decide whether PPS is trustworthy as a reference.
- Use `disc_state == DISCIPLINED` to decide whether derived pendulum rates (ppm, sec/day, min/day) are high confidence.

### 1.3 Pendulum CSV status mapping
Current pendulum CSVs typically expose `gps_status` numerically:

- `0` = `FREE_RUN`
- `1` = `ACQUIRE`
- `2` = `DISCIPLINED`
- `3` = `HOLDOVER`

Use this mapping when the CSV does not contain the textual discipline state.

### 1.4 “Hard glitch” vs “soft outlier”
- **Hard glitch (HG):** an implausible PPS sample (e.g., obvious gap, duplicate, severe reconstruction anomaly, or very large deviation). These should not update estimators.
- **Soft outlier (SO):** a sample that is unusual but still plausible. These may be counted, downweighted, or excluded from some statistics, but do not necessarily imply bad PPS.

The current firmware tracks both rolling-window and cumulative counters for these classes in the `gps_health*` stream.

### 1.5 Wrap / mapping diagnostics are now broader than `db_step`
Older analysis focused on a single `db_step` signal. In the current repo / logs, wrap and mapping issues are better diagnosed using a **family of signals**, including:

- `br`, `br_mad`, `br_max`
- `gap65536`, `gap131072`
- `cli_gt65536`, `cli_gt131072`, `cli_unbal`
- `coh_retry`, `coh_seen`, `coh_applied`
- `backstep`
- `lat16_max`, `lat16_wr`

Treat these as the current primary forensic signals for extension / wrap / coherent-read issues.

---

## 2) Data quality gates and recommended tiers

To make analyses comparable, use explicit tiers.

### Tier A (high-confidence pendulum metrics)
- `disc_state == DISCIPLINED` (or `gps_status == 2`)
- `pps_valid == 1` where available
- `dropped == 0`
- exclude obvious hard glitches / corrupt rows

### Tier B (usable, lower confidence)
- `pps_valid == 1`
- `ACQUIRE` or `DISCIPLINED`
- allow soft outliers, or report separately

### Tier C (debug / exploratory)
- all rows except obvious corruption
- use mainly for acquisition debugging, state-machine behaviour, or logging issues

---

# Part I — Pendulum analysis (core work)

## 3) Pendulum analysis: objectives
Primary outputs once timebase is disciplined:

- **Period** and deviation from nominal (e.g. 2.000000 s)
- **Rate error** in ppm and sec/day or min/day
- **Short-term stability** (e.g. overlapping Allan deviation)
- **Impulse-cycle structure** (e.g. 15-swing fold for a 30 s impulse on a 2 s pendulum)
- **Tick–tock asymmetry** and slow drift vs environment / geometry / impulse conditions

## 4) Pendulum period calculation (recommended)

### 4.1 Canonical full-period calculation
If the CSV contains per-swing components, use:

`P_ticks = (tick_block + tick) + (tock_block + tock)`

Then convert to seconds via:

`P_s = P_ticks / ticks_per_second`

Where `ticks_per_second` comes from the discipline pipeline if available. If not, use nominal `F_CPU` and mark confidence lower.

### 4.2 Half-cycle asymmetry
A useful asymmetry measure is:

`A_half_ticks = (tick_block + tick) - (tock_block + tock)`

Persistent nonzero or drifting `A_half` can indicate geometry, sensor threshold, impulse asymmetry, or thermal/mechanical change.

### 4.3 Outlier handling
Use robust filtering on `P_ticks` or `P_s`:

- rolling medians
- MAD / Hampel filtering
- hard rejection of clearly impossible intervals for the clock under test

Prefer reporting **raw**, **robust**, and **filtered** summaries separately when diagnosing a troublesome run.

## 5) Standard pendulum outputs and plots
Run these routinely (Tier A preferred):

1. **Summary stats**
   - count of samples, duration
   - mean / median period
   - ppm error vs nominal period
   - drift in sec/day or min/day

2. **Time series**
   - period or ppm vs time
   - rolling medians (e.g. 5–15 min)
   - overlay discipline-state transitions if present

3. **Impulse-cycle phase folding**
   - for 30 s impulse on 2 s pendulum, fold with `L = 15`
   - plot mean period per phase bin
   - compare all phases vs impulse-adjacent phases

4. **Tick–tock asymmetry**
   - `A_half` vs time
   - rolling median of asymmetry
   - compare with temperature / pressure / humidity if available

5. **Stability**
   - overlapping Allan deviation of fractional frequency
   - inspect whether stability improves with averaging time `τ`

6. **Environment coupling**
   - regress drift / period / asymmetry vs temperature, pressure, humidity
   - use Tier A data unless explicitly studying acquisition / holdover behaviour

---

# Part II — GPS PPS diagnostics (debug work)

## 6) PPS diagnostics: when to run
Run PPS diagnostics when any of the following happens:

- discipline never reaches `DISCIPLINED`
- lock thrash / repeated transitions / reseed churn
- unexpected step changes in pendulum scale or correction terms
- frequent `pps_valid` loss or repeated non-`OK` reasons
- suspicious wrap / mapping / coherent-read signals
- baseline PPS jitter seems too large for the hardware / environment

If things are stable, keep PPS diagnostics lightweight and routine.

## 7) Current log schema mapping
Use the current log families explicitly if present.

### 7.1 Pendulum CSV
Typical useful fields:
- `tick`, `tock`, `tick_block`, `tock_block` (raw timer-cycle counts emitted by the Nano)
- `corr_inst_ppm`, `corr_blend_ppm`
- `gps_status`
- `dropped`
- environmental columns (temperature, pressure, humidity, etc.)

### 7.2 STS headers / run metadata
Typical setup / identity lines include:
- `build`
- `schema`
- `flags`
- `tun1`
- `pps_cfg`

Read these first. In particular, `tun1` shows **live** tunables and can reveal migration / EEPROM surprises.

### 7.3 STS routine health / decision families
Current firmware commonly emits:
- `gps_decision`
- `gps_health1`
- `gps_health2`
- `gps_health3`
- `gps_health_d1`
- `gps_health_d2`
- `court1`
- `court2`
- `court3`
- `court4`
- `court5`
- `gps1`
- `gps2`
- `pend_health`
- `tcb0_health`

### 7.4 Optional forensic / snap-dump families
If enabled, also use:
- `gps_snap_dump`
- `gps_snap1`
- `gps_snap2`

These are optional forensic enrichments, not guaranteed routine output.

## 8) Minimum PPS diagnostics signals (current firmware)
Prefer these from routine STS output.

### 8.1 Decision / state layer
From `gps_decision`, use where available:
- `state`
- `pps_valid`
- `cls`
- `lockN`
- `dt32`
- `dt16`
- `R_ppm`
- `J`
- `reason`

These are the primary “what decision did the state machine make, and why?” signals.

### 8.2 Rolling health layer
From `gps_health1`, `gps_health2`, and `gps_health3`, use:
- `R`, `J`
- `br_mad`, `br_max`
- short-window counts/rates such as `w10`, `lr10`, `so10`, `hg10`, `gap10`, `bs10`
- cumulative counts such as `hg_cnt`, `gap_cnt`, `so_cnt`, `warm_so_cnt`, `bs_cnt`, `edge_gap_cnt`, `trunc_cnt`
- ISR and latency health such as `maxisr_tcb0`, `maxisr_tcb1`, `maxisr_tcb2`, `lat16_max`, `lat16_wr`

### 8.3 Detailed per-edge health layer
From `gps_health_d1` and `gps_health_d2`, use:
- `edge_seq`
- class counters / rolling status
- `f_fast`, `f_slow`, `f_hat`
- `R_ppm`
- `mad_ticks`
- `hold_ms`
- `dt16`, `dt32`
- `br`
- `lat16_max`, `lat16_wr`
- seeding / reseed / reference-source fields where present

### 8.4 Courtroom / wrap-mapping layer
From `court1`–`court5`, use the current wrap / mapping diagnostics such as:
- `cli_gt65536`
- `cli_gt131072`
- `cli_unbal`
- `gap65536`
- `gap131072`
- `coh_retry`
- `coh_seen`
- `coh_applied`
- `backstep`

These are the current preferred signals for diagnosing extension-path issues that older notes may have described as “`db_step` problems”.

### 8.5 Optional forensic layer
If `gps_snap*` is present, also inspect:
- `dc`
- `dm`
- expected interval / error terms
- MAD(error)
- coherent snapshot inputs / extension reconstruction context

Do **not** assume `gps_snap*` is always present.

## 9) Recommended PPS debug procedures

### 9.1 First-order sanity
- Verify PPS reaches `ACQUIRE` and then `DISCIPLINED`
- Check that non-`OK` reasons are rare once stable
- Check cumulative counters for `gap_cnt`, `so_cnt`, `hg_cnt`, `bs_cnt`
- Confirm `pps_backlog_max` and related backlog indicators stay near zero if present

### 9.2 State / reason audit
Summarise:
- time in each `state`
- first time to `DISCIPLINED`
- counts by `reason`
- lock / unlock episodes
- whether `R_TOO_HIGH` or `J_TOO_HIGH` are startup-only, persistent, or clustered

### 9.3 Latency / coherent-read audit
Use current signals rather than older `dt_read` instrumentation:
- `lat16_max`
- `lat16_wr`
- `coh_retry`
- `coh_seen`
- `coh_applied`

Interpretation:
- low `lat16_max`, zero `lat16_wr`, and modest coherent retries suggest a healthy reconstruction path
- `lat16_wr > 0` or large coherent-read pressure suggests possible reconstruction stress

### 9.4 Wrap / mapping audit
Use:
- `br`, `br_mad`, `br_max`
- `gap65536`, `gap131072`
- `cli_gt65536`, `cli_gt131072`, `cli_unbal`
- `backstep`

Interpretation:
- stable low `br` metrics suggest mapping consistency
- ±65536- or ±131072-like counts indicate wrap / extension / ordering problems worth investigation
- persistent `backstep` or unbalanced coherent-read counters deserve attention

### 9.5 Quiet-mode A/B test (only if needed)
If baseline jitter is puzzling:
- run 10–20 minutes in a reduced-activity mode
- suppress avoidable SD writes / display refresh / heavy prints
- compare `R_ppm`, `J`, `br_mad`, and latency counters vs normal mode

A large improvement suggests software load, ISR pressure, or power / coupling effects.

### 9.6 Snap-dump forensics (if enabled)
If `gps_snap*` exists, inspect it for anomalies around:
- `HARD_GLITCH`
- `GAP`
- `DUP`
- sudden wrap / mapping signatures

If `gps_snap*` is absent, rely on `gps_health_d2`, `gps_decision`, and `court*` as the primary forensic source.

---

## 10) “Once settled, it should stay settled” — but keep guards
Even when PPS is behaving well:

- keep rolling rates and periodic health output
- keep build / schema / tunables headers
- keep enough wrap / mapping counters to confirm nothing is regressing
- treat `tun1` as a source of truth for **live** runtime tunables

A notable current example: a run can report `slowShift=1` live even if the compile default is `PPS_SLOW_SHIFT_DEFAULT = 8`, because EEPROM migration precedence may still seed from legacy `ppsEmaShift`. Always verify the live settings from the log rather than assuming compile defaults were applied.

---

## 11) Checklist: routine vs debug-only

### Routine (pendulum analysis)
- period and drift in real seconds
- rolling medians and ppm vs time
- phase folding
- tick–tock asymmetry
- Allan deviation
- environment coupling

### Debug-only (PPS diagnostics)
- state / reason audit
- `gps_decision` and `gps_health*` review
- wrap / mapping audit from `court*` and `gps_health_d2`
- latency / coherent-read audit
- snap-dump forensics if present
- quiet-mode A/B comparisons when jitter source is unclear

---

## 12) Suggested file outputs for a “morning review”
For a long overnight run, generate:

- `pendulum_summary.md` — Tier A and Tier B pendulum summaries
- `pendulum_timeseries.csv` — downsampled time-series export
- `pps_health_summary.md` — time in state, reasons, anomaly counts, wrap / latency health
- `pps_forensics.csv` — extracted `court*`, `gps_health_d2`, and optional `gps_snap*` anomalies
- plots: period vs time, ppm vs time, phase fold, Allan deviation, asymmetry vs time, temperature correlation, `R_ppm` / `J` vs time, and selected wrap / latency metrics
