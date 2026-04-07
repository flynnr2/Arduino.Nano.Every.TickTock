# Synchronome Pendulum Data Analysis Prompt

You are analysing pendulum timing data from a **Synchronome free-pendulum clock**. The goal is to produce a technically rigorous but intuitively readable analysis of the pendulum run, with special attention to half-swing asymmetry, impulse-cycle structure, data hygiene, stability, **jitter**, phase-conditioned tick/tock jitter localisation, environmental coupling, and cautious physical inference about damping, **purity (P)**, and **quality (Q)**.

## Context and assumptions

Treat this as a **Synchronome**, i.e. a pendulum with a periodic impulse mechanism that may produce a repeating multi-swing structure, often visible when phase-folded by the impulse cadence. Unless the data clearly indicates otherwise, assume:

* nominal pendulum period is **2.000000 s per full swing**
* one row is approximately **one full pendulum cycle**
* timing fields may be named with `_us` suffixes even if they are actually in **raw MCU clock-cycle counts**
* if values around a nominal 2-second swing are near **32,000,000**, that strongly suggests **16 MHz cycle counts**, not literal microseconds

## Optional geometry and physical inputs

At the very top of the analysis, allow the following to be specified if known. If they are not provided, proceed anyway and clearly state what could and could not be inferred without them.

* width of the pendulum feature that passes through the beam sensor: **2.52 mm feature through a 1.27 mm slit**
* pivot-to-beam distance: **957 mm + 24 mm + 45 mm = 1242 mm** 
* effective pendulum length: **infer from data unless directly supplied, but should be close to 957 mm + (240 mm / 2) = 1077 mm (which seems a little long vs. theory)**
* whether the beam is centred exactly at bottom dead centre (BDC) or offset slightly: **very close (O(100) F_CPU clock cycles), see data file**
* pendulum weight: **6.94 kg**
* any known impulse cadence (default working hypothesis: **15 swings**)
* any known local environmental channels (temperature, humidity, pressure, etc.)

Also note that a small BDC offset may sometimes be inferred from systematic differences between `tick_block` and `tock_block`, though this should be treated as an inference rather than a directly measured fact.

## Data hygiene and initial characterisation

Before doing any interpretation, perform and report a clear data-quality pass.

### Structural checks

* identify all columns and infer likely units
* determine whether timing columns are in microseconds or raw clock counts
* compute row count and approximate elapsed run time
* check for missing rows, duplicated rows, NaNs, impossible values, negative values where not expected, or obviously corrupt records

### Quality segmentation

Report:

* total row count
* count by `gps_status` value
* count of `dropped` values
* count of rows satisfying the Tier A filter: `gps_status == 2` and `dropped == 0`
* number of transitions into and out of `gps_status == 2`
* startup period length before the run becomes clean/locked

If outliers exist, report them explicitly and distinguish between:

* startup/transitional artefacts
* persistent measurement anomalies
* likely physical excursions

### Recommended baseline filter

Unless there is a reason not to, use a primary clean-analysis subset of:

* `gps_status == 2`
* `dropped == 0`

If further outlier rejection is needed, do it transparently and conservatively. Keep a record of:

* rows removed
* criterion used
* percentage removed

## Core derived channels

Construct at least the following derived series.

If the raw columns are cycle counts, keep the base analysis in those native units and only convert to seconds/ms/us for presentation where helpful.

### Fundamental timing channels

* `full_period = tick_block + tick + tock_block + tock`
* `tick_half = tick_block + tick`
* `tock_half = tock_block + tock`
* `half_diff = tick_half - tock_half`
* `half_sum = tick_half + tock_half`
* `block_diff = tick_block - tock_block`
* `block_sum = tick_block + tock_block`
* `swing_time = tick + tock`
* `block_time = tick_block + tock_block`

### Normalised timing error channels

Relative to a 2.0 s target full period:

* instantaneous period error in seconds
* period error in ppm
* seconds/day equivalent
* fractional frequency `y = -(full_period - target_period) / target_period`

If appropriate, also compute demeaned or detrended versions of the key channels for visualisation, jitter analysis, and correlation work.

Also derive residual forms useful for jitter estimation, for example:

* `full_period_resid_phase15 = full_period - phase15_mean(phase)`
* `full_period_resid_slow = full_period - rolling_median(full_period, slow_window)`
* `full_period_resid_phase15_slow = full_period - phase15_mean(phase) - rolling_median(full_period, slow_window)`

Use clear names and state exactly what has been removed from each residual series.

## Main analyses

## 1) Overall rate and stability

Report on the clean subset:

* mean full period
* median full period
* standard deviation
* min/max
* ppm error versus 2.0 s
* seconds/day equivalent

Assess drift over time using rolling summaries. Use robust summaries where appropriate.

Also compute **overlapping Allan deviation** on fractional frequency if feasible, using the clean subset, and comment on what it suggests about short- vs long-timescale stability.

## 2) Jitter analysis — required and explicit

Estimate jitter in several complementary ways, because “jitter” can mean different things depending on whether slow drift and Synchronome phase structure are included or removed.

At minimum, report the following for `full_period`, and where useful also for `tick_half`, `tock_half`, `block_time`, and `half_diff`:

### Jitter metrics to compute

1. **Raw cycle-to-cycle scatter**
   * standard deviation of the clean series
   * robust alternative such as MAD × 1.4826 or IQR / 1.349

2. **Adjacent-cycle jitter**
   * compute `diff(full_period)` and estimate cycle-to-cycle jitter as `std(diff(full_period)) / sqrt(2)`
   * also report a robust version using MAD if outliers remain

3. **Residual jitter after removing the 15-swing structure**
   * subtract the phase-fold mean by phase modulo 15 (or the best-supported cadence if not 15)
   * compute standard deviation / robust scatter of the residuals

4. **Residual jitter after removing slow drift**
   * subtract a slow rolling baseline (default about 30 minutes unless the run is short)
   * compute standard deviation / robust scatter of the residuals

5. **Residual jitter after removing both phase structure and slow drift**
   * this is often the best estimate of the remaining short-timescale timing noise
   * report it prominently

6. **Short-timescale stability from Allan deviation**
   * comment on the Allan deviation at the shortest few tau values and where it reaches a minimum
   * interpret this as stability, not a direct synonym for point-to-point jitter

### Jitter reporting requirements

For each jitter metric:

* state exactly what was removed before the metric was computed
* give the result in both native units and a physically readable unit (µs or ms)
* say whether the value reflects:
  * gross variability
  * cycle-to-cycle noise
  * residual noise after removing deterministic structure
  * averaging-time-dependent stability

Explicitly distinguish:

* **raw variability** from **true short-timescale jitter**
* **deterministic 15-swing structure** from **random noise**
* **slow drift** from **cycle-to-cycle timing noise**

Where possible, identify which of the following dominates the observed raw scatter:

* deterministic Synchronome impulse-cycle structure
* persistent tick–tock asymmetry
* slow drift / environmental co-drift
* remaining local random timing noise

## 2A) Phase-conditioned jitter drill-down by impulse phase

To help separate a true mechanism-linked impulse-side noise source from a flat electronics or timer floor, perform a **phase-conditioned residual-jitter analysis** on the 15-swing cycle (or the best-supported cadence if not 15).

### Required method

Using the clean subset:

1. assign each row a phase modulo 15
2. for each key timing channel, subtract that channel’s own phase-fold mean or median template by phase
3. subtract a slow rolling baseline (default about **30 minutes**, preferably a centred rolling median)
4. compute the residual short-timescale jitter **separately for each phase 0..14**

This should be done at minimum for:

* `full_period`
* `tick_half`
* `tock_half`

If feasible, also do it for:

* `tick`
* `tock`
* `tick_block`
* `tock_block`
* `block_time`
* `half_diff`

### Required phase-conditioned metrics

For each phase 0..14 and each analysed channel, report at least:

* phase median or mean template offset
* residual RMS jitter after removing phase template and slow drift
* robust residual jitter (MAD × 1.4826 or similar)
* if useful, same-phase repeatability, e.g. `std(diff(x_phase_series)) / sqrt(2)` computed on successive observations of the same phase

Present these in a markdown table and save the numeric results as CSV.

### Required interpretation

Explicitly comment on whether:

* residual jitter is roughly uniform by phase, suggesting a more phase-independent floor
* one or two phases stand out, suggesting **impulse-phase-specific** variability
* the phases with the largest template offsets are also the least repeatable
* the hotspot aligns more with `tick_half` or `tock_half`, or with the non-block vs block segments

Be careful with interpretation: because a measured full period can straddle the exact instant of impulse, a jitter hotspot may appear on the impulse phase itself, the adjacent phase, or be split across both. State this explicitly.

## 2B) Tick-versus-tock residual-jitter localisation — required

Because the Synchronome mechanical impulse occurs **once every 15 swings and only on one side of the beat**, perform a dedicated drill-down to identify whether the impulse-linked jitter source sits more clearly in the **tick** side or the **tock** side for this run.

The side may depend on startup alignment, so do not assume in advance whether the impulse lands on tick or tock. Infer it from the data.

### Required method

For `tick` and `tock` individually, and preferably also for `tick_half` and `tock_half`:

1. phase-fold by modulo 15
2. remove each segment’s own phase template
3. remove a slow rolling baseline
4. compute residual RMS jitter by phase

### Required outputs

Report, at minimum:

* a table of phase 0..14 with `tick` residual RMS jitter and `tock` residual RMS jitter in native units and µs
* the quiet-phase baseline for each side if a clear baseline exists
* the hottest phase(s) for `tick`
* the hottest phase(s) for `tock`
* the ratio or excess jitter of hotspot versus quiet baseline

Also state clearly whether the run appears to show:

* **tick-side impulse localisation**
* **tock-side impulse localisation**
* ambiguous or split localisation

### Interpretation guidance

Use this drill-down to reason about RMS jitter sources. In particular, discuss whether the strongest residual-jitter hotspot is more consistent with:

* mechanical impulse/release variability
* adjacent-swing recovery effects
* block/sensor geometry effects
* a broad electronics/firmware timing floor

If one side shows a clear phase-localised hotspot while the other side remains near baseline, say that this materially strengthens the case for a **mechanism-linked impulse-side jitter source** rather than a uniform timer-chain floor.

## 3) Tick–tock asymmetry

This is a key analysis.

Quantify and interpret:

* mean and median `tick_half`
* mean and median `tock_half`
* mean and median `half_diff`
* standard deviation / IQR of `half_diff`
* whether asymmetry is persistent, drifting, or impulse-phase-dependent

Discuss the likely physical meaning. For example:

* geometric asymmetry
* sensor placement offset from BDC
* escapement/impulse asymmetry
* differences in the two sides of motion

Do not overclaim, but explain the likely interpretations clearly.

## 4) Beam-block timing behaviour

Analyse:

* `tick_block`
* `tock_block`
* `block_sum`
* `block_diff`

Comment on:

* whether `tick_block` and `tock_block` move together over time
* whether they carry temperature/amplitude information
* whether their difference suggests beam offset from BDC
* whether block-time variation participates in the impulse-cycle structure

## 5) Synchronome impulse-cycle / phase-fold analysis

This is especially important.

A Synchronome often shows a repeating signature tied to the impulse cadence. Phase-fold the data by plausible cycle lengths, especially **15 swings** unless the data strongly suggests another cadence.

At minimum:

* phase-fold `full_period` by phase modulo 15
* phase-fold `tick_half` and `tock_half` by phase modulo 15
* phase-fold `tick_block` and `tock_block` by phase modulo 15
* identify which phases are longest/shortest and by how much

If a phase signature is clear, describe:

* its amplitude
* whether it is concentrated more in `tick_half` or `tock_half`
* whether it is concentrated in the block segments or the non-block segments
* whether the effect looks impulse-linked, recovery-linked, or broadly distributed

If useful, also test a few neighbouring fold lengths, but keep 15 as the primary presentation if that is the evident cycle.

## 6) Correlation structure — required and explicit

Compute a **correlation matrix for each main part of the beat and all available environmental factors**. Include both the pendulum timing channels and the environmental channels in the same correlation workup.

### Channels to include if present

Timing channels:

* `tick`
* `tock`
* `tick_block`
* `tock_block`
* `tick_half`
* `tock_half`
* `full_period`
* `half_diff`
* `half_sum`
* `block_diff`
* `block_sum`
* `swing_time`
* `block_time`
* period error in ppm

Environmental channels if present:

* temperature
* humidity
* pressure
* any other logged environmental or system-state channels that plausibly matter

### Correlation methodology

Do **not** treat simple global de-meaning as a separate analysis, because Pearson correlation already subtracts the global mean. Instead, compute and report at least these two versions:

1. **Raw Pearson correlation matrix** on the clean subset
   * this captures overall co-variation across the run, including shared drift

2. **Locally detrended Pearson correlation matrix**
   * subtract a robust rolling baseline (preferably a centred rolling median; if not practical, use a rolling mean and say so)
   * default window: about **30 minutes**, unless the run is too short for that to make sense
   * this is intended to suppress slow shared drift and reveal local co-movement

If useful, also provide:

3. **Spearman rank correlation**
   * especially if relationships look monotonic but non-linear, or outliers remain a concern

### Correlation reporting requirements

Report and interpret:

* the **full raw matrix**
* the **full locally detrended matrix**
* a smaller **environment-vs-beat slice** for readability
* which correlations are strong enough to matter physically
* which apparent correlations largely disappear after detrending

Explain the distinction clearly:

* **raw correlation** answers: “what drifts together over the run?”
* **locally detrended correlation** answers: “what moves together around the local baseline?”

Use correlation carefully and interpret in physical terms. Explain whether the period seems more related to:

* overall amplitude / common-mode movement
* left-right asymmetry
* beam-crossing speed / block time
* impulse-cycle phase
* slow environmental drift versus local environmental forcing

Be explicit that if a strong raw environmental correlation collapses after local detrending, that suggests a **co-trending / shared drift story**, not strong evidence of direct short-timescale causation.

## 7) Relaxation, damping, Purity (P), and Quality (Q)

Use the leapsecond.com **Purity / Quality** framework cautiously if the data and geometry support it.

### Definitions to use

Use the following conceptual definitions:

* **Q (quality)** = `E / ΔE`
  * energy stored divided by energy lost per cycle
  * this is an efficiency / dissipation measure

* **P (purity)** = `ΔE / σE`
  * energy loss per cycle divided by the cycle-to-cycle scatter in pendulum energy
  * this is an energy-consistency measure

* therefore **stability is approximately proportional to `1 / (P * Q)`**, which is algebraically equivalent to `σE / E`

Be clear that this is a **clock-accuracy / pendulum-metrology heuristic framework**, not a licence to overclaim a precise physical Q from incomplete geometry.

### Practical estimation approach

Where possible, use beam-block time as a **local speed proxy** near BDC.

If geometry is sufficiently constrained:

1. estimate local bob speed from beam-crossing time and effective blocked width
2. use that as a proxy for near-BDC speed
3. use speed squared as a proxy for kinetic energy, hence pendulum energy up to a scale factor
4. derive a relative energy time series `E_rel`
5. from the energy decay / recovery structure, estimate:
   * representative fractional loss per cycle `ΔE / E`
   * therefore a **Q-like estimate** `Q ≈ E / ΔE ≈ 1 / (ΔE / E)`
6. estimate `σE / E` from the scatter of `E_rel`
7. estimate **P** from `P = ΔE / σE`
8. check whether the implied `1/(P*Q)` is numerically consistent with `σE/E`

### Required caution and model hygiene

Clearly separate:

* directly measured quantities
* inferred quantities
* model assumptions
* uncertainty / reasons the estimate may only be indicative

Address explicitly:

* the beam is near BDC but not necessarily exactly at BDC
* the blocked width is a feature-plus-slit effective geometry, not an infinitesimal point measurement
* local speed inferred from beam-cross time is not the same as exact maximum bob speed unless the geometry justifies that approximation
* Synchronome impulse behaviour may violate a simple free-decay model over the full cycle
* if energy injection is phase-localised, discuss whether Q/P should be treated as **local / indicative / effective** rather than universal constants

### Preferred output for P and Q

Produce, where justified:

* a **Q-like** estimate from relative cycle loss
* a **P-like** estimate from loss magnitude relative to cycle-to-cycle energy scatter
* a short explanation of what those numbers mean physically
* a statement of confidence level: e.g. **illustrative only**, **indicative**, or **reasonably constrained**

If the geometry is insufficient to estimate a trustworthy physical Q or P, say so explicitly. It is acceptable to produce only:

* an indicative local damping estimate
* an energy-proxy stability estimate
* a statement that full P/Q estimation is too assumption-sensitive for the available data

If the beam width is known but pivot-to-beam distance / effective length are not, explain what can still be inferred and what cannot.

## Visualisations

Create intuitive charts that make the physics easy to see. Prefer readability over quantity.

### Chart-formatting requirements — mandatory

Every chart must be presentation-ready and self-explanatory.

For **every** figure:

* include a clear, descriptive title
* label the **x-axis** and **y-axis**
* include **units on axis labels** wherever possible, e.g. `Time (h)`, `Period error (µs)`, `Phase mod 15`, `Temperature (°C)`, `Pressure (hPa)`, `Allan deviation`
* include a **legend** whenever more than one series, smoothing line, fit, band, or reference line appears
* make the legend text explicit enough that a reader can tell what is plotted, e.g. `tick_half raw`, `tick_half rolling median (301 cycles)`, `phase-15 mean`, `target = 2.000000 s`
* if colour is used, choose colours that are distinguishable and do not rely on colour alone when line style or marker style can help
* if heavy smoothing, detrending, or residualisation is shown, say so in the title or legend
* where helpful, add a light grid and use readable tick formatting
* do not leave axes unlabeled or legends implicit

If a figure contains only a single series and no overlays, a legend is optional, but axis labels remain mandatory.

Whenever a series is shown in native cycle units, make that explicit on the axis label or in the title. Prefer physically readable units for presentation unless native units are important to the point being made.

### Time-series plots

Include at least:

* full period over time
* demeaned full period over time
* half-cycle asymmetry over time (`half_diff`)
* block-time difference over time (`block_diff`)
* one or more residual-jitter series over time, especially the residual after removing phase-15 structure and slow drift
* rolling summaries where appropriate

### Combined / overlaid charts

These are required because they are often more intuitive than separate charts.

1. **Overlay chart:** `tick_half` and `tock_half` on the same axes
   * show raw series lightly if dense
   * show a smoothed or rolling-median version prominently
   * make the persistent offset and common-mode drift easy to see

2. **Half-sum / half-difference chart**
   * plot `half_sum`
   * plot `half_diff`
   * this should visually separate common-mode motion from asymmetry

3. **Overlay chart:** `tick_block` and `tock_block` on the same axes
   * again, raw faint / smooth prominent if useful
   * make it obvious whether they move together or diverge

4. **Block-sum / block-difference chart**
   * plot `block_sum`
   * plot `block_diff`
   * use this to separate common-mode beam-speed movement from BDC-offset/asymmetry information

### Phase-folded charts

These are also required.

5. **Phase-15 half overlay**
   * mean `tick_half` by phase 0..14
   * mean `tock_half` by phase 0..14
   * overlaid on the same chart
   * this should reveal whether the impulse-cycle structure sits more on one side

6. **Phase-15 block overlay**
   * mean `tick_block` by phase 0..14
   * mean `tock_block` by phase 0..14
   * overlaid on the same chart

7. **Phase-conditioned residual-jitter by phase**
   * bar chart or line chart of residual RMS jitter versus phase 0..14
   * at minimum for `full_period`, and preferably also `tick_half` and `tock_half`
   * title and legend should make clear that the phase template and slow drift have been removed

8. **Tick-versus-tock residual-jitter localisation chart**
   * overlaid line chart or heatmap showing `tick` residual RMS jitter by phase and `tock` residual RMS jitter by phase
   * this is specifically to identify which side of the beat carries the impulse-linked noise hotspot

9. **Residual distribution by phase**
   * boxplot, violin plot, or similar of residuals by phase for at least one key channel
   * use this to show whether the hotspot is due to broader scatter, skew, or occasional outliers

### Correlation plots

10. **Raw correlation heatmap**
   * include timing channels and environmental channels together if feasible

11. **Locally detrended correlation heatmap**
   * same variables, after rolling-baseline subtraction

12. **Environment-vs-beat heatmap or compact table**
   * make the most decision-useful slice easy to read

### P/Q and damping plots

If P/Q analysis is attempted, include as appropriate:

13. **Energy-proxy over time**
14. **Energy-proxy histogram or density**
15. **Energy-proxy versus phase modulo 15**
16. **Any fitted decay / loss diagnostic used for Q-like estimation**

Also include:

* phase-folded full period by phase modulo 15
* if informative, demeaned phase-fold plots
* Allan deviation plot if computed
* histogram or density plots for key distributions if useful
* histogram or density plot of at least one key jitter residual
* optional lag-1 scatter or diff-series plot if it helps clarify cycle-to-cycle noise

## Reporting style

Produce both:

1. a concise executive summary
2. a full technical report

The executive summary should state, plainly:

* whether the run was clean
* the average rate error
* the most decision-useful jitter estimates, clearly distinguishing raw scatter from residual short-timescale jitter
* whether there is strong tick–tock asymmetry
* whether a 15-swing Synchronome signature is visible
* the most important physical interpretation
* what the correlation analysis says about drift versus local coupling
* any geometry / damping / P / Q conclusions and caveats

The full report should:

* describe the data-quality filtering
* show the main summary statistics
* explain the meaning of the combined charts
* interpret the phase-fold results in physical terms
* present both raw and detrended correlations, and explain why both matter
* clearly distinguish evidence from inference
* explain any P/Q estimation path and why it is or is not trustworthy

## Deliverables

Wrap all outputs into a downloadable package and also provide immediate feedback in the chat.

The downloadable package should include:

* `report.md`
* `executive_summary.md`
* summary statistics tables in CSV form
* jitter metrics tables in CSV form
* phase-fold summary tables in CSV form
* raw and detrended correlation matrices in CSV form
* environment-vs-beat correlation slices in CSV form
* any derived data products used for jitter analysis
* any derived data products used for Q/damping/P/Q analysis
* a `plots/` folder containing all generated figures

At minimum, the `plots/` folder should include files corresponding to:

* `full_period_over_time`
* `demeaned_full_period_over_time`
* `half_diff_over_time`
* `block_diff_over_time`
* `jitter_residual_over_time`
* `jitter_residual_histogram`
* `overlay_tick_tock_half`
* `half_sum_and_difference`
* `overlay_tick_tock_block`
* `block_sum_and_difference`
* `phase15_full_period`
* `phase15_half_overlay`
* `phase15_block_overlay`
* `phase_conditioned_jitter_by_phase`
* `tick_tock_segment_jitter_localisation`
* `phase_conditioned_residual_distribution`
* `raw_correlation_heatmap`
* `detrended_correlation_heatmap`
* `env_vs_beat_heatmap` (or equivalent compact visual)
* `energy_proxy_over_time` (if computed)
* `energy_proxy_phase15` (if computed)
* `allan_deviation` (if computed)

## Output expectations

In the immediate response, provide:

* dataset size and duration
* quality counts and any startup exclusions
* mean rate error in ppm and s/day
* at least two jitter estimates:
  * one representing raw variability
  * one representing residual short-timescale jitter after removing deterministic structure and slow drift
* phase-conditioned residual-jitter findings, including whether one or two phases are materially noisier than the rest
* tick-versus-tock localisation findings, including which side appears to carry the strongest impulse-linked RMS jitter hotspot
* mean half-swing asymmetry in physically readable units
* whether the 15-swing phase structure is clear
* a short statement about the most likely interpretation
* a short statement about whether environmental correlations are mostly raw-drift effects or remain present after detrending
* a short statement about whether P/Q estimation appears meaningful, indicative only, or too assumption-sensitive

Be explicit, numerically grounded, and careful not to overstate uncertain inferences.
