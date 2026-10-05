# Bounded I²C recovery

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

The OLED uses **Wire** (SDA 18/A4, SCL 19/A5). BMP280 at 0x77 and SHT41 at 0x44 use **Wire1** (SDA 27, SCL 26/Qwiic). Each controller has its own fixed recovery state. Wire runs at 400 kHz and Wire1 at 100 kHz, both with a 10 ms Wire transaction timeout. Sensor libraries now explicitly select Wire1; older source used their Wire defaults.

## Detection and suspension

Only idle-line observations trigger recovery: SDA or SCL must be low on two observations at least 1 ms apart, with transactions suspended from the first observation. A NACK, absent device, invalid sensor value, or timeout with both lines high does not consume a recovery attempt. Normal device initialization backoff is independent and cannot reset the bus budget.

The guard runs in the foreground after the existing ingestion backlog check. OLED transfers, sensor operations, and diagnostics respect the guard on their own bus. Capture, SD logging, and the other controller continue. No recovery runs in an interrupt and there is no automatic power cycling.

## Attempt and verification

Each attempt closes only the affected controller, disables its peripheral ownership through `TwoWire::end()`, and releases its SDA/SCL as inputs. GPIO operations only drive LOW or release to external pull-ups. If SCL cannot rise within 100 microseconds, clock recovery stops promptly with zero pulses. Otherwise it sends at most nine recovery clocks, stopping as soon as SDA releases. Each SCL rise has the same bounded wait. If SDA is still held after nine clocks, STOP is impossible and is skipped. When SDA has released, STOP preparation pulls SCL and SDA low, releases SCL, then releases SDA while SCL is high. This final STOP preparation is separate from the reported recovery-clock count.

The controller is restored at its configured speed (400 kHz for Wire, 100 kHz for Wire1) and 10 ms timeout even after a failed attempt. Both lines are checked again after restoration; STOP success alone is insufficient. GPIO delays total at most about 1.2 ms per attempt, excluding controller calls and log writes. No waiting for the retry interval occurs inside a call.

Released lines verify **bus availability**, not device presence. Device responses are reported separately by the ordinary address probes (`responding`, `address NACK/absent`, etc.) and sensor health. This lets an unplugged device remain absent without repeatedly recovering a healthy bus. It does not establish that an OLED rendered pixels or that a sensor measurement is valid.

## Budget, exhaustion, and re-arming

There are at most **two attempts per episode**, at least one second apart. The next eligible foreground service performs a due attempt; ingestion pressure can defer it. After the second failed attempt the state latches exhausted, with no transactions or further pulses on that bus. Sensor polling, initialization retries, and repeated diagnostics cannot re-arm it.

Exhausted buses sample idle lines every 30 seconds. A high sample starts verification, followed by two further high observations at least one second apart (three samples spanning at least two seconds). Any observed low retains the consumed attempt budget. Only completion of this stable verification, or a firmware restart, re-arms the two-attempt budget. A successful clock recovery can resume transactions immediately but still retains its consumed budget during this verification interval; flapping cannot obtain unlimited attempts. Manual/spontaneous recovery keeps transactions suspended until verification completes, then logs recovery and re-arms. Stability here means sampled idle-line stability; it cannot rule out faults between samples and does not require every device to ACK.

## Diagnostics and memory

`i2c.recovery` records detection, each attempt (`1/2` or `2/2`), actual recovery pulses, STOP success, clock obstruction, restored line levels, and outcome (`recovered`, `still failed`, `recovery exhausted`). Stable re-arming and spontaneous/manual recovery are separate transitions. Unchanged failures do not emit per-poll recovery logs. The existing `i2c.health` record every 30 seconds explicitly distinguishes Wire and Wire1 pin levels and device probes. Logs use the existing SD diagnostic rate limits and are available while logging is open; early boot or disabled logging does not retain a recovery history.

Recovery uses two fixed state objects and bounded stack formatting buffers, with no new heap allocations or growing buffers. The existing device libraries and OLED framebuffer retain their own allocations. Host tests verify the recovery policy and adapter using deterministic GPIO/controller substitutes; electrical behavior and serial capture continuity still require board testing. The existing BMP280 successful initialization delay (100 ms) remains a library limitation, separate from bounded recovery.
