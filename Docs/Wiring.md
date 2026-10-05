# Wiring Information

## Conceptual Signal Flow

```text
GPS PPS (3.3 V) ──┬── AHCT125 gate 1 (5 V) → Nano A3 / D17 → EVSYS/TCB capture
                 └── Pi GPIO17 / physical pin 11 → Linux PPS → chrony
TCXO output → AHCT125 gate 2 (5 V) → Nano D2 / PA0 / EXTCLK
Photogate blue → AHCT125 gate 3 (5 V) → Nano D9 → EVSYS/TCB capture

Nano USB (Serial) ↔ Pi USB data port (OTG host)
GPS TX/RX ↔ Pi GPIO UART (3.3 V logic)
```

The selected Pi wiring uses the Nano's **USB connector**, superseding the old
D0/D1 Serial1 connection to the receiver. The deprecated Uno remains a reference.
See the [complete Pi wiring schedule](../Raspberry.Pi/WIRING.md) for power,
physical pins and Pi configuration.

The preferred build uses **one SN74AHCT125N powered at 5 V**, with independent
channels for PPS, TCXO and photogate. Its [combined pin schedule](../Raspberry.Pi/WIRING.md#preferred-quad-buffer-pin-schedule-pps-clock-and-photogate)
includes the three enables, unused fourth gate and decoupling. The Pi supply,
Nano, buffer and photogate detector use 5 V; the TCXO, OLED, sensors and Pi GPIO
signals use 3.3 V. GPS VIN may be 3.3 V or 5 V while TX/PPS remain 3.3 V.
USB handles Nano/Pi communications; GPS RX/TX connect directly to the Pi UART.
The AHCT125 restores levels and drives edges, but is not a Schmitt-trigger or
debounce filter.

## Hardware Components

| Generic component            | Specific choice here                                | Role                                       |
| ---------------------------- | --------------------------------------------------- | ------------------------------------------ |
| MCU / capture board          | Arduino Nano Every / ATmega4809                     | Timing capture node                        |
| GPS receiver                 | Adafruit PA1616D GPS breakout                       | PPS to both boards; serial data only to Pi |
| Pendulum sensor              | OPB912W55Z IR photologic sensor                     | Pendulum beam-break event input            |
| External oscillator          | ECS-TXO-5032-160-TR 16 MHz TCXO                     | Precision EXTCLK source                    |
| Clock buffer / level shifter | SN74AHCT125N (preferred); SN74AHCT1G125 alternative | Condition TCXO/GPSDO/OCXO clock into Nano  |

## Wiring

The default `USE_EXTCLK_MAIN=1` build requires a driven clock at D2/PA0 before
boot. Its frequency must match the build's `F_CPU` and `MAIN_CLOCK_HZ`; this
ECS-TXO-5032-160-TR requires both to be 16000000, not an assumed board default.
Use `USE_EXTCLK_MAIN=0` when building for the internal main clock.

The firmware defaults to USB `Serial` at 115200 baud for both data and commands;
no routing override is needed for the selected USB wiring. A laptop can use the
same Nano USB connection with the [minimal capture tool](../tools/README.md).
The Pi acquisition service must use the Nano USB device; `/dev/serial0` is now
reserved for gpsd. The application reports read-only chrony/gpsd health; OS
service provisioning and hardware acceptance remain commissioning work. See the
[timekeeping guide](../Raspberry.Pi/TIMEKEEPING.md).

| Signal                    | Nano connection               | Interface                                           |
| ------------------------- | ----------------------------- | --------------------------------------------------- |
| GPS PPS                   | A3 / D17 / PD0                | Non-inverting AHCT buffer powered at 5 V            |
| Photogate                 | D9 / PB0                      | Verified TTL-to-5 V-CMOS buffer                     |
| External clock            | D2 / PA0                      | Non-inverting AHCT buffer powered at 5 V            |
| Capture data and commands | USB connector, Arduino Serial | Pi USB data port through OTG adapter and data cable |
| Power                     | USB connector                 | Nominal 5 V; Nano remains a 5 V board               |
| Ground                    | GND                           | Shared with Pi, GPS and input buffers               |
| D0 / D1                   | No Pi connection              | Former Serial1 receiver wiring removed              |

## TCXO level conversion

**ECS-TXO-5032-160-TR output must also pass through a 5 V AHCT buffer before
Nano D2 / PA0 / EXTCLK.** Power the oscillator at 3.3 V; power the buffer at
Nano/capture 5 V. The oscillator's minimum specified HIGH is only 0.8 × VDD
(2.64 V at nominal 3.3 V), below the Nano's guaranteed 3.5 V HIGH at 5 V.

Use a separate **SN74AHCT1G125** for the clock, or an independent channel of
**SN74AHCT125N**. The complete [TCXO wiring schedule](../Raspberry.Pi/WIRING.md#tcxo-33-v-oscillator-through-a-5-v-clock-buffer)
lists oscillator pads, both buffer pinouts, bypass capacitors and startup/layout
checks. The preferred quad-buffer assignment is **gate 1 for GPS PPS, gate 2
for the 16 MHz clock, and gate 3 for photogate**. Never combine signals on one gate.

## GPS PPS level correction

The GPS breakout outputs a nominal **3.3 V PPS**, even when powered at 5 V.
The Nano's ATmega4809 guarantees HIGH at **0.7 × VDD**, or 3.5 V with a 5 V
supply. A direct connection may work on the installed device but lacks the
specified logic margin. Replace that direct wire with a **non-inverting 5 V
AHCT buffer**, preserving rising-edge polarity. USB power does not change the
Nano's GPIO domain to 3.3 V.

The [Pi schedule's PPS section](../Raspberry.Pi/WIRING.md#correct-the-pps-input-level)
gives separate pin schedules for **SN74AHCT1G125** (single gate) and
**SN74AHCT125N** (quad gate), both powered at 5 V, with decoupling and datasheet
references. SN74LVC1G17 is not the preferred substitute for this level conversion;
its input thresholds differ, and at 3.3 V it would not raise the PPS output level.
GPS TX/RX connect directly to the Pi's 3.3 V UART, separately from PPS capture.
Also branch the original GPS PPS output, **before the AHCT buffer**, directly
to Pi GPIO17 (physical pin 11). Never connect the buffer's 5 V output to the Pi.
The Pi uses gpsd and chrony for GPS/PPS UTC, with network NTP as fallback;
NMEA processing stays off the Nano. See the [shared PPS schedule](../Raspberry.Pi/WIRING.md#shared-pps-for-pi-utc-and-nano-capture).

## Confirmed photogate supply and interface

The installed photogate is **OPB912W55Z**, replacing the earlier OPB718Z label
in this guide. Its detector needs **4.5–16 V**; use the capture-side regulated
**5 V** rail. Its separate emitter requires a current-limiting resistor or
driver and must not be connected directly across 5 V. The existing resistors
are confirmed to be **in series with the red LED lead**, with a measured
resistance of **145.5 Ω** (reported 28 September 2026). This records the fitted
resistance, not a nominal replacement-part value. Retain them for emitter
current limiting. They do not condition the blue output. In the revised build,
blue goes to AHCT125 pin 9 (gate 3 input), and pin 8 goes to Nano D9.

The [manufacturer datasheet](https://www.ttelectronics.com/TTElectronics/media/ProductFiles/Datasheet/OPB900-913.pdf), pp. 1–3,
identifies an **inverted TTL totem-pole output**: illuminated is low, blocked
is high. Verified manufacturer lead assignments are:

| Lead / pin | Function            | Connection                                         |
| ---------- | ------------------- | -------------------------------------------------- |
| White / 3  | Detector VCC        | Regulated capture-side 5 V                         |
| Green / 5  | Detector ground     | Common ground                                      |
| Blue / 4   | Inverted TTL output | AHCT125 pin 9 input; pin 8 output to Nano D9 / PB0 |
| Red / 1    | LED anode           | Existing current-limited emitter supply            |
| Black / 2  | LED cathode         | Emitter circuit return, normally ground            |

Do not treat blue/output as open collector. Its published high-level output
limit does not guarantee the Nano's `0.7 × VDD` input threshold. Use a verified
TTL-input buffer, such as a non-inverting SN74AHCT125 powered at 5 V, unless the
existing circuit already provides suitable conditioning. The emitter has a
40 mA absolute maximum and a maximum switching threshold specified at 20 mA;
retain its verified resistor/current-driver design.

See the [Pi voltage audit and complete wiring schedule](../Raspberry.Pi/WIRING.md)
for voltage domains, input-buffer rationale, USB/GPS serial wiring and separate
shared sensor/OLED I²C bus. The photogate, PPS and external clock remain Nano inputs.
