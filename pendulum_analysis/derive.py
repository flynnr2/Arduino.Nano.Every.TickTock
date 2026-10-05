"""Derived timing fields."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import AnalysisConfig
from .schema import DERIVED, INPUT


def add_derived_fields(data: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    result = data.copy()
    # Raw durations use the declared nominal counter rate. PPS calibration is
    # calculated separately from captured PPS boundaries by add_pps_calibration.
    result[DERIVED.f_used_hz] = config.nominal_hz

    result[DERIVED.tick_half_cycles] = result[INPUT.tick_block] + result[INPUT.tick]
    result[DERIVED.tock_half_cycles] = result[INPUT.tock_block] + result[INPUT.tock]
    result[DERIVED.period_cycles] = result[DERIVED.tick_half_cycles] + result[DERIVED.tock_half_cycles]
    result[DERIVED.A_half_cycles] = result[DERIVED.tick_half_cycles] - result[DERIVED.tock_half_cycles]
    result[DERIVED.period_s] = result[DERIVED.period_cycles] / result[DERIVED.f_used_hz]
    result[DERIVED.rate_ppm] = (result[DERIVED.period_s] / config.target_period_s - 1.0) * 1_000_000.0
    result[DERIVED.sec_per_day] = result[DERIVED.rate_ppm] * 86_400.0 / 1_000_000.0
    result[DERIVED.min_per_day] = result[DERIVED.sec_per_day] / 60.0
    result[DERIVED.A_half_ms] = result[DERIVED.A_half_cycles] / result[DERIVED.f_used_hz] * 1000.0
    return result



def add_pps_calibration(data: pd.DataFrame, pcps: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    """Add offline metrology while preserving every raw counter/nominal field."""
    edges = [f"edge{i}_tcb0" for i in range(5)]
    if not all(c in data for c in edges):
        return data
    from .pps.timescale import TimescaleConfig, build_timescale
    result = data.copy()
    if pcps.empty:
        calibration = pd.DataFrame({**{f"edge{i}_s": np.nan for i in range(5)},
                                    "period_s": np.nan, "frequency_hz": np.nan,
                                    "calibration_valid": False, "counter_segment": -1,
                                    "alignment_reason": "no_pps_data"}, index=data.index)
        result.attrs["pps_timescale"] = {"status": "unavailable", "reason": "no PPS data",
                                       "estimator": "canonical_metrology", "window_seconds": config.pps_window_seconds}
    else:
        scale = build_timescale(pcps, TimescaleConfig(nominal_hz=config.nominal_hz,
                                                    window_seconds=config.pps_window_seconds))
        calibration = scale.calibrate_ticks(data[edges].to_numpy(), seq=data.seq,
                                            period_hint_s=config.target_period_s)
        result.attrs["pps_timescale"] = scale.metadata
    for column in calibration:
        result["pps_"+column] = calibration[column].to_numpy()
    for column in (DERIVED.period_s, DERIVED.rate_ppm, DERIVED.sec_per_day,
                   DERIVED.min_per_day, DERIVED.A_half_ms, DERIVED.f_used_hz):
        result["nominal_"+column] = result[column]
    drops = [name for name in ("drop_ir", "drop_pps", "drop_swing") if name in result]
    if drops:
        increments = result[drops].diff().mod(2**32)
        increments.iloc[0] = result[drops].iloc[0]
        result[INPUT.dropped] = increments.gt(0).any(axis=1).astype(int)
    result[DERIVED.period_s] = result.pps_period_s
    result[DERIVED.f_used_hz] = result.pps_frequency_hz
    result[DERIVED.rate_ppm] = (result[DERIVED.period_s]/config.target_period_s-1)*1e6
    result[DERIVED.sec_per_day] = result[DERIVED.rate_ppm]*.0864
    result[DERIVED.min_per_day] = result[DERIVED.sec_per_day]/60
    result[DERIVED.A_half_ms] = (2*result.pps_edge2_s-result.pps_edge0_s-result.pps_edge4_s)*1000
    return result
