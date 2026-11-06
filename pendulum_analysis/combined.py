"""Combine independently analysed epochs without concatenating capture clocks.

The only inputs are completed reports with full interval exports. Calibration,
event ownership, complete cycles and local windows remain owned by each report.
"""
from pathlib import Path
from html import escape
import argparse
import json
import os
import shutil
import tempfile

import numpy as np
import pandas as pd

from .suite.common import fingerprint, stats, write_json

SCHEMA = 'pendulum-combined-analysis.v1'


def _optional_table(path):
    # The normal suite writes a one-newline CSV for some unavailable products.
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _boolean(series, name):
    if series.isna().any() or not series.isin([True, False]).all():
        raise ValueError(f'Invalid boolean {name} in derived report')
    return series.astype(bool)


def _receipt_map(folder, summary, frame, role):
    """Use explicit receipt witnesses; never manufacture UTC from elapsed spans."""
    recorded = summary.get('provenance', {}).get('receipt_files', {}).get(role)
    candidates = []
    if recorded:
        candidate = Path(recorded['path'])
        if fingerprint(candidate)['sha256'] != recorded['sha256']:
            raise ValueError('Receipt provenance changed after analysis')
        candidates = [candidate]
    else:
        source = summary.get('provenance', {}).get('inputs', {}).get(role, {}).get('path')
        if source and Path(source).parent.is_dir():
            candidates = [p for p in Path(source).parent.iterdir()
                          if p.name.casefold() == role + '_provenance.csv']
    result = pd.DataFrame(index=frame.index, columns=['receipt_utc', 'utc_basis', 'source_session'])
    result['receipt_utc'] = np.nan
    if not candidates:
        return result, None
    if len(candidates) != 1:
        raise ValueError('Ambiguous receipt provenance files')
    provenance = pd.read_csv(candidates[0])
    time = 'selection_epoch' if 'selection_epoch' in provenance else 'host_receipt_epoch'
    if not {'source_row', 'seq', time}.issubset(provenance):
        raise ValueError('Receipt provenance requires source_row, seq and receipt epoch')
    if provenance.source_row.duplicated().any():
        raise ValueError('Duplicate source rows in receipt provenance')
    joined = frame[['source_row', 'seq']].merge(provenance, on='source_row', how='left',
                                               validate='one_to_one', suffixes=('', '_receipt'))
    present = joined[time].notna()
    if not joined.loc[present, 'seq'].eq(joined.loc[present, 'seq_receipt']).all():
        raise ValueError('Receipt sequence does not match analysed source row')
    values = pd.to_numeric(joined[time], errors='coerce')
    if not np.isfinite(values[present]).all():
        raise ValueError('Nonfinite receipt time')
    result['receipt_utc'] = values.to_numpy()
    result['utc_basis'] = (joined['utc_basis'].fillna('recorded receipt UTC').to_numpy()
                           if 'utc_basis' in joined else 'recorded receipt UTC')
    if 'source_session' in joined:
        result['source_session'] = joined.source_session.to_numpy()
    return result, fingerprint(candidates[0])


def _load(folder, number):
    folder = Path(folder).resolve()
    summary = json.loads((folder/'summary.json').read_text())
    if not summary.get('complete'):
        raise ValueError(f'Incomplete report: {folder}')
    schema = summary.get('schema')
    if schema == 'pendulum-clock-swing-suite.v1' and summary.get('swings') is not None:
        package, role, filename = 'swing', 'pcsw', 'swing_intervals.csv.gz'
        settings = summary['settings']
    elif schema == 'pendulum_analysis.pps.v1':
        package, role, filename = 'pps', 'pcps', 'intervals.csv.gz'
        settings = summary['config']
    else:
        raise ValueError(f'Unsupported report schema or absent swings: {folder}')
    path = folder/'csv'/filename
    if not path.is_file():
        raise ValueError(f'Combined analysis requires --export-intervals: {folder}')
    frame = pd.read_csv(path)
    required = ({'epoch', 'calibrated_valid', 'full_pps_s', 'elapsed_cycles', 'phase15'}
                if package == 'swing' else {'segment', 'frequency_valid', 'frequency_offset_cycles', 'elapsed_cycles'})
    if not {'source_row', 'seq', *required}.issubset(frame) or frame.empty:
        raise ValueError('Missing required derived interval columns')
    valid = 'calibrated_valid' if package == 'swing' else 'frequency_valid'
    frame[valid] = _boolean(frame[valid], valid)
    value_columns = ['full_pps_s', 'elapsed_cycles'] if package == 'swing' else ['frequency_offset_cycles', 'elapsed_cycles']
    if not np.isfinite(frame.loc[frame[valid], value_columns]).all().all():
        raise ValueError('Eligible intervals contain nonfinite measurements')
    frame['local_epoch'] = frame.epoch if package == 'swing' else frame.segment
    identifier = f'group-{number:04d}'
    frame['analysis_epoch'] = identifier + ':e' + frame.local_epoch.astype(int).astype(str)
    receipt, witness = _receipt_map(folder, summary, frame, role)
    frame = pd.concat([frame, receipt], axis=1)
    for _, group in frame.groupby('analysis_epoch', sort=False):
        time = group.receipt_utc.dropna()
        if not time.is_monotonic_increasing:
            raise ValueError('Receipt chronology reverses within an analysis epoch')
    session = summary.get('panel_context', {}).get('session')
    sessions = {s['manifest']['session'] for s in summary.get('provenance', {}).get('recording_segments', [])}
    if not session and len(sessions) == 1:
        session = next(iter(sessions))
    frame['source_session'] = frame.source_session.fillna(session or '')
    provenance = dict(summary=fingerprint(folder/'summary.json'), intervals=fingerprint(path), receipt=witness)
    if package == 'swing':
        products = [folder/'csv'/name for name in ('swing_cycles.csv',
            'swing_environment_hourly.csv', 'swing_environment_joint_hourly.csv')]
        products += sorted((folder/'plots').glob('swing_e*_allan_hadamard.csv'))
        provenance.update({p.name: fingerprint(p) for p in products if p.is_file()})
    return dict(id=identifier, folder=folder, summary=summary, settings=settings,
                frame=frame, package=package, role=role, provenance=provenance)


def _compatible(parts):
    if len({p['package'] for p in parts}) != 1:
        raise ValueError('PPS and swing reports must be combined separately')
    keys = (['nominal_hz', 'swing_period_s', 'pps_window_seconds', 'profile',
             'max_blocked_fraction', 'half_period_tolerance', 'optical_path_mm', 'pivot_to_beam_mm']
            if parts[0]['package'] == 'swing' else ['nominal_hz', 'normal_interval_tolerance'])
    reference = parts[0]['settings']
    for key in keys:
        if any(p['settings'].get(key) != reference.get(key) for p in parts[1:]):
            raise ValueError(f'Incompatible measurement settings: {key}')
    implementations = [p['summary'].get('provenance', {}).get('implementation',
        p['summary'].get('provenance', {}).get('analysis_source_sha256')) for p in parts]
    known = [value for value in implementations if value]
    if known and any(value != known[0] for value in known[1:]):
        raise ValueError('Independent reports use different analysis implementations; regenerate them with one revision')
    # Check source identity rather than assuming different report folders mean
    # different measurements. Anonymous byte-identical inputs are ambiguous.
    seen, anonymous = {}, set()
    for part in parts:
        frame = part['frame']
        for session, group in frame.loc[frame.source_session.ne('')].groupby('source_session'):
            sequence = set(group.seq.astype(int))
            if sequence & seen.setdefault(session, set()):
                raise ValueError('Overlapping captures in the same source session')
            seen[session].update(sequence)
        if frame.source_session.eq('').all():
            identity = part['summary'].get('provenance', {}).get('inputs', {}).get(part['role'], {}).get('sha256')
            if identity and identity in anonymous:
                raise ValueError('Duplicate anonymous source captures')
            anonymous.add(identity)
    return {key: reference.get(key) for key in keys}


def within_epoch_environment(table, features):
    """Cycle-weighted shared slopes with independent epoch intercepts and holdouts."""
    required = ['analysis_epoch', 'hour', 'response', 'samples', *features]
    data = table.dropna(subset=required).copy()
    data = data.loc[data.samples.gt(0)]
    data = data.loc[np.isfinite(data[['response', 'samples', *features]]).all(axis=1)]

    def solve(population):
        x, y, weights, centres = [], [], [], {}
        for epoch, group in population.groupby('analysis_epoch', sort=False):
            w = group.samples.to_numpy(float)
            a = group[features].to_numpy(float); b = group.response.to_numpy(float)
            mean_x, mean_y = np.average(a, axis=0, weights=w), np.average(b, weights=w)
            centres[epoch] = (mean_x, mean_y)
            x.append(a-mean_x); y.append(b-mean_y); weights.append(w)
        if not x:
            return None
        x, y, w = np.concatenate(x), np.concatenate(y), np.concatenate(weights)
        scale = np.sqrt(np.average(x*x, axis=0, weights=w))
        if (scale <= 0).any() or len(y) < len(features)+3:
            return None
        design = x/scale*np.sqrt(w[:, None])
        if np.linalg.matrix_rank(design) < len(features):
            return None
        coefficient = np.linalg.lstsq(design, y*np.sqrt(w), rcond=None)[0]/scale
        residual = y-x@coefficient
        return coefficient, centres, float(np.sqrt(np.average(residual**2, weights=w)))

    result = dict(features='+'.join(features), hours=len(data), epochs=int(data.analysis_epoch.nunique()),
                  samples=int(data.samples.sum()), status='insufficient within-epoch variation',
                  train_hours=0, test_hours=0, validation_status='insufficient chronological holdout',
                  method='Cycle-weighted OLS with separate epoch intercepts; descriptive association, no causal inference')
    fitted = solve(data)
    if fitted is None:
        return result
    coefficient, _, rmse = fitted
    result.update(status='available', fit_rmse=rmse, slopes=dict(zip(features, coefficient.tolist())))
    train, test = [], []
    for _, group in data.groupby('analysis_epoch', sort=False):
        group = group.sort_values('hour'); split = int(.7*len(group))
        if split >= 4 and len(group)-split >= 2:
            train.append(group.iloc[:split]); test.append(group.iloc[split:])
    if not train:
        return result
    training, testing = pd.concat(train), pd.concat(test)
    fit = solve(training)
    if fit is not None:
        beta, centres, _ = fit
        errors, baseline, weights = [], [], []
        for epoch, group in testing.groupby('analysis_epoch', sort=False):
            x0, y0 = centres[epoch]
            errors.extend(group.response-(y0+(group[features].to_numpy()-x0)@beta))
            baseline.extend(group.response-y0); weights.extend(group.samples)
        score = float(np.sqrt(np.average(np.square(errors), weights=weights)))
        constant = float(np.sqrt(np.average(np.square(baseline), weights=weights)))
        result.update(train_hours=len(training), test_hours=len(testing), validation_status='available',
                      test_rmse=score, baseline_rmse=constant,
                      improvement_fraction=1-score/constant if constant > 0 else None)
    return result


def pool_stability(table):
    """Pool existing within-run squared differences; introduce no new differences."""
    rows = []
    for cycles, group in table.groupby('averaging_cycles'):
        low, high = group.tau_s.min(), group.tau_s.max()
        row = dict(averaging_cycles=int(cycles), tau_min_s=low, tau_max_s=high,
                   epochs=int(group.analysis_epoch.nunique()), status='cadences differ')
        if low > 0 and high/low <= 1.001:
            row['status'] = 'available'
            for metric, count in [('adev_ppm','allan_pairs'),('hdev_ppm','hadamard_triples')]:
                good = np.isfinite(group[metric]) & group[count].gt(0)
                row[count] = int(group.loc[good, count].sum())
                row[metric] = (float(np.sqrt(np.average(group.loc[good, metric]**2,
                                                       weights=group.loc[good, count]))) if good.any() else None)
        rows.append(row)
    return pd.DataFrame(rows)


def _table(frame):
    if frame.empty:
        return '<p>Unavailable for this population.</p>', 'Unavailable for this population.\n'
    view = frame.copy()
    for col in view:
        view[col] = view[col].map(lambda x: '' if x is None or (isinstance(x,float) and not np.isfinite(x))
                                  else f'{x:.6g}' if isinstance(x,float) else str(x))
    rows = [[str(c) for c in view.columns]] + view.values.tolist()
    rows = [[v.replace('|','\\|').replace('\n',' ') for v in row] for row in rows]
    widths = [max(3,max(len(row[j]) for row in rows)) for j in range(len(rows[0]))]
    def line(row):return '| '+' | '.join(value.ljust(width) for value,width in zip(row,widths))+' |'
    md = [line(rows[0]), line(['-'*width for width in widths])]+[line(row) for row in rows[1:]]
    return view.to_html(index=False,escape=True), '\n'.join(md)+'\n'


def _relative(target, directory):
    return Path(os.path.relpath(target, directory)).as_posix()


def _build(parts, stage, out, metadata):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.dates import DateFormatter

    package = parts[0]['package']; shared = _compatible(parts)
    csv, plots = stage/'csv',stage/'plots';csv.mkdir();plots.mkdir()
    epochs, series, profiles, distributions, stability, hourly, joint = [], [], [], [], [], [], []
    warnings = []
    nominal = parts[0]['settings']['nominal_hz']
    period = parts[0]['settings'].get('swing_period_s',2.)
    for part in parts:
        frame = part['frame'];folder = part['folder'];summary = part['summary']
        cycles = (pd.read_csv(folder/'csv/swing_cycles.csv') if package=='swing' else None)
        anchors = ({int(a['epoch']):a for a in summary['swings'].get('impulse_alignment',{}).get('epochs',[])}
                   if package=='swing' else {})
        for local, group in frame.groupby('local_epoch',sort=False):
            identity = group.analysis_epoch.iloc[0]
            valid = group.calibrated_valid if package=='swing' else group.frequency_valid
            values = ((group.full_pps_s-period)*1e6 if package=='swing'
                      else group.frequency_offset_cycles*1e6/nominal)
            anchor = anchors.get(int(local),{})
            time = group.receipt_utc.dropna()
            label = part['id']+' / epoch '+str(int(local))
            epochs.append(dict(analysis_epoch=identity,label=label,session=','.join(group.source_session.unique()),
                records=len(group),eligible=int(valid.sum()),excluded=int((~valid).sum()),
                first_receipt_utc=pd.to_datetime(time.min(),unit='s',utc=True).isoformat() if len(time) else None,
                last_receipt_utc=pd.to_datetime(time.max(),unit='s',utc=True).isoformat() if len(time) else None,
                utc_records=len(time),inferred_utc_records=int((group.utc_basis.fillna('').str.contains('inferred')).sum()),
                alignment=anchor.get('status','not requested'),original_anchor_half_phase=anchor.get('anchor_phase30'),
                detail_report=_relative(folder/'report.html',out),**stats(values.loc[valid])))
            distribution = pd.DataFrame(dict(analysis_epoch=identity,source_row=group.source_row,
                seq=group.seq,receipt_utc=group.receipt_utc,utc_basis=group.utc_basis,
                observed_elapsed_s=group.elapsed_cycles/nominal,value=values,eligible=valid))
            distributions.append(distribution)
            if package=='swing':
                blocks=cycles.loc[cycles.epoch.eq(local)&_boolean(cycles.available,'available')].copy()
                rows=group.set_index('source_row')
                blocks['receipt_utc']=blocks.last_source_row.map(rows.receipt_utc)
                # A reconstructed odd period can finish in the next source row.
                if 'tick_source_row' in rows:
                    contributing=blocks.last_source_row.map(rows[['tick_source_row','tock_source_row']].max(axis=1))
                    blocks['receipt_utc']=contributing.map(rows.receipt_utc)
                blocks['analysis_epoch']=identity
                blocks['run']=blocks.series_run
                blocks['value']=blocks.period_error_us
                series.append(blocks[['analysis_epoch','run','receipt_utc','time_s','value']])
                if anchor.get('status')=='aligned':
                    z=pd.DataFrame(dict(analysis_epoch=identity,phase=group.phase15,value=values)).loc[valid]
                    profiles.append(z)
                else:
                    warnings.append(f'{label}: excluded from pooled mechanical phase profiles; no accepted impulse anchor.')
                p=folder/'plots'/f'swing_e{int(local)}_allan_hadamard.csv'
                if p.exists():
                    z=_optional_table(p)
                    if not z.empty:
                        z['analysis_epoch']=identity;stability.append(z)
            else:
                # Preserve all excluded intervals as boundaries before averaging
                # valid data for display. No minute-average spans a bad interval.
                z=distribution.copy();breaks=(~valid)|(~valid.shift(fill_value=False))|group.seq.diff().ne(1)
                z['run']=breaks.cumsum();z=z.loc[valid].copy()
                z['minute']=(z.observed_elapsed_s//60).astype(int)
                z=z.groupby(['analysis_epoch','run','minute'],sort=False).agg(
                    receipt_utc=('receipt_utc','mean'),time_s=('observed_elapsed_s','mean'),value=('value','mean')).reset_index()
                series.append(z[['analysis_epoch','run','receipt_utc','time_s','value']])
            if len(time) != len(group):
                warnings.append(f'{label}: no per-record receipt UTC for {len(group)-len(time)} records; elapsed-time panel retained without inventing chronology.')
        if package=='swing':
            for filename,target in [('swing_environment_hourly.csv',hourly),('swing_environment_joint_hourly.csv',joint)]:
                path=folder/'csv'/filename
                if path.exists():
                    z=_optional_table(path)
                    if z.empty:
                        continue
                    z=z.loc[_boolean(z.eligible,'eligible')].copy()
                    z['analysis_epoch']=part['id']+':e'+z.epoch.astype(int).astype(str);target.append(z)
    epoch_table=pd.DataFrame(epochs);data=pd.concat(distributions,ignore_index=True)
    timeline=pd.concat(series,ignore_index=True) if series else pd.DataFrame()
    pooled=stats(data.loc[data.eligible,'value']);unit='µs' if package=='swing' else 'ppm'
    epoch_table.to_csv(csv/'epochs.csv',index=False);data.to_csv(csv/'combined_intervals.csv.gz',index=False)
    timeline.to_csv(csv/'timeline.csv.gz',index=False)
    phase=pd.concat(profiles,ignore_index=True) if profiles else pd.DataFrame(columns=['analysis_epoch','phase','value'])
    phase_by_epoch=phase.groupby(['analysis_epoch','phase']).value.agg(['count','mean','std']).reset_index()
    phase_pooled=phase.groupby('phase').value.agg(['count','mean','std']).reset_index()
    phase_by_epoch.to_csv(csv/'phase_by_epoch.csv',index=False);phase_pooled.to_csv(csv/'phase_pooled.csv',index=False)
    stable=pd.concat(stability,ignore_index=True) if stability else pd.DataFrame(columns=['analysis_epoch','averaging_cycles','tau_s','adev_ppm','hdev_ppm','allan_pairs','hadamard_triples'])
    pooled_stability=pool_stability(stable) if len(stable) else pd.DataFrame(columns=['averaging_cycles','status','adev_ppm','hdev_ppm','allan_pairs','hadamard_triples'])
    stable.to_csv(csv/'stability_by_epoch.csv',index=False);pooled_stability.to_csv(csv/'stability_pooled.csv',index=False)
    fits=[]
    if hourly:
        h=pd.concat(hourly,ignore_index=True)
        for channel,g in h.groupby('channel'):
            g=g.rename(columns={'reading':channel,'period_error_us':'response','cycles':'samples'})
            fits.append(within_epoch_environment(g,[channel]))
    if joint:
        fits.append(within_epoch_environment(pd.concat(joint,ignore_index=True),['temperature_C','humidity_pct','pressure_hPa']))
    environment=pd.DataFrame(fits) if fits else pd.DataFrame(columns=['features','hours','epochs','samples','status'])
    environment.to_csv(csv/'environment_within_epoch.csv',index=False)
    # Only fully mapped epochs appear on the common calendar axis. Incomplete
    # or absent mappings get their own elapsed panel with the uncertainty stated.
    mapped={e['analysis_epoch'] for e in epochs if e['utc_records']==e['records']}
    gaps=[]
    ordered=epoch_table.loc[epoch_table.analysis_epoch.isin(mapped)].sort_values('first_receipt_utc')
    for previous,current in zip(ordered.to_dict('records'),ordered.to_dict('records')[1:]):
        seconds=(pd.Timestamp(current['first_receipt_utc'])-pd.Timestamp(previous['last_receipt_utc'])).total_seconds()
        gaps.append(dict(before=previous['analysis_epoch'],after=current['analysis_epoch'],receipt_separation_s=seconds,
                         receipt_separation_only=True,filled=False))
        if seconds < 0:
            warnings.append('Receipt coverage overlaps across independent epochs; no continuity or event-time order is inferred within that overlap.')
    unknown=[e for e in epochs if e['analysis_epoch'] not in mapped]
    panels=(1 if mapped else 0)+len(unknown)
    fig,axes=plt.subplots(max(1,panels),1,figsize=(13,3.6*max(1,panels)),squeeze=False,layout='constrained')
    axis=0
    if mapped:
        ax=axes[0,0]
        for (epoch,run),g in timeline.loc[timeline.analysis_epoch.isin(mapped)].groupby(['analysis_epoch','run'],sort=False):
            ax.plot(pd.to_datetime(g.receipt_utc,unit='s',utc=True),g.value,'.-',lw=.65,markersize=2,label=epoch if run==timeline.loc[timeline.analysis_epoch.eq(epoch),'run'].iloc[0] else None)
        ax.xaxis.set_major_formatter(DateFormatter('%d %b\n%H:%M',tz=__import__('datetime').timezone.utc))
        ax.set(xlabel='Host receipt UTC · inferred boot labels remain identified in exports',ylabel='Cycle mean − nominal (µs)' if package=='swing' else 'Frequency offset (ppm)')
        ax.legend(fontsize=8);axis=1
    for epoch in unknown:
        ax=axes[axis,0];axis+=1
        for _,g in timeline.loc[timeline.analysis_epoch.eq(epoch['analysis_epoch'])].groupby('run',sort=False):
            ax.plot(g.time_s/3600,g.value,'.-',lw=.65,markersize=2)
        ax.set(title=epoch['label']+' · UTC unavailable or incomplete',xlabel='Observed elapsed hours within this input',ylabel=unit)
    for ax in axes.flat:ax.grid(alpha=.2)
    fig.suptitle('Combined '+package+' analysis · independent epochs and visible gaps')
    fig.savefig(plots/'timeline.png',dpi=140);plt.close(fig)
    for number,gap in enumerate(gaps,1):
        before=next(e for e in epochs if e['analysis_epoch']==gap['before'])
        after=next(e for e in epochs if e['analysis_epoch']==gap['after'])
        left=pd.Timestamp(before['last_receipt_utc']);right=pd.Timestamp(after['first_receipt_utc'])
        start=min(left,right)-pd.Timedelta(seconds=180)
        end=max(left,right)+pd.Timedelta(seconds=180)
        fig,ax=plt.subplots(figsize=(12,4),layout='constrained')
        for epoch in (gap['before'],gap['after']):
            z=timeline.loc[timeline.analysis_epoch.eq(epoch)].copy()
            z['time']=pd.to_datetime(z.receipt_utc,unit='s',utc=True)
            z=z.loc[z.time.between(start,end)]
            for run,g in z.groupby('run',sort=False):
                ax.plot(g.time,g.value,'o-',label=epoch if run==z.run.iloc[0] else None,markersize=3)
        ax.axvspan(min(left,right),max(left,right),color='#a4aab0',alpha=.25)
        ax.set(xlim=(start,end),xlabel='Host receipt UTC · shaded receipt separation is not physical PPS outage duration',
               ylabel='Cycle mean − nominal (µs)' if package=='swing' else 'Frequency offset (ppm)',
               title=f'Independent epoch boundary {number} · receipt separation {gap["receipt_separation_s"]:.3f} s')
        ax.xaxis.set_major_formatter(DateFormatter('%d %b\n%H:%M:%S',tz=__import__('datetime').timezone.utc))
        ax.grid(alpha=.2);ax.legend(fontsize=8)
        fig.savefig(plots/f'boundary_{number:04d}.png',dpi=140);plt.close(fig)
    fig,ax=plt.subplots(figsize=(12,4.5),layout='constrained')
    bins=np.histogram_bin_edges(data.loc[data.eligible,'value'],bins=80) if data.eligible.any() else np.linspace(-1,1,10)
    for epoch,g in data.loc[data.eligible].groupby('analysis_epoch',sort=False):
        ax.hist(g.value,bins=bins,histtype='step',label=epoch)
    ax.set(xlabel=('Full period − nominal' if package=='swing' else 'PPS frequency offset')+' ('+unit+')',ylabel='Eligible intervals',title='Validated interval distributions · pooled spread includes epoch differences')
    if data.eligible.any():ax.legend(fontsize=8)
    fig.savefig(plots/'distribution.png',dpi=140);plt.close(fig)
    if len(phase):
        fig,ax=plt.subplots(figsize=(12,4.5),layout='constrained')
        for epoch,g in phase_by_epoch.groupby('analysis_epoch',sort=False):ax.plot(g.phase,g['mean'],'o-',label=epoch,markersize=3)
        ax.plot(phase_pooled.phase,phase_pooled['mean'],'k--',lw=2,label='Pooled by eligible observation count')
        ax.set(xlabel='Impulse-aligned full phase',ylabel='Mean period − nominal (µs)',xticks=range(15),title='Mechanical phase profiles · independently accepted anchors only')
        ax.grid(alpha=.2);ax.legend(fontsize=8);fig.savefig(plots/'phase.png',dpi=140);plt.close(fig)
    title='Combined '+('swing' if package=='swing' else 'dedicated PPS')+' analysis'
    policy=('Calibration, half pairing, complete cycles, rolling windows and stability differences remain confined to their original valid epoch/run. '
            'No missing samples or cross-boot absolute phase are reconstructed. Receipt separations do not establish physical PPS outage duration or independently verified event UTC. Pooled descriptive spread includes between-epoch changes. '
            'Phase pooling uses independently accepted impulse anchors only. Stability pools within-run squared differences by their actual support counts; overlapping counts are not independent degrees of freedom. '
            'Environmental slopes use separate epoch intercepts and chronological holdouts within each epoch; they are associations, not causal corrections.')
    body=['<h1>'+title+'</h1>','<p>'+policy+'</p>']
    markdown=['# '+title,'',policy,'']
    for warning in warnings:body.append('<p role="alert">'+escape(warning)+'</p>');markdown.append(warning+'\n')
    view=epoch_table[['analysis_epoch','session','records','eligible','excluded','alignment','original_anchor_half_phase','first_receipt_utc','last_receipt_utc']]
    for heading,table in [('Epochs and coverage',view),('Receipt separations between independent epochs',pd.DataFrame(gaps)),('Unavailable or excluded segments',pd.DataFrame(metadata.get('excluded',[]))),('Overall eligible population',pd.DataFrame([dict(unit=unit,**pooled)])),('Within-epoch environmental fits',environment)]:
        h,m=_table(table);body.extend(['<h2>'+heading+'</h2>','<div class="scroll">'+h+'</div>']);markdown.extend(['## '+heading,'',m])
    figures=[('timeline.png','Separate lines stop at every run and epoch boundary. Blank spans are not interpolated.')]
    figures += [(p.name,'Boundary detail: independent lines and unchanged exclusions; receipt labels are not event UTC.') for p in sorted(plots.glob('boundary_*.png'))]
    figures += [('distribution.png','Same eligible populations as the independent reports; no clipping or recalibration.'),('phase.png','Only accepted mechanically aligned epochs enter the pooled phase profile.')]
    for filename,caption in figures:
        if (plots/filename).exists():body.append('<figure><img src="plots/'+filename+'" alt="'+caption+'"><figcaption>'+caption+'</figcaption></figure>');markdown.extend([f'![{caption}](plots/{filename})',''])
    h,m=_table(pooled_stability);body.extend(['<h2>Stability pooled within valid runs</h2>',h]);markdown.extend(['## Stability pooled within valid runs','',m])
    body.append('<h2>Independent detail reports</h2><ul>')
    for part in parts:
        link=_relative(part['folder']/'report.html',out);body.append('<li><a href="'+escape(link,quote=True)+'">'+part['id']+'</a></li>');markdown.append(f'- [{part["id"]}]({link})')
    body.append('</ul><p><a href="summary.json">Machine-readable summary and provenance</a></p><p>CSV exports: '+', '.join('<a href="csv/'+p.name+'">'+p.name+'</a>' for p in sorted(csv.iterdir()))+'</p>')
    style='<style>body{font:16px system-ui;line-height:1.5;max-width:1250px;margin:2em auto;padding:0 1em}table{border-collapse:collapse}td,th{padding:.4em .7em;border:1px solid #ccd4dc;text-align:left}.scroll{overflow:auto}img{max-width:100%}a{color:#006580}figcaption{color:#455365}</style>'
    (stage/'report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>'+title+'</title>'+style+''.join(body)+'</html>')
    (stage/'report.md').write_text('\n'.join(markdown)+'\n')
    return dict(schema=SCHEMA,complete=True,package=package,collection_mode='combined',
        reports=metadata.get('reports',[dict(path=_relative(p['folder'],out),records={'pcps':p['summary'].get('records',p['summary'].get('clock',{}).get('records',0))}) for p in parts]),
        excluded=metadata.get('excluded',[]),epochs=epochs,shared_settings=shared,pooled_statistics=dict(unit=unit,**pooled),
        phase_pooling=dict(eligible_epochs=int(phase.analysis_epoch.nunique()),eligible_observations=len(phase),pooled_by='eligible observation count',unaligned_epochs_excluded=True),
        gaps=gaps,warnings=warnings,policy=policy,provenance=dict(independent_reports=[p['provenance'] for p in parts],
        implementation_sha256=fingerprint(Path(__file__))['sha256']),
        production_sources=[p['summary'].get('production_source') for p in parts],
        environment=fits,stability=pooled_stability.to_dict('records'))


def write_combined_report(reports, out, *, metadata=None):
    """Combine completed reports with interval exports; never recalibrate raw CSVs."""
    paths=[Path(p).resolve() for p in reports];out=Path(out).resolve()
    if not paths or len(set(paths))!=len(paths):raise ValueError('Use distinct completed report directories')
    if any(out==p or out.is_relative_to(p) for p in paths):raise ValueError('Combined output must be outside its source report directories')
    if any(p == out/name or p.is_relative_to(out/name) for p in paths for name in ('csv','plots')):
        raise ValueError('Combined output products would overwrite an independent source report')
    parts=[_load(p,i) for i,p in enumerate(paths,1)]
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.combined-analysis-',dir=out.parent) as temporary:
        stage=Path(temporary);result=_build(parts,stage,out,metadata or {})
        for part in parts:
            for entry in part['provenance'].values():
                if entry and fingerprint(Path(entry['path']))['sha256']!=entry['sha256']:raise ValueError('An independent source report changed during combination')
        write_json(stage/'summary.json',result)
        out.mkdir(exist_ok=True)
        for name in ('csv','plots'):shutil.copytree(stage/name,out/name,dirs_exist_ok=True)
        for name in ('summary.json','report.md','report.html'):shutil.copy2(stage/name,out/name)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports',nargs='+',type=Path,help='Completed independent report directories with --export-intervals')
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args(argv)
    try:write_combined_report(args.reports,args.out)
    except (OSError,ValueError,KeyError) as exc:parser.exit(2,f'Combined analysis failed: {exc}\n')
    print(f'Complete: {args.out.resolve()/"report.html"}')


if __name__=='__main__':main()
