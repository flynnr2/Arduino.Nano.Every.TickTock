# Observatory dashboard reference

This describes implemented live dashboard behaviour. For stored observations and
statistical formulas, use [DATA_FORMAT.md](DATA_FORMAT.md#observatory-display-history);
for requests and limits, use [HTTP_API.md](HTTP_API.md). Settings and restart
requirements belong to [CONFIGURATION.md](CONFIGURATION.md). Physical acceptance
remains outstanding; [OBSERVATORY_PLAN.md](OBSERVATORY_PLAN.md) records delivered
stages and deferred work.

## Period, rate and display timebase

The overview, OLED and ThingSpeak use one arithmetic mean of accepted full
swings over 600 seconds of captured swing duration. Each observation's four
beam intervals are calibrated with the qualified PPS dual-EWMA frequency
available when that observation arrives; later PPS observations do not
recalibrate earlier swings. The PPS model remains unchanged.

“Filling window” means fewer than 600 seconds of fresh captured observations;
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
expired calibration, resumed values are provisional until 600 seconds of fresh
calibrated swings replace them. Missing time does not count towards filling.

A new capture session/serial timeline, sequence discontinuity in swings, invalid
swing intervals or capture silence of ten seconds starts new display estimates.
There is no nominal-frequency fallback before qualified PPS calibration. Restarting the acquisition
process starts fresh clock, sequence and forecast state. Restarting only analysis
resumes its exact saved causal state and replay position. For a short acquisition restart,
a bounded checkpoint of calibrated individual swings can prime the mean after
fresh PPS qualification and a current swing. Only samples still younger than
600 seconds are reused. The dashboard shows “Restored / provisional”, checkpoint
age and fresh coverage; the OLED shows `MEAN600s restored`. Restored samples age
out and never count towards the required 600 fresh seconds. Same-boot age uses
the monotonic clock. Across Pi boots both the saved and current UTC must be
verified; an initially incorrect boot date cannot admit old samples. Incompatible,
corrupt, expired or future-dated checkpoints are rejected. The checkpoint is
written off-thread every 30 seconds and at graceful shutdown. It starts being
populated after this version is installed; existing aggregate history cannot
supply individually calibrated swings. Retained display history is not recalculated.
Changing the holdover limit alone does not reset estimates. ThingSpeak retains
its stricter fresh, locked PPS requirement and skips holdover observations.

## Individual-swing forecast

The forecast panel sits beside the retrospective Swing phase charts. Set
`forecast_cycle_length` to the mechanism's repeating full-swing cycle: `15` for
this Synchronome, `1` for a mean-only forecast, or `0` (default) to disable it.
Other cycle lengths from 2 to 120 are supported. No cycle is inferred automatically.

The model adds a learned zero-mean cycle shape to the 600-second mean. Its shape
uses a fixed 600-second EWMA half-life. A prediction for the next sequence is
frozen using only preceding observations. When that swing arrives, the model
scores it before learning from it and issuing the following prediction.
The panel shows the next prediction, the last forecast versus observation, and
recent forecast and mean-only RMSE over 600 captured seconds. Error is observed
minus predicted period. Learning and stale/unavailable states are explicit;
gaps and unavailable calibration reset the forecast. Changing the cycle length
resets only the forecast, leaving the rate mean intact.

The small chart uses the latest 120 scored swings retained by acquisition.
Browser polling delays and page reloads retrieve that buffer. Sequence continuity
controls joins; capture/model resets clear it. Time labels show Pi receipt time,
not exact Nano-event UTC. Historical scores remain visible while current values
are stale. It is a bounded recent buffer, not a persistent per-swing log. Raw captures remain
the evidence for replay. The [forecast replay tool](../tools/README.md#replay-the-live-swing-mean-and-forecast)
uses the production model and records its configuration and implementation hash.
An ex-post phase median can use later observations; a forecast cannot. Neither
an individual forecast error nor a phase deviation is accumulated clock error.

## Breakbeam diagnostics

The breakbeam panel shows the same 600-second arithmetic means for `tick`, `tock`,
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
selection. It plots the 600-second mean full period against temperature, with
a fitted line, R², and slope in µs/°C. Historical EWMA fits remain available
through explicit API selections for older recordings. Points are
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
