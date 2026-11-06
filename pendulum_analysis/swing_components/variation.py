"""Rolling variation and stability, plus requestable PCA and spectral views."""
from html import escape
import numpy as np
import pandas as pd
from . import core,plots
from .rendering import fragment,image,safe_json,pack_series,CONTROL_STYLE


def local_summary(series,label):
    valid=np.isfinite(series.range);a=series.range[valid]
    return dict(sampling=label,averaged_period_points=len(series.t),complete_range_points=len(a),
        median_range_us=float(np.median(a)) if len(a) else np.nan,
        median_local_sd_us=float(np.median(series.sd[valid])) if len(a) else np.nan,
        p95_range_us=float(np.quantile(a,.95)) if len(a) else np.nan,
        maximum_range_us=float(a.max()) if len(a) else np.nan,
        minimum_points_per_range=int(series.count[valid].min()) if len(a) else 0,
        maximum_points_per_range=int(series.count[valid].max()) if len(a) else 0)


CONTROLS='''<h3>Select an interval and its context</h3>
<p class="meta">Cadences remain separate. Two-minute and four-hour buttons centre on the current selection; no quiet interval is selected automatically. Frequency is another units view of the same timing data.</p>
<div class="controls"><label>Sampling<select id="sampling"><option value="dense">Every swing</option><option value="cycle">Once per complete cycle</option></select></label>
<label>Units<select id="units"><option value="period">Period (µs)</option><option value="frequency">Frequency offset (ppm)</option></select></label>
<label>Vertical scale<select id="scale"><option value="visible">Fit visible interval</option><option value="dataset">This whole dataset</option></select></label>
<label>Start day<input id="start" type="number" min="0" step="0.001" value="0"></label><label>Duration (minutes)<input id="duration" type="number" min="0.01" step="0.1" value="120"></label>
<button id="apply">Show interval</button><button id="two">2 minutes</button><button id="context">4-hour context</button><button id="all">Whole dataset</button><button id="earlier">Earlier</button><button id="later">Later</button></div>
<p id="status" class="status" aria-live="polite">Loading embedded plot data…</p><canvas id="rolling-chart" role="img" aria-label="Average period and trailing two-minute range for the selected interval"></canvas><p id="hover">Hover or tap to inspect the support and trailing range.</p>
<details><summary>Selected interval statistics</summary><div id="selected-stats" class="stats"></div><p class="meta">Selected-window statistics remain in period microseconds. Range and RMS retain drift; only the explicitly labelled detrended RMS removes a fitted straight line. Overlapping averages are correlated.</p></details>
<p class="meta">At whole-run scale, first/minimum/maximum/last values per horizontal pixel are retained separately within valid runs. This changes display only. Every selected value enters statistics. CSV exports retain full precision.</p>'''


def render(events,cycle,epoch,directory,cfg,table_renderer):
    prefix=f'swing_e{epoch}_';label=f'Epoch {epoch}'
    dense,sparse,meta=core.rolling(events)
    checks=dict(rolling=meta,no_cross_dataset_dependency=True,automatic_quiet_selection=False)
    if cycle is not None:
        np.testing.assert_array_equal(sparse.seq,cycle['seq']+14)
        error=float(np.max(abs(sparse.mean-cycle['mean'])))
        assert error<1e-7;checks['cycle_mean_max_error_us']=error
    plots_used=[];exports=[]
    rows=[local_summary(dense,'Every swing'),local_summary(sparse,'Once per complete cycle')]
    for name,series in [('dense',dense),('cycle',sparse)]:
        filename=prefix+name+'_averages_and_ranges.csv.gz'
        series.frame().to_csv(directory/filename,index=False);exports.append(filename)
    summary=pd.DataFrame(rows);filename=prefix+'local_variation_summary.csv'
    summary.to_csv(directory/filename,index=False);exports.append(filename)
    filename=prefix+'average_and_range.png';plots.rolling(dense,directory/filename,label);plots_used.append(filename)
    nominal=events['nominal_s']
    body=f'<p>{label}. The arithmetic mean of 15 consecutive eligible full swings includes each phase once. The default advances by one swing; the alternative emits only complete phase-0…14 results. Each mean is compared with the nominal {nominal:g}-second full period. The lower curve is its trailing two-minute peak-to-peak range.</p>'
    body+=image(directory/filename,'Average period and local variation','All eligible averaged periods are retained. Axes belong to this dataset. Local variation includes noise, slope and transitions; it is not individual-swing jitter.')
    view=summary[['sampling','averaged_period_points','complete_range_points','median_range_us','median_local_sd_us','p95_range_us','maximum_range_us']].copy()
    view.columns=['Sampling','Averaged periods','Complete ranges','Median range (µs)','Median local SD (µs)','95th percentile range (µs)','Maximum range (µs)']
    h,md=table_renderer(view,limit=2)
    body+='<div class="scroll">'+h+'</div><p class="meta">Median local SD is the median of the standard deviations about the local mean within the same complete trailing two-minute windows. It uses denominator N and retains drift. It describes local averaged-period scatter, not whole-run SD or uncertainty of the mean. Cadences and their extrema are kept separate.</p>'
    payload={'dense':pack_series(dense),'cycle':pack_series(sparse)}
    checks['interactive_max_display_rounding_us']=max(p['max_display_error_us'] for p in payload.values())
    body+=CONTROLS
    if cycle is None:
        body+='<h3>Stability versus averaging time</h3><p>No complete phase-0…14 groups are available for cycle stability, PCA or spectra.</p>'
    else:
        stability,metadata=core.stability(cycle);checks['stability']=metadata
        filename=prefix+'allan_hadamard.csv';stability.to_csv(directory/filename,index=False);exports.append(filename)
        body+='<h3>Stability versus averaging time</h3><p>Allan and Hadamard deviation use actual fractional frequency of complete, nonoverlapping cycles. Differences never cross exclusions or epochs. No drift is fitted out; Hadamard intrinsically rejects linear frequency drift.</p>'
        if len(stability):
            filename=prefix+'allan_hadamard.png';plots.stability(stability,directory/filename,label);plots_used.append(filename)
            body+=image(directory/filename,'Allan and Hadamard stability','Hollow markers have fewer than 10 disjoint three-block spans. This is a support flag, not a confidence interval; overlapping pair counts are not independent degrees of freedom. No sampled minimum is declared an optimal averaging time.')
        else:body+='<p>'+escape(metadata['status'])+'</p>'
        if cfg.swing_pca:
            model=core.pca(cycle)
            body+='<details class="optional-analysis"><summary>Optional: phase-pattern PCA</summary><p>Covariance PCA in unscaled microseconds, centred separately by phase. There is no per-cycle common-mode subtraction or phase standardisation. Components and signs are fitted to this dataset and do not identify mechanical causes.</p>'
            if model is None:body+='<p>No varying phase population is available.</p>'
            else:
                checks['pca_variance_fractions']=model['fraction'].tolist()
                patterns=prefix+'pca_patterns.png';scores=prefix+'pca_scores.png'
                plots.pca(model,cycle,directory/patterns,directory/scores,label);plots_used.extend([patterns,scores])
                body+=image(directory/patterns,'Dataset-local PCA','Explained variance and phase loadings. Matching component numbers across recordings do not establish matching mechanisms.')
                body+=image(directory/scores,'PCA scores through time','First three component scores; all components contribute to the variance summary. Gaps remain gaps and a large episode can dominate the fitted basis.')
                filename=prefix+'pca_loadings.csv'
                pd.DataFrame([dict(component=k+1,phase=p,loading=float(v),variance_fraction=float(model['fraction'][k])) for k,row in enumerate(model['loadings']) for p,v in enumerate(row)]).to_csv(directory/filename,index=False);exports.append(filename)
                score=pd.DataFrame(dict(start_seq=cycle['seq'],elapsed_mid_s=cycle['t']))
                for k in range(model['scores'].shape[1]):score[f'PC{k+1}_us']=model['scores'][:,k]
                filename=prefix+'pca_scores.csv.gz';score.to_csv(directory/filename,index=False);exports.append(filename)
                filename=prefix+'pca_residual_scatter.csv';pd.DataFrame(dict(phase=np.arange(15),residual_sd_us=model['phase_residual_sd_us'])).to_csv(directory/filename,index=False);exports.append(filename)
            body+='</details>'
        if cfg.swing_spectra:
            spectra,metadata=core.spectra(cycle);checks['spectra']=metadata
            body+='<details class="optional-analysis"><summary>Optional: frequency spectra</summary><p>Welch spectra of the mean full-period deviation in µs²/Hz: Hann windows, 50% overlap and linear detrending per segment. Physical power is retained. No gap is interpolated and no ranked peak is interpreted automatically as an oscillator.</p>'
            if spectra:
                filename=prefix+'spectra.png';plots.spectra(spectra,directory/filename,label);plots_used.append(filename)
                body+=image(directory/filename,'Mean-period spectral density','Segment duration, contributing segment count and covered eligible cycles are shown. The first nonzero bin is a resolution limit, not evidence of a recurring event. Short and long segments answer different resolution questions.')
                filename=prefix+'welch_spectra.csv'
                pd.concat([pd.DataFrame(dict(frequency_hz=s['frequency_hz'],psd_us2_per_hz=s['psd_us2_per_hz'],segment_cycles=s['segment_cycles'])) for s in spectra]).to_csv(directory/filename,index=False);exports.append(filename)
                body+=f'<p class="meta">Median cadence {metadata["cadence_s"]:.6f} s; maximum contiguous-cadence departure {metadata["max_cadence_departure_s"]:.6f} s. Cadence is treated as uniform at that median. The shortest resolvable period is about {2*metadata["cadence_s"]:.1f} s. The {cfg.pps_window_seconds:g}-second PPS calibration can influence short-timescale structure.</p>'
            else:body+='<p>'+escape(metadata['status'])+'</p>'
            body+='</details>'
    body+='<details><summary>Component exports and support definitions</summary><p>Outer windows use (t−120 s, t] and require fresh uninterrupted history. Point counts vary with actual timing. Each source and output interval retains sequence identity; exclusions are never filled. Endpoint coordinates use observed hardware elapsed time.</p><ul>'
    for filename in exports:body+=f'<li><a href="plots/{filename}">{filename}</a></li>'
    body+='</ul></details>'
    data=dict(series=payload,origin=None,label=label,nominal=nominal)
    body+='<script id="plot-data" type="application/json">'+safe_json(data)+'</script>'
    return dict(title='Swing variation and stability',html=fragment(f'variation-e{epoch}',body,'variation.js',CONTROL_STYLE),
                markdown=md+'\n\nAllan/Hadamard stability uses complete nonoverlapping cycles; optional PCA and spectra are fitted to this dataset.\n',plots=plots_used,checks=checks)
