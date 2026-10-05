# Changelog

## Unreleased

### Combined system repository

- Import the former Display repository's Uno firmware as `Uno.R4.Deprecated/`,
  with its host regressions under `tests/uno_r4/` and historical documentation
  under `Docs/Uno.R4.Deprecated/`. Preserve source commit provenance.
- Establish `Raspberry.Pi/` as the new receiver's home with a shared I²C bus,
  logging/web and explicit reboot gaps. Document the selected Nano USB / GPS
  UART wiring. Nano data and commands default to USB `Serial`; Pi acquisition
  still needs its serial device configured for the observed Nano USB path.
- Keep Nano capture firmware, receiver implementations and offline analysis
  together in one repository.

### Raspberry Pi receiver

- Base OLED swing/PPS feed warnings on acquisition's accepted receipt timestamps,
  preventing false stale banners between saved-analysis updates while preserving
  three-second feed-stoppage warnings and separate period freshness.

- Separate durable capture, causal analysis and cached dashboard views. Replay
  committed inputs with atomic model/history/cursor saves; report analysis lag
  separately from received and durable captures. Preserve timestamped sensor
  evidence for backlog joins. Website, OLED and ThingSpeak share saved results.
  Protect unconsumed journals from archival and keep existing history intact.

- Replace pendulum short/long/blended EWMAs with one 600-second arithmetic mean
  shared by OLED, dashboard, beam diagnostics and ThingSpeak. Calibrate each
  observation using the unchanged PPS dual EWMA; preserve legacy history without
  relabelling its values as the new mean.
- Add configurable causal individual-swing forecasts, frozen next-sequence
  predictions, observed errors and recent RMSE against a mean-only baseline.
  Include production-model replay and implementation/configuration provenance.


- Add a 600-second mean of full-swing tick counts calibrated by a 600-sample
  mean of qualified one-second PPS intervals beside the live EWMA estimates and
  on the pendulum time-series chart. Earlier raw-swing median history is not
  shown as this new series.
- Add coordinated, bounded nine-clock I²C recovery for the Zero 2 W's bus 1,
  with a persistent two-attempt fault budget, driver restoration and status.
- Restore the Uno OLED information layout and refresh cadence, send only
  changed page spans, and document a fixed shared 400 kHz bus with 100 kHz
  fallback instead of per-device speed changes.

- Default the OLED and environmental sensors to shared hardware I²C bus 1;
  allow shared bus settings and update wiring, setup and migration instructions.
  Independent workers protect serial capture, but a stuck bus can affect all
  three peripherals.

- Show beats per minute beside both dashboard period estimates, counting a full
  tick/tock cycle as one beat, with the same measurement freshness and timebase.
- Add short/long averages for all four beam intervals, signed open/block and
  half-cycle differences, and normalised timing-balance diagnostics for sensor
  centring. Preserve capture records and flag stale or learning estimates.
- Add coordinated size/age rotation, capture-quality minute summaries and
  repeated-diagnostic suppression for long-running recordings.
- Add an independent storage worker with verified gzip compression, complete-set
  offload to a configured archive folder, tiered local retention and storage
  pressure protection. Preserve unarchived data when a limit stops recording.
- Add catalogue browsing, bounded date-range downloads and archive/storage
  health to the dashboard. Automatically analyse contiguous recording segments
  with gzip support while keeping pauses, missing segments and sessions separate.
- Implement a Python Pi Zero 2 W receiver with separate acquisition, web,
  environmental-sensor, OLED and storage processes; retain Nano hardware
  capture and receive its records through configurable serial (selected wiring: USB).
- Validate protocol/schema metadata, request replay on joining/recovery, preserve
  raw bytes, count capture/queue/storage loss, and acknowledge reboot gaps without
  resetting the Nano or fabricating measurements.
- Preserve archived PCSW/PCPS CSV headers with environmental columns; leave
  unavailable legacy swing diagnostics empty. Add Nano STS, Pi PI diagnostics,
  per-segment manifests and raw JSONL evidence.
- Add a local-network dashboard, live receiver settings, bounded log downloads
  during recording and acknowledged Nano commands without automatic mutation retry.
- Add independent BMP280/SHT4x recovery, shared sensor/OLED I²C bus 1, and
  period/BPM/block-difference and PPS estimates adapted from the Uno reference.
- Add replay/demo modes, automated compatibility/failure/serial/web tests,
  repeatable Pi installation and automatic service startup. Physical hardware
  acceptance and sustained-operation testing remain outstanding.
- Document mixed 5 V/3.3 V rails from device specifications and correct the
  confirmed photogate to OPB912W55Z, including its TTL-output interface.
- Add an OS Lite configuration checklist covering Wi-Fi, SSH, device enablement,
  power saving and edge-timestamp limitations, plus Git deployment over SSH.
- Implement read-only background chrony and gpsd diagnostics, with independent
  freshness and failure reporting in the dashboard and Pi health records. Document
  GPS/PPS timekeeping with network NTP fallback; OS provisioning, hardware
  acceptance and precise Nano event-to-UTC mapping remain separate work.
- Expose `repair eeprom` in the restricted Pi Nano command interface. Successful
  firmware mutation acknowledgements follow verified EEPROM persistence; the Pi
  matches the acknowledgement without an independent EEPROM readback and never
  automatically retries an uncertain mutation.
- Reconcile documentation with the current implementation and add maintained
  configuration, HTTP, observatory and development/validation references.

### PPS metrology and firmware diagnostics

- Align raw PCPS/PCSW through shared TCB0 ranges instead of parser epoch IDs;
  reject ambiguous wrap/reset matches and keep PPS gaps separate from resets.
- Use a common centred quadratic PPS phase fit (61-second configurable window)
  for offline calibrated swings; retain nominal raw durations and gap masks.
- Retain fractional fast/slow firmware frequency state so small oscillator
  errors accumulate. Add separately tested host replay of actual discipliner calls.
- Make acquisition exclusively captured CSW/CPS boundaries; calculate duration
  and clock calibration on the host. Protocol v3 advertises CSW v2 (nine fields)
  and CPS v1. Firmware swing rows contain only sequence and capture timestamps.
- Emit complete CFG metadata, including PPS schema and firmware identity.
- Embed figures in HTML reports and restore PPS summary sections in the suite.

### Current analysis command

`pendulum-analyze` now runs the clock and swing suite, with PCPS required and
PCSW optional. Its output defaults to `RUN/analysis_suite/report.html`; see
[Clock and swing analysis](Docs/Clock_Swing_Analysis.md) for current options and
outputs. The artifact-registry entry below records an earlier implementation
stage and is not the current command contract. Its retained regression harness
is `python -m pendulum_analysis.historical_cli`; `--flat-output-aliases` is no
longer accepted by either entry point.

### Historical artifact registry and hierarchical analysis output

The following records an earlier implementation stage. It is retained as change
history; the current command and output contract is linked above. Statements
about defaults and compatibility aliases below apply only to that earlier stage.

Changed files:

- `pendulum_analysis/artifacts.py`
- `pendulum_analysis/cli.py`
- `pendulum_analysis/canonical_v2.py`
- `pendulum_analysis/plots.py`
- `tests/test_analysis.py`
- `README.md`
- `CHANGELOG.md`

New directory layout:

- `report/canonical_report.md`
- `report/manifest.json`
- `pcps/{plots,csv,summary}/`
- `pcsw/{plots,csv,summary}/`
- `combined/{plots,csv,summary}/`
- `metadata/config.json`
- `metadata/provenance.json`
- `metadata/hashes.json`

Registry API:

- Added `ArtifactRegistry` with `register_plot`, `register_csv`, `register_summary`, `register_report`, and `register_metadata`.
- Registry owns slug sanitization, namespace/type validation, directory creation, relative paths, namespace-local numbering, and duplicate registration checks.
- Plot IDs use `PCPS-01`, `PCSW-01`, and `COMB-01` style IDs. Non-plot IDs include the artifact type, for example `PCSW-CSV-01` and `META-META-01`.

Manifest format:

- `report/manifest.json` uses schema `pendulum_analysis.artifacts.v1`.
- Each artifact entry contains `id`, `namespace`, `artifact_type`, `slug`, `title`, `section`, `relative_path`, `source_files`, `analysis_name`, `created_at`, and optional `description` / `caption`.

Report-reference changes:

- `report/canonical_report.md` references figures by stable ID in prose, for example `Figure PCSW-01`.
- Markdown image links now point to registered relative paths such as `../pcsw/plots/01_full_cycle_timing_overview.png`.
- The generated report no longer relies on stale flat plot filenames.

Backward compatibility:

- The default output is the new hierarchy.
- Passing `--flat-output-aliases` writes root-level compatibility copies using the previous flat filenames.
