"""Meaningful invariants for the standalone trial, independent of private data."""
import numpy as np
import pandas as pd
import pytest

from synchronome_trial.config import TrialConfig
from synchronome_trial.data import METRICS, moist_density
from synchronome_trial.phase import template, apply_template, flag_profile, flag_evolution
from synchronome_trial.periods import alternative_periods
from synchronome_trial.events import select_events
from synchronome_trial.modulation import investigate
from synchronome_trial.environment import models, hourly


def phase_data(n_cycles=30):
    seq = np.arange(n_cycles*15)
    phase = seq%15
    f = pd.DataFrame(dict(epoch=0, cycle=seq//15, phase15=phase, source_row=seq+2,
                          sequence_extended=seq, time_s=seq*2, calibrated_valid=True))
    profile = np.arange(15)-7
    for metric in METRICS:
        f[metric] = 100+profile[phase]
    c = pd.DataFrame(dict(epoch=0, cycle=np.arange(n_cycles), available=True,
                          start_s=np.arange(n_cycles)*30, last_start_s=np.arange(n_cycles)*30+28))
    return f, c


def test_phase_template_is_frozen_and_preserves_later_change():
    f, c = phase_data()
    p, count = template(f, c, 0, 0, 600)
    assert count == 20
    assert np.allclose(p.groupby("metric").offset_us.sum(), 0)
    changed = f.copy()
    changed.loc[changed.cycle.ge(20), "period_us"] += 50
    adjusted = apply_template(changed, p)
    assert np.allclose(adjusted.loc[adjusted.cycle.lt(20), "period_us_adjusted"], 100)
    assert np.allclose(adjusted.loc[adjusted.cycle.ge(20), "period_us_adjusted"], 150)
    # A missing cycle excludes its complete set of phases, without shifting any.
    c.loc[c.cycle.eq(3), "available"] = False
    p, count = template(changed, c, 0, 0, 600)
    assert count == 19
    assert p["count"].eq(19).all()


def test_flag_profile_normalizes_each_direction_without_losing_cycle_exposure():
    f, c = phase_data(150)
    c["time_s"] = c.start_s+15
    # Arbitrary amplitude/geometry scale varies by cycle and direction; the
    # normalized pattern should be unchanged when those whole-cycle scales vary.
    for side, scale in (("tick", 1.), ("tock", 1.2)):
        f[side+"_block_us"] = (24_000+f.phase15*10)*scale*(1+f.cycle*.001)
    profile = flag_profile(f, c)
    assert profile["count"].eq(150).all()
    assert profile.cycle_normalized_speed_std_pct.max() < 1e-10
    history = flag_evolution(f, c)
    assert history.position30.nunique() == 30
    assert history.groupby(["epoch", "hour"])["count"].nunique().eq(1).all()


def edge_frame():
    n = 12
    seq = np.arange(n)
    # Wrap the hardware counter inside the first row, then several more times
    # could be added without changing the component identities.
    absolute = 2**32-5_000_000+seq*32_000_000
    tick_open = np.full(n, .975)
    tick_block = .024+seq*1e-5
    tick_half = tick_open+tick_block
    tock_block = .023+seq*2e-5
    tock_open = 2-tick_half-tock_block
    f = pd.DataFrame(dict(source_row=seq+2, seq=seq, sequence_extended=seq, epoch=0,
                          cycle=seq//15, phase15=seq%15, time_s=seq*2, day=seq*2/86400,
                          calibrated_valid=True, raw_valid=True, calibration_counter_segment=0))
    values = dict(full=np.full(n, 2.), tick_open=tick_open, tick_blocked=tick_block,
                  tick_half=tick_half, tock_open=tock_open, tock_blocked=tock_block)
    for name, value in values.items():
        f[name+"_s"] = value
        f[name+"_pps_s"] = value
    offsets = [np.zeros(n), tick_open, tick_half, tick_half+tock_open, np.full(n, 2.)]
    for j, offset in enumerate(offsets):
        f[f"edge{j}_tcb0"] = (absolute+np.rint(offset*16_000_000).astype("int64"))%2**32
    return f, offsets


def test_four_periods_use_actual_crossing_offsets_and_handle_wrap():
    f, offsets = edge_frame()
    p = alternative_periods(f)
    assert p.pair_valid.sum() == len(f)-1
    for j in range(4):
        absolute_times = f.time_s.to_numpy()+offsets[j]
        expected = (np.diff(absolute_times)-2)*1e6
        assert np.allclose(p[f"pps_e{j}_us"].dropna(), expected, atol=1e-8)
    assert np.allclose(p.pps_tick_mid_us.dropna(), (p.pps_e1_us+p.pps_e2_us).dropna()/2)
    assert np.allclose(p.pps_tock_mid_us.dropna(), p.pps_e3_us.dropna()/2)
    assert np.allclose(p.raw_e2_us.dropna(), p.pps_e2_us.dropna())


@pytest.mark.parametrize("break_kind", ["sequence", "epoch", "shared_edge", "calibration", "counter_segment"])
def test_alternative_periods_never_bridge_invalid_adjacency(break_kind):
    f, _ = edge_frame()
    if break_kind == "sequence":
        f.loc[6:, "sequence_extended"] += 1
    elif break_kind == "epoch":
        f.loc[6:, "epoch"] = 1
    elif break_kind == "shared_edge":
        f.loc[6, "edge0_tcb0"] += 1
    elif break_kind == "calibration":
        f.loc[6, "calibrated_valid"] = False
    else:
        f.loc[6:, "calibration_counter_segment"] = 1
    p = alternative_periods(f)
    assert not p.loc[5, "pair_valid"]
    assert p.loc[5, [name for name in p if name.startswith("pps_")]].isna().all()
    if break_kind == "calibration":
        assert not p.loc[6, "pair_valid"]


def cycle_signal(n=400):
    k = np.arange(n)
    wave = np.sin(2*np.pi*k/20)
    return pd.DataFrame(dict(epoch=0, cycle=k, available=True, series_run=1, time_s=k*30+15,
        start_s=k*30, last_start_s=k*30+28, period_us=wave, difference_us=2*wave,
        flag_mean_us=24_000+wave, environment_valid=True,
        temperature_C=22+wave*.1, humidity_pct=60+wave, pressure_hPa=1000+wave,
        density_kg_m3=1.2+wave*.001, day=(k*30+15)/86400))


def test_known_twenty_cycle_modulation_and_original_even_odd_pairing():
    c = cycle_signal()
    _, pairs, _, _, metadata = investigate(c)
    assert all(abs(row["peak_period_s"]-600) < 1e-8 for row in metadata)
    # Dropping one group does not renumber the subsequent even/odd pairs.
    c.loc[9, "available"] = False
    c.loc[10:, "series_run"] = 2
    _, pairs, _, _, _ = investigate(c)
    p = pairs.loc[pairs.metric.eq("period_us")]
    assert 4 not in set(p.pair)
    assert 5 in set(p.pair)


@pytest.mark.parametrize("step", [200., -200., 80.])
def test_candidate_selection_keeps_epoch_boundaries_and_declared_times(step):
    c = cycle_signal(180)
    c["period_us"] = 0.
    c["difference_us"] = 0.
    c["flag_mean_us"] = 24_000.
    c.loc[c.cycle.ge(90), "difference_us"] = step
    events = select_events(c, TrialConfig(max_events=2))
    assert events
    assert any(abs(event["cycle"]-90) <= 1 for event in events)
    event = next(event for event in events if event["cycle"] == 90)
    assert event["difference_us_selection_jump"] == step
    assert event["difference_us_selection_contrast"] == step
    assert event["period_us_selection_jump"] == 0
    assert event["flag_mean_us_selection_contrast"] == 0
    assert event["score"] == abs(step)/80
    from dataclasses import asdict
    from synchronome_trial.report import selection_table
    rendered = selection_table([event], asdict(TrialConfig()))
    assert rendered.count('class="selection-hit"') == 2
    assert f"{step:+.3f} ✓" in rendered
    assert "Half difference: cycle jump / Half difference: median contrast" in rendered
    with pytest.raises(ValueError, match="No complete cycle"):
        select_events(c, TrialConfig(), [200])


def test_joint_models_use_common_population_and_only_train_on_earlier_data():
    n = 100
    i = np.arange(n)
    t = 22+np.sin(i*.4)
    rh = 60+np.cos(i*.7)*3
    p = 1000+np.sin(i*.13)*5
    y = 2*t-.7*rh+1.3*p
    y[70:] += 25 # unseen operating-state shift
    h = pd.DataFrame(dict(epoch=0, eligible=True, hour=i, time_s=i*3600, day=i/24,
        temperature_C=t, humidity_pct=rh, pressure_hPa=p,
        density_kg_m3=moist_density(t, rh, p), speed_proxy_pct=np.cos(i*.21),
        thermal_direction=np.sign(np.cos(i*.4)), direction_label=np.where(np.cos(i*.4)>0, "warming", "cooling"),
        temperature_rate_C_per_hour=.4*np.cos(i*.4),
        period_us=y, difference_us=y*.2))
    results, predictions, _ = models(h)
    available = [r for r in results if r["status"] == "available"]
    assert {r["hours"] for r in available} == {100}
    assert {r["train_hours"] for r in available} == {70}
    joint = next(r for r in available if r["label"] == "Temperature + humidity + pressure" and r["response"] == "period_us")
    assert joint["training_coefficients"] == pytest.approx(dict(temperature_C=2, humidity_pct=-.7, pressure_hPa=1.3), abs=1e-9)
    assert joint["test_rmse_us"] == pytest.approx(25, abs=1e-8)
    train = predictions.loc[predictions.validation_split.eq("earlier training")]
    assert train.test_prediction_us.isna().all()
    assert predictions.loc[predictions.validation_split.eq("later testing")].test_prediction_us.notna().all()


def test_thermal_direction_is_unavailable_across_missing_hours():
    c = cycle_signal(600)
    c.loc[c.time_s.between(3600, 7200, inclusive="left"), "available"] = False
    h = hourly(c)
    assert 1 not in set(h.hour)
    assert h.loc[h.hour.eq(2), "thermal_direction"].isna().all()


def test_impulse_anchor_requires_both_phase_and_direction():
    with pytest.raises(ValueError, match="supplied together"):
        TrialConfig(impulse_phase=2)
    with pytest.raises(ValueError, match="0..14"):
        TrialConfig(impulse_phase=15, impulse_side="tick")
