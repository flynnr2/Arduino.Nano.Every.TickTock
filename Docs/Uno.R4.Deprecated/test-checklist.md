# Validation checklist

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

## Automated host regressions

With Python 3 and a C++11 compiler, run from the repository root:

```bash
python3 tests/uno_r4/run_host_tests.py
```

Tests use production helpers and source functions with deterministic hardware substitutes. They cover listener recovery, metadata negotiation while receiving records, independent sequence joins/restarts/wrap, sensor freshness and recovery, and logging limits/failures. See individual test files for the exact boundary mocked.

Build the sketch with `arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi Uno.R4.Deprecated`. Install the [Uno README dependencies](../../Uno.R4.Deprecated/README.md) first. Record the board core and library versions, flash/static RAM totals, and compiler warnings for comparisons. Neither a compile nor a host regression measures actual stack peaks or peripheral latency.

## Board acceptance tests

1. Independently start/restart the UNO and Nano at randomized offsets, including mid-record and during metadata replay. Delay/omit metadata, then restore it. After a validated stream join, account for every sequence discontinuity; do not count pre-join sequences as lost records.
2. With continuous PPS/swing traffic, disconnect/reconnect Wi-Fi repeatedly, change credentials while connected, request manual reconnect, boot without credentials, and hold a connected interface without an IP. Verify HTTP returns once usable and diagnostics do not repeat per service pass.
3. Enable the LED startup test. With no active fault override, verify all frames display while serial records continue being stored; faults or backlog may postpone the animation.
4. Exercise diagnostic rotation with prefilled files or reduced test-only limits. Verify either diagnostic or measurement limit moves PCPS, PCSW, UNO and STS to the same suffix, preserving all old members even after more than four rotations. Check quote escaping and complete CSV headers.
5. Exercise open failures and short writes in each member. Canonical open failures must retry the same set while healthy members continue; a short write must end the whole set and recover to one fresh shared suffix. Completed segments must remain untouched; a partial tail must not gain an appended retry. Check loss counts, bounded filename collision scanning and paced recovery. Stop logging during a pending recovery and verify it stays stopped. Revisit a previous day via simulated clock adjustment without overwriting its files; check all four daily paths change together, including when a diagnostic is the first record of the day.
6. Remove/reinsert the card, fill it, and cut power. Verify recovery preserves files and distinguishes intentionally stopped logging from missing records while recording was requested. Inspect partial tails and filesystem integrity externally.
7. Disconnect each environmental sensor separately. The other must keep updating; expired values become CSV `nan`; reconnect restores readings. Simulate constant finite pressure separately and check suspect status without falsely reporting an I2C failure.
8. Load registered HTTP endpoints while recording. Using diagnostic instrumentation as needed (there is no public JSON health route), record the minimum free memory, serial service maximum gap, ingestion queue peak/drops, per-stream write durations and loss counters. Keep the existing queue capacities. Exercise long-uptime timer wrap with a test clock where possible.

## Resource and latency interpretation

Canonical set rotation build (UNO R4 core 1.6.0, SD 1.3.0): 175,780 bytes flash, 23,260 bytes static globals, 9,508 bytes remaining for stack/heap. Host regressions cover all four size triggers, matching suffixes, legacy collisions, retention beyond four sets, cold-restart policies, daily rollover, open/header/data-write failures, remount and stopping during recovery. The board build passes; physical SD rotation and power-loss behavior still require the board acceptance tests above.

Historical measurements from before statistics removal (not current build totals): baseline on the local test toolchain (UNO R4 core 1.6.0, SD 1.3.0): 174,432 bytes flash, 22,620 bytes static globals, 10,148 bytes remaining for stack/heap. Revised static globals: 22,956 bytes, an increase of 336 bytes (about 1% of board RAM), leaving 9,812 bytes for stack/heap. This is 80 bytes above the initial provisional 256-byte state-growth target; queue capacities are unchanged. Runtime headroom must be measured separately.

Historical compiler stack-frame checks (`-fstack-usage`) showed the normal Nano service frame at 552 bytes versus 528 originally. The initial async build inlined the 848-byte metadata handler into this path; explicit handler boundaries avoid that increase. The asynchronous startup initializer uses 16 bytes versus 520 for the former blocking reader. These are per-function compiler estimates, not whole-call-chain or measured runtime peaks.

The installed core uses a 512-byte UART ring (511 usable bytes). At 115,200 baud with 8N1, continuous traffic can fill that space in roughly 44 ms; a partly full buffer has less time. Real Nano bursts, metadata replies and other library calls determine the applicable bound. The existing one-second HTTP budget and 1.5-second quarantine threshold are not safe serial-latency guarantees.

The SD library does not expose flush completion status. Modem-command waits, I2C operations, SD calls and synchronous HTTP responses still require board measurements. The installed BMP280 3.0.0 driver waits 100 ms after a successful `begin()`, so even staged sensor initialization is not a hard real-time guarantee. SHT4x 1.0.5 reinitialization is guarded against its dangling-pointer failure path. No test here establishes zero loss under arbitrary peripheral stalls or power interruption.

## Statistics removal

For the 400 kHz OLED updates, check `oled.health` and `i2c.health`
every 30 seconds alongside sensor/memory logs. Host tests cover changed and unchanged
pages, disjoint-page changes, partial writes, transaction failures, timer rollover,
one-transaction-per-service scheduling, ingestion deferral and probe results. Bench
testing must still establish actual transfer latency and capture continuity.

The local UNO R4 core 1.6.0 / SD 1.3.0 build of this experiment uses 164,248 bytes
flash and 22,932 bytes static RAM, leaving 9,836 bytes for stack/heap. Static RAM is
120 bytes above the preceding 22,812-byte build. There is no second OLED framebuffer
or new heap allocation for diagnostics/deltas. Compiler stack estimates with
`-fstack-usage` are 32 bytes for `Display::service`, 24 for its I²C writer and 272 for
the separate diagnostic formatter; these exclude called functions and are not
whole-call-chain or runtime high-water measurements. All host regressions passed.

- Verify PCSW contains nine capture fields and PCPS eight, with the three appended environment columns in each file.
- Check OLED short/long period, BPM and dBlock, alternating supplemental health, shared UTC/fault row and alert ticker. Verify half-life learning, stale/reset behavior and numerical fit as described in `oled-display.md`.
- Confirm `/stats`, `/stats/`, **/stats.json**, `/json` and `/log` return 404 and the home page has no stats link.
- Confirm `/uno` exposes only the new display EWMA half-lives alongside the existing UNO settings; saved logging/Wi-Fi configuration survives migration and the new display settings survive reboot.
- Soak-test serial capture and SD logging on the board; removal of statistics does not bound remaining SD, Wi-Fi, HTTP or sensor stalls.

## OLED pendulum layout and fault priority

- Confirm UTC date/time advances at 15-second boundaries, with `UTC: waiting for sync` before the logger has NTP time. Rendering must not request network time.
- Confirm the rating screen lasts 30 seconds, supplemental screen 15 seconds and timebase screen 15 seconds, including across `millis()` wrap. Rating uses two full-width rows per measurement, short then long, with units visible and no wrapping on the 128×64 panel.
- Check period, BPM and signed dBlock at ordinary and extreme values, both normal and extra precision. Learning markers should remain until the corresponding half-life of accepted swing duration has accumulated. Stale readings must become explicit placeholders.
- Confirm supplemental temperature, humidity, pressure, GPS/PPS state and age, timebase, logging/SD, sensor health and active EWMA horizons are readable.
- Stop logging, trigger a serial error, or disconnect a sensor: a highlighted warning must replace date/time on either screen. Concurrent faults should cycle every two seconds and clear automatically on recovery. An OLED/bus failure may prevent warnings from rendering.
- Confirm the bottom ticker carries active warnings and transient alerts; routine status belongs on the supplemental screen. A new drop increase warns for 12 seconds; historical totals and a counter reset must not leave a permanent warning.
- Verify changed-page updates and foreground ingestion continue during screen transitions and warnings; no second framebuffer or growing message history is allocated.
- Check `/uno` bounds (short 1–30 minutes, long 15–360 minutes, long at least twice short), persistence across reboot and migration from older configuration. An OLED-only change must not restart logging or change captured records; estimators restart their learning indication.
- Confirm sustained stable readings gain a digit only after the documented dwell, and renewed variation removes it without chattering. Screen switching and redraws must never count as measurements.

### OLED redesign validation (2026-09-21)

All host regressions pass, including production OLED row geometry, screen rotation,
configuration/HTTP validation, byte-interrupted EEPROM migration, continuity and
adaptive precision. Production row snapshots were rendered with Adafruit GFX's
actual 5×7 font to inspect normal/fine/mature readings, supplemental status and
fault/extreme-value layouts. Every row fits within 21 characters / 126 pixels;
the full signed dBlock protocol range retains its value and unit by reducing
fractional places and optional spacing when needed.

UNO R4 WiFi core 1.6.0, SD 1.3.0, SSD1306 2.5.17 and GFX 1.12.6 compile successfully:
183,516 bytes flash and 22,588 bytes static RAM, leaving 10,180 bytes for stack/heap.
The same-toolchain baseline used 180,404 bytes flash and 23,260 bytes static RAM,
so the redesign adds 3,112 flash bytes and saves 672 static RAM bytes. This build
and host validation do not measure runtime stack peaks or replace a physical
OLED/ingestion/logging soak test; hardware flashing/soak was not performed.

## Bounded I²C recovery validation

The isolated recovery build (UNO R4 core 1.6.0, SD 1.3.0, compared with commit
`e6912df`) uses **22,852 bytes static RAM**, up **32 bytes** from 22,820. This
leaves 9,916 bytes for stack/heap. Fixed formatting buffers are on the stack;
the recovery formatter uses 280 bytes, and the health formatter grew from 192
to 280 bytes. These are not whole-call-chain high-water measurements. No new
heap allocation is introduced by recovery.

Run the host regression command above. The focused recovery and adapter tests cover
first/second-attempt success, persistent exhaustion, SCL held low, ordinary NACKs,
isolation, restoration verification, flapping, timer rollover, stable manual
recovery/re-arming, GPIO ownership and LOW-only drives. Sensor/OLED integration
tests verify routing, suspended operations and ingestion priority.

Board follow-up (firmware was not uploaded for this change):

- Unplug a sensor with both bus lines released: device health may fail, but no
  recovery attempts should occur and the other sensor should continue.
- Hold SDA low on each bus separately: observe attempts 1/2 and 2/2 followed by
  exhaustion, with capture/logging and the other bus continuing. Release SDA:
  after a 30-second check and stable verification, expect manual recovery.
- Hold SCL low: expect zero recovery pulses and an explicit clock-obstruction
  message. Check the waveform and absence of any active-high drive.
- Release SDA during recovery clocks: verify at most nine recovery pulses, STOP
  where possible, restored 400 kHz OLED and 100 kHz sensor traffic, and separate device probe results.
- Check repeated sensor initialization or OLED refreshes do not reset an
  exhausted budget; compare capture-drop counters and serial service latency.

See [i2c-recovery.md](i2c-recovery.md) for the exact verification and re-arm criteria.

- Timebase diagnostics: verify title, EST/20s/1h (six fractional Hz digits), and PPS status fit without clipping on the physical panel. Host tests check all 21 cells at the production 6×8 font metrics, startup/status/extreme-frequency rows and exact 30/15/15-second boundaries. Exercise PPS loss through the five-second stale boundary and recovery; retained stale values must be labelled STALE.
