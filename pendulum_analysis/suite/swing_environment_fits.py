"""Descriptive environmental associations for complete calibrated swing cycles."""
import numpy as np
import pandas as pd

from pendulum_analysis.air_density import density_array

from .common import ENV, environmental_validity, mechanical_cycles


def fit_hourly_environment(hourly, epochs, min_hours=24):
    """Separate single-sensor OLS fits; validate on later hours within each epoch.

    No independent-sample p-values are attached to these correlated time series.
    The 70/30 holdout fits only earlier data, including its constant baseline.
    """
    models, validation = [], []
    for epoch in epochs:
        for channel in ENV:
            data = hourly.loc[(hourly.epoch == epoch) & (hourly.channel == channel)
                              & hourly.eligible].sort_values("hour")
            x = data.reading.to_numpy(float)
            y = data.period_error_us.to_numpy(float)
            n = len(data)
            row = dict(epoch=int(epoch), channel=channel, hours=n,
                       cycles=int(data.cycles.sum()),
                       start_day=float(data.start_s.min() / 86400) if n else np.nan,
                       end_day=float(data.end_s.max() / 86400) if n else np.nan,
                       sensor_min=float(x.min()) if n else np.nan,
                       sensor_max=float(x.max()) if n else np.nan,
                       reference_reading=float(x.mean()) if n else np.nan,
                       period_at_reference_us=float(y.mean()) if n else np.nan,
                       slope_us_per_unit=np.nan, r_squared=np.nan,
                       residual_rmse_us=np.nan, status="insufficient hourly coverage")
            check = dict(epoch=int(epoch), channel=channel, train_hours=0, test_hours=0,
                         split_hour=np.nan, slope_us_per_unit=np.nan,
                         fit_rmse_us=np.nan, constant_rmse_us=np.nan,
                         improvement_fraction=np.nan, status="insufficient holdout coverage")
            if n >= min_hours:
                dx, dy = x - x.mean(), y - y.mean()
                if np.ptp(x) == 0:
                    row["status"] = "constant sensor readings"
                    check["status"] = "constant sensor readings"
                else:
                    slope = float(np.dot(dx, dy) / np.dot(dx, dx))
                    residual = dy - slope * dx
                    total = float(np.dot(dy, dy))
                    row.update(slope_us_per_unit=slope,
                               r_squared=1 - float(np.dot(residual, residual)) / total if np.ptp(y) > 0 else np.nan,
                               residual_rmse_us=float(np.sqrt(np.mean(residual**2))), status="available")
                    split = int(n * .7)
                    if split >= min_hours and n - split >= 12:
                        train_x, train_y = x[:split], y[:split]
                        centred = train_x - train_x.mean()
                        check.update(train_hours=split, test_hours=n-split,
                                     split_hour=int(data.hour.iloc[split]))
                        if np.ptp(train_x) == 0:
                            check["status"] = "constant training sensor readings"
                        else:
                            slope = float(np.dot(centred, train_y - train_y.mean()) / np.dot(centred, centred))
                            prediction = train_y.mean() + slope * (x[split:] - train_x.mean())
                            rmse = float(np.sqrt(np.mean((y[split:] - prediction)**2)))
                            baseline = float(np.sqrt(np.mean((y[split:] - train_y.mean())**2)))
                            check.update(slope_us_per_unit=slope, fit_rmse_us=rmse,
                                         constant_rmse_us=baseline,
                                         improvement_fraction=1-rmse/baseline if baseline > 0 else np.nan,
                                         status="available")
            models.append(row)
            validation.append(check)
    return pd.DataFrame(models), pd.DataFrame(validation)


def swing_environment_fits(frame, blocks, cfg):
    """Pair sensors and periods on identical complete cycles, then average hourly."""
    masks, _, _ = environmental_validity(frame, frame, cfg)
    complete = blocks.loc[blocks.available, ["epoch", "cycle", "time_s", "start_s",
                                           "last_start_s", "period_error_us"]].copy()
    complete["hour"] = (complete.time_s // 3600).astype(np.int64)
    complete["end_s"] = complete.last_start_s + cfg.swing_period_s
    work = frame[["epoch", "sequence_extended"]].copy()
    work = work.loc[frame.phase15.ge(0) & np.isfinite(frame.sequence_extended)]
    work["cycle"] = mechanical_cycles(frame, cfg).loc[work.index].astype(np.int64)
    for channel in masks:
        work[channel] = frame[channel].where(masks[channel])
    work = work.merge(complete[["epoch", "cycle"]], on=["epoch", "cycle"], how="inner",
                      validate="many_to_one")
    hours = []
    for channel in ENV:
        if channel not in work:
            continue
        sensors = work.groupby(["epoch", "cycle"])[channel].agg(["count", "mean"]).reset_index()
        # All 15 readings must be screened-valid, not just a subset of the cycle.
        paired = complete.merge(sensors.loc[sensors["count"].eq(15)],
                                on=["epoch", "cycle"], validate="one_to_one")
        hourly = paired.groupby(["epoch", "hour"]).agg(
            cycles=("cycle", "size"), reading=("mean", "mean"),
            period_error_us=("period_error_us", "mean"), start_s=("start_s", "min"),
            end_s=("end_s", "max")).reset_index()
        hourly["channel"] = channel
        # Half an hour of complete cycles at the configured nominal swing period.
        hourly["eligible"] = hourly.cycles * 15 * cfg.swing_period_s >= 1800
        hours.append(hourly)
    hourly = pd.concat(hours, ignore_index=True) if hours else pd.DataFrame(
        columns=["epoch", "hour", "cycles", "reading", "period_error_us", "start_s", "end_s", "channel", "eligible"])
    models, validation = fit_hourly_environment(hourly, sorted(frame.epoch.unique()))
    joint=pd.DataFrame(columns=['epoch','hour',*ENV,'samples','response','eligible','start_s','end_s'])
    if all(c in work for c in ENV):
        for name, legacy in (('density_kg_m3', False), ('density_legacy_kg_m3', True)):
            work[name] = density_array(work.temperature_C, work.humidity_pct, work.pressure_hPa, legacy=legacy)
        density_columns = ['density_kg_m3', 'density_legacy_kg_m3']
        grouped=work.groupby(['epoch','cycle'])[[*ENV, *density_columns]].agg(['count','mean'])
        good=np.logical_and.reduce([grouped[(c,'count')].eq(15) for c in ENV])
        sensors=grouped.loc[good,[(c,'mean') for c in ENV]].copy()
        sensors.columns=list(ENV)
        for name in density_columns:
            sensors[name] = grouped.loc[good, (name,'mean')].where(grouped.loc[good, (name,'count')].eq(15))
        sensors=sensors.reset_index()
        paired=complete.merge(sensors,on=['epoch','cycle'],validate='one_to_one')
        if len(paired):
            joint=paired.groupby(['epoch','hour']).agg(samples=('cycle','size'),response=('period_error_us','mean'),
                start_s=('start_s','min'),end_s=('end_s','max'),**{c:(c,'mean') for c in (*ENV, *density_columns)}).reset_index()
            for name in density_columns:
                counts = paired.groupby(['epoch','hour'])[name].count().to_numpy()
                joint.loc[counts != joint.samples.to_numpy(), name] = np.nan
            joint['eligible']=joint.samples*15*cfg.swing_period_s>=1800
    from .environment_models import fit_environment
    expanded,checks,predictions=fit_environment(hourly.rename(columns={'period_error_us':'response','cycles':'samples'}),
        joint,sorted(frame.epoch.unique()))
    return dict(swing_environment_hourly=hourly, swing_environment_fits=models,
                swing_environment_validation=validation,swing_environment_joint_hourly=joint,
                swing_environment_all_fits=expanded,swing_environment_all_validation=checks,
                swing_environment_predictions=predictions)
