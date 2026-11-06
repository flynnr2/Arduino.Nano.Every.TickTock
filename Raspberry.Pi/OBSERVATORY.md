# Observatory dashboard reference

This describes implemented live dashboard behaviour. For stored observations and
statistical formulas, use [DATA_FORMAT.md](DATA_FORMAT.md#observatory-display-history);
for requests and limits, use [HTTP_API.md](HTTP_API.md). Settings and restart
requirements belong to [CONFIGURATION.md](CONFIGURATION.md). Physical acceptance
remains outstanding; see the [hardware acceptance checklist](HARDWARE_TESTS.md).

## Period, rate and display timebase

The overview, OLED and ThingSpeak use one arithmetic mean of the latest 300 accepted complete swings. Each observation's four
beam intervals are calibrated with the qualified PPS dual-EWMA frequency
available when that observation arrives; later PPS observations do not
recalibrate earlier swings. The PPS model remains unchanged.

“Filling window” means fewer than 300 fresh complete swings;
it is not an accuracy guarantee. Full-swing BPM is `60 / period in seconds`,
so a two-second full period is 30 BPM. Gain/loss and BPM are calculated once
from that same period and shared by the displays and publisher. History uses
stored mean values; legacy EWMA values are never substituted into this series.

The Pi display's PPS calibration rejects early extra captures using an
expected-edge candidate;
the raw recorder still receives every parsed capture before that filtering.
Cold start accepts a one-second interval within ±10% of nominal. Once
calibrated, its acceptance window is ±1.25 ms around the predicted next edge.
A late edge establishes only a recovery candidate. The frequency estimator
still requires a plausible pair with Nano `LOCKED` status; this is independent
of the Pi's chrony/GPS UTC reference.

`pps_holdover_seconds` (default 180, range 5–86400) bounds reuse of the last good
calibration. Holdover begins when Nano reports non-locked status or calibration
is five seconds old. The HTTP overview and OLED show `HOLDOVER`; history stores
that quality and calibration age. Dashed chart segments mean learning or PPS
holdover, with the exact state at the cursor. At the limit, estimates pause and
rates become unavailable; raw PPS/swing recording continues. Reacquisition
retains earlier mean observations if swing continuity was preserved. After an
expired calibration, resumed values are provisional until 300 fresh
calibrated swings replace them. Missing time does not count towards filling.

A new capture session/serial timeline, sequence discontinuity in swings, invalid
swing intervals or capture silence of ten seconds starts new display estimates.
There is no nominal-frequency fallback before qualified PPS calibration. Restarting the acquisition
process starts fresh clock, sequence and forecast state. Restarting only analysis
resumes its exact saved causal state and replay position. For a short acquisition restart,
a bounded checkpoint of calibrated individual swings can prime the mean after
fresh PPS qualification and a current swing. The checkpoint must have been
saved less than 600 seconds ago; its latest 300 samples are reused provisionally. The dashboard shows “Restored / provisional”, checkpoint
age and fresh coverage; the OLED shows `MEAN300sw restored`. Restored samples leave in capture order and never count towards the required 300 fresh complete swings. Same-boot age uses
the monotonic clock. Across Pi boots both the saved and current UTC must be
verified; an initially incorrect boot date cannot admit old samples. Incompatible,
corrupt, expired or future-dated checkpoints are rejected. The checkpoint is
written off-thread every 30 seconds and at graceful shutdown. It starts being
populated after this version is installed; existing aggregate history cannot
supply individually calibrated swings. Retained display history is not recalculated.
Changing the holdover limit alone does not reset estimates. ThingSpeak retains
its stricter fresh, locked PPS requirement and skips holdover observations.

## Velocity, estimated amplitude and the impulse cycle

The motion panel reports latest tick and tock passage speeds in mm/s and angular
semi-amplitudes in degrees, plus arithmetic means of the latest 300 valid fresh
swings. Each crossing is converted before averaging: speed is `3.79 / B`, with B
in seconds, and semi-amplitude is `speed * P / (2*pi*1242)` radians, with the full
period P in seconds. Semi-amplitude means centre to peak; peak-to-peak is twice
that angle. Velocity is displayed as a speed magnitude, not a signed left/right
component or the bob's linear speed.

The 3.79 mm effective optical path (2.52 mm feature plus nominal 1.27 mm slit)
is provisional. The 1,242 mm pivot-to-beam distance is the owner measurement.
The harmonic inference assumes the sensing region is near bottom dead centre;
optical thresholds, displacement from the centre and impulse acceleration limit
absolute accuracy. No mass or point-mass pendulum length is required. These
estimates do not correct the period or environmental model.

The cycle chart uses separate directional medians from up to 20 complete cycles
of 15 swings. Partial cycles are omitted. Phase follows the extended swing
sequence across uint32 wrap, and its zero is not an independently observed
impulse. With at least three complete cycles, the greatest positive speed
increase relative to the preceding like-direction passage is marked as a
candidate impulse-related feature. Nominal half-phase seconds are a cycle
coordinate, not measured contact times. Points support hover, tap and keyboard
inspection; the measure selector switches between speed and estimated amplitude.

Two aligned history charts retain the separate tick/tock 300-swing means. They
share the existing ranges, zoom, cursor, session selection and startup-estimate
control. The mean takes about ten minutes to fill; a complete cycle appears in
about 30–60 seconds depending on initial sequence phase. A normal analysis
restart restores motion samples. New acquisition sessions, discontinuities and
calibration interruptions start fresh motion samples. First installation adds
motion to existing analysis checkpoints without replaying old captures; earlier
history lacks these values. A deliberate history rebuild can calculate them
from retained input, using these same provisional assumptions.

## Individual-swing forecast diagnostics

The Swing prediction dashboard section has been removed. Optional causal forecast
state remains available through `/api/status`, saved analysis results and replay
tools. The `forecast_cycle_length` setting and timing estimator are unchanged.

## Breakbeam diagnostics

The breakbeam panel shows the same 300-swing arithmetic means for `tick`, `tock`,
`tick_block` and `tock_block`, plus tick-minus-tock and block-minus-block
differences in microseconds. Positive means the tick interval is longer.
Open intervals come from edges 0–1 and 2–3; blocked intervals from edges 1–2
and 3–4. Each swing has equal weight. All four means share the period estimator's
capture window, PPS calibration, filling state, resets and freshness handling.

The centring proxy is `100 * (mean tick - mean tock) / mean full period`.
Zero means equal open times. Block mismatch is separately expressed as a
percentage of the mean of the two averaged block durations. The half-cycle
difference compares `(tick + tick_block) - (tock + tock_block)`.
These timing diagnostics help guide sensor adjustment, but do not measure a
physical displacement or establish correct centring: motion asymmetry and
beam/flag geometry can also affect them. Tick/tock signs do not identify physical
left/right. Ratios are calculated from the duration averages, not averaged
per-beat ratios. They are live display diagnostics; recorded captures and the
retained history schema are unchanged.

## Target and retained trends

Choose a target full period in configuration (the 2-second preset is explicit)
to display estimated gain/loss. Positive rate means gaining time. The rate is
derived from each period estimate as `86400 * (target / measured_period - 1)`
seconds per day, without additional averaging; divide by 24 for seconds per hour.
An unset target leaves rate unavailable. Gain/loss is a rate estimate, not accumulated clock error.

Period and gain/loss history charts hide estimates flagged as learning by
default, including them in neither the trace nor its vertical scale. Select
**Show startup estimates** to inspect these provisional values as dashed traces.
The control redraws loaded history without another database request; observations
remain stored. Hidden startup intervals remain gaps, environmental charts keep
their readings, and settled PPS holdover estimates remain visible as dashed
traces. Environmental fits continue to require period learning to be complete.

History contains sampled capture-time display estimates and environmental readings, not raw
metrology. It follows recording enablement, persists without an open browser,
and has independent retention and size limits (30 days / 256 MiB by default).
It shares the existing overall data budget and free-space reserve. See
[data semantics](DATA_FORMAT.md) for what is stored and [storage policy](STORAGE.md)
for raw recordings, archives and exports. Older recordings are not automatically
imported into this new history.

Current history follows saved captures and backfills processing delays.
For older status-sampled history, short publication pauses (over three seconds, up to 30 seconds) and
Chrony diagnostic changes are persisted as diagnostic boundaries without
splitting valid measurements. Longer observation gaps, backwards monotonic time,
wall-clock corrections, recording pauses and write/queue failures still split
traces. Timing, temperature/humidity and pressure have separate continuity IDs,
so an unrelated sensor or timing outage does not break another valid trace.
Environmental regression still requires their combined measurement continuity.
Older history can join only narrowly identified short diagnostic boundaries
with matching capture and configuration provenance; raw records remain unchanged.

## Environmental relationships

The temperature relationship panel follows the history range and session
selection. It plots the 300-swing mean full period against temperature, with
a fitted line, R², and slope in µs/°C. The history rebuild replaces superseded estimates with the 300-swing model. Points are
coloured from earlier to later; hovering shows the paired reading and sample
count. A positive slope means a longer period at higher temperature.

The view uses eligible retained observations, groups them into paired time-bucket
averages, and fits each continuity segment separately. The default segment has
the most paired averages. Learning/stale values and missing pairs are excluded;
resets, changed settings and gaps are not pooled into one relationship. Live
comparisons refresh every 30 seconds; selection changes refresh immediately.
The [recorded-history and fit contract](DATA_FORMAT.md#observatory-display-history)
owns bucket widths, eligibility, segment boundaries and returned statistics.

The panel also shows residual RMS (scatter around the line, in µs) and an
approximate 95% confidence interval for the slope. Expand **Uncertainty method
& statistics** for its Newey–West standard error, approximate two-sided p-value
for zero slope, dependence window, and minimum sample requirement. R² and RMS
describe the selected observations; neither is a measure of slope uncertainty.

The [statistical method](DATA_FORMAT.md#observatory-display-history) defines the
Newey–West lag window, minimum observations and reasons for withholding
uncertainty. The descriptive line can remain visible when those requirements
are unmet. Confidence intervals and p-values are approximate, and R² does not
establish causation or independent observations. The fit applies no thermal lag
or temperature-sensor error correction and does not alter capture data.

### Air density and matched environmental models

The current density card and history chart use CIPM-2007, with absolute local
BMP280 pressure and SHT41 temperature/humidity. CO₂ is assumed at 400 ppm, not
measured. The published 2007 constants are retained. The previous ideal moist-air
mixture with Buck saturation pressure remains visible for comparison.

Values outside the recommended 15–27 °C, 600–1100 hPa and 0–100% RH range
are unavailable. Sensor freshness and existing continuity checks still apply.
Density is calculated at each jointly valid sensor observation before averaging
over the measured 300-swing window and then into display buckets. Historical
chart density is derived at the retained sensor points; its extrema are not
independently preserved by the existing history reduction.

The density comparison includes density alone, temperature plus density and a
three-sensor control, with corresponding previous-formula models. All five use
identical complete in-range paired averages. Density replaces pressure/humidity
in the two-variable model. It is not appended to all three inputs. Slopes retain
the same conditioning and Newey–West uncertainty rules. These are descriptive
fits, not forecasts or validated environmental compensation.

The **Clean period** button selects from 1 November 2026, 13:00 GMT. A clean
selection excludes trailing swing windows reaching before that operator-declared
boundary. Selections spanning the boundary are explicitly labelled. Each
measurement segment remains separate; the boundary does not waive validity checks.

The density views use existing saved history as soon as it is available. Reloading
or restarting the HTTP dashboard does not reset the logger or the 300-swing mean.
The analysis worker persists its full state and replay cursor transactionally.
A new acquisition session or a genuine timing discontinuity can still require
qualification/provisional filling; that is separate from a browser restart.

## Swing phase medians

The Swing phase panel shows two PPS-calibrated polar charts: the median full
swing in 15 bins, and the median half swing in 30 alternating tick/tock bins.
Hover, tap or keyboard-focus a bar for its absolute median, signed deviation and
eligible/excluded counts. Expand the bin tables for an accessible numeric view.
The full-swing reference is the pooled median; half swings have separate pooled
tick/tock medians, matching the offline analysis report. Empty bins are marked
with a cross and never treated as zero. Phase zero is the sequence convention,
not an independently identified impulse.

This is a separate recent-recording view, independent of the history controls.
It reads up to 1,800 latest swing rows and 3,800 latest PPS rows from the current
recording segment (at most 2 MiB per capture file), retaining complete lines.
For a two-second pendulum this is about an hour of swings when captures are
continuous. Gaps can make the elapsed span longer; limited PPS coverage can make
fewer swings eligible. Only the latest analysis epoch is displayed; older epochs
and previous recording segments are not pooled. The coverage caption identifies
the segment, sequence bounds and observed span. Segment rotation starts a new
view. This first version does not query archived/compressed segments.

The saved-view service runs one background calculation at most once a minute;
the web process serves its saved result to all browsers. Acquisition continues independently. The
calculation directly reuses `pendulum_analysis` swing validity, sequence phase,
PPS timescale and median statistics. It uses the Synchronome profile (nominal
full period 2 seconds) and the recorded nominal counter frequency. No nominal
clock fallback or smoothed display estimates enter these charts. The centred
61-second PPS fit truncates at capture boundaries; recent values may change as
more PPS arrives. Inactive recordings and failed refreshes are labelled.

The standard Pi installer now installs the repository's analysis package as well
as the receiver. For an existing standalone development environment, install it
from the repository root into the same environment as the web service:

```sh
Raspberry.Pi/.venv/bin/python -m pip install -e .
```

Without that package the phase panel explains that analysis is unavailable;
other dashboard functions continue. Environment/peripheral details, time-reference
health, storage/archive, downloads, files, configuration and Nano configuration
are collapsed initially and can be expanded with their headings.
