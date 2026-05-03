# Nano Every Host Serial Ingest Example

Install dependency:

```bash
python3 -m pip install pyserial
```

Run on macOS:

```bash
python3 tools/nano_serial_ingest.py --port /dev/cu.usbmodemXXXX --baud 115200 --out ./nano_logs
```

Run on Raspberry Pi/Linux:

```bash
python3 tools/nano_serial_ingest.py --port /dev/ttyACM0 --baud 115200 --out ./nano_logs
```

Outputs are written to one session directory per run and split by record class (`raw_serial.csvl`, `derived_smp.csv`, `canonical_swing.csv`, `canonical_pps.csv`, `status_sts.csvl`, `cfg.csvl`, `schema.csvl`, `malformed.csvl`, `ingest_events.csvl`, `session_manifest.json`).

`raw_serial.csvl` is the forensic source of truth and is written before parsing/routing each record. Canonical mode emits `CSW`/`CPS` rows; derived mode emits `SMP` rows. The ingestor supports both.
