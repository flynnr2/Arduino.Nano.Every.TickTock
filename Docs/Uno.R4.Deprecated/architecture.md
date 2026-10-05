# Architecture

> Deprecated Uno R4 reference. New receiver development targets
> [Raspberry.Pi](../../Raspberry.Pi/README.md).

## Data path overview

1. IR sensor events are captured by Nano Every.
2. Nano projects and compensates captures onto a shared free-running counter, then assembles swings.
3. Nano emits CFG, SCH, CSW, CPS and STS rows over UART.
4. UNO R4 receives protocol rows, appends environment metrics, and logs CSV.
5. UNO R4 serves operator views via HTTP and LED matrix status indicators.

## Roles

### Nano Every
- Time-critical edge capture and timestamping
- PPS discipline and correction estimation
- Swing assembly and serial tunables interface

### UNO R4 WiFi
- Validated capture-contract ingestion and four-file SD recording
- BMP280/SHT4x sensor sampling
- Wi-Fi provisioning + AP fallback
- HTTP configuration and file-download endpoints
- Capture-health status and a bounded OLED-only short/long EWMA rating estimator; broader statistics are computed off-device from CSV

## Firmware layout

```text
/Uno.R4.Deprecated
  ├─ src/
  │   ├─ NanoComm.cpp/.h
  │   ├─ HttpServer.cpp/.h
  │   ├─ Sensors.cpp/.h
  │   ├─ IngestOrchestrator.cpp/.h
  │   ├─ RecordSerializer.cpp/.h
  │   ├─ SDLogger.cpp/.h
  │   ├─ Display.cpp/.h
  │   ├─ StatusDisplay.cpp/.h
  │   └─ Config.h
  └─ Uno.R4.Deprecated.ino
```
