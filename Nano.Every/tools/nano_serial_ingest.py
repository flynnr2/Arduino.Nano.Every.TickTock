#!/usr/bin/env python3
"""Reference Nano Every serial ingestor.

Design notes:
- Raw capture is authoritative. Every decoded line is persisted to raw_serial.csvl
  before parse/routing so forensic replay remains possible.
- DERIVED mode uses SMP rows; CANONICAL mode uses CSW/CPS rows. Hosts should
  support both because firmware mode and historic logs can vary.
- HDR_PART rows are preserved as observed schema metadata, but their
  concatenation order is not authoritative SMP field order. SMP ordering is the
  PendulumProtocol SAMPLE_SCHEMA contract.
- STS payloads evolve; this ingestor preserves them flexibly instead of
  hard-coding every family.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


HOST_FIELDS = ["host_utc_iso", "host_monotonic_ns", "rx_line_number"]
SMP_FIELDS = [
    "tick","tick_adj","tick_block","tick_block_adj","tick_total_adj_direct",
    "tick_total_adj_diag","tock","tock_adj","tock_block","tock_block_adj",
    "tock_total_adj_direct","tock_total_adj_diag","tick_total_f_hat_hz",
    "tock_total_f_hat_hz","gps_status","holdover_age_ms","dropped",
    "adj_diag","adj_comp_diag","pps_seq_row",
]
CSW_FIELDS = ["seq","edge0_tcb0","edge1_tcb0","edge2_tcb0","edge3_tcb0","edge4_tcb0","drop_ir","drop_pps","drop_swing","adj_diag","adj_comp_diag"]
CPS_FIELDS = ["seq","edge_tcb0","gps_status","holdover_age_ms","cap16","latency16","now32","drop_pps"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_csv_line(line: str) -> list[str]:
    return next(csv.reader([line]))


def parse_kv_tokens(tokens: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for tok in tokens:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def jsonl_write(fp: TextIO, obj: dict[str, Any]) -> None:
    fp.write(json.dumps(obj, separators=(",", ":")) + "\n")


@dataclass
class Session:
    out_dir: Path
    raw_fp: TextIO
    malformed_fp: TextIO
    sts_fp: TextIO
    cfg_fp: TextIO
    schema_fp: TextIO
    events_fp: TextIO
    manifest_path: Path
    parsed_flush_every: int
    raw_flush_every: int
    rotate_lines: int | None
    rx_line_number: int = 0
    smp_row_number: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    observed_tags: set[str] = field(default_factory=set)
    observed_emit_modes: set[str] = field(default_factory=set)
    schema_ids: dict[str, str] = field(default_factory=dict)
    files_written: set[str] = field(default_factory=set)
    malformed_count: int = 0
    stop_requested: bool = False
    status: str = "running"
    error: str | None = None

    def __post_init__(self) -> None:
        self._smp_state = self._open_structured_csv("derived_smp.csv", SMP_FIELDS)
        self._csw_state = self._open_structured_csv("canonical_swing.csv", CSW_FIELDS)
        self._cps_state = self._open_structured_csv("canonical_pps.csv", CPS_FIELDS)
        self._raw_since_flush = 0
        self._parsed_since_flush = 0

    def _open_structured_csv(self, name: str, fields: list[str]) -> dict[str, Any]:
        p = self.out_dir / name
        fp = p.open("w", newline="", encoding="utf-8")
        writer = csv.writer(fp)
        writer.writerow(HOST_FIELDS + fields)
        self.files_written.add(name)
        return {"fp": fp, "writer": writer, "fields": fields, "name": name}

    def host_meta(self) -> dict[str, Any]:
        return {
            "host_utc_iso": utc_now_iso(),
            "host_monotonic_ns": time.monotonic_ns(),
            "rx_line_number": self.rx_line_number,
        }

    def event(self, kind: str, **payload: Any) -> None:
        rec = self.host_meta() | {"event": kind} | payload
        jsonl_write(self.events_fp, rec)
        self.events_fp.flush()

    def write_raw(self, raw_line: str) -> dict[str, Any]:
        self.rx_line_number += 1
        meta = self.host_meta()
        jsonl_write(self.raw_fp, meta | {"raw_line": raw_line})
        self._raw_since_flush += 1
        if self._raw_since_flush >= self.raw_flush_every:
            self.raw_fp.flush()
            self._raw_since_flush = 0
        if self.rotate_lines and self.rx_line_number % self.rotate_lines == 0:
            self.event("rotate_hint", line=self.rx_line_number, note="rotation threshold reached; simple example keeps one file")
        return meta

    def parsed_flush_tick(self) -> None:
        self._parsed_since_flush += 1
        if self._parsed_since_flush >= self.parsed_flush_every:
            for st in (self._smp_state, self._csw_state, self._cps_state):
                st["fp"].flush()
            self.sts_fp.flush(); self.cfg_fp.flush(); self.schema_fp.flush(); self.malformed_fp.flush()
            self._parsed_since_flush = 0


def handle_structured(session: Session, tag: str, fields: list[str], meta: dict[str, Any], raw_line: str) -> None:
    mapping = {"SMP": session._smp_state, "CSW": session._csw_state, "CPS": session._cps_state}
    st = mapping[tag]
    expected = len(st["fields"])
    if len(fields) != expected:
        malformed(session, "field_count_mismatch", raw_line, meta, tag=tag, expected=expected, observed=len(fields))
        return
    if tag == "SMP":
        session.smp_row_number += 1
    st["writer"].writerow([meta[k] for k in HOST_FIELDS] + fields)
    session.parsed_flush_tick()


def malformed(session: Session, reason: str, raw_line: str, meta: dict[str, Any], **extra: Any) -> None:
    session.malformed_count += 1
    jsonl_write(session.malformed_fp, meta | {"reason": reason, "raw_line": raw_line} | extra)
    session.malformed_fp.flush()


def route_record(session: Session, raw_line: str, meta: dict[str, Any]) -> None:
    if not raw_line.strip():
        malformed(session, "empty_line", raw_line, meta)
        return
    try:
        cols = parse_csv_line(raw_line.rstrip("\r\n"))
    except Exception as exc:
        malformed(session, "csv_parse_error", raw_line, meta, error=str(exc))
        return
    if not cols:
        malformed(session, "no_fields", raw_line, meta)
        return
    tag, payload = cols[0], cols[1:]
    session.observed_tags.add(tag)
    session.counts[tag] = session.counts.get(tag, 0) + 1

    if tag in {"SMP", "CSW", "CPS"}:
        handle_structured(session, tag, payload, meta, raw_line)
    elif tag == "STS":
        status = payload[0] if payload else ""
        body = payload[1:] if len(payload) > 1 else []
        kv = parse_kv_tokens(body)
        family = body[0].split("=", 1)[0] if body else ""
        jsonl_write(session.sts_fp, meta | {"status_code": status, "family": family, "payload": body, "payload_joined": ",".join(body), "kv": kv, "raw_line": raw_line})
        session.parsed_flush_tick()
    elif tag == "CFG":
        kv = parse_kv_tokens(payload)
        em = kv.get("em")
        if em:
            session.observed_emit_modes.add(em)
        for key in ("pv", "nhz", "ss", "css", "cps", "fw"):
            if key in kv:
                session.schema_ids[key] = kv[key]
        jsonl_write(session.cfg_fp, meta | {"tokens": payload, "kv": kv, "raw_line": raw_line})
        session.parsed_flush_tick()
    elif tag in {"HDR_PART", "SCH"}:
        rec = {"tag": tag, "payload": payload, "raw_line": raw_line}
        if tag == "SCH" and len(payload) >= 3:
            decl_tag, schema_id, schema_fields = payload[0], payload[1], payload[2:]
            rec |= {"decl_tag": decl_tag, "schema_id": schema_id, "schema_fields": schema_fields}
            if decl_tag in {"CSW", "CPS"}:
                expected = CSW_FIELDS if decl_tag == "CSW" else CPS_FIELDS
                if schema_fields != expected:
                    session.event("schema_mismatch", decl_tag=decl_tag, expected=expected, observed=schema_fields)
        if tag == "HDR_PART" and len(payload) >= 3:
            rec |= {"part_index": payload[0], "part_count": payload[1], "fields": payload[2:]}
        jsonl_write(session.schema_fp, meta | rec)
        session.parsed_flush_tick()
    else:
        malformed(session, "unknown_tag", raw_line, meta, tag=tag)


def write_manifest(session: Session, args: argparse.Namespace, started_at: str, ended_at: str) -> None:
    manifest = {
        "started_at_utc": started_at,
        "ended_at_utc": ended_at,
        "args": vars(args),
        "status": session.status,
        "error": session.error,
        "counts_by_tag": session.counts,
        "observed_tags": sorted(session.observed_tags),
        "observed_emit_modes": sorted(session.observed_emit_modes),
        "schema_ids": session.schema_ids,
        "malformed_count": session.malformed_count,
        "files_written": sorted(session.files_written | {"raw_serial.csvl","status_sts.csvl","cfg.csvl","schema.csvl","malformed.csvl","ingest_events.csvl","session_manifest.json"}),
    }
    session.manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def self_test() -> int:
    lines = [
        "CFG,pv=1,nhz=16000000,st=SMP,ss=raw_cycles_hz_v7,asv=2,hm=segmented_v1,em=CANONICAL,cst=CSW,css=canonical_swing_v1,cpt=CPS,cps=canonical_pps_v1,fw=test",
        "SCH,CSW,canonical_swing_v1,seq,edge0_tcb0,edge1_tcb0,edge2_tcb0,edge3_tcb0,edge4_tcb0,drop_ir,drop_pps,drop_swing,adj_diag,adj_comp_diag",
        "SCH,CPS,canonical_pps_v1,seq,edge_tcb0,gps_status,holdover_age_ms,cap16,latency16,now32,drop_pps",
        "CSW,1,100,200,300,400,500,0,0,0,0,0",
        "CPS,1,16000000,2,0,12345,93,16000001,0",
        "HDR_PART,1,4,tick,tick_block,tock,tock_block",
        "SMP,1,1,2,2,3,0,4,4,5,5,6,0,16000000,16000000,2,0,0,0,0,1",
        "STS,PROGRESS_UPDATE,cfg,pv=1,nhz=16000000,em=CANONICAL",
        "BAD,xyz",
    ]
    out = Path("/tmp/nano_ingest_selftest")
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*"):
        f.unlink()
    with (out / "raw_serial.csvl").open("w", encoding="utf-8") as raw, \
         (out / "malformed.csvl").open("w", encoding="utf-8") as mal, \
         (out / "status_sts.csvl").open("w", encoding="utf-8") as sts, \
         (out / "cfg.csvl").open("w", encoding="utf-8") as cfg, \
         (out / "schema.csvl").open("w", encoding="utf-8") as sch, \
         (out / "ingest_events.csvl").open("w", encoding="utf-8") as evt:
        s = Session(out, raw, mal, sts, cfg, sch, evt, out / "session_manifest.json", 1, 1, None)
        for line in lines:
            meta = s.write_raw(line)
            route_record(s, line, meta)
        assert s.counts.get("SMP") == 1 and s.counts.get("CSW") == 1 and s.counts.get("CPS") == 1
        assert s.malformed_count == 1
    print("self-test passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ingest Nano Every CSV telemetry over serial into per-tag files.")
    p.add_argument("--port")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--out", default="./nano_serial_logs")
    p.add_argument("--session-name", default=f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_nano")
    p.add_argument("--rotate-lines", type=int, default=0)
    p.add_argument("--raw-flush-every", type=int, default=1)
    p.add_argument("--parsed-flush-every", type=int, default=10)
    p.add_argument("--serial-timeout", type=float, default=1.0)
    p.add_argument("--idle-status-seconds", type=float, default=30.0)
    p.add_argument("--no-console-status", action="store_true")
    p.add_argument("--self-test", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        return self_test()
    if not args.port:
        print("--port is required unless --self-test", file=sys.stderr)
        return 2
    root = Path(args.out)
    session_dir = root / args.session_name
    session_dir.mkdir(parents=True, exist_ok=False)
    started = utc_now_iso()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    with (session_dir / "raw_serial.csvl").open("w", encoding="utf-8") as raw, \
         (session_dir / "malformed.csvl").open("w", encoding="utf-8") as mal, \
         (session_dir / "status_sts.csvl").open("w", encoding="utf-8") as sts, \
         (session_dir / "cfg.csvl").open("w", encoding="utf-8") as cfg, \
         (session_dir / "schema.csvl").open("w", encoding="utf-8") as sch, \
         (session_dir / "ingest_events.csvl").open("w", encoding="utf-8") as evt:
        session = Session(session_dir, raw, mal, sts, cfg, sch, evt, session_dir / "session_manifest.json", max(1,args.parsed_flush_every), max(1,args.raw_flush_every), args.rotate_lines or None)
        def _stop(_sig: int, _frm: Any) -> None:
            session.stop_requested = True
            session.event("signal_stop")
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        try:
            import serial
            with serial.Serial(args.port, args.baud, timeout=args.serial_timeout) as ser:
                session.event("serial_open", port=args.port, baud=args.baud)
                last_status = time.monotonic()
                while not session.stop_requested:
                    b = ser.readline()
                    if not b:
                        if (not args.no_console_status) and (time.monotonic() - last_status) >= args.idle_status_seconds:
                            logging.info("idle: lines=%d malformed=%d tags=%s", session.rx_line_number, session.malformed_count, sorted(session.observed_tags))
                            last_status = time.monotonic()
                        continue
                    decoded = b.decode("utf-8", errors="replace")
                    raw_line = decoded.rstrip("\r\n")
                    meta = session.write_raw(raw_line)
                    route_record(session, raw_line, meta)
                session.status = "stopped_signal"
        except Exception as exc:
            session.status = "error"
            session.error = str(exc)
            session.event("exception", error=str(exc))
            logging.exception("ingestor exiting on exception")
            return_code = 1
        else:
            return_code = 0
        finally:
            write_manifest(session, args, started, utc_now_iso())
            session.event("shutdown", status=session.status)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
