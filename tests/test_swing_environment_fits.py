"""Matched cycle populations and chronological environmental association checks."""
import numpy as np
import pandas as pd
import pytest

from pendulum_analysis.suite.common import Settings
from pendulum_analysis.suite.swing_environment_fits import fit_hourly_environment, swing_environment_fits
from pendulum_analysis.suite.time_series import cycle_series
from test_swing_time_series import frame


def hourly(x, y, epoch=0, channel="temperature_C"):
    n = len(x)
    return pd.DataFrame(dict(epoch=epoch, channel=channel, hour=np.arange(n),
                             cycles=120, reading=x, period_error_us=y,
                             start_s=np.arange(n)*3600, end_s=(np.arange(n)+1)*3600,
                             eligible=True))


def test_known_slope_and_holdout_stay_within_epoch():
    x = np.linspace(10, 30, 48)
    data = pd.concat([hourly(x, 7 + 2*x), hourly(x, 90 - 3*x, epoch=1)], ignore_index=True)
    models, validation = fit_hourly_environment(data, [0, 1])
    selected = models[models.channel.eq("temperature_C")]
    assert selected.slope_us_per_unit.tolist() == pytest.approx([2, -3])
    assert selected.r_squared.tolist() == pytest.approx([1, 1])
    assert selected.cycles.tolist() == [48*120]*2
    check = validation[validation.channel.eq("temperature_C")]
    assert check.train_hours.tolist() == [33, 33]
    assert check.test_hours.tolist() == [15, 15]
    assert check.fit_rmse_us.tolist() == pytest.approx([0, 0], abs=1e-12)
    assert check.constant_rmse_us.gt(0).all()
    assert models.loc[models.channel.ne("temperature_C"), "status"].eq("insufficient hourly coverage").all()


def test_later_behaviour_never_leaks_into_training_fit_or_baseline():
    x = np.arange(48, dtype=float)
    y = 5 + 2*x
    y[33:] += 1000
    models, validation = fit_hourly_environment(hourly(x, y), [0])
    check = validation.iloc[0]
    assert check.slope_us_per_unit == pytest.approx(2)
    assert check.fit_rmse_us == pytest.approx(1000)
    assert check.constant_rmse_us == pytest.approx(np.sqrt(np.mean((y[33:] - y[:33].mean())**2)))
    assert models.iloc[0].slope_us_per_unit > 2


def test_constant_insufficient_and_ineligible_hours_are_explicit():
    data = pd.concat([hourly(np.full(48, 12.3), np.arange(48)),
                      hourly(np.arange(48), np.full(48, 2.3), channel="humidity_pct"),
                      hourly(np.arange(23), np.arange(23), channel="pressure_hPa")], ignore_index=True)
    models, validation = fit_hourly_environment(data, [0])
    assert models.status.tolist() == ["constant sensor readings", "available", "insufficient hourly coverage"]
    assert np.isnan(models.r_squared.iloc[1])
    assert np.isnan(models.slope_us_per_unit.iloc[0])
    assert validation.status.iloc[0] == "constant sensor readings"
    data.loc[data.channel.eq("humidity_pct"), "eligible"] = False
    models, _ = fit_hourly_environment(data, [0])
    assert models.hours.iloc[1] == 0


def test_sensor_screening_and_period_use_identical_complete_groups():
    f = frame(np.arange(3600), np.full(3600, 2.0003))
    f["temperature_C"] = 20 + np.sin(f.elapsed_cycles / 16e6 / 3600)
    f["humidity_pct"] = 50 + np.sin(f.elapsed_cycles / 16e6 / 3600)
    f["pressure_hPa"] = 1000.  # Stale for this whole two-hour run.
    f.loc[:14, "full_pps_s"] = 2.1
    f.loc[0, "temperature_C"] = np.nan
    f.loc[20, "humidity_pct"] = 101
    f.loc[35, "calibrated_valid"] = False
    blocks, _, _ = cycle_series(f, Settings())
    tables = swing_environment_fits(f, blocks, Settings())
    hours = tables["swing_environment_hourly"]
    temp = hours[hours.channel.eq("temperature_C")]
    humidity = hours[hours.channel.eq("humidity_pct")]
    assert temp.cycles.tolist() == [118, 120]
    assert humidity.cycles.tolist() == [118, 120]
    assert temp.period_error_us.tolist() == pytest.approx([300, 300])
    assert humidity.period_error_us.iloc[0] > 300  # Its first cycle remains valid.
    assert not hours.channel.eq("pressure_hPa").any()
    assert tables["swing_environment_fits"].hours.tolist() == [2, 2, 0]


def test_sparse_hours_remain_exported_but_do_not_enter_fits():
    f = frame(np.arange(15*20))
    f["temperature_C"] = np.linspace(20, 21, len(f))
    blocks, _, _ = cycle_series(f, Settings())
    tables = swing_environment_fits(f, blocks, Settings())
    assert tables["swing_environment_hourly"].cycles.tolist() == [20]
    assert not tables["swing_environment_hourly"].eligible.any()
    assert tables["swing_environment_fits"].hours.eq(0).all()
