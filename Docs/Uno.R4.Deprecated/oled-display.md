# External OLED display

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

The 128×64 SSD1306 alternates a **rating screen for 30 seconds**, a **supplemental screen for 15 seconds**, then a **timebase screen for 15 seconds** (60 seconds total). All three use the existing 6×8 default font, with a maximum of 21 characters (126 pixels) per row. The six body rows give each measurement a short and a long row: full-width numbers take priority over squeezing high-resolution values into aligned columns. The first and last rows are shared by all screens. Rating and timebase values redraw every two seconds, supplemental values every ten seconds, with an immediate body redraw on a screen transition or configuration request. Work remains deferred while ingestion or an existing OLED transfer is busy.

| Row | Rating screen                             | Supplemental screen             |
| --- | ----------------------------------------- | ------------------------------- |
| 0   | UTC date/time or highlighted active fault | Same                            |
| 1   | Period, short EWMA                        | Temperature / relative humidity |
| 2   | Period, long EWMA                         | Pressure                        |
| 3   | BPM, short estimate                       | GPS/PPS state and age           |
| 4   | BPM, long estimate                        | Current timebase                |
| 5   | Signed dBlock, short EWMA                 | Logging, SD and sensor health   |
| 6   | Signed dBlock, long EWMA                  | Active EWMA half-lives          |
| 7   | Active warnings and transient alerts      | Same                            |

On the supplemental screen, `S` denotes the SHT sensor and `B` the BMP sensor. Their health codes are `R` ready, `D` degraded, `S` stale, `X` offline and `I` initializing. `AGE` is the age of the latest status-bearing record (PPS); `HAG` is reported holdover age. `TIMEBASE` shows `PPS`, `HOLD`, `NOM`, `WAIT` or `STL`.

The logger's cached UTC clock is refreshed at 15-second boundaries; rendering makes no network request. Before synchronization it says `UTC: waiting for sync`. Active faults replace the date with an inverted warning and concurrent faults rotate every two seconds. Serial errors, SD logging off/not ready, stale canonical feeds, stale period, received GPS no-PPS or stale status, and degraded/stale/offline environment sensors retain their fault priority. Increasing drop counters warn for 12 seconds; historical totals and counter resets do not create permanent warnings. The bottom ticker retains active warnings and transient messages, scrolling in 21-character windows every four seconds. Routine GPS, timebase, logging and environment information lives on the supplemental screen; the old d5M explanation and routine IP/feed-age/drop-counter rotation are removed.

## Display estimators

These are downstream **display-only** estimates. Authoritative analysis remains offline from raw `PCPS` / `PCSW` and swing measurements. No raw capture semantics, CSV fields or logged scale provenance are changed.

For each accepted full-swing measurement, independently update short and long period and signed block-difference EWMAs:

```
alpha = 1 - exp(-ln(2) * swing_seconds / half_life_seconds)
estimate += alpha * (measurement - estimate)
BPM = 60,000,000 / period_us
```

The first valid measurement seeds both horizons. Subsequent weights use measured full-swing duration, not serial arrival spacing, so draining a queue in one service does not alter smoothing. At steady cadence the most recent half-life carries approximately half the weight. Missing/discontinuous captures restart the estimate rather than pretending a larger interval was observed. This is constant-memory smoothing, with no hour-long sample array. BPM is calculated from its corresponding period estimate; instantaneous BPM is never separately averaged.

`dBlock` is the signed difference between the two captured swing blocks and is smoothed directly. Period and block intervals come from wrap-safe differences of the raw shared-counter edges. They use the causal PPS frequency estimate described below while fresh, otherwise nominal frequency. The Nano has already applied capture projection and input-filter-delay compensation. The logged captures remain unchanged.

The default half-lives are **5 minutes short / 60 minutes long**. On `/uno`, `oledShortMinutes` accepts 1–30 minutes and `oledLongMinutes` 15–360 minutes, with long at least twice short. See [tunables-index.md](tunables-index.md) for validation and persistence. Changing the configuration resets the display estimators and updates the supplemental screen on its next available refresh, without restarting capture or logging.

## Causal PPS timebase

Previously, each qualified adjacent locked PPS interval replaced the clock scale directly. `CanonicalTiming::PpsClock` now seeds both frequency states from the first qualified interval and updates on every accepted one-second observation:

```
fast += (1 - 2^(-1/20)) * (observation - fast)
slow += (1 - 2^(-1/3600)) * (observation - slow)
EST = 0.75 * fast + 0.25 * slow
period_us = period_ticks * 1000000 / EST
```

These are **half-lives**, with one update per qualified interval rather than elapsed arrival time. The double-precision coefficients are approximately 0.0340636710751544 and 0.000192522348682555. `PeriodDisplayState::observeSwing` uses the double returned by `PpsClock::correctedHz`; the diagnostic screen reads that same clock through `timebaseClock()`. No integer-Hz rounding or second display-only estimator is introduced.

Qualification retains adjacent sequence, locked endpoints, unchanged drop counter and nominal frequency, and the existing ±10% nominal interval bounds. Duplicate, unlocked, missing and rejected observations do not advance either EWMA. A gap requires a new adjacent valid pair. The last estimate remains usable for less than 5000 ms after the last accepted observation; at exactly 5000 ms it becomes stale. States are retained through gaps, including long gaps, and resume from the next qualified observation without inventing samples. Capture-timeline restart or nominal-frequency change resets the clock. The existing explicit nominal-frequency fallback remains for uninitialized/stale clocks; downstream display smoothing resets when switching between corrected and nominal scales.

The timebase screen's body is:

```
TIMEBASE Hz
EST   16000029.347000
20s   16000029.347000
1h    16000029.347000

PPS LOCKED
```

Each frequency row uses `%-3s %17.6f`: a four-cell label area followed by a right-aligned 17-cell number, with six fractional Hz digits. The classic Adafruit GFX font at size 1 occupies 6×8 pixels per character including spacing. A complete 21-cell row occupies x=0..125, leaving two pixels on the 128-pixel panel. Even 4294967295.000000 fits its numeric field. Body rows occupy y=8..55; the existing date/fault row at y=0 and ticker at y=56 remain unchanged. Compile-time geometry assertions and tests using a larger buffer verify widths before truncation, as well as the actual production rendering path. Fractional digits describe estimator state, not guaranteed physical accuracy.

Before initialization the numeric fields show `--`. Status distinguishes `WAIT`, `LOCKED`, `REJECTED`, `NO PPS`, `HOLDOVER`, `ACQUIRING` and `STALE`. Rejected/unlocked status shows that a recent retained estimate is being used; `STALE` explicitly marks retained diagnostic values that are **no longer used** for conversion. The rating and supplemental screens retain their contents, formatting and layouts.

Frequency smoothing does not integrate or absorb PPS phase error. The Uno capture path has no existing phase accumulator; raw `PCPS`/`PCSW` records and any separate protocol phase information remain unchanged for offline analysis. No offline reconstruction is modified.

## Startup, continuity and precision

A `~` next to a horizon means it is still learning: less than one configured half-life of accepted swing duration has accumulated since initialization. A value can be useful immediately without implying a mature one-hour history. Waiting and stale states use explicit placeholders. Ten seconds without a fresh accepted swing makes the readings stale. The next valid measurement after that gap starts a new estimate.

Invalid timing, nominal-frequency changes, transitions between calibrated and nominal timing, swing sequence/edge discontinuities, relevant cumulative drop-counter changes and capture-timeline resets restart the display state. Duplicate swings do not count or prolong freshness. OLED redraws, page transfers and screen switches never count as measurements or change estimator state.

Adaptive precision changes formatting only. Period and dBlock normally show one fractional microsecond digit and can show two; BPM normally shows five fractional digits and can show six. Each horizon and measurement tracks squared changes of its displayed estimate with a 30-second exponential time constant. It adds a digit after 60 measured seconds with RMS movement below half a fine digit; it removes that digit after 6 measured seconds above 1.5 fine digits. Separate thresholds and dwell times suppress chattering. This indicates numerical stability, not guaranteed physical accuracy. Negative rounded zero is suppressed. An over-wide value first drops fractional places, then removes optional spacing if necessary. The full protocol range still fits; values beyond representable width use an explicit `range` placeholder rather than wrapping or silently clipping.

The old frozen `REF(45)`, `dREF` and rolling `d5M` rows are superseded. The obsolete display-specific formatter is removed; the reusable rolling-mean utility and its regression tests remain, with no rolling history allocated in the live OLED state.

## Changed-page transfers and diagnostic experiment

The existing Adafruit framebuffer is reused. Eight CRC32 fingerprints (32 bytes)
identify changed 128-byte pages, corresponding to the eight text rows. No second
framebuffer is allocated. Unchanged pages send no pixel data. Fingerprints are
committed only after successful page writes; a failed or partial page is invalidated
and resent on a later refresh. CRC32 is a compact change detector, not a mathematical
guarantee of equality. Once a minute all pages are refreshed as a backstop. A failed
OLED address probe also invalidates the remembered page state.

After panel initialization, each foreground service performs at most one I²C transaction: an address probe,
page-address command, or at most 31 pixel bytes plus the control byte. The normal
main loop services serial ingestion between chunks, including during full refreshes.
The framebuffer is not redrawn during a transfer. Display/probe operations use a
10 ms timeout on their own controller (`Wire` for OLED, `Wire1` for sensors).
The bus guard suspends transactions during a stuck-bus episode; see
[i2c-recovery.md](i2c-recovery.md). This is not a guarantee against every peripheral stall. Unlike Adafruit's full-frame `display()`
call, the foreground path checks each transaction result and stops on failure.

Two short records are attempted in the UNO diagnostic log every 30 seconds,
independently of the optional verbose service heartbeat. They use the existing SD
diagnostic rate limits and fixed-size counters, with no growing in-memory history:

| Category      | Fields and interpretation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `oled.health` | `ready`: framebuffer initialization succeeded (not proof the panel acknowledged initialization); `try`, `ok`, `fail`: changed-image attempts, complete transfers and aborted transfers; `defer`: seconds with display work deferred for ingestion, not individual loop skips; `active`: transfer pending; `ok_age_ms`: time since last successful image transfer; `frame_max_ms`: maximum successful transfer duration including waits between chunks; `tx_max_us`: maximum OLED transfer transaction duration; `last_err`: most recent nonzero OLED transfer error, retained after recovery; `same`: unchanged images skipped; `pages`: successfully transmitted pages |
| `i2c.health`  | `Wire OLED_3d` and `Wire1 SHT41_44` / `BMP280_77`: readable address-probe results, explicitly routed to each bus; `SDA`/`SCL`: that bus's current levels; `error_SDA`/`error_SCL`: Wire levels on the last failed OLED transfer; `pending` and `age_ms`: probe-round status                                                                                                                                                                                                                                                                                                                                                                                             |

Probe results say `responding`, `address NACK/absent`, `data NACK`, `controller error`,
`timeout`, `controller not initialized`, `suspended for bus recovery`, or `not probed`.
Unknown ages are 4294967295; error line levels are -1 until the first failed transfer.
Only the three configured addresses are probed, one per idle service, every 30
seconds. The first health record precedes the first round; subsequent records show
the preceding results. While a round is pending, some results may be older than
others. Probes yield to ingestion and do not perform a complete address scan.

An increasing `defer` count with no transactions points toward scheduling pressure.
Increasing failures, lost acknowledgements or low bus lines support an I²C-path
problem. Combine these records with `sensor.health` and `mem` records. An unchanged
screen can legitimately increase `ok_age_ms` without missed work. ACKs and completed transfers still do not prove pixels changed on the panel.
Bounded bus recovery runs independently for each controller. Initial OLED setup deferred by a stuck bus resumes after release; ACKs do not verify panel contents.
