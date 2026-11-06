"""Motion figures and an embedded interval explorer for a single epoch."""
from html import escape
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from .plots import BLUE, ORANGE, save, broken
from .rendering import fragment, image, safe_json, pack_columns, CONTROL_STYLE
from ..suite.motion import SIDES
from ..phase_colors import PHASE_COLORS, phase_colors, phase_ticks, phase_legend, phase_legend_html

INLINE_LIMIT = 100_000
CHUNK_ROWS = 16_384


def individual_payload(observations, epoch, directory):
    """Keep long-run browser memory bounded; local scripts also work over file://."""
    if len(observations) <= INLINE_LIMIT:
        return packed(observations)
    chunks = []
    for i, start in enumerate(range(0, len(observations), CHUNK_ROWS)):
        part = observations.iloc[start:start+CHUNK_ROWS]
        identity = f"motion-e{epoch}-{i}"
        filename = f"swing_e{epoch}_motion_chunk_{i:04d}.js"
        payload = safe_json(dict(id=identity, packed=packed(part)))
        (directory/filename).write_text("document.dispatchEvent(new CustomEvent('pendulum-motion-chunk',{detail:"+payload+"}));")
        chunks.append(dict(id=identity, src="plots/"+filename,
                           start=float(part.start_s.iloc[0]), end=float(part.start_s.iloc[-1]), rows=len(part)))
    return dict(chunks=chunks, points=len(observations), max_chunks=4)

COLORS = ('#333333', '#777777')
CAPTION = ("Passage speed = effective blocked path / calibrated blocked duration. Estimated semi-amplitude "
           "is the centre-to-peak harmonic angle vP/(2πr), assuming a gate near bottom dead centre. "
           "The two estimates share the same optical observations. Tick/tock identify acquisition order; physical left/right is unassigned.")
CONTROLS = '''<h3>Inspect an interval</h3><div class="controls">
<label>Measurements<select id="sampling"><option value="individual">Individual passages</option><option value="cycles">Complete 15-swing averages</option></select></label>
<label>Display<select id="units"><option value="absolute">mm/s and degrees</option><option value="relative">Change from directional mean (%)</option></select></label>
<label>Start (elapsed days)<input id="start" type="number" step="any"></label>
<label>Duration (minutes)<input id="duration" type="number" step="any" min="0.001"></label>
<button id="apply" type="button">Apply interval</button><button id="detail" type="button">90-second detail</button>
<button id="all" type="button">Whole epoch</button><button id="earlier" type="button">Earlier</button><button id="later" type="button">Later</button></div>
<p id="status" class="status" role="status"></p>
<canvas id="motion-chart" role="img" aria-label="Passage speed and estimated semi-amplitude over the selected interval"></canvas>
<p id="hover" class="meta"></p><div id="selected-stats" class="stats"></div>
<p class="meta">Individual passages use their full-swing phase colour: circles for tick, triangles for tock. Solid and dashed neutral lines identify tick and tock complete-cycle means, which combine all 15 phases. All values enter statistics; the interactive display preserves first, last, minimum and maximum per phase within each screen column. Baselines are separate whole-epoch directional means. Gaps remain unfilled.</p>'''


def draw_series(axes, observations, cycles, detail=False, start=0, duration=90):
    for side, color in zip(SIDES, COLORS):
        marker, linestyle = ('o', '-') if side == 'tick' else ('^', '--')
        t = observations[side + "_time_s"].to_numpy()
        run = observations.series_run.to_numpy()
        for ax, metric in zip(axes[:2], ("velocity_mm_s", "amplitude_deg")):
            values = observations[side + "_" + metric].to_numpy()
            if detail:
                x, y = broken(t, values, run)
                ax.plot(x*86400-start, y, linestyle=linestyle, color=color, lw=.5, alpha=.5)
                ax.scatter(t-start, values, c=phase_colors(observations.phase15), marker=marker, s=17, label=side.title(), zorder=3)
            else:
                ax.scatter(t/86400, values, s=.6, alpha=.65, c=phase_colors(observations.phase15), marker=marker, linewidths=0, rasterized=True)
                x, y = broken(cycles.time_s.to_numpy(), cycles[side + "_" + metric].to_numpy(), cycles.series_run.to_numpy())
                ax.plot(x, y, color=color, linestyle=linestyle, lw=.9, marker=marker if len(cycles)<=100 else None,
                        ms=4, label=side.title()+" · 15-swing mean")
        if detail:
            x, y = broken(t, observations[side + "_blocked_ms"].to_numpy(), run)
            axes[2].plot(x*86400-start, y, linestyle=linestyle, color=color, lw=.5, alpha=.5)
            axes[2].scatter(t-start, observations[side + "_blocked_ms"], c=phase_colors(observations.phase15), marker=marker, s=17, zorder=3)
    axes[0].set_ylabel("Passage speed (mm/s)")
    axes[1].set_ylabel("Estimated semi-amplitude (°)")
    if detail:
        axes[0].legend(handles=[Line2D([],[],color='#555555',marker=marker,linestyle=style,label=side.title(),lw=.6)
                               for side,marker,style in (('tick','o','-'),('tock','^','--'))],
                       frameon=False,fontsize=9,loc='upper left')
    else:
        axes[0].legend(frameon=False, fontsize=9, loc="upper left")
    if detail:
        x, y = broken(observations.start_s.to_numpy(), (observations.period_s.to_numpy()-observations.attrs["nominal_s"])*1e6,
                      observations.series_run.to_numpy())
        axes[3].plot(x*86400-start, y, color='#777777', lw=.5)
        axes[3].scatter(observations.start_s-start, (observations.period_s-observations.attrs['nominal_s'])*1e6,
                        c=phase_colors(observations.phase15), s=17, zorder=3)
        axes[2].set_ylabel("Blocked duration (ms)")
        axes[3].set_ylabel("Period − nominal (µs)")
        axes[-1].set(xlabel=f"Seconds after elapsed hour {start/3600:g}", xlim=(0, duration))
        if not observations.available.any():
            axes[0].text(.5, .5, "No eligible passages in this interval", ha="center", transform=axes[0].transAxes)
    else:
        axes[-1].set_xlabel("Observed elapsed days")
    for ax in axes:
        ax.grid(axis="y", alpha=.17)


def static_figures(observations, cycles, phase, evolution, predictions, epoch, cfg, directory):
    prefix = f"swing_e{epoch}_motion_"
    used, captions = [], []
    def finish(fig, suffix, title, caption):
        filename = prefix+suffix+".png"
        save(fig, directory/filename)
        used.append(filename)
        captions.append((filename, title, caption))
    fig, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
    draw_series(axes, observations, cycles)
    phase_legend(fig)
    fig.subplots_adjust(top=.84)
    axes[0].set_title(f"Epoch {epoch} · passage speed and estimated semi-amplitude")
    finish(fig, "time_series", "Velocity and amplitude over time", "Every eligible passage and complete-cycle mean; transform each passage before averaging. "+CAPTION)
    start = cfg.detail_start_hours*3600 if cfg.detail_start_hours is not None else float(observations.start_s.min())
    duration = cfg.detail_duration_seconds
    selected = observations.loc[observations.start_s.between(start-cfg.swing_period_s, start+duration)].copy()
    selected.attrs["nominal_s"] = cfg.swing_period_s
    fig, axes = plt.subplots(4, 1, figsize=(14, 10), sharex=True)
    draw_series(axes, selected, cycles, True, start, duration)
    phase_legend(fig)
    fig.subplots_adjust(top=.88)
    axes[0].set_title(f"Epoch {epoch} · local motion detail")
    finish(fig, "detail", "Local motion detail", "The configured detail window, or the first observed window by default. Blocked durations and full periods use the same source records. Impulse-overlapping passages need not represent a free-swing amplitude.")
    fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
    for ax, metric, unit in zip(axes, ("velocity_mm_s", "amplitude_deg"), ("Passage speed (mm/s)", "Estimated semi-amplitude (°)")):
        for side, color in zip(SIDES, COLORS):
            g = phase.loc[phase.metric.eq(metric) & phase.side.eq(side)].sort_values("phase")
            marker, linestyle = ('o', '-') if side == 'tick' else ('^', '--')
            ax.plot(g.phase, g["mean"], color=color, linestyle=linestyle, label=side.title(), lw=.7)
            ax.vlines(g.phase, g.p05, g.p95, colors=phase_colors(g.phase, half=True), alpha=.5, lw=2)
            ax.scatter(g.phase, g['mean'], c=phase_colors(g.phase, half=True), marker=marker, s=25, zorder=3)
        ax.set_ylabel(unit)
        ax.grid(axis="y", alpha=.17)
    axes[0].legend(frameon=False, loc="upper left")
    axes[-1].set(xlabel="Analysis half phase (30 per replenishment cycle)", xticks=np.arange(30))
    phase_ticks(axes[-1], half=True)
    if phase.empty:
        axes[0].text(.5, .5, "No complete eligible cycles", ha="center", transform=axes[0].transAxes)
    finish(fig, "phase", "Motion through the replenishment cycle", "Mean and observed 5th–95th percentiles, using complete cycles with equal phase exposure. Coloured intervals show passage spread, not a confidence interval. Half phases 2p and 2p+1 share full-swing phase p's colour. Circles identify source tick; triangles identify source tock. Phase zero follows the report's per-epoch alignment status; no exact contact instant is measured.")
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    for ax, metric in zip(axes, ("velocity_mm_s", "amplitude_deg")):
        g = evolution.loc[evolution.metric.eq(metric)]
        ax.set(ylabel="Half phase", title=("Passage speed" if metric == "velocity_mm_s" else "Estimated semi-amplitude")+" · pattern relative to each side's time-bin mean")
        if len(g):
            lo, hi = int(g.time_bin.min()), int(g.time_bin.max())
            values = g.pivot(index="phase", columns="time_bin", values="change_pct").reindex(index=range(30), columns=range(lo, hi+1)).to_numpy()
            limit = max(float(np.nanmax(abs(values))), .001)
            width = float(g.bin_seconds.iloc[0])
            mesh = ax.imshow(np.ma.masked_invalid(values), aspect="auto", origin="lower", cmap="RdBu_r", vmin=-limit, vmax=limit,
                             extent=(lo*width/86400, (hi+1)*width/86400, -.5, 29.5))
            fig.colorbar(mesh, ax=ax, label="Deviation (%)")
        else:
            ax.text(.5, .5, "No complete eligible cycles", ha="center", transform=ax.transAxes)
        ax.set_yticks(np.arange(30))
        ax.tick_params(axis='y', labelsize=7)
        phase_ticks(ax, half=True, axis='y')
    axes[-1].set_xlabel("Observed elapsed days")
    finish(fig, "evolution", "Motion phase evolution", "Complete cycles only. Blank cells have no eligible data. Each time bin is centred separately by side; the absolute timeline retains overall drift. All colour ranges belong to this epoch.")
    fig, ax = plt.subplots(figsize=(11, 5.5))
    g = cycles.loc[cycles.available]
    if len(g):
        ax.scatter(g.amplitude_squared_rad2*1e6, g.period_error_us, s=2, alpha=.22, color=BLUE, label="Complete cycles", rasterized=True)
    p = predictions.loc[predictions.model.eq("amplitude")].sort_values("amplitude_squared_rad2")
    if len(p):
        ax.plot(p.amplitude_squared_rad2*1e6, p.fitted_us, color=ORANGE, label="Hourly descriptive fit", lw=1.2)
    ax.set(xlabel="Mean squared estimated semi-amplitude (mrad²)", ylabel="Mean full period − nominal (µs)",
           title=f"Epoch {epoch} · amplitude and period association")
    ax.grid(alpha=.17)
    if len(g) or len(p):
        ax.legend(frameon=False, loc="upper left")
    finish(fig, "period", "Amplitude and period", "Squared amplitude is averaged across all 30 passages, paired with the same complete-cycle periods. The line uses paired eligible hourly means when sufficient coverage exists. Shared capture edges and the period used in amplitude estimation can induce association; no causal coefficient or correction is inferred.")
    return used, captions


def packed(source, cycle=False):
    columns = [("t", source.time_s if cycle else source.start_s, "<f8", "Float64Array"),
               ("run", source.series_run, "<u4", "Uint32Array"),
               ("seq", source.first_sequence if cycle else source.sequence_extended, "<f8", "Float64Array")]
    if not cycle:
        columns.append(('phase', source.phase15, '<f8', 'Float64Array'))
        for side in SIDES:
            columns.append((side+'_phase', source.get(side+'_phase30',2*source.phase15+(side=='tock')), '<f8', 'Float64Array'))
    for side in SIDES:
        if not cycle:
            columns.append((side+"_t", source[side+"_time_s"], "<f8", "Float64Array"))
            columns.append((side+"_blocked", source[side+"_blocked_ms"], "<f8", "Float64Array"))
        for metric, key in (("velocity_mm_s", "v"), ("amplitude_deg", "a")):
            columns.append((side+"_"+key, source[side+"_"+metric], "<f8", "Float64Array"))
    columns.append(("period", source.mean_period_s if cycle else source.period_s.where(source.available), "<f8", "Float64Array"))
    return pack_columns(columns)


def render(swing, epoch, cfg, directory, table_renderer):
    tables = swing.tables
    def select(name):
        f = tables["swing_motion_"+name]
        return f.loc[f.epoch.eq(epoch)]
    obs, cycles, phase, evolution = [select(n) for n in ("observations", "cycles", "phase", "evolution")]
    summary, fits, validation, predictions = [select(n) for n in ("summary", "fits", "validation", "predictions")]
    used, captions = static_figures(obs, cycles, phase, evolution, predictions, epoch, cfg, directory)
    geometry = f"Provisional effective blocked path {cfg.optical_path_mm:g} mm; pivot-to-beam radius {cfg.pivot_to_beam_mm:g} mm."
    body = "<p>"+escape(geometry+" "+CAPTION)+"</p>"+phase_legend_html()
    body += image(directory/used[0], captions[0][1], captions[0][2])
    view = summary.loc[summary.metric.isin(("velocity_mm_s", "amplitude_deg"))].copy()
    view["metric"] = view.metric.map({"velocity_mm_s": "Passage speed (mm/s)", "amplitude_deg": "Estimated semi-amplitude (°)"})
    columns = {"side": "Passage", "metric": "Measurement", "count": "Eligible passages", "excluded": "Excluded", "mean": "Mean", "std": "SD", "p05": "5th percentile", "p95": "95th percentile"}
    h, md = table_renderer(view[list(columns)].rename(columns=columns), limit=len(view), digits=5)
    body += '<div class="scroll">'+h+'</div><p class="meta">SD and percentiles include drift and the repeating impulse pattern. They are not uncertainty estimates for the geometry or mean.</p>'
    if obs.available.any():
        individual = individual_payload(obs, epoch, directory)
        data = dict(individual=individual, cycles=packed(cycles, True), nominal=cfg.swing_period_s, phase_colors=PHASE_COLORS,
                    start=float(obs.start_s.min()), end=float(obs.start_s.max()+cfg.swing_period_s),
                    baselines={side: {key: float(summary.loc[summary.side.eq(side) & summary.metric.eq(metric), "mean"].iloc[0])
                                      for key, metric in (("v", "velocity_mm_s"), ("a", "amplitude_deg"))} for side in SIDES})
        body += CONTROLS+'<script id="plot-data" type="application/json">'+safe_json(data)+'</script>'
        if "chunks" in individual:
            body += '<p class="meta">Long recordings open with complete-cycle averages. Choose a shorter interval or 90-second detail for individual passages. Detailed data loads from companion files in the plots folder; keep that folder beside this report when moving it. Wide individual selections use cycle averages until you zoom in. No observations are removed from the analysis or CSV exports.</p>'
    else:
        body += '<p>No eligible PPS-calibrated optical passages. Motion estimates are unavailable.</p>'
    for filename, title, caption in captions[1:]:
        body += image(directory/filename, title, caption)
    body += '<details><summary>Phase counts and observed spread</summary>'
    ph, pm = table_renderer(phase, limit=len(phase), digits=5)
    body += '<div class="scroll">'+ph+'</div></details>'
    body += '<h3>Amplitude, environment and later-hour checks</h3><p>Amplitude-only fits use mean squared estimated semi-amplitude. The environment and environment-plus-amplitude models use exactly the same complete cycles and hours, with all 15 readings valid for each environmental sensor. Hours need 30 nominal minutes of complete cycles; fits need 24 hours. The first 70% train the later 30% check, requiring 24 training and 12 test hours. All baselines and predictor scaling use training data only. Compare later errors within the shared population. Velocity is not added as another predictor because it derives from the same measurements. Fits describe association; no reported periods are corrected.</p>'
    names = {"amplitude": "Amplitude² only", "environment": "Environment · shared population", "environment_amplitude": "Environment + amplitude² · shared population"}
    combined = fits.merge(validation, on=["epoch", "model"], suffixes=("", "_validation"), validate="one_to_one")
    combined["model"] = combined.model.map(names)
    cols = {"model": "Model", "hours": "Hours", "residual_rmse_us": "Full-run residual RMS (µs)",
            "model_rmse_us": "Later fit RMS (µs)", "constant_rmse_us": "Later constant RMS (µs)",
            "status_validation": "Later check status"}
    fh, fm = table_renderer(combined[list(cols)].rename(columns=cols), limit=len(combined))
    body += '<div class="scroll">'+fh+'</div><details><summary>Coefficients and separability</summary>'
    sh, sm = table_renderer(fits, limit=len(fits))
    body += '<div class="scroll">'+sh+'</div><p>Slopes are withheld for rank-deficient predictors or a scaled condition number above 30. Time-series dependence, optical uncertainty and shared-edge measurement coupling remain.</p></details>'
    markdown = geometry+" "+CAPTION+"\n\n"+md
    for filename, title, caption in captions:
        markdown += f"\n\n![{title}](plots/{filename})\n\n{caption}"
    markdown += "\n\n### Phase counts and observed spread\n\n"+pm+"\n\n### Amplitude and environment checks\n\n"+fm+"\n\n"+sm
    return dict(title=f"Epoch {epoch}: Velocity and amplitude", html=fragment(f"motion-e{epoch}", body,
                "motion.js" if obs.available.any() else None, CONTROL_STYLE), markdown=markdown, plots=used)
