# Serial reference logger has moved

Use the single [Nano capture tool](../README.md):

```bash
python3 -m pip install pyserial
python3 tools/nano_capture.py --out ./my-recording
```

Run these commands from the repository root. `pendulum_wire_logger.py` has been
removed. The replacement retains all received bytes, requests metadata on
startup and writes analysis-compatible `PCPS.CSV` and `PCSW.CSV` files. It
continues past malformed data; strict protocol regression checks now live in
`tests/test_nano_capture.py` and `tests/test_capture_protocol.py`, with fixtures
in `tests/fixtures/nano_capture/`.

The former `--input` and `--self-test` command-line modes are not capture options.
See the linked guide for the supported command line and test commands.
