# Clock and swing analysis

The supported analysis is `pendulum_analysis.suite`. It runs locally, without AI
services, accounts, prompts or network access. It does not read UNO.CSV. It uses
one PCPS timeline and an explicit PCSW event model, rather than the older
canonical-v2 profiles and capture-investigation report contracts.

## Run

```sh
python -m pip install -e .
pendulum-analyze examples/synthetic --out analysis_test/synthetic
# Equivalent, without installing the command:
python -m pendulum_analysis.suite examples/synthetic --out analysis_test/synthetic
```

This checked-in example runs without private recordings. Open
`analysis_test/synthetic/report.html`; its short synthetic span cannot establish
long-term stability or environmental effects. For real data, replace
`examples/synthetic` with your recording directory. `Data/67Days` referenced in
historical investigations is local data, not part of a fresh checkout.

Without `--out`, output goes to the input directory's `analysis_suite/report.html`,
with a Markdown version, CSV tables and `summary.json`. Charts are embedded in the HTML, so copying
`report.html` alone preserves its figures and displayed tables. CSV/JSON download
links still need their companion files; the Markdown report uses external PNGs. `--out PATH` selects a separate output directory.

The command supports `--nominal-hz` (default 16000000), `--swing-period` (default
2 seconds), `--phase-origin` (default sequence origin 0), `--stale-seconds`
(default 3600), and `--export-intervals` for complete gzip-compressed derived
frames. Unknown/old flags fail; there is no legacy configuration translation.

The default report leads with PPS-calibrated pendulum time series, complete
15-swing averages, a mean phase profile, phase evolution, calibrated median
and jitter polar charts (15 full-swing phases and 30 half-swing phases), pendulum frequency
and compact swing-period/environment fit tables. Clock frequency,
stability, validity and compact mean/standard-deviation summaries support these
views. Full CSV statistics remain available. `--diagnostics` adds the previous
raw comparisons, polar/distribution/cancellation plots, detailed PPS tables and
environmental model evaluation. The standalone PPS analysis uses the same
recording assembly layer; its diagnostic calculations retain their existing contract.

`--detail-start-hours H` adds a local calibrated view beginning at observed
elapsed hour H; `--detail-duration-seconds S` sets its duration (default 90).
`--autocorrelation` adds an optional persistence view of complete 15-swing
averages. Detail coordinates belong to each report's PCSW timeline; collection
reports apply the same requested window separately to each assembled group.

`--profile synchronome` is the default for every invocation; `--profile generic`
disables the Synchronome optical-geometry acceptance guards. Their explicit
settings are `--max-blocked-fraction` (default 0.20) and
`--half-period-tolerance` (default 0.25).

## Rotated Pi recordings

Pass a recording collection/session directory, an extracted web export, or
`catalogue.json` to the same command. Completed contiguous segments with an
identical contract and receiver session are assembled automatically, including
gzip CSVs. Each separate session, missing segment or changed contract gets a
separate report under an HTML index; unknown time gaps are not joined. Active,
interrupted and expired segments are listed as exclusions. Source hashes and
row mappings remain in each report summary. Temporary joined CSVs use disk on
the analysis host, and the analysis itself still needs memory for each group.
A single segment also accepts `PCPS.CSV.gz` with matching compressed siblings.

Both this suite and the [standalone PPS suite](PPS_Analysis.md) assemble inputs
before calculating statistics. The standalone suite requires PCPS only; PCSW
remains optional for its swing-association diagnostics.

A receiver session is the Pi acquisition run/recovery identity, with an
independent host monotonic origin. Service startup, serial disconnect/reconnect,
changed or invalid metadata, capture sequence restart/reordering and receiver
queue overflow can start new sessions. File rotation keeps the session identity.
A session change does not necessarily mean that the Nano restarted, but the
current assembly layer always separates sessions. `previous_session` records
predecessor identity only; it does not certify counter continuity or event UTC.

## Inputs and validity

PCPS.CSV is required. Its required columns are `seq`, `edge_tcb0`, `cap16`,
`latency16`, `now32`, `gps_status`, `holdover_age_ms` and `drop_pps`.
PCSW.CSV is optional; its required columns are `seq`, all five `edgeN_tcb0`
timestamps and `drop_ir`, `drop_pps`, `drop_swing`. File-name case is ignored,
but ambiguous case variants fail. The input may be a run directory or its
PCPS.CSV, not a direct PCSW.CSV or a CSW/CPS filename alias. STS.CSV is preserved
as metadata. The nominal frequency is an explicit setting, not silently inferred
from status text.

Source files are never changed or sorted. `source_row` counts CSV records with
the header as 1. Syntax failures, duplicate headers and missing columns fail
the command. Invalid required fields are retained as events, with
parsed field values in the invalid-fields table. Pandas type and missing-value
inference occurs before this export, so it is not a byte-for-byte preservation
of the original CSV text. Invalid sequence values have no phase
assignment. Missing observations are never filled with zero.

Timelines use unsigned modular differences. A sequence gap selects additional
whole counter wraps only if elapsed time agrees with the expected sequence
duration to within 10% of one period. Resets, invalid rows and ambiguous
chronology start new epochs. A forward swing interval shorter than half a
counter wrap retains chronology even if its mechanical duration is abnormal.
Observed elapsed time joins known spans; unknown time between epochs is not UTC.

Clock frequency requires consecutive sequence numbers, locked status at both
endpoints, consistent `(now32-edge) mod 2^32 = latency16` at both endpoints, no
new cumulative PPS drop and a duration within 5% of one nominal second.
Existing historical drop counts do not disqualify every later observation.
The arithmetic identity does not prove physically accurate capture timestamps.

Raw swing eligibility requires valid integers, ordered edges, total duration
strictly inside ±25% of the configured swing period, no new reported drops and consistent
edge4-to-next-edge0 ownership for consecutive sequences. This wide mechanical
bound retains the impulse pattern; there is no global robust outlier deletion.
The first swing is excluded if any initial drop count is nonzero; later swings
are checked for changes in cumulative counts. Duplicate or ambiguous events are
also excluded. All components of a swing use the same population.

For Synchronome, a full period close to two seconds is insufficient evidence of
a valid optical measurement. Either blocked component occupying over 20% of its
own half, or either half departing more than 25% from nominal, flags suspected
loss of optical clearance. Such records are excluded from **both raw and
calibrated** timing, phase and jitter statistics. This catches severely
distorted crossings during swing-down even before a row starts spanning two
physical cycles. These conservative thresholds are configurable and reported;
they are profile-specific validity guards, not a causal diagnosis. Normal rows
after recovery become eligible again without changing their sequence phase.
Every episode is retained in `swing_sensor_runs.csv`; event rows retain both
half durations and blocked fractions. All component durations are available in
the complete frame with `--export-intervals`. The report plots up to eight
optical-geometry episodes. The final 67-day recording
episode is known from the operator to be swing-down with incomplete sensor
clearance; recurring episodes receive the same validity checks.

## The 15/30-bin mapping

For extended swing sequence `s` and fixed origin `o`:

```text
phase15 = (s - o) % 15
tick_phase30 = 2 * phase15
tock_phase30 = 2 * phase15 + 1
```

| Measurement  | Owned interval | Bin          |
| ------------ | -------------- | ------------ |
| Full swing   | edge0 → edge4  | phase15      |
| Tick open    | edge0 → edge1  | tick_phase30 |
| Tick blocked | edge1 → edge2  | tick_phase30 |
| Tick half    | edge0 → edge2  | tick_phase30 |
| Tock open    | edge2 → edge3  | tock_phase30 |
| Tock blocked | edge3 → edge4  | tock_phase30 |
| Tock half    | edge2 → edge4  | tock_phase30 |

The same mapping is used in aggregation, plots and ownership examples. A
missing swing removes its own two halves and never shifts later phase bins.
`seq % 30` on full-swing rows would describe 30 full swings, not the requested
30 half-swings. PCPS sequence and row number are never mechanical phase indices.

Counter sequence wrap is extended before modulo phase; resets have separate
epochs that are never pooled. Phase zero is only the documented sequence
convention until a physical impulse anchor is independently established.

## Clock calibration

Raw durations divide captured cycles by nominal frequency and remain available
as a distinct view. The PPS-calibrated view uses the common PPS package's
`canonical_metrology` timescale: a centred, non-causal local quadratic fit of
raw PPS phase estimates counter frequency across many observations. The default
window is 61 seconds, configurable with `--pps-window-seconds`; it is a default,
not an automatically selected optimum for every oscillator. Estimator settings
and coverage are recorded in report metadata.

PCPS and PCSW align through shared TCB0 hardware timestamps and their observed
ranges. Parser-local epoch numbers are not cross-file identities. Missing PPS
captures are cadence/coverage anomalies and do not by themselves reset the
counter timeline. Counter resets and ambiguous chronology remain boundaries.
Lock, capture validity and calibration coverage are separate from continuity.
Swings crossing unusable PPS coverage are excluded from calibration, with no
fallback to nominal durations in the calibrated view. Calibration can resume
when adequate valid PPS observations return.

The estimator does not declare every observed PPS interval to be exactly one
second. This avoids directly injecting individual PPS interval jitter into
swing periods. It uses raw captures, leaving projection corrections diagnostic.
Unknown whole-wrap offsets or indistinguishable reset histories cannot be
resolved from raw counters alone; ambiguous associations remain unavailable.
Both files must describe the same hardware recording, subject to the alignment
assumptions recorded in the output.

The historical timer-projection normalization remains a separately labelled
PCPS diagnostic. It does not silently modify the reference timestamps used for
swing calibration.

## Pendulum time series

`swing_time_series.png` shows every eligible calibrated full period and a
separate panel of nonoverlapping complete 15-swing averages. A group contains
sequence phases 0 through 14 exactly once, with all source rows consecutive,
within one epoch, and all swings measurement-valid and PPS-calibrated. Its mean
is the sum of its 15 full durations divided by 15; impulse swings contribute
normally. Partial ends, missing observations and exclusions make the whole
group unavailable. Later groups retain their sequence assignment. Lines break
at unavailable or missing groups and epoch boundaries; no smoothing or gap
filling is applied. `swing_cycles.csv` retains group coverage and source IDs.
Period error is expressed in microseconds relative to the configured nominal
period; the individual and average panels use separate vertical scales.

Every eligible calibrated individual swing is plotted, without sampling.
Colours identify `(extended sequence - phase_origin) mod 15`, using the same
phase assignment as the statistics, so missing rows never shift later colours.
A discrete colour key shows all 15 phases.

The individual/average period timeline and pendulum frequency timelines use a
display-only rule for isolated extremes: the central 99% range (0.5th to 99.5th
percentiles), with 10% of that span added at each end. Individual swings use the
union of per-phase ranges, preserving the regular impulse bands. Groups with fewer than
20 finite values retain their full range; constant populations get a small
nonzero margin. Bounds are applied only when observations remain off-scale.
Each off-scale point is marked by a black X at its actual time and the upper
or lower chart edge, with counts in the legend. This is a display flag, not a
classification of bad measurements. The original observations remain in all
statistics and exports. `swing_plot_offscale_points.csv` records their actual
values, identifiers, bounds and panel (period panels use µs; frequency panels
use ppm). Phase profiles, heatmaps, polar charts, local component details and
environmental charts retain their full scales.

Available environmental panels use hourly PCSW readings, screened for invalid
and stale values on PCSW's own timeline. This avoids assuming that the PCPS and
PCSW recording starts or parser epochs are identical. The readings provide
context without a fitted correction. Long timing exclusions are shaded red.

Each epoch also has a calibrated mean 15-phase full-period profile and a
phase-versus-time heatmap. The profile includes every eligible calibrated
swing. The heatmap uses only complete 15-swing groups, so every phase has equal
exposure in each time group. It subtracts the equal-phase mean within each time
group; the separate rate timeline retains the overall changes. Empty time
groups remain blank. Hourly, six-hour, daily or weekly groups are selected to
keep about 96 or fewer time groups for the recording. Values, exposure, group
boundaries and the selected width are exported in `swing_phase_evolution.csv`
and recorded in `summary.json`. This is descriptive analysis of observed
patterns; it does not identify physical direction, release timing or cause.

The optional detail chart shows full periods plus the four open/blocked edge
intervals over the requested window. Acquisition-order labels (edge 0 to 1,
1 to 2, 2 to 3 and 3 to 4) avoid assigning physical direction. Lines break at
missing sequences, exclusions and epochs. An unavailable window is labelled.

Optional autocorrelation subtracts each epoch's mean 15-swing period and uses
a biased estimator: each lag's sum of paired products is divided by the full
epoch sum of squared deviations. Pairs must belong to one uninterrupted run;
missing groups are never compressed. At least 20 pairs support a lag, up to
120 cycles (about one hour at the nominal period). Lag seconds use observed
mean separation. Drift remains included, with no significance bounds or claim
of a physical cause. Constant or insufficient series are unavailable. The
values and pair counts are exported as `swing_autocorrelation.csv` when requested.

## Complete-cycle frequency and scatter

The default `swing_eN_frequency.png` plots pendulum frequency derived as the
reciprocal of each complete 15-swing mean period. Its offset in ppm is
`(nominal_period / mean_period - 1) * 1e6`; the epoch mean offset is subtracted
for display. This is pendulum rate, distinct from the measured capture-clock
frequency. Longer periods mean lower frequency. Drift is retained and lines
break at unavailable groups.

A second panel shows a four-hour window centred in observed time between the
first and last complete eligible groups in each epoch. If that span is shorter
than four hours, the panel shows the entire eligible span and labels its actual
duration. This reduces the emphasis on possible initial settling in longer
recordings without selecting for low scatter or avoiding outliers. Gaps remain
gaps; the window is centred in time, not by row or group count. The optional
`--detail-start-hours` setting controls only the separate local component view.
This provides a view comparable to the four-hour frequency chart in newsletter
message #2903. The summary table and `swing_frequency_summary.csv` report peak-to-peak
period variation and RMS deviation from the mean, for both the full epoch and
the selected window. RMS divides by the number of groups, rather than using
sample-standard-deviation normalization. A separate RMS removes only a
least-squares straight line in observed elapsed time, addressing the critique
that drift inflates the overall RMS. This retains nonlinear drift and
measurement noise; it does not estimate pure mechanical noise. The plotted
values are not detrended. All groups receive equal weight, without gap filling.

## Swing period and environmental readings

Default tables fit each of temperature, humidity and pressure separately to
PPS-calibrated period, using only complete 15-swing groups. For each channel,
all 15 sensor readings in a group must be physically valid and pass the existing
retrospective stale-value screen. Period and sensor means use exactly the same
groups; no unmatched environmental observations contribute. Each group is
assigned to its midpoint hour on the PCSW timeline. Hourly period and sensor
means weight complete groups equally. Hours require at least 30 nominal
minutes of eligible groups; fits require at least 24 eligible hours in one
epoch. No epochs are pooled or readings interpolated.

Separate single-sensor least-squares fits have an intercept and report slope
(µs per °C, percentage point RH or hPa), full-run R², hourly/group coverage,
sensor range and observed elapsed span. These are descriptive associations:
shared time trends or correlation among sensors can create a fit, and the
single-sensor slopes are not independent environmental effects. The swing
periods are not environmentally corrected. Independent-sample p-values are
omitted because hourly values can remain serially correlated.

A chronological 70/30 check trains on the earliest eligible hours and predicts
the latest, requiring at least 24 training and 12 test hours. It compares test
RMSE against the mean period of the same training population. Both use only
training data. Lower RMSE is better; a fit that loses to this constant baseline
has not transferred to later hours. Full coefficients, paired hourly values,
coverage and validation are exported in `swing_environment_fits.csv`,
`swing_environment_hourly.csv` and `swing_environment_validation.csv`.
Unavailable/constant sensors and insufficient coverage are labelled explicitly.

## Summaries and optional distribution diagnostics

The default `swing_eN_pps_calibrated_phase_polars.png` places full-swing
(15-phase) and half-swing (30-phase) medians side by side, with matching jitter
charts below them, per epoch. Jitter is the sample standard deviation of
individual eligible durations within each phase; slow drift remains included.
All four charts retain full scales, including extreme phase values. Full-swing bars are
deviations from the pooled full-swing median. Half-swing bars use separate
pooled half A and half B medians, preserving within-half patterns despite a
large offset between halves. References are printed in milliseconds; bars are
in microseconds. Half A/B labels refer to acquisition order, not travel
direction. Missing statistics are marked with a cross, including jitter for
bins with fewer than two events. No mean panel, raw comparison or duplicate
robust-spread chart is added to the default.

Each eligible event is formed before statistics are calculated. In particular,
the median full duration is not the sum of component medians. Every expected
phase bin appears, including empty bins, with record, eligible and excluded counts.

The HTML and Markdown reports also show full-swing, half A/B, open and
blocked duration summary tables for every epoch, with separate raw and
PPS-calibrated populations in diagnostic mode. The default shows calibrated
count, mean and sample standard deviation. Diagnostic rows show count, mean,
median, sample standard deviation, robust spread (1.4826 × MAD), 5th and 95th
percentiles, minimum and maximum. All duration statistics are in seconds;
spread includes slow drift and phase structure. Empty populations are labelled
unavailable, as is sample standard deviation for a single event. The existing
`swing_component_summary.csv` retains full precision and gains `p05` and `p95`.
Full-precision statistics remain in the CSV exports.

With `--diagnostics`, both raw and PPS-calibrated views provide:

- 15-bin full-swing mean and median polar bars;
- 30-bin half-swing, open and blocked component mean/median polar bars;
- corresponding standard deviation and scaled-MAD polar bars;
- 15-bin full-swing and 30-bin half-swing box-and-whisker plots;
- paired tick/tock cancellation density plots;
- full component statistics in the 15-bin table, including tick and tock;
- exposure, weekly pattern and minute/hour/day period summaries.

Mean/median polar charts show signed deviations from a fixed pooled median,
using the same scale in the paired charts. Half-swing and component charts use
separate pooled tick/tock references, printed below the chart. Grey rings mark
zero, inward/outward bars indicate shorter/longer durations, and absolute means
and medians remain in the CSV. This retains phase structure while avoiding
dominance by the constant directional asymmetry. Blue/even bins are tick;
orange/odd bins are tock. Empty bins are marked, not plotted as zero values.

Standard deviation is centred on the bin mean; scaled MAD is 1.4826 times the
median absolute deviation from the bin median. Raw jitter includes slow drift.
No per-phase baseline is subtracted before displaying the mean/median pattern.
The separate weekly persistence heatmap removes each week's directional centre
and explicitly identifies that transformation.

Box-and-whisker charts use individual eligible durations in milliseconds. Boxes
span Q1–Q3, the line is the median, whiskers reach the furthest sample within
1.5 × IQR, and dots show all outliers. Missing phase bins are marked with a cross;
they do not shift later events. Tick and tock alternate in the 30-bin chart.

Cancellation charts pair the tick and tock halves from the same swing. One panel
subtracts each direction's epoch mean; the other subtracts each direction's
phase-bin mean. Both retain all finite eligible pairs and apply no time
detrending. Negative correlation indicates opposing half-swing deviations;
the dashed line shows exact cancellation in their sum. Each panel reports the
sample standard deviations of both halves and their sum, plus the hypothetical
independent-halves spread `sqrt(σ_tick² + σ_tock²)`. These are descriptive
statistics, not evidence of causation or a confidence interval. Constant residuals
have unavailable correlation, and fewer than two eligible pairs produce no
cancellation chart. Singleton phase bins contribute zero within-phase residuals.

Raw and PPS-calibrated populations and epochs remain separate. The report's
data links include `plots/half_cancellation_statistics.csv`, which retains headers
even when no eligible pairs are available. Existing polar charts, summaries and
exports are unchanged.

## Clock phase, stability and environment

The default HTML and Markdown reports include compact PPS data quality,
GPS state/holdover and mean/standard-deviation frequency summaries, plus
classified event counts. Detailed diagnostics with `--diagnostics` include
PPS summary tables for data quality,
the sequential interval-filter counts, GPS state counts and lock/holdover health,
raw and diagnostic-normalized timing distributions, capture/ISR latency and
cap16 phase coverage. Matching `clock_*` CSV exports retain full precision,
population definitions, standard deviation, MAD, scaled MAD, IQR and percentiles.
Interval statistics use the same eligibility mask as the clock analysis;
capture latency and phase coverage use all records with valid required fields.
The optional `pps_jitter_ticks` channel is labelled unavailable when absent.

Latency event tables use the existing historical threshold of strictly more
than 485 cycles; the report shows the eight largest and exports all of them.
Large-latency runs stop at sequence gaps and epoch boundaries. cap16 coverage
uses 256 equal bins across the full 16-bit timer, with a circular empty-gap
measure and descriptive uniform-bin ratios. These capture-system diagnostics
are not pendulum-performance scores or statistical significance tests.

The default clock plots show measured frequency against PPS and the raw
gap-aware Allan deviation. The clock's environmental fits, accumulated phase
and diagnostic-normalized stability comparison are optional report diagnostics.
Minute/hour/day clock summaries include coverage and frequency spread.
Accumulated phase resets after every excluded interval, with separate nominal
frequency and retrospective constant-calibration curves. It is not the actual
firmware disciplined-clock output.

Overlapping Allan deviation compares adjacent m-sample averages separated by m
seconds. Every contributing 2m window must be valid and within one epoch. Gaps
are never compressed, and no rolling-median detrending changes the raw result.
Each point needs at least 20 disjoint supporting pairs. Approximate 95% intervals
resample squared-difference blocks of at least one day or 4τ, requiring eight
blocks. They preserve within-block dependence but are not formal metrological
confidence guarantees. Raw and diagnostic-normalized curves remain distinct.

Environmental channels are checked separately. An unchanged recorded run lasting
at least `stale_seconds`, with enough observations, is retrospectively marked
stale from its first unchanged reading. Invalid/stale values are excluded from
fits and retained visibly for diagnosis. This is a screening rule, not direct
sensor-age telemetry; a genuinely constant signal could also be flagged.

Environmental validity checks and clock models use optional channels on PCPS;
environmental columns present only on PCSW do not feed these models.

Hourly temperature models require at least 1800 matched samples. Candidate
models are constant frequency, linear temperature, quadratic temperature,
temperature plus previous-hour temperature, and temperature plus humidity.
The history model never uses future temperature or crosses hourly gaps/epochs.
Holdouts train on the first half or first three quarters, then test later hours.
Each constant baseline uses exactly its candidate's matching population.
Temperature slope intervals resample entire days. Weekly slopes and daily
frequency changes are exported. Environmental associations are not causal
claims or calibrated oscillator-temperature coefficients. Pressure is not
automatically fitted when its useful span may be much shorter than the run.

## Outputs and validation

`summary.json` records settings, counts, exclusions, models, input-file and suite-source hashes,
runtime versions, plot names and completion. `csv/` contains clock aggregates,
phase summaries, stability estimates, environmental validity/models, swing
component/phase/weekly tables and events. `swing_bin_examples.csv` shows original
IDs and component phases at the start of PCSW and around every swing event. All counts
and statistics use the complete input; plots use summaries, not random sampling.

Source hashes cover the suite and shared PPS analysis implementation, not a
firmware binary or every external dependency. Firmware information is retained only
when supplied in STS metadata. Report generation stages files before copying
them into the output directory; reusing a directory overwrites matching names
but does not delete obsolete files from previous runs. Use a fresh `--out`
directory when changing inputs or settings to keep the output set unambiguous.

```sh
python -m pytest tests/test_suite.py
python -m pytest tests/test_recording_collection.py
python -m pytest tests/test_swing_time_series.py tests/test_swing_diagnostic_plots.py
python -m pytest tests/test_swing_environment_fits.py
python -m pytest tests/test_swing_plot_limits.py
```

Focused tests cover missing rows, sequence/counter wraps, resets, component
ownership, tick/tock parity, empty bins, medians of complete events, cumulative
drops, paired lock status, invalid clock coverage, gap-aware Allan deviation,
sensor staleness, forward-only fitting and report generation. The older analysis
modules and their explicitly historical entry points remain regression references;
the supported `pendulum-analyze` command runs this suite only. Install the root
test extra for these checks. See [development and validation](Development_and_Validation.md)
for environment creation, all test entry points and whole-repository dependencies;
an unrestricted pytest run also collects Pi tests requiring Python 3.11+ and
the Pi package's dependencies.
