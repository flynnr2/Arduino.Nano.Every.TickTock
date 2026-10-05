# Synchronome trial package

This is an experimental, separate analysis package. It imports the supported
PCPS/PCSW metrology read-only and does not change the normal suite, firmware,
logger, installation metadata or existing reports. Run it from this checkout
with Python 3.10+ and the repository dependencies.

```sh
.venv/bin/python -m synchronome_trial Data/20260928_multiday_0
```

The default output is `Data/20260928_multiday_0/synchronome_trial/report.html`.
Figures are embedded; copy the companion `csv/` directory to retain download
links. `summary.json` records settings, source and implementation fingerprints,
coverage, validation, unsupported measurements and the numerical audit.
Only the selected PCPS/PCSW pair is read. The suspect `67Days` recording is
explicitly rejected. Unknown gaps and separate epochs are never joined.

## Trial investigations

1. Fixed initial-baseline 15-phase correction of all four components, complete
   halves, half difference and full period. Later data never update the template.
2. Automatic event candidates and optional requested times; event-specific
   pre-event templates, before/after profiles, changes and scatter. Detection is
   an exploratory ranking of cycle jumps and short-window contrasts, not a
   classification of mechanical cause.
3. Interleaved 30-position flag profiles and relative passage speed, plus the
   accumulated periodic timing deviation. Sequence labels remain unassigned to
   physical impulse/gathering/passover until externally established.
4. Four homologous-edge periods and two flag-midpoint periods, reconstructed
   only across valid adjacent records. All estimates share the same population.
5. Original-cycle even/odd contrasts, a declared 20-cycle fold, gap-aware
   correlations and longest-unbroken-run spectra. No peak significance is claimed.
6. Joint environmental models: temperature + humidity + pressure, extensions
   with speed proxy, temperature rate and thermal direction, and density benchmarks. Both period
   and half difference are responses. All models use the same hourly population;
   an earlier-70%/later-30% prediction check supplements whole-record fitting.

Swings are the open tick/tock intervals; flags are tick_block/tock_block.
A complete half is open + blocked. `period_us` means full period minus 2 s;
the other `_us` quantities are durations or signed differences in microseconds.
The speed proxy is inverse cycle-mean flag duration relative to the initial
baseline, assuming unchanged optical geometry. It is not absolute amplitude.

## Options and deliberate limits

```sh
.venv/bin/python -m synchronome_trial --help
.venv/bin/python -m synchronome_trial Data/20260928_multiday_0 \
    --out Data/20260928_multiday_0/synchronome_trial_custom --event-hour 4.168
```

Other options change the initial baseline, event count, sequence phase origin
or PPS fit window. Supplying `--impulse-phase N --impulse-side tick` (or `tock`)
adds a user-provided physical anchor. It is not inferred by choosing a prominent
feature. Sequence phase origin still controls the 15-position cycle grouping.

The current trial processes one recording at a time; it does not discover or
concatenate recording collections. Reset epochs remain separate. Event windows
require at least ten complete cycles for a template/comparison; missing or short
windows are marked unavailable. Environmental validation requires at least 24
earlier and 8 later common hourly observations. Rank-deficient fits are marked
unavailable. Environmental direction is derived from adjacent eligible hourly
temperature measurements; it uses current conditions, not advance forecasts.

Every retained timing observation is kept without outlier clipping. The reused
metrology's explicit edge, drop, optical-clearance and PPS-coverage checks still
apply. Frozen templates remove only a repeating phase offset, not changing
rate or amplitude. Standard deviations describe scatter; no iid p-values or
coefficient confidence intervals are manufactured from serial measurements.

Absolute amplitude, true impulse phase/strength, coast-down Q, UTC/solar-noon
alignment and an 8 Hz rod mode require additional calibration or measurements.
High timestamp resolution does not turn sparse beam crossings into a continuous
motion record. A short trial cannot establish long-term material ageing.

## Checks

```sh
.venv/bin/python -m pytest tests/test_synchronome_trial.py
```

Tests exercise frozen-template behaviour, matched edge-period identities,
counter wraps, gaps/resets, original-cycle pairing, a known modulation and
multivariable prediction without training/test leakage. A real-recording run
also verifies input/code immutability and component identities before publishing.
