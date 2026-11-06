"""Headless figures and a self-contained local report for PPS diagnostics."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
import platform
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "pendulum_pps_matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
import numpy as np
import pandas as pd

from .core import PpsResult, sha256
from ..report_assets import png_data_uri

BLUE = "#245a78"
RED = "#bb4f32"
GREEN = "#3d745a"


def plain(value):
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _empty(ax, message):
    ax.text(.5, .5, message, ha="center", va="center", transform=ax.transAxes, wrap=True)
    ax.set_xticks([])
    ax.set_yticks([])


def _finish(fig, path):
    for ax in fig.axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=.15)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def capture_overview(result: PpsResult):
    """Separate wrap-related latency from the background shoulder.

    Use observed values, not the recording-specific 63/82/135-cycle guides.
    Keep rare large delays in the full histogram CSV and disclose plot limits.
    """
    f = result.frame.loc[result.frame.row_valid, ["edge_tcb0", "latency16"]].copy()
    left = -min(150, result.config.wrap_window_cycles)
    right = min(180, result.config.wrap_window_cycles)
    f["phase"] = (f.edge_tcb0 + 32768) % 65536 - 32768
    near = f.phase.between(left, right)
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), constrained_layout=True)
    fig.suptitle(f"PPS / TCB2 latency: {len(f):,} valid captures\n"
                 "TCB0 overflow shape and the background shoulder", fontsize=17)
    if f.empty:
        for ax in axes.flat:
            _empty(ax, "No valid capture records")
        return fig
    baseline = int(f.latency16.mode().iloc[0])
    ceiling = max(250, baseline + 100)
    hist = pd.crosstab(f.latency16, near).reindex(columns=[False, True], fill_value=0)
    view = hist.loc[hist.index <= ceiling]
    ax = axes[0, 0]
    ax.bar(view.index, view[False], width=.85, color=BLUE, label="Away from wrap")
    ax.bar(view.index, view[True], bottom=view[False], width=.85, color=RED,
           label=f"Near wrap: {left} to +{right} cycles")
    ax.set(yscale="log", xlabel="latency16 (cycles)", ylabel="Captures (log scale)",
           title="The histogram mixes distinct mechanisms")
    ax.legend(fontsize=8)
    omitted = int(f.latency16.gt(ceiling).sum())
    if omitted:
        ax.text(.98, .68, f"{omitted:,} above {ceiling} cycles\nFull distribution: latency_histogram.csv",
                ha="right", va="top", transform=ax.transAxes, fontsize=8)

    boundary = f.loc[near & f.latency16.le(ceiling)]
    counts = boundary.groupby(["phase", "latency16"]).size().reset_index(name="records")
    minimum = boundary.groupby("phase").latency16.min()
    for ax, sample_time in ((axes[0, 1], False), (axes[1, 1], True)):
        ax.set(xlabel="Edge phase relative to TCB0 wrap (cycles)", xlim=(left, right))
        if counts.empty:
            _empty(ax, "No wrap-boundary captures within the plot latency limit")
            continue
        y = counts.latency16 + counts.phase if sample_time else counts.latency16
        scatter = ax.scatter(counts.phase, y, c=counts.records, s=16, cmap="viridis",
                             norm=LogNorm(vmin=1, vmax=max(2, int(counts.records.max()))))
        guide = minimum + minimum.index if sample_time else minimum
        ax.plot(minimum.index, guide, "--", color=".25", lw=.8, label="Observed minimum at each phase")
        ax.axvline(0, color=".5", lw=.8)
        ax.set(ylabel="Counter sample time relative to wrap (cycles)" if sample_time else "latency16 (cycles)",
               title="Waiting captures converge on a common sample time" if sample_time
               else "Reread rail before wrap; waiting ramp after")
        if not sample_time:
            fig.colorbar(scatter, ax=ax, label="Captures at exact coordinate", shrink=.75)
            ax.legend(fontsize=8)
        hidden = int((near & f.latency16.gt(ceiling)).sum())
        if hidden:
            ax.text(.98, .02, f"{hidden:,} boundary captures above {ceiling}-cycle latency limit",
                    ha="right", transform=ax.transAxes, fontsize=8)

    ax = axes[1, 0]
    away = f.loc[~near, "latency16"]
    low = baseline + 5
    high = min(int(away.max()), baseline + 100) if len(away) else low
    if len(away) and high >= low:
        shoulder = away.value_counts().reindex(range(low, high + 1), fill_value=0)
        ax.bar(shoulder.index, shoulder, width=.85, color=BLUE)
        ax.set(xlabel="latency16 (cycles)", ylabel="Captures away from wrap",
               title="Background shoulders with nearby overflows excluded",
               xlim=(low - .6, high + 1.6))
        ax.text(.98, .96, f"Observed away maximum: {int(away.max())} cycles\n"
                f"{int(away.lt(low).sum()):,} below {low}; {int(away.gt(high).sum()):,} above {high}",
                ha="right", va="top", transform=ax.transAxes, fontsize=8)
        ax.set_ylim(0, max(1, int(shoulder.max())) * 1.3)
    else:
        _empty(ax, "No away-from-wrap captures above the baseline zoom cutoff")
    return fig


def make_plots(result: PpsResult, directory: Path) -> list[tuple[str, str, str]]:
    directory.mkdir(parents=True, exist_ok=True)
    f, t, s, cfg = result.frame, result.tables, result.summary, result.config
    plots = []
    plt.rcParams.update({"font.size": 10, "axes.titlesize": 12, "axes.labelsize": 10,
                         "figure.facecolor": "white", "axes.facecolor": "white"})
    fig = capture_overview(result)
    filename = "01_capture_overview.png"
    _finish(fig, directory / filename)
    plots.append((filename, "Capture overview",
                  "Valid captures are split by reconstructed TCB0 wrap phase. "
                  "The shoulder panel excludes nearby wraps and the dominant baseline bins. "
                  "Counter sample phase is edge phase plus latency16. Dashed lines show observed "
                  "per-phase minima, not a firmware timing model. No projection correction is applied."))

    fig, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)
    fig.suptitle("Latency over time and relative to swing completion", fontsize=17)
    daily = t["daily_rates"]
    ax = axes[0, 0]
    ax.plot(daily.day, daily.latency_max, ".-", color=BLUE, label="Daily maximum")
    e = t["events"]
    large = e[e.event.str.contains("large_latency")]
    ax.scatter(large.elapsed_cycles / cfg.nominal_hz / 86400, large.latency16, s=13, color=RED, label="Every large spike")
    ax.set(xlabel="Observed elapsed time (days)", ylabel="Capture latency (cycles)", title="Daily maxima retain rare spikes")
    ax.legend(fontsize=8)
    ax = axes[0, 1]
    ax.plot(daily.day, daily.shifted_per_million, ".-", color=RED, label="Changed projection")
    ax.plot(daily.day, daily.large_latency_per_million, ".-", color=BLUE, label="Large latency")
    ax.set(xlabel="Observed elapsed time (days)", ylabel="Events per million recorded captures", title="Event rates with recorded exposure")
    ax.legend(fontsize=8)
    ax = axes[1, 0]
    if "since_edge4_us" in f:
        view = f[f.since_edge4_us.between(0, 2000) & f.row_valid]
        if len(view):
            ax.hexbin(view.since_edge4_us, view.latency16, gridsize=(100, 60), mincnt=1, norm=LogNorm(), cmap="Blues", linewidths=0)
        ax.scatter(large.since_edge4_us, large.latency16, s=12, color=RED, label="Every large spike")
        ax.set(xlabel="Time since preceding edge4 (µs)", ylabel="Capture latency (cycles)", title="First 2 ms following completed swings", xlim=(0, 2000))
        ax.legend(fontsize=8)
        ax.text(.98, .96, f"{int((large.since_edge4_us > 2000).sum()):,} large spikes outside 2 ms\n{int(large.since_edge4_us.isna().sum()):,} without a swing match", ha="right", va="top", transform=ax.transAxes, fontsize=8)
    else:
        _empty(ax, s["swing_association"]["reason"])
    ax = axes[1, 1]
    b = t["swing_latency_bins"]
    b = b[b.since_edge4_us_end <= 2000]
    if len(b):
        ax.bar((b.since_edge4_us_start + b.since_edge4_us_end) / 2, b.large_per_million, width=45, color=RED)
        ax.set(xlabel="Time since preceding edge4 (µs)", ylabel="Large spikes per million captures in bin", title="Spike incidence accounts for swing-phase coverage")
    else:
        _empty(ax, "Swing-phase exposure unavailable")
    filename = "02_latency_and_swings.png"
    _finish(fig, directory / filename)
    plots.append((filename, "Latency and swing completion", "The swing histogram divides spike counts by all associated captures in each bin. Unmatched records are reported separately."))

    fig, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)
    fig.suptitle("Oscillator frequency, recording gaps and exceptional captures", fontsize=17)
    h = t["hourly_frequency"]
    ax = axes[0, 0]
    for segment, group in h.groupby("segment"):
        ax.plot(group.hour / 24, group.raw_mean_cycles / cfg.nominal_hz * 1e6, color=BLUE, lw=1, label="Raw" if segment == h.segment.iloc[0] else None)
        ax.plot(group.hour / 24, group.adjusted_mean_cycles / cfg.nominal_hz * 1e6, color=RED, lw=.8, alpha=.7, label="Diagnostic normalized" if segment == h.segment.iloc[0] else None)
    ax.set(xlabel="Observed elapsed time (days)", ylabel="Hourly oscillator offset (ppm)", title="Oscillator cycles per GPS second")
    ax.legend(fontsize=8)
    ax = axes[0, 1]
    for col, color, label in (("frequency_offset_cycles", BLUE, "Raw"), ("diagnostic_adjusted_offset_cycles", RED, "Diagnostic normalized")):
        counts = f.loc[f.frequency_valid, col].value_counts().sort_index()
        if len(counts):
            ax.step(counts.index, counts, where="mid", color=color, label=label)
    ax.set(xlabel="Oscillator cycles per GPS second minus nominal", ylabel="Intervals (log scale)", title="Full-data interval distributions", yscale="log")
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(fontsize=8)
    else:
        _empty(ax, "No eligible frequency intervals")
    ax = axes[1, 0]
    candidates = e[e.event.str.contains("possible_extra_capture")]
    if len(candidates):
        source = candidates.iloc[0].source_row
        windows = t["event_windows"]
        w = windows[windows.event_source_row == source]
        ax.plot(w.relative_record, w.interval_cycles / cfg.nominal_hz, "o-", color=RED)
        ax.axhline(1, color=".6", lw=1)
        ax.set(xlabel=f"Records relative to sequence {int(candidates.iloc[0].seq)}", ylabel="Interval (nominal seconds)", title=f"First of {len(candidates):,} possible extra captures")
    else:
        _empty(ax, "No pair of short intervals summing to a normal second")
    ax = axes[1, 1]
    runs = t["state_runs"]
    for _, row in runs.iterrows():
        if np.isfinite(row.gps_status):
            ax.plot([row.start_cycles / cfg.nominal_hz / 86400, row.end_cycles / cfg.nominal_hz / 86400], [row.gps_status] * 2, color=BLUE, lw=2, marker=".")
    gaps = e[e.event.str.contains("sequence_gap")]
    for x in gaps.elapsed_cycles / cfg.nominal_hz / 86400:
        ax.axvline(x, color=RED, lw=1, alpha=.65)
    ax.set(xlabel="Observed elapsed time (days)", ylabel="Recorded GPS state", title=f"GPS state and {len(gaps)} sequence gaps")
    ax.set_yticks([0, 1, 2, 3], ["0: free run", "1: acquiring", "2: locked", "3: holdover"])
    filename = "03_frequency_and_integrity.png"
    _finish(fig, directory / filename)
    plots.append((filename, "Frequency and integrity", "Frequency statistics require consecutive, locked endpoints with consistent reconstruction and no newly reported PPS drops. Gaps are not averaged into one-second intervals."))
    return plots


def _fmt(value, digits=4):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "unavailable"
    if isinstance(value, (int, np.integer)):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def summary_sections(result):
    tolerance = result.config.normal_interval_tolerance*100
    return [
        ("pps_characteristics", "PPS timing characteristics",
         f"These eight metrics use this report's eligible frequency intervals: consecutive, locked and reconstruction-consistent at both endpoints, no new cumulative PPS drop, and duration within {tolerance:g}% of one nominal second. The older canonical report used a broader filter (0.5–1.5 nominal seconds, current-row lock and zero drop counter); counts and extrema can therefore differ. Raw error is captured interval cycles minus nominal_hz. Nanoseconds = cycles / nominal_hz × 10⁹; ppm = cycles / nominal_hz × 10⁶. "
         + result.summary["offline_residual_method"] + " Robust spread is 1.4826 × MAD. Zero spread does not mean all values are equal: exact counts are in pps_discrete_counts.csv. latency16 and cap16 here use the same eligible interval endpoints."),
        ("pps_data_quality", "PPS data quality", "Counts use all input records. Unknown time between timeline segments is excluded from observed duration."),
        ("pps_gps_states", "GPS status counts", "Record percentages use all input records, with missing or invalid states shown separately. They are not elapsed-time fractions."),
        ("pps_timebase_health", "GPS lock and holdover", "Lock transitions require adjacent valid consecutive captures in one segment. Missing holdover telemetry is unavailable."),
        ("pps_latency_summary", "Capture/ISR latency summary", "All captures with valid required fields, including unlocked states. Latency is modulo 65,536 cycles and measures capture/ISR service. It is not pendulum jitter."),
        ("pps_cap16_coverage", "cap16 phase coverage", "All captures with valid required fields. Coverage is a capture-system sanity check, not a precision score. Bin ratios compare 256 equal bins against uniform coverage; all counts are in pps_cap16_bins.csv."),
    ]


def table_markdown(table):
    def cell(value):
        return _fmt(value, digits=8).replace("|", "\\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(table.columns) + " |",
                       "| " + " | ".join(["---"]*len(table.columns)) + " |"] +
                      ["| " + " | ".join(cell(v) for v in row) + " |"
                       for row in table.itertuples(index=False, name=None)])


def write_report(result: PpsResult, output: Path, export_intervals: bool = False) -> None:
    output.mkdir(parents=True, exist_ok=True)
    csv_dir = output / "csv"
    csv_dir.mkdir(exist_ok=True)
    for name, table in result.tables.items():
        table.to_csv(csv_dir / (name + ".csv"), index=False)
    if export_intervals:
        result.frame.to_csv(csv_dir / "intervals.csv.gz", index=False, compression="gzip")
    plots = make_plots(result, output / "plots")
    s = result.summary
    s["warnings"] = result.warnings
    s["provenance"] = {
        "inputs": {role: {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)} for role, path in result.inputs.items()},
        "analysis_source_sha256": {p.name: sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
        "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__, "matplotlib": matplotlib.__version__,
    }
    s["complete"] = True
    cfg = result.config
    summary_rows = [
        ("Recorded captures", s["records"]), ("Observed duration (days)", s["observed_duration_days"]),
        ("Timeline segments", s["segments"]), ("Eligible one-second frequency intervals", s["frequency_valid_intervals"]),
        ("Malformed records", s["malformed_records"]), ("Missing sequence records", s["missing_sequence_records"]),
        (f"Latency exactly {cfg.rail_latency_cycles} cycles", s["rail_records"]),
        ("Changed projection", s["projection_shifted_records"]), ("Positive six-cycle projection shift", s["six_cycle_shift_records"]),
        (f"Latency above {cfg.large_latency_cycles} cycles", s["event_counts"]["large_latency"]),
        ("Largest spike (µs)", s["large_latency_max_us"]),
        ("Large spikes with changed projection", s["large_latency_projection_shifted_records"]),
        ("Inconsistent now32 − edge = latency records", s["event_counts"]["reconstruction_mismatch"]),
    ]
    events = result.tables["events"]
    relevant = ["event", "source_row", "seq", "seq_delta", "interval_cycles", "latency16", "projection_shift_cycles", "since_edge4_us"]
    relevant = [c for c in relevant if c in events]
    integrity = events[events.event.str.contains("sequence_gap|possible_extra_capture|ambiguous_timeline|malformed_record|reconstruction_mismatch")][relevant]
    largest = events[events.event.str.contains("large_latency")].nlargest(12, "latency16")[relevant]
    offset = result.tables["projection_offsets"]
    temp = s["temperature_fit"]
    swing = s["swing_association"]
    findings = [
        f"The modal capture-to-timestamp relationship is computed separately in each timeline segment. {s['projection_shifted_records']:,} records differ from it; {s['rail_shifted_records']:,} of the {s['rail_records']:,} rail records differ.",
        f"There are {s['event_counts']['possible_extra_capture']:,} candidate extra captures, {s['event_counts']['sequence_gap']:,} sequence gaps and {s['event_counts']['gps_state_change']:,} GPS state changes. These are observations; the CSV alone does not locate a missing record within the logging chain.",
        f"The raw mean oscillator offset is {_fmt(s['raw_frequency_offset_cycles']['mean'])} cycles per GPS second; the diagnostic-normalized mean is {_fmt(s['diagnostic_adjusted_frequency_offset_cycles']['mean'])}. Assuming stable GPS PPS, this measures the oscillator, not GPS frequency drift.",
        f"Temperature fit: {_fmt(temp['slope_ppm_per_C'])} ppm/°C, correlation {_fmt(temp['correlation'])}, using {temp['hours']:,} hours with at least {cfg.min_hourly_samples} eligible temperature/frequency samples. This is an environmental sensor correlation, not an oscillator-temperature calibration or causal test.",
    ]
    if swing["available"]:
        d = swing["large_latency_since_edge4_us"]
        findings.append(f"{swing['associated_large_latency_records']:,} of {swing['large_latency_records']:,} large spikes were associated with preceding swing completion, {_fmt(d['min'])}–{_fmt(d['max'])} µs later. {s['large_latency_projection_shifted_records']:,} large spikes have changed projection. Preserved projection is evidence of compensation, but cannot itself bound unseen capture loss or modulo-latency aliasing.")
    else:
        findings.append("Swing association unavailable: " + swing["reason"] + ".")
    methods = [
        "All statistics, histograms, anomaly classifications and phase-bin counts use the full input. The event CSV retains every classified record, with semicolon-separated reasons. Report tables are explicitly limited selections; event windows include every event and never cross segment boundaries.",
        "source_row is the CSV record ordinal with the header counted as 1. Inputs are read in file order, never sorted or edited. Invalid unsigned integer fields remain events and break interval continuity. Broken CSV syntax or missing required columns fail the command with a nonzero exit code.",
        "32-bit counter wraps are reconstructed in file order. A sequence gap can imply additional counter wraps only when its timestamp delta agrees with one PPS per sequence step to within 0.1 nominal second. Resets, duplicate/reversed sequences, malformed rows and ambiguous chronology split segments. Elapsed time joins observed spans; unobserved time between segments is unknown. It is not UTC.",
        "Frequency intervals require adjacent valid captures, sequence step 1, gps_status=2 at both endpoints, no new drop_pps count, consistent now32−edge=latency modulo 2^32 at both endpoints, and duration within the configured normal tolerance. drop_pps is cumulative: an old nonzero count does not exclude every later row.",
        "Projection offset = (cap16 − edge_tcb0) mod 65536. Edge shift = signed circular difference between the segment's modal offset and the observed offset. Diagnostic interval normalization subtracts the change in edge shift between endpoints. Only shifts within the configured maximum are normalized. No source timestamp is replaced, and no rule uses latency=152 as a correction trigger.",
        "A +6-cycle shifted edge lengthens the incoming interval by six cycles and shortens the following interval if that edge returns to baseline. The normalization tests a stable timer-phase model. It cannot distinguish all physical timer-phase changes from reconstruction changes.",
        "latency16 is modulo 65536. At 16 MHz it cannot distinguish a delay from that delay plus 4.096 ms. now32−edge consistency is an arithmetic check, not an independent proof of physical timestamp accuracy. Firmware execution bounds and a fresh recording are needed to validate a firmware fix.",
        "Swing association requires single ordered overlapping streams. Their starts are assumed to differ by less than half a 32-bit wrap. The shared raw counter selects the initial epoch; missing/late matches are masked. Without external epoch information, a whole-counter-wrap file offset cannot be independently excluded. Per-bin spike rates include the exposure from ordinary PPS captures.",
        "A possible extra capture is identified by two positive consecutive short intervals adding to a normal second within 1%. This is an electrical/recording hypothesis, not a diagnosis of GPS hardware. Status transitions and exceptional intervals remain visible regardless of frequency exclusions.",
        "No network services, AI models, credentials, interactive prompts or UNO.CSV access are used. The resolved settings and input/code SHA-256 fingerprints are in summary.json.",
    ]
    def table_html(table, limit=20):
        if table.empty:
            return "<p>No matching records.</p>"
        view = table.head(limit).copy().astype(object)
        for column in view:
            view[column] = view[column].map(lambda v: _fmt(v, digits=8))
        return f"<p>Showing {min(len(table), limit):,} of {len(table):,} rows.</p>" + view.to_html(index=False, border=0, escape=True)
    body = ["<h1>PPS capture analysis</h1>", "<p>Automatic analysis of captured PPS timing, oscillator frequency and swing-associated latency.</p>",
            "<table><thead><tr><th>Measurement</th><th>Result</th></tr></thead><tbody>" + "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(_fmt(v))}</td></tr>" for k,v in summary_rows) + "</tbody></table>",
            "<h2>Findings</h2>" + "".join("<p>" + html.escape(x) + "</p>" for x in findings),
            "<h2>Coverage and limitations</h2>" + ("<ul>" + "".join("<li>" + html.escape(x) + "</li>" for x in result.warnings) + "</ul>" if result.warnings else "<p>No input coverage warnings.</p>")]
    for name, title, caption in summary_sections(result):
        body += [f"<h2>{html.escape(title)}</h2><p>{html.escape(caption)}</p>",
                 table_html(result.tables[name]), f"<p><a href='csv/{name}.csv'>Complete CSV</a></p>"]
    for filename, title, caption in plots:
        body += [f"<h2>{html.escape(title)}</h2><img src='{png_data_uri(output / 'plots' / filename)}' alt='{html.escape(title)}'><p>{html.escape(caption)}</p>"]
    body += ["<h2>Projection states</h2>", table_html(offset), "<h2>Recording anomalies</h2>", table_html(integrity),
             "<h2>Largest latency events</h2>", table_html(largest), "<h2>Methods</h2>",
             "".join("<p>" + html.escape(x) + "</p>" for x in methods), "<h2>Data and reproducibility</h2><p>Charts are embedded in this HTML; CSV and summary links require the companion output files.</p><ul>"]
    for name in result.tables:
        body.append(f"<li><a href='csv/{name}.csv'>{name.replace('_', ' ')}</a></li>")
    body += ["<li><a href='summary.json'>Summary, settings and provenance</a></li></ul>"]
    document = "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>PPS capture analysis</title><style>body{max-width:1100px;margin:40px auto;padding:0 24px;font:16px/1.6 system-ui,sans-serif;color:#24323b}h1,h2{line-height:1.2}h2{margin-top:2.3em}img{width:100%;height:auto}table{border-collapse:collapse;width:100%;font-size:13px;display:block;overflow-x:auto}td,th{padding:7px 10px;border-bottom:1px solid #ddd;text-align:left}th{background:#f1f5f6}a{color:#245a78}</style><body>" + "\n".join(body) + "</body></html>"
    (output / "report.html").write_text(document, encoding="utf-8")
    lines = ["# PPS capture analysis", "", "| Measurement | Result |", "|---|---:|"]
    lines += [f"| {k} | {_fmt(v)} |" for k,v in summary_rows]
    lines += ["", "## Findings", ""] + [x + "\n" for x in findings]
    lines += ["## Coverage and limitations", ""] + ["- " + x for x in result.warnings]
    for name, title, caption in summary_sections(result):
        lines += ["", f"## {title}", "", caption, "", table_markdown(result.tables[name]),
                  "", f"[Complete CSV](csv/{name}.csv)"]
    for filename, title, caption in plots:
        lines += ["", f"## {title}", "", f"![{title}](plots/{filename})", "", caption]
    lines += ["", "## Methods", ""] + [x + "\n" for x in methods]
    lines += ["## Data", ""] + [f"- [{name.replace('_', ' ')}](csv/{name}.csv)" for name in result.tables]
    lines += ["- [Summary, settings and provenance](summary.json)"]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Publish completion only after all report artifacts have been materialized.
    (output / "summary.json").write_text(json.dumps(plain(s), indent=2, allow_nan=False) + "\n", encoding="utf-8")
