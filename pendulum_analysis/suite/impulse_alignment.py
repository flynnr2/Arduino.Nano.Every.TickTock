"""Conservative Synchronome feature alignment, with an unchanged-data fallback.

The adjacent positive half-period signature is an operator-established impulse
proxy, not a measurement of contact or force. Never realign individual windows.
"""
import numpy as np
import pandas as pd

from .common import environmental_validity

WINDOW_SECONDS = 600
MIN_PER_PHASE = 10
MIN_WINDOWS = 3
MIN_COVERAGE = .8
MIN_CLEAR_COVERAGE = .8


def _profile(group):
    """Require both dominant outliers to be adjacent and separated from noise."""
    stats = group.groupby('phase').value.agg(['mean', 'std', 'count']).reindex(range(30))
    if stats['count'].min() < MIN_PER_PHASE or not np.isfinite(stats['mean']).all():
        return None, 'insufficient phase exposure', np.nan
    excess = stats['mean'].to_numpy().copy() * 1e6
    for parity in (0, 1):
        excess[parity::2] -= np.median(excess[parity::2])
    anchor = int(np.argmax(excess + np.roll(excess, -1)))
    pair = [anchor, (anchor + 1) % 30]
    if set(np.argsort(excess)[-2:]) != set(pair):
        return None, 'no dominant adjacent positive pair', np.nan
    background = np.delete(excess, pair)
    scatter = 1.4826 * np.median(np.abs(background - np.median(background)))
    sampling = float(np.nanmedian(stats['std'] / np.sqrt(stats['count']))) * 1e6
    scale = max(scatter, sampling, 1.)
    strength = float(min(excess[pair]) / scale)
    if strength < 6 or min(excess[pair]) - max(background) < 3 * scale:
        return None, 'adjacent pair is not clearly separated from background', strength
    return anchor, 'clear adjacent positive pair', strength


def find_anchors(frame, cfg):
    """Freeze on the first third of windows; audit every sufficiently exposed bin."""
    rows, diagnostics = [], []
    for epoch, group in frame.groupby('epoch', sort=True):
        valid = group.calibrated_valid & group.phase15.ge(0)
        halves = []
        for side, offset in (('tick', 0), ('tock', 1)):
            half = pd.DataFrame(dict(phase=(2 * group.sequence_extended + offset) % 30,
                                     value=group[side + '_half_pps_s'],
                                     window=np.floor(group.elapsed_cycles / cfg.nominal_hz / WINDOW_SECONDS)))
            halves.append(half.loc[valid])
        half = pd.concat(halves, ignore_index=True)
        windows = sorted(np.floor(group.elapsed_cycles / cfg.nominal_hz / WINDOW_SECONDS).unique())
        training = windows[:max(1, len(windows) // 3)]
        anchor, reason, strength = _profile(half.loc[half.window.isin(training)])
        audits = []
        for window, data in half.groupby('window', sort=True):
            candidate, explanation, score = _profile(data)
            sufficient = explanation != 'insufficient phase exposure'
            audits.append(dict(epoch=int(epoch), window=int(window), start_s=int(window)*WINDOW_SECONDS,
                               training=window in training, candidate_anchor_phase30=candidate,
                               sufficient_exposure=sufficient, matches_frozen_anchor=candidate is not None and candidate == anchor,
                               strength=score, reason=explanation))
        exposed = [a for a in audits if a['sufficient_exposure']]
        # Include intervals with no eligible timing at all in the coverage denominator.
        observed_windows = np.floor(group.elapsed_cycles / cfg.nominal_hz / WINDOW_SECONDS).nunique()
        coverage = len(exposed) / max(observed_windows, 1)
        clear = [a for a in exposed if a['candidate_anchor_phase30'] is not None]
        matching = sum(a['matches_frozen_anchor'] for a in exposed)
        clear_coverage = matching / max(observed_windows, 1)
        conflicts = sum(not a['matches_frozen_anchor'] for a in clear)
        if anchor is not None:
            if conflicts:
                reason = 'clear impulse signatures disagree between windows'
            elif len(exposed) < MIN_WINDOWS or coverage < MIN_COVERAGE:
                reason = 'insufficient stability coverage'
            elif matching < MIN_WINDOWS or clear_coverage < MIN_CLEAR_COVERAGE:
                reason = 'insufficient clear windows confirming the frozen anchor'
            else:
                reason = 'clear anchor confirmed with sufficient coverage and no confident conflicts'
        accepted = anchor is not None and reason.startswith('clear anchor confirmed')
        rows.append(dict(epoch=int(epoch), status='aligned' if accepted else 'fallback',
                         anchor_phase30=anchor if accepted else None,
                         candidate_anchor_phase30=anchor, strength=strength,
                         stability_windows=len(exposed), matching_windows=matching,
                         ambiguous_windows=len(exposed)-len(clear), conflicting_windows=conflicts,
                         window_coverage=coverage, clear_window_coverage=clear_coverage, reason=reason,
                         pairing=('tock(n) + tick(n+1)' if anchor % 2 else 'tick(n) + tock(n)') if accepted else 'original source full swings'))
        diagnostics.extend(audits)
    return pd.DataFrame(rows), pd.DataFrame(diagnostics, columns=[
        'epoch', 'window', 'start_s', 'training', 'candidate_anchor_phase30',
        'sufficient_exposure', 'matches_frozen_anchor', 'strength', 'reason'])


def align_impulse(frame, cfg):
    """Re-pair an odd anchor while retaining the actual tick/tock identities.

    Capture fields (seq, source_row and edge*_tcb0) always identify the first
    source record. Explicit directional source IDs identify the contributing
    halves. Source-derived intervals are exported separately by the caller.
    """
    anchors, audit = find_anchors(frame, cfg)
    f = frame.copy(deep=anchors.status.eq('aligned').any())
    environment_masks, _, _ = environmental_validity(frame, frame, cfg)
    f['original_phase15'] = frame.phase15
    f['original_elapsed_cycles'] = frame.elapsed_cycles
    f['analysis_cycle'] = np.floor((frame.sequence_extended - cfg.phase_origin) / 15)
    f['tick_phase30'] = 2 * frame.phase15
    f['tock_phase30'] = 2 * frame.phase15 + 1
    f['impulse_aligned'] = False
    for side in ('tick', 'tock'):
        f[side + '_source_row'] = frame.source_row
        f[side + '_source_sequence'] = frame.sequence_extended
    for row in anchors.itertuples():
        if row.status != 'aligned':
            continue
        anchor = int(row.anchor_phase30)
        mask = frame.epoch.eq(row.epoch)
        odd = anchor % 2
        first_half = 2 * frame.sequence_extended + odd
        phase = ((first_half - anchor) % 30) // 2
        f.loc[mask, 'phase15'] = phase.loc[mask]
        f.loc[mask, 'analysis_cycle'] = np.floor((first_half.loc[mask] - anchor) / 30)
        f.loc[mask, 'tick_phase30'] = (2 * phase + odd).loc[mask]
        f.loc[mask, 'tock_phase30'] = (2 * phase + 1 - odd).loc[mask]
        f.loc[mask, 'impulse_aligned'] = True
        if not odd:
            continue
        # Never join a gap, reset, missing source row or a broken edge chain.
        adjacent = (frame.epoch.eq(frame.epoch.shift(-1))
                    & frame.sequence_extended.shift(-1).sub(frame.sequence_extended).eq(1)
                    & frame.source_row.shift(-1).sub(frame.source_row).eq(1)
                    & frame.edge4_tcb0.eq(frame.edge0_tcb0.shift(-1)))
        join = mask & adjacent
        for name in ('source_row', 'sequence_extended'):
            dest = 'tick_source_row' if name == 'source_row' else 'tick_source_sequence'
            f.loc[mask, dest] = frame[name].shift(-1).where(adjacent).loc[mask]
        for suffix in ('_s', '_pps_s'):
            for component in ('open', 'blocked', 'half'):
                col = 'tick_' + component + suffix
                f.loc[mask, col] = frame[col].shift(-1).where(adjacent).loc[mask]
            f.loc[mask, 'full' + suffix] = (f['tock_half' + suffix] + f['tick_half' + suffix]).loc[mask]
        f.loc[mask, 'tick_blocked_fraction'] = frame.tick_blocked_fraction.shift(-1).where(adjacent).loc[mask]
        # Invalid captures still need a finite plotting coordinate; their
        # reconstructed durations remain ineligible and are never used.
        shift = frame.tick_half_s.where(frame.raw_valid, 0) * cfg.nominal_hz
        f.loc[mask, 'elapsed_cycles'] = (frame.elapsed_cycles + shift).loc[mask]
        for col in ('raw_valid', 'calibrated_valid'):
            f.loc[mask, col] = (frame[col] & frame[col].shift(-1, fill_value=False) & join).loc[mask]
        # Environmental context must be valid on both contributing source records.
        for col in ('temperature_C', 'humidity_pct', 'pressure_hPa'):
            if col in f:
                source_valid = pd.Series(environment_masks[col], index=frame.index)
                both = adjacent & source_valid & source_valid.shift(-1, fill_value=False)
                f.loc[mask, col] = ((frame[col] + frame[col].shift(-1)) / 2).where(both).loc[mask]
    warnings = [f"Epoch {r.epoch}: Impulse alignment unavailable; phases are sequence-based ({r.reason})."
                for r in anchors.itertuples() if r.status == 'fallback']
    warnings += [f"Epoch {r.epoch}: Impulse alignment retained with a frozen anchor; {r.ambiguous_windows} sufficiently exposed windows are ambiguous. Inspect swing_impulse_stability.csv."
                 for r in anchors.itertuples() if r.status == 'aligned' and r.ambiguous_windows]
    statuses = set(anchors.status)
    metadata = dict(requested=True, status='aligned' if statuses == {'aligned'} else 'fallback' if statuses == {'fallback'} else 'mixed',
                    warnings=warnings, epochs=anchors.to_dict('records'),
                    method='Adjacent positive half-period signature; first-third template frozen per epoch; at least 80% of observed ten-minute windows must clearly confirm it, with no confident conflicting anchor. Ambiguous windows are reported without retuning. Impulse identity is an operator-established proxy, not a direct force measurement.',
                    thresholds=dict(window_seconds=WINDOW_SECONDS, min_per_phase=MIN_PER_PHASE,
                                    min_stability_windows=MIN_WINDOWS, min_window_coverage=MIN_COVERAGE,
                                    min_clear_window_coverage=MIN_CLEAR_COVERAGE,
                                    min_strength_sigma=6, min_separation_sigma=3),
                    source_fields='seq, source_row and edge*_tcb0 retain the first source capture; tick/tock_source_row and tick/tock_source_sequence identify each half. Original derived intervals are in swing_source_intervals when interval export is requested.')
    return f, dict(swing_impulse_anchors=anchors, swing_impulse_stability=audit), metadata
