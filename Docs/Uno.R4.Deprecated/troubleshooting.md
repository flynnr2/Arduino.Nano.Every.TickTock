# Troubleshooting

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

## Common checks

- Consult the external Nano firmware for supported diagnostic commands.
- UNO OLED/LED capture status and SD logs for capture health. Statistics are computed off-device; `/stats`, **/stats.json**, `/json` and `/log` return 404. Use `/uno` for logging settings and OLED EWMA half-lives.
- If sensor values are NaN/zero, verify I2C wiring and installed libraries.
- If no logs appear, verify SD card format (FAT32), media health, and insertion timing.

## Build and integration notes

- Keep buffer sizes as powers of two where applicable.
- If changing Nano pins/timers, update matching EVSYS/TCB configuration in Nano firmware.
- Keep shared protocol definitions synchronized between Nano firmware and `Uno.R4.Deprecated/src/PendulumProtocol.h`.
