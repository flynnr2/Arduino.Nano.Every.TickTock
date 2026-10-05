"""Optional causal next-full-swing prediction; never an input to clock rate.

The supplied rate baseline is independent of this diagnostic. A phase EWMA
learns deviations from the last K-swing mean, with a zero-mean prediction shape.
All adaptation and score windows use captured durations, not receipt cadence.
"""

import math
from collections import deque


MASK32 = (1 << 32) - 1
MODEL = 'causal-cycle-shape-ewma'
VERSION = 1
HALF_LIFE_SECONDS = 600.0
SCORE_WINDOW_SECONDS = 600.0
MAX_SCORE_SAMPLES = 10000
MAX_RESULTS = 120


class SwingForecaster:
    def __init__(self, cycle_length=0):
        self.cycle_length = self._cycle_length(cycle_length)
        self.revision = 0
        self.reset('initialization')

    @staticmethod
    def _cycle_length(value):
        try:
            length = int(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError('cycle_length must be an integer from 0 to 120') from error
        if isinstance(value, bool) or length != value or not 0 <= length <= 120:
            raise ValueError('cycle_length must be an integer from 0 to 120')
        return length

    def configure(self, cycle_length):
        length = self._cycle_length(cycle_length)
        if length == self.cycle_length:
            return False
        self.cycle_length = length
        self.reset('configuration changed')
        return True

    def reset(self, reason='reset'):
        self.revision += 1
        self.reset_reason = reason
        self.previous_seq = None
        self.phase = 0
        self.elapsed = 0.0
        self.periods = deque(maxlen=self.cycle_length or 1)
        self.shape = [0.0] * self.cycle_length
        self.phase_at = [None] * self.cycle_length
        self.next = self.last = None
        self.scores = deque()
        self.results = deque(maxlen=MAX_RESULTS)
        self.baseline_learning = True

    def observe(self, seq, period_us, baseline_period_us, elapsed_seconds, *,
                baseline_learning=False, timebase='PPS', observed_epoch=None):
        """Score, learn, then freeze seq+1. elapsed_seconds is this swing's duration.

        Callers reset on capture continuity or clock-domain breaks not visible in
        the sequence. A duplicate is ignored, even if its payload differs.
        """
        if not self.cycle_length:
            return
        if isinstance(seq, bool) or not isinstance(seq, int) or not 0 <= seq <= MASK32:
            self.reset('invalid sequence')
            return
        if seq == self.previous_seq:
            return
        if self.previous_seq is not None and (seq - self.previous_seq) & MASK32 != 1:
            self.reset('sequence discontinuity')
        try:
            valid = all(not isinstance(value, bool) and math.isfinite(value) and value > 0 for value in
                        (period_us, baseline_period_us, elapsed_seconds))
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid or timebase not in ('PPS', 'HOLDOVER'):
            self.reset('calibration unavailable' if timebase not in ('PPS', 'HOLDOVER')
                       or baseline_period_us is None else 'invalid period or duration')
            return
        if self.previous_seq is None:
            self.phase = seq % self.cycle_length
        else:
            # uint32 rollover is a consecutive swing, not a phase restart.
            self.phase = (self.phase + 1) % self.cycle_length
        self.previous_seq = seq
        self.elapsed += elapsed_seconds
        self.baseline_learning = bool(baseline_learning)
        if self.next is not None and self.next['target_seq'] == seq:
            frozen = self.next
            error = period_us - frozen['period_us']
            baseline_error = period_us - frozen['baseline_period_us']
            if not all(math.isfinite(value * value) for value in (error, baseline_error)):
                self.reset('nonfinite prediction error')
                return
            self.last = {'target_seq': seq, 'origin_seq': frozen['origin_seq'],
                         'predicted_period_us': frozen['period_us'],
                         'observed_period_us': period_us, 'error_us': error,
                         'baseline_error_us': baseline_error,
                         'timebase': frozen['timebase'], 'learning': frozen['learning']}
            self.results.append(dict(self.last, elapsed_seconds=self.elapsed,
                                     observed_epoch=observed_epoch))
            self.scores.append((self.elapsed, error * error, baseline_error * baseline_error))
            cutoff = self.elapsed - SCORE_WINDOW_SECONDS
            while self.scores and (self.scores[0][0] <= cutoff
                                   or len(self.scores) > MAX_SCORE_SAMPLES):
                self.scores.popleft()
        self.periods.append((period_us, elapsed_seconds))
        if self.cycle_length > 1 and len(self.periods) == self.cycle_length:
            residual = period_us - sum(p for p, _ in self.periods) / self.cycle_length
            previous_at = self.phase_at[self.phase]
            phase_seconds = (self.elapsed - previous_at if previous_at is not None
                             else sum(seconds for _, seconds in self.periods))
            weight = -math.expm1(-math.log(2) * phase_seconds / HALF_LIFE_SECONDS)
            self.shape[self.phase] += weight * (residual - self.shape[self.phase])
            self.phase_at[self.phase] = self.elapsed
        pattern = (self.shape[(self.phase + 1) % self.cycle_length]
                   - sum(self.shape) / self.cycle_length) if self.cycle_length > 1 else 0.0
        prediction = baseline_period_us + pattern
        if not math.isfinite(prediction) or prediction <= 0:
            self.reset('invalid learned prediction')
            return
        self.next = {'target_seq': (seq + 1) & MASK32, 'origin_seq': seq,
                     'period_us': prediction,
                     'baseline_period_us': baseline_period_us, 'pattern_us': pattern,
                     'timebase': timebase, 'learning': self._learning()}

    def _learning(self):
        return (self.baseline_learning or self.elapsed < HALF_LIFE_SECONDS
                or self.cycle_length > 1 and any(at is None for at in self.phase_at))

    def snapshot(self, available=True, stale=False):
        count = len(self.scores)
        return {'enabled': bool(self.cycle_length), 'cycle_length': self.cycle_length,
                'model': MODEL, 'version': VERSION,
                'half_life_seconds': HALF_LIFE_SECONDS,
                'learning': bool(self.cycle_length) and self._learning(),
                'available': bool(available and self.next is not None), 'stale': bool(stale),
                'revision': self.revision, 'reset_reason': self.reset_reason,
                'next': dict(self.next) if available and not stale and self.next else None,
                'last': dict(self.last) if self.last else None,
                'results': [dict(row) for row in self.results],
                'recent': {'count': count, 'window_seconds': SCORE_WINDOW_SECONDS,
                           'rmse_us': math.sqrt(sum(row[1] for row in self.scores) / count)
                           if count else None,
                           'baseline_rmse_us': math.sqrt(sum(row[2] for row in self.scores) / count)
                           if count else None}}
