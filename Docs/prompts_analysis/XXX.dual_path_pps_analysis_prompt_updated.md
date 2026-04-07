# Dual-Path PPS Analysis Prompt (TCB1 + TCB2, Current Nano Repo, Revised)

You are analysing a **dual-path PPS run** from the current Arduino Nano Every pendulum timing firmware.

In this run, the **same GPS PPS signal is wired to both capture paths**:

- **TCB2 path** = the dedicated PPS capture path, reflected in the **STS / `PPS_BASE`** telemetry
- **TCB1 path** = the pendulum capture path, reflected in **`pendulum.csv` / SMP rows**

The purpose of this analysis is to compare the two capture paths as directly and rigorously as possible, while also re-running the PPS stability / tuner diagnostics in a way that is consistent with the **current repo schema**, not older legacy prompts.

This is not a normal pendulum run.

## Important revised understanding

For **paired dual-path comparison**, the authoritative per-second correction source is **STS `PPS_BASE`**, not the row-level `tick_total_f_hat_hz/tock_total_f_hat_hz` in the pendulum/SMP file.

Reason:

- each TCB2/SMP row contains **two PPS seconds**
- but only **one** `tick_total_f_hat_hz/tock_total_f_hat_hz` and **one** `tick_total_f_hat_hz/tock_total_f_hat_hz`
- therefore the SMP-row correction fields are **not intrinsically phase-resolved to each one-second period**
- in a simultaneous TCB1-vs-TCB2 dual-PPS test, using SMP-row `tick_total_f_hat_hz/tock_total_f_hat_hz` to adjust both TCB1 seconds can create a **false appearance** that TCB1 adjusts worse than TCB2, even when the raw paired periods sit on top of each other

Therefore:

- For **main adjusted-path comparisons**, reconstruct the per-second applied estimate from STS:
  - `applied_hz_sts = d - eh`
- Then use that **same matched per-second STS `applied_hz`** to adjust:
  - TCB2 `d`
  - TCB1 `tick_period_raw`
  - TCB1 `tock_period_raw`
- Treat SMP `tick_total_f_hat_hz/tock_total_f_hat_hz` and `tick_total_f_hat_hz/tock_total_f_hat_hz` as:
  - useful row-level telemetry
  - acceptable for standalone / unpaired TCB2 analysis when STS is unavailable
  - **not** the authoritative source for a strict apples-to-apples dual-path adjusted comparison

You should still analyse the SMP-row `tick_total_f_hat_hz/tock_total_f_hat_hz` fields as a **secondary diagnostic**:

- compare SMP-row `tick_total_f_hat_hz/tock_total_f_hat_hz` to matched STS `applied_hz`
- quantify the mismatch
- if desired, report sensitivity tests such as:
  - `tick-prev / tock-current`
  - `tick-mid / tock-current`
  - `current/current`

But those should be clearly labelled as **secondary / telemetry alignment diagnostics**, not the main adjusted comparison.

## Physical meaning of the TCB2 "pendulum" rows in this special test

For this special test, the PPS signal is wired into the pendulum sensor input, so:

- `tick_block` and `tock_block` are the **RISING→FALLING high-time** of a PPS pulse
- `tick` and `tock` are the **remainder** of the PPS second
- each row therefore contains **two PPS seconds**
- define:
  - `tick_period_raw = tick + tick_block`
  - `tock_period_raw = tock + tock_block`

Those are the two one-second PPS periods represented in each TCB2 row.

---

## Files expected

The analysis should consume, as available:

- `PENDULUM.CSV` or equivalent exported pendulum sample file
- `STS.CSV` or equivalent STS log
- repo snapshot / source tree if needed for schema confirmation

You should use the **current Nano repo** as the source of truth for field meanings if the logs and older prompts/glossaries disagree.

---

## Current repo schema to assume unless the files prove otherwise

### SMP / pendulum sample rows

The authoritative sample header in the current repo is:

```text
tick,tock,tick_block,tock_block,tick_total_f_hat_hz,tock_total_f_hat_hz,gps_status,holdover_age_ms,dropped
```

Interpretation:

- `tick`, `tock`, `tick_block`, `tock_block` are raw cycle counts
- `tick_total_f_hat_hz/tock_total_f_hat_hz` is the instantaneous PPS-based ticks-per-second estimate
- `tick_total_f_hat_hz/tock_total_f_hat_hz` is the row-level currently applied / disciplined ticks-per-second estimate
- `gps_status` is the live GPS/PPS state used by the firmware
- `holdover_age_ms` is the holdover age in ms
- `tick_total_f_hat_hz` and `tock_total_f_hat_hz` provide row-level applied frequency context
- `dropped` flags sample drops

### PPS_BASE telemetry in STS

Current repo `PPS_BASE` schema is:

```text
PPS_BASE,q,m,d,ef,es,eh,pf,ps,pa,c,v,s,r,js,ja,lg,ub,l,cp
```

Interpretation for current repo:

- `d` = raw PPS interval in ticks for one GPS second
- `ef`, `es`, `eh` = residuals in ticks vs fast / slow / applied estimators
- `pf`, `ps`, `pa` = absolute residual magnitudes in ppm vs fast / slow / applied estimators
- `c` = PPS classifier code
- `v` = validator trust flag
- `s` = discipliner state
- `r` = fast/slow disagreement in ppm
- `js` = slow residual-scatter MAD metric in ticks
- `ja` = applied residual-scatter MAD metric in ticks
- `lg` = lock pass mask
- `ub` = unlock breach mask
- `l` = ISR service latency diagnostic in ticks
- `cp` = raw capture value

Do **not** assume older fields like `en`, `ff`, `fs`, `fh`, or legacy single `j` exist unless the file actually contains them.

---

## Core objectives

Your job is to produce a technically rigorous but operationally useful analysis that answers all of the following.

### A. Dual-path capture comparison

Compare the **same PPS signal** as seen through:

- **TCB2 / PPS path** via STS `PPS_BASE`
- **TCB1 / pendulum path** via `tick_period_raw` and `tock_period_raw`

Quantify whether the two paths are truly similar, as expected for the same signal captured through EVSYS hardware but processed through somewhat different firmware paths.

### B. Raw vs PPS-adjusted period quality

For both paths, compute and compare:

- **mean**
- **median**
- **standard deviation**

for the one-second PPS periods:

- in **raw cycles**
- in **microseconds**
- in **ppm**
- and in **PPS-adjusted cycles**

Be explicit that the **main adjusted comparison** uses the matched per-second STS `applied_hz = d - eh` for both paths.

### C. Sliding-window behavior

Repeat the above comparison on an **evolving 15-minute basis**, not just over the whole run, because live performance is assessed locally, not only over the entire overnight sample.

### D. PPS stability / tunables / lock diagnostics

Re-run the PPS diagnostics and tuning analysis in a way consistent with the current repo:

- settled vs startup behavior
- lock/unlock transitions
- evidence for or against changing tunables
- interpretation of `r`, `js`, `ja`, `lg`, `ub`, `l`
- implications for the pendulum timing use case


### E. Comparator-conditioned path analysis

In addition to the main STS-applied comparison, explicitly reconstruct and compare the three STS-side comparator estimates:

- **fast** = `d - ef`
- **slow** = `d - es`
- **blended/applied comparator** = `d - eh`

Important:

- `d - eh` is **not** a fourth independent estimate; it is the reconstruction of the blended/applied comparator from telemetry
- in the current repo, the **consumer-facing metrology/export estimate** may differ from the telemetry comparator, because downstream metrology can use `slow` in `DISCIPLINED` while STS `eh` still reflects the blended comparator
- therefore this section must clearly distinguish:
  - **telemetry comparators** reconstructed from STS (`d-ef`, `d-es`, `d-eh`)
  - **consumer/export estimate semantics** inferred from repo state logic or row-level fields

The report must show, for the matched per-second subset, how TCB1 tick/tock and TCB2 behave under each comparator choice.

### F. State-transition-conditioned analysis

Because estimator semantics may change with discipliner state, explicitly analyse matched TCB1 tick/tock periods by:

- matched STS state (`ACQUIRE`, `DISCIPLINED`, and other states if present)
- proximity to a state transition
- direction of transition where practical (`ACQUIRE→DISCIPLINED`, `DISCIPLINED→ACQUIRE`)

At minimum define:

- **near-transition** = within 5 seconds of an `ACQUIRE ↔ DISCIPLINED` boundary
- **far-from-transition** = more than 30 seconds from such a boundary

If those thresholds are varied, report the chosen thresholds explicitly and keep the defaults above unless there is a strong reason not to.

### G. DUAL_PPS_PROFILING telemetry, if present

If STS contains `DUAL_PPS_PROFILING` lines, parse and analyse them.

Important:

- do **not** invent a schema if one is not stated in the file or repo
- infer the exact emitted field names and meanings from the actual log and/or current repo
- if the lines are absent, say so explicitly and skip this section without penalty

The purpose of this section is to determine whether any path-to-path disagreement, transition-local anomaly, or residual adjustment discrepancy could be explained by explicit dual-path profiling telemetry rather than by estimator choice alone.

---

## 1. Data hygiene and parsing

### 1.1 Parse and validate both files

Parse:

- SMP / pendulum sample rows
- STS rows, especially `PPS_BASE`, `TUNE_EVT`, `TUNE_WIN`, `TUNE_CFG`, and other relevant status rows
- `DUAL_PPS_PROFILING` rows if present

Confirm:

- row counts
- missing values
- malformed rows
- field presence
- whether the observed headers match the current repo schema
- whether `DUAL_PPS_PROFILING` is present, and if so, what exact schema/field order is actually emitted

### 1.2 Identify valid analysis subsets

For SMP / TCB2 rows, define at minimum:

- full parsed set
- valid set: rows with `dropped = 0`
- PPS-usable set: rows with `gps_status` indicating usable PPS / disciplined operation
- disciplined-only set if appropriate

For STS / TCB2 rows, define at minimum:

- full `PPS_BASE` set
- valid set: `v = 1`
- disciplined-only set: `s = DISCIPLINED` if present

For **paired adjusted** analysis, prefer the intersection where:

- TCB2 is valid / disciplined
- TCB1 row is not dropped
- sequence pairing is clean

Be explicit about the inclusion rules and report retained counts and durations.

### 1.3 Time alignment and pairing

This is critical.

Because the TCB2 file contains **two PPS periods per row** but the TCB2 `PPS_BASE` file contains **one PPS period per row**, you must carefully describe how you align them.

At minimum:

- define TCB2 one-second series:
  - `tick_period_raw = tick + tick_block`
  - `tock_period_raw = tock + tock_block`
- define TCB2 one-second series:
  - `tcb2_period_raw = d`

Then attempt a principled alignment between TCB1 and TCB2 periods.

Possible strategies include:

- sequence-based pairing if metadata permits
- timestamp pairing using coarse time / row order
- nearest-neighbor pairing under monotonic order
- lag search / cross-correlation if needed

You must:

- explain the alignment rule used
- report whether there appears to be a fixed lag offset
- report how many periods could be paired cleanly
- identify any ambiguity caused by startup or missing rows

If exact per-second pairing is not reliable for some subset, say so clearly and separate:
- statistics that require pairing
- statistics that do not

---

## 2. Definitions and conversions

Use:

- nominal clock = **16,000,000 cycles per second**
- `1 tick = 62.5 ns = 0.0625 µs`
- for a 1-second interval:
  - `ppm_error = cycles_error / 16`
  - `ppm_error = microseconds_error`

When reporting one-second period errors versus nominal:

- `cycles_error = observed_cycles - 16,000,000`
- `us_error = cycles_error / 16`
- `ppm_error = cycles_error / 16`

Be explicit that for a 1-second period, the numeric value in **ppm** equals the numeric value in **µs error**.


Also define the STS-side comparator estimates explicitly:

```text
f_fast_sts  = d - ef
f_slow_sts  = d - es
f_blend_sts = d - eh
```

Use those exact names or equivalent clear labels when building the comparator-conditioned tables.

Important semantic reminder:

- `f_blend_sts = d - eh` is the **telemetry blended/applied comparator**
- it is not automatically identical to the estimate actually used downstream for pendulum metrology/export in every state
- if repo evidence is available, distinguish **telemetry comparator semantics** from **consumer/export semantics**

---

## 3. Raw one-second period analysis

### 3.1 TCB2 raw periods

For `PPS_BASE.d`, compute:

- mean
- median
- std
- p05 / p25 / p75 / p95 / p99
- min / max
- IQR

in:

- raw cycles
- error vs nominal in cycles
- error vs nominal in µs
- error vs nominal in ppm

Do this at least for:

- full valid set
- disciplined-only set

### 3.2 TCB1 raw periods

For both:

- `tick_period_raw`
- `tock_period_raw`

compute the same statistics in the same units and subsets.

### 3.3 Compact whole-run comparison table

Provide a compact table comparing, at minimum:

- TCB2 raw
- TCB1 raw tick
- TCB1 raw tock

with columns for:

- mean error (cycles / µs / ppm)
- median error (cycles / µs / ppm)
- std (cycles / µs / ppm)

---

## 4. PPS-adjusted period analysis

This is a key section.

### 4.1 Main correction measure for paired comparison

For the main paired TCB1-vs-TCB2 comparison, reconstruct the per-second applied estimate from STS:

```text
applied_hz_sts = d - eh
```

Then use that **same matched per-second STS `applied_hz_sts`** to adjust all paired one-second periods:

- TCB2: `d`
- TCB1 tick: `tick_period_raw`
- TCB1 tock: `tock_period_raw`

This is the authoritative apples-to-apples adjusted comparison.

### 4.2 Adjusted-period formula

For any raw observed one-second interval `x` and applied estimate `applied_hz`, define adjusted cycles as:

```text
x_adjusted = x * 16,000,000 / applied_hz
```

### 4.3 Secondary diagnostic: SMP-row correction fields

You should also analyse the pendulum/SMP row-level fields as a secondary diagnostic:

- compare SMP `tick_total_f_hat_hz/tock_total_f_hat_hz` with matched STS `applied_hz_sts`
- quantify mean / median / std of that mismatch
- if useful, report sensitivity tests for assigning row-level `tick_total_f_hat_hz/tock_total_f_hat_hz` to:
  - tick-prev / tock-current
  - tick-mid / tock-current
  - current/current

But those must be explicitly labelled as **secondary row-telemetry alignment checks**, not the main adjusted result.

### 4.4 Consistency check

If the raw paired paths sit essentially on top of each other but one adjusted path appears much worse than the other, you should treat that first as a likely **correction-source or alignment artifact**, not as a real hardware-path difference, unless independent evidence proves otherwise.

### 4.5 Adjusted whole-run tables

Provide a compact table for:

- TCB2 adjusted using matched STS `applied_hz`
- TCB1 tick adjusted using matched STS `applied_hz`
- TCB1 tock adjusted using matched STS `applied_hz`

with:

- mean error (cycles / µs / ppm)
- median error (cycles / µs / ppm)
- std (cycles / µs / ppm)

You may additionally provide a separate table showing what happens when SMP-row `tick_total_f_hat_hz/tock_total_f_hat_hz` is used.


### 4.6 Comparator-conditioned adjusted analysis (required)

For the matched per-second subset, adjust **both TCB2 and TCB1 tick/tock** using each of:

- `f_fast_sts = d - ef`
- `f_slow_sts = d - es`
- `f_blend_sts = d - eh`

Then report, for each comparator choice:

- TCB2 adjusted one-second error
- TCB1 tick adjusted one-second error
- TCB1 tock adjusted one-second error
- direct adjusted path differences:
  - `delta_tick_adjusted = TCB1_tick_adjusted - TCB2_adjusted`
  - `delta_tock_adjusted = TCB1_tock_adjusted - TCB2_adjusted`

The goal is to separate:
- true path disagreement
- comparator-choice effects
- state/transition effects

### 4.7 Compact comparator matrix (required)

Provide one compact comparison table with **rows**:

- `raw`
- `fast-adjusted`
- `slow-adjusted`
- `blend-adjusted`

and with **columns** covering at least:

- `tick`
- `tock`
- `tick−TCB2 delta`
- `tock−TCB2 delta`
- `DISCIPLINED`
- `ACQUIRE`
- `near-transition`
- `far-from-transition`

Interpretation guidance:

- for `tick` / `tock`, report at minimum mean / median / std error versus nominal
- for `tick−TCB2 delta` / `tock−TCB2 delta`, report at minimum mean / std of direct disagreement
- for `DISCIPLINED`, `ACQUIRE`, `near-transition`, and `far-from-transition`, report the corresponding subset-specific mean / std (and median where practical) for tick and tock under each row/estimator

This table is intended to be something the reader can “stare at in one place” to see:
- whether raw TCB1 and TCB2 are on top of each other
- which comparator is tightest
- whether `slow` degrades mainly in `ACQUIRE` and/or near state transitions
- whether transition-local effects dominate the apparent adjustment gap

### 4.8 Transition-local analysis (required)

Using matched STS states, identify `ACQUIRE ↔ DISCIPLINED` boundaries and analyse estimator behavior near them.

At minimum:

- count transitions by direction
- report how many matched tick/tock periods fall within:
  - 5 seconds of a transition
  - more than 30 seconds away from any transition
- compare `raw`, `fast-adjusted`, `slow-adjusted`, and `blend-adjusted` on those subsets
- explicitly assess whether the `slow` comparator deteriorates disproportionately near transitions
- if practical, report the one-step change in the active estimate across:
  - `ACQUIRE→DISCIPLINED`
  - `DISCIPLINED→ACQUIRE`

If the current repo semantics are available, connect the observed transition-local behavior to the fact that downstream metrology/export may use different estimator semantics by state.

---

## 5. Sliding-window analysis (required)

Evaluate performance over a **15-minute sliding window**, because that better reflects live operational behavior.

### 5.1 Window definitions

Use real 15-minute windows:

- **TCB2 / PPS_BASE:** 1 row = 1 PPS second → **900 samples per 15 minutes**
- **TCB1 / pendulum rows:** 1 row = 2 PPS seconds → **450 rows per 15 minutes**, or equivalently 900 one-second periods if you explode the rows into a per-second series

You may choose either implementation for TCB2, but state it clearly.

### 5.2 Sliding metrics

For each relevant series, compute rolling:

- mean error
- median error if feasible
- std

Then summarize the distribution of those rolling values, especially:

- p05
- median
- p95
- min / max if useful

Do this for:

- TCB2 raw
- TCB1 raw tick
- TCB1 raw tock
- TCB2 adjusted (STS-based)
- TCB1 adjusted tick (STS-based)
- TCB1 adjusted tock (STS-based)

### 5.3 Compact rolling comparison table

Provide a compact comparison table with at least:

- 15-minute median rolling mean error
- 15-minute median rolling std

for each of the above series, in:

- cycles
- ppm
- µs

### 5.4 Interpret the rolling results

Explicitly discuss:

- whether whole-run std materially overstates local noise
- whether the adjusted series are much more stable than the raw series
- whether TCB1 and TCB2 remain closely matched on a local basis
- whether any persistent raw or adjusted bias exists between the paths

---

## 6. Direct TCB1 vs TCB2 path-difference analysis (required)

Because this is a simultaneous dual-path run on the **same PPS signal**, this is one of the most important sections.

Once aligned as well as possible, compute direct per-period difference series such as:

- `delta_tick = TCB1_tick_raw - TCB2_raw`
- `delta_tock = TCB1_tock_raw - TCB2_raw`

Also compute the corresponding **STS-based adjusted** differences using the same matched per-second STS `applied_hz`.

Then report for each difference series:

- mean
- median
- std
- p05 / p95
- min / max
- IQR

in:

- cycles
- µs
- ppm-equivalent over 1 second if helpful

Also compute 15-minute rolling mean/std of the path difference.

This section is especially important because it largely cancels the common oscillator drift and should expose actual path-to-path disagreement.

Interpretation should address:

- fixed bias between paths
- relative noise floor between paths
- whether one path shows systematic offset or extra variance
- whether differences are small enough to support the expectation that the two EVSYS capture paths are effectively equivalent

If startup alignment ambiguity exists, isolate a well-paired settled subset and use that for the main difference metrics.

---

## 7. TCB1 / TCB2 comparison expectations and evaluation

You should directly assess whether the results support the engineering expectation that:

- the same PPS signal, captured by both hardware paths, should produce **very similar raw periods**
- and **very similar STS-adjusted periods**
- with only small residual differences attributable to:
  - capture path specifics
  - ISR / bookkeeping timing
  - row-structure / alignment choices
  - occasional anomalies

Be explicit about whether the data support or contradict that expectation.

---

## 8. PPS stability and tuning diagnostics (current repo semantics)

Use the STS file to assess PPS behavior and tunables, using the **current repo field meanings**.

### 8.1 Startup / acquisition / disciplined behavior

Summarize:

- startup period
- first entry into disciplined mode
- counts and durations by state `s`
- counts by classifier `c`
- counts by validity `v`
- any DISCIPLINED ↔ ACQUIRE or HOLDOVER chatter
- whether the system appears too eager to unlock or too slow to relock

### 8.2 Core settled-segment descriptive statistics

For the settled disciplined-valid segment, compute descriptive stats for at least:

- `d`
- `ef`
- `es`
- `eh`
- `pf`
- `ps`
- `pa`
- `r`
- `js`
- `ja`
- `l`

Report:

- mean
- median
- std
- p05 / p25 / p75 / p95 / p99
- min / max
- IQR

### 8.3 Interpretation of current metrics

Use current meanings:

- `r` = fast/slow disagreement in ppm
- `js` = slow MAD residual scatter in ticks
- `ja` = applied MAD residual scatter in ticks
- `lg` = lock pass mask
- `ub` = unlock breach mask
- `l` = ISR service latency diagnostic, not direct timestamp error

Do not translate these back into legacy field meanings unless the actual file is legacy.

### 8.4 Parameter / threshold analysis

Assess whether the current run supports changing any of the following:

- `ppsLockRppm`
- `ppsUnlockRppm`
- `ppsLockMadTicks`
- `ppsUnlockMadTicks`
- `ppsLockCount`
- `ppsUnlockCount`
- `ppsBlendLoPpm`
- `ppsBlendHiPpm`
- `fastShift`
- `slowShift`
- holdover / stale timeouts
- validator strictness

Use state transitions, `ub`, `lg`, `r`, `js`, `ja`, and residual behavior to justify recommendations.

Be explicit about confidence and tradeoffs.

### 8.5 Negative-control latency check

Use `l` as a negative-control metric.

Assess:

- whether `l` is stable
- whether rare `l` spikes line up with `eh`, `ja`, `js`, or path-difference anomalies
- whether latency appears too small/stable to explain the main noise floor


### 8.6 DUAL_PPS_PROFILING analysis (if present)

If `DUAL_PPS_PROFILING` lines are present, analyse them explicitly.

Required approach:

- infer the exact schema from the file and/or current repo
- list the emitted fields exactly as observed
- compute counts, missingness, malformed-line counts, and retained valid rows
- provide descriptive statistics for each profiling metric that appears materially relevant
- align the profiling rows, where possible, with:
  - raw `delta_tick` / `delta_tock`
  - adjusted path differences
  - `ACQUIRE` / `DISCIPLINED` state
  - proximity to transitions
  - anomalies in `eh`, `es`, `ef`, `ja`, `js`, `ub`, or `l`

Interpretation goals:

- determine whether the profiling telemetry reveals a fixed path offset, timer-domain offset, ISR-ordering issue, or bookkeeping artifact
- assess whether rare path-difference spikes are better explained by explicit profiling telemetry than by oscillator/estimator behavior
- state clearly whether the profiling evidence changes the main conclusion about path equivalence

If `DUAL_PPS_PROFILING` is absent, include a brief note that it was not present and therefore no profiling-specific conclusions can be drawn from that channel.

---

## 9. Visualizations (clean, Tufte-oriented)

Keep plots sparse, clear, and information-dense.

Recommended plots:

### Dual-path comparison
1. TCB2 raw period over time
2. TCB1 tick and tock raw periods over time
3. TCB2 adjusted period over time using STS `applied_hz`
4. TCB1 adjusted tick and tock periods over time using matched STS `applied_hz`
5. TCB1 vs TCB2 direct difference series over time
6. distributions / ECDFs of TCB1 raw vs TCB2 raw
7. distributions / ECDFs of TCB1 adjusted vs TCB2 adjusted
8. rolling 15-minute mean/std comparisons for raw and adjusted paths
9. SMP `tick_total_f_hat_hz/tock_total_f_hat_hz` minus matched STS `applied_hz` over time (secondary telemetry diagnostic)
10. comparator-conditioned overlays for `raw`, `fast`, `slow`, and `blend`
11. state-conditioned comparator plots for `DISCIPLINED` vs `ACQUIRE`
12. transition-local plots showing behavior within 5 seconds of `ACQUIRE ↔ DISCIPLINED` boundaries

### PPS tuning diagnostics
13. `d` over time
14. `eh` over time
15. `r` over time
16. `js` and `ja` over time
17. `l` over time
18. state transition timeline / counts
19. focused anomaly zooms where `ub` is non-zero or path differences spike
20. `DUAL_PPS_PROFILING` metrics over time and against path-difference anomalies, if present

---

## 10. Recommendation matrix (required)

End with a concrete recommendation matrix.

At minimum include:

- whether TCB1 and TCB2 appear equivalent for practical purposes
- whether either path appears measurably noisier or biased
- whether the TCB1 pendulum-capture path is suitable for precision pendulum metrology given this PPS cross-check
- whether STS should be treated as the authoritative correction source in future dual-PPS cross-checks
- whether SMP-row `tick_total_f_hat_hz/tock_total_f_hat_hz` needs finer per-second telemetry if it is to support standalone adjusted comparisons
- whether raw whole-run stats or rolling-window stats are more representative for operational use
- any recommended tunable changes
- any additional telemetry or firmware changes recommended

For each recommendation include:

- recommendation
- confidence
- evidence
- tradeoff / rationale

---

## 11. Final questions you must answer directly

At the end, answer these explicitly:

1. How similar are the raw one-second PPS periods on TCB1 vs TCB2?
2. How similar are the **STS-adjusted** one-second PPS periods on TCB1 vs TCB2?
3. What are the whole-run mean / median / std for each path, in cycles, µs, and ppm?
4. What are the 15-minute median rolling mean / std for each path, in cycles, µs, and ppm?
5. What is the best estimate of the direct path-to-path disagreement after common oscillator drift is removed?
6. Does the evidence support the expectation that the two hardware capture paths are effectively equivalent?
7. Does the STS log suggest any tunable changes now?
8. What remaining telemetry or firmware changes would make the next comparison even cleaner?
9. How large is the mismatch between SMP-row `tick_total_f_hat_hz/tock_total_f_hat_hz` and matched STS `applied_hz`, and does that explain any apparent adjusted-path discrepancy?
10. How do `fast`, `slow`, and `blend/applied` comparator-conditioned adjustments compare for TCB1 tick/tock and TCB2?
11. Which comparator is tightest overall, and does that ranking change between `DISCIPLINED`, `ACQUIRE`, near-transition, and far-from-transition subsets?
12. Do the results indicate that apparent adjustment differences are primarily due to comparator choice / state semantics rather than hardware path mismatch?
13. If `DUAL_PPS_PROFILING` is present, does it materially change the interpretation of path equivalence or transition-local anomalies?

---

## 12. Output package

Produce:

1. a main markdown report
2. key plots as image files
3. a concise executive summary
4. compact comparison tables suitable for pasting into notes / email
5. a compact comparator matrix covering `raw`, `fast-adjusted`, `slow-adjusted`, and `blend-adjusted`
6. if practical, a machine-readable CSV of the key summary statistics

Be technically rigorous, but operationally useful.

Prioritize:
- clear units
- explicit alignment assumptions
- separation of startup vs settled behavior
- honest treatment of uncertainty
- emphasis on direct path comparison rather than only separate-run summaries
- and avoidance of false adjusted-path conclusions caused by row-level TCB2 correction ambiguity
