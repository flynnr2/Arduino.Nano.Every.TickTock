# Raspberry Pi hardware acceptance checks

These checks complement the automated replay and unit tests. The full acceptance
programme has **not** been completed. Record the Pi
model, OS release, software revision, Nano firmware/build settings,
OTG adapter/data cable, PPS buffer, breakout models, addresses, session directories, and
pass/fail evidence when carrying them out.

Use the [wiring schedule](WIRING.md) and [installation instructions](INSTALL.md).
The installer does not configure gpsd/chrony. The dashboard reports cached
chrony and gpsd diagnostics; use [TIMEKEEPING.md](TIMEKEEPING.md) and OS
diagnostics for physical timekeeping acceptance. The checks below are future
acceptance requirements, not claims of completed hardware validation.
Keep a backup of measurements before fault testing. Prefer stopped-service
rewiring or software-injected faults over disconnecting live bare wires.

## Recorded operating baseline: 2026-09-30

Read-only SSH and local HTTP inspection of `pendulum0` observed:

- Raspberry Pi Zero 2 W Rev 1.0; Raspbian GNU/Linux 13 (trixie), `armv7l`.
- Installed source record and clean checkout both at
  `ad288184a0bb94138d62418479e50c64a2ed9607`, tagged `pi-known-good-2026-09-30`.
- Acquisition, sensors, OLED, storage, web and I²C recovery services active;
  gpsd and chrony active; optional ThingSpeak timer active and waiting.
- HTTP acquisition status healthy, ready, connected and capture not stale.
- Chrony status synchronized to GPS/PPS; gpsd status fresh and available,
  with a 3D fix, 11 satellites used and 26 visible at the sampled instant.
- Installed dependency/source records, private configuration, publisher state
  and service definitions backed up off the Pi to the operator's Mac.

These observations establish a recoverable operating reference, not electrical
or metrology acceptance. No reboot, disconnect, power fault or retention-pressure
test was performed, and no physical checklist item below was marked complete.
The Nano's flashed binary and build settings were not independently identified.

## 1. Unpowered and initial electrical checks

- [ ] Header orientation and every physical pin match the schedule. GPS TX goes
      to Pi RX (pin 10), and GPS RX to Pi TX (pin 8).
- [ ] Nano USB connects to Pi USB data through an OTG host adapter and data
      cable. Nano D0/D1 have no Pi connection; no former UART translator remains.
- [ ] Original GPS PPS branches at 3.3 V directly to Pi GPIO17 (physical pin 11)
      before the AHCT gate. No 5 V buffer output reaches Pi GPIO. Check the
      rising edge at both receivers with both branches connected.
- [ ] GPS PPS reaches Nano A3/D17 through the non-inverting 5 V AHCT buffer.
      Check supply, enable, decoupling, unused inputs and the Nano-side waveform.
- [ ] TCXO pad 4 receives 3.3 V; pad 3 feeds a dedicated 5 V AHCT gate whose
      output reaches Nano D2/PA0. GPS PPS and clock use independent gates.
- [ ] Check the 16 MHz clock level/edges at D2 with a suitable low-capacitance
      probe. Clock is present before EXTCLK handoff; F_CPU and MAIN_CLOCK_HZ
      both equal 16000000. Verify local oscillator/buffer bypass capacitors.
- [ ] Preferred quad-buffer assignment is gate 1 PPS, gate 2 clock and gate 3
      photogate. Blue photogate output feeds IC pin 9; IC pin 8 feeds Nano D9.
      Retain the existing red-lead LED resistors; no direct blue-to-D9 bypass.
- [ ] IC pins 1/4/10 are grounded; unused gate 4 has pin 13 high, pin 12 low
      and pin 11 disconnected. Pin 14 is 5 V, pin 7 ground, with 100 nF bypass.
- [ ] Check photogate input and output transitions as well as static levels;
      AHCT125 has no Schmitt-trigger or debounce function.
- [ ] Nano IR and EXTCLK pin assignments still match its firmware build.
- [ ] Pi and Nano share ground; independent 5 V supply outputs are not connected.
- [ ] USB powers the Nano with no second independent 5 V feed. Verify the
      capture rail stays above 4.5 V under load and the TCXO supply is correct.
- [ ] Every Pi-facing logic signal and I²C pull-up is limited to 3.3 V. Verify
      idle UART and I²C voltages before connecting the Pi signal inputs.
- [ ] Each breakout's actual supply and signal specification has been checked;
      OLED and sensor SDA/SCL share bus 1 on GPIO2/GPIO3 (physical pins 3/5).
- [ ] Check power budget, secure connectors and strain relief. Keep the
      temperature/humidity sensor away from the Pi's own heat.

Also complete the [bus recovery and fixed-speed acceptance checks](I2C_RECOVERY.md#hardware-acceptance),
and verify the [OLED screens and changed-page transfers](OLED.md).

## 2. OS and peripheral discovery

With application services stopped:

```sh
cat /etc/os-release
readlink -f /dev/serial0
ls -l /dev/serial/by-id/
ls -l /dev/i2c-1
i2cdetect -l
sudo i2cdetect -y 1 0x44 0x44
sudo i2cdetect -y 1 0x76 0x77
sudo i2cdetect -y 1 0x3c 0x3d
```

- [ ] `/dev/serial0` resolves to the GPS UART; no login prompt or OS boot
      output is sent to GPS RX. GPS baud matches the module (default 9600).
- [ ] Nano firmware uses Serial for both data and commands at 115200; acquisition
      opens the observed Nano USB by-id path, never the GPS UART.
- [ ] Once gpsd is deployed, it alone owns the GPS UART and polls continuously;
      it does not probe the Nano USB port. Verify valid NMEA/fix data on the Pi
      while Nano capture continues, with no NMEA parsing moved onto the Nano.
- [ ] Bus 1 sees SHT4x, BMP280 and OLED at their configured addresses. Both
      `sensor_bus` and `oled_bus` are `1`. Neither application workers nor
      another diagnostic tool use the bus during these scans.
- [ ] The service account can open serial and I²C devices without running the
      application as root.
- [ ] Wi-Fi and SSH work with the GPIO UART/Bluetooth configuration applied.

### GPS/PPS and chrony acceptance

These checks require the separately commissioned OS services described in
[TIMEKEEPING.md](TIMEKEEPING.md). Use OS diagnostics for independent acceptance;
a healthy acquisition dashboard is not proof of valid Pi UTC.

- [ ] Identify `/dev/pps0` as the GPIO17 source. With `ppstest`, verify rising
      assert events advance once per second and reach the Pi at 3.3 V.
- [ ] Confirm gpsd has valid UTC and chrony pairs NMEA with the correct PPS
      second. Compare the date and offset with independent network time; rule
      out a one-second offset. Record the measured NMEA correction and config.
- [ ] In Time-reference health, compare the displayed fix type, optional fix
      status, satellites used/in view and report ages with gpsd's own diagnostics.
      Confirm `PI.CSV` periodic health rows record the same receiver readings.
      Disconnect the receiver and verify stale counts and fix claims disappear.
- [ ] With GPS valid, `chronyc sources -v` selects the GPS/PPS reference;
      `chronyc tracking` and `chronyc sourcestats -v` show credible offsets and
      uncertainty. Only chrony disciplines the system clock.
- [ ] Safely simulate GPS timing loss: confirm network NTP becomes the selected
      source once GPS is rejected. A PPS pulse alone must not imply valid UTC.
- [ ] Remove network access while GPS is valid: GPS/PPS keeps time and local
      recording continues. Restore access and verify network sources return.
- [ ] Make both external references unavailable: verify the Pi continues on
      its own clock, with no fresh external synchronisation. Record holdover
      duration and uncertainty; do not treat stale source details as GPS lock.
      Recording Nano counters continues even when UTC is unavailable.
- [ ] Restore timing and verify reacquisition without resetting the Nano or
      inventing events. Check Nano capture continuity and PPS/optical drop
      counters throughout. Physical GPS PPS removal can affect Nano PPS health;
      distinguish that expected effect from a software-induced capture fault.
- [ ] Verify startup UTC correction and normal gradual clock corrections do not
      replace Nano counters. Host receipt times remain receipt times. Check a
      Pi reboot creates a new session and an acknowledged, unfilled gap.
- [ ] Compare the implemented chrony panel's selected source, reference age,
      last offset and error estimate with OS diagnostics. Verify unavailable or
      stale checks are labelled, and `PI.CSV` health rows retain the observations.
      Receiver fix diagnostics are separate; independent GPS time validity and
      direct Pi PPS freshness are not implemented application checks. Verify
      those with the OS tools above rather than expecting dashboard proof.

## 3. Join the Nano stream and verify output

Start the application using the README instructions with real peripherals and
serial enabled. Keep the Nano running before starting the Pi receiver.

- [ ] The receiver requests `emit meta`, validates metadata, and becomes ready
      without a Nano reset or changes to timing settings.
- [ ] Raw serial evidence, Nano status records and Pi diagnostics are recorded.
      A startup/session record distinguishes this receiver run from earlier runs.
- [ ] PPS rows appear when PPS is present, and swing rows appear when the
      pendulum is moving. The dashboard shows fresh capture/serial state.
- [ ] Latest temperature, humidity and pressure are physically plausible. A
      missing or stale reading is visibly marked and is not silently reused as
      a fresh measurement.
- [ ] OLED shows useful measurement and health information and refreshes while
      the browser is connected. Confirm its orientation and legibility.
- [ ] Downloaded `PCSW.CSV` and `PCPS.CSV` have the exact header order in
      [the measurement CSV reference](DATA_FORMAT.md#measurement-csvs).
- [ ] Match a sample of recorded capture fields to their original Nano serial
      records. Pi receipt time must not replace Nano capture counters. Current
      nine-field PCSW records leave legacy `adj_diag` and `adj_comp_diag` empty;
      they must not acquire invented values.
- [ ] Environmental columns retain units °C, percent RH and hPa. `STS.CSV`
      retains `ts_ms,raw`; `PI.CSV` provides Pi diagnostics in place of `UNO.CSV`.
- [ ] Run the existing analysis against a sufficiently long completed segment
      containing both PPS and swing rows. Confirm loading, time reconstruction
      and results without manual CSV column changes.

## 4. Logging during monitoring and downloads

Use a recording long enough that downloading it takes several seconds.

- [ ] Browse live status and repeatedly download completed logs while recording.
      Start several browser connections, including one slow download.
- [ ] Download an active log: verify its documented snapshot behaviour and that
      complete rows form a valid CSV. Confirm the on-disk log keeps growing.
- [ ] Compare capture counts and sequence continuity against a quiet baseline.
      Downloads must not introduce unexplained acquisition gaps or hide drop
      counters; record the duration, sizes and observed counts.
- [ ] Make a supported configuration change and verify it is applied or reported
      as requiring restart. Invalid values produce an error without damaging
      the saved configuration or interrupting acquisition.
- [ ] Verify download paths cannot escape the configured log directory and that
      the interface exposes only the intended logs and configuration.

## 5. Failure and recovery

Use reversible tests with the service stopped when electrical changes are needed.
Some cases are safer using replay or a disposable test data directory than on
the only live recording.

- [ ] **Serial disconnected:** mark capture stale/disconnected; retain previous
      logs. On reconnect, request metadata and resume only with a valid contract.
- [ ] **Pi software restart:** verify USB power, Nano uptime and GPS/clock
      continuity; mark a new receiver session and resume after metadata.
      Accept missing measurements during downtime. Do not deliberately reset the
      Nano, fabricate rows, or join counters across the gap without evidence.
- [ ] **USB unplug/shared supply interruption:** expect the USB-powered Nano
      to lose power; verify a fresh capture epoch is handled on reconnect.
- [ ] **Nano restart:** detect its new epoch/metadata, separate incompatible
      capture continuity, and resume with a clear diagnostic trail.
- [ ] **One sensor unavailable:** make just that sensor unavailable using a
      configuration/test harness or power-off rewire; show missing/stale values
      while preserving serial acquisition and the other available readings.
- [ ] **OLED unavailable:** with the shared bus electrically healthy, serial
      logging and sensor acquisition continue; expose OLED failure and recovery.
- [ ] **Shared I²C bus fault:** serial logging continues, but both sensors and
      OLED may fail. Show stale/missing environmental values and peripheral
      errors; verify recovery after the bus fault is removed. Separate workers
      do not provide electrical bus or power-rail isolation.
- [ ] **Wi-Fi lost:** recording continues locally. Reconnecting the browser shows
      current status and the logs recorded during the outage.
- [ ] **Malformed input or metadata mismatch:** preserve raw evidence, mark the
      fault and refuse invalid capture rows; a later valid metadata replay can
      restore readiness.
- [ ] **Low/full storage:** use a disposable directory/filesystem or injected
      write failure. Show an explicit storage fault, preserve existing logs,
      and do not claim continued durable recording. Never fill the Pi root
      filesystem merely to test this behaviour.
- [ ] **Application crash/restart:** supervision restores the application and
      opens a clearly identified session. The prior log remains readable to
      its last complete row; report any recovery limitations.

## 6. Sustained operation and shutdown

- [ ] Run at least 24 hours, preferably 72, with real PPS/swings and periodic
      downloads. Record serial errors, Nano drop counters, receiver drops,
      memory use, free disk space, restarts and peripheral failures. Investigate
      unexplained growth or gaps rather than declaring success from uptime.
- [ ] Check storage growth against the configured free-space reserve and record
      the chosen flush policy. Measurements after the last durable flush can
      be lost on abrupt power failure; a normal reboot gap is accepted.
- [ ] Confirm log/session separation and metadata are sufficient for later
      analysis without relying on the web interface being available.
- [ ] Stop services cleanly, verify final logs, then run `sudo poweroff`. Restart
      and confirm automatic acquisition, peripheral updates and web service.

Hardware acceptance is complete only after the relevant boxes above have
recorded evidence. Automated tests establish software behaviour with simulated
inputs; they do not establish electrical correctness or unattended Pi reliability.
