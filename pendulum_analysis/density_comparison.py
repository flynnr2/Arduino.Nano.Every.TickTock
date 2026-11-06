"""Compare air-density equations on a verified clean-period recording.

python -m pendulum_analysis.density_comparison --recording DIR --output DIR
Uses saved metrology outputs and receipt provenance; does not recalibrate time.
"""
import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .air_density import CLEAN_START, CO2_PPM, density_array
from .suite.common import Settings, environmental_validity, fingerprint, stats, write_json
from .suite.environment_models import DENSITY_SPECS, _solve


def compare(recording, output):
    recording, output = Path(recording).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    files = {name: recording / name for name in (
        'selection-audit.json', 'verification.json', 'swing_analysis/summary.json',
        'swing_analysis/csv/swing_intervals.csv.gz', 'swing_analysis/csv/swing_cycles.csv',
        'swing_input/PCSW_provenance.csv')}
    audit = json.loads(files['selection-audit.json'].read_text())
    verification = json.loads(files['verification.json'].read_text())
    if not verification.get('source_files_unchanged'):
        raise ValueError('Recording source verification must have passed')
    # Recheck the retained source snapshot rather than trusting an old flag.
    for relative, expected in audit['source_sha256'].items():
        source = (recording/relative).resolve()
        if not source.is_relative_to(recording) or fingerprint(source)['sha256'] != expected:
            raise ValueError(f'Recording source fingerprint mismatch: {relative}')
    configuration = json.loads(files['swing_analysis/summary.json'].read_text())['settings']
    cfg = Settings(**{k: v for k, v in configuration.items() if k in Settings.__dataclass_fields__})
    f = pd.read_csv(files['swing_analysis/csv/swing_intervals.csv.gz'])
    provenance = pd.read_csv(files['swing_input/PCSW_provenance.csv'])
    f = f.merge(provenance[['source_row', 'host_receipt_epoch']], on='source_row', validate='one_to_one')
    masks, stale, coverage = environmental_validity(f, f, cfg)
    selected = f.host_receipt_epoch.ge(CLEAN_START)
    env_good = selected & np.logical_and.reduce([masks[k] for k in ('temperature_C', 'humidity_pct', 'pressure_hPa')])
    for name, legacy in (('density_kg_m3', False), ('density_legacy_kg_m3', True)):
        f[name] = density_array(f.temperature_C, f.humidity_pct, f.pressure_hPa, legacy=legacy)
    valid = env_good & f.density_kg_m3.notna() & f.density_legacy_kg_m3.notna()
    f['density_delta_kg_m3'] = f.density_kg_m3 - f.density_legacy_kg_m3
    f['density_delta_ppm'] = f.density_delta_kg_m3 / f.density_legacy_kg_m3 * 1e6
    sample = f.loc[valid].copy()
    if sample.empty:
        raise ValueError('No jointly valid clean-period sensor readings')
    # Match saved complete cycles by their actual source-row membership, retaining
    # the recording's alignment/calibration and continuity runs without refitting.
    blocks = pd.read_csv(files['swing_analysis/csv/swing_cycles.csv'])
    complete = blocks.loc[blocks.available].copy()
    columns = ['temperature_C','humidity_pct','pressure_hPa','density_kg_m3','density_legacy_kg_m3']
    f['density_valid'] = valid
    by_row = f.set_index('source_row')
    rows = []
    for block in complete.itertuples():
        members = by_row.loc[block.first_source_row:block.last_source_row]
        if len(members) != 15 or not members.density_valid.all():
            continue
        if not members.calibrated_valid.all() or members.epoch.nunique() != 1:
            continue
        rows.append(dict(epoch=block.epoch, run=block.series_run, cycle=block.cycle,
            time=float(members.host_receipt_epoch.mean()), period_us=block.period_error_us,
            **{k: float(members[k].mean()) for k in columns}))
    cycles = pd.DataFrame(rows)
    if cycles.empty:
        raise ValueError('No complete clean cycles with jointly valid environmental inputs')
    cycles['hour'] = np.floor((cycles.time-CLEAN_START)/3600).astype(int)
    hourly = cycles.groupby(['epoch','run','hour']).agg(
        cycles=('cycle','size'), time=('time','mean'), period_us=('period_us','mean'),
        **{k:(k,'mean') for k in columns}).reset_index()
    hourly = hourly.loc[hourly.cycles*15*cfg.swing_period_s >= 1800].copy()
    models = []
    for (epoch, run), group in hourly.groupby(['epoch','run']):
        group = group.sort_values('time');n=len(group);split=int(n*.7)
        if n < 5:
            continue
        y=group.period_us.to_numpy()
        for label, predictors in DENSITY_SPECS.items():
            x=group[list(predictors)].to_numpy()
            fit=_solve(x,y);residual=y-fit['prediction']
            row=dict(epoch=int(epoch), run=int(run), model=label, hours=n,
                r_squared=1-float(residual@residual)/float(np.sum((y-y.mean())**2)) if np.ptp(y) else None,
                fit_rmse_us=float(np.sqrt(np.mean(residual**2))),
                coefficients_available=fit['separable'], condition_number=fit['condition'],
                coefficients={k:float(v) for k,v in zip(predictors,fit['coef']/fit['scale'])} if fit['separable'] else {},
                train_hours=split, test_hours=n-split, test_rmse_us=None, constant_test_rmse_us=None)
            if split >= 24 and n-split >= 12:
                train=_solve(x[:split],y[:split])
                prediction=train['reference']+(x[split:]-train['centre'])/train['scale']@train['coef']
                row.update(test_rmse_us=float(np.sqrt(np.mean((y[split:]-prediction)**2))),
                           constant_test_rmse_us=float(np.sqrt(np.mean((y[split:]-y[:split].mean())**2))))
            models.append(row)
    summary=dict(model='CIPM-2007', co2_ppm_assumed=CO2_PPM,
        clean_boundary_utc='2026-11-01T13:00:00Z', requested_start=audit.get('requested_start_utc'),
        requested_end_exclusive=audit.get('requested_end_utc_exclusive'),
        actual_start_utc=datetime.fromtimestamp(float(sample.host_receipt_epoch.min()),timezone.utc).isoformat(),
        actual_end_utc=datetime.fromtimestamp(float(sample.host_receipt_epoch.max()),timezone.utc).isoformat(),
        time_semantics='Pi receipt UTC, not independently verified Nano event UTC',
        selected_records=int(selected.sum()), jointly_valid_density_records=len(sample),
        excluded_environment_records=int((selected & ~env_good).sum()),
        excluded_cipm_range_records=int((env_good & ~valid).sum()), complete_cycles=len(cycles),
        eligible_hours=len(hourly), continuity_runs=int(cycles.groupby(['epoch','run']).ngroups),
        statistics={k:stats(sample[k]) for k in columns+['density_delta_kg_m3','density_delta_ppm']},
        models=models, sensor_coverage=coverage.to_dict('records'),
        rotation_boundaries=audit.get('verified_rotation_boundaries',[]),
        validation_note='Descriptive comparison with a chronological 70/30 diagnostic split. These data have already been examined in previous analyses; this is not fresh prospective validation.',
        source_snapshot_hashes_rechecked=len(audit['source_sha256']),
        sources=[fingerprint(p) for p in files.values()])
    write_json(output/'summary.json',summary)
    sample[['host_receipt_epoch','epoch','source_row',*columns,'density_delta_kg_m3','density_delta_ppm']].to_csv(output/'density-samples.csv.gz',index=False)
    cycles.to_csv(output/'cycles.csv',index=False);hourly.to_csv(output/'hourly.csv',index=False)
    pd.DataFrame(models).to_csv(output/'models.csv',index=False)
    stale.to_csv(output/'stale-runs.csv',index=False)
    plot_comparison(sample, output)
    delta=summary['statistics']['density_delta_kg_m3'];ppm=summary['statistics']['density_delta_ppm']
    def fmt(value):return 'Unavailable' if value is None else f'{value:.4f}'
    labels={'density':'CIPM-2007 density', 'temperature_density':'Temperature + CIPM-2007 density', 'legacy_density':'Previous density', 'temperature_legacy_density':'Temperature + previous density', 'density_matched_sensors':'Temperature + humidity + pressure'}
    table=''.join(f'<tr><td>{escape(labels[m["model"]])}</td><td>{m["hours"]}</td><td>{fmt(m["r_squared"])}</td><td>{fmt(m["fit_rmse_us"])}</td><td>{fmt(m["test_rmse_us"])}</td><td>{fmt(m["constant_test_rmse_us"])}</td></tr>' for m in models)
    html=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Clean-period air density comparison</title><style>body{{font:17px/1.5 system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#21372c}}img{{width:100%}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:9px;border-bottom:1px solid #ccd8d0;text-align:left}}.note{{color:#54665a}}</style><h1>CIPM-2007 air density: clean-period comparison</h1>
<p>{escape(summary['actual_start_utc'])} to {escape(summary['actual_end_utc'])}. Operator boundary: 1 November 2026 at 13:00 GMT. Earlier measurements are excluded.</p>
<p><strong>CIPM-2007 minus previous Buck/ideal-mixture calculation: mean {delta['mean']:.7f} kg/m³ ({ppm['mean']:.1f} ppm of density).</strong> Difference ranges from {delta['min']:.7f} to {delta['max']:.7f} kg/m³; its span is {delta['max']-delta['min']:.7f} kg/m³.</p>
<p>{len(sample):,} jointly valid environmental readings; {len(cycles):,} complete calibrated cycles; {len(hourly)} eligible hours in {summary['continuity_runs']} continuity run(s). CO₂ is assumed at 400 ppm for both the interpretation and the CIPM calculation; it is not measured. {summary['excluded_environment_records']:,} selected readings fail existing environmental checks; {summary['excluded_cipm_range_records']:,} further readings fall outside CIPM validity.</p>
<img src="density-comparison.png" alt="Two density calculations and their difference over the clean period"><h2>Matched environmental models</h2><p>Every row uses the same complete cycles and eligible hours within its continuity run. Density is calculated for each sensor observation before averaging. Temperature plus density replaces humidity and pressure; density is not appended to all three correlated inputs. Fit error describes this history. The chronological split is an exploratory diagnostic on previously examined data.</p><table><thead><tr><th>Model</th><th>Hours</th><th>R²</th><th>Fit RMSE (µs)</th><th>Later 30% RMSE (µs)</th><th>Earlier-mean baseline RMSE (µs)</th></tr></thead><tbody>{table}</tbody></table><p class="note">No independent-sample confidence intervals or causal compensation claim. Sensor calibration, spatial temperature gradients, response delays, unknown CO₂ and pendulum dynamics remain separate limitations. Receipt time is not independently verified capture UTC. Continuity and capture-validity checks remain in force.</p><p><a href="summary.json">Statistics, source hashes and audit</a> · <a href="models.csv">Model results</a> · <a href="hourly.csv">Eligible hourly data</a></p><p>Formula source: <a href="https://www.nist.gov/system/files/documents/calibrations/CIPM-2007.pdf">Picard et al., Metrologia 45 (2008), 149–155</a>. Previous calculation retains Buck saturation pressure and gas constants 287.05 and 461.5 unchanged.</p></html>'''
    (output/'report.html').write_text(html)
    return summary


def plot_comparison(sample, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    data=sample.copy();data['hour']=(data.host_receipt_epoch-CLEAN_START)//3600
    h=data.groupby(['epoch','hour']).agg(time=('host_receipt_epoch','mean'),
        density=('density_kg_m3','mean'),legacy=('density_legacy_kg_m3','mean'),delta=('density_delta_kg_m3','mean')).reset_index()
    fig,axes=plt.subplots(2,1,figsize=(12,7),sharex=True,layout='constrained')
    # Split at epoch changes and missing hours; never bridge a recording gap.
    h['run']=(h.epoch.ne(h.epoch.shift())|h.hour.diff().ne(1)).cumsum()
    for index,(_,g) in enumerate(h.groupby('run')):
        x=(g.time-CLEAN_START)/3600
        axes[0].plot(x,g.density,color='#217c65',label='CIPM-2007' if index==0 else None)
        axes[0].plot(x,g.legacy,'--',color='#be702c',label='Previous Buck / ideal mixture' if index==0 else None)
        axes[1].plot(x,g.delta*1e6,color='#356fa0')
    axes[0].set_ylabel('Air density (kg/m³)');axes[0].legend();axes[1].set_ylabel('CIPM − previous (mg/m³)')
    axes[1].set_xlabel('Hours since 1 November 2026, 13:00 GMT')
    axes[0].set_title('Clean period · hourly averages of paired valid density readings · CO₂ assumed 400 ppm')
    for ax in axes:ax.grid(alpha=.2);ax.ticklabel_format(useOffset=False,axis='y')
    fig.savefig(output/'density-comparison.png',dpi=160);plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recording',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();summary=compare(args.recording,args.output)
    print(json.dumps({k:summary[k] for k in ('actual_start_utc','actual_end_utc','jointly_valid_density_records','complete_cycles','eligible_hours')}))


if __name__=='__main__':main()
