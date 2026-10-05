"""PPS report characteristics using the capture analyzer's existing populations."""
import numpy as np
import pandas as pd


def characteristic(metric, values):
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a)]
    if not len(a):
        return dict(metric=metric, n=0, median=None, robust_spread=None,
                    p05=None, p95=None, min=None, max=None, note="unavailable")
    median = float(np.median(a))
    spread = float(1.4826*np.median(np.abs(a-median)))
    return dict(metric=metric, n=len(a), median=median, robust_spread=spread,
                p05=float(np.quantile(a, .05)), p95=float(np.quantile(a, .95)),
                min=float(a.min()), max=float(a.max()),
                note="robust sigma" + ("; robust spread is zero; see discrete counts and quantiles" if spread == 0 else ""))


def add_summary_tables(result):
    f, cfg, s = result.frame, result.config, result.summary
    valid = f.frequency_valid
    raw = f.frequency_offset_cycles.where(valid)
    # A causal previous-window median. Exclusions restart the warm-up, so
    # dropped rows or unlocked periods never become adjacent reference samples.
    runs = ((~valid) | f.segment.ne(f.segment.shift())).cumsum()
    previous = pd.Series(np.nan, index=f.index)
    if valid.any():
        previous.loc[valid] = raw.loc[valid].groupby(runs[valid], sort=False).transform(
            lambda values: values.shift().rolling(31, min_periods=10).median())
    f["offline_expected_error_cycles_prev31"] = previous
    f["offline_pps_adjusted_residual_cycles"] = (raw-previous).where(valid)
    series = {}
    for name, values in (("pps_raw_error", raw),
                         ("offline_pps_adjusted_residual", f.offline_pps_adjusted_residual_cycles)):
        for unit, factor in (("cycles", 1), ("ns", 1e9/cfg.nominal_hz), ("ppm", 1e6/cfg.nominal_hz)):
            series[f"PCPS_{name}_{unit}"] = values*factor
    series["PCPS_latency16_valid"] = f.loc[valid, "latency16"]
    series["PCPS_cap16_valid"] = f.loc[valid, "cap16"]
    result.tables["pps_characteristics"] = pd.DataFrame([characteristic(k, v) for k,v in series.items()])
    discrete = []
    for name in ("PCPS_pps_raw_error_cycles", "PCPS_offline_pps_adjusted_residual_cycles", "PCPS_latency16_valid"):
        counts = series[name].dropna().value_counts().sort_index()
        discrete.append(pd.DataFrame(dict(metric=name, value=counts.index, records=counts.to_numpy())))
    result.tables["pps_discrete_counts"] = pd.concat(discrete, ignore_index=True)
    s["pps_characteristics"] = result.tables["pps_characteristics"].to_dict("records")
    s["offline_residual_method"] = "Raw interval error minus median of previous up to 31 consecutive eligible intervals; at least 10 preceding intervals; restart after any exclusion or segment boundary. Separate from timer-projection normalization."

    def metrics(rows):
        return pd.DataFrame(rows, columns=["metric", "value", "unit"])

    result.tables["pps_data_quality"] = metrics([
        ("Recorded captures", len(f), "records"),
        ("Valid required fields", int(f.row_valid.sum()), "records"),
        ("Malformed records", s["malformed_records"], "records"),
        ("Candidate intervals after first row", len(f)-1, "intervals"),
        ("Eligible one-second frequency intervals", int(valid.sum()), "intervals"),
        ("Excluded candidate intervals", len(f)-1-int(valid.sum()), "intervals"),
        ("Offline residual available", int(f.offline_pps_adjusted_residual_cycles.notna().sum()), "intervals"),
        ("Sequence gaps", s["event_counts"]["sequence_gap"], "events"),
        ("Missing forward sequence records", s["missing_sequence_records"], "records"),
        ("Timeline segments", s["segments"], "segments"),
        ("Observed duration", s["observed_duration_days"]*24, "hours"),
    ])
    state = f.get("gps_status", pd.Series(np.nan, index=f.index))
    state_valid = f.row_valid & state.isin([0, 1, 2, 3])
    count = int(state_valid.sum())
    states = [(str(k), label, int((state_valid & state.eq(k)).sum()))
              for k,label in enumerate(("Free run", "Acquiring", "Locked", "Holdover"))]
    states.append(("unavailable", "Missing/invalid state or capture", len(f)-count))
    result.tables["pps_gps_states"] = pd.DataFrame([
        dict(gps_status=k, state=label, records=n, percent_all_records=n/len(f)*100)
        for k,label,n in states])
    age = f.get("holdover_age_ms", pd.Series(np.nan, index=f.index))
    age_valid = f.row_valid & np.isfinite(age) & age.ge(0) & age.lt(2**32) & age.eq(np.floor(age))
    pair = state_valid & state_valid.shift(fill_value=False) & f.segment.eq(f.segment.shift()) & f.seq_delta.eq(1)
    result.tables["pps_timebase_health"] = metrics([
        ("Locked fraction of valid state records", 100*int((state_valid & state.eq(2)).sum())/count if count else None, "%"),
        ("Lock/unlock transitions across consecutive valid states", int((pair & state.eq(2).ne(state.shift().eq(2))).sum()) if pair.any() else None, "transitions"),
        ("Positive holdover age", int((age_valid & age.gt(0)).sum()) if age_valid.any() else None, "records"),
        ("Maximum holdover age", age[age_valid].max() if age_valid.any() else None, "ms"),
    ])
    result.tables["pps_latency_summary"] = pd.DataFrame([
        characteristic("latency16_all_valid_captures_cycles", f.loc[f.row_valid, "latency16"]),
        characteristic("latency16_all_valid_captures_us", f.loc[f.row_valid, "latency16"]*1e6/cfg.nominal_hz)])
    phase = f.loc[f.row_valid, "cap16"].to_numpy(dtype=int)
    unique = np.unique(phase)
    counts = np.bincount(phase//256, minlength=256)
    expected = len(phase)/256
    result.tables["pps_cap16_bins"] = pd.DataFrame(dict(cap16_start=np.arange(256)*256,
        cap16_end=np.arange(256)*256+255, records=counts))
    result.tables["pps_cap16_coverage"] = metrics([
        ("Valid captures", len(phase), "records"),
        ("Unique cap16 values", len(unique), "values"),
        ("Unique phase coverage", len(unique)/65536*100 if len(phase) else None, "%"),
        ("Largest circular empty gap", int(np.diff(np.r_[unique, unique[0]+65536]).max()-1) if len(phase) else None, "ticks"),
        ("Phase bins", 256, "bins"),
        ("Expected records per bin", expected if len(phase) else None, "records"),
        ("Maximum bin / expected", counts.max()/expected if len(phase) else None, "ratio"),
        ("Minimum bin / expected", counts.min()/expected if len(phase) else None, "ratio"),
    ])
