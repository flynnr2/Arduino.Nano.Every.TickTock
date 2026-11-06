"""Off-scale display retains data and preserves deterministic phase bands."""
import numpy as np
import pandas as pd
import pytest
from matplotlib import pyplot as plt

from pendulum_analysis.suite.plot_limits import flag_offscale, time_series_limits


def test_both_edges_are_counted_at_original_times_without_changing_values():
    values = np.r_[np.linspace(-1,1,100), -80., 100., np.nan]
    original = values.copy()
    times = np.arange(len(values))*30.
    fig, ax = plt.subplots()
    side, bounds = flag_offscale(ax,times,values)
    assert side[-3:].tolist() == [-1,1,0]
    assert (side == 0).sum() == 101
    markers = ax.collections[0].get_offsets()
    np.testing.assert_allclose(markers[:,0],times[-3:-1])
    np.testing.assert_allclose(markers[:,1],bounds)
    assert "1 above, 1 below" in ax.get_legend_handles_labels()[1][0]
    np.testing.assert_equal(values,original)
    plt.close(fig)


def test_phase_envelope_does_not_hide_regular_impulse_band():
    # Deliberately unequal phase exposure: rare populated phases must stay visible.
    phase = np.r_[np.zeros(5000,int),np.full(21,2)]
    values = np.r_[np.linspace(-1,1,5000),1000+np.linspace(-1,1,21)]
    assert time_series_limits(values) is not None
    assert time_series_limits(values,phase) is None
    values[0] = -500.
    bounds = time_series_limits(values,phase)
    assert bounds[0] > values[0]
    assert bounds[1] > values[phase==2].max()


@pytest.mark.parametrize("values", [[], [np.nan]*30, [2.]*50, [0.]*10+[100.]])
def test_empty_constant_and_small_populations_retain_full_display(values):
    assert time_series_limits(values) is None


def test_scatter_colours_use_sequence_phase_across_gaps(tmp_path,monkeypatch):
    from test_suite import clock_frame, swings_frame
    from pendulum_analysis.suite.clock import analyze_clock
    from pendulum_analysis.suite.swings import analyze_swings
    from pendulum_analysis.suite.common import Settings
    from pendulum_analysis.suite import time_series_plots
    cfg = Settings(phase_origin=5)
    sequence = np.r_[np.arange(32),np.arange(33,150)]
    swing = analyze_swings(swings_frame(sequence),analyze_clock(clock_frame(400),cfg),cfg)
    before = swing.frame.copy(deep=True)
    captured = []
    def inspect(fig,path):
        if path.name == "swing_time_series.png":
            points = fig.axes[0].collections[0]
            captured.append((points.get_offsets().copy(),points.get_array().copy()))
        plt.close(fig)
    monkeypatch.setattr(time_series_plots,"finish",inspect)
    time_series_plots.plot_time_series(swing,cfg,tmp_path)
    eligible = swing.frame.calibrated_valid & np.isfinite(swing.frame.full_pps_s)
    offsets,colours = captured[0]
    assert len(offsets) == int(eligible.sum())
    np.testing.assert_equal(colours,(swing.frame.loc[eligible,"sequence_extended"]-5)%15)
    assert not np.array_equal(colours,np.arange(len(colours))%15)
    pd.testing.assert_frame_equal(before,swing.frame)


def test_default_polar_layout_has_medians_above_untrimmed_jitter(tmp_path,monkeypatch):
    from test_suite import clock_frame, swings_frame
    from pendulum_analysis.suite.clock import analyze_clock
    from pendulum_analysis.suite.swings import analyze_swings
    from pendulum_analysis.suite.common import Settings
    from pendulum_analysis.suite import report
    cfg = Settings()
    swing = analyze_swings(swings_frame(np.arange(150)),analyze_clock(clock_frame(400),cfg),cfg)
    captured = []
    def inspect(fig,path):
        assert len(fig.axes) == 4
        assert fig.axes[0].get_position().x0 < fig.axes[1].get_position().x0
        assert fig.axes[0].get_position().y0 > fig.axes[2].get_position().y0
        for ax,metric,bins in zip(fig.axes[2:],["full","half"],[15,30]):
            data = swing.tables["swing_phase"]
            data = data.loc[data.basis.eq("pps_calibrated") & data.metric.eq(metric)
                            & data.bin_count.eq(bins)].sort_values("phase")
            expected = data["std"].to_numpy()*1e6
            np.testing.assert_allclose([p.get_height() for p in ax.patches],expected[np.isfinite(expected)])
            assert "Jitter: standard deviation" in ax.get_title()
        captured.append(True)
        plt.close(fig)
    monkeypatch.setattr(report,"finish",inspect)
    assert report.plot_calibrated_phase_polars(swing,0,tmp_path/"polars.png")
    assert captured
