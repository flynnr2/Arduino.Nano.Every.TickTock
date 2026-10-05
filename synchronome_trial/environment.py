"""Matched-population multivariable fits with chronological prediction checks."""
import numpy as np
import pandas as pd
from .modulation import detrend

SPECS = (
    ("Density", ("density_kg_m3",)),
    ("Temperature + humidity + pressure", ("temperature_C", "humidity_pct", "pressure_hPa")),
    ("Density + speed proxy", ("density_kg_m3", "speed_proxy_pct")),
    ("All three + speed proxy", ("temperature_C", "humidity_pct", "pressure_hPa", "speed_proxy_pct")),
    ("All three + thermal direction", ("temperature_C", "humidity_pct", "pressure_hPa", "thermal_direction")),
    ("All three + temperature rate", ("temperature_C", "humidity_pct", "pressure_hPa", "temperature_rate_C_per_hour")),
    ("All three + speed + temperature rate", ("temperature_C", "humidity_pct", "pressure_hPa", "speed_proxy_pct", "temperature_rate_C_per_hour")),
    ("All three + speed + thermal direction", ("temperature_C", "humidity_pct", "pressure_hPa", "speed_proxy_pct", "thermal_direction")),
)


def hourly(cycles, baseline_seconds=3600):
    parts = []
    for epoch, c in cycles.loc[cycles.available].groupby("epoch"):
        w = c.copy()
        first = c.loc[c.time_s < c.time_s.min()+baseline_seconds]
        reference = float(first.flag_mean_us.mean())
        w["speed_proxy_pct"] = 100*(reference/w.flag_mean_us-1)
        w["hour"] = (w.time_s//3600).astype(int)
        names = ["period_us", "difference_us", "flag_mean_us", "speed_proxy_pct",
                 "temperature_C", "humidity_pct", "pressure_hPa", "density_kg_m3", "time_s", "day"]
        # Joint environmental validity for the whole cycle; all candidate models
        # later use exactly the same hourly population, including speed and direction.
        w = w.loc[w.environment_valid.eq(True)]
        h = w.groupby(["epoch", "hour"]).agg(**{name: (name, "mean") for name in names}, cycles=("cycle", "size")).reset_index()
        h["coverage_s"] = h.cycles*30
        h["eligible"] = h.cycles.ge(60)
        consecutive = h.hour.diff().eq(1) & h.eligible & h.eligible.shift(fill_value=False)
        h["temperature_rate_C_per_hour"] = (h.temperature_C.diff()/(h.time_s.diff()/3600)).where(consecutive)
        h["thermal_direction"] = np.where(h.temperature_rate_C_per_hour.gt(.05), 1., np.where(h.temperature_rate_C_per_hour.lt(-.05), -1., 0.))
        h.loc[~consecutive, "thermal_direction"] = np.nan
        h["direction_label"] = h.thermal_direction.map({1.: "warming", -1.: "cooling", 0.: "approximately steady"}).fillna("unavailable")
        h["reference_flag_us"] = reference
        parts.append(h)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fit_predict(train, test, columns, response="period_us"):
    """Training-only centering/scaling; rank deficiency is reported, not hidden."""
    x = train[list(columns)].to_numpy(float)
    xt = test[list(columns)].to_numpy(float)
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale == 0] = 1
    X = np.column_stack([np.ones(len(train)), (x-mean)/scale])
    Xt = np.column_stack([np.ones(len(test)), (xt-mean)/scale])
    beta, _, rank, _ = np.linalg.lstsq(X, train[response], rcond=None)
    if rank != X.shape[1]:
        return None
    return dict(prediction=Xt@beta, fitted=X@beta, coefficients=dict(zip(columns, beta[1:]/scale)),
                intercept=float(beta[0]-np.dot(beta[1:]/scale, mean)),
                condition_number=float(np.linalg.cond(X)), rank=int(rank))


def models(hourly_data):
    results, predictions, fit_coefficients = [], [], []
    required = sorted(set(name for _, columns in SPECS for name in columns))
    if hourly_data.empty:
        return results, pd.DataFrame(), pd.DataFrame()
    for epoch, data in hourly_data.loc[hourly_data.eligible].groupby("epoch"):
        d = data.dropna(subset=required+["period_us", "difference_us"]).sort_values("time_s")
        split = int(len(d)*.7)
        if split < 24 or len(d)-split < 8:
            results.append(dict(epoch=int(epoch), status="insufficient common hours for 70/30 validation", hours=len(d)))
            continue
        train, test = d.iloc[:split], d.iloc[split:]
        for response in ("period_us", "difference_us"):
            baseline_rms = float(d[response].std(ddof=0))
            constant = np.full(len(test), train[response].mean())
            constant_rmse = float(np.sqrt(np.mean((test[response]-constant)**2)))
            for label, columns in SPECS:
                all_fit = fit_predict(d, d, columns, response)
                held = fit_predict(train, test, columns, response)
                if all_fit is None or held is None:
                    results.append(dict(epoch=int(epoch), response=response, label=label,
                                        status="rank-deficient full or training design", hours=len(d)))
                    continue
                residual = d[response].to_numpy()-all_fit["fitted"]
                rmse = float(np.sqrt(np.mean(residual**2)))
                result = dict(epoch=int(epoch), response=response, label=label, status="available",
                              hours=len(d), train_hours=len(train), test_hours=len(test),
                              fit_rms_us=rmse, r2=1-rmse**2/baseline_rms**2 if baseline_rms else None,
                              test_rmse_us=float(np.sqrt(np.mean((test[response]-held["prediction"])**2))),
                              constant_test_rmse_us=constant_rmse, coefficients=all_fit["coefficients"],
                              intercept_us=all_fit["intercept"], training_intercept_us=held["intercept"],
                              training_coefficients=held["coefficients"], condition_number=all_fit["condition_number"],
                              training_condition_number=held["condition_number"], thermal_direction_counts=d.direction_label.value_counts().to_dict())
                results.append(result)
                for name, coefficient in {"intercept": all_fit["intercept"], **all_fit["coefficients"]}.items():
                    fit_coefficients.append(dict(epoch=int(epoch), response=response, label=label, variable=name,
                        coefficient=coefficient,
                        training_coefficient=held["intercept"] if name == "intercept" else held["coefficients"][name],
                        condition_number=all_fit["condition_number"], training_condition_number=held["condition_number"]))
                p = d[["epoch", "hour", "time_s", "day", "direction_label", response]].copy()
                p["response"] = response
                p["observed_us"] = d[response]
                p["label"] = label
                p["fit_residual_us"] = residual
                p["test_prediction_us"] = np.nan
                p.loc[test.index, "test_prediction_us"] = held["prediction"]
                p["test_constant_us"] = np.nan
                p.loc[test.index, "test_constant_us"] = constant
                p["validation_split"] = np.where(np.arange(len(d)) < split, "earlier training", "later testing")
                # Warn numerically about extrapolation rather than imposing bounds.
                outside = np.zeros(len(test), bool)
                for name in columns:
                    outside |= ~test[name].between(train[name].min(), train[name].max()).to_numpy()
                result["test_outside_training_range_fraction"] = float(outside.mean())
                predictions.append(p.drop(columns=response))
    return results, pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame(), pd.DataFrame(fit_coefficients)
