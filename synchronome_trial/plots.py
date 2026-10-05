"""Scientific figures for the six exploratory investigations."""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from .data import METRICS

BLUE, ORANGE, RED, GREY = "#235a99", "#b96816", "#b33b47", "#7b8693"


def generate(out, frame, cycles, templates, adjusted, flag_profile, flag_history, events,
             event_summary, event_profiles, views, periods, period_profile,
             period_cycles, acf, pairs, folds, spectra, hourly, predictions, config):
    out.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.alpha": .18, "figure.facecolor": "white"})
    plots = []

    def save(fig, name, section, caption):
        fig.savefig(out/name, dpi=145, bbox_inches="tight")
        plt.close(fig)
        plots.append(dict(name=name, section=section, caption=caption))

    def line(ax, data, name, color=BLUE, label=None, time="day", centre=False):
        # Never connect across unavailable groups, sequence gaps or epochs.
        if data.empty or name not in data:
            return
        ycentre = data[name].mean() if centre else 0
        keys = ["epoch", "series_run"] if "series_run" in data else ["epoch"]
        first = True
        for _, group in data.groupby(keys, sort=False):
            ordinal = next((name for name in ("sequence_extended", "cycle", "hour") if name in group), None)
            ids = np.cumsum(np.r_[True, np.diff(group[ordinal]) != 1]) if ordinal else np.zeros(len(group), int)
            for segment in np.unique(ids):
                part = group.iloc[np.flatnonzero(ids == segment)]
                ax.plot(part[time], part[name]-ycentre, color=color, lw=.7,
                        label=label if first else None)
                first = False

    c = cycles.loc[cycles.available]
    fig, axes = plt.subplots(4, 1, figsize=(12, 9), sharex=True, layout="constrained")
    line(axes[0], c, "period_us", RED); axes[0].set_ylabel("Full period − 2 s (µs)")
    line(axes[1], c, "difference_us", BLUE, centre=True); axes[1].set_ylabel("Half difference\nchange (µs)")
    line(axes[2], c, "tick_block_us", BLUE, "Tick flag")
    line(axes[2], c, "tock_block_us", ORANGE, "Tock flag"); axes[2].set_ylabel("Flag duration (µs)"); axes[2].legend()
    line(axes[3], c, "temperature_C", ORANGE); axes[3].set_ylabel("Temperature (°C)")
    for event in events:
        for ax in axes:
            ax.axvline(event["time_s"]/86400, color=GREY, lw=.7, ls="--")
        axes[0].text(event["time_s"]/86400, .98, event["event_id"], transform=axes[0].get_xaxis_transform(), va="top")
    axes[0].set_title("Complete 15-swing means; selected event candidates")
    axes[-1].set_xlabel("Observed elapsed days; unknown epoch gaps are not UTC")
    save(fig, "01_overview.png", "overview", "Every complete calibrated cycle. Flag durations retain their absolute scale; the half difference is mean-centred. Dashed lines identify exploratory candidates, not externally observed events.")

    for epoch, p in templates.groupby("epoch"):
        flag = flag_profile.loc[flag_profile.epoch.eq(epoch)]
        period = p.loc[p.metric.eq("period_us")].sort_values("phase15")
        fig, axes = plt.subplots(3, 1, figsize=(12, 8), layout="constrained")
        axes[0].plot(period.phase15, period.offset_us, "o-", color=BLUE)
        axes[0].set_ylabel("Period phase offset (µs)"); axes[0].set_xticks(range(15))
        axes[0].set_title(f"Epoch {epoch}: fixed initial baseline and interleaved flags")
        for side, color in (("tick", BLUE), ("tock", ORANGE)):
            part = flag.loc[flag.side.eq(side)]
            axes[1].plot(part.position30, part.relative_speed_pct, "o-", color=color, label=side)
        axes[1].set_ylabel("Relative passage speed (%)"); axes[1].set_xlabel("30 half-swing positions (each side normalised separately)")
        axes[1].legend()
        cumulative = np.r_[0., np.cumsum(period.offset_us)]
        axes[2].plot(range(16), cumulative, "o-", color=RED)
        axes[2].set_ylabel("Accumulated timing\ndeviation (µs)"); axes[2].set_xlabel("Boundary after each sequence phase")
        if config.impulse_phase is not None:
            axes[0].axvline(config.impulse_phase, color=GREY, ls="--")
            axes[1].axvline(2*config.impulse_phase+(config.impulse_side == "tock"), color=GREY, ls="--")
        save(fig, f"02_phase_e{epoch}.png", "phase", "The period template comes only from the initial baseline. The flag profile uses all complete cycles and distinguishes directions. Accumulated offsets describe the periodic timing pattern; they are not an independent measurement of the physical impulse phase or a free-pendulum phase kick.")
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, layout="constrained")
    for epoch, f in adjusted.groupby("epoch"):
        axes[0].scatter(f.day, f.period_us, color=GREY, s=.25, alpha=.6)
        axes[0].scatter(f.day, f.period_us_adjusted, color=BLUE, s=.25, alpha=.7)
        axes[1].scatter(f.day, f.flag_mean_us_adjusted-f.flag_mean_us_adjusted.mean(), color=ORANGE, s=.25)
    axes[0].set_title("Frozen baseline phase correction; every calibrated swing")
    axes[0].set_ylabel("Period − 2 s (µs)")
    axes[1].set_ylabel("Mean flag, adjusted\nchange from mean (µs)"); axes[1].set_xlabel("Observed elapsed days")
    save(fig, "03_phase_adjusted.png", "phase", "Grey: raw measured period; blue: period after subtracting the fixed initial phase offsets. The lower panel shows adjusted flag duration. No rolling template learns away later changes; individual points are retained without clipping.")

    for epoch, profile in flag_profile.groupby("epoch"):
        fig, axes = plt.subplots(3, 1, figsize=(12, 9), layout="constrained")
        for side, color in (("tick", BLUE), ("tock", ORANGE)):
            p = profile.loc[profile.side.eq(side)]
            y = p.cycle_normalized_speed_mean_pct
            sd = p.cycle_normalized_speed_std_pct
            axes[0].plot(p.position30, y, "o-", color=color, label=side)
            axes[0].fill_between(p.position30, y-sd, y+sd, color=color, alpha=.12)
            axes[1].plot(p.position30, sd, "o-", color=color, label=side)
        axes[0].set_title(f"Epoch {epoch}: flag-cycle structure and scatter after removing overall speed changes")
        axes[0].set_ylabel("Relative speed (%)"); axes[0].legend()
        axes[1].set_ylabel("Within-position\nspeed scatter (%)"); axes[1].set_xlabel("30 half-swing positions")
        history = flag_history.loc[flag_history.epoch.eq(epoch)]
        if len(history):
            # Missing hours remain blank in their actual elapsed positions.
            p = history.pivot(index="position30", columns="hour", values="within_hour_speed_pct")
            p = p.reindex(columns=range(int(p.columns.min()), int(p.columns.max())+1))
            limit = float(np.nanmax(np.abs(p.to_numpy())))
            im = axes[2].imshow(p.to_numpy(), origin="lower", aspect="auto", cmap="RdBu_r", vmin=-limit, vmax=limit,
                extent=[p.columns.min()/24, (p.columns.max()+1)/24, -.5, 29.5])
            axes[2].grid(False); fig.colorbar(im, ax=axes[2], label="Within-hour relative speed (%)")
        axes[2].set_xlabel("Observed elapsed days"); axes[2].set_ylabel("Half-swing position")
        save(fig, f"03_flags_e{epoch}.png", "flags", "Top: each flag is normalised by its own direction's mean flag duration within the same complete cycle; shading shows ±1 standard deviation of observed scatter, not uncertainty of the mean. Middle: that scatter by position. Bottom: hourly relative profiles remove each direction's overall hourly speed change, preserving changes of cycle shape. Hours with under 60 complete cycles remain blank; no physical impulse direction is assigned.")

    for event in events:
        eid, t, epoch = event["event_id"], event["time_s"], event["epoch"]
        view = views[eid]
        local = c.loc[c.epoch.eq(epoch) & c.time_s.between(t-config.event_view_seconds, t+config.event_view_seconds)].copy()
        local["relative_minutes"] = (local.time_s-t)/60
        fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True, layout="constrained")
        axes[0].scatter(view.relative_minutes, view.period_us_adjusted, s=2, color=GREY, alpha=.4, label="Adjusted individual swings")
        line(axes[0], local, "period_us", RED, "Complete-cycle mean", "relative_minutes")
        axes[0].set_ylabel("Period − 2 s (µs)"); axes[0].legend(loc="upper right")
        line(axes[1], local, "difference_us", BLUE, time="relative_minutes", centre=True); axes[1].set_ylabel("Half difference\nlocal change (µs)")
        line(axes[2], local, "tick_block_us", BLUE, "Tick flag", "relative_minutes")
        line(axes[2], local, "tock_block_us", ORANGE, "Tock flag", "relative_minutes"); axes[2].set_ylabel("Flag duration (µs)"); axes[2].legend()
        line(axes[3], local, "temperature_C", ORANGE, time="relative_minutes"); axes[3].set_ylabel("Temperature (°C)")
        guard, width = config.event_guard_seconds/60, config.event_baseline_seconds/60
        for ax in axes:
            ax.axvline(0, color=GREY, ls="--", lw=.8)
            ax.axvspan(-guard-width, -guard, color=BLUE, alpha=.07)
            ax.axvspan(guard, guard+width, color=ORANGE, alpha=.07)
        axes[0].set_title(f"{eid}: {t/3600:.3f} elapsed hours; trigger {event['trigger']}")
        axes[-1].set_xlabel("Minutes relative to selected candidate; blue/orange shade = comparison windows")
        save(fig, f"event_{eid}.png", "events", f"{eid}: the phase correction uses complete cycles only in the shaded pre-event window. Cycle means provide 30-second detail; adjusted individual swings provide two-second detail. Candidate timing and averaging do not establish exact physical onset or causality.")
        if not event_profiles.empty:
            p = event_profiles.loc[event_profiles.event_id.eq(eid)]
            fig, axes = plt.subplots(2, 1, figsize=(12, 6.5), layout="constrained")
            for label, color in (("before", BLUE), ("after", ORANGE)):
                period = p.loc[p.window.eq(label) & p.metric.eq("period_us")].sort_values("phase15")
                axes[0].plot(period.phase15, period.mean_us, "o-", color=color, label=label)
                for side, marker in (("tick", "o"), ("tock", "s")):
                    flag = p.loc[p.window.eq(label) & p.metric.eq(side+"_block_us")].sort_values("phase15")
                    axes[1].plot(2*flag.phase15+(side == "tock"), flag.mean_us, marker+"-", color=color, label=f"{label} {side}")
            axes[0].set_ylabel("Period − 2 s (µs)"); axes[0].set_xlabel("Sequence phase modulo 15"); axes[0].legend()
            axes[1].set_ylabel("Flag duration (µs)"); axes[1].set_xlabel("30 half-swing positions"); axes[1].legend(ncol=2)
            axes[0].set_title(f"{eid}: phase profiles before and after, with equal exposure")
            save(fig, f"event_{eid}_profiles.png", "events", f"{eid}: absolute mean profiles preserve both the overall displacement and changes of shape. Each comparison window contains complete cycles; standard deviations and counts are in the CSV, without treating correlated swings as independent replicates.")

    if not period_profile.empty:
        for epoch, p in period_profile.groupby("epoch"):
            fig, axes = plt.subplots(2, 1, figsize=(12, 7), layout="constrained")
            for name, color in zip(("e0", "e1", "e2", "e3"), (BLUE, ORANGE, RED, GREY)):
                axes[0].plot(p.phase15, p["pps_"+name+"_us"], "o-", color=color, label=name)
            axes[0].set_xlabel("Sequence phase modulo 15"); axes[0].set_ylabel("Period − 2 s (µs)"); axes[0].legend(ncol=4)
            pc = period_cycles.loc[period_cycles.epoch.eq(epoch)]
            line(axes[1], pc, "edge_spread_us")
            axes[1].set_xlabel("Observed elapsed days"); axes[1].set_ylabel("Spread of four cycle\nmeans (µs)")
            axes[0].set_title(f"Epoch {epoch}: four homologous-edge periods, one matched population")
            save(fig, f"04_periods_e{epoch}.png", "periods", "Each period spans consecutive occurrences of the same edge type. Different starting phases can legitimately give different instantaneous periods. Complete-cycle comparisons require all 15 adjacent pairs; missing rows are never bridged.")
    period_names = ("e0", "e1", "e2", "e3", "tick_mid", "tock_mid")
    period_colours = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b")
    event_periods = [periods.loc[periods.epoch.eq(event["epoch"]) & periods.pair_valid
                    & periods.time_s.between(event["time_s"]-90, event["time_s"]+90)]
                    for event in events]
    finite_values = [local[["pps_"+name+"_us" for name in period_names]].to_numpy().ravel()
                     for local in event_periods if not local.empty]
    limits = None
    if finite_values:
        values = np.concatenate(finite_values)
        values = values[np.isfinite(values)]
        if len(values):
            lower, upper = values.min(), values.max()
            margin = max((upper-lower)*.05, 1)
            limits = (lower-margin, upper+margin)
    strongest = max(events, key=lambda x: x["score"] if x["score"] is not None else 0) if events else None
    for event, local in zip(events, event_periods):
        t = event["time_s"]
        fig, ax = plt.subplots(figsize=(12, 5), layout="constrained")
        segments = [] if local.empty else list(local.groupby(np.cumsum(np.r_[True, np.diff(local.sequence_extended) != 1])))
        for name, color in zip(period_names, period_colours):
            first = True
            for _, group in segments:
                ax.plot((group.time_s-t), group["pps_"+name+"_us"], ".-", lw=.8, ms=4,
                        color=color, label=name if first else None)
                first = False
        ax.axvline(0, color=GREY, linestyle="--", lw=.8)
        ax.set_xlim(-90, 90)
        if limits is not None:
            ax.set_ylim(*limits)
        ax.set_xlabel("Seconds relative to selected candidate"); ax.set_ylabel("Period − 2 s (µs)"); ax.legend(ncol=3)
        ax.set_title(f"{event['event_id']}: edge and flag-midpoint periods at {t/3600:.3f} elapsed hours")
        name = "05_period_detail.png" if event is strongest else f"05_period_detail_{event['event_id']}.png"
        save(fig, name, "periods", f"{event['event_id']}: all six estimates use the same valid adjacent-record population. All event detail charts share the ±90-second window, colours and vertical scale. Zero marks the selected candidate cycle, not a verified physical onset. Flag-midpoint periods average their endpoint times; they are an additional view, not an independently verified physical centre crossing.")

    if not acf.empty:
        fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, layout="constrained")
        for ax, mode in zip(axes, ("linear drift removed", "successive differences")):
            for name, color in zip(("period_us", "difference_us", "flag_mean_us"), (RED, BLUE, ORANGE)):
                for epoch, p in acf.loc[acf.metric.eq(name) & acf['mode'].eq(mode)].groupby("epoch"):
                    ax.plot(p.lag_s/60, p.correlation, color=color, label=f"{name.replace('_us','')} e{epoch}")
            ax.axhline(0, color=GREY, lw=.8); ax.set_ylabel("Correlation"); ax.set_title(mode); ax.legend(ncol=3)
        axes[-1].set_xlabel("Lag in minutes; pairs never cross unavailable cycles")
        save(fig, "06_cycle_correlations.png", "modulation", "Linear drift is removed separately from each uninterrupted run. Successive differences suppress slow movement but themselves alter correlation: even white measurement noise produces negative lag-one correlation after differencing. No significance thresholds are inferred.")
    if not pairs.empty:
        fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True, layout="constrained")
        for ax, name, color in zip(axes, ("period_us", "difference_us", "flag_mean_us"), (RED, BLUE, ORANGE)):
            for epoch, p in pairs.loc[pairs.metric.eq(name)].groupby("epoch"):
                p = p.copy()
                p["hour"] = (p.time_s//3600).astype(int)
                means = p.groupby("hour").agg(time_s=("time_s", "mean"), contrast=("odd_minus_even", "mean"), pairs=("pair", "size"))
                means = means.loc[means.pairs.ge(30)]
                for _, segment in means.groupby(np.cumsum(np.r_[True, np.diff(means.index) != 1])):
                    ax.plot(segment.time_s/86400, segment.contrast, "o-", ms=3, lw=.8, color=color, label=f"epoch {epoch}")
            ax.axhline(0, color=GREY, lw=.7); ax.set_ylabel(name.replace("_us", "")+"\nodd − even (µs)")
        axes[0].set_title("Adjacent even/odd cycle contrast through time; hourly means")
        axes[-1].set_xlabel("Observed elapsed days; at least 30 valid original-cycle pairs per hour")
        save(fig, "07_cycle_alternation.png", "modulation", "Original-cycle parity is retained. Opposite signs at different times can cancel in the recording-wide mean, so the hourly contrasts are also shown. Each pair is adjacent within one uninterrupted run. This is an alternation diagnostic, not a roller diagnosis.")
    if not folds.empty:
        fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True, layout="constrained")
        for ax, name, color in zip(axes, ("period_us", "difference_us", "flag_mean_us"), (RED, BLUE, ORANGE)):
            for epoch, p in folds.loc[folds.metric.eq(name)].groupby("epoch"):
                ax.plot(p.position20, p['mean'], "o-", color=color, label=f"epoch {epoch}")
            ax.set_ylabel(name.replace("_us", "")+" (µs)")
        axes[0].set_title("Declared 20-cycle fold (~10 minutes), linear drift removed")
        axes[-1].set_xlabel("Cycle number modulo 20; physical roller orientation is unverified")
        save(fig, "07_twenty_cycle_fold.png", "modulation", "The 20-cycle trial is motivated by the thread rather than selected to maximise agreement. Position is based on original cycle numbers, not compressed row numbers. A profile does not prove a repeating mechanical defect.")
    if not spectra.empty:
        fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True, layout="constrained")
        for ax, name, color in zip(axes, ("period_us", "difference_us", "flag_mean_us"), (RED, BLUE, ORANGE)):
            for epoch, p in spectra.loc[spectra.metric.eq(name) & spectra.period_s.between(60, 3600)].groupby("epoch"):
                ax.plot(p.period_s/60, p.power_fraction*100, color=color, lw=.8, label=f"epoch {epoch}")
            ax.axvline(10, color=GREY, ls="--", lw=.8); ax.set_xscale("log"); ax.set_ylabel("Power per bin (%)")
            ax.set_title(name.replace("_us", "")); ax.legend()
        axes[-1].set_xlabel("Period in minutes; dashed line = 10-minute hypothesis")
        save(fig, "08_cycle_spectra.png", "modulation", "Hann-window spectra use only the longest uninterrupted complete-cycle run per epoch, with linear drift removed. Power is a fraction of all positive-frequency power. Thirty-second cycle sampling cannot resolve the thread's 8 Hz rod mode; peaks have no significance or causal interpretation attached.")

    h = hourly.loc[hourly.eligible] if not hourly.empty else hourly
    if len(h):
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), layout="constrained")
        for label, color, marker in (("warming", RED, "^"), ("cooling", BLUE, "v"), ("approximately steady", GREY, "o")):
            p = h.loc[h.direction_label.eq(label)]
            axes[0, 0].scatter(p.temperature_C, p.period_us, s=24, color=color, marker=marker, label=label)
        axes[0, 0].set_xlabel("Temperature (°C)"); axes[0, 0].set_ylabel("Period − 2 s (µs)"); axes[0, 0].legend()
        im = axes[0, 1].scatter(h.density_kg_m3, h.period_us, c=h.day, cmap="viridis", s=25)
        axes[0, 1].set_xlabel("Moist air density (kg/m³)"); axes[0, 1].set_ylabel("Period − 2 s (µs)")
        fig.colorbar(im, ax=axes[0, 1], label="Elapsed day")
        axes[1, 0].scatter(h.speed_proxy_pct, h.period_us, c=h.day, cmap="viridis", s=25)
        axes[1, 0].set_xlabel("Relative speed proxy (%)"); axes[1, 0].set_ylabel("Period − 2 s (µs)")
        if not predictions.empty:
            for label, color in (("Density", GREY), ("Temperature + humidity + pressure", BLUE), ("All three + speed proxy", ORANGE)):
                p = predictions.loc[predictions.response.eq("period_us") & predictions.label.eq(label)]
                for epoch, part in p.groupby("epoch"):
                    line(axes[1, 1], part, "fit_residual_us", color, label+f" e{epoch}")
            axes[1, 1].legend(fontsize=8)
        axes[1, 1].set_xlabel("Observed elapsed days"); axes[1, 1].set_ylabel("Joint-fit residual (µs)")
        fig.suptitle("Environmental context and residuals; model comparisons use a common hourly population")
        save(fig, "09_environment.png", "environment", "Scatter plots provide context; fitted tests are multivariable. Warming/cooling uses ±0.05 °C/hour. Speed is inverse mean flag duration relative to the initial baseline and can also reflect sensor geometry. Residuals here use whole-record fits; prediction validation is shown separately.")
    if not predictions.empty:
        for response in ("period_us", "difference_us"):
            fig, ax = plt.subplots(figsize=(12, 5.5), layout="constrained")
            labels = (("Density", GREY), ("Temperature + humidity + pressure", BLUE),
                      ("All three + speed proxy", ORANGE), ("All three + speed + thermal direction", RED))
            for label, color in labels:
                p = predictions.loc[predictions.response.eq(response) & predictions.label.eq(label) & predictions.validation_split.eq("later testing")]
                for epoch, part in p.groupby("epoch"):
                    line(ax, part, "test_prediction_us", color, f"{label} e{epoch}")
            reference = predictions.loc[predictions.response.eq(response) & predictions.label.eq("Density") & predictions.validation_split.eq("later testing")]
            for epoch, part in reference.groupby("epoch"):
                line(ax, part, "observed_us", "black", f"Observed e{epoch}")
                line(ax, part, "test_constant_us", GREY, f"Training mean e{epoch}")
            ax.set_title(response.replace("_us", "")+": later 30% held out from fitting")
            ax.set_xlabel("Observed elapsed days"); ax.set_ylabel("Observed / predicted (µs)"); ax.legend(fontsize=8, ncol=2)
            save(fig, f"10_validation_{response}.png", "environment", "Every coefficient, centre and scale is determined using only the earlier 70% of common eligible hourly observations. The later 30% is used for prediction checks. All models receive the same population; out-of-training-range fractions are reported.")
    return plots
