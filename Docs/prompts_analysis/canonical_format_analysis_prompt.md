# CANONICAL Timing Analysis Prompt (Pendulum / Clock Agnostic)

## Objective

Analyze CANONICAL-format timing telemetry emitted by the Arduino Nano Every / Uno R4 timing repositories.

The analysis must be:

- pendulum-agnostic;
- clock-agnostic;
- mechanism-agnostic;
- transparent and reproducible;
- diagnostically useful;
- visually rigorous;
- suitable for both timing metrology and mechanism research.

The system should initially be treated as a generic cyclic timing instrument producing timestamped edge events plus optional PPS and environmental telemetry.

Only after the generic timing analysis is complete may optional interpretation layers infer pendulum semantics such as:

- tick/tock;
- impulse side;
- escape asymmetry;
- Synchronome-style impulse cadence;
- slave dial pulses;
- synthetic PPS harnesses;
- balance-wheel or oscillator behaviour.

---

# Input Files

The analysis should automatically discover and ingest whichever of the following files are present:

| File | Purpose |
|---|---|
| `pcsw.csv` / `_csw.csv` | Canonical swing edge timestamps |
| `pcps.csv` / `_cps.csv` | Canonical PPS timestamps |
| `sts.csv` | Status / disciplining telemetry |
| `uno.csv` | Host-side logging / WiFi / service telemetry |
| `cfg.csv` | Configuration metadata |
| `hdr.csv` | Header/schema metadata |

The analysis must tolerate missing optional files.

Environmental telemetry columns may appear appended to CANONICAL rows, for example:

- temperature_C
- humidity_pct
- pressure_hPa

The analysis should automatically detect and use them where present.

---

# CANONICAL Swing Semantics

Treat CANONICAL swing rows as neutral edge-boundary timestamps.

Do NOT assume pendulum semantics immediately.

Canonical edge fields:

```text
edge0_tcb0
edge1_tcb0
edge2_tcb0
edge3_tcb0
edge4_tcb0
```

The beam-break sensor is HIGH when blocked.

Therefore the neutral decomposition is:

```text
open_A  = edge1 - edge0
block_A = edge2 - edge1
open_B  = edge3 - edge2
block_B = edge4 - edge3
half_A  = edge2 - edge0
half_B  = edge4 - edge2
full    = edge4 - edge0
```

The analysis should initially preserve these neutral names.

Optional interpretation layers may later infer:

- tick/tock;
- impulse side;
- drive phase;
- escape behaviour;
- mechanical asymmetry.

---

# PPS Semantics

When PPS telemetry exists:

- reconstruct PPS timing from CPS rows;
- estimate oscillator scale and drift;
- distinguish carefully between:
  - underlying unadjusted timing;
  - PPS-adjusted timing;
  - derived rolling summaries.

The report must clearly explain:

- the PPS adjustment methodology;
- the smoothing methodology;
- any filtering assumptions;
- lock-state assumptions;
- holdover handling.


## PCPS / CPS Clock Accuracy and Stability Analysis

When a `pcps.csv` / `_cps.csv` file is present, perform a dedicated PPS-referenced clock-quality analysis. This analysis is independent of pendulum/swing mechanics and should be included even when no swing file is present.

### Required PCPS/CPS Inputs

Use the CPS fields available in the file, typically including:

```text
seq
edge_tcb0
gps_status
drop_pps
cap16
latency16
now32
```

The analysis must first determine whether the CPS file has enough information to compute raw oscillator accuracy and stability. At minimum, this requires:

- a PPS edge timestamp such as `edge_tcb0`;
- a sequence field such as `seq`, or enough timestamp continuity to infer consecutive PPS intervals;
- PPS quality fields such as `gps_status` and/or `drop_pps`, where present.

If these fields are missing, state explicitly what can and cannot be computed.

### Raw PPS-Referenced Clock Interval Analysis

For valid consecutive PPS captures, compute the underlying unadjusted oscillator interval error:

```text
pps_interval_cycles = edge_tcb0[i] - edge_tcb0[i-1]
raw_pps_error_cycles = pps_interval_cycles - nominal_cycles_per_second
```

Default `nominal_cycles_per_second`:

```text
16,000,000
```

unless configuration metadata indicates another clock rate, such as 20 MHz. The chosen nominal clock rate must be stated in the report.

Apply conservative default filtering:

- include only rows with `gps_status == 2` where that field exists;
- exclude rows with `drop_pps != 0` where that field exists;
- require consecutive `seq` values where `seq` exists;
- require plausible one-second intervals around the nominal clock rate;
- document all exclusions and row counts.

Report raw PPS interval accuracy and stability using at least:

```text
n
mean
median
std
MAD
robust_sigma = 1.4826 × MAD
min
max
IQR
percentiles
```

Report results in both:

- oscillator cycles; and
- time units, using `seconds = cycles / nominal_cycles_per_second`.

Also report ppm using:

```text
ppm = raw_pps_error_cycles / nominal_cycles_per_second × 1,000,000
```

### Offline PPS-Adjusted Residual Analysis

If CPS contains enough consecutive PPS intervals, compute an offline PPS-adjusted residual series using the prompt’s default smoothing method unless otherwise specified:

```text
31-second trailing median
```

Preferred causal form:

```text
expected_error[i] = median(raw_pps_error_cycles[i-31 : i])
pps_adjusted_residual_cycles[i] = raw_pps_error_cycles[i] - expected_error[i]
```

This uses the previous 31 valid PPS intervals to adjust the current interval. If a centered or inclusive rolling median is used instead, state that explicitly.

Report PPS-adjusted residual accuracy and stability using the same statistics as the raw PPS interval analysis:

```text
n
mean
median
std
MAD
robust_sigma
min
max
IQR
percentiles
```

Again report results in:

- cycles;
- time units;
- ppm-equivalent residual per second.

### Important Provenance Caveat

Clearly distinguish between:

1. **Offline PPS-adjusted residuals reconstructed from PCPS/CPS alone**, using the analysis methodology above; and
2. **Firmware-applied PPS discipliner estimates**, which may require per-second `sts.csv` telemetry such as fast/slow/blended estimates, applied correction, lock state, estimator provenance, holdover state, and transition history.

If `sts.csv` contains only startup/config/header rows and not per-second discipliner telemetry, state that PCPS/CPS is sufficient for an offline PPS-adjusted residual analysis, but not sufficient to exactly reproduce the firmware’s internal applied PPS-adjusted estimate.

### PCPS/CPS Visualizations

When CPS data exists, include concise clock-quality visualizations:

1. Raw PPS interval error vs. time or sequence, in cycles and/or ns.
2. Raw PPS interval error histogram with median and robust sigma annotated.
3. Offline PPS-adjusted residual vs. time or sequence.
4. Offline PPS-adjusted residual histogram with median and robust sigma annotated.
5. Optional `cap16` and `latency16` plots for quantization, ISR, capture-path, and comb-structure diagnostics.
6. Optional Allan deviation for raw and offline PPS-adjusted interval residuals where dataset length permits.

Use the same visualization standards as the rest of the report: labelled axes, units, legends where appropriate, dots for underlying data, lines for derived rolling estimates.

---

# Data Validation and Integrity Checks

Perform strict validation before analysis.

Validate:

- field counts;
- schema consistency;
- malformed rows;
- monotonicity expectations;
- sequence continuity;
- timestamp continuity;
- timer wrap handling;
- duplicate rows;
- NaN/infinite values.

Produce a dedicated data-quality summary including:

- row counts;
- malformed row counts;
- dropped-row counts;
- sequence gaps;
- PPS lock-state distributions;
- GPS status distributions;
- holdover durations;
- missing-data intervals.

Do not silently discard problematic data.

Any filtering or exclusions must be explicitly documented.

---

# Derived Timing Quantities

Compute at minimum:

## Component Intervals

```text
open_A
block_A
open_B
block_B
```

## Half Cycles

```text
half_A
half_B
```

## Full Cycle

```text
full
```

## Symmetry Metrics

Including but not limited to:

```text
half_asymmetry = half_A - half_B
component asymmetries
normalized asymmetries
```

## Frequency Metrics

Where appropriate:

- instantaneous period;
- frequency;
- ppm deviation;
- drift rate;
- Allan deviation.

When no nominal period is supplied:

- infer nominal period robustly from the dataset;
- clearly state the inferred reference value.

---

# Statistical Methodology

Default derived methodology:

- 31-second trailing median;
- MAD;
- robust_sigma = 1.4826 × MAD.

Compute at minimum:

- count;
- mean;
- median;
- min/max;
- standard deviation;
- MAD;
- robust_sigma;
- IQR;
- percentiles.

Be explicit about:

- whether values are underlying unadjusted timing or PPS-adjusted timing;
- which smoothing/filtering methodology is used;
- whether statistics are robust or classical.

Do not present derived quantities ambiguously.

---

# Visualization and Reporting Standards

All visualizations must be:

- Tufte-esque;
- high-information-density;
- low chart junk;
- readable;
- publication quality.

Rules:

- Use colour to distinguish different series clearly.
- Every chart must include:
  - descriptive title;
  - labelled axes;
  - units where applicable;
  - legend/key where appropriate.
- Use underlying observed data as dots/scatter points.
- Use derived summaries/trends as lines.
- Distinguish clearly between:
  - underlying unadjusted timing;
  - PPS-adjusted timing;
  - derived rolling summaries;
  - inferred interpretation layers.
- Never silently clip or hide outliers.
- If filtered/zoomed views are shown:
  - explicitly state filtering criteria.

Avoid:

- excessive chart junk;
- ambiguous axis labels;
- monochrome multi-series charts;
- unlabeled units;
- hidden smoothing assumptions.

---

# Required Terminology

Use:

- “underlying data points” for individual observations;
- “underlying unadjusted timing” for cycle-derived timing before PPS correction;
- “PPS-adjusted timing” for PPS-corrected values;
- “derived series” for rolling medians/trends/fitted lines;
- neutral labels such as:
  - open_A
  - block_A
  - half_A
  - full

Avoid:

- using “raw” ambiguously;
- assigning tick/tock labels prematurely;
- implying PPS-adjusted values are absolute truth.

---

# Required Visualizations

Produce at minimum the following visualizations.

## 1. Full-Cycle Timing Overview

Show:

- underlying full-cycle timing as dots;
- 31-second trailing median as line;
- optional PPS-adjusted overlay;
- optional environmental overlays.

Clearly distinguish:

- underlying unadjusted timing;
- PPS-adjusted timing.

---

## 2. Half-Cycle Comparison

Show:

- half_A;
- half_B;
- rolling medians;
- asymmetry.

Use colours consistently.

---

## 3. Component Decomposition

Show:

- open_A;
- block_A;
- open_B;
- block_B.

Use dots for underlying data and lines for rolling medians.

---

## 4. Residual Histogram

Histogram of residuals relative to rolling median or PPS-adjusted baseline.

Annotate:

- median;
- robust sigma;
- percentiles.

---

## 5. Residual Autocorrelation

Show autocorrelation of:

- full-cycle residuals;
- optionally half-cycle residuals.

Look for:

- periodic structure;
- drive cadence;
- scheduler/service interference;
- thermal cycles.

---

## 6. Spectral / FFT Analysis

Analyze residual structure in the frequency domain.

Highlight:

- dominant frequencies;
- periodic disturbances;
- comb structures;
- environmental periodicity.

---

## 7. Phase-Folded Analysis

Generate phase-folded visualizations where appropriate.

Examples:

- modulo impulse cadence;
- modulo inferred drive cycle;
- modulo environmental cycle.

Use direct phase labels where practical.

---

## 8. PPS Diagnostics

Where CPS/PPS exists:

Show:

- raw PPS interval error from consecutive CPS `edge_tcb0` timestamps;
- offline PPS-adjusted residual using the declared smoothing method, defaulting to the previous 31 valid PPS intervals;
- latency16;
- cap16;
- PPS residuals;
- lock-state transitions where available;
- holdover periods where available.

Include summary tables for both raw PPS interval error and offline PPS-adjusted residual in cycles, time units, and ppm-equivalent units.

Look for:

- comb patterns;
- ISR contention;
- periodic interference;
- quantization structure.

---

## 9. Environmental Correlation Analysis

Where environmental telemetry exists:

Analyze relationships between timing and:

- temperature;
- humidity;
- pressure;
- supply voltage;
- WiFi/network activity;
- logging backlog;
- CPU/service activity.

Use:

- overlays;
- rolling correlation;
- heatmaps;
- lagged correlation where useful.

---

## 10. Allan Deviation

Where dataset length permits:

Compute overlapping Allan deviation for:

- underlying unadjusted timing;
- PPS-adjusted timing.

Interpret:

- short-term noise;
- medium-term drift;
- long-term stability.

---

# Interpretation Layer

Only after the generic timing analysis is complete may the report optionally infer:

- likely tick/tock mapping;
- likely impulse side;
- escape asymmetry;
- drive cadence;
- pendulum amplitude proxies;
- thermal behaviour;
- mechanical instability;
- WiFi/service interference;
- scheduler artifacts;
- quantization effects.

Clearly separate:

- observed facts;
- derived quantities;
- inferred interpretations;
- speculation.

---

# Report Structure

Produce a comprehensive markdown report including:

1. Executive summary
2. Data quality summary
3. Dataset characterization
4. Statistical summaries
5. Visualizations
6. PPS disciplining and PCPS/CPS clock-quality analysis
7. Environmental analysis
8. Residual/spectral analysis
9. Interpretation layer
10. Key findings
11. Open questions / caveats

The report should be concise but technically rigorous.

---

# Engineering Philosophy

The analysis should:

- prioritize transparency over cleverness;
- prefer robust statistics where appropriate;
- preserve provenance;
- avoid hidden assumptions;
- clearly distinguish observed vs derived vs inferred quantities;
- remain useful for both diagnostics and scientific investigation.
