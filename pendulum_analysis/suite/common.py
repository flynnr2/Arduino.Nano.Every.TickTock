"""Shared input, chronology and statistics contracts for the analysis suite."""
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import gzip
import json
from typing import Optional

import numpy as np
import pandas as pd

U32 = 2**32
ENV = ("temperature_C", "humidity_pct", "pressure_hPa")


@dataclass(frozen=True)
class Settings:
    profile: str = "synchronome"
    nominal_hz: float = 16_000_000.0
    swing_period_s: float = 2.0
    pps_window_seconds: float = 61.0
    phase_origin: int = 0
    stale_seconds: int = 3600
    min_hour_samples: int = 1800
    max_blocked_fraction: float = .20
    half_period_tolerance: float = .25
    diagnostics: bool = False
    detail_start_hours: Optional[float] = None
    detail_duration_seconds: float = 90.0
    autocorrelation: bool = False

    def __post_init__(self):
        if self.detail_start_hours is not None and (not np.isfinite(self.detail_start_hours) or self.detail_start_hours < 0):
            raise ValueError("detail_start_hours must be finite and nonnegative")
        if not np.isfinite(self.detail_duration_seconds) or self.detail_duration_seconds <= 0:
            raise ValueError("detail_duration_seconds must be finite and positive")
        if not np.isfinite(self.pps_window_seconds) or self.pps_window_seconds < 5:
            raise ValueError("pps_window_seconds must be finite and at least 5")
        if self.profile not in ("synchronome","generic"):
            raise ValueError("profile must be synchronome or generic")
        if not 0 < self.max_blocked_fraction < 1 or not 0 < self.half_period_tolerance < 1:
            raise ValueError("Optical clearance thresholds must be between zero and one")
        if not np.isfinite(self.nominal_hz) or not 1 <= self.nominal_hz < U32 / 4:
            raise ValueError("nominal_hz must be finite and between 1 and 2^30")
        if not np.isfinite(self.swing_period_s) or not 0 < self.swing_period_s < U32 / self.nominal_hz / 4:
            raise ValueError("swing_period_s is outside the unambiguous timer range")
        for name in ("phase_origin", "stale_seconds", "min_hour_samples"):
            if isinstance(getattr(self, name), bool) or not isinstance(getattr(self, name), int):
                raise ValueError(f"{name} must be an integer")
        if self.stale_seconds < 60 or not 1 <= self.min_hour_samples <= 3600:
            raise ValueError("stale_seconds must be >=60 and min_hour_samples must be 1..3600")


def discover(path):
    path = Path(path).resolve()
    directory = path if path.is_dir() else path.parent
    if not path.exists():
        raise ValueError(f"Input does not exist: {path}")
    found = {}
    for role in ("pcps", "pcsw", "sts"):
        matches = [p for p in directory.iterdir() if p.is_file() and p.name.casefold() in (role + ".csv", role + ".csv.gz")]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous case variants for {role}.csv")
        if matches:
            found[role] = matches[0]
    if not path.is_dir() and path.name.casefold() not in ("pcps.csv", "pcps.csv.gz"):
        raise ValueError("Provide a run directory or its PCPS.CSV")
    if "pcps" not in found:
        raise ValueError("PCPS.CSV is required")
    return found


def read_numeric(path, required):
    # Fail on malformed CSV syntax; do not let pandas infer an extra index field.
    import csv
    opener = gzip.open if str(path).lower().endswith('.gz') else open
    with opener(path, 'rt', newline="") as stream:
        header = next(csv.reader(stream),None)
    if not header:
        raise ValueError(f"Empty CSV: {path}")
    if len(header) != len(set(header)):
        raise ValueError(f"Duplicate columns in {path}")
    if set(required) - set(header):
        raise ValueError(f"{path.name} missing columns: {sorted(set(required)-set(header))}")
    d = pd.read_csv(path, skip_blank_lines=False)
    if not isinstance(d.index, pd.RangeIndex) or d.empty:
        raise ValueError(f"Empty input or malformed field count: {path}")
    d.insert(0, "source_row", np.arange(2, len(d)+2, dtype=np.int64))
    invalid_fields=[]
    for col in d.columns:
        if col != "source_row":
            numeric = pd.to_numeric(d[col], errors="coerce")
            if col in required:
                bad=~uint_valid(numeric,16 if col in ("cap16","latency16") else 32)
                if col=="gps_status":bad |= ~numeric.isin([0,1,2,3]).to_numpy()
                for i in np.flatnonzero(bad):
                    invalid_fields.append(dict(source_row=int(i+2),column=col,raw_value=str(d[col].iloc[i]),reason="invalid_required_field"))
            d[col] = numeric
    d.attrs["invalid_fields"]=invalid_fields
    return d


def uint_valid(values, bits=32):
    a = np.asarray(values, dtype=float)
    return np.isfinite(a) & (a >= 0) & (a < 2**bits) & (a == np.floor(a))


def signed_mod(a, modulus=U32):
    return (np.asarray(a) + modulus//2) % modulus - modulus//2


def timeline(seq, edge, row_valid, expected_cycles, max_consecutive_cycles=None):
    """Infer wraps only with chronological evidence; never sort or compress rows.

    Within gaps, whole-wrap selection assumes one expected interval per sequence
    step. Disagreement over 10% of one period starts a new epoch. Sequence resets,
    malformed records and duplicate sequences also start epochs.
    """
    s, e = np.asarray(seq, float), np.asarray(edge, float)
    valid = np.asarray(row_valid, bool)
    step = np.r_[np.nan, np.diff(s)]
    wrap = (step < 0) & (np.r_[np.nan, s[:-1]] > U32-65536) & (s < 65536)
    step[wrap] += U32
    # Boolean cumsum defaults to the platform integer width (32-bit on Pi).
    # A 32-bit array cannot multiply by the 2**32 sequence modulus.
    wrap_count = np.cumsum(wrap, dtype=np.int64)
    resets = (step < 0) & ~wrap
    wrap_count -= np.maximum.accumulate(np.where(resets,wrap_count,0))
    seq_extended = s + wrap_count*np.int64(U32)
    dt = np.r_[np.nan, np.diff(e)] % U32
    pair = valid & np.r_[False, valid[:-1]]
    gap = pair & (step > 1)
    dt[gap] += np.maximum(0, np.rint((step[gap]*expected_cycles-dt[gap])/U32))*U32
    ambiguous = gap & (np.abs(dt-step*expected_cycles) > expected_cycles*.1)
    maximum=expected_cycles*2 if max_consecutive_cycles is None else max_consecutive_cycles
    ambiguous |= pair & (step == 1) & ((dt <= 0) | (dt > maximum))
    boundary = ~pair | (step <= 0) | ambiguous
    boundary[0] = True
    dt[boundary] = np.nan
    epoch = np.cumsum(boundary).astype(np.int32)-1
    # These coordinates join observed spans; elapsed time between epochs is unknown.
    ticks = np.cumsum(np.nan_to_num(dt))
    return pd.DataFrame(dict(epoch=epoch, sequence_extended=seq_extended,
                             seq_step=step, delta_cycles=dt, elapsed_cycles=ticks,
                             boundary=boundary, ambiguous=ambiguous))


def stats(a):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    if not len(a):
        return dict(n=0, mean=None, median=None, std=None, robust_sigma=None, min=None, max=None)
    median = float(np.median(a))
    return dict(n=len(a), mean=float(a.mean()), median=median,
                std=float(a.std(ddof=1)) if len(a)>1 else None,
                robust_sigma=float(1.4826*np.median(np.abs(a-median))),
                min=float(a.min()), max=float(a.max()))


def finite_json(value):
    if isinstance(value, dict):
        return {str(k):finite_json(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json(v) for v in value]
    if isinstance(value, np.ndarray):
        return finite_json(value.tolist())
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    return value


def write_json(path, value):
    Path(path).write_text(json.dumps(finite_json(value), indent=2, allow_nan=False)+"\n")


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024), b""):
            digest.update(chunk)
    return dict(path=str(Path(path).resolve()), bytes=Path(path).stat().st_size, sha256=digest.hexdigest())


def environmental_validity(d, tl, cfg):
    """Retrospective unchanged-run flag, within observed chronological epochs."""
    events, coverage = [], []
    time = tl.elapsed_cycles.to_numpy()/cfg.nominal_hz
    epochs = tl.epoch.to_numpy()
    masks = {}
    for col in ENV:
        if col not in d:
            coverage.append(dict(channel=col, present=False, valid_records=0, stale_records=0))
            continue
        a = d[col].to_numpy(float)
        finite = np.isfinite(a)
        physical = finite.copy()
        if col == "humidity_pct":
            physical &= (a>=0)&(a<=100)
        elif col == "pressure_hPa":
            physical &= a>0
        else:
            physical &= a>=-273.15
        start = np.r_[0,np.flatnonzero((a[1:] != a[:-1]) | (epochs[1:] != epochs[:-1]) | ~finite[1:] | ~finite[:-1])+1]
        end = np.r_[start[1:]-1,len(a)-1]
        stale = np.zeros(len(a),bool)
        candidates = physical[start] & (time[end]-time[start]>=cfg.stale_seconds) & (end-start>=cfg.stale_seconds/2)
        for i,j in zip(start[candidates],end[candidates]):
                stale[i:j+1] = True
                events.append(dict(channel=col, epoch=int(epochs[i]), start_source_row=int(i+2),
                    end_source_row=int(j+2), start_day=time[i]/86400, end_day=time[j]/86400,
                    records=int(j-i+1), value=float(a[i]), reason="unchanged_for_at_least_threshold"))
        masks[col] = physical & ~stale
        coverage.append(dict(channel=col,present=True,valid_records=int(masks[col].sum()),
                             stale_records=int(stale.sum()),invalid_records=int((~physical).sum())))
    return masks, pd.DataFrame(events, columns=["channel","epoch","start_source_row","end_source_row","start_day","end_day","records","value","reason"]), pd.DataFrame(coverage)
