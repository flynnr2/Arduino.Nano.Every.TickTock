"""Cycle alternation, gap-aware correlations and a declared spectral search band."""
import numpy as np
import pandas as pd

VARIABLES = ("period_us", "difference_us", "flag_mean_us")


def detrend(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    dx = x-x.mean()
    denom = np.dot(dx, dx)
    return y-y.mean()-(np.dot(dx, y-y.mean())/denom*dx if denom else 0)


def investigate(cycles, max_lag=120):
    correlations, pairs, folded, spectra, metadata = [], [], [], [], []
    for epoch, e in cycles.loc[cycles.available].groupby("epoch"):
        runs = [g.sort_values("cycle") for _, g in e.groupby("series_run") if len(g) >= 3]
        if not runs:
            continue
        longest = max(runs, key=len)
        spacing = float(np.median(np.diff(longest.time_s)))
        for name in VARIABLES:
            residual = [detrend(g.time_s, g[name]) for g in runs]
            for mode in ("linear drift removed", "successive differences"):
                values = residual if mode == "linear drift removed" else [np.diff(a) for a in residual]
                if mode == "successive differences":
                    values = [a-a.mean() for a in values]
                denom = sum(float(np.dot(a, a)) for a in values)
                for lag in range(1, max_lag+1):
                    count = sum(max(0, len(a)-lag) for a in values)
                    if count < 20:
                        break
                    numerator = sum(float(np.dot(a[:-lag], a[lag:])) for a in values if len(a) > lag)
                    correlations.append(dict(epoch=int(epoch), metric=name, mode=mode, lag_cycles=lag,
                                             lag_s=lag*spacing, pairs=count, correlation=numerator/denom if denom else np.nan))
            for g, y in zip(runs, residual):
                work = g[["epoch", "cycle", "time_s"]].copy()
                work["value"] = y
                work["pair"] = work.cycle//2
                for pair, two in work.groupby("pair"):
                    if len(two) == 2 and two.cycle.iloc[1]-two.cycle.iloc[0] == 1:
                        pairs.append(dict(epoch=int(epoch), metric=name, pair=int(pair), time_s=two.time_s.mean(),
                                          odd_minus_even=two.value.iloc[1]-two.value.iloc[0]))
                work["position20"] = work.cycle%20
                work["metric"] = name
                folded.append(work)
            if len(longest) >= 64:
                y = detrend(longest.time_s, longest[name])
                window = np.hanning(len(y))
                frequency = np.fft.rfftfreq(len(y), spacing)[1:]
                power = abs(np.fft.rfft(y*window))[1:]**2
                total = power.sum()
                fraction = power/total if total else np.full_like(power, np.nan)
                period = 1/frequency
                spec = pd.DataFrame(dict(epoch=int(epoch), metric=name, frequency_hz=frequency,
                                         period_s=period, power_fraction=fraction))
                spectra.append(spec)
                band = spec.loc[spec.period_s.between(120, 1800)].dropna()
                peak = band.loc[band.power_fraction.idxmax()] if len(band) else None
                metadata.append(dict(epoch=int(epoch), metric=name, cycles=len(longest),
                                     start_s=float(longest.time_s.min()), end_s=float(longest.time_s.max()),
                                     nominal_sample_spacing_s=spacing, declared_peak_band_s=[120, 1800],
                                     peak_period_s=float(peak.period_s) if peak is not None else None,
                                     peak_bin_fraction=float(peak.power_fraction) if peak is not None else None,
                                     status="descriptive spectrum of longest uninterrupted run; no significance claim"))
    acf = pd.DataFrame(correlations)
    pair_table = pd.DataFrame(pairs)
    fold = pd.concat(folded, ignore_index=True).groupby(["epoch", "metric", "position20"]).value.agg(["count", "mean", "std"]).reset_index() if folded else pd.DataFrame()
    spectrum = pd.concat(spectra, ignore_index=True) if spectra else pd.DataFrame()
    return acf, pair_table, fold, spectrum, metadata
