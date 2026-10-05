# Pi HTTP interface

This reference describes the routes implemented by
[web.py](pendulum_pi/web.py) and their acquisition/storage counterparts. It owns
transport, authentication, validation and response behaviour. Measurement, history
and statistical meanings remain in [DATA_FORMAT.md](DATA_FORMAT.md), live-setting
validation in [CONFIGURATION.md](CONFIGURATION.md), and archive policy in
[STORAGE.md](STORAGE.md). This API has no route-version prefix or separately
negotiated schema version; clients should tolerate additional object fields and
use the documented named fields.

## Access and shared limits

All `GET` routes below are read-only and unauthenticated, including command-result
polling and downloads. `POST` routes require:

- A configured nonempty `api_token` and an exact `Authorization: Bearer TOKEN` header.
- `Content-Type: application/json` with a JSON body, at most 8192 bytes.
- If `Origin` is supplied, its scheme and authority must match the request, with
  no path, query or fragment. `Sec-Fetch-Site: cross-site` is rejected.

An empty configured token gives `403` for administration, a missing/wrong bearer
token gives `401`, origin failures give `403`, and a non-JSON content type gives
`415`. Bad JSON/input normally gives `400`; oversized bodies give `413`. The
application's handlers for `400`, `401`, `403`, `404`, `409`, `413`, `415`, `429`
and `503` return `{"error":"message"}`. Unsupported HTTP methods and unexpected
server errors are not covered by that JSON-error handler; clients must also
handle ordinary HTTP error responses.

Responses set `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer` and a same-origin content-security policy. There is
no built-in TLS. Use the [deployment network guidance](INSTALL.md#administrator-token-and-network-access)
for access to credentials and recordings.

The supplied server uses six request threads, at most 24 connections, backlog
24, a 30-second channel timeout, an 8 KiB request-body limit and a 16 KiB request-header
limit. Application semaphores allow two combined archive/individual downloads
and two combined history/environmental/temperature queries per web process. They return `429`
when occupied. The channel timeout is not a promise that every computation or
stream finishes within 30 seconds. Configuration saves and command submission
share a process-local mutation lock; deployment runs one web service.

## Route inventory

| Method | Route                        | Result / purpose                                              |
| ------ | ---------------------------- | ------------------------------------------------------------- |
| GET    | `/`                          | Dashboard HTML                                                |
| GET    | `/static/<path:filename>`    | Bundled dashboard assets, using Flask's static-file handler   |
| GET    | `/api/status`                | Acquisition, display, environment and storage health snapshot |
| GET    | `/api/phase`                 | Cached/background-built latest-segment swing phase charts     |
| GET    | `/api/history`               | Bounded sampled display-history query                         |
| GET    | `/api/history/environment`   | Joint and individual environmental/period relationships       |
| GET    | `/api/history/temperature`   | Legacy temperature/period comparison and fit                  |
| GET    | `/api/config`                | Redacted configuration and editable-field list                |
| POST   | `/api/config`                | Save validated live settings                                  |
| POST   | `/api/commands`              | Queue one permitted Nano command                              |
| GET    | `/api/commands/<ident>`      | Poll one command result                                       |
| GET    | `/api/catalogue`             | Paginated completed recording sets                            |
| GET    | `/api/export/estimate`       | Validate selection and estimate an archive download           |
| GET    | `/api/export`                | Stream a selected recording collection as tar.gz              |
| GET    | `/api/files`                 | Paginated recording-directory listing                         |
| GET    | `/api/download/<path:value>` | Download one permitted recording file                         |

`HEAD`/`OPTIONS` behaviour is supplied by Flask for the registered routes. Only
the phase and history routes explicitly reject unknown query keys; clients
should still send only the parameters documented below.

## Status and phase charts

`GET /api/status` combines acquisition's runtime `status.json`, independent
`receiver.json`, saved `analysis.json` and storage's `storage.json`, rather than opening the UART or I²C devices. With no acquisition
snapshot it returns a sparse object with derived health fields, not a fixed
all-null acquisition schema. Normal snapshot groups include `version`, `source`,
`boot_id`, `updated_utc`, `updated_monotonic`, `uptime_seconds`, `connected`,
`ready`, `capture_stale`, `last_capture_age_seconds`, `transport`, `logging`,
`counters`, `contract`, `latest`, `display`, `history`, `time_health`, `gps_health`,
`environment`, `oled` and `config_error`. The web layer adds `storage`,
`service_stale`, `snapshot_age_seconds` and `healthy`. `analysis` separately
reports processing lag, saved data time and analysis-service freshness; `healthy`
is capture health. `logging.written_capture` and `logging.committed_capture`
distinguish written from durable captures. `measurement_observed_utc` identifies
the saved result time. Opening a page does not advance it.

`last_swing_received_monotonic` and `last_pps_received_monotonic` identify the
latest accepted, nonduplicate CSW and CPS receipts in acquisition, independently
of analysis progress. They are null before receipt and after acquisition
recovery until fresh validated records arrive. These are Pi-local monotonic
timestamps, not Nano edge UTC. The OLED uses them for its three-second feed
warnings; analysis/result freshness remains separate.

A snapshot is fresh when its monotonic age is between zero and five seconds.
Storage has its own age and `stale` flag, with a threshold of
`max(15, 3*storage_interval_seconds)` seconds. Missing or invalid ages are stale.
A stale acquisition snapshot makes available chrony/gpsd status unavailable;
GPS fix/satellite values are cleared. `healthy` requires fresh, running,
connected, metadata-ready acquisition, recent captures, no configuration or
logging error, and active recording when recording is enabled. It does not
assert chrony synchronisation, sensor health or successful archiving; inspect
those groups separately. [TIMEKEEPING.md](TIMEKEEPING.md) owns UTC interpretation.

The current `display.window` contains one PPS-calibrated 600-second swing
mean: `model`, `window_seconds`, `count`, `elapsed_seconds`, `filling_seconds`,
`learning`, `period_us`, `bpm`, `gain_seconds_per_day`, and component/balance
metrics. `model=swing_mean_600s_pps_dual_ewma_v1` identifies calibration by the
unchanged PPS dual EWMA. OLED and HTTP use the same snapshot; ThingSpeak reads
these values directly. There are no current `display.short`/`display.long` swing
estimators. `display.pps` still exposes its existing fast, slow and blended PPS
frequency estimates.

`display.available`, `display.stale`, `display.timebase` and `window.learning`
must be interpreted together. A fresh but filling mean is provisional until
600 fresh captured seconds accumulate. There is no nominal calibration fallback.
Calibration expiry or stale swing evidence masks current mean values and the
next forecast. After calibration resumes, previous measurements may remain in a
provisional refilling mean, with 600 fresh seconds required before it is ready.

`display.forecast` is the optional causal next-full-swing diagnostic, with
`enabled`, `cycle_length`, `model`, `version`, `half_life_seconds`, `learning`,
`available`, `stale`, `revision`, `reset_reason`, `next`, `last`, `results` and `recent`.
`next` binds its frozen duration, mean-only baseline and learned pattern to
`origin_seq` and `target_seq`. `last` retains the scored observation, frozen
prediction and signed errors; `recent` reports pattern and baseline RMSE over a
600-second captured-duration window. Disabled, unavailable or stale forecasts
have no next prediction. Errors are observed minus predicted duration. Sequence
gaps and unavailable calibration reset the forecast; it does not feed the rate
estimate. Historical scored values remain historical diagnostics, not current
predictions. `results` contains up to 120 scored results for the current model
revision, including `elapsed_seconds` and `observed_epoch` (Pi receipt time,
nullable for offline callers). It survives stale polls and clears on model reset.
`window.priming` reports `active`, `restored_samples`, `fresh_seconds` and
`checkpoint_age_seconds`; active restoration is provisional. `mean_checkpoint`
reports checkpoint worker/restore health separately from capture health.

`GET /api/phase` accepts **no query parameters** (`400` otherwise). It returns a
saved snapshot for the latest recording segment. The independent `pendulum-views`
service schedules a refresh at most every 60 seconds; requests start no calculation. Input tails are limited to 1800 swings and 3800 PPS records,
with a tail-read bound of 2 MiB per capture file (plus its header). Response fields can include `charts`, `state` (`updating`, `ready`,
`unavailable`), `segment`, `updating`, `source`, `live`, `refresh_seconds`,
`swing_limit`, `generated_at`, `nominal_hz`, `records`, `eligible` and analysis
metadata. Unavailable input returns `200` with an explanatory `message`; an
existing result can accompany `stale: true` after a failed refresh. A refresh
requires the analysis package in the saved-view service environment and valid
committed capture files.
There is no blocking request-to-completion guarantee. See the
[swing-phase view](OBSERVATORY.md#swing-phase-medians) for chart meaning.

## History queries

The detailed response data, reduction, estimator provenance, continuity boundaries,
fit and uncertainty definitions are owned by
[observatory display history](DATA_FORMAT.md#observatory-display-history).

| Route                      | Parameters and bounds                                                                                                                                    |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/api/history`             | `start`, `end`: finite Unix seconds, `0 <= start < end`, at most 366 days; defaults: last hour ending now                                                |
| `/api/history`             | `max_points`: integer 32–2000, default 1000; optional `session`: 1–128 letters/digits/underscore/dot/colon/hyphen                                        |
| `/api/history`             | `series`: optional comma-separated allowed series names; at most 9 names, each at most 40 characters; see history owner for names                        |
| `/api/history/environment` | `start`, `end`: same time range/defaults; optional `session`; period and environment use matching 600-second means                                       |
| `/api/history/temperature` | `start`, `end`: same time range/defaults; optional `session`; `estimate`: `window` (default); `short`, `long`, `blended` for explicit historical queries |

`/api/history/environment` queues a cached fit in the saved-view service and
returns `state=updating` while it runs, then `state=ready` with saved results.
`busy` reports the bounded eight-job queue; `unavailable` reports a failed fit.
Responses identify requested and cached minute cuts. Clients should retry
pending jobs, including historical selections.

Unknown parameters, invalid ranges, unrecognised series/estimates and invalid
session identities return `400`. All three endpoints share the two-query limit (`429`)
and return `503` for database/filesystem unavailability. The history query has an
eight-second SQLite execution budget; the environmental and temperature queries have four-second
budgets. Relationship results are limited to 2000 paired averages
across segments and reject excessive fragmentation instead of fitting a truncated
subset. The history point budget can reduce the returned chart representation;
read its reduction metadata. None of these routes scans raw recordings. There are eleven available history series;
the current HTTP parser limits an explicit list to nine names. Omit `series`
to select all eleven. Current records fill `window_period_s` and
`window_rate_s_day` from the shared 600-second mean; retired swing EWMA columns
remain only for historical records and are null for new ones. The mean's current
model identity distinguishes PPS dual-EWMA calibration from earlier mean records;
older stored model identities and values are preserved. See the history owner
for schema-3 migration, model boundaries and fit eligibility. The default
`estimate=window` does not fall back to a historical EWMA series.

## Configuration

`GET /api/config` returns:

```json
{"settings": {"...": "all shared settings except api_token"}, "editable": ["..."], "administration_enabled": true}
```

Paths are strings (or null for an unset archive). `POST /api/config` accepts a
nonempty object containing only live field/value changes, for example:

```json
{"target_period_s": 2.0, "forecast_cycle_length": 15}
```

The full resulting settings must pass the [configuration constraints](CONFIGURATION.md).
Success is `200 {"saved":true,"applies":"within one second"}`. The response
confirms a saved Pi configuration; the acquisition reload normally occurs on its
one-second periodic cycle. It is not an acknowledgement from a running Nano or
proof that acquisition is currently running. Invalid fields, types or coupled
values return `400` and leave the saved settings unchanged. Retired
`short_minutes` and `long_minutes` are rejected in API patches; only loading an
existing configuration file ignores them. Current settings expose no swing mean
window or pattern adaptation-rate setting.

## Nano commands

`POST /api/commands` requires exactly `{"command":"TEXT"}`. The Pi accepts only
these case-sensitive command forms, with exact single spaces and a maximum of
63 characters:

- `get PARAM`
- `set PARAM UNSIGNED_INTEGER`
- `emit meta`
- `emit startup`
- `repair eeprom`

`PARAM` starts with an ASCII letter followed by letters, digits or underscores.
A set value uses decimal digits and must not exceed 4294967295. The Pi validates
syntax; the Nano validates supported parameters and firmware value limits. See
[Command_Interface_Contract.md](../Docs/Command_Interface_Contract.md) for exact
firmware meanings, EEPROM saving/repair and acknowledgement content.

Success returns `202 {"id":"<32 lowercase hex characters>","state":"queued"}`.
This means the request was written to the runtime queue. It does not mean serial
transmission or execution. At most 32 queued/recently in-flight request identities
are accepted; further requests get `429`. A command/results directory scan reaching
512 entries gives `503` and requires acquisition maintenance. Posting is possible
while acquisition is unavailable, but the request can then expire.

Poll `GET /api/commands/<ident>`. A malformed identity or absent result gives
`404`. Results contain `id`, `command`, `state`, `updated_monotonic`, and usually
`response` (the initial web-written `queued` result omits `response`). Runtime
monotonic timestamps are for local freshness, not portable UTC. State meanings:

| State     | Meaning                                                                                           |
| --------- | ------------------------------------------------------------------------------------------------- |
| `queued`  | On disk, or acquisition accepted it and is awaiting serial transmission                           |
| `sent`    | Serial owner reported transmission; awaiting a matching Nano status acknowledgement               |
| `ok`      | Matching `STS` success observed after sending, in the same serial connection generation           |
| `error`   | Validation, expiry, transport/interruption or Nano error; inspect `response` for execution status |
| `timeout` | No timely matching acknowledgement; execution/persistence may be uncertain                        |

Acquisition dispatches one command at a time, only with a connected serial source
and validated metadata. Replay/demo commands fail in acquisition. Unsent requests
older than 30 seconds or predating acquisition startup are rejected; mutations
are never automatically replayed after restart. The ten-second acknowledgement
timer starts when acquisition accepts a request for dispatch, and timeout is
checked only after the receive-event queue is empty so an already-received
acknowledgement is not discarded behind captures. Timeout blocks dispatch and
forces transport reconnect/metadata rejoin without resetting the Nano. Polling
also presents a `timeout` when a stored `queued`/`sent` result is over 30 seconds
old; this is a response overlay, not a cancellation or durable state transition.
Acquisition retains only the most recent 256 result files by modification time;
results are runtime state and can disappear on reboot.

Matching success uses command verb/parameter and connection generation; the wire
has no transaction identifier. For mutating commands whose successful firmware
path saves EEPROM, `ok` reports the firmware's verified-save acknowledgement.
The Pi does not independently read EEPROM back. An ambiguous timeout or transport
error still requires inspection before resubmission, because execution may have
occurred even if its acknowledgement was lost.

## Catalogue and exports

`GET /api/catalogue` reads the storage worker's completed-set catalogue. Optional
`start` and `end` must be supplied together as ISO dates (`YYYY-MM-DD`, midnight
UTC) or ISO timestamps with an explicit UTC offset. The end is exclusive and must
be later than the start. This date syntax differs from history's numeric Unix
seconds. With no range, it lists all catalogued sets.

`cursor` is an integer offset, default 0, allowed 0–1000000; `limit` is 1–100,
default 50. Results are sorted descending by start time and path. The object
contains `segments`, `total`, `updated_utc`, `time_semantics`, and `next_cursor`
(integer or null). Each segment exposes identity/path/session/segment, coverage,
`files` and `archive` information described in
[manifest and catalogue metadata](DATA_FORMAT.md#manifest-and-catalogue-metadata).
A missing catalogue behaves as an empty catalogue; an invalid or over-32-MiB
catalogue gives `503`. Pagination is an offset over the current catalogue, not a
stable snapshot across requests.

`GET /api/export/estimate` and `GET /api/export` require the same `start`/`end`
range syntax. `kind` is `measurements` (default: PCSW/PCPS), `summary` (SUMMARY)
or `full` (PCSW/PCPS/STS/PI/RAW/SUMMARY). Selection includes whole overlapping,
cleanly closed segments using host-recorded coverage, including recorded clock
excursions; it is not an event-UTC filter. Active segments are excluded. Expired
local files are reported as unavailable; these routes do not fetch archive copies.

The estimate returns `format_version: 1`, `kind`, `requested_start_utc`,
`requested_end_utc_exclusive`, `time_semantics`, `segment_count`, `segments`,
`unavailable_file_count`, `source_bytes`, `maximum_download_bytes` and
`download_limit_bytes`. Segment provenance records retained and unavailable files,
identity and coverage. The byte maximum includes conservative tar/gzip overhead,
not just source file bytes. Both estimate and download reject an estimate above
`export_max_mb` with `413`.

Download returns `application/gzip`, attachment `pendulum-<kind>.tar.gz`, with
`X-Export-Maximum-Bytes`. It streams without a temporary archive and includes
`export.json`, per-segment manifests and selected plain/gzip files in their session
folders. The embedded `export.json` contains selection/provenance fields; byte
estimates are added to the HTTP estimate after that embedded payload is built and
are not included in it. Extract before offline analysis; preserve boundaries.

Invalid date/kind gives `400`; no overlapping completed sets or no retained files
of that kind gives `404`; duplicate identity, unsafe paths, unavailable/changed
catalogued members or missing manifests gives `409`. Malformed coverage or a
busy/unavailable catalogue gives `503`. Catalogue/estimate hold a shared storage
lock during planning; exports hold it through streaming. Maintenance takes the
exclusive lock, and new locked reads fail promptly with `503` while it runs.
Errors occurring after stream headers have been sent may terminate the download;
clients must handle incomplete archives rather than expect a JSON error then.

## Individual file browsing and downloads

`GET /api/files` takes `path` relative to `data_dir` (default empty root), plus
`cursor`/`limit` with the same numeric bounds as catalogue pagination. It returns
`path`, `entries`, `next_cursor`. Entries have `name`, `path`, `directory`,
`bytes` (null for directories) and `modified` (Unix seconds). Ordering is the
filesystem's iteration order, not sorted. The cursor indexes directory entries,
including skipped entries; each request scans at most 1000 entries beyond its
offset. Concurrent file changes can alter subsequent pages.

Path components must match `[A-Za-z0-9][A-Za-z0-9_.-]{0,95}`, may not be `.` or
`..`, and paths may contain at most four components. Symlink traversal is refused.
Only directories within the listing depth and allowlisted files are returned.
A missing data root returns an empty root listing; absent/unsafe subpaths give
`404`. Listing itself does not take the shared storage lock.

`GET /api/download/<path:value>` allows only regular, non-symlink files beneath
the data root whose final name is PCSW.CSV, PCPS.CSV, STS.CSV, PI.CSV, RAW.jsonl,
SUMMARY.CSV, manifest.json or catalogue.json; gzip variants are permitted only
for CSV/JSONL files. It returns an `application/octet-stream` attachment with
`Content-Length` for a fixed prefix observed when opened. It can download active
files. Plain CSV/JSONL prefixes end at the last complete newline found in the
last MiB (`409` if none exists); JSON and compressed files use their observed
byte length. This is not an atomic multi-file snapshot. A missing/unsafe file
gives `404`, maintenance contention gives `503`, and this route shares the two
active-download slots with `/api/export` (`429` when full). There is no
`export_max_mb` ceiling on an individual-file download.
