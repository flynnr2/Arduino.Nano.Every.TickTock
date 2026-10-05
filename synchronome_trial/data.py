"""Reuse source metrology; expose named trial quantities and complete cycles."""
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd

from pendulum_analysis.suite.common import (
    Settings, discover, read_numeric, fingerprint, environmental_validity,
)
from pendulum_analysis.suite.clock import REQUIRED as PPS_REQUIRED
from pendulum_analysis.suite.swings import analyze_swings, REQUIRED as SWING_REQUIRED
from pendulum_analysis.pps.timescale import build_timescale, TimescaleConfig


METRICS = ("period_us", "difference_us", "tick_us", "tock_us",
           "tick_block_us", "tock_block_us", "tick_half_us", "tock_half_us", "flag_mean_us")
ENV = ("temperature_C", "humidity_pct", "pressure_hPa")


def moist_density(temperature_C, humidity_pct, pressure_hPa):
    """Ideal moist mixture with updated Buck saturation pressure, kg/m3."""
    t = np.asarray(temperature_C, float)
    p = np.asarray(pressure_hPa, float) * 100
    e = np.asarray(humidity_pct, float) / 100 * 611.21 * np.exp((18.678-t/234.5)*t/(257.14+t))
    return (p-e)/(287.05*(t+273.15)) + e/(461.5*(t+273.15))


def trial_frame(frame, settings):
    f = frame.copy()
    f["time_s"] = f.elapsed_cycles / settings.nominal_hz
    f["day"] = f.time_s / 86400
    f["cycle"] = ((f.sequence_extended-settings.phase_origin)//15).fillna(-1).astype("int64")
    mapping = {"tick": "tick_open", "tock": "tock_open",
               "tick_block": "tick_blocked", "tock_block": "tock_blocked",
               "tick_half": "tick_half", "tock_half": "tock_half"}
    for label, component in mapping.items():
        f[label+"_us"] = f[component+"_pps_s"] * 1e6
    f["period_us"] = (f.full_pps_s-2)*1e6
    f["difference_us"] = f.tick_half_us-f.tock_half_us
    f["flag_mean_us"] = (f.tick_block_us+f.tock_block_us)/2
    for name in ENV:
        if name not in f:
            f[name] = np.nan
    masks, stale, _ = environmental_validity(f, f, settings)
    env_ok = np.logical_and.reduce([masks[name] for name in ENV])
    env_ok &= f.temperature_C.between(-20, 50).to_numpy() & f.pressure_hPa.between(600, 1100).to_numpy()
    f["environment_valid"] = env_ok
    for name in ENV:
        f[name] = f[name].where(masks[name])
    f["density_kg_m3"] = np.where(env_ok, moist_density(f.temperature_C, f.humidity_pct, f.pressure_hPa), np.nan)
    return f, stale


def aggregate_cycles(frame, blocks):
    """Keep unavailable groups in the timeline; no reindexing across exclusions."""
    columns = list(METRICS)+list(ENV)+["density_kg_m3"]
    selected = frame.merge(blocks.loc[blocks.available, ["epoch", "cycle"]],
                           on=["epoch", "cycle"], validate="many_to_one")
    means = selected.groupby(["epoch", "cycle"]).agg(
        **{name: (name, "mean") for name in columns},
        environment_valid=("environment_valid", "all"), phases=("phase15", "nunique"),
    ).reset_index()
    base = blocks.drop(columns="phases").copy()
    result = base.merge(means, on=["epoch", "cycle"], how="left", validate="one_to_one")
    result["day"] = result.time_s/86400
    joint = result.environment_valid.eq(True)
    result.loc[~joint, [*ENV, "density_kg_m3"]] = np.nan
    return result


def load(input_path, config):
    directory = Path(input_path).resolve()
    if "67days" in (part.casefold() for part in directory.parts):
        raise ValueError("The suspect 67Days recording is explicitly excluded from this trial")
    inputs = discover(directory)
    if "pcsw" not in inputs:
        raise ValueError("This trial needs matching PCPS and PCSW files")
    fingerprints = {role: fingerprint(path) for role, path in inputs.items()}
    settings = Settings(nominal_hz=config.nominal_hz, pps_window_seconds=config.pps_window_seconds,
                        phase_origin=config.phase_origin)
    scale = build_timescale(read_numeric(inputs["pcps"], PPS_REQUIRED),
                            TimescaleConfig(nominal_hz=config.nominal_hz, window_seconds=config.pps_window_seconds))
    result = analyze_swings(read_numeric(inputs["pcsw"], SWING_REQUIRED), SimpleNamespace(timescale=scale), settings)
    f, stale = trial_frame(result.frame, settings)
    cycles = aggregate_cycles(f, result.tables["swing_cycles"])
    if not cycles.available.any():
        raise ValueError("No complete calibrated 15-swing cycles are available")
    return f, cycles, stale, result.summary, inputs, fingerprints
