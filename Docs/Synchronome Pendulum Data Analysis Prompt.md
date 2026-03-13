# Synchronome Pendulum Data Analysis Prompt

You are analysing pendulum timing data from a **Synchronome free-pendulum clock**. The goal is to produce a technically rigorous but intuitively readable analysis of the pendulum run, with special attention to half-swing asymmetry, impulse-cycle structure, data hygiene, stability, and any clues about geometry or beam placement.

## Context and assumptions

Treat this as a **Synchronome**, i.e. a pendulum with a periodic impulse mechanism that may produce a repeating multi-swing structure, often visible when phase-folded by the impulse cadence. Unless the data clearly indicates otherwise, assume:

* nominal pendulum period is **2.000000 s per full swing**
* one row is approximately **one full pendulum cycle**
* timing fields may be named with `_us` suffixes even if they are actually in **raw MCU clock-cycle counts**
* if values around a nominal 2-second swing are near **32,000,000**, that strongly suggests **16 MHz cycle counts**, not literal microseconds

## Optional geometry inputs

At the very top of the analysis, allow the following to be specified if known. If they are not provided, proceed anyway and clearly state what could and could not be inferred without them.

* width of the pendulum feature that passes through the beam sensor
* pivot-to-beam distance
* effective pendulum length
* whether the beam is centred exactly at bottom dead centre (BDC) or offset slightly

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

### Normalised timing error channels

Relative to a 2.0 s target full period:

* instantaneous period error in seconds
* period error in ppm
* seconds/day equivalent

If appropriate, also compute demeaned versions of the key channels for visualisation.

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

## 2) Tick–tock asymmetry

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

## 3) Beam-block timing behaviour

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

## 4) Synchronome impulse-cycle / phase-fold analysis

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

## 5) Correlation structure

Compute correlations among the key channels, especially:

* `full_period` vs `half_diff`
* `full_period` vs `tick_block`
* `full_period` vs `tock_block`
* `full_period` vs `block_sum`
* `full_period` vs `tick_half`
* `full_period` vs `tock_half`

Use correlation carefully and interpret in physical terms. Explain whether the period seems more related to:

* overall amplitude/common-mode movement
* left-right asymmetry
* beam-crossing speed/block time
* impulse-cycle phase

## 6) Relaxation / Q-style inference if geometry permits

If the pendulum feature width passing through the beam is known, you may use beam-block time as a local-speed proxy.

If the geometry is sufficiently constrained, attempt a cautious inference about damping or a **Q-like** measure, clearly separating:

* directly measured quantities
* inferred quantities
* model assumptions
* uncertainty / reasons the estimate may only be indicative

If the geometry is insufficient to estimate a trustworthy physical Q, say so explicitly. It is acceptable to produce only an indicative local damping estimate.

If the beam width is known but pivot-to-beam distance / effective length are not, explain what can still be inferred and what cannot.

## Visualisations

Create intuitive charts that make the physics easy to see. Prefer readability over quantity.

### Time-series plots

Include at least:

* full period over time
* demeaned full period over time
* half-cycle asymmetry over time (`half_diff`)
* block-time difference over time (`block_diff`)
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

Also include:

* phase-folded full period by phase modulo 15
* if informative, demeaned phase-fold plots
* Allan deviation plot if computed
* histogram or density plots for key distributions if useful

## Reporting style

Produce both:

1. a concise executive summary
2. a full technical report

The executive summary should state, plainly:

* whether the run was clean
* the average rate error
* whether there is strong tick–tock asymmetry
* whether a 15-swing Synchronome signature is visible
* the most important physical interpretation
* any geometry/Q conclusions and caveats

The full report should:

* describe the data-quality filtering
* show the main summary statistics
* explain the meaning of the combined charts
* interpret the phase-fold results in physical terms
* clearly distinguish evidence from inference

## Deliverables

Wrap all outputs into a downloadable package and also provide immediate feedback in the chat.

The downloadable package should include:

* `report.md`
* `executive_summary.md`
* summary statistics tables in CSV form
* phase-fold summary tables in CSV form
* any derived data products used for Q/damping or correlation analysis
* a `plots/` folder containing all generated figures

At minimum, the `plots/` folder should include files corresponding to:

* `full_period_over_time`
* `demeaned_full_period_over_time`
* `half_diff_over_time`
* `block_diff_over_time`
* `overlay_tick_tock_half`
* `half_sum_and_difference`
* `overlay_tick_tock_block`
* `block_sum_and_difference`
* `phase15_full_period`
* `phase15_half_overlay`
* `phase15_block_overlay`
* `allan_deviation` (if computed)

## Output expectations

In the immediate response, provide:

* dataset size and duration
* quality counts and any startup exclusions
* mean rate error in ppm and s/day
* mean half-swing asymmetry in physically readable units
* whether the 15-swing phase structure is clear
* a short statement about the most likely interpretation

Be explicit, numerically grounded, and careful not to overstate uncertain inferences.
