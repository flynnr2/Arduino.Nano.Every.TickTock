"""Local HTML/Markdown report and reproducible scientific plots."""
from pathlib import Path
from html import escape
from ..report_assets import png_data_uri
import os

os.environ.setdefault("MPLCONFIGDIR", str(Path(os.environ.get("TMPDIR","/tmp"))/"pendulum-suite-matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BLUE="#2458a6"
ORANGE="#c16b18"
RED="#b73842"


def finish(fig,path):
    fig.savefig(path,dpi=140,bbox_inches="tight")
    plt.close(fig)


def plot_clock(clock,cfg,directory):
    if not cfg.diagnostics:
        return plot_clock_compact(clock, cfg, directory)
    plots=[]
    h=clock.tables["clock_hour"]
    fig,axes=plt.subplots(4,1,figsize=(13,11),sharex=True,layout="constrained")
    for epoch,g in h.groupby("epoch"):
        axes[0].plot(g.day,g.offset_mean_cycles/cfg.nominal_hz*1e6,lw=.8,color=BLUE)
    pred=clock.tables["environment_predictions"]
    if "temperature_ppm" in pred:
        for _,g in pred.groupby("epoch"):
            axes[0].plot(g.day,g.temperature_ppm,lw=1,color=ORANGE)
    axes[0].set(ylabel="Offset (ppm)",title="Clock against PPS: hourly frequency (blue), temperature fit (orange)")
    for ax,col,label in zip(axes[1:],("temperature_C","humidity_pct","pressure_hPa"),("Temperature (°C)","Humidity (%)","Pressure (hPa)")):
        if col in h:
            for _,g in h.groupby("epoch"):ax.plot(g.day,g[col],lw=.9,color=BLUE)
            stale=clock.tables["environment_stale_runs"]
            for _,r in stale.loc[stale.channel==col].iterrows():
                ax.axvspan(r.start_day,r.end_day,color=RED,alpha=.12)
                ax.hlines(r.value,r.start_day,r.end_day,colors=RED,linestyles="--")
        else:ax.text(.5,.5,"Channel absent",ha="center",transform=ax.transAxes)
        ax.set_ylabel(label)
    axes[-1].set_xlabel("Observed elapsed days (epochs do not establish UTC)")
    for ax in axes:ax.grid(alpha=.2)
    name="clock_environment.png";finish(fig,directory/name)
    plots.append((name,"Clock and environmental validity","Shaded red spans are retrospectively flagged stale values. Dashed values are shown for diagnosis and excluded from environmental fits. The temperature model uses valid matched hourly samples."))
    minute=clock.tables["clock_minute"]
    fig,ax=plt.subplots(2,1,figsize=(13,7),sharex=True,layout="constrained")
    for _,g in minute.groupby(["epoch","phase_run"]):
        ax[0].plot(g.day,g.phase_raw_s*1000,lw=.8,color=BLUE)
        ax[1].plot(g.day,g.phase_constant_s*1000,lw=.8,color=ORANGE)
    ax[0].set(title="Accumulated error at nominal frequency",ylabel="Error (ms)")
    ax[1].set(title="Accumulated error after a constant full-run frequency calibration",ylabel="Residual error (ms)",xlabel="Observed elapsed days")
    for a in ax:a.grid(alpha=.2)
    name="clock_phase.png";finish(fig,directory/name)
    plots.append((name,"Accumulated timing error","Each curve resets after an excluded interval. Unobserved phase through gaps is unknown. The constant calibration uses the whole run and is retrospective; neither curve measures the firmware's disciplined clock output."))
    fig,ax=plt.subplots(figsize=(10,6),layout="constrained")
    for key,label,color in (("clock_allan_raw","Raw frequency",BLUE),("clock_allan_diagnostic","Diagnostic projection normalization",ORANGE)):
        a=clock.tables[key]
        if len(a):
            ax.loglog(a.tau_s,a.adev_fractional,".-",color=color,label=label)
            ax.fill_between(a.tau_s,a.approx_low,a.approx_high,color=color,alpha=.12)
    if len(clock.tables["clock_allan_raw"]):ax.legend()
    else:ax.text(.5,.5,"Insufficient contiguous eligible data",ha="center",transform=ax.transAxes)
    ax.set(xlabel="Averaging time τ (seconds)",ylabel="Fractional-frequency Allan deviation",title="Clock stability: windows never cross exclusions or epochs")
    ax.grid(which="both",alpha=.2)
    name="clock_stability.png";finish(fig,directory/name)
    plots.append((name,"Stability versus averaging time","At least 20 disjoint pairs support each point. Shading is an approximate 95% block-bootstrap interval, using blocks of at least one day or 4τ and at least eight blocks. It is not a metrological confidence guarantee. No rolling-median detrending is applied."))
    fig,ax=plt.subplots(1,2,figsize=(13,5),layout="constrained")
    if "temperature_C" in h and "temperature_ppm" in pred:
        mask=np.isfinite(pred.temperature_ppm)
        sc=ax[0].scatter(h.loc[mask,"temperature_C"],pred.loc[mask,"observed_ppm"],c=h.loc[mask,"day"],s=9,cmap="viridis",alpha=.65)
        fig.colorbar(sc,ax=ax[0],label="Elapsed day")
        ax[0].set(xlabel="Matched hourly temperature (°C)",ylabel="Frequency offset (ppm)",title="Temperature association")
        for _,g in pred.groupby("epoch"):
            ax[1].plot(g.day,(g.observed_ppm-g.temperature_ppm)*1000,lw=.7,color=BLUE)
        ax[1].set(xlabel="Elapsed day",ylabel="Residual (ppb)",title="Residual after a full-run linear temperature fit")
    else:
        for a in ax:a.text(.5,.5,"Insufficient matched temperature data",ha="center",transform=a.transAxes)
    name="clock_temperature.png";finish(fig,directory/name)
    plots.append((name,"Temperature and remaining variation","The full-run fit is descriptive. The model-validation table below uses earlier training data to predict later hours. The history model uses previous temperature only, never future measurements."))
    return plots


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
        reference=np.array([base[i%2] for i in phase])
        note=f"References: half A {base[0]*1000:.6f} ms; half B {base[1]*1000:.6f} ms (pooled medians)"
    values=[t[field].to_numpy(float)*1e6-(0 if jitter else reference*1e6) for field in fields]
    finite=np.concatenate(values);finite=finite[np.isfinite(finite)]
    scale=max(float(np.max(np.abs(finite))) if len(finite) else 0,1e-3)
    colors=[BLUE if bins==15 or i%2==0 else ORANGE for i in phase]
    fig,axes=plt.subplots(1,len(fields),figsize=(8 if median_only else 13,7),subplot_kw={"projection":"polar"},layout="constrained")
    axes=np.atleast_1d(axes)
    for ax,v,title in zip(axes,values,titles):
        _draw_phase_polar(ax,phase,v,scale,jitter,colors,title+" (µs)")
    label="Full swing" if bins==15 else {"half":"Half-swing total","open":"Open component","blocked":"Blocked component"}[metric]
    fig.suptitle(f"{label}: {bins} phase bins · {basis.replace('_',' ')} · epoch {epoch}",fontsize=15)
    footer=("Unfiltered within-bin spread; no slow-drift subtraction" if jitter else note+"\nBars outward/inward from the grey zero ring show longer/shorter durations")
    if bins==30:footer+="\nBlue = half A (even bins); orange = half B (odd bins); travel direction unassigned"
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
            reference = np.where(phase%2==0,a,b)
            references.append(f"Half references: A {a*1000:.6f} ms; B {b*1000:.6f} ms (pooled medians)")
        colours = [BLUE if bins==15 or p%2==0 else ORANGE for p in phase]
        for row,field in enumerate(("median","std")):
            values = t[field].to_numpy(float)*1e6 - (reference*1e6 if row==0 else 0)
            finite = values[np.isfinite(values)]
            scale = max(float(np.max(np.abs(finite))) if len(finite) else 0,1e-3)
            title = f"{label} · {bins} phases\n" + ("Median deviation (µs)" if row==0 else "Jitter: standard deviation (µs)")
            _draw_phase_polar(axes[row,column],phase,values,scale,row==1,colours,title)
    fig.suptitle(f"PPS-calibrated phase medians and jitter · epoch {epoch}",fontsize=16)
    fig.supxlabel("\n".join(references)+"\nMedian bars: outward/inward from grey ring = longer/shorter. Jitter retains drift."
                  "\nHalf A = blue (even phases); half B = orange (odd phases); physical direction unassigned.",fontsize=10)
    finish(fig,path)
    return True


def plot_swing_boxes(frame, directory):
    """Tukey boxes of individual eligible events, grouped by sequence phase."""
    plots=[]
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    for epoch,group in frame.groupby("epoch"):
        for basis,valid,suffix in (("raw","raw_valid","_s"),("pps_calibrated","calibrated_valid","_pps_s")):
            f=group.loc[group[valid] & group.phase15.between(0,14)]
            if f.empty: continue
            for metric,bins in (("full",15),("half",30)):
                samples=[]
                for phase in range(bins):
                    column=("full" if bins==15 else ("tick_half" if phase%2==0 else "tock_half"))+suffix
                    values=f.loc[f.phase15==(phase if bins==15 else phase//2),column].to_numpy(float)*1000
                    samples.append(values[np.isfinite(values)])
                fig,ax=plt.subplots(figsize=(15,6),layout="constrained")
                boxes=ax.boxplot(samples,positions=np.arange(bins),widths=.6,whis=1.5,
                    patch_artist=True,showfliers=True,
                    flierprops=dict(marker=".",markersize=2,alpha=.25),
                    medianprops=dict(color="black",linewidth=1.2))
                for phase,box in enumerate(boxes["boxes"]):
                    box.set_facecolor(BLUE if bins==15 or phase%2==0 else ORANGE)
                    box.set_alpha(.55)
                ax.set_xticks(range(bins))
                ax.set_xticklabels([str(i) for i in range(bins)],fontsize=7)
                for phase,values in enumerate(samples):
                    if not len(values):
                        ax.text(phase,.02,"×",color=RED,ha="center",transform=ax.get_xaxis_transform())
                ax.set(xlabel="Sequence phase bin",ylabel="Duration (ms)",
                    title=f"Phase-{bins} {'full' if bins==15 else 'half'} swing · {basis.replace('_',' ')} · epoch {epoch}")
                ax.ticklabel_format(axis="y",style="plain",useOffset=False)
                ax.grid(axis="y",alpha=.2)
                caption="Boxes: Q1–Q3; black line: median; whiskers: furthest samples within 1.5 × IQR; dots: all outliers; ×: empty bin. Eligible individual events; no detrending."
                caption+=" Phase p = (extended sequence − origin) mod 15."
                if bins==30: caption+=" Tick = 2p (blue); tock = 2p+1 (orange)."
                fig.supxlabel("Sequence-based bins preserve gaps. "+("Blue = tick; orange = tock. " if bins==30 else "")+"Whiskers = 1.5 × IQR; dots = outliers.",fontsize=10)
                name=f"swing_e{epoch}_{basis}_{metric}_boxplot.png"
                finish(fig,directory/name)
                plots.append((name,f"Epoch {epoch}: {basis.replace('_',' ')} phase-{bins} box-and-whisker",caption))
    return plots


def plot_half_cancellation(frame, directory):
    """Paired halves from each event; density includes every eligible pair."""
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    plots=[]
    statistics=[]
    for epoch,group in frame.groupby("epoch"):
        for basis,valid,suffix in (("raw","raw_valid","_s"),("pps_calibrated","calibrated_valid","_pps_s")):
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
            caption="Each pair belongs to the same swing sequence. Left: each direction's epoch mean removed. Right: each direction's sequence-phase mean removed (p = (extended sequence − origin) mod 15). All eligible pairs shown; no time detrending. Full residual = tick residual + tock residual."
            fig.supxlabel("Dashed line: equal and opposite deviations leave the full swing unchanged. All eligible pairs included.",fontsize=10)
            name=f"swing_e{epoch}_{basis}_half_cancellation.png"
            finish(fig,directory/name)
            plots.append((name,f"Epoch {epoch}: {basis.replace('_',' ')} tick / tock cancellation",caption))
    columns=["epoch","basis","centring","count","correlation","tick_std_ms",
             "tock_std_ms","full_std_ms","independent_full_std_ms"]
    pd.DataFrame(statistics,columns=columns).to_csv(directory/"half_cancellation_statistics.csv",index=False)
    return plots


def plot_swing_diagnostics(swing,cfg,directory):
    plots=plot_swing_boxes(swing.frame,directory)
    plots+=plot_half_cancellation(swing.frame,directory)
    table=swing.tables["swing_phase"]
    totals=swing.tables["swing_component_summary"]
    for epoch in sorted(table.epoch.unique()):
        for basis in ("raw","pps_calibrated"):
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
        for (epoch,basis),g in table[(table.bin_count==bins)&(table.metric==metric)].groupby(["epoch","basis"]):
            ax.plot(g.phase,g["count"],".-",label=f"epoch {epoch}, {basis}")
        ax.set(xlabel="Sequence phase",ylabel="Eligible events",title=f"{bins}-bin exposure")
        ax.set_xticks(range(bins));ax.tick_params(axis="x",labelsize=7);ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    name="swing_phase_exposure.png";finish(fig,directory/name)
    plots.append((name,"Phase-bin exposure","Counts are reported for every bin, including empty bins. A missing row removes one full swing and its two halves; later events retain their original sequence phases."))
    weekly=swing.tables["swing_phase_weekly"]
    for epoch in sorted(weekly.epoch.unique()):
        fig,axes=plt.subplots(2,1,figsize=(13,8),layout="constrained")
        for ax,basis in zip(axes,("raw","pps_calibrated")):
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
        ax[0].plot(seconds,context.full_s,color=BLUE,lw=.8)
        ax[0].scatter(seconds[bad],context.loc[bad,"full_s"],s=10,color=RED)
        ax[0].set(ylabel="Full period (s)",title="Diagnostic raw captures: red records are excluded from metrology")
        ax[1].plot(seconds,context.tick_half_s,color=BLUE,label="Tick")
        ax[1].plot(seconds,context.tock_half_s,color=ORANGE,label="Tock")
        ax[1].set_ylabel("Half period (s)");ax[1].legend()
        ax[2].plot(seconds,context.tick_blocked_fraction,color=BLUE)
        ax[2].plot(seconds,context.tock_blocked_fraction,color=ORANGE)
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
        if plot_calibrated_phase_polars(swing,epoch,directory/name):
            plots.append((name,f"Epoch {epoch}: PPS-calibrated phase medians and jitter",
                          "Top row: full-swing (15-phase) and half-swing (30-phase) medians side by side. Median deviations use the pooled full-swing median or separate pooled half A/B medians. Bottom row: sample standard deviation of eligible individual events within the same phase bins; slow drift is retained. No second smoothing or robust-spread statistic is added. All phase-profile values use their full scales, without time-series display clipping. Missing statistics have a cross, including jitter with fewer than two events. References appear below the charts; absolute values and counts are in swing_phase.csv. Physical travel direction is unassigned."))
    if cfg.diagnostics:
        plots += plot_swing_diagnostics(swing, cfg, directory)
    return plots


def _table(frame,limit=24,digits=6):
    if frame is None or frame.empty:return "<p>No eligible data.</p>","No eligible data."
    view=frame.head(limit).copy()
    for c in view.select_dtypes(include="number"):
        view[c]=view[c].map(lambda v:f"{v:.{digits}g}" if pd.notna(v) else "unavailable")
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
    columns={"metric":"Measurement", "n":"Count", "mean":"Mean (s)",
             "median":"Median (s)", "std":"Std dev (s)",
             "robust_sigma":"Robust spread (s)", "p05":"P05 (s)",
             "p95":"P95 (s)", "min":"Min (s)", "max":"Max (s)"}
    table=swing.tables["swing_component_summary"]
    for (epoch,basis),group in table.groupby(["epoch","basis"],sort=True):
        if not diagnostics and basis != "pps_calibrated":
            continue
        view=group.set_index("metric").reindex(labels).reset_index()
        view["metric"]=view.metric.map(labels)
        title=f"Epoch {epoch}: {'Raw' if basis=='raw' else 'PPS-calibrated'} swing characteristics"
        selected = list(columns) if diagnostics else ["metric", "n", "mean", "std"]
        yield title,view[selected].rename(columns=columns)


def write_report(out,clock,swing,cfg,summary):
    out=Path(out);plots_dir=out/"plots";plots_dir.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({"font.size":10,"axes.titlesize":12,"figure.facecolor":"white","axes.spines.top":False,"axes.spines.right":False})
    plots=plot_clock(clock,cfg,plots_dir)
    if swing:
        swing_plots = plot_swings(swing,cfg,plots_dir)
        plots = plots + swing_plots if cfg.diagnostics else swing_plots + plots
    tables={**clock.tables,**(swing.tables if swing else {})}
    csvdir=out/"csv";csvdir.mkdir(exist_ok=True)
    for name,table in tables.items():table.to_csv(csvdir/(name+".csv"),index=False)
    s=clock.summary
    intro=f"{s['records']:,} PCPS records over {s['observed_days']:.5f} observed days. {s['eligible_intervals']:,} eligible one-second intervals."
    if swing:
        intro+=f" {swing.summary['records']:,} swing records, with {swing.summary['pps_calibrated_eligible']:,} PPS-calibrated eligible swings."
        if cfg.diagnostics:
            intro+=f" {swing.summary['raw_eligible']:,} raw eligible swings."
        series = swing.summary["time_series"]
        intro+=f" {series['complete_cycles']:,} complete 15-swing groups; {series['incomplete_cycles']:,} partial or excluded groups."
    sections=[("Overview",intro)]
    sections.append(("Clock result",f"Mean frequency offset: {s['offset_ppm']:.6f} ppm relative to PPS, equivalent to {s['uncorrected_ms_per_day']:.3f} ms/day at nominal frequency. This is not a measurement of disciplined firmware time. The reference and local clock cannot be independently separated using PCPS alone."
                     if s["eligible_intervals"] else "No eligible one-second intervals. Clock frequency, phase and stability are unavailable; invalid and excluded records remain in the diagnostic exports."))
    notable = "; ".join(f"{key.replace('_', ' ')}: {value:,}" for key, value in s["event_counts"].items() if value)
    if notable:
        sections.append(("PPS events", notable + ". These are classified observations; complete event rows and reasons are exported."))
    if swing is None:
        sections.append(("Swing coverage","PCSW.CSV is absent. Swing timing, phase and jitter analyses are unavailable."))
    stale=clock.tables["environment_stale_runs"]
    if len(stale):
        sections.append(("Environmental validity"," ".join(f"{r.channel}: {int(r.records):,} unchanged records at {r.value:g}, from day {r.start_day:.5f} to {r.end_day:.5f}. This interval is excluded from environmental fitting." for r in stale.itertuples())))
    else:sections.append(("Environmental validity","No unchanged run meets the configured staleness threshold. This is a screening result, not independent sensor validation."))
    if swing:
        sections.append(("Synchronome measurement validity" if cfg.profile=="synchronome" else "Swing measurement validity",
            f"Profile: {cfg.profile}. {swing.summary['event_counts']['suspected_optical_clearance_loss']:,} records are flagged for suspected optical clearance loss and excluded from all timing, phase and jitter statistics. For Synchronome, either a blocked fraction above {cfg.max_blocked_fraction:.0%} of its half or a half-period departure above {cfg.half_period_tolerance:.0%} is sufficient. These are conservative geometry/period guards, not a proof of the physical cause. Raw rejected values remain in diagnostic events and episode plots."))
        sections.append(("Mechanical phase",f"Phase p = (extended PCSW sequence − {cfg.phase_origin}) mod 15. Phase zero is a sequence convention; physical travel direction and release/reset timing are unassigned. Half A is edge 0→2; half B is edge 2→4. Epochs are reported separately; alignment across a reset is unknown. PCPS sequence and CSV row number never determine mechanical phase."))
        if swing.summary["calibration_unavailable_reason"]:
            sections.append(("Calibration coverage",swing.summary["calibration_unavailable_reason"]))
        sections.append(("Clock alignment",swing.summary["alignment_assumption"]))
    if "timescale" in s:
        meta=s["timescale"]
        sections.append(("PPS timescale",f"Estimator: {meta.get('estimator', 'unavailable')}. "
                         f"{meta.get('method', '')}. Window: {meta.get('window_seconds', 'unavailable')} seconds. "
                         f"Boundary policy: {meta.get('boundary_policy', 'unavailable')}. "
                         f"Calibration runs: {meta.get('calibration_runs', 0)}."))
    sections.append(("Methods and limits","All source records are read in file order. Integer-field validity, sequence continuity, timestamp arithmetic and cumulative-drop changes determine eligibility. Unknown time across epochs is not reconstructed. Stale-channel detection flags the complete unchanged run retrospectively; it does not measure sensor update age. No global robust clipping removes the impulse pattern. Component totals are calculated per event before aggregation, and medians are never added. Raw jitter retains slow drift. The historic six-cycle projection and large-latency signatures remain diagnostic events, not the focus of this report."))
    if cfg.diagnostics:
        sections.append(("Model evaluation","Candidate models are fitted to matched hourly data. Chronological holdouts use only earlier rows for training; the constant baseline uses the same training/test population as its candidate. Any slope intervals resample entire elapsed days and are approximate. Coefficients describe association, not causation or an oscillator-temperature calibration. Pressure models are not fitted automatically because valid coverage may be short; its eligible interval remains available for a separate investigation."))
    html=["<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Clock and swing analysis</title><style>body{font:16px/1.55 system-ui,sans-serif;color:#203047;background:#f3f5f8;margin:0}main{max-width:1200px;margin:auto;padding:32px;background:white}h1{font-size:32px}h2{margin-top:36px}p{max-width:1050px}img{width:100%;height:auto}figure{margin:28px 0;padding-bottom:24px;border-bottom:1px solid #d9dfe8}figcaption{font-size:14px;color:#536176}table{border-collapse:collapse;font-size:13px;width:100%}th,td{padding:8px;border-bottom:1px solid #ddd;text-align:right}th:first-child,td:first-child{text-align:left}th{background:#e9eef5}a{color:#2458a6}.scroll{overflow:auto}nav{display:flex;gap:20px;flex-wrap:wrap}@media print{body,main{background:white;padding:0}figure{break-inside:avoid}details{display:block}}</style><main><h1>Clock and swing analysis</h1><nav><a href='#plots'>Charts</a><a href='#models'>Model validation</a><a href='#data'>Data exports</a><a href='summary.json'>Summary and provenance</a></nav>"]
    if swing is not None:
        html[0]=html[0].replace("<a href='#plots'>", "<a href='#swings'>Swing summaries</a><a href='#plots'>",1)
    if not cfg.diagnostics:
        html[0] = html[0].replace("<a href='#models'>Model validation</a>", "")
    md=["# Clock and swing analysis\n"]
    for title,body in sections:
        html.append(f"<h2>{escape(title)}</h2><p>{escape(body)}</p>")
        md.append(f"## {title}\n\n{body}\n")
    def append_charts():
        html.append("<h2 id='plots'>Charts</h2>")
        for name,title,caption in plots:
            html.append(f"<figure><h3>{escape(title)}</h3><img loading='lazy' src='{png_data_uri(plots_dir/name)}' alt='{escape(title)}'><figcaption>{escape(caption)}</figcaption></figure>")
            md.append(f"## {title}\n\n![{title}](plots/{name})\n\n{caption}\n")
    if not cfg.diagnostics:
        append_charts()
    for name,title,caption,columns,limit in pps_tables():
        if not cfg.diagnostics and name not in ("clock_data_quality", "clock_gps_states", "clock_timebase_health", "clock_timing_summary"):
            continue
        frame=tables[name]
        if not cfg.diagnostics and name == "clock_timing_summary":
            frame = frame[frame.metric.eq("Raw frequency offset") & frame.unit.eq("ppm")]
            columns = ["metric", "unit", "n", "mean", "std"]
            caption = "Measured oscillator frequency against PPS; mean offset and sample standard deviation in ppm. Spread includes drift. Full timing distributions remain in the CSV export."
        if columns is not None:
            frame=frame[[c for c in columns if c in frame]]
        a,b=_table(frame,limit=limit,digits=10)
        html.append(f"<h2>{escape(title)}</h2><p>{escape(caption)}</p><div class='scroll'>{a}</div>")
        md.append(f"## {title}\n\n{caption}\n\n{b}\n")
    if swing is not None:
        html.append("<h2 id='swings'>Swing summary tables</h2>")
        md.append("## Swing summary tables\n")
        caption=("All durations and spreads are in seconds. Statistics use complete eligible events, "
                 "with raw and PPS-calibrated populations and timeline epochs kept separate. "
                 "Count is the number of finite eligible events for that measurement. "
                 "Std dev is the sample standard deviation; robust spread is 1.4826 × MAD. "
                 "P05 and P95 are the 5th and 95th percentiles. Spread includes slow drift and the phase pattern. "
                 "Missing statistics are unavailable; a single event has no sample standard deviation. "
                 "Raw and calibrated counts can differ where PPS coverage is unavailable.")
        if not cfg.diagnostics:
            caption = "PPS-calibrated complete eligible events, with epochs kept separate. Mean and sample standard deviation are in seconds. Spread includes slow drift and the repeating phase pattern. Physical directions are unassigned. Full distributions and raw results remain in the CSV export."
        html.append(f"<p>{escape(caption)}</p>")
        md.append(caption+"\n")
        for title,frame in swing_summary_tables(swing, cfg.diagnostics):
            a,b=_table(frame,limit=len(frame),digits=10)
            html.append(f"<h3>{escape(title)}</h3><div class='scroll'>{a}</div>")
            md.append(f"### {title}\n\n{b}\n")
        link="csv/swing_component_summary.csv"
        html.append(f"<p><a href='{link}'>Complete swing component CSV</a></p>")
        md.append(f"[Complete swing component CSV]({link})\n")
        html.append("<h2>Complete-cycle rate variation</h2>")
        caption = ("Variation of complete 15-swing mean periods, for each whole epoch and the selected four-hour window. "
                   "Peak to peak is maximum minus minimum; RMS is the root mean square deviation from that population's mean, with drift retained. "
                   "The final column removes only a least-squares straight line in observed elapsed time, addressing the effect of trend on RMS. "
                   "It still includes any nonlinear drift and measurement noise, and is not a pure pendulum-noise estimate. "
                   "Groups are equally weighted; exclusions remain gaps. Full coefficients, mean frequency offsets and window boundaries are in swing_frequency_summary.csv.")
        html.append(f"<p>{escape(caption)}</p>")
        md.append("## Complete-cycle rate variation\n\n" + caption + "\n")
        frequency = tables["swing_frequency_summary"].copy()
        frequency["Start (h)"] = frequency.window_start_s / 3600
        frequency_columns = {"epoch": "Epoch", "scope": "Population", "Start (h)": "Start (h)", "cycles": "15-swing groups",
                             "period_peak_to_peak_us": "Peak to peak (µs)", "period_rms_us": "RMS (µs)",
                             "detrended_period_rms_us": "RMS after linear trend (µs)", "status": "Status"}
        view = frequency[list(frequency_columns)].rename(columns=frequency_columns)
        a,b = _table(view, limit=len(view))
        html.append(f"<div class='scroll'>{a}</div>"); md.append(b + "\n")
        html.append("<h2 id='swing-environment'>Swing period and environment</h2>")
        md.append("## Swing period and environment\n")
        caption = ("Separate single-sensor linear fits to hourly mean periods from complete PPS-calibrated 15-swing groups. "
                   "Each sensor and period use exactly the same groups; all 15 sensor readings must pass physical/stale screening. "
                   "Hours need at least 30 nominal minutes of complete groups, and fits need at least 24 eligible hours within one epoch. "
                   "Slopes are in microseconds per sensor unit; positive means a longer period. R² describes the full-run association. "
                   "These correlated time series can share drift, so the fits do not establish causation or an independent sensor effect. "
                   "No environmental correction is applied to the swing results.")
        html.append(f"<p>{escape(caption)}</p>")
        md.append(caption + "\n")
        models = tables["swing_environment_fits"].copy()
        labels = {"temperature_C": "Temperature (°C)", "humidity_pct": "Humidity (% RH)", "pressure_hPa": "Pressure (hPa)"}
        models["channel"] = models.channel.map(labels)
        models["Sensor range"] = models.apply(lambda r: f"{r.sensor_min:.6g} to {r.sensor_max:.6g}" if pd.notna(r.sensor_min) else "unavailable", axis=1)
        models["Coverage (days)"] = models.apply(lambda r: f"{r.start_day:.5g} to {r.end_day:.5g}" if pd.notna(r.start_day) else "unavailable", axis=1)
        fit_columns = {"epoch": "Epoch", "channel": "Sensor", "hours": "Hours", "cycles": "15-swing groups",
                       "Coverage (days)": "Coverage (days)", "Sensor range": "Sensor range",
                       "slope_us_per_unit": "Slope (µs/unit)", "r_squared": "R²", "status": "Status"}
        view = models[list(fit_columns)].rename(columns=fit_columns)
        a,b = _table(view, limit=len(view))
        html.append(f"<div class='scroll'>{a}</div>"); md.append(b + "\n")
        caption = ("Chronological check: fit the earliest 70% of eligible hours and predict the latest 30%, "
                   "with at least 24 training and 12 test hours. The constant baseline uses only the same training hours. "
                   "Lower test RMSE is better; a worse result than the constant baseline indicates that the fitted association did not transfer to later data. "
                   "RMSE is in microseconds of full period. Hourly averaging does not make adjacent hours independent, so no independent-sample p-values are reported. "
                   "Paired hourly samples, full coefficients and validation results are exported as swing_environment_* CSVs.")
        html.append(f"<h3>Prediction of later hours</h3><p>{escape(caption)}</p>")
        md.append("### Prediction of later hours\n\n" + caption + "\n")
        check = tables["swing_environment_validation"].copy()
        check["channel"] = check.channel.map(labels)
        check_columns = {"epoch": "Epoch", "channel": "Sensor", "train_hours": "Train hours", "test_hours": "Test hours",
                         "fit_rmse_us": "Fit RMSE (µs)", "constant_rmse_us": "Constant RMSE (µs)", "status": "Status"}
        view = check[list(check_columns)].rename(columns=check_columns)
        a,b = _table(view, limit=len(view))
        html.append(f"<div class='scroll'>{a}</div>"); md.append(b + "\n")
    if cfg.diagnostics:
        html.append("<h2 id='models'>Environmental model validation</h2>")
        md.append("## Environmental model validation\n")
        for name in ("environment_models","environment_validation","environment_weekly"):
            a,b=_table(clock.tables[name])
            html.append(f"<h3>{escape(name.replace('_',' '))}</h3><div class='scroll'>{a}</div>")
            md.append(f"### {name.replace('_',' ')}\n\n{b}\n")
        append_charts()
    html.append("<h2 id='data'>Data exports</h2><ul>")
    md.append("## Data exports\n")
    for name in tables:
        html.append(f"<li><a href='csv/{name}.csv'>{name.replace('_',' ')}</a></li>")
        md.append(f"- [{name}](csv/{name}.csv)")
    if swing is not None and (plots_dir/"half_cancellation_statistics.csv").is_file():
        link="plots/half_cancellation_statistics.csv"
        html.append(f"<li><a href='{link}'>Paired tick / tock cancellation statistics</a></li>")
        md.append(f"- [Paired tick / tock cancellation statistics]({link})")
    html.append("</ul><p>Charts are embedded in this HTML; CSV and summary links require the companion output files. Counts, masks and settings are reproducible. Source files are unchanged. Input and implementation fingerprints are in summary.json.</p></main></html>")
    (out/"report.html").write_text("\n".join(html))
    (out/"report.md").write_text("\n".join(md))
    return [name for name,_,_ in plots]
