# Pi recording format and recovery

The Pi keeps the Nano's capture values intact and adds environmental readings.
It does not replace captured counters with serial-arrival times or apply its
OLED/PPS estimates to the recorded measurements. These file formats belong to
this Pi producer: the laptop capture tool and retained Uno receiver have their
own output contracts. Wire-field meanings remain
owned by the [protocol contract](../Docs/Protocol_Wire_Contract.md).

## UTC and capture time

The selected [timekeeping design](TIMEKEEPING.md) uses GPS NMEA plus Pi GPIO PPS
with chrony, and network NTP as fallback. This disciplines the Pi wall clock;
it does not replace the Nano's capture counters or make USB receipt timestamps
exact event timestamps. Mapping Nano events to UTC requires verified association
with the common PPS stream; that mapping is not added by this documentation.

Host `epoch` values and date-based session/segment names reflect the Pi clock at
the time of writing. Before synchronisation, or after losing all time sources,
they may be inaccurate. Segment age uses monotonic elapsed time, so wall-clock
corrections do not themselves cause rotation.
Session-relative monotonic times are independent of wall-clock adjustments.
Existing records are not rewritten when UTC becomes available. CSV headers and
capture fields remain unchanged. A separate dashboard health check now reads
chrony's selected source and tracking state. Acquisition health and recording
manifests alone still do not prove UTC synchronisation or Nano event UTC timing.

## Directory layout

The data root also contains `observatory-history.sqlite3`, a separate versioned
store of sampled display estimates. It is not part of raw recording sets,
verified archives, `SUMMARY.CSV`, or date-range raw-file exports.

```text
data_dir/
├── last_session.json
└── 20260926T120000-<unique-id>/
    ├── segment-000001/
    │   ├── PCSW.CSV
    │   ├── PCPS.CSV
    │   ├── STS.CSV
    │   ├── PI.CSV
    │   ├── RAW.jsonl
    │   ├── SUMMARY.CSV
    │   └── manifest.json
    └── segment-000002/
        └── ...
```

A new acquisition service run gets a unique session. Reconnection, capture
sequence restart/reordering, changed metadata and recovery from receiver queue
overflow can create further sessions. Each session has an independent receiver
monotonic time origin. `last_session.json` links successive sessions; it is a
convenience pointer, not a measurement file.

Within a session, all recording files rotate together when any file reaches
128 MiB, the combined files reach 256 MiB, or seven monotonic days elapse
(defaults). Checks run between received events; one event plus closing rows can
slightly exceed a threshold. Midnight does not itself rotate a segment. Rotation does not reset the Nano,
the capture sequence or the session time origin. Metadata accompanies every
segment. After a recording error, recovery creates a new segment and never
appends to the potentially damaged tail of the failed one.

## Measurement CSVs

Headers match the representative archived files in `Data/20260923_multiday_0/`.
CSV row order preserves accepted serial receipt order within each capture family.

`PCSW.CSV`:

```csv
seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing,adj_diag,adj_comp_diag,temperature_C,humidity_pct,pressure_hPa
```

`PCPS.CSV`:

```csv
seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps,temperature_C,humidity_pct,pressure_hPa
```

The nine current CSW fields and eight current CPS fields retain their original
unsigned integer values, including 32-bit wraps and Nano drop counters. There is
no host-side correction hidden in these fields. Identical sequence numbers are
counted as duplicates and are not written again. Forward sequence gaps are
recorded as missing measurements; a backward/reordered sequence triggers rejoin.

The legacy PCSW columns `adj_diag` and `adj_comp_diag` remain in their archived
positions but are **empty**. Current protocol v3 does not emit them. They are
neither zero nor reconstructed diagnostics. This retains the archived CSV shape
without inventing firmware observations.

Environmental values are formatted to two decimal places:

| Column        | Source | Unit            |
| ------------- | ------ | --------------- |
| temperature_C | SHT4x  | Degrees Celsius |
| humidity_pct  | SHT4x  | Percent RH      |
| pressure_hPa  | BMP280 | Hectopascals    |

The sensor worker caches the last accepted readings with independent last-good
ages for SHT4x and BMP280. The default sample interval is one second and the
freshness limit is ten seconds. A cached value can be reused within that limit,
even if a subsequent read failed; the failure remains visible in sensor health.
Missing, disabled, non-finite or stale readings become the literal `nan` in CSV
and `null` in live JSON. Failure of one device does not automatically erase the
other device's last-good data.

A sensor reading taken after a serial record arrived is not attached to that
record. When processing is delayed and only a newer sensor snapshot remains,
the affected environmental fields are `nan`; there is no historical sensor
interpolation. Capture continues through sensor failures. Demo mode deliberately
uses simulated environment data and identifies its synthetic source. Replay
without an active sensor worker normally has missing environment data.

For analysis, keep both CSVs together in a segment directory and run
`pendulum-analyze PATH/TO/SEGMENT` from an environment containing the analysis
package. Gzip files and a collection/session directory or `catalogue.json` are
also accepted. Contiguous segments are assembled automatically within each
session; unknown gaps and session changes remain separate reports. A useful result needs enough real PPS/swings; metadata-only or short
recovery segments may not provide enough data. Do not concatenate sessions
blindly across restart/wrap boundaries.

The standalone PPS command
`python -m pendulum_analysis.pps.historical_main PATH/TO/RECORDINGS` uses this
same assembly layer and accepts collections/session directories, `catalogue.json`
and plain/gzip segments. It requires PCPS only; PCSW is optional. A receiver
session boundary is a Pi acquisition/recovery boundary, not proof of a Nano
restart. Neither suite currently joins separate sessions using the
`previous_session` link, because that link alone does not establish continuity.

## Nano status and Pi diagnostics

`STS.CSV` preserves the existing receiver shape:

```csv
ts_ms,raw
```

`raw` is the whole accepted Nano `STS,...` line without its line ending, properly
CSV-quoted as one field. `ts_ms` is elapsed receiver-session monotonic
milliseconds at receipt, not Nano uptime or a capture counter. Invalid/non-ASCII
input remains in `RAW.jsonl` rather than being treated as valid Nano status.

`PI.CSV` replaces receiver-specific `UNO.CSV` with Pi diagnostics:

```csv
ts_ms,epoch,category,key,value,msg
```

`epoch` is host UNIX time in seconds; it is not the Nano's reset epoch. These
host wall-clock values can change as the Pi obtains or corrects network time.
Use Nano captured counters for metrology and session-monotonic values for host
elapsed time. Both host clocks are diagnostic metadata.

Pi diagnostic categories cover session/recovery, serial connectivity, metadata
readiness, rejected records, sequence gaps/restarts, Nano drop-counter changes,
receiver queue loss, command transmission, periodic health and service shutdown.
A health snapshot is normally emitted every 30 seconds and includes transport,
capture counters, logging, per-device environmental health, chrony time status
and gpsd receiver diagnostics (fix, satellites and report ages). Live status is
published about once a second. `unrecorded` counts unsuccessful output-write
attempts across output families, not just missing captures; the separate
`storage_lost_CSW`/`storage_lost_CPS` counters identify captures not recorded due
to storage failure. Intentionally disabled recording is counted separately.

## Raw evidence and manifest

Each line in `RAW.jsonl` is an object with:

| Key      | Meaning                                                           |
| -------- | ----------------------------------------------------------------- |
| ts_ms    | Receiver-session elapsed monotonic milliseconds at receipt        |
| epoch    | Host UNIX seconds at receipt                                      |
| raw_hex  | Original bytes in hexadecimal, including received line endings    |
| text     | ASCII convenience view, with replacement characters for bad bytes |
| fragment | True for partial, oversized or initial mid-stream framing data    |

`raw_hex` is authoritative when text decoding is lossy. Raw input is written
before validation, so pre-readiness records, malformed data and rejected tags
are retained when storage and the receive queue permit. Oversized lines are
split into bounded fragments, rather than allowing unbounded memory growth.
On a serial attachment the first received line is conservatively treated as a
fragment, because it may begin mid-record.

`manifest.json` records receiver/format versions, session/segment identity and
reason, the previous session, configuration without the administrator token,
validated CFG/schema metadata, CSV columns, missing legacy diagnostic fields,
start/last-receipt timestamps and cumulative service write/loss counters. Clean
closure adds a closure time and reason. Missing closure metadata can indicate
an interrupted run; a manifest is not a guarantee that every attempted write
survived a storage or power failure.

## Minute summary CSV

`SUMMARY.CSV` is produced by [summaries.py](pendulum_pi/summaries.py) from
successfully recorded captures. It is independent of both offline analysis and
the observatory history database. One row describes one `CSW` or `CPS` stream's
session-relative minute bin or segment fragment. The 41 columns, in file order,
are:

| Position | Column                     | Meaning / unit                                                              |
| -------- | -------------------------- | --------------------------------------------------------------------------- |
| 1        | `session`                  | Receiver session identifier                                                 |
| 2        | `segment`                  | Integer recording segment number within session                             |
| 3        | `stream`                   | `CSW` or `CPS`                                                              |
| 4        | `bin_start_ms`             | Inclusive session-monotonic bin start, a multiple of 60000 milliseconds     |
| 5        | `bin_end_ms`               | Exclusive nominal bin end, always start + 60000 milliseconds                |
| 6        | `first_ts_ms`              | First recorded capture's session-monotonic receipt milliseconds in fragment |
| 7        | `last_ts_ms`               | Last recorded capture's session-monotonic receipt milliseconds in fragment  |
| 8        | `first_host_epoch`         | First capture's host receipt Unix seconds                                   |
| 9        | `last_host_epoch`          | Last capture's host receipt Unix seconds                                    |
| 10       | `partial`                  | Integer 1 for a split/early-flushed fragment, otherwise 0                   |
| 11       | `capture_count`            | Recorded captures observed in this fragment                                 |
| 12       | `missing_sequences`        | Sum of forward sequence gaps minus one, relative to previous recorded row   |
| 13       | `duplicate_sequences`      | Count of repeated sequence values seen by summariser                        |
| 14       | `sequence_restarts`        | Count of modulo sequence steps at least 2^31 seen by summariser             |
| 15       | `baseline_unknown`         | Count of captures with no previous recorded row for this stream/session     |
| 16       | `drop_ir_increase`         | Sum of forward modulo-2^32 IR drop-counter changes                          |
| 17       | `drop_pps_increase`        | Sum of forward modulo-2^32 PPS drop-counter changes                         |
| 18       | `drop_swing_increase`      | Sum of forward modulo-2^32 swing drop-counter changes                       |
| 19       | `drop_counter_resets`      | Number of individual counter comparisons with modulo delta at least 2^31    |
| 20       | `gps_unlocked_count`       | CPS captures with `gps_status != 2`; zero for CSW                           |
| 21       | `period_invalid_count`     | Captures failing this summariser's nominal-period eligibility               |
| 22       | `period_nominal_us_count`  | Number of eligible nominal-period values                                    |
| 23       | `period_nominal_us_mean`   | Mean nominal period in microseconds                                         |
| 24       | `period_nominal_us_min`    | Minimum nominal period in microseconds                                      |
| 25       | `period_nominal_us_max`    | Maximum nominal period in microseconds                                      |
| 26       | `period_nominal_us_stddev` | Population standard deviation of nominal period, microseconds               |
| 27       | `temperature_C_count`      | Number of finite attached SHT4x temperature observations                    |
| 28       | `temperature_C_mean`       | Capture-weighted temperature mean, degrees Celsius                          |
| 29       | `temperature_C_min`        | Minimum temperature, degrees Celsius                                        |
| 30       | `temperature_C_max`        | Maximum temperature, degrees Celsius                                        |
| 31       | `temperature_C_stddev`     | Population standard deviation of temperature, degrees Celsius               |
| 32       | `humidity_pct_count`       | Number of finite attached SHT4x humidity observations                       |
| 33       | `humidity_pct_mean`        | Capture-weighted relative humidity mean, percent                            |
| 34       | `humidity_pct_min`         | Minimum relative humidity, percent                                          |
| 35       | `humidity_pct_max`         | Maximum relative humidity, percent                                          |
| 36       | `humidity_pct_stddev`      | Population standard deviation of relative humidity, percentage points       |
| 37       | `pressure_hPa_count`       | Number of finite attached BMP280 pressure observations                      |
| 38       | `pressure_hPa_mean`        | Capture-weighted pressure mean, hectopascals                                |
| 39       | `pressure_hPa_min`         | Minimum pressure, hectopascals                                              |
| 40       | `pressure_hPa_max`         | Maximum pressure, hectopascals                                              |
| 41       | `pressure_hPa_stddev`      | Population standard deviation of pressure, hectopascals                     |

Counts are nonnegative integers. For each statistic group, zero observations
produce count `0` and four **empty CSV cells**, not zeros. One observation has
standard deviation zero. Standard deviation is `sqrt(sum((x-mean)^2)/n)`, using
population denominator `n`, not sample denominator `n-1`. Environmental statistics
include every finite value attached to a recorded capture, even when its period
is ineligible. Cached sensor readings may therefore be counted repeatedly, with
separate weighting for each stream. The summariser uses the attached numerical
values before the two-decimal formatting of measurement CSVs, so recomputing from
rounded CSV values may differ slightly.

Sequence/drop baselines carry across ordinary segment rotation within a session.
An initial drop total has no preceding observation and is not counted as an
increase. A modulo drop delta below 2^31 is accumulated; a delta at least 2^31
is counted as a reset instead. CPS has only `drop_pps`; its IR/swing totals remain
zero. Current acquisition filters duplicate captures and starts a new session on
sequence restart/reordering before recording, so duplicate/restart summary counts
normally remain zero. They describe the summariser's inputs, not all rejected
wire records; use `PI.CSV` and raw evidence for receiver rejection diagnostics.

Period eligibility is intentionally limited:

- CSW sums four unsigned-32-bit differences between its five edges. Every
  interval must be nonzero and their sum must be below 2^31 ticks. An initial
  CSW or one after a forward sequence gap can qualify because its period is
  contained within that one capture.
- CPS requires a preceding recorded CPS, a sequence step of exactly one, no
  change in its drop counter, both `gps_status` values equal to 2, and an
  unsigned edge difference between 0.9 and 1.1 times nominal `nhz`, inclusive.
- Both streams require `nhz >= 1000`, nonzero ticks, neither a duplicate nor a
  restart sequence classification, and no nonzero change in any available
  drop counter. A detected drop reset also prevents eligibility.

Eligible ticks are multiplied by `1000000/nhz`. This is nominal-counter timing,
not a PPS-calibrated pendulum period or an oscillator fit. These summaries do not
check `now32-edge_tcb0 == latency16` for PPS reconstruction coherence; offline
PPS analysis additionally checks reconstructed rows/pairs and uses its configured
cadence and calibration policies. Summary eligibility and counts must not be
substituted for offline analysis eligibility or Pi display estimator health.

A new capture in a later minute flushes the prior bin for that stream. Closing a
segment flushes outstanding bins; `partial=1` if closure occurs before nominal
bin end, or the bin starts before the previous segment's summary flush time.
Thus adjacent segments may have separate rows for the same stream/minute. The
nominal bin boundaries remain a full minute; first/last capture times and counts
show the observed fragment. `partial=0` does **not** prove full capture coverage.
Empty minutes produce no rows, and an open minute is not yet present on disk.
Failed writes or interrupted closure can leave its summary absent even when some
capture rows survived. Compare captures, quality counters and segment manifests
before combining fragments; means and standard deviations cannot be averaged
without their counts and appropriate pooled-moment calculations.

## Manifest and catalogue metadata

These are Pi recording/storage schemas, not revisions of the Nano wire protocol.
The current producers write the following version identifiers:

| Artifact                   | Version field    | Current value | Owner / purpose                                                   |
| -------------------------- | ---------------- | ------------- | ----------------------------------------------------------------- |
| Segment `manifest.json`    | `format_version` | 2             | Recorder's segment identity, configuration and capture provenance |
| Data-root `catalogue.json` | `format_version` | 1             | Storage worker's completed local recording sets                   |
| Segment `lifecycle.json`   | none             | none          | Internal mutable storage state, embedded in catalogue segments    |
| Archive `archive.json`     | `format_version` | 1             | Archive identity and physical member size/SHA-256 inventory       |
| Export `export.json`       | `format_version` | 1             | Requested range, selected sets, retained/unavailable files        |

Manifest fields are produced by [recording.py](pendulum_pi/recording.py).

| Field(s)                                  | Meaning                                                                                           |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `format_version`, `receiver_version`      | Recording format and application version; neither is the Nano protocol version                    |
| `session`, `segment`, `previous_session`  | Current session/segment and prior-session pointer (possibly null)                                 |
| `reason`                                  | Reason this receiver session was created                                                          |
| `started_utc`                             | Segment opening wall-clock timestamp                                                              |
| `first_record_utc`, `last_record_utc`     | First/last successfully recorded raw input or capture receipt timestamps                          |
| `min_record_utc`, `max_record_utc`        | Receipt-time extrema, preserving wall-clock excursions between endpoints                          |
| `first_record_ts_ms`, `last_record_ts_ms` | Corresponding first/last receipt times on the session-monotonic axis                              |
| `host_clock_reversed`                     | Whether a subsequent recorded receipt had an earlier wall-clock time                              |
| `host_clock_discontinuities`              | Count of adjacent receipt clock-delta disagreements greater than one second                       |
| `contract`                                | Validated CFG/schema snapshot; can be empty before metadata readiness                             |
| `settings`                                | Shared settings snapshot with `api_token` removed                                                 |
| `columns`                                 | Ordered headers for each CSV, including all 41 summary fields                                     |
| `files`                                   | Per logical file: original uncompressed `bytes` and data `rows` (header bytes count, rows do not) |
| `written_since_service_start`             | Successful CSW/CPS capture-write totals across service sessions, not this segment's rows          |
| `unrecorded_since_service_start`          | Failed/disabled write-attempt total across output families, not a capture-only count              |
| `missing_legacy_columns`                  | PCSW placeholders `adj_diag`, `adj_comp_diag`                                                     |
| `time_semantics`, `summary_semantics`     | Human-readable interpretations of receipt times and derived minute statistics                     |
| `closed_utc`, `closed_reason`             | Added only after successful segment close/sync; storage publication boundary                      |

Receipt coverage fields can be null before data arrives. Diagnostic/status rows
alone do not update them; raw records, including malformed/pre-readiness input,
do. The discontinuity counter compares adjacent host-epoch elapsed time against
session-monotonic elapsed time. It does not establish external UTC accuracy.
During recording the manifest is a periodically refreshed snapshot. The cleanly
closed manifest is then immutable; storage does not rewrite it when compressing
or expiring files. Its original file inventory must therefore not be mistaken
for current local availability.

The catalogue root has `format_version`, `updated_utc` and `segments`. Each
segment contains `id` and `path` (both `SESSION/segment-NNNNNN`), `session`,
`segment`, duplicated coverage fields, the full closed `manifest`, current
`files`, and `archive` state. Each `files` key is a logical recording filename;
its record contains:

| File record field      | Meaning                                                                      |
| ---------------------- | ---------------------------------------------------------------------------- |
| `path`                 | Relative local plain or gzip path                                            |
| `compressed`           | Whether the physical representation is gzip                                  |
| `bytes`, `sha256`      | Current physical representation's byte length and SHA-256                    |
| `uncompressed_bytes`   | Original byte length                                                         |
| `uncompressed_sha256`  | Original content SHA-256                                                     |
| `rows`                 | Original data-row count copied from the closed manifest, or null             |
| `expired`              | Local file was intentionally expired; its provenance remains                 |
| `fingerprint`          | Internal filesystem identity/change-detection cache; not a portable checksum |
| `compression_deferred` | Null or explanation for deferring compression                                |

`archive` starts with `verified: false`. Verified offload records `verified: true`,
`immutable: true`, absolute destination `path` and `verified_utc`; revalidation
failure clears verification and adds `error` while preserving representation
immutability. Expiry may add further lifecycle timestamps. The archive's separate
`archive.json` records its identity and each physical member's `sha256`/`bytes`,
including the copied manifest. See [STORAGE.md](STORAGE.md) for verification,
expiry and retirement policy, and [HTTP_API.md](HTTP_API.md#catalogue-and-exports)
for exported selection metadata and API projections.

Read actual CSV headers and named metadata fields, preserve unknown fields when
copying provenance, and do not infer file presence from the original manifest.
Current storage/export readers validate selected structures and identities; they
do **not** uniformly reject unknown `format_version` values or negotiate schemas.
There is no implemented automatic migration of arbitrary future versions. Older
metadata can lack receipt extrema and newer fields: export coverage falls back
to available opening/closing/last-receipt bounds, and some legacy storage paths
allow manifests without the current inventory. Missing older fields do not prove
zero loss or stable UTC. Treat the versions and available fields as provenance,
and verify compatibility before using another producer's metadata. The internal
lifecycle file is storage-worker state, not an independently versioned public API.

## Readiness, gaps and failures

The serial owner requests `emit meta` until CFG and both schemas validate. It
preserves pre-readiness raw/status evidence but does not write capture rows
under an unverified contract. Identical metadata replays are idempotent. Changed
or invalid metadata and unknown tags require recovery; a malformed capture row
is rejected individually. No Nano reset is sent merely to rejoin its stream.

There is a bounded **8192-event** receive queue by default while the application
runs. It helps absorb temporary disk stalls, but is not a durable spool. Queue
overflow is counted, marks a continuity break and forces metadata recovery.
Measurements arriving while the Pi is rebooting, disconnected or otherwise not
receiving are lost. **Nothing buffers them across a Pi reboot, and the receiver
does not fabricate or backfill them.** It records the available interruption
and recovery evidence; it cannot know every missing measurement's timestamp.

A serial reconnect or ambiguous command outcome does not authorize retrying a
Nano mutation. An uncertain acknowledgement timeout blocks further dispatch
until the UART reconnects and metadata is validated again; it does not reset
the Nano. Consult the command result and inspect current Nano settings before
deciding to submit another command. Host command acknowledgements have
no wire transaction ID. For mutating commands whose firmware success path saves
EEPROM, the successful acknowledgement follows firmware readback verification;
the Pi does not independently read EEPROM back. A timeout still leaves execution
and persistence uncertain. See [command handling](HTTP_API.md#nano-commands).

## Durability and storage policy

Writes go directly to the operating system, with an `fsync` interval of two
seconds by default and an explicit sync on clean closure. The interval is
configurable from 0.1 to 60 seconds. Visibility to a browser is not proof of
physical-media durability; abrupt power loss can lose recent writes and damage
the final row/filesystem. Actual SD-card behaviour must be tested on the Pi.

Free space is checked before opening a segment and about every five seconds.
The default reserve is **1 GiB**, alongside an **8 GiB** recording budget. Low space or a write/sync failure stops
recording, exposes an error, and closes the current segment; the application
tries to open a new segment after a delay when storage permits. The separate storage worker can expire verified archived files under the
configured retention/budget policy. Sole unarchived copies are preserved. See
[storage lifecycle and archive setup](STORAGE.md). Serial reception and loss
accounting can continue while disk recording is unavailable, but missed disk
records are not replayed later.

Keep `RAW.jsonl`, `STS.CSV`, `PI.CSV` and the manifest with the measurements when
investigating a problem. If the runtime/status location itself becomes
unwritable, service supervision may restart acquisition; the web interface must
then treat its previous status snapshot as stale.

## Downloads during recording

The web service snapshots an individual file's length and streams only that
prefix. For CSV/JSONL it trims an incomplete final line. Appending continues
independently, so an active download does not include rows appended after its
snapshot. At most two downloads run concurrently; additional requests receive a
retryable response. Downloads refuse traversal, symlinks and unapproved names.

Snapshots are per file, **not an atomic multi-file export**. Download completed
segments for a stable set of CSVs and metadata, or retain each active file's
download timing when comparing counts. Downloading never truncates or deletes
the on-disk recording.

## Compression, summaries and date-range exports

[Minute summaries](#minute-summary-csv) contain derived statistics keyed by receiver
session and elapsed time, including partial-minute boundaries and quality counts.
Their timing statistics use nominal counters, not offline PPS calibration. Closed
recording files may become `.gz` files after verified compression. A local
catalogue records completed sets and retained/expired file availability.

Date-range exports package whole overlapping completed segments with manifests
and export provenance. Selection uses host-recorded time, not verified event
UTC. Measurements, summaries and full diagnostic packages are separate choices.
See [STORAGE.md](STORAGE.md) for limits, retention and analysis instructions.

## Observatory display history

`observatory-history.sqlite3` stores a versioned stream of derived display
observations. Its SQLite `user_version` and history response `schema_version`
are currently `3`. The writer initializes version `0` databases and upgrades
versions `1` and `2` in place, adding window-series columns where needed; other
versions except `3` are rejected. History reads require version `3`. Older
observations are not reconstructed into the new window estimates. Legacy median
fields are removed from returned points; missing window values become null and
missing window-learning flags become true.
This is separate from raw-recording manifest and Nano wire versions.
The independent analysis worker owns the 600-second swing mean and reads only
committed `ANALYSIS.jsonl` inputs. It saves full causal model state, replay cursor
and derived history/minute summaries atomically in SQLite. Processing delay is
not an observation timestamp. The acquisition process has no estimator or
history queue; it records inputs and publishes capture health. See
[architecture](ARCHITECTURE.md) for the durable frontier and restart contract.
Routine observations are persisted every ten seconds; observed settings,
timebase, UTC quality, sensor validity, capture/reset and recording transitions
are persisted immediately at the next capture observation. Chrony diagnostic
transitions and short status-publication delays (3–30 seconds) retain measurement
continuity; longer gaps and clock corrections split it. `continuity` carries
independent `timing`, `sht4x` and `bmp280` trace IDs, while `segment` still defines
the combined measurement continuity used by environmental regressions.

Each point includes the mean period, derived rate when
a target exists, learning flags, sensor readings/freshness, the latest captured
swing identity, source, capture session, history session, monotonic elapsed time,
host observation time, settings and quality. These are sampled capture-time display
estimates, not every raw swing, exact event UTC, or offline PPS recalibration.
The API preserves the existing full-swing BPM convention; the dashboard labels
it “full swings/min”. Rate is `86400 * (target_period / period - 1)` seconds/day,
positive for gaining time. Changing the target does not reset period estimates.
The current mean averages each accepted swing's four component durations over
600 seconds of captured swing duration. Each observation is calibrated with the
qualified PPS dual-EWMA frequency available at receipt. Earlier observations
are not recalibrated by later PPS values; the PPS 600-sample mean remains a
separate diagnostic. Values require qualified calibration and remain provisional
until 600 seconds of fresh captured swings fill the window. Estimator resets
clear the swing window. After expired PPS calibration, old observations may be
retained on resumption but the mean must refill with 600 fresh captured seconds
before it is mature. Missing time never contributes to filling. A recent
`swing-mean-checkpoint.json` sidecar can restore individual calibrated samples
following a short service/Pi restart. Samples expire at 600 seconds of verified
age; fresh clock qualification remains required. `quality.window_priming`
records restoration status and age alongside the learning flag. The sidecar is
bounded, replaced atomically off-thread, included in overall disk accounting,
and never changes canonical raw captures or previously stored mean values.

New observations populate `window_period_s` and `window_rate_s_day`; the six
legacy EWMA series remain null. Schema 3 retains older EWMA values unchanged
for explicit historical queries. Earlier ratio-of-means window observations also
remain unchanged. Settings include `estimator_model` to distinguish
`swing_mean_600s_pps_dual_ewma_v1` from older window estimates; old points without
an identity use `legacy_mean_calibration_unspecified`. A model change creates a
continuity/configuration boundary. Neither historical EWMAs nor older raw captures
are reconstructed into the new mean. History stores sampled mean values and
extrema for reduced long-range queries.

Quality additionally records `calibration_age_seconds` and
`holdover_limit_seconds`; older points may omit these fields. `HOLDOVER` points
use the last qualified calibration and retain mean observations. Transitions
into and out of holdover create quality boundaries, not estimator resets.
Expired calibration produces gap points until valid PPS resumes. The
`pps_holdover_seconds` setting is retained with each new point. Raw PCPS.CSV and
RAW.jsonl contain rejected captures unchanged; no calculation filter, holdover,
or history reduction is applied to those files.

Recording pauses save a gap marker rather than continued measurements. Writer
errors, queue overflow, stale capture, settings changes, estimator resets,
session boundaries and host clock steps split continuity segments. Raw segment
rotation alone does not split an otherwise continuous estimate. Missing sensor
values remain null. Each chart joins points only within one continuity segment
and history session. Selecting elapsed time is limited to a single history
session; all-session host-time order is explicitly uncertain when UTC is unverified.

The sidecar defaults to a 30-day maximum age and 256 MiB budget. Size limits can
expire points earlier; 30 days is not guaranteed. SQLite uses write-ahead logging
(WAL), `synchronous=NORMAL`, and periodic checkpoints. Page limits and journal
headroom constrain disk use; the worker prunes only its own derived store. For raw
recording priority it conservatively reserves an additional full history budget
plus 1 MiB against recorder-reported total usage, and 1 MiB above the configured
filesystem free-space reserve. History pauses when that headroom is unavailable.
Existing raw retention, rotation, summaries and verified archives are unchanged.
Old raw recordings are not automatically imported into this sidecar.

`GET /api/history` accepts Unix-second `start`/`end`, optional `session`, optional
comma-separated `series`, and `max_points` from 32 to 2000. Requests are limited
to 366 days, two concurrent queries and a bounded query execution time. Default
selection is the last hour. Indexed minute extrema support larger selections;
the response reports reduction and any point-budget limitation. In very fragmented
ranges the budget may omit extrema/segments; narrow the selection for detail.
All returned points keep continuity and configuration provenance. These minute
extrema are internal chart indexes and are unrelated to recording `SUMMARY.CSV`.

The eleven selectable `series` names and their units are:

| Series                 | Unit / meaning                                       |
| ---------------------- | ---------------------------------------------------- |
| `short_period_s`       | Legacy short EWMA full period, seconds               |
| `long_period_s`        | Legacy long EWMA full period, seconds                |
| `short_rate_s_day`     | Short estimated gain/loss, seconds per day           |
| `long_rate_s_day`      | Long estimated gain/loss, seconds per day            |
| `short_block_delta_us` | Short tick-block minus tock-block mean, microseconds |
| `long_block_delta_us`  | Long tick-block minus tock-block mean, microseconds  |
| `temperature_C`        | Attached fresh temperature, degrees Celsius          |
| `humidity_pct`         | Attached fresh relative humidity, percent            |
| `pressure_hPa`         | Attached fresh station pressure, hectopascals        |
| `window_period_s`      | Stored 600-second mean period, seconds               |
| `window_rate_s_day`    | Corresponding estimated gain/loss, seconds per day   |

Omitting `series` selects all eleven. An explicit HTTP selection currently
accepts at most nine names, so omit it to request the complete set. Selection controls which series' extrema are
retained during reduction and removes unselected numerical series from returned
points; continuity/quality/settings metadata remains. Missing values stay null.
The current browser plots the stored window series. Historical blended fits
can still be derived from retained short/long periods; there is no stored or
selectable `blended_period_s` series. Query parameters, limits and
errors are specified in [HTTP_API.md](HTTP_API.md#history-queries).

### Environmental relationships

`GET /api/history/environment` accepts finite Unix-second `start`/`end` and an
optional `session`. The dashboard uses this endpoint for temperature, relative
humidity and pressure individually and together. It requires no new stored series
or database migration. The older temperature endpoint below remains available
for historical clients.

At each observation time, the response pairs the stored tracking
`window_period_s` (600-second full-swing mean) with trailing 600-second means of
`temperature_C`, `humidity_pct` and `pressure_hPa`. These environmental means
are sample averages of retained fresh observations, not individual sensor reads
or duration-weighted integrals. History normally samples approximately every ten
seconds. Environmental coverage must reach to within 30 seconds of the window's
start, with no inter-observation gap over 30 seconds or null environmental/gap
observation in the window. Period learning observations can contribute
**environmental** warm-up; only tracking, positive 600-second period values
supply regression responses. Windows never cross a session/continuity boundary.
These sampling and coverage policies approximate the same trailing window used
by the per-swing period mean; their underlying sampling rates differ.

All four values are then averaged together in epoch-aligned time buckets, at least
60 seconds wide and rounded up to a whole minute for approximately 600 buckets
across the selected range. A 24-hour selection uses three-minute buckets. Partial
buckets report actual sample counts. Empty buckets are omitted, never filled.
All individual fits and the joint model use these **same complete paired bucket
means**, each equally weighted, within one selected uninterrupted segment.

For bucket b the individual models are `P_b = alpha + beta * X_b + error_b`,
with X respectively temperature, humidity and pressure. The combined model is
`P_b = alpha + beta_T*T_b + beta_H*H_b + beta_B*B_b + error_b`.
Period is the response (left-hand side); environment supplies the predictors
(right-hand side). Centred period deviations are expressed in microseconds and
predictors are centred/scaled internally. Slopes are converted back to µs/°C,
µs per percentage point of relative humidity, and µs/hPa. Adjusted slopes include
the other two predictors in the joint model; no causal interpretation or delay
is applied.

The result contains `bucket_seconds`, `environmental_window_seconds` (600),
`coverage_tolerance_seconds` (30), and `segments`. Each segment includes its
source, settings, timebase, bounds, sample count, `spans`, paired `points`,
`individual` fits keyed by the environmental series names, and a `joint` fit.
Individual fits report `slope_us_per_unit`, `mean_x`, `mean_period_s`,
`r_squared`, `residual_rms_us`, and `uncertainty` with `se_us_per_unit` and
`ci95_us_per_unit`. Uncertainty metadata otherwise follows the temperature
endpoint below. Constant predictors leave their individual fit unavailable.

At least five paired averages are needed for a joint fit. It reports
`r_squared`, `adjusted_r_squared`, `residual_rms_us`,
`temperature_residual_rms_us`, `residual_reduction_pct` relative to temperature
alone on the same points, `predictor_rank`, `condition_number`, `condition_limit`
(30), `coefficients_available`, `coefficients`, and `uncertainty`.
Each coefficient contains `slope_us_per_unit`, `se_us_per_unit`,
`ci95_us_per_unit`. Points also include `fitted_period_s` and `residual_us`.
Constant response has null R²; an unavailable temperature fit or zero temperature
residual RMS leaves the relative reduction null. These describe in-sample fit,
not performance on unseen data. No readings or clock-rate estimates are corrected.

A three-by-three spectral decomposition of the scaled predictor correlation
matrix detects rank deficiency (eigenvalues at or below 1e-10 times the largest
are omitted). Rank-deficient models retain their fitted response through a
spectral pseudoinverse, but all adjusted slopes are withheld. The same applies
when the scaled design condition number exceeds 30. These thresholds are explicit
display policies for unstable/separable contributions. Fitted-history plots and
R² remain available even when separate slopes cannot be identified.

Joint uncertainty uses each slope's full-model influence score
`q_i = [(X'X)^-1 X_i]_j * error_i`, in original predictor units. The Bartlett
Newey–West sum below applies with `n/(n-rank-1)` in place of `n/(n-2)`.
The same lag, consecutive-bucket, minimum-history and residual-resolution gates
apply to all fits. Joint intervals are displayed only when all three resolve.
No IID fallback is provided. The normal-reference intervals are approximate and
conditional on a stable linear model; they do not account for sensor errors,
response delays or arbitrarily slow drift. The API retains the two-query limit,
four-second SQL budget, 366-day range limit and 2000-average cap. The browser
refreshes at most every 30 seconds and clears stale results on a failed refresh.

### Legacy temperature relationship

`GET /api/history/temperature` accepts Unix-second `start`/`end`, optional
`session`, and `estimate=window|short|long|blended` (default `window`).
The latter three selections read only retained historical EWMAs. It reads the
observations directly, independently of the chart extrema index. Temperature and
the selected positive period must be present in the same non-gap observation;
the selected estimator must be tracking, with both tracking for the blend.
The paired values are averaged within epoch-aligned buckets separately for each
session and continuity segment. The width is a whole number of minutes, at least
one minute and chosen for approximately 600 buckets across the requested range.
Partial buckets retain their actual observation counts; empty buckets are omitted.

The response includes `bucket_seconds` and `segments`, each with its paired
`points`, source, settings, timebase, covered times, observation count and `fit`.
Points contain `temperature_C`, `period_s`, average observation `time`,
epoch-aligned `bucket_start`, and `samples`. Each point has equal weight in an
ordinary least-squares fit with an intercept. The fit reports `slope_us_per_C`,
`r_squared`, and centred coordinates
`mean_temperature_C`/`mean_period_s` for drawing the line. Fewer than three points
or constant temperature make the fit unavailable with a reason; constant period
has a horizontal line and null R². Segments are never pooled into a single fit.

Available fits also report `residual_rms_us` (sqrt(mean squared residual), without
a degrees-of-freedom adjustment) and an `uncertainty` object. The latter includes
`available`/`reason`, `method`, `reference`, `confidence_level`, the relevant
recorded `smoothing_window_seconds` (600 for the mean) or historical
`half_life_minutes`, `lag_buckets`, `window_seconds`, `minimum_points`,
`slope_se_us_per_C`, `slope_ci95_us_per_C` (lower/upper), and `p_value`.
Unavailable uncertainty leaves its SE, interval and p-value null; it does not
hide the descriptive slope, R² or RMS.

For centred temperatures x and residuals e in µs, the slope influence is
`q_i = x_i * e_i / sum(x_i²)`. Newey–West slope variance is
`n/(n-2) * [sum(q_i²) + 2*sum_l((1-l/(L+1))*sum_i(q_i*q_(i-l)))]`.
This is the Bartlett HAC sandwich with an intercept and the usual n/(n-k)
correction for k=2. See the
[Statsmodels HAC reference](https://www.statsmodels.org/stable/generated/statsmodels.stats.sandwich_covariance.cov_hac.html).
It includes both residual correlation and unequal variance, rather than scaling
the ordinary standard error by a residual-only effective sample count.

For the mean, `L = max(floor(4*(n/100)^(2/9)), ceil(600/bucket_seconds))`.
For historical EWMAs the second term is
`ceil(5*half_life_minutes*60/bucket_seconds)`: the recorded half-life applies,
and the historical blend uses the maximum of both. The minimum-history policy
requires `n >= max(30, 5*(L+1))`. No lag cap forces short segments to qualify.
These explicit policies recognize overlapping windows and historical EWMA
memory; slower drift and shared calibration can add dependence beyond them.

Bucket identities must be consecutive and equally spaced; missing, duplicate
or reordered buckets withhold uncertainty rather than treating separated
observations as adjacent. No fallback to IID inference is used. Numerically
unresolvable residual scatter or nonpositive/unresolvable covariance also
withhold inference. Otherwise, `SE=sqrt(variance)`, the approximate 95% interval
is `slope ± 1.959963984540054*SE`, and the two-sided zero-slope p-value is
`erfc(abs(slope/SE)/sqrt(2))`. This is asymptotic normal inference, not a finite
sample Student-t test. Underflowed p-values are shown as `< 0.001`, never as proof
of a zero probability. Interpretation remains conditional on a stable linear
relationship and dependence fading within the window; no thermal lag, sensor
error model, nonstationary drift correction or causal inference is provided.

The endpoint shares the two-query history limit, permits at most 366 days, has a
four-second database execution budget, and returns at most 2000 paired averages
across all segments. Excessively fragmented ranges are rejected with a request
to narrow the range, rather than returning a fit on a silently truncated subset.
The browser refreshes this comparison at most every 30 seconds in live mode.
The temperature-comparison endpoint itself adds no stored series or database
migration and does not scan raw files; the separate window-series migration
is described above.

Initial and changed display-model settings are recorded as `display,configuration`
JSON diagnostics in `PI.CSV`, with model identities, revisions and the latest
accepted capture sequences. Each new segment records its current configuration.
The fixed-configuration replay tool does not automatically apply these events.

History, UTC collector status and GPS receiver status are included in periodic `PI.CSV` health
diagnostics. The sidecar is not offered as a raw-file download or included in
the existing date-range archive exports. Adjustment notes and comparison/export
features remain a later stage.
