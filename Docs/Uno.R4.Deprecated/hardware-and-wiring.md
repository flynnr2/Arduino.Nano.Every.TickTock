# Hardware and Wiring

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

## UNO connections

- Nano UART output -> UNO `Serial1` RX, with a shared ground. Connect UNO `Serial1` TX to the Nano command input for metadata requests and tunables. The shared baud rate is 115200.
- External SPI SD module: chip select on digital pin 10 (`SD_CS_PIN`), plus the board SPI connections, suitable power and ground. The firmware expects a FAT32 card.
- BMP280 and SHT41 (SHT4x driver) on `Wire1`, the Qwiic bus (SDA pin 27, SCL pin 26). The BMP280 uses the library's default address through `bmp.begin()`; there is no firmware address override.
- External SSD1306 OLED on `Wire` (A4/SDA pin 18, A5/SCL pin 19): 128×64, I2C address `0x3D`, no separate reset pin (`OLED_RESET = -1`).
- The built-in 12×8 LED matrix provides a separate status display.

`setup()` starts Wire at 400 kHz and Wire1 at 100 kHz with 10 ms transaction timeouts. Recovery restores these settings. The buses are independent; do not assume the OLED and environmental sensors share SDA/SCL. Earlier source defaulted the sensors to `Wire`; sensor constructors/initialization now explicitly select `Wire1`. Use pull-ups and voltage levels appropriate for each bus (the Qwiic connector is 3.3 V). Recovery only pulls lines low or releases them to their external pull-ups; it does not power-cycle devices. Hardware SD card detection is disabled by default (`SD_CARD_DETECT_PIN = -1`).

## Nano connections

IR capture and GPS PPS wiring belong to the Nano firmware under `Nano.Every/`.
See the [Nano wiring guide](../Wiring.md) and verify its pin/timer configuration
before wiring.
