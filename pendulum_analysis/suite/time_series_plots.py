"""Time-first calibrated pendulum views; no physical direction is assumed."""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm

from .common import ENV
from .report import finish, BLUE, ORANGE, RED
from .plot_limits import flag_offscale


def _record_offscale(rows, ax, data, times, values, chart, panel, groups=None):
    side, bounds = flag_offscale(ax, times, values, groups)
    if bounds is None:
        return
    low, high = bounds
    for index in np.flatnonzero(side):
        source = data.iloc[index]
        rows.append(dict(chart=chart, panel=panel, epoch=int(source.epoch),
                         source_row=source.get("source_row", np.nan),
                         sequence_extended=source.get("sequence_extended", np.nan),
                         phase15=source.get("phase15", np.nan), cycle=source.get("cycle", np.nan),
                         time_s=float(source.time_s),
                         value=float(np.asarray(values)[index]), display_low=low, display_high=high,
                         side="below" if side[index] < 0 else "above"))


def _cycle_lines(ax, blocks, divisor):
    for _, group in blocks.loc[blocks.available].groupby("series_run"):
        ax.plot(group.time_s / divisor, group.period_error_us, color=BLUE, lw=.7,
                marker="." if len(group) < 3 else None)


def plot_time_series(swing, cfg, directory):
    plots = []
    offscale = []
    f, blocks = swing.frame, swing.tables["swing_cycles"]
    seconds = f.elapsed_cycles / cfg.nominal_hz
    divisor, time_label = (3600, "hours") if seconds.max() < 7 * 86400 else (86400, "days")
    context = swing.tables["swing_environment"]
    channels = [name for name in ENV if name in context and context[name].notna().any()]
    fig, axes = plt.subplots(2 + len(channels), 1, figsize=(14, 7 + 1.6 * len(channels)),
                             sharex=True, layout="constrained")
    eligible = f.calibrated_valid & np.isfinite(f.full_pps_s)
    samples = f.loc[eligible].copy()
    samples["time_s"] = seconds[eligible]
    errors = (samples.full_pps_s - cfg.swing_period_s) * 1e6
    # Categorical colours follow sequence phase, never compressed row positions.
    palette = ListedColormap([plt.get_cmap("tab20").colors[i]
                              for i in list(range(0,20,2)) + list(range(1,10,2))])
    points = axes[0].scatter(samples.time_s / divisor, errors, c=samples.phase15,
                             cmap=palette, norm=BoundaryNorm(np.arange(16)-.5, 15),
                             s=1, alpha=.7, linewidths=0, rasterized=True)
    colourbar = fig.colorbar(points, ax=axes[0], orientation="horizontal", location="top",
                            ticks=range(15), fraction=.08, pad=.12)
    colourbar.set_label("Sequence phase: (extended sequence − origin) mod 15")
    axes[0].set(title="Individual full swings · PPS calibrated", ylabel="Period − nominal (µs)")
    _record_offscale(offscale, axes[0], samples, samples.time_s/divisor, errors,
                     "swing_time_series.png", "individual swings", samples.phase15)
    _cycle_lines(axes[1], blocks, divisor)
    complete = blocks.loc[blocks.available]
    _record_offscale(offscale, axes[1], complete, complete.time_s/divisor, complete.period_error_us,
                     "swing_time_series.png", "15-swing averages")
    axes[1].set(title="Complete 15-swing averages · nonoverlapping", ylabel="Mean period − nominal (µs)")
    if not blocks.available.any():
        axes[1].text(.5, .5, "No complete eligible 15-swing groups", ha="center", transform=axes[1].transAxes)
    for ax, name in zip(axes[2:], channels):
        for _, group in context.groupby("epoch"):
            # Hourly gaps remain visible rather than joining disjoint observations.
            times = np.arange(int(group.hour.min()), int(group.hour.max()) + 1)
            values = group.set_index("hour")[name].reindex(times)
            ax.plot((times + .5) * 3600 / divisor, values, color=BLUE, lw=.8)
        ax.set_ylabel({"temperature_C": "Temperature (°C)", "humidity_pct": "Humidity (%)", "pressure_hPa": "Pressure (hPa)"}[name])
    # Retain long exclusions visibly even though invalid durations are not plotted.
    bad = ~eligible.to_numpy()
    starts = np.flatnonzero(bad & ~np.r_[False, bad[:-1]])
    ends = np.flatnonzero(bad & ~np.r_[bad[1:], False])
    for start, end in zip(starts, ends):
        if seconds.iloc[end] - seconds.iloc[start] >= 30:
            for ax in axes[:2]:
                ax.axvspan(seconds.iloc[start] / divisor, (seconds.iloc[end] + cfg.swing_period_s) / divisor,
                           color=RED, alpha=.1)
    axes[-1].set_xlabel(f"Observed elapsed {time_label} (unknown gaps between epochs are not UTC)")
    for ax in axes:
        ax.grid(alpha=.2)
        ax.ticklabel_format(axis="y", style="plain", useOffset=False)
    name = "swing_time_series.png"
    finish(fig, directory / name)
    plots.append((name, "Pendulum behaviour over time",
                  f"All {len(samples):,} eligible individual PPS-calibrated swings are plotted without sampling, coloured by their sequence-derived phase (gaps do not shift later colours). Each average contains phases 0..14 exactly once; incomplete or excluded groups remain gaps. Timing panels have independent vertical scales. Where the central 99% range plus 10% padding is exceeded, black Xs mark each off-scale point at its actual time and the upper/lower edge; the legend counts them. Individual-swing bounds use the union of per-phase ranges to preserve the impulse bands. This changes display only: statistics and exports retain every eligible point. Off-scale values and identities are in swing_plot_offscale_points.csv. Red spans mark calibration/measurement exclusions lasting at least 30 seconds. Environmental panels show screened hourly PCSW readings without fitted corrections."))

    evolution = swing.tables["swing_phase_evolution"]
    phase = swing.tables["swing_phase"]
    for epoch in sorted(f.epoch.unique()):
        profile = phase[(phase.epoch == epoch) & (phase.basis == "pps_calibrated")
                        & (phase.metric == "full") & (phase.bin_count == 15)].sort_values("phase")
        e = evolution[evolution.epoch == epoch]
        fig, axes = plt.subplots(2, 1, figsize=(13, 8), layout="constrained")
        axes[0].plot(profile.phase, (profile["mean"] - cfg.swing_period_s) * 1e6, ".-", color=BLUE)
        axes[0].set(ylabel="Mean period − nominal (µs)", title=f"Full-swing phase profile · PPS calibrated · epoch {epoch}")
        axes[0].set_xticks(range(15)); axes[0].grid(alpha=.2)
        if len(e):
            width = float(e.bin_seconds.iloc[0])
            bins = np.arange(int(e.time_bin.min()), int(e.time_bin.max()) + 1)
            grid = e.pivot(index="time_bin", columns="phase15", values="phase_deviation_us").reindex(index=bins, columns=range(15))
            limit = max(float(np.nanmax(np.abs(grid.to_numpy()))), .001)
            im = axes[1].pcolormesh(np.arange(16) - .5, np.r_[bins, bins[-1] + 1] * width / divisor,
                                   np.ma.masked_invalid(grid.to_numpy()), cmap="RdBu_r", vmin=-limit, vmax=limit)
            fig.colorbar(im, ax=axes[1], label="Phase deviation from time-bin mean (µs)")
            axes[1].set(title=f"Phase evolution · {width / 3600:g}-hour time groups",
                        ylabel=f"Observed elapsed {time_label}")
        else:
            axes[1].text(.5, .5, "No complete eligible cycles for phase evolution", ha="center", transform=axes[1].transAxes)
        axes[1].set(xlabel="Sequence phase (physical impulse position and travel direction are unassigned)", xticks=range(15))
        name = f"swing_e{epoch}_phase_evolution.png"
        finish(fig, directory / name)
        plots.append((name, f"Epoch {epoch}: repeating pattern and its evolution",
                      "The profile uses every eligible calibrated swing. The heatmap uses complete 15-swing groups with equal phase exposure, subtracting the mean across the 15 phases in each time group. Blank cells have no complete cycles; rate changes remain visible in the separate timeline. Time-group width adapts to recording length (one hour, six hours, one day or one week, up to about 96 groups). Sequence phase establishes neither physical direction nor a measured release/reset event."))

    if cfg.detail_start_hours is not None:
        plots.append(plot_detail(swing, cfg, directory))
    if cfg.autocorrelation:
        fig, ax = plt.subplots(figsize=(11, 5), layout="constrained")
        correlation = swing.tables["swing_autocorrelation"]
        for epoch, group in correlation.groupby("epoch"):
            ax.plot(group.lag_seconds / 60, group.autocorrelation, label=f"Epoch {epoch}")
        if len(correlation):
            ax.legend()
        else:
            ax.text(.5, .5, "Insufficient complete cycles or constant series", ha="center", transform=ax.transAxes)
        ax.set(xlabel="Observed lag (minutes)", ylabel="Autocorrelation", title="Persistence of 15-swing averages · drift retained")
        ax.grid(alpha=.2)
        name = "swing_autocorrelation.png"
        finish(fig, directory / name)
        plots.append((name, "Persistence across impulse cycles",
                      "Optional biased autocorrelation of 15-swing mean periods after removing each epoch's mean. Numerators pair only within uninterrupted eligible cycle runs; the denominator is the full epoch sum of squared deviations. At least 20 pairs support each lag, up to 120 cycles. Lag seconds are the observed mean separation. Drift remains present and no significance bounds are claimed."))
    plots.extend(plot_swing_frequency(swing, directory, divisor, time_label, offscale))
    swing.tables["swing_plot_offscale_points"] = pd.DataFrame(offscale, columns=[
        "chart", "panel", "epoch", "source_row", "sequence_extended", "phase15", "cycle",
        "time_s", "value", "display_low", "display_high", "side"])
    return plots


def plot_swing_frequency(swing, directory, divisor, time_label, offscale):
    plots = []
    blocks = swing.tables["swing_cycles"]
    summary = swing.tables["swing_frequency_summary"]
    for epoch, group in blocks.groupby("epoch"):
        selected = summary.loc[(summary.epoch == epoch) & summary.scope.eq("Central detail window")].iloc[0]
        start, end = selected.window_start_s, selected.window_end_s
        duration_hours = (end-start)/3600
        duration_label = "Four-hour" if np.isclose(duration_hours,4) else f"{duration_hours:.3g}-hour"
        valid = group.loc[group.available]
        fig, axes = plt.subplots(2, 1, figsize=(13, 8), layout="constrained")
        for _, run in valid.groupby("series_run"):
            axes[0].plot(run.time_s/divisor, run.frequency_deviation_ppm, color=BLUE, lw=.7,
                         marker="." if len(run) < 3 else None)
            detail = run.loc[(run.time_s >= start) & (run.time_s < end)]
            axes[1].plot((detail.time_s-start)/3600, detail.frequency_deviation_ppm, ".-", color=BLUE, lw=.7, ms=2)
        name = f"swing_e{epoch}_frequency.png"
        _record_offscale(offscale, axes[0], valid, valid.time_s/divisor, valid.frequency_deviation_ppm,
                         name, "whole epoch frequency")
        detail = valid.loc[(valid.time_s >= start) & (valid.time_s < end)]
        _record_offscale(offscale, axes[1], detail, (detail.time_s-start)/3600, detail.frequency_deviation_ppm,
                         name, "central detail frequency")
        axes[0].set(xlabel=f"Observed elapsed {time_label}", ylabel="Frequency deviation (ppm)",
                    title=f"Pendulum frequency · complete 15-swing averages · epoch {epoch}")
        axes[1].set(xlabel="Hours from window start", ylabel="Frequency deviation (ppm)", xlim=(0,max(duration_hours,1e-6)),
                    title=f"{duration_label} central detail · observed hours {start/3600:.3f}–{end/3600:.3f}")
        if not len(valid):
            axes[0].text(.5,.5,"No complete eligible groups",ha="center",transform=axes[0].transAxes)
        if selected.cycles == 0:
            axes[1].text(.5,.5,"No complete eligible groups in this window",ha="center",transform=axes[1].transAxes)
        else:
            labels = [("Peak to peak", selected.period_peak_to_peak_us), ("RMS", selected.period_rms_us),
                      ("RMS after linear trend removal", selected.detrended_period_rms_us)]
            annotation = "Period variation: " + "; ".join(f"{label} {value:.4g} µs" if np.isfinite(value) else f"{label} unavailable" for label,value in labels)
            axes[1].text(.02,.98,annotation,va="top",transform=axes[1].transAxes,fontsize=9,
                         bbox=dict(facecolor="white",alpha=.85,edgecolor="none"))
        for ax in axes:
            ax.grid(alpha=.2); ax.ticklabel_format(axis="y", style="plain", useOffset=False)
        finish(fig, directory / name)
        plots.append((name, f"Epoch {epoch}: pendulum frequency and {duration_label.lower()} variation",
                      "Frequency = 1 / the complete 15-swing mean period. Fractional frequency offset is relative to the configured nominal period, with the epoch mean offset removed for display. Longer periods give lower frequencies. Both panels retain drift and break at unavailable groups; this is pendulum rate, separate from the capture oscillator. The central 99% range plus 10% padding flags off-scale points with counted black Xs at the chart edge, retaining them in all statistics and CSV exports. The detail is centred in observed time between the first and last complete eligible groups, using four hours or the entire eligible span if shorter. Selection does not search for low scatter or avoid outliers, and gaps remain gaps. This is independent of --detail-start-hours, which controls the optional local component view. Its annotation reports period scatter about the window mean, plus RMS after removing only a fitted straight line; no trend is removed from the plots."))
    return plots


def plot_detail(swing, cfg, directory):
    start = cfg.detail_start_hours * 3600
    time = swing.frame.elapsed_cycles / cfg.nominal_hz
    f = swing.frame.loc[(time >= start) & (time < start + cfg.detail_duration_seconds)].copy()
    seconds = f.elapsed_cycles / cfg.nominal_hz - start
    # Break lines at gaps, exclusions and epoch boundaries; physical travel is unknown.
    breaks = f.epoch.ne(f.epoch.shift()) | f.sequence_extended.diff().ne(1) | ~f.calibrated_valid | ~f.calibrated_valid.shift(fill_value=False)
    run = breaks.cumsum()
    fig, axes = plt.subplots(3, 1, figsize=(13, 9), sharex=True, layout="constrained")
    for _, indices in f.groupby(run).groups.items():
        group = f.loc[indices]
        x = seconds.loc[indices]
        valid = group.calibrated_valid
        axes[0].plot(x, ((group.full_pps_s - cfg.swing_period_s) * 1e6).where(valid), ".-", color=BLUE, lw=.8)
        for ax, components in ((axes[1], (("tick_open", "Edge 0→1", BLUE), ("tock_open", "Edge 2→3", ORANGE))),
                               (axes[2], (("tick_blocked", "Edge 1→2", BLUE), ("tock_blocked", "Edge 3→4", ORANGE)))):
            for name, label, color in components:
                ax.plot(x, (group[name + "_pps_s"] * 1000).where(valid), ".-", color=color, lw=.8, label=label)
    axes[0].set(title=f"Local swing dynamics · PPS calibrated · start {cfg.detail_start_hours:g} h", ylabel="Full period − nominal (µs)")
    axes[1].set(ylabel="Open interval (ms)")
    axes[2].set(ylabel="Blocked interval (ms)", xlabel="Seconds from requested window start")
    for ax in axes:
        ax.set_xlim(0, cfg.detail_duration_seconds); ax.grid(alpha=.2)
        ax.ticklabel_format(axis="y", style="plain", useOffset=False)
    for ax in axes[1:]:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            unique = dict(zip(labels, handles)); ax.legend(unique.values(), unique.keys())
    if not f.calibrated_valid.any():
        axes[0].text(.5, .5, "No eligible calibrated swings in this window", ha="center", transform=axes[0].transAxes)
    name = "swing_detail.png"
    finish(fig, directory / name)
    return name, "Selected interval: swing dynamics", "Individual complete swings and their four edge intervals, with gaps at exclusions, missing sequences and epoch boundaries. Edge labels preserve acquisition order; travel direction is unassigned. This is an offline calibrated view."
