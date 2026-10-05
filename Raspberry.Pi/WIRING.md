# Raspberry Pi Zero 2 W wiring schedule

This schedule replaces the deprecated Uno receiver. The Nano Every continues to
capture PPS and pendulum edges. The selected connection is now **USB serial at
115200 baud**, using the Nano's USB connector and Arduino **Serial** interface.
GPS PPS branches to the Nano through a 5 V buffer and directly to Pi GPIO17
(physical pin 11) at 3.3 V; GPS RX/TX go only to the Pi GPIO UART.
The Pi logs to its own microSD card; the old SPI SD-card module is not needed.

**Receiver configuration required:** the current Nano firmware defaults to
`DATA_SERIAL=Serial` and `CMD_SERIAL=Serial`, matching this USB wiring.
Point Pi acquisition at the Nano's USB device, not `/dev/serial0`.
Reserve `/dev/serial0` for gpsd. The application already reports read-only chrony
and gpsd health; installing and configuring those OS services and accepting the
physical system remain commissioning work. See [TIMEKEEPING.md](TIMEKEEPING.md).
The Pi acquisition default remains `/dev/serial0`, so its configuration must be
changed for this wiring; see [CONFIGURATION.md](CONFIGURATION.md).

**Physical pin numbers below are header positions, not GPIO numbers.** Identify
pin 1 before connecting anything, using the official [40-pin header diagram](https://www.raspberrypi.com/documentation/computers/images/GPIO-Pinout-Diagram-2.png).
An unsoldered Zero 2 W needs a suitable header fitted first. Wire with power off.

## Selected signal and supply arrangement

**Use one SN74AHCT125 powered at 5 V for all three Nano capture inputs:**
GPS PPS, the 16 MHz TCXO and the photogate. Each signal uses its own independent,
non-inverting channel. The exact preferred pin schedule below is for the
**SN74AHCT125N, 14-pin PDIP**; confirm the package suffix and top-view orientation.
Single-gate SN74AHCT1G125 wiring is retained later as an alternative, not as
additional stages to place in series with the quad buffer.

- **Nano ↔ Pi over USB:** the boards' USB interfaces handle communications
  without external RX/TX level converters. Nano firmware must use `Serial`.
- **GPS RX/TX ↔ Pi GPIO UART:** direct connection is suitable for the documented
  Adafruit GPS breakout. Cross GPS TX to Pi RX (pin 10), GPS RX to Pi TX (pin 8),
  and share ground.
- **GPS PPS → both boards:** branch the original 3.3 V output directly to
  **Pi GPIO17, physical pin 11**, and to quad-buffer gate 1 input (IC pin 2).
  Gate 1 output (IC pin 3) goes only to **Nano A3 / D17** at 5 V.
  Never connect that 5 V output to the Pi.
- **3.3 V TCXO → Nano D2 / PA0:** through quad-buffer gate 2 at 5 V.
- **Photogate blue output → Nano D9 / PB0:** through quad-buffer gate 3 at 5 V.
  Retain the existing resistors in the **red LED lead**; these limit emitter
  current and do not condition the blue output.

The supply domains are:

- **3.3 V:** TCXO, OLED and environmental sensors. Pi GPIO signals are also
  3.3 V; this does not mean the Pi's power input is 3.3 V.
- **5 V:** Nano, AHCT buffer, OPB912W55Z detector, and Pi power input. The Nano
  remains a 5 V board when powered through USB.
- **GPS breakout:** VIN accepts 3.3 V or 5 V; TX and PPS remain 3.3 V signals.
  The schedule uses clean capture-side 5 V to VIN. Leave its regulator-output
  pin unconnected; do not join independent 3.3 V regulator outputs.

### Preferred quad-buffer pin schedule: PPS, clock and photogate

**All pin numbers in this table belong to the SN74AHCT125N IC**, except where
explicitly labelled as Nano pins or TCXO pads. Identify pin 1 from the package
notch/dot and the [datasheet top view](https://www.ti.com/lit/ds/symlink/sn74ahct125.pdf), p. 3.

| Circuit            | Buffer input connection | Buffer output connection         | Enable / other connection                                   |
| ------------------ | ----------------------- | -------------------------------- | ----------------------------------------------------------- |
| GPS PPS, gate 1    | GPS PPS → pin 2 (1A)    | Pin 3 (1Y) → Nano A3 / D17 / PD0 | Pin 1 (/1OE) → GND                                          |
| TCXO clock, gate 2 | TCXO pad 3 → pin 5 (2A) | Pin 6 (2Y) → Nano D2 / PA0       | Pin 4 (/2OE) → GND                                          |
| Photogate, gate 3  | Blue lead → pin 9 (3A)  | Pin 8 (3Y) → Nano D9 / PB0       | Pin 10 (/3OE) → GND                                         |
| Unused gate 4      | Pin 12 (4A) → GND       | Pin 11 (4Y) unconnected          | Pin 13 (/4OE) → 5 V                                         |
| IC power           | —                       | —                                | Pin 14 (VCC) → Nano/capture 5 V; pin 7 → common GND         |
| IC bypass          | —                       | —                                | 100 nF ceramic capacitor between pins 14 and 7, close to IC |

Keep all three signal paths separate. Remove any direct source-to-Nano bypass
wires when inserting the buffer; never join buffer outputs. Ground is common
between the Nano, Pi, GPS, oscillator, detector and buffer. The 5 V buffer
outputs go only to the Nano, not Pi GPIO.

The AHCT125 restores suitable logic levels and provides driven output edges.
**It is not a Schmitt-trigger input or a debounce/noise filter.** Its inputs
still need valid levels and sufficiently fast transitions (specified maximum
20 ns/V input transition time in the [operating conditions](https://www.ti.com/lit/ds/symlink/sn74ahct125.pdf),
p. 5). Check the source and Nano-side waveforms, particularly on the 16 MHz
clock and photogate, rather than assuming the buffer removes all noise.
Use short wiring, nearby ground returns and the supply bypass capacitor.

## Voltage audit and rail schedule

**Use a mixed 5 V / 3.3 V system.** The Pi takes 5 V power but its GPIO is 3.3 V;
the Nano Every remains a 5 V capture board. The OLED and environmental breakouts
can run from the Pi's 3.3 V rail. Do not lower the Nano supply to 3.3 V to avoid
translation, or connect the Pi GPIO to 5 V merely because the Pi itself takes 5 V.

The manufacturer references identify the following devices. Match the markings on your
actual boards before applying the breakout-specific connections. Page numbers
below are PDF page numbers, counted from the first page.

| Component                                      | Documented supply                             | Rail to use here                   | Signal-level consequence                                 |
| ---------------------------------------------- | --------------------------------------------- | ---------------------------------- | -------------------------------------------------------- |
| Pi Zero 2 W                                    | 5 V power input; 3.3 V GPIO                   | 5 V to PWR IN                      | UART and shared I²C bus remain 3.3 V                     |
| Nano Every                                     | 5 V board                                     | USB cable from Pi                  | Main processor/GPIO remain nominally 5 V                 |
| Adafruit OLED product 938                      | 3 V or 5 V breakout, regulator/level shifting | Pi 3.3 V → VIN                     | Use 3.3 V I²C; 3Vo is an output                          |
| Adafruit BMP280 breakout                       | VIN 3–5 V, regulator/level shifting           | Pi 3.3 V → VIN                     | SDA = SDI; SCL = SCK; 3Vo is an output                   |
| Adafruit SHT41 product 5776, if fitted         | Breakout 3.3–5 V                              | Pi 3.3 V → VIN                     | 3.3 V I²C; exact installed breakout unconfirmed          |
| Adafruit Ultimate GPS breakout                 | VIN 3–5 V                                     | Clean capture-side 5 V → VIN       | PPS and TX remain 3.3 V outputs                          |
| OPB912W55Z (confirmed)                         | Detector VCC 4.5–16 V                         | Capture-side regulated 5 V         | TTL inverted totem-pole output; condition for Nano input |
| ECS-TXO-5032-160-TR, if fitted                 | 3.3 V ±5%                                     | Capture-side regulated 3.3 V       | HCMOS clock needs 3.3 V → 5 V conditioning               |
| SN74AHCT125 / SN74AHCT1G125 toward Nano inputs | VCC 4.5–5.5 V; VIH minimum 2 V                | Capture-side 5 V → VCC             | Accepts 3.3 V / TTL input, produces 5 V output           |
| AD5693R breakout, if retained separately       | VIN 3–5 V                                     | Depends on required analogue range | Not used by this Pi application; see note below          |

Sources and distinctions:

- [OLED product 938 page](https://www.adafruit.com/product/938):
  SSD1306, regulator and level shifting; the 2019 revision has auto-reset and
  defaults to I²C. Older boards may need interface jumpers and reset wiring.
- [BMP280 breakout guide](https://cdn-learn.adafruit.com/downloads/pdf/adafruit-bmp280-barometric-pressure-plus-temperature-sensor-breakout.pdf),
  pp. 6–7 and 19: connect Pi 3V3 to **VIN**, not the regulator output `3Vo`.
- [SHT41 breakout manufacturer page](https://www.adafruit.com/product/5776):
  the Adafruit breakout supports 3.3–5 V. The **bare SHT4x chip** has a
  1.08–3.6 V supply range ([Sensirion datasheet](https://cdn-shop.adafruit.com/product-files/5776/Datasheet_SHT4x.pdf),
  p. 9); breakout input ratings do not apply to a bare chip.
- [GPS breakout guide](https://cdn-learn.adafruit.com/downloads/pdf/adafruit-ultimate-gps.pdf), pp. 6 and
  11–13: the board accepts 3–5 V on VIN, but its PPS remains 3.3 V. Its `3.3V`
  pin is a regulator **output**. These are breakout ratings, not permission to
  power a bare PA1616D/other GPS module at 5 V.
- [IR sensor datasheet](https://www.ttelectronics.com/TTElectronics/media/ProductFiles/Datasheet/OPB900-913.pdf), pp. 1–3:
  OPB912 is the inverted totem-pole option, and its detector requires at least
  4.5 V. The emitter is a separate LED and needs its existing correctly sized
  series resistor/current driver; do not put 5 V directly across it. The fitted
  **OPB912W55Z** is confirmed; the older OPB718Z reference has been corrected in
  [the capture wiring guide](../Docs/Wiring.md).
- [ECS oscillator specification](https://ecsxtal.com/store/pdf/ECS-TXO-5032.pdf),
  p. 1: the documented TCXO uses 3.3 V. Keep its supply on the capture side so
  an ordinary Pi reboot does not interrupt the clock. With USB-powered capture,
  removing Pi power also removes capture power.
- [SN74AHCT125 datasheet](https://www.ti.com/lit/ds/symlink/sn74ahct125.pdf), pp. 3–6, and
  [SN74AHCT1G125 datasheet](https://www.ti.com/lit/ds/symlink/sn74ahct1g125.pdf), pp. 3–5: a suitable family for the 3.3 V → 5 V direction. Check the actual
  package pinout, output enable, unused inputs, decoupling and power-off behaviour;
  this is a directional buffer, not a bidirectional I²C translator.
- [Nano Every datasheet](https://docs.arduino.cc/resources/datasheets/ABX00028-datasheet.pdf),
  pp. 4–6, describes the board's 5 V domain and separate 3.3 V circuitry.
  [Pi power guidance](https://www.raspberrypi.com/documentation/computers/getting-started.html#power-supply)
  recommends a 5 V, 2.5 A supply for Zero models.
- [AD5693R breakout guide](https://cdn-learn.adafruit.com/downloads/pdf/adafruit-ad5693r-16-bit-dac-breakout-board.pdf),
  pp. 4–5 and 8: 3.3 V operation is possible but cannot deliver a 5 V analogue
  output range. If it remains powered at 5 V, do not connect its I²C pull-ups
  directly to the Pi. The Pi receiver does not currently control this DAC.

USB handles communication between the Nano and Pi without external UART level
converters. **GPS PPS, TCXO and TTL photogate inputs to the Nano still need
appropriate conditioning.** GPS UART signals to the Pi remain 3.3 V even when
the GPS breakout is powered at 5 V. No Nano D0/D1 connection to the Pi is used.

Keep the Pi's 3.3 V regulator output separate from the Nano/GPS regulator
outputs. Sharing ground is required; joining independent regulator outputs is
not. A common upstream 5 V supply is possible with a designed power distribution
and sufficient current capacity, but separate supplies must not be paralleled.

### Confirmed OPB912W55Z photogate

The resistors already fitted in the **red lead** are confirmed to limit
infrared LED current. Their measured resistance is **145.5 Ω**, reported
28 September 2026; this is the fitted resistance, not a nominal replacement-part
value. Retain that working emitter circuit. They are separate
from the detector's 5 V supply and do not raise the blue output's logic level.
In the revised wiring, blue connects through **quad-buffer gate 3** before D9.

The manufacturer lead schedule (local IR datasheet p. 2) is:

| Lead / manufacturer pin | Function                       | Connection                                                      |
| ----------------------- | ------------------------------ | --------------------------------------------------------------- |
| White / 3               | Detector VCC                   | Capture-side regulated 5 V                                      |
| Green / 5               | Detector GND                   | Capture-side common ground                                      |
| Blue / 4                | Inverted TTL totem-pole output | AHCT125 pin 9 (3A); pin 8 (3Y) → Nano D9 / PB0                  |
| Red / 1                 | Infrared LED anode             | Existing LED supply through confirmed red-lead series resistors |
| Black / 2               | Infrared LED cathode           | LED circuit return; normally common ground                      |

Unblocked light drives the output **low**; blocking the light drives it **high**.
This is a driven totem-pole output, not an open-collector output needing a
pull-up to create a high level. Do not add a 3.3 V pull-up as a substitute for
proper conditioning.

The detector supply is separate from the LED: the latter has a 40 mA absolute
maximum, a 1.0–1.7 V forward drop at 20 mA, and a maximum positive-going optical
switching threshold specified at 20 mA (p. 3). Retain a verified current-limited
emitter circuit; choose its resistor/current driver using actual rail tolerance,
LED drop, required switching current and power dissipation. Never connect the
red/black emitter pair directly across 5 V.

The photogate's high output is specified as low as `VCC − 2.1 V` under the stated
load, while the [ATmega4809 I/O specification](https://ww1.microchip.com/downloads/en/DeviceDoc/ATmega4808-09-DataSheet-DS40002173C.pdf)
(p. 470, table 32-17) requires `VIH ≥ 0.7 × VDD`: 3.5 V at a 5 V Nano supply.
Thus a direct connection is **not guaranteed by the published limits**, even if
it works on a particular lightly loaded unit. Retain or add a non-inverting
**5 V SN74AHCT125 TTL-input buffer** (or equivalent with verified input/output
limits) between blue/output and D9. Its 2 V high-input threshold is suitable for
the photogate's TTL output, and its output reaches the Nano's 5 V domain.
Confirm actual buffer package, enables and decoupling; do not assume the
SN74LVC1G17 is an interchangeable part for this direction.

## USB between Nano Every and Pi

Use the Pi Zero 2 W port labelled **USB**, not **PWR IN**, for the Nano:

```text
5 V supply → Pi PWR IN
Pi USB → micro-USB OTG host adapter → USB-A to micro-USB DATA cable → Nano USB
```

The OTG adapter selects host operation on the Pi; a charge-only cable will not
work. The Nano's onboard USB-to-serial bridge handles the electrical interface.
Leave Nano **D0/RX1 and D1/TX1 unconnected to the Pi**; remove the former GPIO
UART translator connections. Pi pins 8 and 10 are now for the GPS instead.
See the [Nano Every schematic](https://docs.arduino.cc/resources/schematics/ABX00028-schematics.pdf)
and [Arduino serial mapping](https://github.com/arduino/ArduinoCore-megaavr/blob/master/variants/nona4809/pins_arduino.h).

The USB cable also carries ground and nominal 5 V power to the Nano. The Nano
remains a **5 V board**, including its capture inputs. Its 3.3 V pin is a
regulator output, not a selector for 3.3 V GPIO operation. Our 16 MHz capture
clock requires at least 4.5 V at the ATmega4809; do not attempt to run this
assembly at 3.3 V ([processor speed grades, p. 5](https://ww1.microchip.com/downloads/en/DeviceDoc/ATmega4808-09-DataSheet-DS40002173C.pdf)).

### Nano acquisition device and firmware

- Keep **both** `DATA_SERIAL` and `CMD_SERIAL` set to `Serial`, consistently
  across all compilation units. These are the current USB defaults in
  [SerialParser.h](../Nano.Every/src/SerialParser.h).
- Keep the Nano protocol baud setting at **115200**. Connect the cable and
  identify its device with `ls -l /dev/serial/by-id/`; it will normally appear
  as `/dev/ttyACM0`, but that number can change. Prefer the observed by-id path.
- Set the Pi acquisition configuration's `serial_port` to that **Nano USB**
  path. Do not point acquisition at the GPS's `/dev/serial0`: GPS NMEA and
  Nano capture records are different protocols with different baud settings.
- Check that opening/reopening USB serial and rebooting the Pi do not reset
  the Nano unexpectedly. Avoid bootloader-touch operations during recording.
  Capture timestamps remain Nano-generated; USB arrival time is diagnostic only.

## GPS: serial to Pi, PPS to both boards

These connections apply to the documented **Adafruit Ultimate GPS breakout**.
The selected supply remains a clean capture-side **5 V to VIN**. A clean 3.3 V
supply to VIN is also supported, but changing supply is unnecessary: TX and PPS
are already 3.3 V signals, and RX accepts the Pi's 3.3 V TX.
The breakout's **3.3V pin is an output; leave it unconnected here**.
See [Adafruit's pin specifications](https://learn.adafruit.com/adafruit-ultimate-gps/pinouts).

| GPS pin | Destination                                                                                             |
| ------- | ------------------------------------------------------------------------------------------------------- |
| VIN     | Clean capture-side 5 V rail; 3.3 V to VIN is an allowed alternative                                     |
| GND     | Common ground: Nano GND, PPS buffer GND and Pi physical pin 6                                           |
| TX      | Pi physical pin 10, GPIO15 / RXD                                                                        |
| RX      | Pi physical pin 8, GPIO14 / TXD                                                                         |
| PPS     | Branch to Pi physical pin 11 / GPIO17 AND AHCT gate 1 input (IC pin 2); IC pin 3 output → Nano A3 / D17 |
| 3.3V    | No connection; onboard regulator output                                                                 |

GPS TX crosses to Pi RX, and Pi TX crosses to GPS RX. No external level
converter is needed on these two lines for this breakout. Do not join GPS TX
to Nano TX. Start the GPS reader at its configured baud rate, **9600 by default**;
that is independent of the Nano's 115200-baud USB stream. The gpsd service will be the sole GPS UART owner; application GPS data should
come from gpsd, not a second serial reader. Disable the serial login console
as described below.

### Shared PPS for Pi UTC and Nano capture

```text
GPS PPS (3.3 V) ──┬── Pi GPIO17 / physical pin 11 → Linux PPS → chrony
                 └── AHCT125 pin 2 → pin 3 (5 V) → Nano A3 / D17
GPS TX/RX ─────────── Pi UART → gpsd → UTC date/second for chrony
```

Take both branches from the GPS output **before** level conversion. Keep the
branches short and share ground; check the rising edge at both receiving pins
with both inputs connected. GPIO17 is dedicated to PPS and does not conflict
with the UART or the shared I²C bus in this schedule. No fourth AHCT gate is
needed for the Pi branch. Never feed Pi GPIO from the 5 V buffer output.

The Pi uses NMEA to identify the UTC second and its own kernel PPS timestamp
to locate the second boundary. Nano USB report arrival has variable delay and
is not the Pi's precision time reference. The Nano continues capturing PPS and
optical edges without parsing NMEA or accepting clock-setting commands.

The selected [chrony timekeeping plan](TIMEKEEPING.md) prefers valid GPS/PPS,
retains internet NTP for fallback and comparison, and marks loss of external
time as holdover/unsynchronised. Pi UTC discipline does not itself attach an
exact UTC timestamp to each Nano event. Reboot gaps remain accepted and are
never reconstructed. The read-only chrony/gpsd status collectors are implemented;
GPS/chrony OS setup and physical timekeeping validation remain commissioning work.

### Correct the PPS input level

The GPS PPS output is nominally **3.3 V**, whether VIN is 3.3 V or 5 V. The
ATmega4809 guarantees a HIGH only at **0.7 × VDD**, or **3.5 V at VDD = 5 V**
([table 32-17, p. 470](https://ww1.microchip.com/downloads/en/DeviceDoc/ATmega4808-09-DataSheet-DS40002173C.pdf)).
The existing direct connection can work, as observed, but does not meet the
guaranteed HIGH-level margin at nominal 5 V. This is a logic-reliability issue,
not an overvoltage issue or evidence that earlier measurements are invalid.

Replace the direct GPS PPS-to-Nano wire with **either SN74AHCT1G125 or
SN74AHCT125, powered from the Nano's 5 V rail**. Both are non-inverting, accept
HIGH from 2.0 V upwards, and output a 5 V-domain pulse. Their specified supply
range is 4.5–5.5 V; do not power them from 3.3 V for this purpose. Use **one**
buffer stage and preserve rising-edge polarity.

| Part          | PPS suitability                         | Practical choice                                                                      |
| ------------- | --------------------------------------- | ------------------------------------------------------------------------------------- |
| SN74AHCT1G125 | Yes, powered at 5 V                     | Single gate; small surface-mount package or suitable breakout                         |
| SN74AHCT125   | Yes, powered at 5 V                     | Four gates; N/PDIP version convenient for breadboard wiring                           |
| SN74LVC1G17   | Not the preferred 3.3 V-to-5 V solution | At 3.3 V its output stays 3.3 V; at 5 V its input lacks the AHCT 2.0 V HIGH guarantee |

The [single-gate datasheet](https://www.ti.com/lit/ds/symlink/sn74ahct1g125.pdf), pp. 3–5,
and [quad-gate datasheet](https://www.ti.com/lit/ds/symlink/sn74ahct125.pdf), pp. 3–6,
confirm the AHCT pinouts and levels. The
[LVC Schmitt-buffer datasheet](https://www.ti.com/lit/ds/symlink/sn74lvc1g17.pdf), pp. 5–6,
specifies supply-dependent switching thresholds, reaching a maximum rising
threshold of 3.33 V at 5.5 V supply. It may work in some 5 V arrangements, but
is not an interchangeable TTL-input substitute here. Powering it at 3.3 V
would leave the original Nano input-level issue unresolved.

#### Option A: SN74AHCT1G125, single gate

The following IC pin numbers apply to the **5-pin DBV, DCK and DRL packages**
in the local datasheet. Confirm package orientation; on a breakout, use its
labelled signals rather than assuming header positions equal IC pin numbers.

| Buffer pin | Function               | Connection                               |
| ---------- | ---------------------- | ---------------------------------------- |
| 5          | VCC                    | Nano/capture 5 V rail                    |
| 3          | GND                    | Common ground                            |
| 1          | /OE, active-low enable | GND, enabling the buffer                 |
| 2          | A input                | GPS PPS                                  |
| 4          | Y output               | Nano A3 / D17 / PD0                      |
| 5 to 3     | Supply bypass          | 100 nF ceramic capacitor close to the IC |

#### Option B: SN74AHCT125N, one gate of four

This example uses gate 1 of an **SN74AHCT125N, 14-pin PDIP, viewed from the
top**; identify the notch/pin 1. This is the preferred assignment: gate 1 for
PPS, gate 2 for clock, gate 3 for photogate, as in the combined schedule above. **Its pin numbering differs from the single-gate
part above.**

| Buffer pin | Function                | Connection                               |
| ---------- | ----------------------- | ---------------------------------------- |
| 14         | VCC                     | Nano/capture 5 V rail                    |
| 7          | GND                     | Common ground                            |
| 1          | /1OE, active-low enable | GND, enabling gate 1                     |
| 2          | 1A input                | GPS PPS                                  |
| 3          | 1Y output               | Nano A3 / D17 / PD0                      |
| 14 to 7    | Supply bypass           | 100 nF ceramic capacitor close to the IC |

For unused gates only, hold their enable inputs high, tie their data inputs to
ground, and leave outputs disconnected. Gates used for other signals retain
their own connections.

For either option, keep the buffer and Nano on the same supply; its 5 V
output must never connect to Pi GPIO. Keep PPS wiring short and verify its
waveform at the Nano during bring-up. The buffer adds propagation delay; retain
it consistently in the measurement path and account for it if absolute PPS
phase becomes a measurement requirement.

The Nano's IR input remains **D9 / PB0**, and its external clock remains
**D2 / PA0**. GPS serial monitoring on the Pi does not replace Nano hardware
PPS capture, and serial sentence arrival is not a precision PPS timestamp.

## TCXO: 3.3 V oscillator through a 5 V clock buffer

**The ECS-TXO-5032-160-TR also requires level conversion for guaranteed
operation with the 5 V Nano. Do not connect its output directly to D2/PA0.**
Keep the oscillator itself at **3.3 V** and use a **non-inverting
SN74AHCT1G125 or a separate SN74AHCT125 channel powered at 5 V**.

The [ECS specification](https://ecsxtal.com/store/pdf/ECS-TXO-5032.pdf), pp. 1–2,
identifies `160` as 16.000 MHz and specifies a 3.135–3.465 V supply. Its minimum
HIGH output is 0.8 × VDD: 2.64 V at nominal 3.3 V, or about 2.51 V at the lowest
specified supply. This meets the AHCT's 2.0 V HIGH requirement but does not
meet the Nano's 3.5 V guaranteed HIGH at a 5 V supply. The AHCT buffer is fast
enough for 16 MHz with appropriate loading and wiring; see the
[single-gate switching specifications](https://www.ti.com/lit/ds/symlink/sn74ahct1g125.pdf), p. 6.

```text
Capture-side 3.3 V → TCXO VDD
TCXO output → AHCT input A → AHCT output Y → Nano D2 / PA0 / EXTCLK
Capture-side 5 V → AHCT VCC
Common ground → TCXO GND, AHCT GND, AHCT /OE and Nano GND
```

### Oscillator connections

These are **oscillator package pad numbers**, not Pi header positions. Identify
pad 1 and the viewing orientation using the ECS package drawing; do not mirror
the bottom view when wiring a breakout. This is the **TXO** part, not the
voltage-controlled **VTXO** variant.

| TCXO pad | Function            | Connection                                       |
| -------- | ------------------- | ------------------------------------------------ |
| 1        | N/C on ECS-TXO      | Leave unconnected                                |
| 2        | GND                 | Common capture ground                            |
| 3        | Clock output        | Dedicated AHCT clock-channel input A             |
| 4        | VDD                 | Regulated capture-side 3.3 V; never 5 V          |
| 4 to 2   | Local supply bypass | 100 nF ceramic capacitor close to the oscillator |

### Option A: a separate SN74AHCT1G125 for the clock

Use a **second IC** if PPS already uses an SN74AHCT1G125. For its 5-pin
DBV/DCK/DRL packages, wire the following; use signal labels if a breakout has
different header numbering.

| Clock buffer pin | Function      | Connection                                   |
| ---------------- | ------------- | -------------------------------------------- |
| 5                | VCC           | Nano/capture 5 V rail                        |
| 3                | GND           | Common capture ground                        |
| 1                | /OE           | GND, permanently enabling the clock buffer   |
| 2                | A input       | TCXO pad 3, clock output                     |
| 4                | Y output      | Nano D2 / PA0 / EXTCLK                       |
| 5 to 3           | Supply bypass | 100 nF ceramic capacitor close to the buffer |

### Preferred option: one SN74AHCT125N shared by all three inputs

Use the [combined quad-buffer schedule](#preferred-quad-buffer-pin-schedule-pps-clock-and-photogate)
above: **gate 1 for PPS, gate 2 for TCXO, gate 3 for photogate**. For the clock,
TCXO pad 3 goes to buffer pin 5 (2A), buffer pin 6 (2Y) goes to Nano D2/PA0,
and buffer pin 4 (/2OE) goes to ground. Pins 14 and 7 supply the IC at 5 V
with the local 100 nF bypass capacitor. Gate 4 is disabled and terminated as
shown in that schedule. Each signal gets a separate channel; no inputs or
outputs are combined.

### Clock layout and startup checks

Keep the oscillator-to-buffer and buffer-to-Nano connections short, with nearby
ground returns. The TCXO is specified for a **15 pF load**; allow for buffer
input, wiring and probe capacitance rather than attaching a long cable or a
heavy probe directly to its output. Check clock amplitude and edges at Nano D2
with a suitable low-capacitance probe during bring-up.

Power the clock buffer from the same rail as the Nano and derive the TCXO's
3.3 V from the capture-side supply. The clock must be running when firmware
switches to EXTCLK; do not gate it with Pi GPIO or wait for Pi software to enable
it. For this oscillator, build with **F_CPU = MAIN_CLOCK_HZ = 16000000** and
**USE_EXTCLK_MAIN=1**. The buffer changes signal voltage, not clock frequency.
A Pi software reboot should leave the capture power/clock intact; removal of
the shared USB power stops this USB-powered capture assembly.

## Environmental sensors and OLED: shared hardware I²C bus 1

All three peripherals share **GPIO2/SDA (physical pin 3)** and **GPIO3/SCL
(physical pin 5)** on `/dev/i2c-1`. Set both `sensor_bus` and `oled_bus` to `1`;
these are the software defaults. Their addresses are distinct: BMP280 at
`0x77`, SHT4x/SHT41 at `0x44`, and SSD1306 OLED at `0x3D`. BMP280 `0x76` and
OLED `0x3C` are configurable.

The documented Adafruit BMP280 uses **VIN = 3.3 V**, **SDI = SDA** and
**SCK = SCL**; leave SDO and CS disconnected for its default I²C arrangement.
The SHT41 connection assumes the Adafruit breakout described above; confirm
its markings. Keep the sensors away from Pi heat and airflow disturbances.

The OLED is a **128 × 64 SSD1306**. The local product 938 sheet confirms 3.3 V
operation. On its header, **Data is SDA**, **Clk is SCL**, and **VIN is the
power input**; leave `3Vo` unconnected. Confirm your revision is configured
for I²C and has auto-reset.

| Pi physical pin | GPIO / rail | Connect to                                      |
| --------------- | ----------- | ----------------------------------------------- |
| 3               | GPIO2 / SDA | BMP280 SDI / SDA, SHT4x SDA and OLED Data / SDA |
| 5               | GPIO3 / SCL | BMP280 SCK / SCL, SHT4x SCL and OLED Clk / SCL  |
| 1               | 3.3 V       | Both sensor breakout VIN pins                   |
| 9               | GND         | Both sensor breakout grounds                    |
| 17              | 3.3 V       | OLED VIN (not 3Vo)                              |
| 20              | GND         | OLED GND                                        |

Use distribution terminals for the shared SDA, SCL and sensor supply
connections. GPIO2 and GPIO3 already have fixed pull-ups to **3.3 V**;
account for the parallel pull-ups on all three breakouts before adding more.
Follow each breakout's instructions for I²C mode and address selection.
GPIO23 and GPIO24 are no longer used for the OLED.

The sensor and OLED workers remain separate processes with independent retries,
but an electrically stuck device on this shared bus can stop both sensor
readings and display updates. Serial capture and logging remain independent of
I²C. General electrical guidance is in the
[official GPIO documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio-and-the-40-pin-header).

Use one fixed **400 kHz** clock for the shared bus; never switch speeds between
OLED and sensor transactions. All three devices support fast-mode I²C. If
commissioning shows errors, select **100 kHz for the entire bus** and reboot.
See [bus recovery and speed](I2C_RECOVERY.md) for device specifications,
recovery limits and hardware acceptance.

## Raspberry Pi OS configuration

Install Raspberry Pi OS Lite and configure Wi-Fi, SSH and a user with Raspberry
Pi Imager. See the application [README](README.md) for installation and service
configuration. The following settings select the pins above on a Zero 2 W.

Edit `/boot/firmware/config.txt` and add or reconcile this block, avoiding
contradictory existing settings:

```ini
[all]
enable_uart=1
dtoverlay=disable-bt
dtoverlay=pps-gpio,gpiopin=17
dtparam=i2c_arm=on
dtparam=i2c_arm_baudrate=400000
```

The GPIO UART arrangement gives the GPS the header PL011 UART by disabling
Bluetooth. Wi-Fi remains available. The PPS overlay timestamps rising edges
on GPIO17; do not enable its `assert_falling_edge` option for this wiring.
Remove any old OLED `dtoverlay=i2c-gpio,bus=3,...` entry when moving its
wiring to bus 1; no software I²C overlay is needed. The overlay parameters
are documented in the [official overlay reference](https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README).

Remove the serial console parameter, such as `console=serial0,115200` or
`console=ttyAMA0,115200`, from `/boot/firmware/cmdline.txt`. Keep that file on
**one line**, retain unrelated parameters, and retain `console=tty1` if present.
In `sudo raspi-config`, the Serial Port answers should be **No** for a login
shell and **Yes** for enabled serial hardware. Disable the Bluetooth UART
service and any enabled serial login service for this port:

```sh
sudo systemctl disable --now hciuart.service
sudo systemctl disable --now serial-getty@serial0.service serial-getty@ttyAMA0.service
sudo reboot
```

Install and configure gpsd/chrony separately using the [timekeeping guide](TIMEKEEPING.md).

A message that a service does not exist can be normal for an OS image that did
not install it. After reboot, verify the device paths:

```sh
readlink -f /dev/serial0
ls -l /dev/serial/by-id/
ls -l /dev/i2c-1 /dev/pps0
i2cdetect -l
```

Expect `/dev/serial0` to resolve to the PL011 device, normally `/dev/ttyAMA0`,
for the GPS, a separate Nano USB serial device to appear, and `/dev/i2c-1`
to exist for all three peripherals. Confirm `/dev/pps0` is the GPIO17 source, not another
PPS device. The application account needs serial and I²C access (normally membership of `dialout` and `i2c`). No kernel sensor or
OLED overlay is required: the application owns these devices through I²C.

Only during bring-up, with peripheral workers stopped, probe the expected
addresses; do not scan a live acquisition system routinely:

```sh
sudo i2cdetect -y 1 0x44 0x44
sudo i2cdetect -y 1 0x76 0x77
sudo i2cdetect -y 1 0x3c 0x3d
```

Record the responding addresses and set application configuration accordingly.
An address response confirms neither the exact chip model nor measurement
accuracy; verify the application readings as well.

## Power and reboots

Power the Pi through **PWR IN**. The discussed [official 5.1 V / 2.5 A micro-USB
supply](https://www.raspberrypi.com/products/micro-usb-power-supply/) is suitable for the planned load; verify rail voltage under load during
bring-up. The Nano receives power through the Pi's USB data connection. Do not
add a second independent 5 V feed to the Nano's 5 V pin alongside USB power.
Any separately powered arrangement requires a reviewed power/USB isolation
scheme; it is not the baseline wiring above.

The Nano's capture-side rail supplies the PPS buffer and the existing modest
capture loads, with appropriate regulation for the TCXO, LED current limiting
for the photogate, and clean power for GPS VIN. Check voltage at the loads:
USB cable and board losses reduce the nominal supply, while the Nano at 16 MHz,
AHCT buffer and OPB912 detector require at least 4.5 V. Keep independent 3.3 V
regulator outputs separate. OLED and environmental sensors use the Pi rail.

Use `sudo poweroff` before removing Pi power. An ordinary software reboot is
expected to leave USB power present, but confirm Nano/GPS/clock continuity and
USB-bridge behaviour on the assembled system. Disconnecting the Nano USB cable
or removing the shared supply stops a USB-powered Nano. Neither a Pi reboot nor
a USB power interruption has a durable measurement buffer: gaps are accepted,
recorded where observable, and never reconstructed. On rejoin, acquisition
requests `emit meta` from the **Nano USB port**, validates the contract and
opens a new session without deliberately resetting the Nano.

Complete the [hardware acceptance checks](HARDWARE_TESTS.md) before unattended use.
