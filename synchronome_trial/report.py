"""Self-contained HTML; CSVs retain the supporting calculations."""
import base64
import html
import math


def table(rows, columns, digits=3):
    if not rows:
        return '<p class="note">No supported comparison for this recording.</p>'
    header = "".join("<th>"+html.escape(label)+"</th>" for _, label in columns)
    body = []
    for row in rows:
        values = []
        for name, _ in columns:
            value = row.get(name)
            if isinstance(value, float):
                value = f"{value:.{digits}f}" if value == value else "unavailable"
            if value is None:
                value = "unavailable"
            values.append("<td>"+html.escape(str(value))+"</td>")
        body.append("<tr>"+"".join(values)+"</tr>")
    return '<div class="scroll"><table><thead><tr>'+header+'</tr></thead><tbody>'+"".join(body)+'</tbody></table></div>'


def selection_table(events, config):
    if not events:
        return '<p class="note">No events selected for this recording.</p>'
    metrics = [("period_us", "Period", config["period_threshold_us"]),
               ("difference_us", "Half difference", config["difference_threshold_us"]),
               ("flag_mean_us", "Mean flag", config["flag_threshold_us"])]
    header = '<th rowspan="2">Event</th><th rowspan="2">Elapsed hour</th>'
    header += "".join(f'<th colspan="2">{label}<br>|threshold| {threshold:g} µs</th>' for _, label, threshold in metrics)
    header += '<th rowspan="2">Largest score<br>(× threshold)</th><th rowspan="2">Largest criterion</th>'
    subheader = '<tr>'+('<th>Cycle jump (µs)</th><th>Median contrast (µs)</th>'*3)+'</tr>'
    body = []
    for event in events:
        cells = [f'<td>{html.escape(event["event_id"])}</td>', f'<td>{event["elapsed_hours"]:.3f}</td>']
        criteria = []
        for metric, label, threshold in metrics:
            for method, method_label in (("jump", "cycle jump"), ("contrast", "median contrast")):
                value = event.get(metric+"_selection_"+method)
                if value is None or not math.isfinite(value):
                    cells.append('<td>unavailable</td>')
                    continue
                criteria.append((abs(value)/threshold, label+": "+method_label))
                text = f"{value:+.3f}"
                if abs(value) >= threshold:
                    cells.append(f'<td class="selection-hit"><strong>{text} ✓</strong></td>')
                else:
                    cells.append(f'<td>{text}</td>')
        score = event.get("score")
        cells.append(f'<td>{score:.3f}</td>' if score is not None else '<td>requested</td>')
        largest = max((value for value, _ in criteria), default=0)
        labels = [label for value, label in criteria if value == largest]
        criterion = " / ".join(labels) if score is not None else "User-specified time"
        cells.append('<td>'+html.escape(criterion)+'</td>')
        body.append('<tr>'+"".join(cells)+'</tr>')
    return '<div class="scroll"><table id="event-selection-criteria"><thead><tr>'+header+'</tr>'+subheader+'</thead><tbody>'+"".join(body)+'</tbody></table></div>'


def write(out, summary, plots, event_summary, env_results):
    def figures(section):
        rendered = []
        for plot in plots:
            if plot["section"] != section:
                continue
            payload = base64.b64encode((out/"plots"/plot["name"]).read_bytes()).decode()
            caption = html.escape(plot["caption"])
            rendered.append(f'<figure><img src="data:image/png;base64,{payload}" alt="{caption}"><figcaption>{caption}</figcaption></figure>')
        return "".join(rendered) or '<p class="note">Insufficient observations for this plot.</p>'

    rows = event_summary.to_dict("records")
    criteria_table = selection_table(summary["events"], summary["config"])
    event_table = table(rows, [("event_id", "Event"), ("elapsed_hours", "Elapsed hour"),
                              ("period_us_change", "Period change (µs)"), ("difference_us_change", "Half-difference change (µs)"),
                              ("flag_mean_us_change", "Mean-flag change (µs)"), ("temperature_C_change", "Temperature change (°C)"),
                              ("before_cycles", "Before cycles"), ("after_cycles", "After cycles")])
    model_tables = []
    for response, label in (("period_us", "Full period"), ("difference_us", "Half-swing difference")):
        models = [r for r in env_results if r.get("response") == response and r.get("status") == "available"]
        model_tables.append(f"<h3>{label}</h3>"+table(models, [("epoch", "Epoch"), ("label", "Joint model"),
                          ("hours", "Common hours"), ("fit_rms_us", "Fit RMS (µs)"), ("r2", "Fit R²"),
                          ("test_rmse_us", "Later RMSE (µs)"), ("constant_test_rmse_us", "Constant RMSE (µs)"),
                          ("test_outside_training_range_fraction", "Later fraction outside earlier ranges")]))
    unsupported = [r for r in env_results if r.get("status") != "available"]
    model_notes = table(unsupported, [("epoch", "Epoch"), ("label", "Model"), ("status", "Status")]) if unsupported else ""
    quality = summary["metrology"]
    fingerprint = html.escape(summary["input_directory"])
    toc = "".join(f'<a href="#{anchor}">{name}</a>' for anchor, name in [("phase", "1. Phase correction"), ("events", "2. Events"),
                  ("flags", "3. Flags / impulse cycle"), ("periods", "4. Alternative periods"),
                  ("modulation", "5. Longer patterns"), ("environment", "6. Joint environment tests"), ("limits", "Limits / exports")])
    links = "".join(f'<li><a href="csv/{path.name}">{html.escape(path.name)}</a></li>' for path in sorted((out/"csv").iterdir()))
    stats = summary["statistics"]
    metrics_table = table(stats, [("epoch", "Epoch"), ("metric", "Quantity, complete cycles"), ("n", "Cycles"),
                                  ("rms_us", "RMS about mean (µs)"), ("range_us", "Peak-to-peak (µs)")])
    interpretation = "".join("<li>"+html.escape(note)+"</li>" for note in summary["observations"])
    spectrum_rows = [{**row, "peak_power_pct": row["peak_bin_fraction"]*100 if row["peak_bin_fraction"] is not None else None}
                     for row in summary["modulation_metadata"]]
    spectrum_table = table(spectrum_rows, [("epoch", "Epoch"), ("metric", "Quantity"), ("cycles", "Unbroken cycles"),
                            ("peak_period_s", "Largest spectral bin within 2–30 minutes (s)"), ("peak_power_pct", "Total power in that bin (%)")])
    comparisons = table(summary["alternation_summary"], [("epoch", "Epoch"), ("metric", "Quantity"), ("pairs", "Adjacent even/odd pairs"),
                            ("mean_odd_minus_even_us", "Mean odd−even (µs)"), ("rms_odd_minus_even_us", "Pair-difference RMS (µs)")])
    env_note = summary["environment_population"]
    text = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Synchronome trial — {html.escape(summary['dataset'])}</title><style>
body{{margin:0;background:#f2f5f8;color:#213044;font:16px/1.55 system-ui,sans-serif}}main{{max-width:1200px;margin:auto;padding:30px;background:white}}h1{{font-size:31px;line-height:1.25}}h2{{font-size:24px;margin-top:44px}}h3{{font-size:20px}}nav{{display:flex;flex-wrap:wrap;gap:8px 18px;padding:16px;background:#edf3fa}}a{{color:#235a99}}.note{{padding:15px;background:#edf3fa;border-left:4px solid #235a99}}.small{{font-size:14px;color:#586676}}figure{{margin:25px 0}}img{{width:100%;height:auto}}figcaption{{font-size:14px;color:#586676}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:9px;text-align:left;border-bottom:1px solid #d7dfe7;vertical-align:top}}.selection-hit{{background:#fff0bd;color:#533f00}}.scroll{{overflow:auto;margin:20px 0}}code{{background:#eef1f5;padding:2px 5px}}details{{margin:18px 0}}@media print{{body{{background:white}}main{{padding:0}}figure{{break-inside:avoid}}nav{{display:none}}}}
</style></head><body><main><p class="small">Exploratory package v{summary['version']} · six separate investigations · generated {summary['generated_utc']}</p>
<h1>Synchronome swing and flag investigations</h1><p><strong>{html.escape(summary['dataset'])}</strong>: {quality['records']:,} recorded full swings, {quality['pps_calibrated_eligible']:,} with PPS calibration, and {summary['complete_cycles']:,} complete 15-swing groups.</p>
<p class="note">Swings mean the open <code>tick</code> and <code>tock</code> intervals; flags mean <code>tick_block</code> and <code>tock_block</code>. Complete halves include both. The half difference is (tick + tick_block) − (tock + tock_block). All time axes use the observed elapsed hardware timeline; there is no verified event-to-UTC mapping.</p>
<nav>{toc}</nav><h2>Recording overview</h2>{metrics_table}<ul>{interpretation}</ul>{figures('overview')}
<h2 id="phase">1. Frozen phase correction</h2><p>A template is estimated separately for each epoch from complete cycles wholly inside its initial {summary['config']['baseline_seconds']/60:g}-minute window. Each metric has one mean offset for each of the 15 sequence phases. The equal-phase mean is removed from the offsets, so applying the template preserves the overall period and component means. Templates are fixed: later changes do not update the baseline. Profiles are sequence-based, not independently identified tooth numbers.</p>{figures('phase')}
<h2 id="events">2. Changes around selected events</h2><p>Automatic candidates use the larger of a consecutive-cycle jump and a contrast between ten preceding and ten following cycle medians. Declared scales are {summary['config']['period_threshold_us']:g} µs for period, {summary['config']['difference_threshold_us']:g} µs for half difference and {summary['config']['flag_threshold_us']:g} µs for mean flag. Candidates must exceed at least one scale; at most {summary['config']['max_events']} automatic events are retained, separated by at least {summary['config']['event_separation_seconds']/60:g} minutes. Smooth rapid changes can qualify; candidate selection does not label a cause or establish an instantaneous step.</p>
<h3>Selection criteria for each event</h3><p>Cycle jump is the selected cycle's mean minus the preceding cycle's mean. Median contrast is the median of ten following cycles minus the median of ten preceding cycles, excluding the selected cycle. Highlighted values with ✓ reach or exceed their threshold in absolute magnitude; the sign shows the direction of change. The largest score is the maximum of the six absolute values divided by their respective thresholds. Selection also requires a local score maximum, event separation and the declared event-count limit. These short-window selection values differ from the longer before/after comparisons below.</p>{criteria_table}
<h3>Before/after comparisons</h3><p>Each comparison uses complete cycles in {summary['config']['event_baseline_seconds']/60:g}-minute windows before and after, leaving {summary['config']['event_guard_seconds']/60:g} minutes on either side. Event-specific phase correction uses only the preceding window. Counts are exposure, not independent replicates; no iid significance tests are applied. The CSV also gives adjusted scatter before/after and all three environmental changes.</p>{event_table}{figures('events')}
<h2 id="flags">3. Flags through the impulse cycle</h2><p>The initial phase figure and each event's before/after profiles interleave the 30 half-swing positions. Each direction's inverse flag duration is normalised separately, providing a relative passage-speed proxy under unchanged sensor geometry. Absolute amplitude, speed in m/s, energy and pendulum Q are not calibrated. Unequal flags can reflect gate geometry as well as motion.</p>
<p>{'A user-supplied impulse anchor is shown; it is not inferred from the data.' if summary['config']['impulse_phase'] is not None else 'No physical impulse phase or gathering/passover direction was supplied. Phase labels are therefore retained without assigning those names.'} Accumulating the mean period offsets shows periodic timing advance/delay, but a true impulse phase kick requires an independently justified undriven-motion reference. Post-impulse changes and settling can be studied once the physical anchor is established.</p>
{figures('flags')}
<h2 id="periods">4. Four edge periods and two flag-midpoint periods</h2><p>For each homologous edge j, the alternative period is the current full period plus next-edge-j offset minus current-edge-j offset. Adjacent records must be valid, consecutive, in the same epoch/counter segment, with identical shared boundary edges. The same paired population is used for all alternatives. Flag-midpoint periods use average endpoint times. No row gap, reset or calibration exclusion is bridged.</p>
<p>{summary['matched_period_pairs']:,} adjacent calibrated pairs support the comparison. Different instantaneous periods around an impulse are expected because each estimate begins at a different point in the motion. Complete-cycle edge agreement is exported separately; neither agreement nor disagreement identifies mechanical cause on its own.</p>{figures('periods')}
<h2 id="modulation">5. Alternation and longer repetition</h2><p>Trials include original-cycle even/odd pairing, a declared 20-cycle fold (~10 minutes), gap-aware correlations and spectra. Linear drift is removed independently within each uninterrupted run. Fourier spectra use only the longest unbroken run per epoch, with nominally regular 30-second sampling and a Hann window. The largest bin in a predeclared 2–30-minute band is listed below as a descriptive feature, without a detection threshold or claim of statistical significance.</p>{comparisons}{spectrum_table}{figures('modulation')}
<h2 id="environment">6. Joint environmental and operating-state relationships</h2><p>Models include density, all three measured quantities together, and extensions with relative passage speed, temperature rate of change and thermal direction. Density-plus-speed is an additional physical benchmark. All models for a response receive exactly the same eligible hours: complete cycles with jointly valid, non-stale temperature, humidity and pressure, at least 30 minutes of exposure per hour, a valid preceding-hour thermal rate/direction, and finite speed. The fixed initial 60 minutes of complete cycles defines the speed reference, independently of the configurable phase-template baseline. Its reference samples precede the validation split.</p>
<p>There are {env_note['eligible_hours']} environmentally eligible hours and {env_note['common_model_hours']} common model hours. Each epoch is fitted separately. Earlier 70% of common hours trains the prediction models; later 30% tests them. A training-mean prediction provides the constant benchmark. Fit R² refers to the entire common population and is not predictive performance. Rank-deficient designs are reported as unavailable. Warming/cooling/steady direction uses temperature rates above +0.05, below −0.05 and between those values in °C/hour, including the current hourly temperature; this is an explanatory test, not an advance weather forecast.</p>
{''.join(model_tables)}{model_notes}{figures('environment')}
<p>Full-record coefficients and training-only coefficients are exported with condition numbers. No coefficient confidence intervals or causal attribution are claimed for these short, serially related data. Event/environment comparisons use the training-only model coefficients and before/after means; they are descriptive conditional predictions, especially when a model performs poorly on held-out data. Thermal-rate/direction models are omitted from the shorter event-window comparisons because those predictors were defined at hourly resolution. The speed proxy may also measure changing optical geometry. Residual regimes or warming/cooling differences are candidates for further investigation, not proof of friction, hysteresis or rod ageing.</p>
<h2 id="limits">Limits and future measurements</h2><ul><li>The 67Days recording is excluded. Only the explicitly selected recording is read; separate epochs are never pooled.</li><li>Physical impulse timing, direction, wall-clock events and interventions are not inferred. Add verified markers/notes to future recordings.</li><li>True amplitude and Q need geometry/calibration or suitable coast-down data. No coast-down protocol is inferred from this running-clock recording.</li><li>The ~8 Hz rod mode discussed in the source cannot be identified from sparse beam crossings alone. A continuous faster optical, motion or vibration measurement is required.</li><li>The recording is too short to establish long-term material ageing or a solar-noon relationship.</li><li>No timing outlier clipping, missing-cycle interpolation or automatic phase realignment is performed. Source metrology applies its documented eligibility guards.</li></ul>
<p>The investigations were motivated by the supplied <em>Yet another old Synchronome</em> discussion, especially May 9–12, August 3–7 and September 26. They test measurable patterns; the thread's mechanical interpretations remain hypotheses.</p>
<h3>Supporting exports</h3><ul>{links}</ul><p><a href="summary.json">Summary, methods, statuses, input/code fingerprints and audit</a></p><p class="small">Input: {fingerprint}. The HTML embeds all figures and can be copied on its own; CSV links require their companion files. Source recordings and the supported analysis suite remain unchanged.</p>
</main></body></html>'''
    (out/"report.html").write_text(text)
