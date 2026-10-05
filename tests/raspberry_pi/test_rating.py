import math
from dataclasses import replace

import pytest

from pendulum_pi.config import Settings, update_settings
from pendulum_pi.display import DisplayEstimator
from pendulum_pi.rating import gain_seconds_per_day, rated_display


def test_rate_sign_units_and_target():
    assert gain_seconds_per_day(2, 2) == 0
    assert gain_seconds_per_day(1.999, 2) == pytest.approx(43.2216108054)
    assert gain_seconds_per_day(2.001, 2) == pytest.approx(-43.1784107946)
    for invalid in (None, 0, -1, math.nan, math.inf, True, '2'):
        assert gain_seconds_per_day(invalid, 2) is None
        assert gain_seconds_per_day(2, invalid) is None


def test_target_validation_and_old_config_defaults():
    assert Settings().target_period_s is None
    for invalid in (0, -2, True, '2', math.inf, math.nan, 121):
        with pytest.raises(ValueError):
            update_settings(Settings(), {'target_period_s': invalid})
    assert update_settings(Settings(), {'target_period_s': 2}).target_period_s == 2
    assert update_settings(replace(Settings(), target_period_s=2), {'target_period_s': None}).target_period_s is None


def test_rate_uses_existing_estimate_and_does_not_mutate_it():
    estimator = DisplayEstimator()
    estimator.window.observe((900_000, 99_561.5, 900_000, 99_438.5))
    estimator.last_swing_at = 100
    original = estimator.snapshot(101)
    result = rated_display(original, 2)
    assert result['window']['gain_seconds_per_day'] > 0
    assert result['window']['bpm'] == original['window']['bpm']
    assert original == estimator.snapshot(101)
    assert 'gain_seconds_per_day' not in original['window']
    assert rated_display(estimator.snapshot(111), 2)['window']['gain_seconds_per_day'] is None
