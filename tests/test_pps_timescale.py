import numpy as np
import pandas as pd
import pytest
from pendulum_analysis.pps.timescale import TimescaleConfig, build_timescale, U32


def pps(t, hz=16_000_016., start=1000, seq=None):
    tick = np.rint(start+np.asarray(t)*hz).astype(np.int64)%U32
    return pd.DataFrame(dict(seq=np.arange(len(t)) if seq is None else seq, edge_tcb0=tick,
        cap16=(tick+8)%65536, latency16=97, now32=(tick+97)%U32, gps_status=2, drop_pps=0))


def edges(t, hz=16_000_016., start=1000):
    return np.rint(start+np.asarray(t)[:, None]*hz+np.arange(5)*hz/2)%U32


@pytest.mark.parametrize('start', [1000, U32-20_000_000])
@pytest.mark.parametrize('swing_start', [-4, 4])
def test_alignment_uses_hardware_overlap_and_wrap(start, swing_start):
    scale = build_timescale(pps(np.arange(100), start=start))
    t = np.arange(swing_start, 105, 2)
    out = scale.calibrate_ticks(edges(t, start=start))
    expected = (t >= 0) & (t+2 <= 99)
    assert out.calibration_valid.tolist() == expected.tolist()
    np.testing.assert_allclose(out.loc[expected, 'period_s'], 2, atol=1e-10)


def test_missing_pulses_keep_counter_continuity_but_exclude_gap():
    t = np.r_[np.arange(30), np.arange(33, 80)]
    scale = build_timescale(pps(t))
    assert scale.observations.counter_segment.nunique() == 1
    assert not scale.observations.cadence_valid.iloc[30]
    out = scale.calibrate_ticks(edges([26, 28, 30, 32, 34, 36]))
    assert out.calibration_valid.tolist() == [True, False, False, False, True, True]


def test_lock_and_drop_failures_never_bridged():
    d = pps(np.arange(100))
    d.loc[:9, 'gps_status'] = 1
    d.loc[50:, 'drop_pps'] = 1
    scale = build_timescale(d)
    out = scale.calibrate_ticks(edges([4, 12, 48, 50, 54]))
    assert out.calibration_valid.tolist() == [False, True, False, True, True]


def test_reset_epochs_are_not_local_parser_keys():
    d = pd.concat([pps(np.arange(80)), pps(np.arange(80))], ignore_index=True)
    d['epoch'] = 100  # deliberately useless parser metadata
    scale = build_timescale(d)
    assert scale.observations.counter_segment.nunique() == 2
    out = scale.calibrate_ticks(edges([4, 6, 8]))
    assert not out.calibration_valid.any()
    assert out.alignment_reason.eq('ambiguous_hardware_overlap').all()


def test_quadratic_phase_fit_recovers_drift_without_injecting_pulse_jitter():
    t = np.arange(200)
    d = pps(t)
    phase = np.rint(.1*t*t+20*(-1.)**t).astype(int)
    d.edge_tcb0 += phase
    d.now32 += phase
    scale = build_timescale(d)
    f = scale.observations.frequency_hz.to_numpy()
    np.testing.assert_allclose(f[30:-30], 16_000_016+.2*t[30:-30], atol=.01)
    assert np.std(np.diff(d.edge_tcb0)-16_000_016-.2*t[1:]) > 30
    assert scale.metadata['window_seconds'] == 61


def test_short_or_malformed_input_has_no_calibration():
    d = pps(np.arange(3))
    scale = build_timescale(d)
    assert not scale.calibrate_ticks(edges([0])).calibration_valid.any()
    d.loc[1, 'now32'] += 1
    assert not build_timescale(d).observations.calibration_interval_valid.any()


def test_config_rejects_invalid_window():
    with pytest.raises(ValueError):
        TimescaleConfig(window_seconds=2)


def test_diagnostic_timeline_missing_pulse_is_not_a_counter_reset():
    from pendulum_analysis.pps.core import reconstruct_timeline
    _, _, _, segment, ambiguous = reconstruct_timeline([1, 2, 3], [1000, 32_001_032, 48_001_048], 16_000_000)
    assert segment.tolist() == [0, 0, 0]
    assert not ambiguous.any()


def test_multiwrap_sequence_gap_is_unwrapped_but_not_calibrated():
    t = np.r_[np.arange(20), np.arange(620, 650)]
    scale = build_timescale(pps(t, seq=t))
    assert scale.observations.counter_segment.nunique() == 1
    assert scale.observations.interval_cycles.iloc[20] == 601*16_000_016
    assert not scale.observations.calibration_interval_valid.iloc[20]


def test_nonmonotonic_first_edge_cannot_corrupt_overlap_search():
    scale = build_timescale(pps(np.arange(80)))
    raw = edges([10, 12, 14, 16])
    # Ordered inside its row, but incorrectly starts behind the previous swing.
    raw[1, 0] = 1000+9*16_000_016
    out = scale.calibrate_ticks(raw)
    assert out.calibration_valid.tolist() == [True, False, True, True]
    assert out.alignment_reason.iloc[1] == 'invalid_swing_timestamp'
    np.testing.assert_allclose(out.loc[out.calibration_valid, 'period_s'], 2, atol=1e-10)


def test_empty_pps_has_explicit_error():
    with pytest.raises(ValueError, match='no data records'):
        build_timescale(pd.DataFrame())
