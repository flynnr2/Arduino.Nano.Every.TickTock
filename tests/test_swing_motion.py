"""Motion metrology, complete-cycle ownership and honest model validation."""
import numpy as np
import pandas as pd
import pytest
from pendulum_analysis.suite.common import Settings
from pendulum_analysis.suite.motion import motion_analysis
from pendulum_analysis.suite.motion_fits import fit_motion_hourly
from pendulum_analysis.suite.time_series import cycle_series


def frame(n=60, scale=1., origin=0):
    seq = np.arange(origin, origin+n, dtype=np.int64)
    f = pd.DataFrame(dict(epoch=0, source_row=np.arange(n)+2, sequence_extended=seq,
                          phase15=(seq-origin)%15, elapsed_cycles=np.arange(n)*32e6,
                          calibrated_valid=True, full_pps_s=2.*scale,
                          tick_open_pps_s=.98*scale, tick_blocked_pps_s=.02*scale,
                          tick_half_pps_s=1.*scale, tock_open_pps_s=.975*scale,
                          tock_blocked_pps_s=.025*scale))
    return f


def analyze(f, cfg=None):
    cfg = cfg or Settings()
    blocks, _, meta = cycle_series(f, cfg)
    return motion_analysis(f, blocks, cfg, meta['evolution_bin_seconds'])


def test_motion_uses_each_inverse_and_each_squared_amplitude_before_averaging():
    f = frame(30)
    f.loc[::2, 'tick_blocked_pps_s'] = .04
    tables, meta = analyze(f)
    obs = tables['swing_motion_observations']
    assert obs.tick_velocity_mm_s.iloc[:2].tolist() == pytest.approx([94.75, 189.5])
    expected = np.degrees(obs.tick_velocity_mm_s.to_numpy()/(np.pi*1242))
    np.testing.assert_allclose(obs.tick_amplitude_deg, expected)
    cycles = tables['swing_motion_cycles']
    assert cycles.tick_velocity_mm_s.iloc[0] == pytest.approx((8*94.75+7*189.5)/15)
    assert cycles.tick_velocity_mm_s.iloc[0] != pytest.approx(3.79/f.tick_blocked_pps_s.iloc[:15].mean())
    assert cycles.tick_amplitude_squared_rad2.iloc[0] == pytest.approx(np.mean(np.deg2rad(expected[:15])**2))
    assert cycles.tick_amplitude_squared_rad2.iloc[0] > np.deg2rad(cycles.tick_amplitude_deg.iloc[0])**2
    assert meta['effective_path_mm'] == 3.79
    assert meta['complete_cycles'] == 2


def test_geometry_and_timebase_scaling_have_distinct_physical_effects():
    a, _ = analyze(frame())
    b, _ = analyze(frame(scale=1.001))
    c, _ = analyze(frame(), Settings(optical_path_mm=7.58, pivot_to_beam_mm=2484))
    x, y, z = [t['swing_motion_observations'] for t in (a,b,c)]
    np.testing.assert_allclose(y.tick_velocity_mm_s, x.tick_velocity_mm_s/1.001)
    np.testing.assert_allclose(y.tick_amplitude_deg, x.tick_amplitude_deg)
    np.testing.assert_allclose(z.tick_velocity_mm_s, x.tick_velocity_mm_s*2)
    np.testing.assert_allclose(z.tick_amplitude_deg, x.tick_amplitude_deg)


def test_gaps_exclusions_partial_ends_and_epochs_never_create_motion_cycles():
    f = frame(90).drop(index=22)
    f.loc[38, 'calibrated_valid'] = False
    f.loc[60:, 'epoch'] = 1
    f.loc[60:, 'phase15'] = (f.loc[60:, 'sequence_extended']-60)%15
    tables, _ = analyze(f)
    cycles = tables['swing_motion_cycles']
    assert cycles.available.tolist() == [True, False, False, True, True, True]
    assert cycles.loc[~cycles.available, 'tick_velocity_mm_s'].isna().all()
    obs = tables['swing_motion_observations']
    assert obs.loc[obs.sequence_extended.eq(38), 'tick_velocity_mm_s'].isna().all()
    assert obs.loc[obs.sequence_extended.eq(21), 'series_run'].iloc[0] != obs.loc[obs.sequence_extended.eq(23), 'series_run'].iloc[0]
    counts = tables['swing_motion_phase'].groupby(['epoch','metric'])['count'].apply(list)
    assert all(set(values)=={2} for values in counts)
    assert set(obs.loc[obs.sequence_extended.eq(23), 'phase15']) == {8}
    assert cycles.series_run.iloc[0] != cycles.series_run.iloc[3]
    partial, _ = analyze(frame(16).iloc[1:])
    assert not partial['swing_motion_cycles'].available.any()


def test_extended_sequence_origin_preserves_phase_and_passage_timestamps():
    origin = 2**32-5
    tables, _ = analyze(frame(30, origin=origin), Settings(phase_origin=origin))
    obs = tables['swing_motion_observations']
    assert tables['swing_motion_cycles'].available.all()
    assert obs.tick_time_s.iloc[0] == pytest.approx(.99)
    assert obs.tock_time_s.iloc[0] == pytest.approx(1.9875)
    assert obs.sequence_extended.iloc[-1] > 2**32
    assert set(tables['swing_motion_phase'].phase) == set(range(30))
    np.testing.assert_allclose(obs.tick_velocity_mm_s_change_pct, 0, atol=1e-12)


def test_no_eligible_motion_retains_explicit_unavailable_populations():
    f = frame()
    f.calibrated_valid = False
    tables, meta = analyze(f)
    assert not tables['swing_motion_observations'].available.any()
    assert not tables['swing_motion_cycles'].available.any()
    assert tables['swing_motion_phase'].empty
    assert tables['swing_motion_summary']['count'].eq(0).all()
    assert meta['eligible_swings'] == 0
    assert tables['swing_motion_fits'].status.eq('insufficient hourly coverage').all()


def hourly(n=60):
    rng = np.random.default_rng(391)
    x = rng.normal(size=(n,4))
    a = .001+x[:,3]*.00001
    y = x[:,:3] @ [3., 2., -1.] + x[:,3]*5
    data = pd.DataFrame(dict(epoch=0, hour=np.arange(n), cycles=100, eligible=True,
                            amplitude_squared_rad2=a, temperature_C=x[:,0], humidity_pct=x[:,1],
                            pressure_hPa=x[:,2], period_error_us=y, population='shared_environment'))
    return pd.concat([data, data.assign(population='motion')], ignore_index=True)


def test_later_checks_are_matched_and_training_never_sees_test_responses():
    hours = hourly()
    fits, validation, pred = fit_motion_hourly(hours, [0])
    check = validation.set_index('model')
    assert check.train_hours.tolist() == [42]*3
    assert check.test_hours.tolist() == [18]*3
    assert check.loc['environment_amplitude','model_rmse_us'] < 1e-9
    assert check.loc['environment','model_rmse_us'] > 1
    assert check.loc['environment','constant_rmse_us'] == check.loc['environment_amplitude','constant_rmse_us']
    assert fits.set_index('model').loc['environment_amplitude','slope_us_per_rad2'] == pytest.approx(5e5)
    changed = hours.copy()
    changed.loc[changed.hour.ge(42), 'period_error_us'] += 100
    _, _, new_pred = fit_motion_hourly(changed, [0])
    np.testing.assert_allclose(pred.held_out_prediction_us, new_pred.held_out_prediction_us, equal_nan=True)


def test_shared_environment_rejects_cycle_if_any_sensor_reading_is_invalid():
    f = frame(1800)
    # Varying readings avoid the retrospective stale-value screen.
    for c, base in [('temperature_C',20),('humidity_pct',50),('pressure_hPa',1010)]:
        f[c] = base + np.arange(len(f))*.00001
    f.loc[17,'temperature_C'] = np.nan
    tables, _ = analyze(f)
    hours = tables['swing_motion_hourly'].set_index('population')
    assert hours.loc['motion','cycles'] == 120
    assert hours.loc['shared_environment','cycles'] == 119


def test_collinear_motion_environment_predictors_withhold_adjusted_slopes():
    hours = hourly()
    hours['amplitude_squared_rad2'] = .001 + hours.temperature_C*.00001
    fits, checks, _ = fit_motion_hourly(hours, [0])
    joint = fits.set_index('model').loc['environment_amplitude']
    assert joint.predictor_rank == 3
    assert np.isnan(joint.slope_us_per_rad2)
    assert joint.status == 'predictors not separable; slopes withheld'
    assert checks.set_index('model').loc['environment_amplitude','status'] == 'training predictors not separable'


def test_motion_render_handles_no_complete_or_calibrated_population(tmp_path):
    from types import SimpleNamespace
    from pendulum_analysis.swing_components.motion import render
    from pendulum_analysis.suite.report import _table
    f = frame(10)
    f.calibrated_valid = False
    tables, _ = analyze(f)
    spec = render(SimpleNamespace(tables=tables), 0, Settings(), tmp_path, _table)
    assert 'No eligible PPS-calibrated optical passages' in spec['html']
    assert '<script' not in spec['html']
    assert len(spec['plots']) == 5
    assert all((tmp_path / p).is_file() for p in spec['plots'])


def test_long_motion_payload_uses_bounded_lossless_companion_chunks(tmp_path, monkeypatch):
    import base64
    import gzip
    import json
    import re
    from types import SimpleNamespace
    from pendulum_analysis.swing_components import motion
    from pendulum_analysis.suite.report import _table
    tables, _ = analyze(frame(75))
    observations = tables['swing_motion_observations']
    monkeypatch.setattr(motion, 'INLINE_LIMIT', 30)
    monkeypatch.setattr(motion, 'CHUNK_ROWS', 17)
    payload = motion.individual_payload(observations, 0, tmp_path)
    assert 'gzip_base64' not in payload
    assert payload['points'] == 75 and payload['max_chunks'] == 4
    assert [c['rows'] for c in payload['chunks']] == [17,17,17,17,7]
    def unpack(packed):
        raw = gzip.decompress(base64.b64decode(packed['gzip_base64']))
        return {k:np.frombuffer(raw, dtype='<u4' if c['type']=='Uint32Array' else '<f8',
                               count=c['length'], offset=c['offset']) for k,c in packed['columns'].items()}
    pieces = []
    for chunk in payload['chunks']:
        script = (tmp_path / chunk['src'].split('/')[-1]).read_text()
        detail = json.loads(script.split('{detail:',1)[1].removesuffix('}));'))
        assert detail['id'] == chunk['id']
        pieces.append(unpack(detail['packed']))
    original = unpack(motion.packed(observations))
    for key,values in original.items():
        np.testing.assert_array_equal(np.concatenate([p[key] for p in pieces]), values)
    assert payload['chunks'][1]['start'] == observations.start_s.iloc[17]
    assert payload['chunks'][0]['end'] == observations.start_s.iloc[16]
    spec = motion.render(SimpleNamespace(tables=tables), 0, Settings(), tmp_path, _table)
    manifest = json.loads(re.search(r'<script id="motion-e0--plot-data" type="application/json">(.*?)</script>', spec['html']).group(1))
    assert manifest['individual'] == payload
    assert len(json.dumps(manifest['individual'])) < 2000
    assert 'keep that folder beside this report' in spec['html']


@pytest.mark.parametrize('name', ['optical_path_mm','pivot_to_beam_mm'])
@pytest.mark.parametrize('value', [0,-1,np.inf,np.nan])
def test_geometry_must_be_positive_and_finite(name,value):
    with pytest.raises(ValueError,match=name):
        Settings(**{name:value})
