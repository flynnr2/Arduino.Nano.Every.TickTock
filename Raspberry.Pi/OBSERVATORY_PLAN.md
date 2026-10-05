# Pendulum Observatory delivery record and remaining work

Stages 1 and 2 are implemented, alongside part of stage 4. Stage 3 remains
planned. This document retains design provenance and the remaining scope;
[OBSERVATORY.md](OBSERVATORY.md) describes current dashboard behaviour and
[DATA_FORMAT.md](DATA_FORMAT.md#observatory-display-history) owns stored history
and statistical semantics. Physical Pi acceptance is still required.

## Design provenance

The original aim was to make the observatory useful for checking clock
performance, following environmental changes and evaluating adjustments, using
the existing Pi estimates and recordings. The references inspired presentation
and workflow; they do not define the implemented measurement contract.

Reference: [article](https://raynerd.co.uk/diy-clock-beat-analyser-pendulum-timer-with-gps/),
[dashboard video](https://www.youtube.com/watch?v=zK1QvNsMGSk), and
[earlier timer video](https://www.youtube.com/watch?v=AOza4zSw_Cs).
Both automatic transcripts and selected video frames were inspected. Useful
themes were readable gain/loss, timing beside environmental trends, and feedback
after adjustment. The presenter explicitly describes unfinished asymmetry and
limitations in the historical graph.

## Delivered stages 1 and 2

- The overview, OLED and ThingSpeak present one 600-second arithmetic swing
  mean, with each observation calibrated by the unchanged PPS dual EWMA.
  Gain/loss uses an explicitly configured target; changing that target does
  not reset the period estimate. Filling labels do not claim accuracy.
- The API/OLED `bpm` convention counts complete swings. Beam-interval and balance
  diagnostics support observation without claiming physical displacement or
  independent escapement beat error.
- Capture-based display history now follows committed records in an independent
  analysis process. Full causal state, progress and history commit together;
  processing delays are backfilled at capture receipt times. Scheduled/cached
  view calculations are independent of website visitors. See [architecture](ARCHITECTURE.md).
- Local chart assets display period/rate and environmental trends, range/session
  selection and a shared cursor. Pi observation time labels and discontinuities
  preserve gaps; history is not exact Nano event UTC or a replacement for raw
  capture analysis.
- Later additions provide retained 600-second mean
  history, period/temperature fits with qualified uncertainty,
  and recent-recording phase medians using the offline analysis implementation.
  The optional individual-swing cycle forecast now adds causal next-swing
  predictions, score-before-learning verification and production-model replay.
  Their current limits and meanings are in the dashboard and data references.

The original queue, sidecar, retention and API design decisions are now embodied
in the implementation and its references. Software checks do not replace
multi-day Pi load, download, loss/recovery and peripheral acceptance in
[HARDWARE_TESTS.md](HARDWARE_TESTS.md).

## Stage 3 — Adjustment notebook and comparison

Deliver a small observational notebook rather than a general project-management
feature.

- Add timestamped notes such as “raised bob by quarter turn”, “wound clock”,
  “moved sensor”, and free text. Support marking now and a selected chart time.
- Store annotations separately from capture CSVs, with stable IDs, session/time
  references and revision history. Use existing administrator authentication for
  creation and edits; monitoring remains read-only without a token.
- Place markers on all aligned charts. Retain the recorded observation-time
  meaning rather than claiming exact event timing for manually entered notes.
- Let the user select before/after intervals and an explicit settling exclusion.
  Compare the same estimator, target, timebase and compatible settings; show
  duration, coverage, learning state and environmental ranges alongside the
  change. Label summaries as comparisons of sampled 600-second mean estimates.
- Warn that the post-adjustment window initially retains earlier observations.
  Never automatically reset it at a note, announce convergence, or treat overlapping mean points
  as independent samples for statistical confidence.
- Export the selected derived series and notes with units, settings and quality
  fields. Preserve existing raw-file downloads as a distinct option.

Acceptance: notes survive restart, anchor correctly across segments, and cannot
execute HTML/script; concurrent edits are handled; comparisons reject or clearly
separate incompatible states and do not conceal gaps or settling periods.

## Stage 4 — Delivered monitoring and remaining work

The acquisition service runs independent read-only chrony and gpsd background
collectors. The dashboard exposes selected UTC source, reference age, offset and
chrony's conditional error estimate, plus receiver fix mode/status, satellite
counts, HDOP and report ages. Unavailable/stale checks are explicit, and Nano PPS
calibration remains separate. Periodic Pi health rows retain these diagnostics;
display history retains its documented UTC-quality subset and boundaries.

Remaining work is divided by responsibility:

- **OS commissioning:** install/configure gpsd and chrony, confirm their ordering,
  correct NMEA/PPS second association and network fallback. The application
  installer does not provision these OS services.
- **Physical acceptance:** exercise GPS loss, network loss, all-reference loss,
  recovery and reboot; compare displayed health with independent OS diagnostics.
  No physical acceptance is asserted here.
- **Future application checks:** independently establish GPS time validity and
  direct Pi kernel PPS freshness. A receiver fix or a configured chrony refid
  does not by itself establish either.
- **Future event-time mapping:** verify the association of Nano PPS captures with
  UTC seconds before assigning precise UTC labels to Nano events. The current
  status panels do not perform that mapping.

[TIMEKEEPING.md](TIMEKEEPING.md) owns deployment responsibilities and the exact
current monitoring limits.

## Boundaries for future work

The adjustment notebook and comparisons above remain prospective. Also deferred
are accumulated clock error, automatic regulation advice, additional smoothing
algorithms, inferred escapement beat error and automatic environment correction.
Older raw logs are not imported into display history: exact reconstruction can
lack intermediate settings/reset provenance, and offline calibration differs
from the live display method. Any later import must declare its method and
assumptions.

Future changes should preserve raw captures, source labels, discontinuities and
estimator ownership. Use existing administrator authentication for writes and
keep monitoring readable without a token. Extend relevant software checks with
synthetic input and fixtures, then perform separate browser and Pi hardware/load
acceptance. Update the owning current reference when a stage is delivered, and
move its entry here from planned to implemented only after checking the code.
