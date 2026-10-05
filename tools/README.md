# Nano serial capture

`nano_capture.py` records the Nano Every serial stream for later analysis. It
needs Python 3.9 or later and `pyserial`; the analysis package is not required
for capture. The current firmware uses USB `Serial` at **115200 baud** for data
and commands. Connect the Nano using a USB data cable and close any other serial
monitor using the same port. The Nano still needs its configured clock and
capture inputs; see [wiring](../Docs/Wiring.md).

## Quick start

Run from the repository root:

```bash
python3 -m pip install pyserial
python3 tools/nano_capture.py --out ./my-recording
```

On Windows use `python` in place of `python3`. The tool automatically selects a
single confidently identified Nano Every using USB port metadata. If there are
no matches or multiple matches, it lists the ports and exits; select one with
`--port`. It does not open other ports or send detection probes.

```bash
python3 tools/nano_capture.py --list-ports
python3 tools/nano_capture.py --port /dev/cu.usbmodem1234 --out ./my-recording
```

Examples for other hosts (replace the port with the one listed on your machine):

```bash
# Linux
python3 tools/nano_capture.py --port /dev/ttyACM0 --out ./my-recording
# Windows
python tools/nano_capture.py --port COM5 --out ./my-recording
```

Stop with **Ctrl+C**. The tool reports its selected port, metadata readiness and
final capture/error counts. A serial disconnection ends the run; start another
recording after reconnecting.

## Options

| Option         | Behavior                                                                                               |
| -------------- | ------------------------------------------------------------------------------------------------------ |
| `--out DIR`    | Required for capture. Create DIR if needed and write the fixed output filenames below.                 |
| `--port PORT`  | Use this port instead of automatic Nano Every detection.                                               |
| `--list-ports` | List available serial ports and descriptions, then exit; no output directory is needed.                |
| `--baud N`     | Serial baud rate; default `115200`. Must match the firmware.                                           |
| `--passive`    | Send no commands. Wait indefinitely for valid metadata from the Nano before accepting structured data. |
| `--help`       | Show command-line help.                                                                                |

By default, capture sends `emit meta` after opening the port, retries at about
3 and 6 seconds if needed, and exits with an error if metadata is not ready
within 10 seconds. This command requests configuration and schema declarations;
data already streams continuously. It does not restart acquisition or change
firmware settings. Use `--passive` for a strictly listening connection. In that
mode metadata must arrive from firmware startup or a replay requested elsewhere.

## Output files

The requested directory receives these six files:

| File           | Contents                                                                              |
| -------------- | ------------------------------------------------------------------------------------- |
| `PCSW.CSV`     | The nine declared swing fields, in firmware order, with a header row.                 |
| `PCPS.CSV`     | The eight declared PPS fields, in firmware order, with a header row.                  |
| `STS.CSV`      | `line_number,status_code,family,payload,raw` for status records.                      |
| `CFG.CSV`      | `line_number,key,value` for valid configuration metadata.                             |
| `HDR.CSV`      | `line_number,tag,schema_id,fields,raw` for valid schema declarations.                 |
| `wire_raw.log` | Every byte received from the serial port, including malformed input and line endings. |

The raw log is written before parsing. It has no added timestamps or markers.
The structured data keeps original counter values and field order, with no
host timestamps or environmental columns. See the
[wire contract](../Docs/Protocol_Wire_Contract.md) for field definitions and
[clock and swing analysis](../Docs/Clock_Swing_Analysis.md) for interpretation.

Capture refuses to start if **any** of those target files already exists. Other
files in the directory are allowed. Choose another directory or remove earlier
outputs yourself. There is no append mode, automatic session naming, rotation,
retention policy or reconnect loop. Files are flushed after each received chunk
and closed on shutdown; arrange sufficient disk space and any backups you need.

## Validation and recovery

Structured `CSW` and `CPS` rows are written only after a supported `CFG` and
both complete `SCH` declarations have arrived. Earlier data remains in the raw
log and is counted as skipped; it is not backfilled into the CSV files. Partial
serial reads are buffered until a complete line arrives.

Malformed rows, unknown tags and invalid integer values are counted and retained
in the raw log, then skipped in structured output. Empty lines are ignored;
whitespace-only lines are malformed. Valid data received before readiness is
counted separately from malformed data. A trailing incomplete line at shutdown
remains in the raw log and counts as malformed.

A malformed contract record clears both configuration and schema readiness
until valid metadata is restored. A line longer than 4096 bytes also clears
readiness and is retained only in the raw log. After readiness is lost, active
capture starts another metadata request/retry cycle with the same 10-second
deadline; passive capture continues waiting. Identical metadata replays are
accepted. An unsupported contract or change to the protocol, capture schemas,
nominal frequency or firmware identity stops the recording with an error;
start a new recording after resolving the cause. Extra configuration keys are
preserved as metadata and may change. See the
[host parser state machine](../Docs/Host_Parser_State_Machine.md).

A reset with identical metadata is not an automatic file boundary: the original
counters remain in the same files. Check sequence gaps, resets and Nano drop
counters alongside the reported parse-error counts when assessing a recording.

## Analysis and tests

`PCPS.CSV` and `PCSW.CSV` are discovered directly by the analysis suite. Install
its separate dependencies and run it from the repository root:

```bash
python3 -m pip install -e .
python3 -m pendulum_analysis.suite ./my-recording
```

No environmental measurements are supplied by this tool. For sensors, OLED,
web access and managed continuous recording, use the
[Raspberry Pi receiver](../Raspberry.Pi/README.md).

The former serial reference logger and Nano ingest example have been replaced
by this single capture command. Strict protocol checks live in the regression
tests, including comparison with actual firmware serialization. Fixtures are in
`tests/fixtures/nano_capture/`. Run the focused tests with:

```bash
python3 -m pip install -e ".[test]"
python3 -m pytest tests/test_nano_capture.py tests/test_capture_protocol.py
```

## Replay the live swing mean and forecast

`swing_forecast_replay.py` uses the same Pi `DisplayEstimator` and optional
`SwingForecaster` as live acquisition. It has no serial I/O and needs only the
Python standard library. Its default cycle length is 0 (forecast disabled);
1 predicts the next full swing from the mean alone, and 2–120 learns an optional
repeating shape. A 15-swing setting is useful for the evaluated Synchronome;
the model assumes neither that mechanism nor a two-second swing.

```bash
python3 tools/swing_forecast_replay.py /path/to/segment/RAW.jsonl \
  --cycle-length 15 --output /path/to/new-result.json \
  --scores-csv /path/to/new-scores.csv
```

RAW is consumed in its recorded receipt order, including receipt timing for
staleness checks. A rotated segment without CFG needs an explicit
`--nominal-hz` fallback. Each replay starts fresh learning; it does not recover
prior segment state or acquisition connection/metadata-readiness transitions.
Fragments are counted and skipped; malformed records stop replay. Replay uses
one caller-specified configuration; it does not apply live configuration changes
recorded in `PI.CSV`. Evaluate a fixed-configuration interval rather than claiming
exact session reproduction after such changes.

For older recordings with only separate capture CSV files, explicitly select
hardware-event-order reconstruction and the declared timer frequency:

```bash
python3 tools/swing_forecast_replay.py Data/20260928_multiday_0 \
  --reconstruct --nominal-hz 16000000 --cycle-length 15 \
  --output /path/to/new-result.json
```

This merges PPS edges and completed swings in their shared timer domain. It
cannot establish original UART delivery order, batching or staleness. The first
stream edges must be less than half a uint32 wrap apart, and adjacent stream
gaps must be shorter than half a wrap. Sequence restarts are rejected; a whole
timer wrap during silence cannot be resolved from the separate files.

Results record ordering assumptions, source and implementation hashes, model
identity and configuration, full-record and post-learning prediction RMSE,
the same mean-only baseline error, and the final live-model snapshot. Optional
scores retain each prediction's origin and target sequence. The current target
is scored against its frozen earlier prediction before learning from that
observation. Error RMSE is a descriptive score, not a confidence interval.
Existing output files are never overwritten, and source captures are unchanged.
