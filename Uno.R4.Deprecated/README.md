# Uno R4 WiFi Receiver — Deprecated

This is the deprecated Arduino receiver for the [pendulum timing system](../README.md),
retained as a buildable reference. New receiver development targets the
[Raspberry Pi Zero 2 W](../Raspberry.Pi/README.md). The Pi application is implemented;
its physical hardware acceptance remains outstanding.
The Uno firmware consumes Nano Every serial records, reads BMP280/SHT4x environmental sensors,
records CSV files to SD, drives an SSD1306 OLED and provides a small HTTP interface.

The firmware was imported from `Arduino.Pendulum.Timer.Display`; see
[import provenance](../Docs/Uno.R4.Deprecated/IMPORT.md). The documents linked
below describe the retained Uno implementation, not the current Pi deployment.

## Build and Run

Install the Arduino UNO R4 Boards package (`arduino:renesas_uno`) and these libraries:

- `SD` (the source repository validated version 1.3.0)
- `Adafruit_BMP280`
- `Adafruit_SHT4x`
- `Adafruit_SSD1306`

Open `Uno.R4.Deprecated.ino` in the Arduino IDE and select **Arduino UNO R4 WiFi**, or run
this command from the repository root:

```bash
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi Uno.R4.Deprecated
```

The current Nano defaults both data and commands to USB `Serial`. To use this
retained Uno receiver, build the Nano with **both** `DATA_SERIAL=Serial1` and
`CMD_SERIAL=Serial1` applied consistently to every compilation unit; a local
sketch-only definition is insufficient. Use the normal Nano clock/build flags
from [the Nano build guide](../Docs/Development_and_Validation.md#nano-board-build), adding these routing overrides.
Connect Nano `Serial1` (D0/RX1 and D1/TX1) to the Uno at 115200 baud with RX/TX
crossed and a shared ground. The USB-default Nano build does not send capture
records to that D0/D1 connection.
Follow the [wiring guide](../Docs/Uno.R4.Deprecated/hardware-and-wiring.md) for the two I²C
buses and SD module. Insert a FAT32 SD card. Wi-Fi secrets are optional for
compilation; absent saved credentials select the access-point provisioning mode.
Use the `/wifi` page to configure credentials.

## Validation

Run from the repository root with Python 3 and a C++11 compiler:

```bash
python3 tests/uno_r4/run_host_tests.py
```

The host regressions exercise production helpers with hardware substitutes.
They do not replace board compilation or physical soak testing. Historical
board results and remaining hardware checks are in the
[validation checklist](../Docs/Uno.R4.Deprecated/test-checklist.md).

## Documentation

- [Architecture and ownership](../Docs/Uno.R4.Deprecated/architecture.md)
- [Repository scope](../Docs/Uno.R4.Deprecated/repo-scope.md)
- [Serial protocol and CSV](../Docs/Uno.R4.Deprecated/protocol-and-csv.md)
- [Hardware and wiring](../Docs/Uno.R4.Deprecated/hardware-and-wiring.md)
- [I²C recovery](../Docs/Uno.R4.Deprecated/i2c-recovery.md)
- [OLED display](../Docs/Uno.R4.Deprecated/oled-display.md)
- [HTTP interface](../Docs/Uno.R4.Deprecated/http-api.md)
- [Operations](../Docs/Uno.R4.Deprecated/operations.md)
- [Troubleshooting](../Docs/Uno.R4.Deprecated/troubleshooting.md)
- [Tunables](../Docs/Uno.R4.Deprecated/tunables-index.md)
- [LED matrix](../Docs/Uno.R4.Deprecated/uno-r4-led-matrix-status.md)
- [Engineering backlog](../Docs/Uno.R4.Deprecated/TODO.md)
