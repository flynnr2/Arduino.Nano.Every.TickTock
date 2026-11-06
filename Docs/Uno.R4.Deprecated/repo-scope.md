# Uno Receiver Scope

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

## Included in this repository

- UNO R4 WiFi firmware (`Uno.R4.Deprecated/`)
- Nano Every capture firmware (`Nano.Every/`)
- Implemented Raspberry Pi receiver (`Raspberry.Pi/`; physical acceptance outstanding)
- Offline analysis, shared protocol documentation and component tests

## Source archive

The Uno receiver was imported from `Arduino.Pendulum.Timer.Display`. Earlier
history remains there; maintained system development now belongs in this
repository. See [import provenance](IMPORT.md).

## Integration model

The UNO R4 firmware consumes Nano protocol version 3 with `CFG`, `SCH`, `CSW`, `CPS` and `STS` records. See `protocol-and-csv.md` for the capture contract. The Nano and Uno copies of `PendulumProtocol.h` must remain byte for byte identical.
