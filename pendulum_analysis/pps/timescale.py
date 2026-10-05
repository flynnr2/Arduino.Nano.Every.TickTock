"""Shared offline TCB0 timescale, independent of parser-local epoch labels.

PPS cadence and counter continuity are separate. Calibration never bridges a
bad PPS interval. Alignment uses whole hardware timestamp ranges; indistinguishable
wrap translations or reset epochs are rejected instead of guessed.
"""
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd

U32 = 2**32


@dataclass(frozen=True)
class TimescaleConfig:
    nominal_hz: float = 16_000_000.
    window_seconds: float = 61.
    min_points: int = 5

    def __post_init__(self):
        if not np.isfinite(self.nominal_hz) or not 0 < self.nominal_hz < U32/2:
            raise ValueError("nominal_hz must be finite, positive and below 2^31")
        if not np.isfinite(self.window_seconds) or self.window_seconds < 5:
            raise ValueError("window_seconds must be finite and at least 5")
        width = 2*int((self.window_seconds-1)//2)+1
        if isinstance(self.min_points, bool) or not isinstance(self.min_points, int) or not 3 <= self.min_points <= width:
            raise ValueError("min_points must be an integer from 3 to the centered window's sample count")


def _number(frame, column):
    return pd.to_numeric(frame.get(column, pd.Series(np.nan, index=frame.index)), errors="coerce").to_numpy(float)


def _uint(a, modulus=U32):
    return np.isfinite(a) & (a >= 0) & (a < modulus) & (a == np.floor(a))


def _unwrap(seq, raw, hz, period=1.):
    """Use sequence only to resolve long gaps, never to identify another stream."""
    seq, raw = np.asarray(seq, float), np.asarray(raw, float)
    n = len(raw)
    if not n:
        return np.array([]), np.array([], int), np.array([]), np.array([])
    valid = _uint(seq) & _uint(raw)
    step = np.r_[np.nan, np.diff(seq)] % U32
    delta = np.r_[np.nan, np.diff(raw)] % U32
    pair = valid & np.r_[False, valid[:-1]]
    expected = step*hz*period
    gaps = pair & (step > 1) & (step < U32/2)
    delta[gaps] += np.maximum(0, np.rint((expected[gaps]-delta[gaps])/U32))*U32
    boundary = ~pair | (step == 0) | (step >= U32/2)
    boundary |= gaps & (delta >= U32/2) & (abs(delta-expected) > hz*period*.1)
    boundary |= pair & (step == 1) & ((delta == 0) | (delta >= U32/2))
    boundary[0] = True
    delta[boundary] = np.nan
    segment = np.cumsum(boundary)-1
    cumulative = np.cumsum(np.nan_to_num(delta))
    starts = np.flatnonzero(boundary)
    ticks = cumulative-cumulative[starts][segment]+raw[starts][segment]
    ticks[~valid] = np.nan
    return ticks, segment, delta, step


def _phase_fit(ticks, config):
    """Quadratic phase fit on regularly spaced, validated one-second pulses."""
    n = len(ticks)
    frequency = np.full(n, np.nan)
    residual = np.full(n, np.nan)
    half = int((config.window_seconds-1)//2)
    # Subtract nominal phase before fitting to avoid large absolute counters.
    phase = ticks-ticks[0]-np.arange(n)*config.nominal_hz
    width = 2*half+1
    if n >= width:
        x = np.arange(-half, half+1, dtype=float)
        design = np.column_stack([np.ones(width), x, x*x])
        weights = np.linalg.pinv(design)
        frequency[half:n-half] = config.nominal_hz + np.correlate(phase, weights[1], mode="valid")
        residual[half:n-half] = phase[half:n-half]-np.correlate(phase, weights[0], mode="valid")
    for i in list(range(min(half, n))) + list(range(max(half, n-half), n)):
        lo, hi = max(0, i-half), min(n, i+half+1)
        if hi-lo < config.min_points:
            continue
        x = np.arange(lo, hi)-i
        coef = np.linalg.lstsq(np.column_stack([np.ones(len(x)), x, x*x]), phase[lo:hi], rcond=None)[0]
        frequency[i] = config.nominal_hz+coef[1]
        residual[i] = phase[i]-coef[0]
    return frequency, residual


def build_timescale(raw_pcps, config=None):
    config = config or TimescaleConfig()
    d = raw_pcps.reset_index(drop=True)
    if d.empty:
        raise ValueError("PCPS contains no data records")
    seq, edge = _number(d, "seq"), _number(d, "edge_tcb0")
    ticks, segment, delta, step = _unwrap(seq, edge, config.nominal_hz)
    valid = _uint(seq) & _uint(edge)
    for key, modulus in (("cap16", 65536), ("latency16", 65536), ("now32", U32), ("drop_pps", U32)):
        valid &= _uint(_number(d, key), modulus)
    valid &= (_number(d, "now32")-edge) % U32 == _number(d, "latency16")
    locked = _number(d, "gps_status") == 2
    drop = _number(d, "drop_pps")
    normal = np.isfinite(delta) & (abs(delta-config.nominal_hz) <= config.nominal_hz*.05) & (step == 1)
    eligible = normal & valid & np.r_[False, valid[:-1]] & locked & np.r_[False, locked[:-1]] & (np.r_[np.nan, np.diff(drop)] == 0)
    f = pd.DataFrame(dict(ticks=ticks, counter_segment=segment, interval_cycles=delta,
                          seq_step=step, cadence_valid=normal, row_valid=valid, locked=locked,
                          calibration_interval_valid=eligible))
    f["frequency_hz"] = np.nan
    f["phase_residual_cycles"] = np.nan
    runs = []
    starts = np.flatnonzero(eligible & ~np.r_[False, eligible[:-1]])
    ends = np.flatnonzero(eligible & ~np.r_[eligible[1:], False])+1
    for start, end in zip(starts, ends):
        ids = np.arange(start-1, end)
        freq, residual = _phase_fit(ticks[ids], config)
        f.loc[ids, "frequency_hz"] = freq
        f.loc[ids, "phase_residual_cycles"] = residual
        good = np.isfinite(freq) & (freq > 0)
        if good.sum() >= 2:
            ids, freq = ids[good], freq[good]
            x = ticks[ids]
            # Piecewise-linear inverse-frequency integration: no PPS interval
            # is forced to one second; individual pulse jitter remains visible.
            rate = 1/freq
            time = np.r_[0., np.cumsum(np.diff(x)*(rate[:-1]+rate[1:])/2)]
            runs.append((int(segment[start]), x, rate, time))
    return PpsTimescale(f, runs, config)


class PpsTimescale:
    def __init__(self, observations, runs, config):
        self.observations, self.runs, self.config = observations, runs, config
        self.metadata = {**asdict(config), "estimator": "canonical_metrology",
                         "method": "centered local quadratic PPS phase regression",
                         "boundary_policy": "truncate windows; require min_points; no gap bridging",
                         "alignment": "unique maximum complete-swing hardware-range overlap; ties rejected",
                         "calibration_runs": len(runs)}

    def calibrate_ticks(self, raw_edges, seq=None, period_hint_s=2.):
        """Return calibrated edge seconds relative to each swing's first edge."""
        edges = np.asarray(raw_edges, float)
        if edges.ndim != 2 or edges.shape[1] < 2:
            raise ValueError("raw_edges must have shape (records, at least two edges)")
        n, k = edges.shape
        seq = np.arange(n, dtype=float) if seq is None else np.asarray(seq, float)
        completion, local, _, _ = _unwrap(seq, edges[:, -1], self.config.nominal_hz, period_hint_s)
        offsets = (edges[:, -1, None]-edges) % U32
        unwrapped = completion[:, None]-offsets
        valid = _uint(edges).all(axis=1) & (np.diff(unwrapped, axis=1) > 0).all(axis=1) & (offsets[:, 0] < U32/2)
        out = pd.DataFrame({f"edge{j}_s": np.full(n, np.nan) for j in range(k)})
        out["calibration_valid"] = False
        out["frequency_hz"] = np.nan
        out["counter_segment"] = -1
        out["alignment_reason"] = "no_overlap"
        # Alignment uses counter segments, including invalid calibration gaps.
        domains = [(int(seg), g.ticks.min(), g.ticks.max()) for seg, g in self.observations.groupby("counter_segment") if g.ticks.notna().any()]
        for loc in np.unique(local):
            ids = np.flatnonzero((local == loc) & valid)
            if not len(ids):
                continue
            # Completion unwrapping ensures increasing finishes, but malformed
            # long records may put their first edge behind an earlier record.
            # Such chronology cannot support binary-search range alignment.
            starts_in_order = unwrapped[ids, 0]
            monotonic = np.r_[True, starts_in_order[1:] >= np.maximum.accumulate(starts_in_order[:-1])]
            valid[ids[~monotonic]] = False
            ids = ids[monotonic]
            lo, hi = unwrapped[ids, 0].min(), unwrapped[ids, -1].max()
            candidates = []
            starts, finishes = unwrapped[ids, 0], unwrapped[ids, -1]
            for seg, a, b in domains:
                first, last = int(np.ceil((a-hi)/U32)), int(np.floor((b-lo)/U32))
                for shift in range(first, last+1):
                    # Endpoints are monotonic within a continuity segment.
                    left = np.searchsorted(starts, a-shift*U32, side="left")
                    right = np.searchsorted(finishes, b-shift*U32, side="right")
                    count = max(0, right-left)
                    if count:
                        candidates.append((count, seg, shift))
            if not candidates:
                continue
            best = max(c[0] for c in candidates)
            winners = [c for c in candidates if c[0] == best]
            if len(winners) != 1:
                out.loc[ids, "alignment_reason"] = "ambiguous_hardware_overlap"
                continue
            _, seg, shift = winners[0]
            x = unwrapped[ids]+shift*U32
            out.loc[ids, "counter_segment"] = seg
            out.loc[ids, "alignment_reason"] = "outside_calibration_support"
            for run_seg, knots, rate, time in self.runs:
                if run_seg != seg:
                    continue
                covered = (x[:, 0] >= knots[0]) & (x[:, -1] <= knots[-1])
                take, query = ids[covered], x[covered]
                if not len(take):
                    continue
                ix = np.clip(np.searchsorted(knots, query, side="right")-1, 0, len(knots)-2)
                dx = query-knots[ix]
                slope = (rate[ix+1]-rate[ix])/(knots[ix+1]-knots[ix])
                partial = rate[ix]*dx+.5*slope*dx*dx
                # Integrate relative to each swing's first knot, avoiding
                # subtraction of day-sized elapsed seconds for short periods.
                base = ix[:, :1]
                integral = np.zeros_like(query)
                for offset in range(int(np.max(ix-base))):
                    j = np.minimum(base+offset, len(knots)-2)
                    interval = (knots[j+1]-knots[j])*(rate[j]+rate[j+1])/2
                    integral += np.where(ix > base+offset, interval, 0.)
                times = integral+partial-partial[:, :1]
                out.loc[take, [f"edge{j}_s" for j in range(k)]] = times
                out.loc[take, "calibration_valid"] = True
                out.loc[take, "frequency_hz"] = (query[:, -1]-query[:, 0])/(times[:, -1]-times[:, 0])
                out.loc[take, "alignment_reason"] = "calibrated"
        out.loc[~valid, "alignment_reason"] = "invalid_swing_timestamp"
        out["period_s"] = out[f"edge{k-1}_s"]-out.edge0_s
        return out

    def calibrate_swings(self, raw_pcsw):
        out = self.calibrate_ticks(raw_pcsw[[f"edge{i}_tcb0" for i in range(5)]].to_numpy(), seq=raw_pcsw.seq)
        return out.rename(columns={**{f"edge{i}_s": f"calibrated_edge{i}_s" for i in range(5)}, "period_s": "calibrated_period_s"})
