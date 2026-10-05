# Nano serial capture has moved

Use the single [Nano capture tool](../../tools/README.md):

```bash
python3 -m pip install pyserial
python3 tools/nano_capture.py --out ./my-recording
```

Run these commands from the repository root. `nano_serial_ingest.py` has been
removed. The replacement uses USB `Serial` by default, requests metadata on
startup and writes `PCPS.CSV` and `PCSW.CSV` directly into your chosen directory.
See the linked guide for port selection, passive capture and file handling.
