"""Chronological cycle populations, changing patterns and compact report coverage."""
import numpy as np
import pandas as pd
import pytest

from pendulum_analysis.suite.common import Settings
from pendulum_analysis.suite.time_series import cycle_series, cycle_autocorrelation, swing_frequency_summary


def frame(sequence, periods=None, epoch=0, start_s=0):
    seq = np.asarray(sequence)
    return pd.DataFrame(dict(epoch=epoch, sequence_extended=seq, source_row=seq + 2,
                             elapsed_cycles=(start_s + np.arange(len(seq)) * 2) * 16e6,
                             calibrated_valid=True, full_pps_s=2. if periods is None else periods,
                             phase15=seq % 15))


def test_complete_cycles_retain_impulse_and_never_compress_missing_observations():
    seq = np.arange(60)
    periods = 2. + (seq % 15 == 12) * .003 + (seq // 15) * .00001
    f = frame(seq, periods)
    f = f.loc[f.sequence_extended.ne(22)]
    f.loc[f.sequence_extended.eq(38), "calibrated_valid"] = False
    blocks, evolution, meta = cycle_series(f, Settings())
    assert blocks.available.tolist() == [True, False, False, True]
    assert blocks.mean_period_s.iloc[0] == pytest.approx(2.0002)
    assert blocks.mean_period_s.iloc[3] == pytest.approx(2.00023)
    assert blocks.mean_period_s.iloc[1:3].isna().all()
    assert blocks.series_run.iloc[0] != blocks.series_run.iloc[3]
    assert meta["complete_cycles"] == 2
    assert evolution["count"].tolist() == [2] * 15
    assert evolution.loc[evolution.phase15.eq(12), "phase_deviation_us"].iloc[0] == pytest.approx(2800)


def test_phase_evolution_separates_rate_drift_from_changes_in_pattern():
    # Equal impulse patterns with a different overall rate must have equal colours.
    a = frame(np.arange(30), 2. + (np.arange(30) % 15 == 5) * .001)
    b = frame(np.arange(30, 60), 2.01 + (np.arange(30) % 15 == 5) * .001, start_s=3600)
    b.source_row += 100
    _, e, _ = cycle_series(pd.concat([a, b], ignore_index=True), Settings())
    left = e.loc[e.time_bin.eq(0), "phase_deviation_us"].to_numpy()
    right = e.loc[e.time_bin.eq(1), "phase_deviation_us"].to_numpy()
    np.testing.assert_allclose(left, right, atol=1e-8)
    b.full_pps_s += (b.phase15 == 5) * .001
    _, changed, _ = cycle_series(pd.concat([a, b], ignore_index=True), Settings())
    assert changed.loc[changed.time_bin.eq(1) & changed.phase15.eq(5), "phase_deviation_us"].iloc[0] > right[5] * 1.9


def test_epochs_partial_ends_and_sequence_wrap_extension_remain_separate():
    origin = 2**32 - 5
    seq = np.arange(origin, origin + 30, dtype=np.int64)
    f = frame(seq)
    f.phase15 = (f.sequence_extended - origin) % 15
    blocks, _, _ = cycle_series(f, Settings(phase_origin=origin))
    assert blocks.available.all() and blocks.first_sequence.iloc[1] > 2**32
    split = pd.concat([frame(np.arange(8)), frame(np.arange(8, 15), epoch=1)], ignore_index=True)
    blocks, _, _ = cycle_series(split, Settings())
    assert not blocks.available.any()
    partial = frame(np.arange(4, 46))
    blocks, _, _ = cycle_series(partial, Settings())
    assert blocks.available.tolist() == [False, True, True, False]


def test_autocorrelation_pairs_never_bridge_an_excluded_cycle():
    f = frame(np.arange(75), 2. + np.repeat([0., .001, .1, .003, .004], 15))
    f.loc[f.sequence_extended.between(30, 44), "calibrated_valid"] = False
    blocks, _, _ = cycle_series(f, Settings())
    result = cycle_autocorrelation(blocks, min_pairs=1)
    assert result.lag_cycles.tolist() == [0, 1]
    assert result.pairs.tolist() == [4, 2]
    assert result.lag_seconds.tolist() == pytest.approx([0, 30])
    values = np.array([0., 1., 3., 4.]) - 2.
    expected = (values[0] * values[1] + values[2] * values[3]) / np.dot(values, values)
    assert result.autocorrelation.tolist() == pytest.approx([1., expected])
    constant, _, _ = cycle_series(frame(np.arange(60)), Settings())
    assert cycle_autocorrelation(constant, min_pairs=1).empty


def test_frequency_is_reciprocal_period_and_scatter_distinguishes_linear_drift():
    periods = 2. + np.repeat(np.arange(10)*1e-6, 15)
    blocks, _, _ = cycle_series(frame(np.arange(150), periods), Settings())
    np.testing.assert_allclose(blocks.frequency_offset_ppm, (2/blocks.mean_period_s-1)*1e6)
    assert blocks.frequency_offset_ppm.iloc[-1] < blocks.frequency_offset_ppm.iloc[0]
    assert blocks.frequency_deviation_ppm.mean() == pytest.approx(0, abs=1e-12)
    summary = swing_frequency_summary(blocks, Settings())
    assert summary.cycles.tolist() == [10, 10]
    assert summary.period_peak_to_peak_us.tolist() == pytest.approx([9, 9], abs=1e-8)
    assert summary.period_rms_us.tolist() == pytest.approx([np.sqrt(8.25)]*2, abs=1e-8)
    assert summary.linear_drift_us_per_hour.tolist() == pytest.approx([120,120], abs=1e-7)
    assert summary.detrended_period_rms_us.tolist() == pytest.approx([0,0], abs=1e-8)
    # The optional local component view must not displace frequency detail.
    pd.testing.assert_frame_equal(summary, swing_frequency_summary(blocks, Settings(detail_start_hours=24)))
    assert summary.iloc[1].scope == "Central detail window"
    assert summary.iloc[1].window_start_s == 0
    assert summary.iloc[1].window_end_s == 300


def test_frequency_window_is_central_in_time_despite_gaps_and_start_offset():
    # A nonzero epoch start and an early exclusion must not select by row count.
    f = frame(np.arange(12*1800), start_s=24*3600, epoch=2)
    f.loc[f.elapsed_cycles.between(25*3600*16e6,29*3600*16e6), "calibrated_valid"] = False
    blocks, _, _ = cycle_series(f, Settings())
    selected = swing_frequency_summary(blocks, Settings()).iloc[1]
    assert selected.window_start_s == 28*3600
    assert selected.window_end_s == 32*3600
    expected = blocks.loc[blocks.available & blocks.time_s.ge(28*3600) & blocks.time_s.lt(32*3600)]
    assert selected.cycles == len(expected)
    assert selected.cycles < 4*120  # Excluded groups are not replaced from elsewhere.


def test_frequency_window_with_no_complete_groups_remains_unavailable():
    blocks, _, _ = cycle_series(frame(np.arange(10)), Settings())
    selected = swing_frequency_summary(blocks, Settings()).iloc[1]
    assert selected.cycles == 0
    assert selected.status == "insufficient complete groups"
    assert np.isnan(selected.period_rms_us)
    assert selected.window_end_s - selected.window_start_s == 20


def test_default_report_and_optional_views(tmp_path):
    from test_suite import clock_frame, swings_frame
    from pendulum_analysis.suite.__main__ import run
    clock_frame(400).drop(columns="source_row").to_csv(tmp_path / "PCPS.CSV", index=False)
    swings_frame(np.arange(150)).drop(columns="source_row").to_csv(tmp_path / "PCSW.CSV", index=False)
    out = tmp_path / "report"
    cfg = Settings(detail_start_hours=0, autocorrelation=True)
    result = run(tmp_path, out, cfg, progress=lambda _: None)
    assert set(result["plots"]) == {"swing_time_series.png", "swing_e0_phase_evolution.png", "swing_detail.png",
                                    "swing_autocorrelation.png", "clock_frequency.png", "clock_stability.png",
                                    "swing_e0_pps_calibrated_phase_polars.png",
                                    "swing_e0_frequency.png"}
    html = (out / "report.html").read_text()
    assert html.count("data:image/png;base64,") == 8
    assert html.index("Pendulum behaviour over time") < html.index("PPS data quality")
    assert "Raw swing characteristics" not in html
    assert "Environmental model validation" not in html
    assert "physical travel direction" in html
    assert "Edge 0→1" in html
    assert "Swing period and environment" in html and "Prediction of later hours" in html
    assert "insufficient hourly coverage" in html
    blocks = pd.read_csv(out / "csv" / "swing_cycles.csv")
    assert blocks.available.all()
    assert blocks.mean_period_s.to_numpy() == pytest.approx(np.full(10, 2.))
    # Generated Markdown tables have aligned separators and outer pipes.
    tables = (out / "report.md").read_text().split("\n\n")
    for paragraph in tables:
        rows = [line for line in paragraph.splitlines() if line.startswith("|")]
        if rows:
            assert all(row.endswith("|") for row in rows)
            assert len({tuple(i for i, char in enumerate(row) if char == "|") for row in rows}) == 1


@pytest.mark.parametrize("values", [{"detail_start_hours": -1}, {"detail_start_hours": np.nan},
                                  {"detail_duration_seconds": 0}, {"detail_duration_seconds": np.inf}])
def test_detail_settings_reject_invalid_coordinates(values):
    with pytest.raises(ValueError):
        Settings(**values)
