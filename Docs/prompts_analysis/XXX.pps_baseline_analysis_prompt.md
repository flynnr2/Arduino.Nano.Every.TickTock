# PPS Baseline Telemetry Analysis Prompt

You are analysing a **24+ hour PPS-only baseline run** from the `Nano.Every` firmware.

The goal is to characterize, as cleanly and rigorously as possible, the behavior of the **MCU clock versus GPS PPS** when no pendulum timing is involved, using the dedicated compile-time-gated `PPS_BASE` telemetry stream.

Your analysis should be technically sound, visually restrained, and immediately useful for firmware/hardware decisions.

---

## Primary objective

Use the `PPS_BASE` telemetry to estimate and explain:

- the second-to-second raw MCU-vs-PPS interval error
- the short-term jitter floor visible at the PPS/capture/firmware level
- the longer-term drift of the MCU clock relative to GPS PPS
- how the discipliner behaves over time
- the frequency and character of anomalous PPS classifications
- whether the residuals suggest mostly GPS/PPS noise, MCU clock wander, capture-path jitter, or software/queueing effects

Where appropriate, distinguish clearly between:

- **raw measured interval error**
- **error relative to the discipliner estimates**
- **capture latency / ISR-adjacent timing noise**
- **rare anomalies** vs ordinary jitter

Do not overclaim. Be explicit about what the data can show directly, what it only suggests, and what would require an additional experiment.

---

## Expected inputs

Assume one or more of the following files may be present:

- a text log containing lines such as:
  - `STS,PPS_BASE,...`
  - optionally `STS,PPS_BASE_WIN,...`
- optional companion logs from the same run
- optional environmental data merged by another board (temperature, pressure, humidity, etc.)

The key per-sample fields may include compact names such as:

- sequence counter
- coarse time stamp (`ms` or similar)
- raw PPS interval (`dt32` or similar)
- raw error vs nominal MCU frequency (`err_nom`)
- residuals vs fast / slow / blended estimates (`err_fast`, `err_slow`, `err_hat`)
- PPS classification (`cls`)
- validity flag (`valid`)
- discipliner state (`state`)
- fast / slow / blended frequency estimates (`f_fast`, `f_slow`, `f_hat`)
- signed rate offset metric (`R_ppm` or compact equivalent)
- jitter metric (`J`)
- capture latency metric (`lat16`)
- optional extras such as `cap16`, `cnt`, `gap`, `ovf`, `seeded`, etc.

Do not assume exact field names. First inspect the actual schema present in the file.

---

## Step 1: Parse and document the schema

Before analysing, inspect the file and:

1. identify all distinct `STS` record types present
2. extract the exact field names used in `PPS_BASE`
3. infer field meanings from names and value ranges
4. note any missing or optional fields
5. document units carefully

Important:

- Treat native timing/frequency fields as being in **raw MCU clock ticks / cycles**, unless the file clearly shows otherwise.
- Do not silently convert units without stating that you are doing so.
- If you additionally show converted units (e.g. microseconds or ppm), keep the raw-tick form available somewhere in the analysis.

---

## Step 2: Data hygiene and validation

Perform a careful pre-analysis hygiene pass.

### 2.1 File integrity and parse checks

Report:

- number of total lines
- number of successfully parsed `PPS_BASE` lines
- number of malformed or skipped lines
- whether sequence numbers are contiguous
- whether timestamps are monotonic
- whether there are obvious truncation issues
- whether there are duplicate records

### 2.2 Coverage and duration

Report:

- first and last sequence number
- approximate run duration
- expected number of PPS samples vs observed
- count and percentage of missing seconds if detectable

### 2.3 Classification and state counts

Report counts and percentages for:

- each PPS classification (`OK`, `GAP`, `DUP`, `HARD_GLITCH`, etc., or compact equivalents)
- valid vs invalid samples
- each discipliner state (`FREE_RUN`, `ACQUIRE`, `DISCIPLINED`, `HOLDOVER`, etc.)
- state transition counts and approximate transition times

### 2.4 Sanity ranges

Check for obviously impossible or suspicious values in:

- `dt32`
- `err_nom`
- residual fields
- frequency estimates
- jitter metric
- latency metric

Flag anything implausible, but do not discard data without saying so.

---

## Step 3: Core quantitative analysis

### 3.1 Raw MCU-vs-PPS interval behavior

Analyse the raw PPS interval measurement (`dt32`) and/or `err_nom = dt32 - nominal`.

Report:

- mean
- median
- standard deviation
- MAD / robust scale
- min / max
- p01 / p05 / p50 / p95 / p99
- interquartile range

Interpretation:

- explain what the central tendency says about whether the MCU clock is fast or slow versus GPS PPS
- explain what the spread says about second-to-second jitter/noise
- distinguish ordinary variation from rare outliers

If `F_CPU` is known and relevant, estimate the implied average frequency offset in ppm.

### 3.2 Residuals against discipliner estimates

If `err_fast`, `err_slow`, and/or `err_hat` are present, analyse each.

Report the same robust summary statistics and compare them directly.

Explain:

- whether the blended/applied estimate reduces residual spread relative to raw nominal error
- whether fast and slow estimates behave as expected
- whether residual structure remains after discipline

### 3.3 Frequency estimate behavior over time

If `f_fast`, `f_slow`, and `f_hat` are present, analyse:

- level
- drift
- short-term noise
- convergence / divergence between estimates
- behavior around state transitions

If `R_ppm` is present, give:

- min / max / mean / median / IQR
- p05 / p95
- how often it changes sign
- whether it exhibits temperature-scale wander, discrete steps, or unusual excursions

### 3.4 Jitter metric behavior

If `J` is present, analyse:

- distribution of `J`
- relationship between `J` and classification anomalies
- whether `J` is mostly stable with rare spikes, or persistently broad
- whether `J` tracks time of day or thermal drift if data permits

### 3.5 Capture latency / timing noise

If `lat16` is present, analyse:

- summary statistics
- histogram / ECDF
- time series of latency
- whether latency correlates with raw interval error or residuals
- whether latency spikes explain outliers

If the data suggests firmware/scheduling effects, say so cautiously and explain why.

### 3.6 Stability by averaging time

Estimate stability versus averaging time using an appropriate method such as:

- overlapping Allan deviation on fractional frequency, or
- another defensible stability metric if Allan deviation is not practical

Use this to separate:

- short-term white-ish noise / capture noise
- medium-term wander
- long-term drift

Be explicit about:

- how you constructed fractional frequency from the available fields
- any assumptions made
- the range of averaging times that are actually supported by the run length

### 3.7 Anomaly analysis

Analyse all non-OK / invalid / unusual samples.

For anomalies:

- count each type
- identify when they occur
- check whether they cluster in time
- inspect surrounding samples before and after each anomaly
- show whether anomalies coincide with large latency, state transitions, or estimate jumps

If the anomalies look more like GPS/PPS issues than MCU/firmware issues, say so carefully.

---

## Step 4: Optional environmental coupling

If environmental fields are present or can be merged from companion logs, analyse whether the MCU clock behavior appears coupled to:

- temperature
- humidity
- pressure
- other available environmental variables

Most important is temperature.

Check for:

- correlation of `err_nom` or `R_ppm` with temperature
- lagged relationship if visually or statistically appropriate
- whether frequency drift looks broadly thermal in character

Be cautious: correlation is not causation.

---

## Step 5: Visualisations

Produce a **small number of high-value visualisations** guided by **Tufte’s core principles**:

- maximize data-ink ratio
- avoid chartjunk
- make comparisons easy
- use direct labeling where possible
- prefer position over area/color for quantitative judgment
- keep scales honest
- use small multiples when that improves comparison
- avoid redundant decoration
- every graphic should answer a clear question

### Required visualisations

#### 5.1 Raw interval error over time

A clean time-series plot of raw MCU-vs-PPS error over time:

- y-axis: `err_nom` (raw ticks, and optionally secondary interpretation in ppm or µs if helpful)
- x-axis: elapsed time
- include a thin rolling median / robust smoother if useful
- clearly mark state transitions or anomalies, but keep annotations light

Purpose:
show drift, noise floor, and rare excursions.

#### 5.2 Residual comparison plot

If available, compare `err_nom`, `err_fast`, `err_slow`, and `err_hat` using either:

- aligned small-multiple time series with the same y-scale, or
- a compact overlay only if it remains readable

Purpose:
show whether discipline meaningfully reduces residual spread and structure.

#### 5.3 Distribution plot for raw error and residuals

Use one of:

- ECDFs, or
- clean histograms with restrained binning, or
- compact kernel-free density alternatives

Prefer ECDF if comparison clarity is better.

Purpose:
compare central spread, tails, and outliers without visual clutter.

#### 5.4 Latency / jitter distribution

If `lat16` and/or `J` are present, produce a clean distribution view.

Purpose:
show whether timing noise is tight and well-behaved or has long tails.

#### 5.5 Stability vs averaging time

Plot overlapping Allan deviation (or chosen stability metric) against averaging time on appropriate axes.

Purpose:
show the short-, medium-, and long-term stability regimes.

#### 5.6 Anomaly timeline

If anomalies are rare enough, produce a sparse event timeline or rug plot of anomaly times by class.

Purpose:
show clustering and whether the run is mostly clean with isolated events.

### Optional visualisations

Only include these if genuinely useful:

- scatter of `lat16` vs `err_nom`
- scatter of temperature vs `R_ppm` or `err_nom`
- time-of-day folded view if a diurnal pattern appears credible
- zoomed excerpts around a few representative anomalies
- QQ-style comparison if tail behavior matters

### Visual style requirements

- Prefer white background.
- No unnecessary gridlines; if used, keep them faint.
- No 3D effects.
- No stacked area charts.
- No rainbow palettes.
- No pie charts.
- Do not use color unless it serves a clear comparison purpose.
- If multiple series are compared, use restrained styling and direct labels where possible.
- Keep legends to a minimum; direct labels are often better.
- Use readable axis labels and concise titles.
- If uncertainty or smoothing is shown, explain exactly how it was constructed.

---

## Step 6: Interpretation and diagnosis

After the quantitative analysis, provide a careful interpretation.

Address the following questions directly:

1. **How good does the MCU clock look against PPS over this run?**
2. **What appears to be the short-term jitter floor?**
3. **How much of the observed variation looks like drift/wander rather than instant jitter?**
4. **Does the discipliner appear well behaved?**
5. **Are anomalies rare and isolated, or structured and concerning?**
6. **Does the evidence point more toward MCU oscillator behavior, GPS/PPS noise, capture-path jitter, or firmware scheduling effects?**
7. **What does this imply for the achievable floor of the pendulum timing system?**

Be explicit about confidence level.

Use language such as:

- “directly supported by the data”
- “suggestive but not conclusive”
- “cannot be determined from this run alone”

---

## Step 7: Recommendations for follow-up experiments

Based on the results, recommend the most useful next experiments. These might include:

- a longer PPS-only run
- a PPS-only run at different ambient temperatures
- injecting a synthetic reference signal in place of the IR path
- comparing different MCU clock sources
- logging additional latency/capture counters
- comparing the same run with pendulum path enabled vs disabled
- repeating the run with richer anomaly-focused debug telemetry only around faults

Keep recommendations concrete and prioritized.

---

## Output requirements

Your output should include:

1. a concise executive summary
2. a data hygiene summary
3. schema documentation for the actual fields found
4. core quantitative results in readable tables
5. the requested visualisations
6. a careful interpretation section
7. a short prioritized recommendation list

Also produce downloadable artefacts where possible:

- cleaned parsed CSV or parquet of the PPS baseline stream
- analysis notebook or script
- figures as separate image files
- a markdown report
- optionally a zip bundle containing all outputs

If a dataset is too large, use chunked/streaming methods where appropriate, but do not sacrifice correctness.

---

## Important cautions

- Do not confuse raw tick-level error with discipliner residuals.
- Do not over-interpret single-sample spikes.
- Do not discard anomalies without reporting them.
- Do not smooth away structure that may matter.
- Do not use decorative visualisation choices that obscure quantitative judgment.
- Keep the analysis rigorous, restrained, and useful for engineering decisions.

