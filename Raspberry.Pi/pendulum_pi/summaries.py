"""Bounded one-minute arrival-time summaries, never a substitute for raw timing data.

Bins are receiver-session monotonic minutes, split into fragments at segment
boundaries. Environment statistics are weighted by recorded captures per stream.
No empty bins are synthesized: absence is visible from bin/time coverage.
"""
from __future__ import annotations

import math

from .protocol import UINT32, sequence_step

ENV_FIELDS = ['temperature_C', 'humidity_pct', 'pressure_hPa']
STAT_FIELDS = ['period_nominal_us', *ENV_FIELDS]
SUMMARY_FIELDS = [
    'session', 'segment', 'stream', 'bin_start_ms', 'bin_end_ms',
    'first_ts_ms', 'last_ts_ms', 'first_host_epoch', 'last_host_epoch', 'partial',
    'capture_count', 'missing_sequences', 'duplicate_sequences', 'sequence_restarts',
    'baseline_unknown', 'drop_ir_increase', 'drop_pps_increase', 'drop_swing_increase',
    'drop_counter_resets', 'gps_unlocked_count', 'period_invalid_count',
] + [f'{key}_{stat}' for key in STAT_FIELDS for stat in ('count', 'mean', 'min', 'max', 'stddev')]


class Moments:
    def __init__(self):
        self.count = 0
        self.mean = self.m2 = 0.0
        self.minimum = self.maximum = None

    def add(self, value):
        if type(value) not in (int, float) or not math.isfinite(value):
            return
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)
        self.minimum = value if self.minimum is None else min(value, self.minimum)
        self.maximum = value if self.maximum is None else max(value, self.maximum)

    def columns(self):
        return ([self.count, self.mean, self.minimum, self.maximum,
                 math.sqrt(max(0, self.m2 / self.count))] if self.count else [0, '', '', '', ''])


class MinuteSummaries:
    def __init__(self):
        self.previous = {}
        self.bins = {}
        self.fragment_start_ms = 0

    def _row(self, stream, partial):
        bucket = self.bins.pop(stream)
        counters, stats = bucket['counters'], bucket['stats']
        return [stream, bucket['start'], bucket['start'] + 60000,
                bucket['first'], bucket['last'], bucket['first_epoch'], bucket['last_epoch'],
                int(partial or bucket['start'] < self.fragment_start_ms),
                *[counters[key] for key in SUMMARY_FIELDS[10:21]],
                *[value for key in STAT_FIELDS for value in stats[key].columns()]]

    def observe(self, stream, values, environment, ts_ms, epoch, nominal_hz):
        """Return a completed prior bin, if this capture crosses its boundary."""
        rows = []
        start = ts_ms // 60000 * 60000
        if stream in self.bins and self.bins[stream]['start'] != start:
            rows.append(self._row(stream, False))
        if stream not in self.bins:
            self.bins[stream] = {
                'start': start, 'first': ts_ms, 'last': ts_ms,
                'first_epoch': epoch, 'last_epoch': epoch,
                'counters': dict.fromkeys(SUMMARY_FIELDS[10:21], 0),
                'stats': {key: Moments() for key in STAT_FIELDS},
            }
        bucket = self.bins[stream]
        bucket['last'], bucket['last_epoch'] = ts_ms, epoch
        counters, stats = bucket['counters'], bucket['stats']
        counters['capture_count'] += 1
        previous = self.previous.get(stream)
        kind, missing = sequence_step(previous['seq'] if previous else None, values['seq'])
        counters['missing_sequences'] += missing
        counters['duplicate_sequences'] += kind == 'duplicate'
        counters['sequence_restarts'] += kind == 'restart'
        counters['baseline_unknown'] += previous is None
        changed_drops = False
        for key in ('drop_ir', 'drop_pps', 'drop_swing'):
            if previous is not None and key in previous and key in values:
                delta = (values[key] - previous[key]) & UINT32
                changed_drops |= bool(delta)
                if delta < 1 << 31:
                    counters[key + '_increase'] += delta
                else:
                    counters['drop_counter_resets'] += 1
        if stream == 'CPS':
            counters['gps_unlocked_count'] += values.get('gps_status') != 2
        for key in ENV_FIELDS:
            stats[key].add(environment.get(key))
        ticks = 0
        if stream == 'CSW':
            intervals = [(values[f'edge{i+1}_tcb0'] - values[f'edge{i}_tcb0']) & UINT32 for i in range(4)]
            if all(intervals) and sum(intervals) < 1 << 31:
                ticks = sum(intervals)
        elif previous and kind == 'next' and not changed_drops:
            if values.get('gps_status') == previous.get('gps_status') == 2:
                delta = (values['edge_tcb0'] - previous['edge_tcb0']) & UINT32
                if nominal_hz * .9 <= delta <= nominal_hz * 1.1:
                    ticks = delta
        if ticks and nominal_hz >= 1000 and kind not in ('duplicate', 'restart') and not changed_drops:
            stats['period_nominal_us'].add(ticks * 1_000_000.0 / nominal_hz)
        else:
            counters['period_invalid_count'] += 1
        self.previous[stream] = dict(values)
        return rows

    def flush(self, end_ms):
        rows = [self._row(stream, end_ms < bucket['start'] + 60000)
                for stream, bucket in list(self.bins.items())]
        self.fragment_start_ms = end_ms
        return rows
