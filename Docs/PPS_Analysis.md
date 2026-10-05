# Unattended PPS analysis

Status: Historical capture-investigation reference. The current suite is documented in [Clock and swing analysis](Clock_Swing_Analysis.md). Commands and output contracts below describe the archived implementation; its regression entry point is `python -m pendulum_analysis.pps.historical_main`.

The PPS analyzer is ordinary Python. It reads local CSV files and produces a
complete report without prompts, AI models, accounts, network services or
manual post-processing. Dependencies are installed once using normal Python
packaging. It does not read `UNO.CSV`.

## Install and run

From the repository root, in a Python environment of your choice:

```sh
python -m pip install -e '.[test]'
python -m pendulum_analysis.pps.historical_main Data/67Days
```

The default output is `Data/67Days/pps_analysis/`. Open `report.html` in any
browser, or read `report.md`. No local web server is needed. A direct input and
explicit output directory also work:

```sh
python -m pendulum_analysis.pps.historical_main Data/67Days/PCPS.CSV --out out/pps
```

When using the virtual environment created for this checkout, substitute
`.venv/bin/python` for `python`. The package has no Codex-specific runtime
dependency. The `pendulum-analyze` command produces the separate clock/swing
suite report; use the command above to regenerate `pps_analysis/report.html`.

## Rotated Pi recordings

The standalone PPS command accepts a recording collection, a receiver session
directory, an extracted export or `catalogue.json`, using the same assembly
layer as the combined clock/swing suite:

```sh
python -m pendulum_analysis.pps.historical_main /path/to/recordings --out /path/to/pps_analysis
```

Consecutive completed segments within one receiver session and identical
contract are assembled before analysis. Plain and gzip CSVs are supported.
PCPS is required; PCSW is optional. Missing PCPS files, active/interrupted
segments, non-rotation closures, missing segments, changed contracts and session
changes prevent joining. Each retained group gets its own report under an HTML
index. Source hashes and original-to-assembled row mappings are saved in each
group's `summary.json`; originals are unchanged. Temporary joined inputs use
disk on the analysis host.

Recorded STS nominal frequency is resolved separately for each assembled group.
Diagnostic `--config` settings preserve that inference; an explicit nominal
frequency overrides it for every group. The Python `run_analysis` API continues
to return `PpsResult` for a single input, and returns the collection index
dictionary for a collection.

A receiver session is a host acquisition/recovery identity. A reconnect can
start one even if the Nano continues running. Current analysis keeps separate
sessions apart because the predecessor link does not prove capture continuity.
See [receiver session boundaries](Clock_Swing_Analysis.md#rotated-pi-recordings).

## Inputs and failure handling

`PCPS.CSV` or `PCPS.CSV.gz` is required; filenames are case insensitive. Required columns are
`seq`, `edge_tcb0`, `cap16`, `latency16`, and `now32`.

- `gps_status` and `drop_pps` are needed for locked, drop-checked oscillator
  statistics. If absent, those statistics are unavailable, with an explicit note.
- `temperature_C` is optional. Without it the temperature fit is unavailable.
- `PCSW.CSV` supplies swing association. Invalid/ambiguous chronology disables
  that association with a reason; missing swing coverage is not treated as zero latency.
- `STS.CSV` supplies recorded configuration and source-build metadata. `nhz`
  (or `f_cpu`) supplies nominal frequency when no override is given. Without
  status or an override, the documented default is 16 MHz.

Malformed unsigned numeric records remain in the event catalogue and in
`malformed_records.csv` with their original field values. No interval crosses
one. Missing required columns, ambiguous filenames, malformed CSV syntax,
invalid settings, or an inaccessible input/output fail with a nonzero exit
code. A diagnostic anomaly in otherwise readable data is a successful analysis,
not a command failure. All unavailable calculations remain explicitly unavailable.

Source files are unchanged. `source_row` is the CSV record ordinal, counting
the header as 1. Do not treat it as a physical line number if a future source
contains quoted multiline fields.

## Calculations

The analyzer retains input order and reconstructs 32-bit wraps. PPS sequence
gaps can identify additional wraps when the resulting elapsed time agrees with
the sequence count to within 0.1 nominal second. A reset, invalid record,
duplicate/reversed sequence or ambiguous time jump starts a separate segment.
Reported elapsed time concatenates observed spans; unknown durations between
segments are not fabricated. Neither the counter nor CSV sequence is a UTC clock.

For oscillator statistics, both endpoints must be locked (`gps_status=2`),
numeric-valid and reconstruction-consistent. The sequence step must be 1,
the interval must be near one second, and `drop_pps` must not increase. That
counter is cumulative: a past drop does not permanently disqualify later data.
Known GPS states are 0/free run, 1/acquiring, 2/locked and 3/holdover.

The key projection diagnostic is:

```text
projection_offset = (cap16 - edge_tcb0) mod 65536
edge_shift = signed_mod(segment_modal_offset - projection_offset, 65536)
raw_frequency_offset = interval_cycles - nominal_hz
diagnostic_normalized_offset = raw_frequency_offset - (edge_shift - previous_edge_shift)
```

This normalization tests the hypothesis of stable relative timer phase within
a segment. It is not a silent correction to source timestamps. The offset mode
is inferred for each segment, so a new firmware version is not required to have
the old absolute baseline of 8. No rule applies a correction just because
latency is 152 cycles. A +6-cycle edge shift produces +6 and -6 errors in its
incoming and outgoing intervals if its neighbors have normal projection.

Temperature fitting uses hourly means of matched eligible frequency and
temperature samples. It reports an association, not a causal result, a measure
of GPS drift, or a measurement of the oscillator's internal temperature.

Swing association uses the shared 32-bit raw timer. File starts are assumed
to differ by less than half a timer wrap (134.218 seconds at 16 MHz); the files
must describe an overlapping run. That whole-wrap ambiguity cannot be resolved
from these columns alone. All five swing edges are checked for consistent
order. A measured median swing period guides gap wrap inference, permitting
5% accumulated period variation capped at a quarter wrap. Association masks
intervals spanning missing swing rows, out-of-coverage events and ages over
1.5 configured swing periods. Spike rates divide by all associated PPS captures
in the same swing-phase bin, so ordinary captures supply the exposure denominator.

`latency16` is modulo 65536 (4.096 ms at 16 MHz). The arithmetic identity
`(now32-edge_tcb0) mod 2^32 == latency16` does not prove a physically correct
timestamp, or exclude an additional whole-wrap delay. Firmware bounds and
hardware measurements provide that independent evidence.

## Outputs

Both report formats include the eight-row PPS characteristics table: raw PPS
error and offline-adjusted residual in cycles, nanoseconds and ppm, plus
`latency16` and `cap16` on eligible interval endpoints. Columns are `metric`,
`n`, `median`, `robust_spread`, `p05`, `p95`, `min`, `max` and `note`. Robust
spread is 1.4826 × MAD; a zero value directs the reader to quantiles and exact
discrete counts rather than implying zero jitter.

The offline residual subtracts the median of up to 31 **preceding** eligible
raw errors, requiring at least 10 preceding intervals. It restarts after every
excluded interval or segment boundary. This causal oscillator-offset diagnostic
is separate from timer-projection normalization and firmware discipline. The
derived frame includes the expected error and residual in cycles.

The older canonical report used a 0.5–1.5-second interval bound, current-row
lock and a zero cumulative drop counter, and compressed eligible intervals
before forming its median history. This report retains its existing stricter
eligibility, so counts and extrema differ from that historical table. No
anomalous interval is restored just to reproduce an older summary value.

Additional tables show PPS data quality, GPS status, lock/holdover health,
all-valid-capture latency and 16-bit timer phase coverage. Missing telemetry is
explicitly unavailable. The latency/phase coverage tables include unlocked
captures with valid required fields; the eight-row characteristics table uses
the narrower frequency-interval population.

| File                                                                                  | Contents                                                                                               |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `report.html`, `report.md`                                                            | Findings, coverage notes, plots, methods and output links                                              |
| `summary.json`                                                                        | Full-data counts, statistics, settings, source/code SHA-256 hashes and completion flag                 |
| `csv/pps_characteristics.csv`                                                         | Eight PPS timing/latency/cap16 metrics with full-precision statistics                                  |
| `csv/pps_discrete_counts.csv`                                                         | Exact counts of eligible raw errors, offline residuals and latency values                              |
| `csv/pps_data_quality.csv`, `csv/pps_gps_states.csv`, `csv/pps_timebase_health.csv`   | Coverage and GPS/holdover summaries                                                                    |
| `csv/pps_latency_summary.csv`, `csv/pps_cap16_coverage.csv`, `csv/pps_cap16_bins.csv` | Capture latency and phase coverage summaries and bin counts                                            |
| `plots/01_capture_overview.png`                                                       | Near/away-wrap latency histogram, wrap-boundary scatter, background shoulders and counter-sample phase |
| `plots/02_latency_and_swings.png`                                                     | Daily maxima/rates and swing-completion association with exposure                                      |
| `plots/03_frequency_and_integrity.png`                                                | Frequency over time, interval distributions, extra-pulse example and GPS state                         |
| `csv/events.csv`                                                                      | Every classified event, source sequence/record and simultaneous reasons                                |
| `csv/event_windows.csv`                                                               | Neighboring records for every event, without crossing segments                                         |
| `csv/malformed_records.csv`                                                           | Original field values for malformed required numeric records                                           |
| `csv/projection_offsets.csv`                                                          | Counts for each observed projection offset and segment                                                 |
| `csv/latency_histogram.csv`                                                           | Exact full-data latency counts                                                                         |
| `csv/wrap_phase_bins.csv`                                                             | Full-data joint counts of wrap phase, latency and projection shift                                     |
| `csv/daily_rates.csv`                                                                 | Daily counts, maxima and event rates                                                                   |
| `csv/hourly_frequency.csv`                                                            | Raw and diagnostic-normalized hourly frequency, counts and matched temperature means                   |
| `csv/state_runs.csv`                                                                  | GPS state runs split at chronology breaks and sequence gaps                                            |
| `csv/swing_latency_bins.csv`                                                          | Ordinary-capture exposure, spike counts and per-bin rates                                              |
| `csv/status_records.csv`                                                              | Original STS records                                                                                   |

There is no random thinning. Counts and histograms use all eligible records;
plots retain every large spike and explicitly count values outside cropped
views. Report tables show limited selections and link to complete CSV tables.
An optional `--export-intervals` writes the complete derived per-record frame
to `csv/intervals.csv.gz`; the default avoids creating another multi-million-row
table when the event catalogue and summaries suffice.

## Settings

Use `--nominal-hz 16000000` or `--config settings.json`. Unknown keys are
errors. Settings supplied in JSON override defaults; `--nominal-hz` takes
precedence over JSON. The defaults are:

```json
{
  "nominal_hz": 16000000,
  "normal_interval_tolerance": 0.05,
  "large_latency_cycles": 485,
  "rail_latency_cycles": 152,
  "wrap_window_cycles": 250,
  "max_projection_adjustment_cycles": 64,
  "min_hourly_samples": 1800,
  "swing_period_s": 2.0,
  "event_window_records": 2
}
```

The 485-cycle cutoff is the historical large-spike threshold, using strictly
greater than. The 152-cycle rail count is a diagnostic count only. Both may be
changed for a different capture build; the report always records their values.
The normal interval tolerance is a fraction of nominal one-second cycles.

## Validation and baseline

```sh
python -m pytest
PPS_67DAYS_DIR=Data/67Days python -m pytest tests/test_pps.py
python tools/capture_reconstruction/check_reconstruction.py
```

The optional full-data regression uses the local historical recording; the
repository's normal tests do not require the ignored raw data files. It checks:

- 5,820,076 records and 6,181 positive six-cycle shifts;
- 5,181 latency-152 records, of which 5,068 have changed projection;
- 132 spikes above 485 cycles, all matched 696.875-990 µs after swing completion;
- two gaps totalling 112 missing sequence records;
- one candidate extra pulse and the approximately 0.0217 ppm/°C temperature slope.

The stricter two-endpoint lock filter yields 5,819,951 eligible frequency
intervals. This deliberately excludes the first interval ending at recovered
lock, as well as the anomalous intervals and sequence gaps.

The implementation and reported conclusions are separate from hardware
validation of changed firmware. See [timestamp investigation](PPS_Timestamp_Investigation.md)
and [swing latency investigation](PPS_Swing_Latency_Investigation.md).
