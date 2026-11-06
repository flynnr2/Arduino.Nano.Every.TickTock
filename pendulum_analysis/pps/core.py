"""PPS capture diagnostics. All calculations are local, deterministic and unattended.

Counter values are never sorted: file order is evidence. Ambiguous chronology
starts a new segment, and frequency statistics never bridge such a boundary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Optional
import hashlib
import re

import numpy as np
import pandas as pd

U32 = 2**32
U16 = 2**16
REQUIRED = ("seq", "edge_tcb0", "cap16", "latency16", "now32")


@dataclass(frozen=True)
class PpsConfig:
    nominal_hz: float = 16_000_000.0
    normal_interval_tolerance: float = 0.05
    large_latency_cycles: int = 485
    rail_latency_cycles: int = 152
    wrap_window_cycles: int = 250
    max_projection_adjustment_cycles: int = 64
    min_hourly_samples: int = 1800
    swing_period_s: float = 2.0
    event_window_records: int = 2

    def __post_init__(self):
        if not np.isfinite(self.nominal_hz) or not 1 <= self.nominal_hz < U32 / 2:
            raise ValueError("nominal_hz must be finite and between 1 and 2^31 Hz")
        if not 0 < self.normal_interval_tolerance < 0.5:
            raise ValueError("normal_interval_tolerance must be between 0 and 0.5")
        if not np.isfinite(self.swing_period_s) or self.swing_period_s <= 0:
            raise ValueError("swing_period_s must be finite and positive")
        for name in ("large_latency_cycles", "rail_latency_cycles", "wrap_window_cycles",
                     "max_projection_adjustment_cycles", "min_hourly_samples", "event_window_records"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if not 1 <= self.wrap_window_cycles < U16 / 2:
            raise ValueError("wrap_window_cycles must be between 1 and 32767")
        if self.max_projection_adjustment_cycles >= U16 / 2 or self.event_window_records > 100:
            raise ValueError("projection adjustment must be <32768; event window must be <=100")


@dataclass
class PpsResult:
    frame: pd.DataFrame
    summary: dict[str, Any]
    tables: dict[str, pd.DataFrame]
    warnings: list[str]
    config: PpsConfig
    inputs: dict[str, Path] = field(default_factory=dict)


def signed_mod(values, modulus=U16):
    return (values + modulus // 2) % modulus - modulus // 2


def discover(input_path: Path) -> dict[str, Path]:
    path = Path(input_path).resolve()
    directory = path if path.is_dir() else path.parent
    if not directory.is_dir():
        raise ValueError(f"Input directory does not exist: {directory}")
    found = {}
    for role in ("pcps", "pcsw", "sts"):
        matches = [p for p in directory.iterdir() if p.is_file()
                   and p.name.casefold() in (role + ".csv", role + ".csv.gz")]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous {role.upper()} input: multiple case variants")
        if matches:
            found[role] = matches[0]
    if not path.is_dir():
        if not path.is_file():
            raise ValueError(f"Input file does not exist: {path}")
        found["pcps"] = path
    if "pcps" not in found:
        raise ValueError("PCPS.CSV is required (file names are case insensitive)")
    return found


def read_status(path: Optional[Path]) -> tuple[pd.DataFrame, dict[str, str]]:
    if path is None:
        return pd.DataFrame(columns=["source_row", "ts_ms", "raw"]), {}
    data = pd.read_csv(path)
    if "raw" not in data:
        raise ValueError("STS.CSV is missing its raw column")
    data.insert(0, "source_row", np.arange(2, len(data) + 2))
    # Keep every original status row. Settings are metadata, never instructions.
    values = {}
    for value in data["raw"].dropna().astype(str):
        for key, val in re.findall(r"(?:^|,)([A-Za-z_][A-Za-z_0-9]*)=([^,]+)", value):
            if key in values and values[key] != val:
                values[key] = "multiple values"
            else:
                values[key] = val
    return data, values


def _uint_valid(series: pd.Series, modulus: int) -> np.ndarray:
    a = series.to_numpy(dtype=float)
    return np.isfinite(a) & (a >= 0) & (a < modulus) & (a == np.floor(a))


def reconstruct_timeline(seq, edge, hz: float, row_valid=None, gap_tolerance_cycles=None, max_consecutive_cycles=None):
    """Return elapsed ticks, intervals, seq steps and segment ids.

    For gaps, select a wrap count only if it agrees with the PPS sequence gap to
    within 0.1 second. This is a documented one-pulse/second inference, not UTC.
    Cadence anomalies below half a counter wrap preserve counter continuity.
    """
    seq = np.asarray(seq, dtype=float)
    edge = np.asarray(edge, dtype=float)
    n = len(seq)
    valid = np.isfinite(seq) & np.isfinite(edge) if row_valid is None else np.asarray(row_valid)
    step = np.r_[np.nan, np.diff(seq)]
    seq_wrap = (step < 0) & (np.r_[np.nan, seq[:-1]] > U32 - 65536) & (seq < 65536)
    step[seq_wrap] += U32
    dt = np.r_[np.nan, np.diff(edge)] % U32
    pair = valid & np.r_[False, valid[:-1]]
    gaps = pair & (step > 1)
    expected = step * hz
    dt[gaps] += np.maximum(0, np.rint((expected[gaps] - dt[gaps]) / U32)) * U32
    tolerance = hz * 0.1 if gap_tolerance_cycles is None else gap_tolerance_cycles
    ambiguous = gaps & (dt >= U32 / 2) & (np.abs(dt - expected) > tolerance)
    maximum = U32 / 2 if max_consecutive_cycles is None else max_consecutive_cycles
    ambiguous |= pair & (step == 1) & (dt > maximum)
    boundary = ~pair | (step <= 0) | ambiguous
    boundary[0] = True
    dt[boundary] = np.nan
    segments = np.cumsum(boundary).astype(np.int32) - 1
    # Concatenated observed time; missing durations between segments are unknown.
    elapsed = np.cumsum(np.nan_to_num(dt, nan=0.0)).astype(np.int64)
    return elapsed, dt, step, segments, ambiguous


def describe(values) -> dict[str, Any]:
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a)]
    if not len(a):
        return {"count": 0, "min": None, "median": None, "mean": None, "p99": None, "max": None, "std": None}
    return {"count": len(a), "min": float(a.min()), "median": float(np.median(a)),
            "mean": float(a.mean()), "p99": float(np.quantile(a, .99)), "max": float(a.max()),
            "std": float(a.std(ddof=1)) if len(a) > 1 else None}


def analyze_frame(data: pd.DataFrame, config: Optional[PpsConfig] = None) -> PpsResult:
    cfg = config or PpsConfig()
    missing = set(REQUIRED) - set(data)
    if missing:
        raise ValueError("PCPS missing required columns: " + ", ".join(sorted(missing)))
    if data.empty:
        raise ValueError("PCPS contains no data records")
    f = data.copy().reset_index(drop=True)
    f.insert(0, "source_row", np.arange(2, len(f) + 2))
    warnings = []
    for c in f.columns:
        f[c] = pd.to_numeric(f[c], errors="coerce")
    valid = np.ones(len(f), dtype=bool)
    for c in REQUIRED:
        valid &= _uint_valid(f[c], U16 if c in ("cap16", "latency16") else U32)
    f["row_valid"] = valid
    elapsed, dt, step, segment, ambiguous = reconstruct_timeline(
        f.seq, f.edge_tcb0, cfg.nominal_hz, valid)
    f["elapsed_cycles"] = elapsed
    f["segment"] = segment
    f["seq_delta"] = step
    valid_pair = valid & np.r_[False, valid[:-1]]
    f["interval_cycles"] = dt
    f["frequency_offset_cycles"] = dt - cfg.nominal_hz
    projection = (f.cap16 - f.edge_tcb0) % U16
    projection = projection.where(valid)
    f["projection_offset_cycles"] = projection
    baseline = projection.groupby(segment).transform(lambda x: x.mode().iloc[0] if x.notna().any() else np.nan)
    shift = signed_mod(baseline - projection)
    f["projection_shift_cycles"] = shift
    coherent = ((f.now32 - f.edge_tcb0) % U32 == f.latency16) & valid
    f["reconstruction_consistent"] = coherent
    normal = np.isfinite(dt) & (step == 1) & (np.abs(dt - cfg.nominal_hz) <= cfg.nominal_hz * cfg.normal_interval_tolerance)
    state = f.get("gps_status", pd.Series(np.nan, index=f.index))
    if "gps_status" not in f:
        warnings.append("gps_status is missing; locked oscillator statistics are unavailable.")
    drop = f.get("drop_pps", pd.Series(np.nan, index=f.index))
    if "drop_pps" not in f:
        warnings.append("drop_pps is missing; drop-checked oscillator statistics are unavailable.")
    drop_delta = drop.diff() % U32
    f["drop_delta"] = drop_delta
    # Counters are cumulative. A historical nonzero count is not a permanent veto.
    locked_pair = (state == 2) & (state.shift() == 2)
    valid_drop = _uint_valid(drop, U32)
    drop_ok = (drop_delta == 0) & valid_drop & np.r_[False, valid_drop[:-1]]
    frequency_valid = normal & locked_pair & drop_ok & coherent & coherent.shift(fill_value=False)
    f["frequency_valid"] = frequency_valid
    adjusted = f.frequency_offset_cycles - shift.diff()
    adjusted_valid = frequency_valid & (np.abs(shift) <= cfg.max_projection_adjustment_cycles) & (np.abs(shift.shift()) <= cfg.max_projection_adjustment_cycles)
    f["diagnostic_adjusted_offset_cycles"] = adjusted.where(adjusted_valid)
    large = valid & (f.latency16 > cfg.large_latency_cycles)
    shifted = valid & (shift != 0)
    rail = valid & (f.latency16 == cfg.rail_latency_cycles)
    phase = signed_mod(f.edge_tcb0)
    f["wrap_phase_cycles"] = phase
    state_transition = (state != state.shift()) & ~(state.isna() & state.shift().isna())
    state_transition.iloc[0] = False
    # Two short consecutive intervals adding to one normal second suggest an extra capture.
    split = ((f.interval_cycles > 0) & (f.interval_cycles < cfg.nominal_hz * (1 - cfg.normal_interval_tolerance)) &
             (f.interval_cycles.shift(-1) > 0) &
             (f.interval_cycles.shift(-1) < cfg.nominal_hz * (1 - cfg.normal_interval_tolerance)) &
             (np.abs(f.interval_cycles + f.interval_cycles.shift(-1) - cfg.nominal_hz) < cfg.nominal_hz * .01) &
             (f.seq_delta == 1) & (f.seq_delta.shift(-1) == 1) & (f.segment == f.segment.shift(-1)))
    segment_start = np.r_[False, np.diff(segment) != 0]
    conditions = {
        "malformed_record": ~valid,
        "segment_boundary": segment_start,
        "ambiguous_timeline": ambiguous,
        "sequence_gap": valid_pair & np.isfinite(step) & (step > 1),
        "duplicate_sequence": valid & (step == 0),
        "sequence_reversal": valid & (step < 0),
        "duplicate_timestamp": valid & (dt == 0),
        "exceptional_interval": np.isfinite(dt) & (step == 1) & ~normal,
        "possible_extra_capture": split,
        "gps_state_change": state_transition,
        "pps_drop_change": (drop_delta > 0) & ~segment_start,
        "reconstruction_mismatch": valid & ~coherent,
        "projection_shift": shifted,
        "large_latency": large,
    }
    # Build reason strings only for events, not for millions of normal rows.
    event_indices = np.flatnonzero(np.logical_or.reduce([np.asarray(v) for v in conditions.values()]))
    events = f.iloc[event_indices].copy()
    events.insert(0, "event", pd.Series([";".join(k for k, v in conditions.items() if np.asarray(v)[i]) for i in event_indices], index=events.index, dtype="str"))
    events["next_frequency_offset_cycles"] = f.frequency_offset_cycles.shift(-1).iloc[event_indices].to_numpy()
    events["next_projection_shift_cycles"] = shift.shift(-1).iloc[event_indices].to_numpy()
    events["missing_records"] = np.maximum(0, events.seq_delta - 1)

    state_run = (state_transition | (f.segment != f.segment.shift()) | (f.seq_delta != 1)).cumsum()
    states = pd.DataFrame({"run": state_run, "source_row": f.source_row, "seq": f.seq,
                          "gps_status": state, "segment": segment, "elapsed_cycles": elapsed})
    states = states.groupby("run", sort=False).agg(segment=("segment", "first"), gps_status=("gps_status", "first"),
            first_source_row=("source_row", "first"), last_source_row=("source_row", "last"),
            first_seq=("seq", "first"), last_seq=("seq", "last"), records=("seq", "size"),
            start_cycles=("elapsed_cycles", "first"), end_cycles=("elapsed_cycles", "last")).reset_index(drop=True)

    hour = (elapsed / cfg.nominal_hz / 3600).astype(np.int64)
    temperature = f.get("temperature_C", pd.Series(np.nan, index=f.index))
    temperature = temperature.where(np.isfinite(temperature) & adjusted_valid)
    h = pd.DataFrame({"segment": segment, "hour": hour, "raw": f.frequency_offset_cycles.where(frequency_valid),
                      "adjusted": f.diagnostic_adjusted_offset_cycles,
                      "temperature_frequency": f.diagnostic_adjusted_offset_cycles.where(temperature.notna()),
                      "temperature": temperature})
    hourly = h.groupby(["segment", "hour"], sort=False).agg(raw_count=("raw", "count"), raw_mean_cycles=("raw", "mean"),
               adjusted_count=("adjusted", "count"), adjusted_mean_cycles=("adjusted", "mean"),
               temperature_C=("temperature", "mean"), temperature_count=("temperature", "count"),
               temperature_frequency_mean_cycles=("temperature_frequency", "mean")).reset_index()
    usable_hours = hourly[(hourly.adjusted_count >= cfg.min_hourly_samples) & (hourly.temperature_count >= cfg.min_hourly_samples)].dropna()
    temp_summary = {"hours": len(usable_hours), "slope_cycles_per_second_per_C": None, "slope_ppm_per_C": None, "correlation": None}
    if len(usable_hours) >= 3 and usable_hours.temperature_C.nunique() >= 2 and usable_hours.temperature_frequency_mean_cycles.nunique() >= 2:
        slope, intercept = np.polyfit(usable_hours.temperature_C, usable_hours.temperature_frequency_mean_cycles, 1)
        temp_summary.update(slope_cycles_per_second_per_C=float(slope), slope_ppm_per_C=float(slope / cfg.nominal_hz * 1e6),
                            intercept_cycles_per_second=float(intercept), correlation=float(usable_hours.temperature_C.corr(usable_hours.temperature_frequency_mean_cycles)))
    daily_input = pd.DataFrame({"day": (elapsed / cfg.nominal_hz / 86400).astype(int), "segment": segment,
                               "records": 1, "shifted": shifted, "large_latency": large, "rail": rail,
                               "latency_max": f.latency16.where(valid), "frequency_valid": frequency_valid})
    daily = daily_input.groupby(["segment", "day"], sort=False).agg(records=("records", "sum"), shifted=("shifted", "sum"),
              large_latency=("large_latency", "sum"), rail=("rail", "sum"), latency_max=("latency_max", "max"), frequency_valid=("frequency_valid", "sum")).reset_index()
    daily["shifted_per_million"] = daily.shifted / daily.records * 1e6
    daily["large_latency_per_million"] = daily.large_latency / daily.records * 1e6
    offsets = pd.DataFrame({"segment": segment, "projection_offset_cycles": projection, "shift_cycles": shift})
    offsets = offsets.groupby(["segment", "projection_offset_cycles", "shift_cycles"]).size().rename("records").reset_index()
    hist = f.loc[valid, "latency16"].value_counts().sort_index().rename_axis("latency_cycles").reset_index(name="records")
    phase_data = pd.DataFrame({"phase_cycles": phase, "latency_cycles": f.latency16, "shift_cycles": shift})
    phase_data = phase_data[valid & (np.abs(phase) <= cfg.wrap_window_cycles)]
    phase_bins = phase_data.groupby(["phase_cycles", "latency_cycles", "shift_cycles"]).size().rename("records").reset_index()
    windows = event_windows(f, event_indices, cfg.event_window_records)
    summary = {
        "schema": "pendulum_analysis.pps.v1", "records": len(f), "malformed_records": int((~valid).sum()),
        "segments": int(segment.max() + 1), "observed_duration_days": float(elapsed[-1] / cfg.nominal_hz / 86400),
        "frequency_valid_intervals": int(frequency_valid.sum()),
        "event_counts": {k: int(np.sum(v)) for k, v in conditions.items()},
        "missing_sequence_records": int(np.sum(np.where(valid_pair & np.isfinite(step) & (step > 1), step - 1, 0))),
        "latency_cycles": describe(f.loc[valid, "latency16"]),
        "large_latency_threshold_cycles": cfg.large_latency_cycles,
        "large_latency_max_us": float(f.loc[large, "latency16"].max() / cfg.nominal_hz * 1e6) if large.any() else None,
        "rail_records": int(rail.sum()), "rail_shifted_records": int((rail & shifted).sum()),
        "projection_shifted_records": int(shifted.sum()), "six_cycle_shift_records": int((valid & (shift == 6)).sum()),
        "shifted_wrap_phase_cycles": describe(phase[shifted]),
        "large_latency_projection_shifted_records": int((large & shifted).sum()),
        "raw_frequency_offset_cycles": describe(f.loc[frequency_valid, "frequency_offset_cycles"]),
        "diagnostic_adjusted_frequency_offset_cycles": describe(f.diagnostic_adjusted_offset_cycles),
        "temperature_fit": temp_summary,
        "gps_status_counts": {str(k): int(v) for k, v in state.value_counts(dropna=False).items()},
        "config": asdict(cfg),
    }
    if not valid.all():
        warnings.append("Malformed numeric records are retained in the event catalogue; intervals touching them are excluded.")
    if segment.max():
        warnings.append("Multiple timeline segments: elapsed time concatenates observed spans; time between segments is unknown.")
    if not frequency_valid.any():
        warnings.append("No intervals passed locked, consecutive, plausible and reconstruction/drop checks; oscillator statistics are unavailable.")
    if shifted.any():
        warnings.append("Projection changes are observed. Diagnostic normalization assumes stable timer phase within each segment; it is not corrected source data.")
    malformed = data.iloc[np.flatnonzero(~valid)].copy()
    malformed.insert(0, "source_row", np.flatnonzero(~valid) + 2)
    result = PpsResult(f, summary, {"events": events, "malformed_records": malformed, "event_windows": windows, "state_runs": states, "hourly_frequency": hourly,
            "daily_rates": daily, "projection_offsets": offsets, "latency_histogram": hist, "wrap_phase_bins": phase_bins}, warnings, cfg)
    from .summary import add_summary_tables
    add_summary_tables(result)
    return result


def event_windows(f: pd.DataFrame, indices: np.ndarray, radius: int) -> pd.DataFrame:
    if not len(indices):
        return pd.DataFrame(columns=["event_source_row", "relative_record"] + list(f.columns))
    offsets = np.arange(-radius, radius + 1)
    targets = (indices[:, None] + offsets).ravel()
    centers = np.repeat(indices, len(offsets))
    relative = np.tile(offsets, len(indices))
    valid = (targets >= 0) & (targets < len(f))
    targets, centers, relative = targets[valid], centers[valid], relative[valid]
    same = f.segment.to_numpy()[targets] == f.segment.to_numpy()[centers]
    out = f.iloc[targets[same]].copy()
    out.insert(0, "relative_record", relative[same])
    out.insert(0, "event_source_row", f.source_row.to_numpy()[centers[same]])
    return out.reset_index(drop=True)


def add_swing_association(result: PpsResult, path: Optional[Path]) -> None:
    """Align ordered, overlapping single-run streams using the shared raw counter.

    One 32-bit epoch remains unobservable from counters alone. Alignment assumes
    the file starts differ by < half a counter wrap, and is reported as such.
    Resets/malformed swings disable this association rather than matching epochs.
    """
    summary = {"available": False, "reason": "PCSW.CSV not supplied"}
    result.summary["swing_association"] = summary
    result.tables["swing_latency_bins"] = pd.DataFrame(columns=["since_edge4_us_start", "since_edge4_us_end", "records", "large_latency_records", "large_per_million"])
    if path is None:
        result.warnings.append("PCSW.CSV is absent; swing association is unavailable.")
        return
    columns = ["seq"] + [f"edge{i}_tcb0" for i in range(5)]
    try:
        sw = pd.read_csv(path, usecols=columns)
    except ValueError as exc:
        summary["reason"] = f"PCSW schema error: {exc}"
        result.warnings.append(summary["reason"])
        return
    for c in columns:
        sw[c] = pd.to_numeric(sw[c], errors="coerce")
    if sw.empty or not all(_uint_valid(sw[c], U32).all() for c in columns) or result.summary["segments"] != 1:
        summary["reason"] = "Association requires valid swing timestamps and one PPS timeline segment"
        result.warnings.append(summary["reason"])
        return
    swing_hz = result.config.nominal_hz * result.config.swing_period_s
    # Swing periods are not PPS seconds. Infer the observed period from adjacent
    # swings and permit accumulated period variation at a gap, while remaining
    # well below the half-wrap ambiguity bound.
    steps = sw.seq.diff().to_numpy()
    deltas = sw.edge4_tcb0.diff().to_numpy() % U32
    ordinary = deltas[(steps == 1) & (deltas > swing_hz * .5) & (deltas < swing_hz * 1.5)]
    if len(ordinary):
        swing_hz = float(np.median(ordinary))
    tolerance = np.minimum(U32 / 4, swing_hz * np.maximum(1, steps) * .05)
    elapsed, _, _, segment, _ = reconstruct_timeline(sw.seq, sw.edge4_tcb0, swing_hz,
        gap_tolerance_cycles=tolerance, max_consecutive_cycles=U32 / 2)
    if segment.max() != 0:
        summary["reason"] = "Swing resets or ambiguous chronology prevent automatic epoch alignment"
        result.warnings.append(summary["reason"])
        return
    start_offset = int(signed_mod(sw.edge4_tcb0.iloc[0] - result.frame.edge_tcb0.iloc[0], U32))
    edges4 = elapsed + start_offset
    if len(edges4) < 2 or np.any(np.diff(edges4) <= 0):
        summary["reason"] = "Insufficient or nonmonotonic swing completion edges"
        result.warnings.append(summary["reason"])
        return
    reconstructed_edges = np.column_stack([edges4 + signed_mod(sw[f"edge{i}_tcb0"].to_numpy() - sw.edge4_tcb0.to_numpy(), U32).astype(np.int64) for i in range(5)])
    if np.any(np.diff(reconstructed_edges, axis=1) <= 0) or np.any(np.diff(reconstructed_edges, axis=0) <= 0):
        summary["reason"] = "Swing edge order is inconsistent within rows or across records"
        result.warnings.append(summary["reason"])
        return
    f, cfg = result.frame, result.config
    query = f.elapsed_cycles.to_numpy()
    previous = np.searchsorted(edges4, query, side="right") - 1
    clipped = np.clip(previous, 0, len(edges4) - 1)
    delta = (query - edges4[clipped]).astype(float)
    next_sequence_gap = np.r_[np.diff(sw.seq.to_numpy()) != 1, False]
    # No matches beyond coverage or through missing swing rows.
    delta[(previous < 0) | (query > edges4[-1]) | next_sequence_gap[clipped] |
          (delta > cfg.swing_period_s * cfg.nominal_hz * 1.5)] = np.nan
    f["since_edge4_us"] = delta / cfg.nominal_hz * 1e6
    events = result.tables["events"]
    event_idx = events.source_row.to_numpy(dtype=int) - 2
    events["since_edge4_us"] = f.since_edge4_us.iloc[event_idx].to_numpy()
    events["preceding_swing_seq"] = np.where(np.isfinite(delta[event_idx]), sw.seq.to_numpy()[clipped[event_idx]], np.nan)
    for i in range(4):
        edge = reconstructed_edges[:, i]
        ix = np.searchsorted(edge, query[event_idx], side="right") - 1
        d = (query[event_idx] - edge[np.clip(ix, 0, len(edge) - 1)]).astype(float)
        d[(ix < 0) | (query[event_idx] > edge[-1]) | next_sequence_gap[np.clip(ix, 0, len(edge) - 1)] |
          (d > cfg.swing_period_s * cfg.nominal_hz * 1.5)] = np.nan
        events[f"since_edge{i}_us"] = d / cfg.nominal_hz * 1e6
    # Include all PPS captures as the exposure denominator, not just the spikes.
    edges = np.r_[np.arange(0, 2050, 50), 5000, 10000, 100000, 500000, 1000000, cfg.swing_period_s * 1.5e6]
    edges = np.unique(np.sort(edges))
    large = f.row_valid & (f.latency16 > cfg.large_latency_cycles)
    counts, _ = np.histogram(f.since_edge4_us, edges)
    spikes, _ = np.histogram(f.loc[large, "since_edge4_us"], edges)
    result.tables["swing_latency_bins"] = pd.DataFrame({"since_edge4_us_start": edges[:-1], "since_edge4_us_end": edges[1:],
        "records": counts, "large_latency_records": spikes,
        "large_per_million": np.divide(spikes * 1e6, counts, out=np.full(len(counts), np.nan), where=counts > 0)})
    summary.update(available=True, reason=None, swing_records=len(sw), associated_pps_records=int(np.isfinite(delta).sum()),
                   large_latency_records=int(large.sum()), associated_large_latency_records=int(np.isfinite(delta[large]).sum()),
                   large_latency_since_edge4_us=describe(f.loc[large, "since_edge4_us"]),
                   alignment="Shared counter; first file timestamps assumed within half a 32-bit wrap; gaps masked. No UTC inferred.")


def analyze_files(input_path: Path, config: Optional[PpsConfig] = None,
                  *, config_overrides: Optional[dict[str, Any]] = None) -> PpsResult:
    inputs = discover(input_path)
    status, metadata = read_status(inputs.get("sts"))
    cfg = config
    if cfg is None:
        hz = (config_overrides or {}).get("nominal_hz", metadata.get("nhz", metadata.get("f_cpu", "16000000")))
        cfg = PpsConfig(nominal_hz=float(hz)) if hz != "multiple values" else PpsConfig()
    if config_overrides:
        cfg = replace(cfg, **config_overrides)
    # pandas errors on broken CSV syntax; numeric failures are retained by analyze_frame.
    source = pd.read_csv(inputs["pcps"])
    if not isinstance(source.index, pd.RangeIndex):
        raise ValueError("PCPS records contain more fields than the header (implicit CSV index)")
    result = analyze_frame(source, cfg)
    result.inputs = inputs
    result.tables["status_records"] = status
    result.summary["status_metadata"] = metadata
    if "sts" not in inputs:
        result.warnings.append("STS.CSV is absent; nominal_hz uses the explicit configuration or the documented 16 MHz default.")
    for key in ("nhz", "f_cpu", "main_clock_hz"):
        if key in metadata and metadata[key] != str(int(cfg.nominal_hz)):
            result.warnings.append(f"STS {key}={metadata[key]} differs from configured nominal_hz={cfg.nominal_hz:g}.")
    add_swing_association(result, inputs.get("pcsw"))
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
