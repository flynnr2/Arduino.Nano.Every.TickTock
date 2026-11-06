#!/usr/bin/env python3
"""Phase-conditioned PPS latency plots; no timestamp/projection correction.

Example: python tools/capture_reconstruction/plot_latency_shape.py Data/20260917_noswings
The 63/82/135 guides are hypotheses for this recording, not universal limits.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'latency-shape-mpl'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
import numpy as np
import pandas as pd


def analyze(source, output):
    output.mkdir(parents=True, exist_ok=True)
    path = source / 'PCPS.CSV'
    f = pd.read_csv(path)
    required = ['seq', 'edge_tcb0', 'latency16', 'now32', 'cap16']
    for col in required:
        x = pd.to_numeric(f[col], errors='raise')
        limit = 65536 if col in ('latency16', 'cap16') else 2**32
        if not (x.notna() & (x == np.floor(x)) & x.between(0, limit - 1)).all():
            raise ValueError(f'Invalid unsigned integer in {col}')
        f[col] = x.astype('int64')
    if f.empty:
        raise ValueError('No PPS captures')
    f['source_row'] = np.arange(len(f)) + 2
    f['phase'] = (f.edge_tcb0 + 32768) % 65536 - 32768
    f['sample_phase'] = f.phase + f.latency16
    near = f.phase.between(-150, 180)
    f['region'] = np.where(near, 'near_wrap', 'away_from_wrap')
    f['quarter'] = np.arange(len(f)) * 4 // len(f) + 1
    bands = [(69, 75), (76, 79), (80, 89)]
    quarter_rows = []
    for quarter, group in f.loc[~near].groupby('quarter'):
        row = {'quarter': int(quarter), 'away_records': len(group),
               'max_latency': int(group.latency16.max())}
        for low, high in bands:
            row[f'percent_{low}_{high}'] = group.latency16.between(low, high).mean() * 100
        quarter_rows.append(row)
    pd.DataFrame(quarter_rows).to_csv(output / 'shoulders_by_quarter.csv', index=False)
    hist = f.groupby(['latency16', 'region']).size().unstack(fill_value=0)
    hist = hist.reindex(columns=['near_wrap', 'away_from_wrap'], fill_value=0)
    hist.to_csv(output / 'latency_by_region.csv')
    boundary = f.loc[near]
    boundary[required + ['source_row', 'phase', 'sample_phase']].to_csv(output / 'boundary_records.csv', index=False)
    boundary.groupby('phase').latency16.agg(['count', 'min', 'median', 'max']).to_csv(output / 'boundary_envelope.csv')
    high = f[f.latency16 >= 90]
    summary = {
        'source': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'records': len(f), 'near_wrap_window': [-150, 180],
        'near_wrap_records': int(near.sum()), 'away_records': int((~near).sum()),
        'latency_min': int(f.latency16.min()), 'latency_max': int(f.latency16.max()),
        'latency_mode': int(f.latency16.mode().iloc[0]),
        'count_63': int(f.latency16.eq(63).sum()), 'count_82': int(f.latency16.eq(82).sum()),
        'count_82_near': int((f.latency16.eq(82) & near).sum()),
        'away_latency_max': int(f.loc[~near, 'latency16'].max()) if (~near).any() else None,
        'at_least_90': len(high), 'at_least_90_away': int((~near & f.latency16.ge(90)).sum()),
        'at_least_90_phase_range': [int(high.phase.min()), int(high.phase.max())] if len(high) else None,
        'reconstruction_mismatches': int(((f.now32 - f.edge_tcb0) % 2**32 != f.latency16).sum()),
        'projection_offset_counts': {str(k): int(v) for k, v in ((f.cap16 - f.edge_tcb0) % 65536).value_counts().items()},
        'sequence_discontinuities': int(((f.seq.diff().iloc[1:] % 2**32) != 1).sum()),
        'guides': '63 baseline, 82 reread, max(63, 135-phase) post-wrap; descriptive hypotheses only',
    }
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})
    blue, orange = '#24658a', '#c46730'
    fig, axs = plt.subplots(2, 2, figsize=(14, 10), constrained_layout=True)
    fig.suptitle(f'PPS / TCB2 latency: {len(f):,} captures\nTCB0 overflow shape and the background shoulder', fontsize=18)
    ax = axs[0, 0]
    ax.bar(hist.index, hist.away_from_wrap, color=blue, label='Away from wrap')
    ax.bar(hist.index, hist.near_wrap, bottom=hist.away_from_wrap, color=orange, label='Near wrap: −150 to +180 cycles')
    ax.set(yscale='log', xlabel='latency16 (cycles)', ylabel='Captures (log scale)', title='The histogram mixes distinct mechanisms')
    ax.legend(fontsize=9)
    ax = axs[0, 1]
    counts = boundary.groupby(['phase', 'latency16']).size().reset_index(name='n')
    scatter = ax.scatter(counts.phase, counts.latency16, c=counts.n, s=16, cmap='viridis', norm=LogNorm(vmin=1, vmax=max(2, counts.n.max())))
    fig.colorbar(scatter, ax=ax, label='Captures at exact coordinate', shrink=.75)
    ax.plot([-150, -70], [63, 63], '--', color='black', lw=1)
    ax.plot([-69, -2], [82, 82], '--', color='black', lw=1)
    ax.plot([0, 72, 180], [135, 63, 63], '--', color='black', lw=1, label='Observed lower-edge guides')
    ax.axvline(0, color='gray', lw=.8)
    ax.set(xlabel='Edge phase relative to TCB0 wrap (cycles)', ylabel='latency16 (cycles)', title='A reread rail before wrap; a waiting ramp after', xlim=(-150, 180))
    ax.legend(fontsize=8)
    ax = axs[1, 0]
    view = hist.reindex(range(68, 91), fill_value=0)
    ax.bar(view.index, view.away_from_wrap, color=blue)
    ax.set(xlabel='latency16 (cycles)', ylabel='Captures away from wrap', title='Persistent shelves, distinct from the overflow ramp', xlim=(67.4, 90.6), ylim=(0, 570))
    for low, high in bands:
        mean = hist.reindex(range(low, high + 1), fill_value=0).away_from_wrap.mean()
        ax.hlines(mean, low - .4, high + .4, colors=orange, lw=2)
        ax.text((low + high) / 2, mean + (50 if low == 69 else 24), f'{mean:.0f}/bin', ha='center', fontsize=9)
    ax.annotate('Cutoff: 89 = 63 + 26', xy=(89, view.loc[89, 'away_from_wrap']), xytext=(79.5, 420), arrowprops={'arrowstyle': '->'}, fontsize=9)
    ax = axs[1, 1]
    ax.scatter(boundary.phase, boundary.sample_phase, s=9, alpha=.65, color=blue)
    ax.plot([-150, -70], [-87, -7], '--', color='black', lw=1)
    ax.plot([-69, -2], [13, 80], '--', color='black', lw=1)
    ax.plot([0, 72, 180], [135, 135, 243], '--', color='black', lw=1)
    ax.axvline(0, color='gray', lw=.8)
    ax.set(xlabel='Edge phase relative to TCB0 wrap (cycles)', ylabel='Counter sample time relative to wrap (cycles)', title='Queued captures reach the counter read at ~135', xlim=(-150, 180))
    for ax in axs.flat:
        ax.grid(axis='y', alpha=.15)
    fig.savefig(output / 'latency_shape.png', dpi=160)
    plt.close(fig)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    analyze(args.source, args.output or args.source / 'latency_investigation')
