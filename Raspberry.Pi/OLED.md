# Pi OLED display

The SSD1306 uses the same information grouping as the deprecated Uno:
30 seconds of rating, 15 seconds of supplemental status, then 15 seconds of
PPS timebase. Rating uses the same canonical PPS-calibrated 600-second swing
mean as the HTTP dashboard and ThingSpeak publisher. Each completed swing's four
component durations use the unchanged PPS dual EWMA available at observation;
period, BPM, gain/loss and balance all share that one window. OLED text only
rounds those values to fit 21 characters; it performs no second calculation.

| Row | Rating                       | Supplemental status            | Timebase                  |
| --- | ---------------------------- | ------------------------------ | ------------------------- |
| 0   | UTC or highlighted fault     | UTC or highlighted fault       | UTC or highlighted fault  |
| 1   | Mean full period (seconds)   | T:valueC RH:value%             | TIMEBASE Hz               |
| 2   | Mean full-swing BPM          | Pressure                       | Blended frequency         |
| 3   | Gain(+)/loss(-), seconds/day | GPS:state AGE/HAG:seconds      | 20-second frequency       |
| 4   | Signed open difference dO    | TIMEBASE:PPS/HOLD/WAIT/STL     | 1-hour frequency          |
| 5   | Signed block difference dB   | Logging and SHT/BMP health     | Blank                     |
| 6   | MEAN600s filling/timebase    | MEAN 600s; optional FC:cycle   | PPS status                |
| 7   | Warnings/transient losses    | Warnings/transient losses      | Warnings/transient losses |

The rating mean is provisional while filling 600 seconds of fresh captured
duration. A missing target gives `R: --`; stale or unavailable measurement values
are replaced by explicit stale/waiting rows. There is no nominal clock-frequency
fallback. Expired PPS calibration masks current values; recovery retains prior
measured observations while provisionally refilling 600 fresh seconds. Period is
a full swing; signed `dO` and `dB` compare tick minus tock open and blocked
durations respectively. Positive rate is gaining; negative is losing.

The status page labels `MEAN 600s` and, when the optional individual-swing
forecast is enabled, its configured cycle length as `FC:15` for example. Forecast
predictions and errors are shown on the HTTP dashboard; they do not change the
OLED rating or the published gain/loss. See [configuration](CONFIGURATION.md) for
cycle lengths and the [dashboard reference](OBSERVATORY.md) for metric meanings.

Sensor codes are R ready, D degraded, S stale, X offline, I initializing.
Holdover age is reported by the Nano; record age is the age of the last
nonduplicate CPS observation, not GPS serial-message arrival. Timebase
frequencies retain six fractional Hz digits, with `--` before initialization
and an explicit stale state when retained values are no longer used.

The UTC header uses cached Pi UTC and fresh synchronized chrony health, refreshed at
15-second clock-slot boundaries, with the same trailing `Z` as the Uno. Without that evidence it shows a waiting message.
Active faults replace the header, invert that row and rotate every two seconds.
The bottom ticker is always inverted and displays `STATUS: OK` when clear.
It advances in whole 21-character windows every four seconds, then moves to
the next message. Fault-set changes reset both banner and ticker rotation. Increasing loss/error counters
show a 12-second `! NANO DROPS RISING` warning; historical totals and counter resets do not create a
permanent warning. The `LOG` and `SD` fields retain their labels; on the Pi they describe
recording and its storage readiness rather than an external SD module.
The Pi renderer uses the same classic Adafruit GFX 5-column glyphs with a
blank spacing column: 21 characters per 128-pixel row, eight 8-pixel rows.
The bundled ASCII subset retains the upstream BSD license.

Rating and timebase bodies update every two seconds, status every ten seconds.
Screen transitions and forecast cycle changes refresh the body immediately.
Fault banners update independently, including canonical feed staleness at
three seconds and period staleness at ten seconds. Swing and PPS feed warnings
use acquisition's latest accepted, nonduplicate CSW and CPS receipt timestamps,
independently of the saved analysis publication cadence. Recovery clears those
timestamps until fresh validated captures arrive. A delayed or unavailable saved
analysis result still produces the separate period-stale warning. The header and
warning ticker are evaluated each worker tick without network requests.

After rendering, the sender compares all eight 128-byte framebuffer pages
against the last successful frame. It skips unchanged pages and sends only
the changed column span within each changed page. Between the periodic
checks below, identical frames cause no I²C display writes. First initialization and recovery require a complete
refresh; any partial-write failure invalidates the cache. As on the Uno,
there is a full-refresh backstop every 60 seconds and a display ACK check every
30 seconds. The Pi uses a harmless SSD1306 NOP command for this check, since
its hardware driver rejects zero-length I²C address probes. Neither ACKs nor
successful writes prove that the panel actually rendered the image.

All display writes use the shared bus lock. Display operations never change
bus speed: the OLED and sensors use the single boot-configured 400 kHz clock,
with whole-bus 100 kHz available as a commissioning fallback. See
[I²C recovery](I2C_RECOVERY.md) for configuration and physical acceptance.
