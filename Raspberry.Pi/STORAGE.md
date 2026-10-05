# Recording storage, archives and downloads

Recording, retention and downloads use independent boundaries. The recorder
keeps each segment's measurements, diagnostics, raw evidence and minute summaries
together. The storage service compresses closed files, copies complete sets to
a configured archive, and expires eligible local files. The web service exports
selected completed sets without concatenating independent receiver sessions.

The observatory also maintains a separately capped display-history sidecar in
the data root. It counts toward total local usage, but is not a raw recording
set and is not copied into verified archives. Its worker expires only derived
history and leaves additional headroom for recording. See
[display history](DATA_FORMAT.md#observatory-display-history) for sampling,
retention, budget and time semantics.

## Defaults

The [configuration reference](CONFIGURATION.md) owns all setting types, ranges,
validation and live/restart behaviour. The table below summarises storage policy
defaults. API request and download contracts are in [HTTP_API.md](HTTP_API.md).

| Setting                      | Default | Meaning                                       |
| ---------------------------- | ------- | --------------------------------------------- |
| `file_bytes`                 | 128 MiB | Rotate the set when any file reaches this     |
| `segment_bytes`              | 256 MiB | Rotate when combined recording bytes reach it |
| `segment_seconds`            | 604800  | Rotate after seven monotonic days             |
| `storage_budget_mb`          | 8192    | Local data budget, in MiB                     |
| `min_free_mb`                | 1024    | Filesystem free-space reserve, in MiB         |
| `measurement_retention_days` | 90      | Target local retention after segment closure  |
| `diagnostic_retention_days`  | 14      | Target for raw, status and Pi diagnostics     |
| `summary_retention_days`     | 1825    | Target for minute summaries                   |
| `storage_interval_seconds`   | 60      | Maintenance interval                          |
| `compression_enabled`        | true    | Compress completed recording files            |
| `archive_dir`                | null    | Existing destination folder; setup required   |
| `export_max_mb`              | 256     | Maximum estimated export size, in MiB         |

Existing configurations retain explicit values, including an older 64 MiB
`segment_bytes` or 256 MiB free-space reserve. Missing settings receive defaults.
Adjust existing installations through the dashboard or configuration file.

Any rotation trigger advances the **entire set**, including `SUMMARY.CSV`.
Checks happen between received events so raw evidence and its parsed capture
stay together. An event and closing metadata/summary rows can slightly exceed
the thresholds. A rotation does not restart the receiver session or reset its
monotonic origin. A wall-clock correction does not itself trigger rotation.

## Background maintenance and failure policy

The separate `pendulum-storage` process handles completed segments. Segments
with an unconsumed analysis journal are protected until the durable SQLite
cursor acknowledges the committed frontier. `ANALYSIS.jsonl` follows measurement
retention rather than the shorter diagnostic retention. It never
compresses an active segment or treats an interrupted/failed closure as complete.
Compression uses a temporary file, verifies decompressed content against the
original hash, and publishes the gzip file before deleting the original.
Metadata retains session identity, original file sizes/counts and checksums.
When compression scratch space is unavailable, complete originals can be
archived without compression so verified retention can still recover space.
Retrying maintenance does not duplicate successful transfers.

The local `catalogue.json` lists completed sets and file availability.
[Metadata formats](DATA_FORMAT.md#manifest-and-catalogue-metadata) define its
relationship to the immutable closed manifest and mutable lifecycle state. Expired
files remain represented in metadata, so a retained measurement set cannot
silently appear to have its original diagnostics. Long-term minute summaries
retain the original segment identity even after measurements expire. After the
summary horizon and expiry of every local file, verified archive copies retain
the identity and the empty local set can be removed from the catalogue.

Local expiry requires a verified full archive. If no archive is configured, a
destination is unavailable or verification fails, unarchived source files are
preserved. Under storage pressure, eligible archived diagnostics are removed
first, then measurements. Summaries expire only at their own age limit. Retention periods are targets, not
guarantees during pressure. Cleanup aims to reduce budget usage from the 90%
high-water mark to the 80% low-water mark while restoring the free-space reserve.
If no eligible files can be removed, recording stops at its storage limit and
reports the fault. This is an application budget, not a filesystem quota:
concurrent writes, temporary files and closure metadata can briefly overshoot
it. The independent free-space reserve and maintenance headroom are essential. Capture loss while recording is stopped is counted; it is
not replayed later. Archive growth requires separate capacity/backup planning.

Storage health is published separately from capture health. Check both. A
running acquisition process is not proof that maintenance or offload succeeded.
Downloads hold a shared storage lock while streaming; maintenance uses the
exclusive lock. New catalogue/export requests receive a retryable busy response
during maintenance instead of occupying every web worker. Acquisition does not wait for this lock. Active individual-file
downloads still represent a fixed prefix and are not atomic multi-file exports.

## Configure an archive

Use a pre-existing folder on a separately provisioned disk or mounted NAS.
`archive_dir` must not overlap the recording or runtime directory. The worker
does not mount a NAS, establish SSH credentials, or create a missing destination
root. A folder on the same SD card is not an off-device backup and will not free
filesystem space overall. Only configure a destination with suitable capacity.

For example, after mounting the archive filesystem, create a writable folder
`/mnt/pendulum-archive/recordings`, then set this in the Pi configuration:

```json
"archive_dir": "/mnt/pendulum-archive/recordings"
```

The supplied service is filesystem-restricted. Add a systemd override with
`sudo systemctl edit pendulum-storage`:

```ini
[Unit]
RequiresMountsFor=/mnt/pendulum-archive

[Service]
ReadWritePaths=/mnt/pendulum-archive/recordings
```

Arrange ownership so the `pendulum` service account can write there. Ensure the
recordings folder exists **only on the mounted filesystem**, so a missing mount
cannot redirect archive writes onto the SD card. Restart `pendulum-storage`
after configuring the destination and inspect archive status in the dashboard.
The destination keeps complete segment sets; local expiry does not prune it.

For local development, run maintenance alongside demo/acquisition and web:

```sh
Raspberry.Pi/.venv/bin/pendulum-pi storage --config .cache/pi-demo/config.json
```

## Summaries and analysis

The [complete summary format](DATA_FORMAT.md#minute-summary-csv) specifies all
41 columns, eligibility, missing values and fragment semantics. `SUMMARY.CSV`
stores session-relative one-minute bins of recorded measurements,
environmental statistics and quality counters. A segment boundary can split a
minute into partial rows. Summaries are derived, with nominal-counter timing
explicitly distinguished from calibrated offline analysis. They cannot replace
original captures for PPS calibration, noise analysis or later reprocessing.
Host receipt timestamps are not verified Nano event UTC.

The web export selector chooses measurements, summaries, or a full diagnostic
package by host-recorded UTC range. It includes whole overlapping **completed**
segments; records outside the requested range can therefore be present. Active
segments are available through individual-file downloads. Estimates and the
export ceiling prevent unexpectedly large packages; select a narrower range or
summaries when a request exceeds the limit. A single oversized set needs
individual-file downloads or compression; narrowing dates cannot shrink a set. Extract downloaded packages before
running analysis; preserve their session/segment directories and manifests.

```sh
pendulum-analyze /path/to/extracted-recordings --out /path/to/analysis
```

The analysis accepts an entire collection, a session directory, `catalogue.json`,
or a single segment with plain/gzip CSVs. Contiguous retained segments with the
same session and contract are analysed together. Missing segments, changed
contracts and session restarts produce separate reports linked from an index.
Logging pauses and storage-limit stops also split reports.
Source-file hashes and assembled row mappings are retained. Temporary joined
inputs require space on the **analysis host**; source recordings are unchanged.

## Hardware acceptance

Before unattended use, exercise capture plus compression/offload/downloads on
the Pi. Verify no receive-queue loss under load, remove the archive mount, fill
the test data budget, interrupt compression/transfer, reboot during recording,
and confirm safe retries and visible gaps. Power-loss durability and NAS mount
behaviour cannot be established by the host unit tests alone. Bound OS journal
usage through the Pi's journald configuration as well; the data budget covers
the recording directory, not unrelated system files.
