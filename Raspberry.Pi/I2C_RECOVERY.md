# Shared I²C bus recovery

OLED, SHT4x and BMP280 use hardware bus 1 on GPIO2/SDA and GPIO3/SCL.
Their worker processes remain independent of serial acquisition. A small
`pendulum-i2c-recovery.service` runs as root because it must temporarily
unbind/rebind the Linux controller. The sensor and OLED services remain
unprivileged. The helper accepts only a fixed fault notification, never
client-supplied paths, commands, pins or bus numbers.

## Fixed bus speed

Use these boot settings in `/boot/firmware/config.txt`, then reboot:

```ini
dtparam=i2c_arm=on
dtparam=i2c_arm_baudrate=400000
```

**400 kHz applies to every device.** No worker or recovery operation changes the
bus speed; rebinding restores the same device-tree configuration. Recovery
clocks are manually paced low/release pulses while the controller is detached,
not a change to its configured transaction speed.

The [BMP280 datasheet](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmp280-ds001.pdf)
supports I²C up to 3.4 MHz, the [SHT4x datasheet](https://sensirion.com/resource/datasheet/sht4x)
supports standard, fast and fast-mode-plus operation, and the
[SSD1306 datasheet](https://cdn-shop.adafruit.com/datasheets/SSD1306.pdf),
table 13-6 (PDF page 54), specifies a minimum 2.5 µs clock cycle (400 kHz).
The boot parameter is in the [Raspberry Pi overlay reference](https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README).

If the assembled wiring is unreliable at 400 kHz, set
`dtparam=i2c_arm_baudrate=100000` and reboot; all devices then run at 100 kHz.
Check 3.3 V pull-ups, parallel breakout resistors, rise times and wiring length.
A device's speed rating does not prove the assembled bus will meet its timing.

## What happens after an error

1. A peripheral retains its last good values/age, closes its failed connection
   and requests recovery. Ordinary initialization retries still back off up to
   60 seconds. Invalid readings and address NACKs can request inspection but
   cannot themselves cause recovery pulses.
2. The helper takes the same exclusive file lock used around every sensor
   initialization/measurement and OLED initialization/transfer. It observes
   GPIO2/3 without changing their pin functions. Both lines high means no bus
   recovery and no attempt consumed.
3. If either line is low on two idle observations at least 1 ms apart, transfers
   are suspended. The helper releases its lock and waits for every open
   `/dev/i2c-1` descriptor to close. Both workers close their handles on the
   next tick, including during a long retry backoff. An independent application
   holding the device open leaves recovery in `quiescing`; it cannot force an
   unbind. Before releasing Linux ownership, the helper records a new
   bus generation, consumes one attempt and records that restoration is pending.
4. It unbinds BSC1, requests GPIO2/3 exclusively using libgpiod open-drain
   outputs, and releases both lines. SCL held low means zero recovery pulses.
   Otherwise it clocks SCL at most nine times, stopping when SDA releases.
   It generates STOP only if the lines permit it. Outputs only pull low or
   release to external pull-ups; they never drive high.
5. It releases GPIO ownership and rebinds Linux in a `finally` path. Workers
   reopen bus/driver objects after release and observe the new generation;
   the OLED also invalidates its display cache and refreshes the whole screen.

There are **two attempts per episode**, at least one second apart. After two
failed attempts the bus stays suspended and is checked every 30 seconds.
Three high observations at least a second apart (spanning at least two seconds)
re-arm the budget. A successful clear permits transfers during this verification
period but does not immediately grant another two attempts. Driver retries,
worker restarts and helper restarts cannot reset the saved budget. Reboot clears
`/run` and starts a new budget.

If the helper is killed while the controller is detached, the saved pending
marker keeps workers suspended; helper startup restores the controller before
further operation. Restoration failures remain blocked and are retried every
30 seconds without additional pulses or budget resets. Kernel calls can block;
Python/Linux timing is not hard real-time. The helper has its own 30-second
process watchdog. It never power-cycles a device or resets the Nano.

## Supported setup and diagnosis

Automatic clock recovery is restricted to the **Pi Zero 2 W**, BCM2835 GPIO
controller, hardware bus 1/BSC1 on GPIO2/3 in ALT0, and a fixed 100 or 400 kHz
boot setting. It refuses another board/controller/pin mux and refuses bus 1
when kernel I²C client devices are registered. Other buses retain normal driver
retry behavior. Unsupported recovery is reported explicitly.

Bus identity is checked through `/sys/bus/i2c/devices/i2c-1`; the legacy
`/sys/class/i2c-adapter` directory is not required. The resolved adapter must
still belong directly to the expected BSC1 controller with its expected driver.

GPIO discovery resolves `/dev/gpiochip*` symlinks before counting matching
controllers, so compatibility aliases for the same chip do not make it
ambiguous. It still requires exactly one `pinctrl-bcm2835` controller. If
discovery fails, the error includes the distinct match count and observed
chip labels; inspect `ls -l /dev/gpiochip*` and `gpiodetect` (if installed)
on the Pi. An `unavailable` recovery state with `blocked: false` does not
stop normal sensor/OLED transfers: devices can remain healthy while automatic
bus clearing is unavailable.

All userspace bus users must obey the shared lock: do not run `i2cdetect`,
independent sensor scripts or another display service while the application is
running. Install/start the helper with both workers stopped. The installer does
this; manual installs without its runtime files retain ordinary retries and
report recovery unavailable.

Recovery state is readable in `/run/pendulum-i2c/status.json`, the service
journal, the sensor health records and the OLED health record. The usual API
status carries those health records. `generation` invalidates old handles;
`attempts`, `blocked`, `state`, `error` and `result` distinguish unavailable
recovery, exhaustion, release and restoration failure. `result` includes the
actual pulse count and STOP outcome. A released bus is not proof that a sensor
responds or that its values are valid.

```sh
systemctl status pendulum-i2c-recovery pendulum-sensors pendulum-oled
journalctl -u pendulum-i2c-recovery -n 30
sudo cat /run/pendulum-i2c/status.json
```

## Hardware acceptance

Automated tests exercise sequencing, locking, retries and failure paths with
substitute GPIO/controllers. Physical acceptance remains outstanding:

- Confirm all three addresses and stable sensor readings/display at 400 kHz.
- With a suitable controlled fault fixture, check SDA release after a partial
  transaction, the maximum nine recovery clocks and STOP on a logic analyzer.
- Confirm permanently low SDA exhausts two attempts; low SCL produces no clocks.
- Confirm no OLED/sensor transaction overlaps GPIO recovery, controller speed
  is unchanged after restoration, and all driver handles reopen correctly.
- Confirm unplugging a device on an otherwise idle-high bus causes retries but
  no recovery pulses, and serial capture/logging continues through faults.
- Confirm helper/worker restart preserves exhaustion and a cleared fault only
  re-arms after the stable-high verification period.

Linux's [fault-injection guidance](https://kernel.org/doc/html/latest/i2c/gpio-fault-injection.html)
explains why clocking must stop when SDA releases: blindly completing more
clocks after an interrupted write can otherwise write unintended device data.
