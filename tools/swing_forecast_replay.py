#!/usr/bin/env python3
"""Replay the live Pi mean and forecaster without serial I/O or analysis packages.

RAW.jsonl is consumed in recorded receipt order. Separate PCPS/PCSW files need
--reconstruct: hardware-event ordering is an approximation to live delivery and
cannot validate receipt latency, batching, or staleness in the original run.
"""

import argparse
import csv
import hashlib
import heapq
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Raspberry.Pi'))
MASK32 = (1 << 32) - 1


def raw_records(path):
    from pendulum_pi.protocol import decode
    previous_ms = None
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            elapsed_ms = row['ts_ms']
            if not isinstance(elapsed_ms, (int, float)) or not math.isfinite(elapsed_ms):
                raise ValueError('RAW receipt timestamp is not finite')
            if previous_ms is not None and elapsed_ms < previous_ms:
                raise ValueError('RAW receipt timestamps reverse; replay one session at a time')
            previous_ms = elapsed_ms
            if row.get('fragment'):
                yield elapsed_ms / 1000, None
                continue
            raw = bytes.fromhex(row['raw_hex']) if 'raw_hex' in row else row['text'].encode('ascii')
            yield elapsed_ms / 1000, decode(raw)


def first_edge(path, name):
    with path.open(newline='') as handle:
        row = next(csv.DictReader(handle), None)
        if row is None:
            raise ValueError(f'empty capture stream: {path}')
        return int(row[name])


def event_stream(path, tag, edge_name, origin):
    from pendulum_pi.protocol import decode, SCHEMAS
    previous_edge = previous_seq = None
    ticks = 0
    with path.open(newline='') as handle:
        for row in csv.DictReader(handle):
            record = decode((','.join([tag] + [row[key] for key in SCHEMAS[tag][1]]) + '\n').encode('ascii'))
            edge, seq = record.values[edge_name], record.values['seq']
            if previous_edge is None:
                # Align the first edges in the common timer domain. Assumes the
                # streams begin less than half a timer wrap apart.
                ticks = ((edge - origin + (1 << 31)) & MASK32) - (1 << 31)
            else:
                seq_step = (seq - previous_seq) & MASK32
                if seq_step >= 1 << 31:
                    raise ValueError('capture restart cannot be aligned from separate streams')
                step = (edge - previous_edge) & MASK32
                if step >= 1 << 31:
                    raise ValueError('ambiguous or backward hardware edge in reconstructed stream')
                ticks += step
            previous_edge, previous_seq = edge, seq
            yield ticks, tag, record


def reconstructed_records(directory, nominal_hz):
    pps = directory / 'PCPS.CSV'
    swing = directory / 'PCSW.CSV'
    origin = first_edge(pps, 'edge_tcb0')
    streams = [event_stream(pps, 'CPS', 'edge_tcb0', origin),
               event_stream(swing, 'CSW', 'edge4_tcb0', origin)]
    # Stable stream-index tie breaking: PPS before swing at an identical edge.
    for ticks, _, record in heapq.merge(*streams, key=lambda item: (item[0], item[1])):
        yield ticks / nominal_hz, record


def digest(path):
    sha = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()


def replay(records, nominal_hz, cycle_length, holdover_seconds, scores_path=None):
    # Same implementation as live acquisition, imported only when replay starts.
    from pendulum_pi.display import DisplayEstimator
    from pendulum_pi.protocol import Contract
    estimator = DisplayEstimator(pps_holdover_seconds=holdover_seconds,
                                 forecast_cycle_length=cycle_length)
    contract = Contract()
    seen = scored = mature_scored = fragments = 0
    square = baseline_square = mature_square = mature_baseline_square = 0.0
    state = estimator.snapshot(0)
    last_now = 0
    last_score_key = None
    score_handle = scores_path.open('x', newline='') if scores_path else None
    writer = csv.DictWriter(score_handle, fieldnames=[
        'receipt_or_reconstructed_seconds', 'revision', 'target_seq', 'origin_seq',
        'predicted_period_us', 'observed_period_us', 'error_us', 'baseline_error_us',
        'timebase', 'learning']) if score_handle else None
    if writer:
        writer.writeheader()
    try:
        for now, record in records:
            last_now = now
            if record is None:
                fragments += 1
                continue
            if record.tag in ('CFG', 'SCH'):
                contract.observe(record)
                if contract.nominal_hz:
                    if nominal_hz and nominal_hz != contract.nominal_hz:
                        raise ValueError('specified nominal frequency contradicts RAW contract')
                    nominal_hz = contract.nominal_hz
            if record.tag not in ('CPS', 'CSW'):
                continue
            if not nominal_hz:
                raise ValueError('no CFG frequency before capture; supply --nominal-hz explicitly')
            seen += 1
            estimator.observe(record.tag, record.values, nominal_hz, now)
            if record.tag != 'CSW':
                continue
            state = estimator.snapshot(now)
            forecast = state['forecast']
            last = forecast['last']
            key = (forecast['revision'], last['origin_seq'], last['target_seq']) if last else None
            if last is None or key == last_score_key:
                continue
            last_score_key = key
            scored += 1
            square += last['error_us'] ** 2
            baseline_square += last['baseline_error_us'] ** 2
            if not last['learning']:
                mature_scored += 1
                mature_square += last['error_us'] ** 2
                mature_baseline_square += last['baseline_error_us'] ** 2
            if writer:
                writer.writerow({'receipt_or_reconstructed_seconds': now,
                                 'revision': forecast['revision'], **last})
    finally:
        if score_handle:
            score_handle.close()
    def metrics(count, total, baseline):
        return {'count': count, 'rmse_us': math.sqrt(total / count) if count else None,
                'baseline_rmse_us': math.sqrt(baseline / count) if count else None}
    return {'nominal_hz': nominal_hz, 'capture_records': seen, 'raw_fragments_skipped': fragments,
            'all_scores': metrics(scored, square, baseline_square),
            'after_learning_scores': metrics(mature_scored, mature_square, mature_baseline_square),
            'final': estimator.snapshot(last_now)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='RAW.jsonl, recording segment, or PCPS/PCSW directory')
    parser.add_argument('--reconstruct', action='store_true', help='explicitly permit hardware-event-order reconstruction')
    parser.add_argument('--nominal-hz', type=int, help='RAW fallback or required CSV timer frequency')
    parser.add_argument('--cycle-length', type=int, default=0, help='0 disables, 1 mean-only, 2..120 repeating cycle')
    parser.add_argument('--pps-holdover-seconds', type=int, default=180)
    parser.add_argument('--output', type=Path, help='new JSON result file; otherwise stdout')
    parser.add_argument('--scores-csv', type=Path, help='new per-target frozen-prediction score file')
    args = parser.parse_args(argv)
    source = args.source.resolve()
    raw = source / 'RAW.jsonl' if source.is_dir() else source
    if raw.is_file():
        if args.reconstruct:
            parser.error('--reconstruct is for separate capture files; RAW already has receipt order')
        sources = [raw]
        records = raw_records(raw)
        ordering = 'recorded RAW receipt order'
        limitations = ['RAW fragments are skipped and counted; syntactically malformed records stop replay.',
                       'This replays timing models from capture lines, not acquisition metadata-readiness or connection-recovery events.',
                       'A rotated segment without CFG requires an explicit --nominal-hz fallback; replay resets its learned models at entry.']
    else:
        if not args.reconstruct or not args.nominal_hz or args.nominal_hz < 1000:
            parser.error('separate captures require --reconstruct and --nominal-hz >= 1000')
        sources = [source / 'PCPS.CSV', source / 'PCSW.CSV']
        records = reconstructed_records(source, args.nominal_hz)
        ordering = 'hardware-event-order reconstruction; actual receipt order unavailable'
        limitations = ['Timer-domain event order cannot establish original receipt batching or transport staleness.',
                       'First stream edges must be less than half a uint32 timer wrap apart; adjacent stream gaps must be less than half a wrap.',
                       'A timer-wrap-sized silent gap cannot be resolved from separate streams; sequence restarts are rejected.',
                       'At equal timestamps PPS is processed before swing.']
    result = replay(records, args.nominal_hz, args.cycle_length,
                    args.pps_holdover_seconds, args.scores_csv)
    from pendulum_pi.forecast import MODEL, VERSION, HALF_LIFE_SECONDS
    result.update(ordering=ordering, limitations=limitations,
                  inputs=[{'path': str(path), 'sha256': digest(path)} for path in sources],
                  model={'identity': MODEL, 'version': VERSION, 'cycle_length': args.cycle_length,
                         'half_life_seconds': HALF_LIFE_SECONDS,
                         'baseline_identity': result['final']['window']['model'],
                         'pps_holdover_seconds': args.pps_holdover_seconds},
                  implementation_sha256={name: digest(ROOT / 'Raspberry.Pi' / 'pendulum_pi' / name)
                                         for name in ('display.py', 'forecast.py', 'protocol.py')},
                  replay_sha256=digest(Path(__file__)))
    output = json.dumps(result, indent=2, allow_nan=False) + '\n'
    if args.output:
        with args.output.open('x') as handle:
            handle.write(output)
    else:
        print(output, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
