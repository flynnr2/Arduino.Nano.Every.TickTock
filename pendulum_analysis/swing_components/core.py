"""Dataset-local swing diagnostics, independent of report/comparison layout."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy import signal


def contiguous_runs(seq, epoch, step=1, valid=None, source_row=None):
    seq, epoch = np.asarray(seq), np.asarray(epoch)
    n = len(seq)
    if not n:
        return []
    valid = np.ones(n, bool) if valid is None else np.asarray(valid, bool)
    joins = (np.diff(seq) == step) & (np.diff(epoch) == 0)
    if source_row is not None:
        joins &= np.diff(source_row) == step
    starts = np.r_[True, ~joins] | ~valid | ~np.r_[False, valid[:-1]]
    boundaries = np.r_[np.flatnonzero(starts), n]
    return [slice(a, b) for a, b in zip(boundaries[:-1], boundaries[1:]) if valid[a] and b > a]


@dataclass
class Series:
    t: np.ndarray
    mean: np.ndarray
    seq: np.ndarray
    run: np.ndarray
    range: np.ndarray
    count: np.ndarray
    low: np.ndarray
    high: np.ndarray
    sd: np.ndarray
    nominal_s: float = 2.

    def frame(self):
        return pd.DataFrame(dict(end_seq=self.seq, elapsed_end_s=self.t,
            valid_run=self.run, mean_period_minus_nominal_us=self.mean,
            mean_period_s=self.nominal_s+self.mean/1e6,
            frequency_offset_ppm=(self.nominal_s/(self.nominal_s+self.mean/1e6)-1.)*1e6,
            trailing_range_us=self.range, trailing_min_us=self.low,
            trailing_max_us=self.high, trailing_sd_us=self.sd, points_in_range_window=self.count,
            range_available=np.isfinite(self.range)))


def local_range(t, values, runs, seconds=120.):
    """Exact (t-seconds,t] range; require fresh uninterrupted elapsed support."""
    n = len(t)
    low, high = np.full(n, np.nan), np.full(n, np.nan)
    sd = np.full(n, np.nan)
    count = np.zeros(n, dtype=np.int32)
    run_id = np.full(n, -1, dtype=np.int32)
    for rid, sl in enumerate(runs):
        tt, yy = t[sl], values[sl]
        if np.any(np.diff(tt) <= 0) or not np.isfinite(yy).all():
            raise ValueError('Range inputs must be finite and chronological within each run')
        r = pd.Series(yy, index=pd.to_timedelta(tt, unit='s')).rolling(
            pd.Timedelta(seconds=seconds), closed='right')
        good = tt-tt[0] >= seconds
        low[sl] = np.where(good, r.min().to_numpy(), np.nan)
        high[sl] = np.where(good, r.max().to_numpy(), np.nan)
        sd[sl] = np.where(good, r.std(ddof=0).to_numpy(), np.nan)
        count[sl] = r.count().to_numpy(np.int32)
        run_id[sl] = rid
    return high-low, count, low, high, run_id, sd


def rolling(events, outer_seconds=120.):
    valid = events['valid'] & np.isfinite(events['error_us']) & np.isfinite(events['end_s'])
    source_runs = contiguous_runs(events['sequence'], events['epoch'], valid=valid,
                                  source_row=events['source_row'])
    indices, means = [], []
    for sl in source_runs:
        if sl.stop-sl.start < 15:
            continue
        # Pandas' compensated rolling sum avoids long-run cumulative cancellation.
        yy = pd.Series(events['error_us'][sl]).rolling(15, min_periods=15).mean().to_numpy()[14:]
        ix = np.arange(sl.start+14, sl.stop)
        indices.append(ix); means.append(yy)
    if not indices:
        raise ValueError('No eligible consecutive 15-swing support')
    ix, mean = np.concatenate(indices), np.concatenate(means)
    t, seq, epoch = events['end_s'][ix], events['sequence'][ix], events['epoch'][ix]
    if len(np.unique(epoch)) != 1:
        raise ValueError('Separate epoch reports are required; never pool resets')
    if not np.all(np.diff(t) > 0):
        raise ValueError('Eligible rolling endpoints must be chronological')
    runs = contiguous_runs(seq, epoch)
    rr, cc, ll, hh, rid, sd = local_range(t, mean, runs, outer_seconds)
    nominal=events.get('nominal_s',2.)
    dense = Series(t, mean, seq, rid, rr, cc, ll, hh, sd,nominal)
    anchor = events['phase'][ix] == 14
    st, sy, sq, se = t[anchor], mean[anchor], seq[anchor], epoch[anchor]
    sparse_runs = contiguous_runs(sq, se, step=15)
    rr, cc, ll, hh, rid, sd = local_range(st, sy, sparse_runs, outer_seconds)
    sparse = Series(st, sy, sq, rid, rr, cc, ll, hh, sd,nominal)
    return dense, sparse, dict(valid_source_periods=int(valid.sum()),
        source_records=len(valid), unavailable_dense_endpoints=int(len(valid)-len(mean)),
        source_valid_runs=len(source_runs), dense_valid_runs=len(runs),
        sparse_valid_runs=len(sparse_runs), inner_swings=15,
        outer_seconds=outer_seconds, interval='(t-outer_seconds,t]',
        inner_update='Every eligible swing; adjacent means share 14 source swings',
        sparse_update='Only complete phase-0..14 supports, endpoint phase 14',
        local_sd='Population standard deviation (ddof=0) about the local mean in the same trailing window; no drift removal; descriptive scatter, not standard error',
        gap_policy='Never bridge missing sequences, exclusions, source gaps or epochs')


def cycles(events):
    starts = np.flatnonzero(events['phase'] == 0)
    starts = starts[starts+14 < len(events['phase'])]
    ix = starts[:, None]+np.arange(15)
    valid = events['valid'] & np.isfinite(events['error_us'])
    good = (valid[ix].all(axis=1)
        & (events['sequence'][ix] == events['sequence'][starts, None]+np.arange(15)).all(axis=1)
        & (events['phase'][ix] == np.arange(15)).all(axis=1)
        & (events['epoch'][ix] == events['epoch'][starts, None]).all(axis=1)
        & (events['source_row'][ix] == events['source_row'][starts, None]+np.arange(15)).all(axis=1))
    ix = ix[good]
    if not len(ix):
        raise ValueError('No complete phase-0..14 cycles')
    epoch = events['epoch'][ix[:, 0]]
    if len(np.unique(epoch)) != 1:
        raise ValueError('Separate epoch reports are required')
    a = events['error_us'][ix]
    nominal=events.get('nominal_s',2.)
    t = (events['start_s'][ix[:, 0]]+events['start_s'][ix[:, -1]])/2.+nominal/2.
    seq = events['sequence'][ix[:, 0]]
    return dict(phases=a, mean=a.mean(axis=1), total=a.sum(axis=1), t=t, seq=seq,
                epoch=epoch, runs=contiguous_runs(seq, epoch, step=15),nominal_s=nominal,
                source_first=events['source_row'][ix[:,0]],source_last=events['source_row'][ix[:,-1]])


def stability(cycle):
    """Overlapping ADEV/HDEV of nonoverlapping complete-cycle frequency."""
    nominal=cycle.get('nominal_s',2.)
    y = nominal/(nominal+cycle['mean']/1e6)-1.
    runs = cycle['runs']
    longest = max(sl.stop-sl.start for sl in runs)
    joins = np.diff(cycle['seq']) == 15
    dt = float(np.median(np.diff(cycle['t'])[joins])) if joins.any() else 15*nominal
    if longest < 3:
        return pd.DataFrame(), {'status': 'Fewer than three consecutive cycles', 'cadence_s': dt}
    sizes = np.unique(np.geomspace(1, max(1, longest//3), 42).astype(int))
    rows = []
    for m in sizes:
        aa, hh, na, nh, disjoint_a, disjoint_h = 0., 0., 0, 0, 0, 0
        for sl in runs:
            a = y[sl]
            sums = np.r_[0., np.cumsum(a)]
            means = (sums[m:]-sums[:-m])/m
            if len(means) > m:
                z = means[m:]-means[:-m]; aa += float(z@z); na += len(z)
            if len(means) > 2*m:
                z = means[2*m:]-2*means[m:-m]+means[:-2*m]; hh += float(z@z); nh += len(z)
            disjoint_a += len(a)//(2*m)
            disjoint_h += len(a)//(3*m)
        if na and nh:
            rows.append(dict(averaging_cycles=int(m), tau_s=float(m*dt),
                adev_ppm=float(np.sqrt(aa/na/2)*1e6), hdev_ppm=float(np.sqrt(hh/nh/6)*1e6),
                allan_pairs=na, hadamard_triples=nh, disjoint_allan_spans=disjoint_a,
                disjoint_hadamard_spans=disjoint_h,
                low_support=disjoint_h < 10))
    return pd.DataFrame(rows), dict(status='available', cadence_s=dt,
        frequency_definition=f'{15*nominal:g} / actual complete-cycle duration - 1',
        drift_removal='None; Hadamard intrinsically rejects linear frequency drift',
        gap_policy='All block differences are confined to contiguous eligible cycles',
        low_support_definition='Fewer than 10 disjoint three-block spans; visual flag, not confidence interval',
        max_averaging_cycles=int(max(sizes)))


def pca(cycle):
    a = cycle['phases']
    z = a-a.mean(axis=0)
    if len(z) < 2 or not np.any(z):
        return None
    _, singular, v = np.linalg.svd(z, full_matrices=False)
    for k in range(len(v)):
        sign = np.sign(v[k, np.argmax(abs(v[k]))]) or 1.
        v[k] *= sign
    fraction = singular**2/(singular**2).sum()
    scores = z@v[:3].T
    residual = z-scores@v[:3]
    return dict(fraction=fraction, loadings=v, scores=scores,
                phase_means_us=a.mean(axis=0), phase_residual_sd_us=residual.std(axis=0),
                method='Covariance PCA in unscaled microseconds; only per-phase mean centring',
                sign='Largest-magnitude loading made positive; component identity is dataset-local',
                total_variance_us2=float(np.var(z, axis=0).sum()))


def spectra(cycle):
    joins = np.diff(cycle['seq']) == 15
    if not joins.any():
        return [], {'status': 'No consecutive complete cycles'}
    cadence = np.diff(cycle['t'])[joins]
    dt = float(np.median(cadence))
    longest = max(sl.stop-sl.start for sl in cycle['runs'])
    maximum = 2**int(np.floor(np.log2(longest)))
    short = min(1024, maximum)
    if short < 32:
        return [], {'status': 'Fewer than 32 consecutive complete cycles'}
    choices = [short]+([8192] if longest >= 8192 and short != 8192 else [])
    results = []
    for size in choices:
        all_power, weights, covered = [], [], 0
        for sl in cycle['runs']:
            n = sl.stop-sl.start
            if n < size:
                continue
            freq, power = signal.welch(cycle['mean'][sl], fs=1./dt, window='hann',
                nperseg=size, noverlap=size//2, detrend='linear', scaling='density')
            # Pool by the number of Welch segments, rather than giving short runs excess weight.
            count = 1+(n-size)//(size-size//2)
            all_power.append(power); weights.append(count); covered += n
        power = np.average(all_power, axis=0, weights=weights)
        results.append(dict(frequency_hz=freq, psd_us2_per_hz=power,
            segment_cycles=size, segment_seconds=size*dt, segments=int(sum(weights)),
            covered_cycles=int(covered), coverage_fraction=float(covered/len(cycle['mean'])),
            nyquist_period_s=2*dt, lowest_nonzero_frequency_hz=float(freq[1])))
    return results, dict(status='available', signal='Mean full-period deviation of complete nonoverlapping cycles',
        units='µs²/Hz', method='Welch; Hann window; 50% overlap; linear detrending per segment',
        pooling='Weighted by actual segment count; no unit-power normalisation',
        gaps='No interpolation; only contiguous runs with enough cycles contribute',
        cadence_s=dt, max_cadence_departure_s=float(np.max(abs(cadence-dt))),
        approximation='Cadence treated as uniform at the measured median; actual departures disclosed',
        interpretation='Exploratory spectrum, not an automatic list of physical oscillators')


def window_summary(t, values):
    t, values = np.asarray(t), np.asarray(values)
    good = np.isfinite(t) & np.isfinite(values)
    t, y = t[good], values[good]
    if not len(y):
        return dict(points=0)
    result = dict(points=len(y), mean_us=float(y.mean()), peak_to_peak_us=float(np.ptp(y)),
                  rms_about_mean_us=float(y.std()))
    if len(y) >= 3 and np.ptp(t) > 0:
        slope, intercept = np.polyfit(t-t.mean(), y, 1)
        result.update(linear_slope_us_per_hour=float(slope*3600),
            linear_detrended_rms_us=float(np.std(y-(slope*(t-t.mean())+intercept))))
    return result
