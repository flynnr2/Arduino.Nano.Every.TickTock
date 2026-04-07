# PPS Baseline Analysis Prompt (Revised for Parameter Tuning)

You are analysing a **PPS-only baseline run** from a pendulum timing firmware.

The purpose of this analysis is **not merely to describe the PPS run**, but to help choose and justify firmware parameter values that will support **accurate pendulum timestamping**.

In this system, the ultimate goal is to measure pendulum timing accurately enough to dial in clock accuracy. The PPS-only run is therefore being used to characterize:

- the MCU clock relative to GPS PPS
- the short-term residual noise floor after discipline
- the degree to which PPS noise averages out over longer windows
- the extent to which ISR latency, capture path, or filter choices contribute to measurement noise
- the best parameterization of the online PPS quality/disciplined-clock metrics, especially **J**, **R**, and the fast/slow/blended estimator dynamics

Your job is to produce a technically rigorous but operationally useful analysis that leads to **clear tuning recommendations**.

---

## 1. Context and terminology

The input file is expected to contain compact per-PPS telemetry, typically in a schema like:

- `d` — raw observed PPS interval in MCU ticks for one GPS second
- `en = d - 16000000` — error versus nominal 16 MHz
- `ff`, `fs`, `fh` — fast, slow, and applied/blended frequency estimates
- `ef = d - ff`
- `es = d - fs`
- `eh = d - fh`
- `j` — rolling robust residual-scatter metric derived from recent `eh`
- `r` — rate/frequency metric used by the discipliner
- `l` — PPS ISR latency metric (`lat16`)
- `v` — validity flag
- `s` — discipliner state
- `c` — PPS classification / validator decision
- `q`, `m` — sequence / coarse time

Treat the fields in their **native units** unless there is strong reason to convert them for readability.

Where helpful, convert ticks to microseconds using:

- 16 MHz MCU clock
- 1 tick = 62.5 ns
- 16 ticks = 1 µs

When discussing frequency offset from nominal, ppm conversion is:

- `ppm = ticks_per_second_error / 16000000 * 1e6`

---

## 2. Primary analytical objective

This is a **parameterization study** for pendulum timing, not just a telemetry summary.

You should explicitly evaluate what the PPS-only run implies for:

1. the best way to interpret the online `j` metric
2. whether the current `j` window is sensible
3. whether `j` lock/unlock thresholds look too loose or too tight
4. whether `r` lock/unlock thresholds look too loose or too tight
5. whether the fast/slow estimator responsiveness appears well chosen
6. whether the blend thresholds appear sensible
7. whether validator thresholds or holdover behavior need attention
8. the practical timing floor likely to matter for pendulum timestamping

Do **not** stop at describing distributions. Push through to actual recommendations.

---

## 3. Data hygiene and segmentation

### 3.1 Parse and validate

- Parse all PPS telemetry lines into a structured table.
- Confirm field presence and type consistency.
- Report total PPS samples.
- Report counts by:
  - validity (`v`)
  - state (`s`)
  - classification (`c`)
- Report any malformed rows or missing values.

### 3.2 Startup / acquisition / anomaly review

Explicitly identify:

- startup / acquisition period
- first sustained entry into disciplined-valid operation
- state transitions
- obvious outliers or pathological intervals
- duplicated / missing / gap-like events

### 3.3 Define the **settled analysis segment**

This is critical.

Create a primary **settled segment** for parameterization work. The settled segment should:

- include only `v = 1`
- include only the stable disciplined state (for example `s = DISCIPLINED` or the state code corresponding to disciplined operation)
- exclude the startup / acquisition period
- exclude an additional short buffer after the transition into disciplined operation if needed
- optionally exclude a small buffer after obvious anomalies or state changes if justified

Report exactly how the settled segment was defined, including:

- the rule used
- the first included sample/time
- the number of samples retained
- the approximate elapsed duration retained

### 3.4 Keep startup separate

Startup/acquisition should still be analyzed, but **do not let it dominate the parameter recommendations**.

Provide:

- a brief startup summary
- separate startup statistics where useful
- but base the main tuning recommendations on the settled segment

---

## 4. Core descriptive analysis

Compute and report, first for the full valid set and then for the settled segment:

- mean
- median
- standard deviation
- p05 / p25 / p75 / p95 / p99
- min / max
- IQR

for at least these fields:

- `en`
- `ef`
- `es`
- `eh`
- `r`
- `j`
- `l`

Comment on:

- how much `en` reflects absolute clock offset/drift from nominal
- how much tighter `eh` is than `en`
- whether `j` appears to be summarizing a real local residual-scatter process
- whether `l` is stable with only a sparse right tail

---

## 5. Visualizations (Tufte-oriented)

Use clean, information-dense, low-ink visualizations based on the **settled segment** data, i.e. excluding the intial noise of settling down and locking to gps.

Avoid clutter, chartjunk, heavy fills, unnecessary grids, or decorative styling.

Preferred plots:

### 5.1 Time series

1. `en` over time
   - include a rolling median or robust smooth
   - annotate state transitions if present

2. `eh` over time
   - include a rolling median / robust smooth

3. `j` over time
   - show startup and settled behavior clearly

4. `l` over time
   - make rare spikes visible without obscuring the baseline

5. `fh`, and optionally `ff` / `fs`, over time
   - or show their residuals if that is clearer

### 5.2 Distributions

6. histogram / density / ECDF of `en`
7. histogram / density / ECDF of `eh`
8. histogram / density / ECDF of `j`
9. histogram / discrete distribution of `l`

Where appropriate, compare:

- full valid segment vs settled segment
- early vs late run
- startup vs disciplined regime

### 5.3 Comparative plots

10. side-by-side comparison of `en` vs `eh` distributions
    - to show how much the discipliner removes long-timescale offset/drift

11. overlay of firmware `j` vs offline rolling residual-scatter estimates of `eh`
    - for multiple windows, e.g. 15 / 31 / 61 / 121 samples

12. Allan deviation / overlapping Allan deviation of:
    - the fractional frequency derived from `d`
    - and/or the residual process `eh`, depending on clarity

### 5.4 Optional but useful

13. scatter / correlation view of `j` vs `l`
14. anomaly-local zoom plots around rare spikes in `j`, `l`, or `eh`
15. early-run vs settled-run overlays for `en`, `eh`, and `j`

---

## 6. Diagnostics specifically for parameter tuning

This section is the heart of the analysis.

### 6.1 `J` interpretation and window study

Treat `j` as a **rolling local residual-scatter metric**, not a universal intrinsic jitter constant.

Using the settled segment:

- compute offline rolling robust scatter estimates of `eh` for candidate windows such as:
  - 15
  - 31
  - 61
  - 121
- compare each to firmware `j`
- quantify:
  - correlation
  - median ratio / scale ratio
  - p95 ratio
  - smoothness
  - responsiveness to local excursions
  - how long an anomaly contaminates the metric

Assess whether the current `j` window appears:

- too short / too noisy
- about right
- too long / too sluggish

Then recommend:

- a preferred `j` window for **online health monitoring**
- a preferred `j` window for **more conservative/smoother characterization** if different

### 6.2 `R` behavior and thresholds

Study the distribution and time series of `r` in the settled segment.

Assess:

- whether the good regime sits comfortably inside current lock thresholds
- whether there is adequate hysteresis between lock and unlock behavior
- whether `r` appears too reactive or too sluggish relative to real clock changes
- whether `r` contains startup/acquisition effects that should not drive threshold recommendations

Recommend:

- `r` lock threshold
- `r` unlock threshold
- whether current defaults seem appropriate or should change

### 6.3 Fast/slow estimator dynamics (`ff`, `fs`, `fh`)

Assess whether the current estimator responsiveness appears well chosen.

Look for signs that:

- `fh` is too sluggish, leaving trend in `eh`
- `fh` is too reactive, chasing short-term PPS noise
- `ff` and `fs` are sensibly separated
- the blend between them appears to behave sensibly

If possible, comment on whether the current fast/slow filter constants (e.g. `fastShift`, `slowShift`) look:

- too aggressive
- too conservative
- broadly reasonable

### 6.4 Blend thresholds

If the telemetry allows it, infer whether the blend thresholds (`ppsBlendLoPpm`, `ppsBlendHiPpm` or their equivalent) appear well tuned.

Assess whether:

- the blend transitions where you would expect
- the system tends to hug one regime unnecessarily
- the applied estimate `fh` seems to reflect a sensible compromise between tracking and smoothing

### 6.5 Validator / anomaly policy

Using classification counts and the tails of `d`, `en`, `eh`, `j`, and `l`, assess whether:

- obvious bad PPS samples are being rejected cleanly
- good PPS samples are being accepted cleanly
- there is evidence that hard-glitch / gap / duplicate thresholds are too loose or too strict
- holdover behavior appears too sticky or too eager

Only recommend validator/holdover changes if the data really support them.

### 6.6 Negative control: latency

Use `l` as a negative-control diagnostic.

Assess whether:

- `l` is so stable that it is unlikely to be the main driver of `eh` / `j`
- rare `l` spikes line up with meaningful degradation in `eh` or `j`
- the short-term noise floor appears dominated by PPS-side / measurement-side noise rather than ISR latency

---

## 7. How to interpret the noise for pendulum timing

Explicitly address the following engineering question:

> For the purpose of accurate pendulum timestamping, what part of the PPS-only behavior should be treated as the likely short-term timing floor, and what part should be treated as long-term MCU drift that the discipliner can track?

In your answer, clearly separate:

- `en` as raw MCU-vs-nominal behavior
- `eh` as residual around the applied estimate
- `j` as rolling local robust scatter of `eh`
- `l` as ISR-service latency rather than necessarily timestamp error

Explain whether the evidence supports the intuition that:

- the MCU clock is relatively smooth short-term but drifts/wanders long-term
- PPS is noisier short-term but averages out over longer windows
- the discipliner should therefore blend MCU short-term smoothness with GPS long-term truth

Translate that into what matters for pendulum timing.

---

## 8. Allan deviation and timescale guidance

Compute and interpret Allan deviation or overlapping Allan deviation where feasible.

Use it to answer:

- at what timescales does averaging materially reduce apparent noise?
- at what timescales does slower wander / non-stationarity begin to dominate?
- what does that imply about the timescale over which `j` should summarize residual scatter?

Do **not** pretend Allan deviation alone mechanically determines the `j` window.

Instead, use it as a guide to the relevant timescale range, alongside the direct rolling-window comparisons.

---

## 9. Recommendation matrix (required)

End with a concrete recommendation matrix for the firmware.

At minimum, provide explicit recommendations or “leave as-is” judgments for:

- `j` window length
- `J` lock threshold
- `J` unlock threshold
- `R` lock threshold
- `R` unlock threshold
- fast estimator responsiveness / `fastShift`
- slow estimator responsiveness / `slowShift`
- blend thresholds / `ppsBlendLoPpm` and `ppsBlendHiPpm`
- validator strictness (only if warranted)
- holdover timeout (only if warranted)

For each recommendation, include:

- recommended value or directional recommendation
- confidence level
- evidence from the run
- tradeoff / rationale

If the run is too short or too unsettled to support a confident recommendation on any item, say so explicitly.

---

## 10. Output package

Produce a bundled output package containing:

1. a main markdown report
2. key plots as image files
3. a concise executive summary
4. a machine-readable summary table of recommended parameter values if practical

The markdown report should include:

- a brief top-level conclusion
- startup summary
- settled-segment definition
- descriptive statistics
- visualization commentary
- parameterization analysis
- recommendation matrix
- what further PPS-only run duration or conditions would most improve confidence

---

## 11. Style and discipline

- Be technically rigorous but intuitive.
- Distinguish clearly between:
  - offset
  - drift / wander
  - residual noise
  - rolling scatter metric behavior
- Do not conflate `en`, `eh`, and `j`.
- Do not let startup dominate tuning conclusions.
- Use the settled disciplined-valid segment as the primary basis for recommendations.
- Be explicit about uncertainty.
- Prefer robust statistics over fragile ones.
- Keep plots clean, sparse, and information-dense.

---

## 12. Final questions you must answer

At the end, answer these directly:

1. What is the best estimate of the settled short-term residual timing floor relevant to pendulum timestamping?
2. Does the evidence support the current `j` window, or suggest a shorter or longer one?
3. Are the `J` and `R` thresholds well chosen for clean lock detection without flapping?
4. Do the fast/slow/blend dynamics appear appropriate for tracking MCU drift without chasing PPS noise?
5. Is ISR latency likely to matter materially for pendulum timing in this design?
6. What firmware parameter changes, if any, are recommended now?
7. What additional run length or telemetry would most improve confidence in those recommendations?
