# Optional ThingSpeak clock diary

The Pi can publish one fresh snapshot approximately each hour to a ThingSpeak
channel. This is a deliberately missable public diary: acquisition, recording,
sensors and the local dashboard continue independently of uploads. There is no
backlog, catch-up burst or immediate retry after a failed request. The next
hourly invocation tries the latest eligible saved result. Upload outcome and
the observation time of the last successful upload are saved locally across
restarts and exposed in dashboard status.

The software is implemented; physical Pi and live-channel commissioning remain
pending. Installation supplies the service and timer but creates no publisher
configuration or key and leaves the timer disabled. Leave it that way until you
are ready to set up the channel. The commands below describe that later setup;
normal installation performs no ThingSpeak validation or publication.

## Public fields and precision

| Field  | Label                  | Unit        | Uploaded decimals |
| ------ | ---------------------- | ----------- | ----------------- |
| field1 | Estimated gain/loss    | seconds/day | 6                 |
| field2 | Pendulum full period   | seconds     | 6                 |
| field3 | Temperature near clock | °C          | 2                 |
| field4 | Atmospheric pressure   | hPa         | 2                 |
| field5 | Relative humidity      | % RH        | 2, optional       |
| field6 | Mean passage velocity  | mm/s        | 6                 |
| field7 | Mean semi-amplitude    | degrees     | 6                 |

Field 8 is unused. Humidity is enabled by default. Pressure is local station
pressure without sea-level correction. Missing or stale environmental values are
omitted independently; they are never replaced by zero or an earlier upload.

Fields 6 and 7 use `(tick mean + tock mean) / 2` from `display.motion.mean` in
the same saved analysis result, sampled at publication time. These are the
latest 300-swing means, not the latest individual passage or an hourly average.
Velocity is optical passage speed in mm/s; amplitude is the estimated harmonic
semi-amplitude in degrees (centre to peak). Both use the dashboard's provisional
3.79 mm effective optical path and 1242 mm pivot-to-beam distance. They are
derived from the same optical captures and are not independent measurements.
Six uploaded decimals preserve numerical detail without claiming that accuracy.

Motion must be available, fresh and outside learning with all 300 swings.
An absent, stale or refilling motion estimate omits fields 6 and 7 while other
eligible fields continue to publish. Each field also requires finite, positive
values for both tick and tock; it never substitutes a single direction or zero.
Motion publication does not depend on environmental sensors being enabled.

The rate uses the target full period and the PPS-calibrated 300-swing mean
from the same saved analysis result used by the OLED and HTTP dashboard. Positive means gaining time; negative means losing time.
Set the target period explicitly in the receiver configuration or dashboard:
there is no automatic assumption of two seconds. A two-second target with a
2.000100-second measured period gives `-4.319784` seconds/day.

Six decimal places preserve 1 microsecond/day numerical increments for rate and
1 microsecond per full period for period. These are different resolutions and
are not measurement-accuracy claims. Rate is calculated from the unrounded period
estimate before either field is formatted. Changing chart rounding must not
change the upload units or precision.

The swing estimate is the arithmetic mean of completed swings in a fixed
300-swing window. Each swing's four component durations are
normalized using the unchanged PPS dual EWMA available when it is received. The
same window supplies period, BPM and estimated gain/loss; the publisher performs
no additional smoothing or rate calculation. It requires 300 fresh complete swings and skips provisional values during filling. Passing that
state does not establish convergence or accuracy.

A published point is a snapshot of the continuous 300-swing estimator,
sampled approximately hourly. It is not an hourly average. There is no nominal
clock-frequency fallback for the current swing estimate. Expired PPS calibration
masks its values; following recovery, another 300 fresh complete swings
are needed before publication. Previously measured observations remain
available to the provisional mean during refilling.

For the current clock, use this channel description:

> Synchronome pendulum with a nominal 2-second full period, swinging NE ↔ SW.
> Period and gain/loss use a PPS-calibrated 300-swing mean. Snapshots uploaded
> approximately hourly. Estimated gain/loss is in seconds/day: positive means
> gaining; negative means losing.

Use this gain/loss chart title:

> 300-swing mean estimated gain(+)/loss(-) (seconds/day)

These are channel setup instructions, not an automatic change to a live channel.
For another pendulum, change the clock description and configure its target full
period; the mean estimator itself is independent of clock type. The optional
individual-swing forecast is diagnostic only and never enters these fields.

ThingSpeak assigns the entry timestamp on receipt. The chart axis is upload time
for a fresh snapshot. Entry status includes bounded measurement context: `schema=4`,
`estimator=mean`, `window_swings=300`, actual `window_seconds`, target, PPS and learning state, saved-result
age and Pi UTC quality. The observation timestamp describes the saved result;
it does not advance merely because a page was opened. Schema 4 adds optional
motion fields to the schema-3 300-swing mean contract. Earlier schema-1 entries retain their original EWMA
half-life semantics; the change does not rewrite public history. The
observation UTC timestamp is included only when fresh synchronized Pi UTC health
is available. Unknown Pi UTC alone does not suppress a fresh PPS-corrected rate.

## Configure later, on the Pi

Create the ThingSpeak channel initially as private. Configure fields 1–7 as
above and enable the status display. Note its channel ID and
**Write API Key**. A Read API Key does not authorize publishing. See MathWorks'
[channel settings](https://www.mathworks.com/help/thingspeak/channel-settings.html)
and [Write Data API](https://www.mathworks.com/help/thingspeak/writedata.html).
For an existing channel, enable field 6 as velocity (mm/s) and field 7 as
semi-amplitude (degrees); existing uploads are not backfilled.

Create a separate publisher configuration with `sudoedit`:

```sh
sudoedit /var/lib/pendulum/thingspeak.json
```

Use the following contents, replacing `123456` with the real channel ID:

```json
{
  "enabled": true,
  "channel_id": 123456,
  "write_key_file": "/var/lib/pendulum/thingspeak.write-key",
  "include_humidity": true
}
```

Create the key file with an editor and paste only the Write API Key, optionally
followed by a newline. Keep the key out of shell commands, the shared receiver
configuration, version control and logs:

```sh
sudoedit /var/lib/pendulum/thingspeak.write-key
sudo chown pendulum:pendulum /var/lib/pendulum/thingspeak.json /var/lib/pendulum/thingspeak.write-key
sudo chmod 0600 /var/lib/pendulum/thingspeak.json /var/lib/pendulum/thingspeak.write-key
```

The key must be readable by the `pendulum` service account. Publisher settings and
credentials are separate from the dashboard's administrator token, recording
manifests and downloads. They are preserved when the application is updated.
Setting `enabled` alone does not activate the timer or bypass key validation.

## Preview without publishing

Inspect eligibility and the exact public fields/status without requiring a key
or making any network request:

```sh
sudo -u pendulum /opt/pendulum/venv/bin/python -m pendulum_pi thingspeak \
  --config /var/lib/pendulum/config.json \
  --publisher-config /var/lib/pendulum/thingspeak.json --dry-run
```

A dry run needs receiver settings and an eligible snapshot to show measurement
fields. It works even when publisher settings are absent or disabled. A skip explains why no measurement would
be sent. Demo and replay snapshots are always ineligible.

## Validate the key and enable the timer

From the Pi's repository checkout, run this **only when ready to make one real
status-only entry** in the configured channel:

```sh
sudo bash Raspberry.Pi/deploy/enable-thingspeak.sh
```

The helper stops any existing optional publisher activity, makes one bounded
validation request as `pendulum`, and enables the hourly timer only when both
validation and a final local readiness check succeed. `enabled` must be `true`.
It does not stop acquisition or other core services. Validation does not need an
eligible pendulum measurement: it posts a commissioning status without fields.

A syntactically plausible key is insufficient. Validation requires ThingSpeak
to return a positive entry ID for the expected channel. Success stores a local receipt at
`/var/lib/pendulum/thingspeak/validation.json`, binding the key fingerprint to the
channel. Routine publication and
timer activation require a matching receipt. A missing, malformed, unreadable,
replaced or unverified key keeps the publisher out of service. Validation errors
leave the timer disabled; fix the cause before running the helper again.

Explicit recommissioning removes the previous receipt before attempting a new
validation, so a failed attempt cannot reuse earlier proof.

The receipt records successful commissioning, not a promise that ThingSpeak will
accept future requests. A subsequently revoked key is discovered on an attempted
write. Definite rejection invalidates the receipt and prevents later measurement
requests until recommissioned. Transient network errors and ambiguous write failures, including a zero response
that could indicate rate limiting, retain an existing receipt and are skipped
until the next hourly attempt, without immediately retrying or repeating
validation.

Check readiness locally, with no network request:

```sh
sudo -u pendulum /opt/pendulum/venv/bin/python -m pendulum_pi thingspeak \
  --config /var/lib/pendulum/config.json \
  --publisher-config /var/lib/pendulum/thingspeak.json --check-ready
```

Exit status zero means enabled settings, readable key and matching validation
receipt; it does not mean that a current measurement is eligible or that the
network is reachable. `--dry-run`, `--validate-key` and `--check-ready` are mutually
exclusive modes. The helper uses `--validate-key` with a process runtime bound;
prefer it for commissioning over an unbounded shell invocation.

## Schedule, health and disabling

The timer uses monotonic time: the first attempt is about one hour after timer
activation (at startup or explicit enablement), then about one hour after the
preceding attempt finishes. It does not align to wall-clock hours or replay missed
runs. Normal operation is about 24 attempts a day.

```sh
systemctl status pendulum-thingspeak.timer --no-pager
systemctl list-timers pendulum-thingspeak.timer --all
sudo journalctl -u pendulum-thingspeak.service -n 30 --no-pager
```

The one-shot service normally appears inactive between attempts. It uses low
CPU priority, idle I/O scheduling and a 20-second runtime limit. Each invocation
makes at most one short-timeout HTTPS request, with no transport retry. A timeout
after sending has an unknown outcome and is not retried. A nonblocking lock
prevents overlapping invocations. The publisher has no serial or sensor access.

Uploads require a recent snapshot from the current Linux boot, real serial
acquisition, ready metadata, fresh capture, a valid target, a positive finite
300-swing mean outside learning, a finite shared gain/loss value and positive
finite BPM, and a fresh locked PPS timebase. Freshness is
recomputed using monotonic time; cached “fresh” flags cannot keep a stopped
producer eligible. Sensor freshness is checked independently. Recording may be
paused while eligible live measurements continue to publish: these controls are
independent.

To stop optional publication:

```sh
sudo systemctl disable --now pendulum-thingspeak.timer
sudo systemctl stop pendulum-thingspeak.service
```

Also set `enabled` to `false` in the publisher configuration if manual invocation
must skip publication. The receiver and recording services are unaffected. To
replace a key, disable the timer first, edit the key file, then rerun the
commissioning helper; the old receipt does not authorize the new key.

Updates stop optional publisher activity while installing. They restore a
previously enabled timer only if local readiness still passes. They neither
create publisher settings nor perform network validation. A disabled timer stays
disabled on update even when credentials are valid.

For a longer interval, use a systemd override. This example selects six hours:

```sh
sudo systemctl edit pendulum-thingspeak.timer
```

```ini
[Timer]
OnActiveSec=
OnActiveSec=6h
OnUnitInactiveSec=
OnUnitInactiveSec=6h
```

Then reload the units and restart the timer if it is already commissioned:

```sh
sudo systemctl daemon-reload
sudo systemctl restart pendulum-thingspeak.timer
```

Changing the timer does not change the fixed 300-swing estimator window.

## Live commissioning checklist

Before making the channel public:

- Compare one real entry with its source snapshot and read back raw field values
  to verify six decimals, rate sign and period units. Check chart rounding
  separately. Allow for the status-only commissioning entry having no fields.
- Confirm field labels, target period, 300-swing mean window, upload cadence,
  sensor placement and rate sign in the channel description. Include the clock's
  confirmed model/pendulum details and a project link.
- Exercise network loss, stopped acquisition, stale or missing sensors and a Pi
  reboot. Confirm gaps, independent omission of sensor values, bounded publisher
  runtime, continued acquisition and no catch-up or duplicate retry bursts.
- Check week/month charts and visible last-entry time. If charts join points
  across gaps, explain that lines do not represent continuous observations and
  a missing upload does not mean the mechanical clock stopped.
- Observe several scheduled uploads and a missed interval before enabling public
  viewing. Describe environmental correlations without implying causation.

No live ThingSpeak writes are part of the automated test suite. See the
[hardware acceptance checklist](HARDWARE_TESTS.md) for physical checks.
