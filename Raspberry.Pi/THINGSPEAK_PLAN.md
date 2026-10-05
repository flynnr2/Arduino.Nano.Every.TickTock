# ThingSpeak clock diary implementation plan

Status: Historical implementation and acceptance plan for the original
schema-1 long-EWMA publisher. Its measurement contract is superseded by the
schema-2 PPS-calibrated 600-second mean in [THINGSPEAK.md](THINGSPEAK.md).
The original plan below is retained as history; its long-period fields,
half-life configuration and historical status descriptions do not describe the
current implementation. See [CONFIGURATION.md](CONFIGURATION.md),
[HTTP_API.md](HTTP_API.md) and [OLED.md](OLED.md) for current settings and shared
calculations. Channel provisioning, key validation and physical Pi commissioning
remain separate work; publishing stays disabled until commissioned.

## Purpose and first release

Share a small, automatically updated public record of the Synchronome's behaviour
with horological enthusiasts. Publish one snapshot each hour so readers can
follow rate, temperature and pressure over weeks and seasons.

Publishing is optional, low priority and deliberately missable. Local capture,
recording and display must continue independently of the internet or ThingSpeak.
A missed update is acceptable; there is no delivery guarantee, backlog or replay.

The first release uses the existing long period estimate and fresh environmental
readings. It does not scan recordings, calculate new averages or require MATLAB
analysis in the cloud. This document records the design and acceptance plan;
the operation guide describes the implemented commands and deployment.

## Public data contract

Keep field assignments stable once the channel is in use.

| Field  | Public label           | Unit        | Source or calculation                              |
| ------ | ---------------------- | ----------- | -------------------------------------------------- |
| field1 | Estimated gain/loss    | seconds/day | Existing rate helper applied to the long period    |
| field2 | Pendulum full period   | seconds     | `display.long.period_us / 1_000_000`               |
| field3 | Temperature near clock | °C          | Fresh SHT4x temperature                            |
| field4 | Atmospheric pressure   | hPa         | Fresh BMP280 station pressure                      |
| field5 | Relative humidity      | % RH        | Optional; fresh SHT4x humidity, disabled initially |

Reserve fields 6–8. Pressure is the sensor's local pressure, without sea-level
correction. Describe the actual sensor placement in the channel information.

Use the target period recorded in the same acquisition snapshot as the estimate.
Require an explicitly configured target; do not silently assume two seconds.
Reuse `rating.gain_seconds_per_day()`:

```text
gain_seconds_per_day = 86400 * (target_period_s / measured_period_s - 1)
```

Positive means gaining; negative means losing. This is an estimated rate, not
accumulated error or a measurement of the clock hands against UTC.

The current default long estimate has a 60-minute exponentially weighted moving
average (EWMA) half-life. Label the channel accordingly: "60-minute smoothed
estimate, sampled hourly". Use the actual half-life in each entry's status and
update the channel description if the setting changes. These are snapshots of a
continuous estimator, not averages of separate hours. Passing the existing
learning threshold is not proof of convergence or measurement accuracy.

Upload rate in seconds/day and period in seconds, both with six decimal places:

- Rate: `0.000001` seconds/day increments, equivalent to 1 microsecond/day.
- Period: `0.000001` second increments, equivalent to 1 microsecond per full period.
- Environmental values: two decimal places, in the units listed above.

Compute rate from the unrounded period estimate, then format the two fields
independently for upload. For example, a target of 2 seconds and a measured period
of 2.000100 seconds produce period `2.000100` and rate `-4.319784` seconds/day.
Rounding the uploaded period must not quantize the separately calculated rate.

Keep these units and upload resolutions fixed regardless of chart rounding.
ThingSpeak may display fewer decimal places, but presentation must not reduce the
precision sent by the publisher. Verify both values by reading back the raw feed
during commissioning. These are numerical reporting resolutions, not claims of
measurement accuracy or equivalent fractional resolution in the two quantities.

Include a compact, bounded `status` string containing the public schema version,
actual half-life, target period, PPS correction state, learning state, observation
time and Pi UTC quality. Build it from allowlisted values; do not upload the whole
status file, internal paths, hostnames or diagnostic error messages.

## Existing foundations

The implementation uses the integrated observatory status contract:

- `service.py`: atomic publication of `runtime_dir/status.json`, normally about
  once per second, including source, acquisition health, estimates and environment.
- `display.py`: long-period EWMA, learning state and Nano PPS calibration health.
- `rating.py`: the shared period-to-rate conversion and target in the snapshot.
- `common.py`: JSON file exchange helpers.
- `time_health.py`: cached Pi UTC diagnostics, separate from Nano PPS correction.
- `__main__.py` and `deploy/`: application commands and systemd installation.

Read the existing status file directly. The publisher must not depend on the web
service, browser activity, history database or raw CSV files. Reuse measurement
semantics from [DATA_FORMAT.md](DATA_FORMAT.md) and [TIMEKEEPING.md](TIMEKEEPING.md).

## Snapshot eligibility and missing data

Evaluate freshness at publication time using the Pi monotonic clock. Do not trust
cached `fresh` or `stale` flags on their own when the producing process may have
stopped. Read one snapshot and build one internally consistent payload.

Skip the entire upload when any of the following applies:

- Publishing is disabled, required publisher configuration is invalid, or the
  current key/channel does not match a successful validation receipt.
- The snapshot is missing, malformed, stopped, more than five seconds old, or
  has a non-finite or future monotonic timestamp.
- The source is anything other than real serial acquisition, or the snapshot
  reports simulated environmental data. Demo and replay never publish.
- Acquisition is disconnected, not ready, capture-stale or has a configuration
  error. Add snapshot age to reported capture age before testing freshness.
- The long estimate is unavailable, stale, learning or not finite and positive.
- The display timebase is not `PPS`, or PPS is uninitialised, stale or not
  `LOCKED`. Recheck freshness conservatively; a snapshot close to the five-second
  PPS limit must not extend that limit. Expose a last-accepted PPS monotonic time
  in status if needed to make this check reliable.
- The snapshot's target period is absent or invalid, or derived rate is invalid.

For each sensor, recompute age from its last-good monotonic timestamp and apply
the existing configured sensor freshness limit. Omit missing, disabled, stale or
non-finite environmental fields independently. A failed pressure sensor must not
suppress a valid clock rate and temperature. Missing values are never zero or a
repeat of the last successfully uploaded value.

Recording being intentionally paused does not itself prevent publication of a
valid live snapshot. Document that the recording switch and publication switch
are independent. Publication failures must not change either switch.

The production runtime directory is under `/run` and does not survive reboot.
The snapshot also carries a Linux boot identity, which must match the current
boot before its monotonic timestamps can be accepted.

## Timestamp policy

Let ThingSpeak assign the feed timestamp on receipt; do not send `created_at` in
the first release. Describe the chart axis as upload time for a fresh snapshot.
This keeps an uncertain or corrected Pi wall clock from misdating the public
series. No precise Nano-event-to-UTC mapping is claimed.

Include the snapshot's Pi observation timestamp in status only when cached UTC
diagnostics are fresh and synchronized; otherwise use `observation_utc=unknown`.
Include snapshot age in either case. Recheck UTC diagnostic freshness after
adding snapshot age. Pi UTC uncertainty alone does not invalidate a fresh,
PPS-corrected period measurement.

## Publisher command and configuration

Add a small `pendulum_pi/thingspeak.py` module and a one-shot command:

```sh
pendulum-pi thingspeak --config /var/lib/pendulum/config.json \
  --publisher-config /var/lib/pendulum/thingspeak.json
```

Support `--dry-run` to report eligibility and the exact public fields/status
without opening a network connection or requiring a write key. Keep normal
publication disabled by default. Also support mutually exclusive `--validate-key`
for a status-only commissioning write and `--check-ready` for a local readiness
check without network access. Separate payload selection from HTTP transport
so each can be exercised with synthetic snapshots and a fake transport.

Use a dedicated publisher configuration file containing enabled state, channel
ID, humidity option and write-key file path. Keep the actual write key in a
separate file readable only by the service account and administrators. Keep it
out of shared application settings, manifests, downloads, command arguments,
version control, dry-run output and logs. The write key chooses the destination
channel; the configured channel ID also allows response verification.

A key is not valid merely because it has the expected shape. Commissioning makes
one status-only POST and requires a positive entry ID plus the expected channel
ID. A durable local receipt binds that success to the key fingerprint and
channel. Normal publication requires a matching receipt; a missing or replaced
key cannot activate the publisher. The receipt is removed on definite credential
rejection. Connectivity failures do not trigger retries or commissioning writes.
The installer never validates keys over the network.

Avoid a general configurable destination URL. Use the fixed ThingSpeak HTTPS
endpoint with certificate validation and no cross-host redirect following.

## HTTP transaction and failure behaviour

Make one JSON POST to `https://api.thingspeak.com/update.json`, containing the
write key, eligible numeric fields and status. Use a short network timeout
(initially five seconds), a small bounded response read, and a separate service
runtime limit to bound DNS and other delays. No automatic transport retries.

Count success only for an HTTP success response containing a valid positive
`entry_id` and the expected `channel_id`. Treat a zero response, malformed reply,
unexpected channel, HTTP error, TLS failure or timeout as unsuccessful. Never
log the request body or credentials. A timeout after sending is an unknown
outcome: the entry might have been accepted. Do not retry it.

ThingSpeak documents these request fields and response semantics in its
[Write Data API](https://www.mathworks.com/help/thingspeak/writedata.html).

Log one short local outcome per invocation: published, skipped with reason, or
failed with a sanitized reason. An expected skip exits successfully; invalid
configuration and transport failure return nonzero without triggering a restart
loop. No alerts, emails or notifications are required.

## Scheduling and resource isolation

Add `deploy/pendulum-thingspeak.service` and `.timer`:

- Use a one-shot service with no automatic restart, running as the existing
  `pendulum` account with low CPU priority and idle I/O scheduling.
- Start approximately hourly using a monotonic timer: first attempt about one
  hour after timer activation, subsequent attempts about one hour after the last
  invocation finishes.
  Do not require alignment to a wall-clock hour or use missed-run catch-up.
- Impose a 20-second maximum runtime for the one-shot process, including startup
  and network work. Verify the correct systemd timeout setting for a one-shot.
- Do not make capture or any existing service depend on this service or timer.
  Do not wait for network-online; one failed attempt is sufficient.
- Permit read access to the configuration, key and runtime snapshot. Keep the
  filesystem read-only for the publisher wherever practical, permitting the
  validation receipt and invocation lock to be updated; use the journal
  for outcomes. It does not need serial or sensor access.
- Prevent overlapping manual and timer invocations with a nonblocking lock;
  skip immediately if another invocation is active. No queued invocations.

Install the optional units without enabling publication on a new installation.
Preserve publisher settings on upgrade and restore existing timer enablement
only when the current key and configuration pass the local readiness check. Extend installer
validation for the command, service and timer. Account for the optional timer
during upgrades without adding it to the five always-on services.

Normal operation sends about 24 updates a day. Longer intervals can be selected
with a documented timer override; they do not change the estimator's half-life.

## Public channel setup

Create and verify the channel during deployment, initially private. Configure
fields 1–4, optionally field 5, and enable the status display. Add:

- Clock make/model and relevant pendulum details, based on confirmed information.
- Sensor placement, target period, smoothing half-life and upload cadence.
- A short explanation of PPS correction, rate sign, upload timestamps and gaps.
- Week and month views for rate, temperature and pressure, plus period for readers
  interested in the underlying measurement.
- A last-entry timestamp visible to readers. An old point must not be described
  as a live reading; missing uploads do not mean the mechanical clock stopped.
- A project link and a statement that environmental association does not by
  itself establish causation.

Use visible point markers and check how the hosted charts connect missing hours.
If standard charts bridge gaps, explain that connecting lines do not represent
continuous observations; do not fabricate samples to control chart appearance.

Channel fields, descriptions, public access and status display are described in
the [ThingSpeak channel documentation](https://www.mathworks.com/help/thingspeak/channel-settings.html).

## Implementation sequence and acceptance

1. **Payload and eligibility.** Implement the pure snapshot-to-payload function,
   publisher configuration and dry run. Verify period units, rate sign, target
   provenance, six-decimal serialization and exact field assignments. Include the
   2.000100-second example above and a sub-microsecond period change that must
   remain visible in the independently calculated rate. Exercise malformed snapshots, stopped
   producers, learning, PPS loss, stale sensors, unknown UTC, demo and replay.
2. **Bounded transport.** Add the single POST and sanitized outcome reporting.
   Test valid success, zero response, malformed and oversized replies, wrong
   channel, HTTP rejection and ambiguous timeout with a fake transport. Assert
   one attempt only, no key leakage and no network access during dry run.
3. **Deployment.** Add timer/service units and installer checks. Document key
   provisioning, enabling, disabling, dry runs and journal inspection. Verify
   optional enablement survives upgrade and rollback is simply disabling the
   timer; capture and data retention remain independent. Verify that absent,
   malformed, rejected or replaced credentials cannot enable publishing and that
   a successful commissioning write is required before activation.
4. **Pi commissioning.** Publish to the private channel and compare a real entry
   with its source snapshot. Exercise network loss, unavailable sensors, stopped
   acquisition and reboot. Check bounded runtime, absence of catch-up, omitted
   sensor fields and continuing acquisition without added capture loss.
5. **Public diary.** Confirm labels and week/month views, then make the verified
   channel public. Observe several scheduled uploads and one missed interval.
   Update the Pi README and installation guide with the channel and operation
   instructions, and record completion of live acceptance separately from
   software implementation.

Run focused publisher tests and the existing affected Pi configuration, CLI and
installer checks. Existing unrelated working-tree changes must be preserved.
No live-channel writes belong in the automated test suite.

## Deferred work

Leave daily summaries, valid-data coverage statistics, adjustment notes, rate
variation, raw-data publishing and custom cloud analysis for a later increment.
Also defer accumulated error, inferred escapement beat error, automatic
temperature/pressure compensation and automatic regulation advice.

A later daily mean must be computed from defined valid intervals with explicit
coverage; averaging whichever hourly uploads happened to arrive is not an
adequate substitute. Add it only if the initial public diary proves useful.
