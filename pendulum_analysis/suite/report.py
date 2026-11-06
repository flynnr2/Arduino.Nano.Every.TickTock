"""Local HTML/Markdown report and reproducible scientific plots."""
from .common import half_phases
from pathlib import Path
from html import escape
from ..report_assets import png_data_uri
from ..phase_colors import PHASE_COLORS, phase_colors, phase_ticks, phase_legend_html
import os

os.environ.setdefault("MPLCONFIGDIR", str(Path(os.environ.get("TMPDIR","/tmp"))/"pendulum-suite-matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import numpy as np
import pandas as pd

BLUE="#2458a6"
ORANGE="#c16b18"
RED="#b73842"


def finish(fig,path):
    fig.savefig(path,dpi=140,bbox_inches="tight")
    plt.close(fig)


def plot_clock(clock,cfg,directory):
    """Clock calibration support; environmental relationships are rendered separately."""
    return plot_clock_compact(clock,cfg,directory)


def _draw_phase_polar(ax, phase, values, scale, jitter, colours, title):
    """Render an untrimmed phase profile; missing statistics remain unavailable."""
    bins = len(phase)
    theta = phase*2*np.pi/bins
    ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    finite = np.isfinite(values)
    colours = np.asarray(colours)[finite]
    ring = 0 if jitter else scale*1.2
    if jitter:
        ax.bar(theta[finite], values[finite], width=2*np.pi/bins*.8, color=colours, alpha=.85)
        ax.set_ylim(0, scale*1.18)
    else:
        ax.bar(theta[finite], np.abs(values[finite]), bottom=ring+np.minimum(values[finite],0),
               width=2*np.pi/bins*.8, color=colours, alpha=.85)
        angles=np.linspace(0,2*np.pi,361)
        ax.plot(angles,np.full_like(angles,ring),color="#555",lw=1)
        ax.set_yticks(ring+np.array([-.8,0,.8])*scale)
        ax.set_yticklabels([f"{a:+.3g}" for a in [-.8*scale,0,.8*scale]],fontsize=8)
        ax.set_ylim(0,ring+scale*1.2)
    ax.set_xticks(theta); ax.set_xticklabels([str(i) for i in phase],fontsize=9 if bins==15 else 8)
    for label, color in zip(ax.get_xticklabels(), phase_colors(phase, half=bins==30)):
        label.set_color(color)
        label.set_fontweight('bold')
    ax.tick_params(axis="y",labelsize=8); ax.grid(alpha=.22)
    ax.set_title(title,pad=25)
    for angle, available in zip(theta, finite):
        if not available:
            ax.text(angle,ax.get_ylim()[1]*.9,"×",color=RED,ha="center")


def polar_pair(table,summary,metric,bins,basis,epoch,jitter,path,median_only=False):
    t=table[(table.metric==metric)&(table.bin_count==bins)&(table.basis==basis)&(table.epoch==epoch)].sort_values("phase")
    if not len(t) or not t["count"].sum(): return False
    phase=t.phase.to_numpy(int)
    fields=("std","robust_sigma") if jitter else ("mean","median")
    titles=("Standard deviation","Robust spread (1.4826 × MAD)") if jitter else ("Mean","Median")
    if median_only:
        fields, titles = ("median",), ("Median",)
    if bins==15:
        row=summary[(summary.metric==metric)&(summary.basis==basis)&(summary.epoch==epoch)]
        base=float(row.iloc[0]["median"])
        reference=np.full(bins,base)
        note=f"Reference: pooled full-swing median {base*1000:.6f} ms"
    else:
        names={"half":("tick_half","tock_half"),"open":("tick_open","tock_open"),"blocked":("tick_blocked","tock_blocked")}[metric]
        base=[]
        for name in names:
            row=summary[(summary.metric==name)&(summary.basis==basis)&(summary.epoch==epoch)]
            base.append(float(row.iloc[0]["median"]))
        reference=np.where(t.side.eq("tick") if "side" in t else phase%2==0,base[0],base[1])
        note=f"References: half A {base[0]*1000:.6f} ms; half B {base[1]*1000:.6f} ms (pooled medians)"
    values=[t[field].to_numpy(float)*1e6-(0 if jitter else reference*1e6) for field in fields]
    finite=np.concatenate(values);finite=finite[np.isfinite(finite)]
    scale=max(float(np.max(np.abs(finite))) if len(finite) else 0,1e-3)
    colors=phase_colors(phase, half=bins==30)
    fig,axes=plt.subplots(1,len(fields),figsize=(8 if median_only else 13,7),subplot_kw={"projection":"polar"},layout="constrained")
    axes=np.atleast_1d(axes)
    for ax,v,title in zip(axes,values,titles):
        _draw_phase_polar(ax,phase,v,scale,jitter,colors,title+" (µs)")
    label="Full swing" if bins==15 else {"half":"Half-swing total","open":"Open component","blocked":"Blocked component"}[metric]
    fig.suptitle(f"{label}: {bins} phase bins · {basis.replace('_',' ')} · epoch {epoch}",fontsize=15)
    footer=("Unfiltered within-bin spread; no slow-drift subtraction" if jitter else note+"\nBars outward/inward from the grey zero ring show longer/shorter durations")
    footer+="\nColours identify full-swing phase p; half bins 2p / 2p+1 share its colour; travel direction unassigned"
    fig.supxlabel(footer,fontsize=10)
    finish(fig,path)
    return True


def plot_calibrated_phase_polars(swing, epoch, path):
    """Full/half medians side by side, with within-phase standard deviations below."""
    table = swing.tables["swing_phase"]
    table = table.loc[(table.epoch == epoch) & table.basis.eq("pps_calibrated")]
    if not table.loc[table.metric.isin(["full", "half"]), "count"].sum():
        return False
    totals = swing.tables["swing_component_summary"]
    totals = totals.loc[(totals.epoch == epoch) & totals.basis.eq("pps_calibrated")].set_index("metric")
    fig, axes = plt.subplots(2,2,figsize=(14,13),subplot_kw={"projection":"polar"},layout="constrained")
    references = []
    for column,(metric,bins,label) in enumerate((("full",15,"Full swings"),("half",30,"Half swings"))):
        t = table.loc[table.metric.eq(metric) & table.bin_count.eq(bins)].sort_values("phase")
        phase = t.phase.to_numpy(int)
        if bins == 15:
            base = float(totals.loc["full", "median"])
            reference = np.full(bins,base)
            references.append(f"Full-swing reference: pooled median {base*1000:.6f} ms")
        else:
            a,b = float(totals.loc["tick_half","median"]),float(totals.loc["tock_half","median"])
            reference = np.where(t.side.eq("tick") if "side" in t else phase%2==0,a,b)
            references.append(f"Half references: A {a*1000:.6f} ms; B {b*1000:.6f} ms (pooled medians)")
        colours = phase_colors(phase, half=bins==30)
        for row,field in enumerate(("median","std")):
            values = t[field].to_numpy(float)*1e6 - (reference*1e6 if row==0 else 0)
            finite = values[np.isfinite(values)]
            scale = max(float(np.max(np.abs(finite))) if len(finite) else 0,1e-3)
            title = f"{label} · {bins} phases\n" + ("Median deviation (µs)" if row==0 else "Jitter: standard deviation (µs)")
            _draw_phase_polar(axes[row,column],phase,values,scale,row==1,colours,title)
    fig.suptitle(f"PPS-calibrated phase medians and jitter · epoch {epoch}",fontsize=16)
    fig.supxlabel("\n".join(references)+"\nMedian bars: outward/inward from grey ring = longer/shorter. Jitter retains drift."
                  "\nFull-swing phase colours are shared throughout; half bins 2p / 2p+1 share phase p's colour; physical direction unassigned.",fontsize=10)
    finish(fig,path)
    return True


def plot_swing_boxes(frame, directory, include_raw=True):
    """Tukey boxes of individual eligible events, grouped by sequence phase."""
    plots=[]
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    for epoch,group in frame.groupby("epoch"):
        for basis,valid,suffix in (("raw","raw_valid","_s"),("pps_calibrated","calibrated_valid","_pps_s")):
            if basis=="raw" and not include_raw: continue
            f=group.loc[group[valid] & group.phase15.between(0,14)]
            if f.empty: continue
            for metric,bins in (("full",15),("half",30)):
                samples=[]
                for phase in range(bins):
                    if bins == 15:
                        values=f.loc[f.phase15==phase,"full"+suffix].to_numpy(float)*1000
                    else:
                        values=np.concatenate([f.loc[half_phases(f,side)==phase,side+"_half"+suffix].to_numpy(float)*1000 for side in ("tick","tock")])
                    samples.append(values[np.isfinite(values)])
                fig,ax=plt.subplots(figsize=(15,6),layout="constrained")
                boxes=ax.boxplot(samples,positions=np.arange(bins),widths=.6,whis=1.5,
                    patch_artist=True,showfliers=True,
                    flierprops=dict(marker=".",markersize=2,alpha=.25),
                    medianprops=dict(color="black",linewidth=1.2))
                for phase,box in enumerate(boxes["boxes"]):
                    box.set_facecolor(PHASE_COLORS[phase if bins==15 else phase//2])
                    box.set_alpha(.55)
                    if bins==30 and len(f) and phase%2 == int(half_phases(f,"tock").iloc[0])%2:
                        box.set_hatch('//')
                ax.set_xticks(range(bins))
                ax.set_xticklabels([str(i) for i in range(bins)],fontsize=7)
                phase_ticks(ax, half=bins==30)
                for phase,values in enumerate(samples):
                    if not len(values):
                        ax.text(phase,.02,"×",color=RED,ha="center",transform=ax.get_xaxis_transform())
                ax.set(xlabel="Analysis phase bin",ylabel="Duration (ms)",
                    title=f"Phase-{bins} {'full' if bins==15 else 'half'} swing · {basis.replace('_',' ')} · epoch {epoch}")
                ax.ticklabel_format(axis="y",style="plain",useOffset=False)
                ax.grid(axis="y",alpha=.2)
                caption="Boxes: Q1–Q3; black line: median; whiskers: furthest samples within 1.5 × IQR; dots: all outliers; ×: empty bin. Eligible individual events; no detrending."
                aligned="impulse_aligned" in group and group.impulse_aligned.all()
                caption+=(" Impulse-aligned phase: the impulse-associated pair forms phase 0." if aligned else " Sequence-based phases; no mechanical impulse anchor assigned.")
                if bins==30: caption+=" Tick is plain; tock is hatched. Half bins 2p and 2p+1 share phase p's colour; their source direction depends on the anchor."
                fig.supxlabel("Analysis phase colours preserve gaps. "+("Plain = tick; hatched = tock. " if bins==30 else "")+"Whiskers = 1.5 × IQR; dots = outliers.",fontsize=10)
                name=f"swing_e{epoch}_{basis}_{metric}_boxplot.png"
                finish(fig,directory/name)
                plots.append((name,f"Epoch {epoch}: {basis.replace('_',' ')} phase-{bins} box-and-whisker",caption))
    return plots


def plot_half_cancellation(frame, directory, include_raw=True):
    """Paired halves from each event; density includes every eligible pair."""
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    plots=[]
    statistics=[]
    for epoch,group in frame.groupby("epoch"):
        for basis,valid,suffix in (("raw","raw_valid","_s"),("pps_calibrated","calibrated_valid","_pps_s")):
            if basis=="raw" and not include_raw: continue
            columns=["tick_half"+suffix,"tock_half"+suffix]
            f=group.loc[group[valid] & group.phase15.between(0,14),["phase15",*columns]].copy()
            f=f.loc[np.isfinite(f[columns]).all(axis=1)]
            if len(f)<2: continue
            fig,axes=plt.subplots(1,2,figsize=(14,7),layout="constrained")
            for ax,within in zip(axes,(False,True)):
                centre=f.groupby("phase15")[columns].transform("mean") if within else f[columns].mean()
                residual=(f[columns]-centre)*1000
                x,y=residual.to_numpy().T
                corr=float(np.corrcoef(x,y)[0,1]) if np.std(x)>0 and np.std(y)>0 else np.nan
                sx,sy,sfull=np.std(x,ddof=1),np.std(y,ddof=1),np.std(x+y,ddof=1)
                independent=np.hypot(sx,sy)
                limit=max(float(np.max(np.abs(residual.to_numpy())))*1.03,1e-6)
                density=ax.hexbin(x,y,gridsize=100,extent=(-limit,limit,-limit,limit),mincnt=1,bins="log",cmap="viridis")
                fig.colorbar(density,ax=ax,shrink=.7,label="Paired events per hexagon (log scale)")
                ax.plot([-limit,limit],[limit,-limit],"--",color=ORANGE,lw=1,label="Perfect cancellation: tock = −tick")
                ax.set(xlim=(-limit,limit),ylim=(-limit,limit),aspect="equal",
                    xlabel="Tick deviation (ms)",ylabel="Tock deviation (ms)",
                    title="Within sequence phase" if within else "Whole epoch")
                ax.legend(loc="lower left",fontsize=8)
                corr_label=f"{corr:.5f}" if np.isfinite(corr) else "unavailable"
                ax.text(.03,.97,f"n = {len(f):,}   Pearson r = {corr_label}\nσ tick = {sx:.4g} ms; tock = {sy:.4g} ms\nσ full = {sfull:.4g} ms\nIndependent-halves prediction = {independent:.4g} ms",transform=ax.transAxes,va="top",fontsize=9,bbox=dict(facecolor="white",alpha=.85,edgecolor="none"))
                statistics.append(dict(epoch=epoch,basis=basis,centring="phase_mean" if within else "epoch_mean",count=len(f),correlation=corr,tick_std_ms=sx,tock_std_ms=sy,full_std_ms=sfull,independent_full_std_ms=independent))
            fig.suptitle(f"Tick / tock cancellation · {basis.replace('_',' ')} · epoch {epoch}",fontsize=15)
            caption="Each pair belongs to the same analysis full period (which may span two source records). Left: each direction's epoch mean removed. Right: each direction's analysis-phase mean removed. All eligible pairs shown; no time detrending. Full residual = tick residual + tock residual."
            fig.supxlabel("Dashed line: equal and opposite deviations leave the full swing unchanged. All eligible pairs included.",fontsize=10)
            name=f"swing_e{epoch}_{basis}_half_cancellation.png"
            finish(fig,directory/name)
            plots.append((name,f"Epoch {epoch}: {basis.replace('_',' ')} tick / tock cancellation",caption))
    columns=["epoch","basis","centring","count","correlation","tick_std_ms",
             "tock_std_ms","full_std_ms","independent_full_std_ms"]
    pd.DataFrame(statistics,columns=columns).to_csv(directory/"half_cancellation_statistics.csv",index=False)
    return plots


def plot_swing_diagnostics(swing,cfg,directory):
    plots=[]
    if cfg.diagnostics or cfg.raw_phase_charts:
        plots+=plot_swing_boxes(swing.frame,directory,include_raw=cfg.raw_phase_charts)
        plots+=plot_half_cancellation(swing.frame,directory,include_raw=cfg.raw_phase_charts)
    table=swing.tables["swing_phase"]
    totals=swing.tables["swing_component_summary"]
    for epoch in sorted(table.epoch.unique()):
        for basis in (("raw","pps_calibrated") if cfg.raw_phase_charts else ("pps_calibrated",)):
            if not cfg.phase_polars: continue
            for metric,bins in (("full",15),("half",30),("open",30),("blocked",30)):
                for jitter in (False,True):
                    kind="jitter" if jitter else "mean_median"
                    name=f"swing_e{epoch}_{basis}_{metric}_{kind}.png"
                    if polar_pair(table,totals,metric,bins,basis,epoch,jitter,directory/name):
                        caption=("Standard deviation and scaled MAD are measured within the phase bin. Slow drift remains included; these charts measure repeatability separately from phase-dependent mean duration." if jitter else
                            "Statistics use complete individual events. Shared scales compare mean and median. A fixed pooled reference is used, separately for tick and tock in 30-bin views; no per-bin centring removes the phase pattern. Absolute values are in swing_phase.csv.")
                        plots.append((name,f"Epoch {epoch}: {basis.replace('_',' ')} {metric.replace('_',' ')} — {kind.replace('_',' ')}",caption))
    fig,axes=plt.subplots(1,2,figsize=(13,5),layout="constrained")
    for ax,bins,metric in zip(axes,(15,30),("full","half")):
        for (epoch,basis),g in table[(table.bin_count==bins)&(table.metric==metric)&(table.basis.eq("pps_calibrated")|cfg.raw_phase_charts)].groupby(["epoch","basis"]):
            ax.plot(g.phase,g["count"],color='#777777',lw=.6,label=f"epoch {epoch}, {basis}")
            if bins==15:
                ax.scatter(g.phase,g['count'],c=phase_colors(g.phase),s=20,zorder=3)
            else:
                for parity,marker in ((0,'o'),(1,'^')):
                    side=g.loc[g.phase.mod(2).eq(parity)]
                    ax.scatter(side.phase,side['count'],c=phase_colors(side.phase,half=True),marker=marker,s=20,zorder=3)
        ax.set(xlabel="Analysis phase",ylabel="Eligible events",title=f"{bins}-bin exposure")
        ax.yaxis.set_major_locator(MaxNLocator(integer=True, min_n_ticks=1))
        ax.set_xticks(range(bins));ax.tick_params(axis="x",labelsize=7);ax.grid(alpha=.2)
        phase_ticks(ax, half=bins==30)
        ax.legend(fontsize=8)
    name="swing_phase_exposure.png";finish(fig,directory/name)
    plots.append((name,"Phase-bin exposure","Counts are reported for every bin, including empty bins. A missing row removes one full swing and its two halves; later events retain their frozen phase assignment; alignment status is reported separately."))
    weekly=swing.tables["swing_phase_weekly"]
    for epoch in sorted(weekly.epoch.unique()):
        fig,axes=plt.subplots(2 if cfg.raw_phase_charts else 1,1,figsize=(13,8 if cfg.raw_phase_charts else 4.5),layout="constrained")
        axes=np.atleast_1d(axes)
        for ax,basis in zip(axes,(("raw","pps_calibrated") if cfg.raw_phase_charts else ("pps_calibrated",))):
            w=weekly[(weekly.epoch==epoch)&(weekly.basis==basis)&(weekly.metric=="half")]
            if not len(w):
                ax.text(.5,.5,"No eligible half-swings",ha="center",transform=ax.transAxes);continue
            grid=w.pivot(index="week",columns="phase",values="median").reindex(columns=range(30))*1e6
            # For the persistence diagnostic only, remove each week's directional centre.
            for parity in (0,1):
                grid.iloc[:,parity::2]=grid.iloc[:,parity::2].sub(grid.iloc[:,parity::2].median(axis=1),axis=0)
            limit=max(np.nanmax(np.abs(grid.to_numpy())),.001)
            im=ax.imshow(grid,aspect="auto",origin="lower",cmap="RdBu_r",vmin=-limit,vmax=limit)
            ax.set_xticks(range(30));ax.set_yticks(range(len(grid)));ax.set_yticklabels(grid.index.astype(int))
            phase_ticks(ax, half=True)
            ax.set(xlabel="Half-swing phase",ylabel="Elapsed week",title=basis.replace("_"," "))
            fig.colorbar(im,ax=ax,label="Median deviation (µs)")
        name=f"swing_e{epoch}_weekly_pattern.png";finish(fig,directory/name)
        plots.append((name,f"Epoch {epoch}: phase pattern across weeks","For this persistence view only, each week's tick and tock bin medians have their own directional median removed. This reveals changes in pattern independently of broad rate drift. Empty weeks/bins remain blank."))
    h=swing.tables["swing_hour"]
    fig,ax=plt.subplots(2,1,figsize=(13,7),sharex=True,layout="constrained")
    for _,g in h.groupby("epoch"):
        ax[0].plot(g.day,(g.raw_mean_s-cfg.swing_period_s)*1000,color=BLUE,lw=.8,label="Raw")
        ax[0].plot(g.day,(g.pps_mean_s-cfg.swing_period_s)*1000,color=ORANGE,lw=.8,label="PPS calibrated")
        ax[1].plot(g.day,(g.raw_mean_s-g.pps_mean_s)*1e6,color=BLUE,lw=.8)
    ax[0].set(ylabel="Period minus nominal (ms)",title="Hourly full-swing period")
    handles,labels=ax[0].get_legend_handles_labels()
    if handles:ax[0].legend(handles[:2],labels[:2])
    ax[1].set(ylabel="Raw minus calibrated (µs)",xlabel="Observed elapsed days",title="Clock calibration effect (hourly populations may differ at exclusions)")
    name="swing_period.png";finish(fig,directory/name)
    plots.insert(0,(name,"Swing period over time","Raw durations divide timer cycles by nominal frequency. PPS calibration integrates the enclosing valid one-second reference intervals. Only swings with complete five-edge coverage enter the calibrated view."))
    runs=swing.tables["swing_sensor_runs"]
    for r in runs.head(8).itertuples():
        f=swing.frame
        context=f.loc[(f.epoch==r.epoch)&(f.source_row>=r.start_source_row-30)&(f.source_row<=r.end_source_row+30)]
        seconds=context.elapsed_cycles/cfg.nominal_hz-r.start_day*86400
        bad=context.optical_clearance_suspect
        fig,ax=plt.subplots(3,1,figsize=(13,8),sharex=True,layout="constrained")
        ax[0].plot(seconds,context.full_s,color='#777777',lw=.6)
        ax[0].scatter(seconds,context.full_s,c=phase_colors(context.phase15),s=12)
        ax[0].scatter(seconds[bad],context.loc[bad,"full_s"],s=10,color=RED)
        ax[0].set(ylabel="Full period (s)",title="Diagnostic raw captures: red records are excluded from metrology")
        ax[1].plot(seconds,context.tick_half_s,color='#555555',label="Tick",lw=.6)
        ax[1].plot(seconds,context.tock_half_s,color='#555555',label="Tock",ls='--',lw=.6)
        ax[1].scatter(seconds,context.tick_half_s,c=phase_colors(context.phase15),marker='o',s=12)
        ax[1].scatter(seconds,context.tock_half_s,c=phase_colors(context.phase15),marker='^',s=12)
        ax[1].set_ylabel("Half period (s)");ax[1].legend()
        ax[2].plot(seconds,context.tick_blocked_fraction,color='#555555',lw=.6)
        ax[2].plot(seconds,context.tock_blocked_fraction,color='#555555',ls='--',lw=.6)
        ax[2].scatter(seconds,context.tick_blocked_fraction,c=phase_colors(context.phase15),marker='o',s=12)
        ax[2].scatter(seconds,context.tock_blocked_fraction,c=phase_colors(context.phase15),marker='^',s=12)
        ax[2].axhline(cfg.max_blocked_fraction,color=RED,ls="--",label="Acceptance limit")
        ax[2].set(ylabel="Blocked fraction of half",xlabel="Seconds relative to first flagged record")
        ax[2].legend()
        for a in ax:a.grid(alpha=.2)
        name=f"swing_sensor_run_{r.run}.png";finish(fig,directory/name)
        plots.insert(0,(name,f"Optical clearance episode: sequences {r.start_seq}–{r.end_seq}",
            f"{r.records:,} suspect records excluded from both raw and PPS-calibrated phase/timing statistics. A plausible full period does not override the component-level checks. {min(8,len(runs))} of {len(runs)} episodes are plotted; every episode is exported in swing_sensor_runs.csv."))
    return plots


def plot_clock_compact(clock, cfg, directory):
    span = clock.summary["observed_days"]
    key = "clock_minute" if span < 7 else "clock_hour"
    data = clock.tables[key]
    fig, ax = plt.subplots(figsize=(13, 5), layout="constrained")
    for _, group in data.groupby("epoch"):
        bins = np.arange(int(group.bin.min()), int(group.bin.max()) + 1)
        values = group.set_index("bin").offset_mean_cycles.reindex(bins) / cfg.nominal_hz * 1e6
        width = 60 if key == "clock_minute" else 3600
        ax.plot((bins + .5) * width / 86400, values, color=BLUE, lw=.8)
    ax.set(xlabel="Observed elapsed days", ylabel="Frequency offset (ppm)",
           title=f"Clock frequency against PPS · {'minute' if key == 'clock_minute' else 'hourly'} means")
    ax.grid(alpha=.2)
    name = "clock_frequency.png"; finish(fig, directory / name)
    plots = [(name, "Clock frequency against PPS", "Eligible measured oscillator intervals relative to PPS. No environmental fit or timer-projection correction is applied. Empty time bins remain gaps and epochs are drawn separately. This supports offline swing calibration; it does not measure the firmware's disciplined output.")]
    fig, ax = plt.subplots(figsize=(10, 5), layout="constrained")
    a = clock.tables["clock_allan_raw"]
    if len(a):
        ax.loglog(a.tau_s, a.adev_fractional, ".-", color=BLUE)
        ax.fill_between(a.tau_s, a.approx_low, a.approx_high, color=BLUE, alpha=.12)
    else:
        ax.text(.5, .5, "Insufficient contiguous eligible data", ha="center", transform=ax.transAxes)
    ax.set(xlabel="Averaging time τ (seconds)", ylabel="Fractional-frequency Allan deviation", title="Clock stability against PPS")
    ax.grid(which="both", alpha=.2)
    name = "clock_stability.png"; finish(fig, directory / name)
    plots.append((name, "Stability versus averaging time", "Raw measured clock frequency against PPS, with no detrending. Every contributing window is contiguous and stays within one epoch. Each point has at least 20 disjoint supporting pairs; available shading is an approximate block-bootstrap interval."))
    return plots


def plot_swings(swing, cfg, directory):
    from .time_series_plots import plot_time_series
    plots = plot_time_series(swing, cfg, directory)
    for epoch in sorted(swing.frame.epoch.unique()):
        name = f"swing_e{epoch}_pps_calibrated_phase_polars.png"
        if cfg.phase_polars and plot_calibrated_phase_polars(swing,epoch,directory/name):
            plots.append((name,f"Epoch {epoch}: PPS-calibrated phase medians and jitter",
                          "Top row: full-swing (15-phase) and half-swing (30-phase) medians side by side. Median deviations use the pooled full-swing median or separate pooled half A/B medians. Bottom row: sample standard deviation of eligible individual events within the same phase bins; slow drift is retained. No second smoothing or robust-spread statistic is added. All phase-profile values use their full scales, without time-series display clipping. Missing statistics have a cross, including jitter with fewer than two events. References appear below the charts; absolute values and counts are in swing_phase.csv. Physical travel direction is unassigned."))
    plots += plot_swing_diagnostics(swing, cfg, directory)
    return plots


def _table(frame,limit=24,digits=4,fixed=None):
    if frame is None or frame.empty:return "<p>No eligible data.</p>","No eligible data."
    view=frame.head(limit).copy()
    fixed=fixed or {}
    for c in view.select_dtypes(include="number"):
        if c in fixed:
            view[c]=view[c].map(lambda v:f"{v:.{fixed[c]}f}" if pd.notna(v) else "unavailable")
        elif pd.api.types.is_integer_dtype(view[c]):
            view[c]=view[c].map(lambda v:f"{int(v):,}" if pd.notna(v) else "unavailable")
        else:
            # Preserve ppm-level departures from one second in PPS support tables.
            units=view['unit'] if 'unit' in view else ['']*len(view)
            view[c]=[f"{v:.{max(digits,9) if unit=='s' else digits}g}" if pd.notna(v) else "unavailable"
                     for v,unit in zip(view[c],units)]
    view=view.fillna("unavailable")
    note=f"<p>Showing {min(limit,len(frame))} of {len(frame)} rows; complete values are in the CSV export.</p>" if len(frame)>limit else ""
    rows = [list(map(str, view.columns)), *[list(map(str, row)) for row in view.itertuples(index=False,name=None)]]
    widths = [max(3, *(len(row[i]) for row in rows)) for i in range(len(view.columns))]
    rows.insert(1, ["-" * width for width in widths])
    md = "\n".join("| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths)) + " |" for row in rows)
    return view.to_html(index=False,escape=True,border=0)+(f"<p>{note}</p>" if note else ""),md+(f"\n\n{note}" if note else "")


def pps_tables():
    """Report selections; the full tables are exported without rounding."""
    compact = ["metric", "unit", "n", "mean", "median", "robust_sigma", "p05", "p95", "p99", "min", "max"]
    return [
        ("clock_data_quality", "PPS data quality", "Record counts and observed coverage.", None, 24),
        ("clock_interval_filters", "PPS interval filters",
         "Sequential filters: each row retains only intervals that passed every preceding stage. The first stage removes the initial record, which has no preceding interval. An old nonzero cumulative drop count does not exclude later intervals.", None, 24),
        ("clock_gps_states", "GPS status counts",
         "Percentages use records with valid required fields, not elapsed time. Invalid records are counted in data quality.", None, 24),
        ("clock_timebase_health", "GPS lock and holdover", "Transitions are counted only across consecutive valid records within one epoch.", None, 24),
        ("clock_timing_summary", "PPS timing characteristics",
         "Raw and diagnostic-normalized offsets use eligible one-second intervals; normalization additionally requires an available projection correction. Positive offset means more oscillator cycles per GPS second than nominal. Reported PPS jitter uses valid records with a finite optional pps_jitter_ticks value. Missing measurements are unavailable. Robust spread is 1.4826 × MAD; the CSV also includes standard deviation, MAD, IQR, p01 and population notes.", compact, 24),
        ("clock_latency_summary", "PPS capture latency",
         "All valid required-field records, including unlocked captures. Latency describes capture/ISR service, not pendulum jitter. latency16 is modulo 65,536 cycles, so longer delays can alias. Microseconds use the configured nominal frequency.", compact, 24),
        ("clock_latency_diagnostics", "PPS latency diagnostics",
         "The historical threshold matches the existing event classifier. Isolated spikes alone do not establish a PPS timing error.", None, 24),
        ("clock_largest_latency_events", "Largest PPS latency events",
         "Up to eight largest captures above the threshold, ranked by latency. Offsets on ineligible intervals are diagnostic observations only; frequency_valid identifies the metrology population. The CSV contains every above-threshold capture.", None, 8),
        ("clock_cap16_coverage", "PPS cap16 phase coverage",
         "Coverage across the 16-bit timer is a capture-system sanity check. Non-uniformity may reflect limited duration or timer/PPS phase structure; this is not a precision or stability score. All 256 bin counts are exported in clock_cap16_bins.csv.", None, 24),
    ]


def swing_summary_tables(swing, diagnostics=True):
    """Complete component distributions, keeping epochs and populations separate."""
    labels={"full":"Full swing", "tick_half":"Half A", "tock_half":"Half B",
            "tick_open":"Edge 0→1 (open)", "tick_blocked":"Edge 1→2 (blocked)",
            "tock_open":"Edge 2→3 (open)", "tock_blocked":"Edge 3→4 (blocked)"}
    columns={"metric":"Measurement", "n":"Count", "mean":"Mean (ms)",
             "median":"Median (ms)", "std":"Std dev (µs)",
             "robust_sigma":"Robust spread (µs)", "p05":"P05 (ms)",
             "p95":"P95 (ms)", "min":"Min (ms)", "max":"Max (ms)"}
    table=swing.tables["swing_component_summary"]
    for (epoch,basis),group in table.groupby(["epoch","basis"],sort=True):
        if not diagnostics and basis != "pps_calibrated":
            continue
        view=group.set_index("metric").reindex(labels).reset_index()
        view["metric"]=view.metric.map(labels)
        for column in ("mean","median","p05","p95","min","max"):view[column]*=1000.
        for column in ("std","robust_sigma"):view[column]*=1e6
        title=f"Epoch {epoch}: {'Raw' if basis=='raw' else 'PPS-calibrated'} swing characteristics"
        selected = list(columns) if diagnostics else ["metric", "n", "mean", "std"]
        yield title,view[selected].rename(columns=columns)


def _exposure_explanation(swing):
    table=swing.tables['swing_phase']
    notes=[]
    selected=table.loc[table.basis.eq('pps_calibrated')&table.metric.eq('full')&table.bin_count.eq(15)]
    for epoch,g in selected.groupby('epoch'):
        low,high=int(g['count'].min()),int(g['count'].max())
        spread=high-low;average=float(g['count'].mean())
        notes.append(f'Epoch {epoch}: {low:,}–{high:,} eligible full swings per phase; '
            f'the {spread:,}-event spread is {100*spread/average if average else 0:.4g}% of the average bin count.')
    return ('These charts count observations, not timing error or duration. The left panel counts full swings by phase 0–14; '
        'the right counts their two halves, with phase 2p and 2p+1 belonging to full phase p. Those two half-bin counts are equal '
        'because both halves use the same eligible full-swing record. A perfectly complete number of 15-swing cycles would give equal counts. '
        'Individual-event exposure also includes eligible partial cycles at the beginning and end, which can give some phases one extra observation. '
        'Missing sequences, reported drops, optical-clearance exclusions and unavailable PPS calibration can remove records at particular phases; '
        'later events keep their original sequence phases. Small light/heavy differences therefore describe coverage, not a physical phase effect. '
        'The count axis is fitted to the observed counts, so a small absolute difference can look prominent. '
        'The new distribution and contribution components use complete cycles only and consequently give every phase identical exposure. '
        +' '.join(notes))


REPORT_STYLE="""body{font:15px/1.55 system-ui,sans-serif;color:#203047;background:#f3f5f8;margin:0}main{max-width:1480px;margin:auto;padding:30px;background:white}h1{font-size:30px;line-height:1.2}h3{font-size:18px;line-height:1.3}p{max-width:1300px}img{width:100%;height:auto}figure{margin:20px 0}figcaption,.meta{font-size:13px;color:#536176}table{border-collapse:collapse;font-size:13px;width:100%}th,td{padding:8px;border-bottom:1px solid #ddd;text-align:right}th:first-child,td:first-child{text-align:left}th{background:#edf1f6}a{color:#2458a6}.scroll,.table{overflow:auto}nav{display:flex;gap:12px 20px;flex-wrap:wrap;margin:18px 0}button{font:inherit;cursor:pointer}.report-section{border-top:1px solid #d9dfe8;margin-top:24px;padding-top:14px}.report-section>summary{font-size:21px;font-weight:550;cursor:pointer;line-height:1.3}.report-section>.section-content{padding-top:8px}.section-content details{margin:18px 0}.section-content summary{cursor:pointer;font-weight:500}.toolbar{margin:18px 0}canvas{max-width:100%}@media(max-width:650px){main{padding:20px 14px}h1{font-size:25px}.report-section>summary{font-size:19px}}@media print{body,main{background:white;padding:0}figure{break-inside:avoid}.toolbar,nav{display:none}}"""


def write_report(out,clock,swing,cfg,summary):
    """Report composition: dataset-local components, all sensors and explicit extras."""
    from .report_environment import render_combined as environment_section
    from ..swing_components.report import render_components
    out=Path(out);plots_dir=out/'plots';plots_dir.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.size':10,'axes.titlesize':12,'figure.facecolor':'white',
                         'axes.spines.top':False,'axes.spines.right':False,'agg.path.chunksize':10000})
    plots=plot_clock(clock,cfg,plots_dir)
    if swing is not None:plots+=plot_swings(swing,cfg,plots_dir)
    tables={**clock.tables,**(swing.tables if swing else {})}
    csvdir=out/'csv';csvdir.mkdir(exist_ok=True)
    export_paths={name: 'csv/'+name+('.csv.gz' if name=='swing_motion_observations' else '.csv') for name in tables}
    for name,table in tables.items():table.to_csv(out/export_paths[name],index=False)
    inputs=summary.get('provenance',{}).get('inputs',{})
    source=inputs.get('pcsw',inputs.get('pcps',{})).get('path')
    label=Path(source).parent.name if source else 'Recorded data'
    if label=='input':label=Path(source).parent.parent.name
    html=["<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>",
          f'<title>{escape(label)} · pendulum analysis</title><style>{REPORT_STYLE}</style><main>',
          f'<h1>Clock and swing analysis · {escape(label)}</h1>',
          "<nav><a href='#behaviour'>Behaviour</a><a href='#components'>Swing analyses</a><a href='#environment'>Environment</a><a href='#phase-exposure'>Phase exposure</a><a href='#data'>Exports</a><a href='summary.json'>Summary and provenance</a></nav>",
          '<div class="toolbar"><button id="report-toggle-all" type="button" aria-expanded="false">Expand all sections</button></div>']
    md=['# Clock and swing analysis\n']
    if swing is not None:
        html.append(phase_legend_html())
    images=[];used=set();counter=0
    def section(title,body,markdown='',anchor=None,opened=True):
        nonlocal counter
        counter+=1;anchor=anchor or f'report-section-{counter}'
        html.append(f'<details class="report-section" id="{escape(anchor)}"'+(' open' if opened else '')+f'><summary>{escape(title)}</summary><div class="section-content">{body}</div></details>')
        md.append(f'## {title}\n\n{markdown}\n')
    def chart(item,anchor=None,opened=True,extra=''):
        name,title,caption=item;used.add(name);images.append(name)
        body=extra+f'<figure><img loading="lazy" src="{png_data_uri(plots_dir/name)}" alt="{escape(title)}"><figcaption>{escape(caption)}</figcaption></figure>'
        section(title,body,f'![{title}](plots/{name})\n\n{caption}',anchor,opened)
    s=clock.summary
    intro=f"{s['records']:,} PPS records over {s['observed_days']:.2f} observed days; {s['eligible_intervals']:,} eligible one-second intervals."
    if swing is not None:
        series=swing.summary['time_series']
        intro+=f" {swing.summary['records']:,} swing records; {swing.summary['pps_calibrated_eligible']:,} PPS-calibrated eligible swings. {series['complete_cycles']:,} complete phase-0…14 groups; {series['incomplete_cycles']:,} partial or excluded groups."
    else:intro+=' PCSW.CSV is absent; swing analyses are unavailable.'
    section('Overview','<p>'+escape(intro)+'</p>',intro)
    if swing is not None and cfg.impulse_alignment:
        alignment=swing.summary['impulse_alignment']
        explanation=('Impulse-aligned phase 0 contains the two adjacent impulse-associated half intervals. '
            'The anchor is inferred from the operator-established signature, frozen per epoch and checked across time. '
            'Fallback epochs retain their original sequence phases and cannot be compared by mechanical phase number with aligned epochs. '
            'Tick/tock retain source identity: an odd anchor pairs tock(n) with tick(n+1); the first source capture fields are preserved. '
            'Capture-quality events remain in original source coordinates.')
        warnings=''.join('<p role="alert"><strong>Warning: '+escape(w)+'</strong></p>' for w in alignment['warnings'])
        a,b=_table(swing.tables['swing_impulse_anchors'],limit=len(swing.tables['swing_impulse_anchors']))
        section('Synchronome impulse alignment',warnings+'<p>'+escape(explanation)+'</p><div class="scroll">'+a+'</div>',
            '\n\n'.join(alignment['warnings'])+'\n\n'+explanation+'\n\n'+b,'impulse-alignment')
    for item in plots:
        if item[0]=='swing_time_series.png':chart(item,'behaviour')
    if swing is not None:
        html.append('<div id="components"></div>')
        if cfg.swing_components:
            for spec in render_components(swing,cfg,plots_dir,_table):
                section(spec['title'],spec['html'],spec['markdown'])
                images.extend(spec['plots'])
    env=environment_section(tables,swing is not None,plots_dir,_table)
    section(env['title'],env['html'],env['markdown'],'environment');images.extend(env['plots'])
    if swing is not None:
        for item in plots:
            if item[0]=='swing_phase_exposure.png':
                explanation=_exposure_explanation(swing)
                chart((item[0],item[1],explanation),'phase-exposure',opened=False)
        views=[(title,view) for title,view in swing_summary_tables(swing,cfg.diagnostics)
               if cfg.raw_phase_charts or ': Raw ' not in title]
        summary_caption=('Complete eligible events, with raw and calibrated populations and epochs separate. '
            'Durations are shown in milliseconds to 1 µs, and spreads in microseconds to 0.01 µs; counts are exact. '
            'CSV exports retain full precision in seconds. Spread includes drift and the repeating phase pattern. Physical travel direction remains unassigned.')
        summary_html=[];summary_md=[]
        for title,view in views:
            fixed={c:3 if c.endswith('(ms)') else 2 for c in view if c.endswith(('(ms)','(µs)'))}
            a,b=_table(view,limit=len(view),fixed=fixed)
            summary_html.append(f'<h3>{escape(title)}</h3><div class="scroll">{a}</div>')
            summary_md.append(title+'\n\n'+b)
        section('Swing summary tables','<p>'+summary_caption+'</p>'+''.join(summary_html),
            summary_caption+'\n\n'+'\n\n'.join(summary_md),'swings',False)
    # Remaining support charts are individually requestable/collapsible; no old frequency detail.
    for item in plots:
        if item[0] in used:continue
        routine=('phase_evolution' in item[0])
        chart(item,opened=routine)
    for name,title,caption,columns,limit in pps_tables():
        if not cfg.diagnostics and name not in ('clock_data_quality','clock_gps_states','clock_timebase_health','clock_timing_summary'):continue
        frame=tables[name]
        if not cfg.diagnostics and name=='clock_timing_summary':
            frame=frame[frame.metric.eq('Raw frequency offset')&frame.unit.eq('ppm')];columns=['metric','unit','n','mean','std']
        if columns is not None:frame=frame[[c for c in columns if c in frame]]
        a,b=_table(frame,limit=limit)
        section(title,f'<p>{escape(caption)}</p><div class="scroll">{a}</div>',caption+'\n\n'+b,opened=False)
    method=(f"Phase p = (extended PCSW sequence − {cfg.phase_origin}) mod 15. Half A is edge 0→2 and half B is edge 2→4; physical travel direction is unassigned and phase zero does not establish a mechanical impulse position. Epochs are never pooled. "
        'Integer validity, chronology, edge ownership, reported drops and optical-clearance screening determine eligible measurements. No distribution-based clipping removes the impulse pattern. Environmental readings must pass physical and stale-run screening. Fits describe associations, not causal effects or timing corrections. ')
    if swing is not None and cfg.impulse_alignment:
        method=swing.summary['phase_convention']+' Capture edge fields and source IDs are unchanged; derived periods and elapsed starts use reconstructed half intervals where aligned. '+method[method.index('Integer validity'):]
    if swing is not None:
        method+=f"{swing.summary['event_counts']['suspected_optical_clearance_loss']:,} records are excluded for suspected optical-clearance loss. "+swing.summary['alignment_assumption']
    method+=' Raw phase charts and polar charts are generated only when explicitly requested; detailed CSV populations remain exported.'
    section('Mechanical phase, validity and methods','<p>'+escape(method)+'</p>',method,opened=False)
    coverage=tables['environment_coverage']
    a,b=_table(coverage,limit=len(coverage))
    section('Environmental coverage and exclusions','<p>Capture-stream sensor screening. Pendulum fits separately screen the readings associated with their complete swing groups. Long unchanged runs are excluded retrospectively; unavailable channels remain explicit.</p><div class="scroll">'+a+'</div>',b,opened=False)
    clocktext=(f"Mean capture-clock frequency offset {s['offset_ppm']:.6f} ppm against PPS, equivalent to {s['uncorrected_ms_per_day']:.3f} ms/day at nominal frequency. " if s['eligible_intervals'] else 'No eligible reference intervals. ')
    meta=s.get('timescale',{})
    clocktext+=f"Offline PPS calibration: {meta.get('method','unavailable')}; {meta.get('window_seconds','unavailable')}-second window. {meta.get('boundary_policy','')} The dedicated PPS report provides capture-system diagnostics."
    section('Clock result','<p>'+escape(clocktext)+'</p>',clocktext,opened=False)
    exports='<ul>';md_exports=[]
    for name in tables:
        link=export_paths[name];exports+=f'<li><a href="{link}">{escape(name.replace("_"," "))}</a></li>';md_exports.append(f'- [{name}]({link})')
    for file in sorted(plots_dir.iterdir()):
        if file.suffix in ('.csv','.json') or file.name.endswith('.csv.gz'):
            link=f'plots/{file.name}';exports+=f'<li><a href="{link}">{escape(file.name)}</a></li>';md_exports.append(f'- [{file.name}]({link})')
    exports+='</ul><p>Charts and overview interactive data are embedded in this HTML. Long recordings load individual motion details from companion files in the plots folder. Keep the complete report folder for detailed inspection, CSV exports and summary links. All source populations, settings and implementation fingerprints are retained in the exports and summary.</p>'
    section('Data exports',exports,'\n'.join(md_exports),'data',False)
    html.append('''<script>(()=>{const button=document.getElementById('report-toggle-all');const sections=()=>Array.from(document.querySelectorAll('main details'));function update(){const all=sections().every(d=>d.open);button.textContent=all?'Collapse all sections':'Expand all sections';button.setAttribute('aria-expanded',String(all));}button.addEventListener('click',()=>{const expand=sections().some(d=>!d.open);sections().forEach(d=>{d.open=expand;});update();});document.querySelector('main').addEventListener('toggle',update,true);update();})();</script></main></html>''')
    (out/'report.html').write_text('\n'.join(html));(out/'report.md').write_text('\n'.join(md))
    return images
