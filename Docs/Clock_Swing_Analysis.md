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
`report.html` alone preserves its figures and displayed tables. Long recordings
load individual motion details from companion scripts in the `plots` folder;
keep the complete report folder for interactive detail and CSV/JSON downloads.
The Markdown report uses external PNGs. `--out PATH` selects a separate output directory.

The command supports `--nominal-hz` (default 16000000), `--swing-period` (default
2 seconds), `--phase-origin` (default sequence origin 0), `--stale-seconds`
(default 3600), and `--export-intervals` for complete gzip-compressed derived
frames. Unknown/old flags fail; there is no legacy configuration translation.

### Optional Synchronome impulse alignment

Add `--impulse-alignment` with the Synchronome profile to put the two adjacent
impulse-associated half intervals at half phases 0 and 1, together forming full
phase 0. The default remains the original sequence-based convention. This mode
uses the operator-established positive half-period signature as an impulse
proxy; it does not measure force, contact angles or physical left/right.

```sh
python -m pendulum_analysis.suite RUN --out RUN/impulse_aligned \
    --profile synchronome --impulse-alignment --export-intervals
```

Each capture epoch is assessed independently using eligible PPS-calibrated
halves. A template from the first third of observed ten-minute windows proposes
an anchor. After removing each direction's median, the two largest positive
phase means must be adjacent. Their smaller excess must exceed six background
scales, and its separation from the largest other phase must exceed three.
The scale is the maximum of the background robust spread, the median sampling
standard error and 1 µs. Each phase needs at least ten eligible observations.
At least three windows, covering at least 80% of all observed windows, must
independently confirm the same clear pair. A confident conflicting pair in any
window triggers fallback. Ambiguous windows are retained in the audit and
reported as a warning; they do not change the frozen anchor. Stability checks
never shift individual windows to fit a changing signal.

If a clear, stable anchor is unavailable, the command prints a warning and
continues that epoch with its original `--phase-origin` phases and full periods.
The report prominently labels this fallback. `summary.json`,
`csv/swing_impulse_anchors.csv` and `csv/swing_impulse_stability.csv` record
status, reasons, candidates and window checks. Mixed aligned/fallback epochs
remain separate. Unaligned phase numbers are not mechanically comparable with
aligned phase numbers. Timing and optical-validity exclusions still apply.

An even half-phase anchor rotates the phase labels. An odd anchor reconstructs
each full period as **tock(n) + tick(n+1)**, with its start shifted to the beginning
of tock(n). Reconstruction requires adjacent source rows and extended sequences,
the same capture epoch, a matching shared edge and eligible source records; it
never bridges a gap or reset. The last unsupported pair is ineligible.
Tick/tock keep their original acquisition identities; their even/odd half-phase
positions reverse for an odd anchor. Directional passage times, phase statistics,
cycle grouping, motion views and phase colours use this common reconstruction.
Environmental context averages screened readings from both contributing records.

In the derived frame, `seq`, `source_row` and `edge*_tcb0` retain the first
source capture. `tick_source_row`, `tock_source_row` and the corresponding
`*_source_sequence` columns identify the contributing halves. `original_phase15`
and `original_elapsed_cycles` preserve the original coordinates; `phase15`,
`tick_phase30`, `tock_phase30` and `analysis_cycle` describe the analysis grouping.
With `--export-intervals`, `swing_source_intervals.csv.gz` preserves all original
derived source intervals alongside reconstructed `swing_intervals.csv.gz`.
Capture-quality events remain in original source coordinates. Source files are
never modified.

The default report includes PPS-calibrated time series, phase evolution and
four dataset-local components: phase-15 distributions, signed phase
contributions, swing variation/stability, and velocity/amplitude. Variation pairs a rolling 15-swing
mean with trailing two-minute peak-to-peak range and local standard deviation,
plus Allan/Hadamard stability. No fixed quiet hours or occupancy dates are used.
Sections collapse individually and an expand/collapse-all button controls them.
Temperature, humidity and pressure are shown separately and in a shared-population
combined fit, with chronological holdouts. All CSV statistics remain available.
`--swing-pca` and `--swing-spectra` add optional investigative sections.
`--raw-phase-charts` and `--phase-polars` explicitly request the corresponding
charts; `--diagnostics` alone does not enable them. `--no-swing-components`
omits the four components. The standalone PPS analysis retains its contract.

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

This suite assembles inputs before calculating statistics. PCPS is required;
PCSW supplies the swing analysis.

A receiver session is the Pi acquisition run/recovery identity, with an
independent host monotonic origin. Service startup, serial disconnect/reconnect,
changed or invalid metadata, capture sequence restart/reordering and receiver
queue overflow can start new sessions. File rotation keeps the session identity.
A session change does not necessarily mean that the Nano restarted, but the
current assembly layer always separates sessions. `previous_session` records
predecessor identity only; it does not certify counter continuity or event UTC.

### Combining valid datasets across breaks

The default `--collection-mode separate` retains the existing detail index.
Use `--collection-mode combined` for a shared report with pooled descriptive
statistics, a timeline, independently aligned phase comparisons, environmental
associations and stability estimates. Each recording group still runs the normal
suite, including all four standard components. Detail reports remain available.

```sh
python -m pendulum_analysis.suite RECORDINGS --out analysis_test/combined \
    --collection-mode combined --profile synchronome --impulse-alignment
python -m pendulum_analysis.pps.historical_main RECORDINGS --out analysis_test/pps \
    --collection-mode combined
```

Combined collections automatically export derived intervals. Already analysed
datasets can instead be combined explicitly, including audited partial/interrupted
captures selected by an external retrieval process:

```sh
python -m pendulum_analysis.combined REPORT_A REPORT_B --out COMBINED_REPORT
```

Those detail reports must be complete and have `--export-intervals` exports.
PPS and swing reports are combined separately. Conflicting measurement settings,
overlapping session/sequence identities and duplicate anonymous inputs are errors.
The combiner reads completed results; it does not recalibrate concatenated clocks.
Calibration, half pairing, complete cycles, rolling windows and stability
differences remain inside each original epoch/run. It never manufactures an
interval, cycle or absolute mechanical phase across a reset or unknown gap.

Each epoch must independently pass impulse alignment to enter pooled mechanical
phase profiles. Original anchors are displayed, even when they differ. Fallback
epochs remain in descriptive timing statistics and their detail reports, but
their sequence phase labels are excluded from mechanical phase pooling. Overall
means and distributions use eligible observations, with no clipping; pooled
spread includes differences between epochs. Stability pools existing squared
differences by their Allan-pair/Hadamard-triple counts at comparable cadence
(within 0.1%); those overlapping counts are not independent degrees of freedom.
Environmental fits use cycle-weighted, within-epoch variation with separate
intercepts and chronological training/holdout populations within each epoch.
They describe association rather than establish a physical cause or correction.

A common calendar axis requires explicit per-record receipt provenance. Optional
`PCPS_provenance.csv` and `PCSW_provenance.csv` companions use CSV-record
`source_row` (header is row 1), matching `seq`, and `selection_epoch` or
`host_receipt_epoch` in UTC epoch seconds; `utc_basis` and `source_session` retain
inference and capture identity. Rotated collections retain these witnesses with
adjusted assembled row numbers. A missing/incomplete mapping gets a separate
elapsed-time panel; times are never invented from manifest dates or counter
subtraction across boots. Receipt labels, including explicitly inferred clock
repairs, are not independently verified event UTC. Gaps stay visible and receipt
separation is not a measurement of physical PPS outage duration. Keep the whole
combined report directory and its linked independent report directories together.

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

Every phase-coloured chart uses the same fixed 15-colour key as **Pendulum
behaviour over time**, including static figures and interactive explorers.
Half phases `2p` (tick) and `2p+1` (tock) inherit full-swing phase `p`'s colour;
circles/solid lines identify tick and triangles/dashed lines identify tock.
Half-phase boxplots use plain tick boxes and hatched tock boxes. Complete-cycle
means combine all 15 phases and use neutral motion lines. Heatmap shading still
encodes the labelled numerical value; coloured phase-axis labels retain the
shared phase key.

Counter sequence wrap is extended before modulo phase; resets have separate
epochs that are never pooled. Phase zero is only the documented sequence
convention until a physical impulse anchor is independently established.

The supported suite does not output a calibrated physical sensor offset.

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
If a swing range overlaps more than one PPS counter epoch, calibration is
unavailable even when one epoch covers more complete swings. Extra overlap is
not evidence of epoch identity after a reboot. Analyse receiver sessions
separately to resolve that ambiguity; never concatenate them to establish a
shared calibration. Within one counter epoch, whole-wrap translations still
require a unique maximum overlap.
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

## Rolling variation and complete-cycle stability

The former automatic four-hour frequency section is replaced by the variation
component. Its 15-swing arithmetic mean advances by one swing; a separately
labelled alternative emits once per complete phase-0..14 cycle. Both use only
consecutive, eligible PPS-calibrated support, with gaps left unfilled. Plotting
frequency is a units option, using `(nominal_period / mean_period - 1) * 1e6`
ppm, with no arbitrary mean offset removed.

Local peak-to-peak range is max minus min over `(t-120 s, t]` of those mean
periods. The table's median local SD is the median of the population standard
deviations about each complete two-minute window's own mean. Drift remains
included. Sparse and dense cadence values stay separate; the points overlap
and their count is not an independent-sample count. After every gap, a fresh
uninterrupted two-minute span is required. CSVs retain every available value.

Select an interval explicitly, inspect its two-minute detail and four-hour
context, and read range, RMS, slope and explicitly labelled detrended RMS for
that selection. No interval is labelled quiet automatically. The plots retain
drift; detrending affects only that labelled statistic.

Allan/Hadamard estimates use nonoverlapping complete-cycle measurements, with
all contributing differences inside eligible uninterrupted runs. Hollow markers
flag fewer than ten disjoint three-block spans; they are not confidence intervals.
The optional PCA is ordinary covariance PCA in unscaled microseconds, fitted
separately per dataset. Optional Welch spectra retain physical mean-period power
in µs²/Hz and disclose segment duration, coverage and cadence approximation.
No ranked peak is automatically interpreted as a mechanical oscillator.

## Passage speed and estimated amplitude

The fourth default component follows variation/stability and precedes environment.
It uses the existing eligible PPS-calibrated five-edge measurements. Invalid
chronology, drops, calibration gaps and optical-clearance exclusions remain gaps;
no missing measurement is carried forward. Epochs are analysed separately.

For each tick/tock blocked passage, speed is `w / B` in mm/s, where `w` is the
effective optical blocked path and `B` its PPS-calibrated blocked duration. This
is a passage-averaged speed magnitude, not signed velocity or a direct measurement
of instantaneous BDC speed. Estimated angular semi-amplitude is
`A = v * P / (2*pi*r)` in radians, converted to degrees, using that record's full
calibrated period `P` and pivot-to-beam radius `r`. It describes centre-to-peak
angle, not peak-to-peak travel. The near-BDC harmonic model can be inaccurate for
an offset gate or a passage overlapping the impulse. Tick/tock do not identify
physical left/right, and phase zero does not locate a mechanical impulse.

`--optical-path-mm` defaults to the provisional effective blocked path of 3.79 mm;
`--pivot-to-beam-mm` defaults to 1242 mm. Both must be finite and positive and are
recorded in settings and `summary.json` motion metadata. Absolute scale uncertainty
from optical switching thresholds is not represented by the observed passage
spread. A common duration-scale change affects speed inversely and cancels from
the amplitude ratio. The definitions match the Pi's harmonic motion model;
offline values use the offline PPS calibration and need not equal live values.

Every individual estimate is calculated before averaging: mean speed is the
mean of `w/B`, not `w/mean(B)`. Complete 15-swing cycle means require all 15
consecutive, eligible, calibrated records, covering every phase once. Tick/tock
means remain separate. Squared amplitude is calculated per passage, then averaged
over all 30 passages; the square of mean amplitude is not substituted.

The overview aligns speed and semi-amplitude on one time axis, showing all
individual passages and complete-cycle means. The embedded interval explorer
switches between those populations and between absolute units and percentage
change from each epoch/side's whole-epoch eligible individual arithmetic mean.
It also shows blocked duration and full-period detail for individual records.
Above 100,000 source swings, the explorer initially shows complete-cycle means.
Individual records are stored losslessly in 16,384-record companion script chunks,
loaded only for the selected interval; at most four chunks form a selected view
and two decoded chunks remain cached. Choosing individual passages at whole-run
scale selects a 90-second central detail; explicitly requested wide individual
intervals fall back to cycle averages with a visible explanation. Every individual
passage remains available on zooming in, in the static overview and in CSV exports.
The companion scripts also load when opening the report directly from disk.
This bounds browser memory instead of decoding millions of redundant observations
when the report first opens. Short recordings retain embedded individual data.
Selected means, sample SD and peak-to-peak statistics use every finite value in
the interval. Pixel-column first/last/min/max reduction affects only drawing,
not statistics or exports. The static local view respects `--detail-start-hours`
and `--detail-duration-seconds`; by default it starts at the epoch's first record.

Thirty-half-phase profiles use complete cycles only and show the mean plus
observed 5th–95th percentile spread, not confidence intervals. Counts and SD are
exported. `same_direction_change_pct` compares each phase's mean with the preceding
same-direction phase's mean (two half phases earlier, wrapping within the
profile); it is descriptive, not an impulse detector or a measured loss law.
Phase evolution heatmaps use the same time-bin width as period phase evolution.
Colours show percentage deviation from each side's equal-phase mean in each time
bin. Missing bins are blank. Absolute timelines retain the drift removed by this
heatmap centring.

Amplitude/period plots pair complete-cycle period with mean squared estimated
amplitude. Fits use hourly averages of those paired cycles. Each hour requires
30 nominal minutes of complete cycles; fits require 24 eligible hours. An
amplitude-only fit uses the motion population. The environment-only and
environment-plus-amplitude fits use exactly the same cycles, with all 15 readings
passing existing physical/stale screening for all three environmental sensors.
Their common hours, chronological split and constant baseline make later prediction
errors comparable. The earliest 70% train the final 30%, with at least 24 training
and 12 test hours. Training alone supplies scaling, coefficients and the constant
baseline. Adjusted slopes are withheld if centred/scaled predictors lack full
rank or have condition number above 30. Velocity is not another predictor because
it derives from the same optical measurements.

These fits are descriptive associations. Shared capture edges, use of period in
the amplitude estimate, drift and escapement effects can produce associations;
the fitted slope is not automatically a circular-error coefficient. No periods
are corrected, and no independent-sample significance claims are made.

`csv/swing_motion_observations.csv.gz` retains source rows, extended sequences,
epochs, phase, passage times, calibrated blocked durations, period, eligibility,
estimates and relative changes. Unavailable values stay missing. Companion
`swing_motion_cycles`, `swing_motion_summary`, `swing_motion_phase`,
`swing_motion_evolution`, `swing_motion_hourly`, `swing_motion_fits`,
`swing_motion_validation` and `swing_motion_predictions` CSVs retain full precision,
coverage and model status. Predictions distinguish full-run descriptive fits
from held-out predictions. Geometry, formulas and population definitions are
recorded in the report and summary. Motion tables remain exported with
`--no-swing-components`, which suppresses the component figures and explorer.

## Swing period and environmental readings

The four swing components follow the behaviour overview; environment and
closed exposure/support sections follow them. Display tables use four
significant figures for general estimates and retain exact integer counts.
Swing duration summaries use milliseconds to 1 µs and spread in microseconds
to 0.01 µs. Full-precision exports are unchanged.

A single **Environment and remaining variation** section leads with the
pendulum response. The three sensor associations share a response axis;
combined observed/fitted and remaining-variation traces share a time axis.
Repeated single-sensor residual charts are omitted. The visible table keeps
coverage, residual RMS and later-hour fit/constant prediction errors together.
Capture-clock associations remain in a closed supporting subsection, because
they measure oscillator response against PPS rather than pendulum sensitivity.
Methods are explained once; slopes and validation populations are available
in expandable detail. Every plotted response keeps its own dataset's scales.

Default tables fit each of temperature, humidity and pressure separately to
PPS-calibrated period, using only complete 15-swing groups. For each channel,
all 15 sensor readings in a group must be physically valid and pass the existing
retrospective stale-value screen. Period and sensor means use exactly the same
groups; no unmatched environmental observations contribute. Each group is
assigned to its midpoint hour on the PCSW timeline. Hourly period and sensor
means weight complete groups equally. Hours require at least 30 nominal
minutes of eligible groups; fits require at least 24 eligible hours in one
epoch. No epochs are pooled or readings interpolated.

Separate single-sensor least-squares fits have an intercept and export slope
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

The combined pendulum model uses only cycles with all three sensors screened-valid
on all 15 swings. It is not a join of unmatched single-sensor hourly means.
Adjusted slopes and predictions follow the same rank/conditioning and chronological
policies as the clock associations. Expanded results are in
`swing_environment_all_fits.csv`, `swing_environment_all_validation.csv`,
`swing_environment_joint_hourly.csv` and `swing_environment_predictions.csv`.

## Summaries and optional distribution diagnostics

When `--phase-polars` is requested, `swing_eN_pps_calibrated_phase_polars.png` places full-swing
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
robust-spread chart is added to this combined layout.

Each eligible event is formed before statistics are calculated. In particular,
the median full duration is not the sum of component medians. Every expected
phase bin appears, including empty bins, with record, eligible and excluded counts.

The HTML and Markdown reports also show full-swing, half A/B, open and
blocked duration summary tables for every epoch. Raw populations are included
only with `--raw-phase-charts`; `--diagnostics` adds detailed statistics. The
default shows calibrated count, mean and sample standard deviation. Diagnostic rows show count, mean,
median, sample standard deviation, robust spread (1.4826 × MAD), 5th and 95th
percentiles, minimum and maximum. Reported durations are in milliseconds and
spreads in microseconds; CSV statistics remain in seconds;
spread includes slow drift and phase structure. Empty populations are labelled
unavailable, as is sample standard deviation for a single event. The existing
`swing_component_summary.csv` retains full precision and gains `p05` and `p95`.
Full-precision statistics remain in the CSV exports.

The phase-15 distribution component is included by default, with full-population
moments and clearly marked scale exceptions. Scale hints use isolated phase
median offsets relative to typical phase IQR, rather than assigning an impulse
identity; dispersed offsets keep shared scales. All hints are dataset-local and
the viewer can override each phase. Zoom discloses exact hidden counts.

`--diagnostics` adds calibrated boxes and cancellation views. Adding
`--raw-phase-charts` enables the raw counterparts; `--phase-polars` requests
polar views. Together these options provide:

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
gap-aware Allan deviation. Environmental associations and remaining variation show all three sensors and
their combined fit. The old clock/environmental-validity plot and accumulated
phase plot are omitted from the report. Minute/hour/day summaries and legacy
phase columns remain exported for diagnosis.

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

Clock associations require at least 1800 matched one-second observations per
hour. All-three hourly means and timing responses use the exact same underlying
valid observations. Single-sensor models keep each sensor's own valid population.
The combined model standardises predictors to inspect rank and conditioning;
adjusted slopes are withheld if rank is deficient or condition number exceeds
30, matching the dashboard policy. A combined prediction may still be shown.
Fits require 24 eligible hours; the chronological 70/30 check requires 24 training
and 12 test hours. Epochs are separate and no independent-sample p-values are used.
Legacy temperature candidate-model CSVs remain available, but the report's
environmental sections and residual plots use the all-three implementation.

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
