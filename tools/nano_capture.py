#!/usr/bin/env python3
"""Capture Nano Every serial bytes and analysis-ready CSVs. See tools/README.md."""
from __future__ import annotations

import argparse
import csv
from contextlib import ExitStack
from pathlib import Path
import re
import sys
import time

PROTOCOL_VERSION = "3"
CANONICAL_SWING_SCHEMA_ID = "canonical_swing_v2"
CANONICAL_PPS_SCHEMA_ID = "canonical_pps_v1"
CSW_FIELDS = "seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing".split(",")
CPS_FIELDS = "seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps".split(",")
SCHEMAS = {"CSW": (CANONICAL_SWING_SCHEMA_ID, CSW_FIELDS),
           "CPS": (CANONICAL_PPS_SCHEMA_ID, CPS_FIELDS)}
REQUIRED_CFG = {"pv": PROTOCOL_VERSION, "cst": "CSW", "css": CANONICAL_SWING_SCHEMA_ID,
                "cpt": "CPS", "cps": CANONICAL_PPS_SCHEMA_ID}
HEADERS = {"PCSW.CSV": CSW_FIELDS, "PCPS.CSV": CPS_FIELDS,
           "STS.CSV": ["line_number", "status_code", "family", "payload", "raw"],
           "CFG.CSV": ["line_number", "key", "value"],
           "HDR.CSV": ["line_number", "tag", "schema_id", "fields", "raw"]}
OUTPUT_NAMES = (*HEADERS, "wire_raw.log")
MAX_LINE_BYTES = 4096


class CaptureError(ValueError):
    """A recording cannot safely continue."""


class Malformed(ValueError):
    """A line must be retained as raw input but excluded from structured output."""


def check_destination(out_dir):
    out_dir = Path(out_dir)
    existing = [name for name in OUTPUT_NAMES if (out_dir / name).exists() or (out_dir / name).is_symlink()]
    if existing:
        raise CaptureError("Output files already exist; choose another directory: " + ", ".join(existing))


class Capture:
    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)
        self.cfg = None
        self.configured = False
        self.schemas = set()
        self.counts = {tag: 0 for tag in ("CSW", "CPS", "STS", "CFG", "SCH")}
        self.malformed = self.before_ready = self.lines = 0
        self.pending = bytearray()
        self.discarding = False
        self.stack = ExitStack()
        self.files = []
        self.writers = {}

    @property
    def ready(self):
        return self.configured and self.schemas == {"CSW", "CPS"}

    def __enter__(self):
        check_destination(self.out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Exclusive creation also protects against another process starting a capture.
            self.raw = self.stack.enter_context((self.out_dir / "wire_raw.log").open("xb"))
            self.files.append(self.raw)
            for name, header in HEADERS.items():
                fp = self.stack.enter_context((self.out_dir / name).open("x", newline="", encoding="utf-8"))
                self.files.append(fp)
                self.writers[name] = csv.writer(fp, lineterminator="\n")
                self.writers[name].writerow(header)
            self.flush()
        except BaseException:
            self.stack.close()
            raise
        return self

    def __exit__(self, *_):
        try:
            self.finish()
        finally:
            self.stack.close()

    def flush(self):
        for fp in self.files:
            fp.flush()

    def finish(self):
        if self.pending and not self.discarding:
            self.lines += 1
            self.malformed += 1
        self.pending.clear()
        self.flush()

    def feed(self, data: bytes):
        # Retain the exact bytes, including invalid UTF-8 and partial/rejected lines.
        self.raw.write(data)
        self.raw.flush()
        self.pending.extend(data)
        while b"\n" in self.pending:
            line, _, tail = self.pending.partition(b"\n")
            self.pending = bytearray(tail)
            if self.discarding:
                self.discarding = False
                continue
            self.lines += 1
            if len(line) > MAX_LINE_BYTES:
                self.malformed += 1
                self.configured = False
                self.schemas.clear()
                continue
            try:
                self._line(bytes(line).removesuffix(b"\r"))
            except Malformed:
                self.malformed += 1
        if len(self.pending) > MAX_LINE_BYTES:
            if not self.discarding:
                self.lines += 1
                self.malformed += 1
                self.configured = False
                self.schemas.clear()
            self.pending.clear()
            self.discarding = True
        self.flush()

    def _line(self, data):
        tag_hint = data.split(b",", 1)[0]
        try:
            raw = data.decode("utf-8")
            if not raw:
                return
            cols = next(csv.reader([raw], strict=True))
            tag, values = cols[0], cols[1:]
            if tag == "CFG":
                self._cfg(values)
            elif tag == "SCH":
                self._schema(values, raw)
            elif tag in SCHEMAS:
                self._sample(tag, values)
                return
            elif tag == "STS":
                if not values or values[0] not in {"OK", "UNKNOWN_COMMAND", "INVALID_PARAM", "INVALID_VALUE", "INTERNAL_ERROR", "PROGRESS_UPDATE"}:
                    raise Malformed("Invalid status")
                self.writers["STS.CSV"].writerow([self.lines, values[0], values[1] if len(values) > 1 else "", ",".join(values[2:]), raw])
            else:
                raise Malformed("Unknown record tag")
            self.counts[tag] += 1
        except (UnicodeError, csv.Error, StopIteration, IndexError, Malformed) as exc:
            if tag_hint in (b"CFG", b"SCH"):
                self.configured = False
                self.schemas.clear()
            raise Malformed(str(exc)) from exc

    def _cfg(self, values):
        cfg = {}
        for token in values:
            key, sep, value = token.partition("=")
            if not sep or not key or key in cfg or not value:
                raise Malformed("Malformed CFG token")
            cfg[key] = value
        if not (REQUIRED_CFG.keys() | {"nhz", "fw"}) <= cfg.keys():
            raise Malformed("Incomplete CFG")
        if not re.fullmatch(r"[0-9]+", cfg["nhz"]) or not 0 < int(cfg["nhz"]) < 2**32:
            raise Malformed("Invalid nominal frequency")
        if any(cfg[k] != v for k, v in REQUIRED_CFG.items()):
            raise CaptureError(f"Unsupported CFG contract at line {self.lines}")
        if self.cfg is not None and any(cfg[k] != self.cfg[k] for k in (*REQUIRED_CFG, "nhz", "fw")):
            raise CaptureError(f"CFG contract changed at line {self.lines}; start a new recording")
        self.cfg = cfg
        self.configured = True
        for key, value in cfg.items():
            self.writers["CFG.CSV"].writerow([self.lines, key, value])

    def _schema(self, values, raw):
        if len(values) < 3:
            raise Malformed("Incomplete SCH")
        tag, schema_id, fields = values[0], values[1], values[2:]
        if tag not in SCHEMAS or (schema_id, fields) != SCHEMAS[tag]:
            raise CaptureError(f"Unsupported or changed SCH contract at line {self.lines}")
        self.schemas.add(tag)
        self.writers["HDR.CSV"].writerow([self.lines, tag, schema_id, ",".join(fields), raw])

    def _sample(self, tag, values):
        fields = SCHEMAS[tag][1]
        if len(values) != len(fields):
            raise Malformed("Incorrect field count")
        for field, value in zip(fields, values):
            limit = 4 if field == "gps_status" else 2**16 if field in {"cap16", "latency16"} else 2**32
            if not re.fullmatch(r"[0-9]+", value) or len(value) > 10 or int(value) >= limit:
                raise Malformed("Invalid unsigned field")
        if not self.ready:
            self.before_ready += 1
            return
        self.writers["PCSW.CSV" if tag == "CSW" else "PCPS.CSV"].writerow(values)
        self.counts[tag] += 1


def port_listing(ports):
    return "\n".join(f"  {p.device}: {p.description or 'Unknown device'} ({getattr(p, 'hwid', '')})" for p in ports) or "  No serial ports found."


def select_port(ports, requested=None):
    if requested:
        return requested
    candidates = []
    for port in ports:
        description = " ".join(str(getattr(port, field, "") or "") for field in ("description", "product"))
        if re.search(r"nano[\s_-]+every\b", description, re.IGNORECASE):
            candidates.append(port.device)
    if len(candidates) == 1:
        return candidates[0]
    raise CaptureError("Could not identify exactly one Nano Every. Specify --port from:\n" + port_listing(ports))


def run_capture(ser, capture, passive=False, clock=time.monotonic, report=print):
    deadline_start = clock()
    requests = 0
    announced = False
    while True:
        now = clock()
        if not capture.ready:
            if announced:
                report("Metadata invalidated; waiting for a valid capture contract.")
                announced = False
                deadline_start, requests = now, 0
            if not passive:
                if now - deadline_start >= 10:
                    raise CaptureError("No valid CFG and CSW/CPS schemas within 10 seconds; check port, baud and firmware. Raw input was retained.")
                if requests < 3 and now - deadline_start >= requests * 3:
                    command = b"emit meta\n"
                    if ser.write(command) != len(command):
                        raise CaptureError("Incomplete metadata request write")
                    requests += 1
        elif not announced:
            report("Capture ready; writing CSW/CPS records. Ctrl+C to stop.")
            announced = True
        data = ser.read(4096)
        if data:
            capture.feed(data)


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="Output directory (required for capture; target files must not exist)")
    parser.add_argument("--port", help="Serial device; otherwise identify one Nano Every from port descriptions")
    parser.add_argument("--baud", type=positive_int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--passive", action="store_true", help="Send no commands; wait indefinitely for metadata")
    parser.add_argument("--list-ports", action="store_true", help="List serial devices and exit")
    args = parser.parse_args(argv)
    if not args.list_ports and args.out is None:
        parser.error("--out is required for capture")
    capture = None
    try:
        import serial
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        if args.list_ports:
            print(port_listing(ports))
            return 0
        port = select_port(ports, args.port)
        check_destination(args.out)
        print(f"Opening {port} at {args.baud} baud; output: {args.out.resolve()}", flush=True)
        with serial.Serial(port, baudrate=args.baud, timeout=0.2, write_timeout=1) as ser:
            with Capture(args.out) as capture:
                print("Waiting for metadata (passive)." if args.passive else "Requesting metadata (emit meta).", flush=True)
                run_capture(ser, capture, args.passive, report=lambda text: print(text, flush=True))
    except ImportError:
        print("Install the serial dependency: python -m pip install pyserial", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCapture stopped.")
    except (OSError, CaptureError, serial.SerialException) as exc:
        print(f"Capture failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if capture is not None:
            print(f"Captured: {capture.counts['CSW']} swings, {capture.counts['CPS']} PPS; "
                  f"{capture.malformed} malformed, {capture.before_ready} before metadata; {capture.lines} received lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
