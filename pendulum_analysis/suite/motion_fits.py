"""Amplitude associations and matched-population environmental comparisons."""
import numpy as np
import pandas as pd
from .common import ENV, environmental_validity, mechanical_cycles
from .environment_models import _solve

AMPLITUDE = "amplitude_squared_rad2"


def fit_motion_hourly(hourly, epochs):
    """Three descriptive models, with train-only chronological validation."""
    models, checks, predictions = [], [], []
    definitions = {"amplitude": [AMPLITUDE], "environment": list(ENV),
                   "environment_amplitude": [*ENV, AMPLITUDE]}
    for epoch in epochs:
        for name, columns in definitions.items():
            source = hourly.loc[hourly.epoch.eq(epoch) & hourly.population.eq(
                "motion" if name == "amplitude" else "shared_environment") & hourly.eligible].sort_values("hour")
            source = source.loc[np.isfinite(source[[*columns, "period_error_us"]]).all(axis=1)]
            n = len(source)
            row = dict(epoch=int(epoch), model=name, hours=n, cycles=int(source.cycles.sum()),
                       slope_us_per_rad2=np.nan, residual_rmse_us=np.nan, r_squared=np.nan,
                       predictor_rank=0, condition_number=np.nan,
                       status="insufficient hourly coverage")
            check = dict(epoch=int(epoch), model=name, train_hours=0, test_hours=0,
                         split_hour=np.nan, model_rmse_us=np.nan, constant_rmse_us=np.nan,
                         status="insufficient holdout coverage")
            if n >= 24:
                x, y = source[columns].to_numpy(float), source.period_error_us.to_numpy(float)
                fit = _solve(x, y)
                residual = y - fit["prediction"]
                total = float(np.sum((y-y.mean())**2))
                row.update(predictor_rank=fit["rank"],
                           condition_number=fit["condition"] if np.isfinite(fit["condition"]) else np.nan,
                           residual_rmse_us=float(np.sqrt(np.mean(residual**2))),
                           r_squared=1-float(residual@residual)/total if total > 0 else np.nan,
                           status="available" if fit["separable"] else "predictors not separable; slopes withheld")
                if fit["separable"] and AMPLITUDE in columns:
                    i = columns.index(AMPLITUDE)
                    row["slope_us_per_rad2"] = float(fit["coef"][i] / fit["scale"][i])
                for i, column in enumerate(columns):
                    row[column + "_slope"] = float(fit["coef"][i] / fit["scale"][i]) if fit["separable"] else np.nan
                predicted = source[["epoch", "hour", "population", "period_error_us", AMPLITUDE]].copy()
                predicted["model"] = name
                predicted["fitted_us"] = fit["prediction"]
                predicted["residual_us"] = residual
                predicted["held_out_prediction_us"] = np.nan
                split = int(n*.7)
                if split >= 24 and n-split >= 12:
                    train = _solve(x[:split], y[:split])
                    estimate = train["reference"] + (x[split:]-train["centre"])/train["scale"] @ train["coef"]
                    check.update(train_hours=split, test_hours=n-split, split_hour=int(source.hour.iloc[split]),
                                 model_rmse_us=float(np.sqrt(np.mean((y[split:]-estimate)**2))),
                                 constant_rmse_us=float(np.sqrt(np.mean((y[split:]-y[:split].mean())**2))),
                                 status="available" if train["separable"] else "training predictors not separable")
                    predicted.iloc[split:, predicted.columns.get_loc("held_out_prediction_us")] = estimate
                predictions.append(predicted)
            models.append(row)
            checks.append(check)
    pred = pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame(
        columns=["epoch", "hour", "population", "period_error_us", AMPLITUDE, "model",
                 "fitted_us", "residual_us", "held_out_prediction_us"])
    return pd.DataFrame(models), pd.DataFrame(checks), pred


def motion_fits(frame, cycles, cfg):
    complete = cycles.loc[cycles.available, ["epoch", "cycle", "time_s", "period_error_us", AMPLITUDE]].copy()
    complete["hour"] = (complete.time_s // 3600).astype(np.int64)
    masks, _, _ = environmental_validity(frame, frame, cfg)
    shared = complete.iloc[:0].copy()
    if all(c in masks for c in ENV):
        work = frame[["epoch", "sequence_extended"]].copy()
        work["cycle"] = mechanical_cycles(frame, cfg)
        for channel in ENV:
            work[channel] = frame[channel].where(masks[channel])
        grouped = work.groupby(["epoch", "cycle"])[list(ENV)].agg(["count", "mean"])
        valid = np.logical_and.reduce([grouped[(c, "count")].eq(15) for c in ENV])
        sensors = grouped.loc[valid, [(c, "mean") for c in ENV]].copy()
        sensors.columns = list(ENV)
        shared = complete.merge(sensors.reset_index(), on=["epoch", "cycle"], validate="one_to_one")
    hourly = []
    for population, source in (("motion", complete), ("shared_environment", shared)):
        agg = dict(cycles=("cycle", "size"), period_error_us=("period_error_us", "mean"),
                   amplitude_squared_rad2=(AMPLITUDE, "mean"))
        agg.update({c: (c, "mean") for c in ENV if c in source})
        hours = source.groupby(["epoch", "hour"]).agg(**agg).reset_index()
        hours["eligible"] = hours.cycles * 15 * cfg.swing_period_s >= 1800
        hours["population"] = population
        for channel in ENV:
            if channel not in hours:
                hours[channel] = np.nan
        hourly.append(hours)
    hourly = pd.concat(hourly, ignore_index=True)
    fits, validation, predictions = fit_motion_hourly(hourly, sorted(frame.epoch.unique()))
    return dict(swing_motion_hourly=hourly, swing_motion_fits=fits,
                swing_motion_validation=validation, swing_motion_predictions=predictions)
