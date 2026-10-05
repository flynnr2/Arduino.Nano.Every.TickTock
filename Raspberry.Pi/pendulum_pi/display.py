"""One causal PPS-calibrated 600-second mean and optional swing prediction.

The PPS dual EWMA is unchanged. All swing components share the same captured
window and calibration. These derived values never modify canonical captures.
"""

import math
from collections import deque

from .forecast import SwingForecaster

MASK32 = (1 << 32) - 1
LOCKED = 2
WINDOW_SECONDS = 600
MAX_WINDOW_SAMPLES = 8192
MEAN_MODEL = 'swing_mean_600s_pps_dual_ewma_v1'


class RollingSwingMean:
    """Mean of completed, causally calibrated components in (now-600s, now]."""
    def __init__(self):
        self.samples = deque()
        self.sample_times = deque()
        self.restored = deque()
        self.restored_at = None
        self.elapsed_seconds = 0.0
        self.total_us = [0.0] * 4
        self.filling_seconds = 0.0
        self.paused = False

    def pause(self):
        # Retain past observations, but require fresh measured coverage before
        # calling a resumed window full. Missing elapsed time is never filled.
        self.paused = True
        self.filling_seconds = 0.0

    def restore(self, samples, saved_at):
        self.restored = deque(samples)
        self.restored_at = saved_at

    def _expire_restored(self, now):
        if self.filling_seconds >= WINDOW_SECONDS:
            self.restored.clear()
        elif now is not None:
            while self.restored and now - self.restored[0][0] >= WINDOW_SECONDS:
                self.restored.popleft()

    def checkpoint_samples(self, now):
        self._expire_restored(now)
        rows = list(self.restored) + [(at, parts) for (_, parts), at in
                                      zip(self.samples, self.sample_times) if at is not None]
        return [[now - at, list(parts)] for at, parts in rows if 0 <= now - at < WINDOW_SECONDS]

    def observe(self, intervals_us, now=None):
        seconds = sum(intervals_us) / 1_000_000.0
        self.elapsed_seconds += seconds
        self.filling_seconds = min(WINDOW_SECONDS, self.filling_seconds + seconds)
        self.paused = False
        self.samples.append((self.elapsed_seconds, tuple(intervals_us)))
        self.sample_times.append(now)
        self._expire_restored(now)
        for i, value in enumerate(intervals_us):
            self.total_us[i] += value
        cutoff = self.elapsed_seconds - WINDOW_SECONDS
        while self.samples and self.samples[0][0] <= cutoff:
            _, old = self.samples.popleft()
            self.sample_times.popleft()
            for i, value in enumerate(old):
                self.total_us[i] -= value
        if len(self.samples) > MAX_WINDOW_SAMPLES:
            # Bad/high-rate observations cannot make a diagnostic unbounded.
            # Discard this estimate rather than claiming a complete 600-second window.
            self.__init__()
            return False
        return True

    def snapshot(self, available, stale=False, now=None):
        self._expire_restored(now)
        count = len(self.samples) + len(self.restored)
        usable = bool(available and not stale and count and not self.paused)
        totals = [value + sum(parts[i] for _, parts in self.restored)
                  for i, value in enumerate(self.total_us)]
        components = tuple(value / count for value in totals) if usable else (None,) * 4
        tick, tick_block, tock, tock_block = components
        period = sum(components) if usable else None
        tick_delta = tick - tock if usable else None
        block_delta = tick_block - tock_block if usable else None
        return {'model': MEAN_MODEL, 'window_seconds': WINDOW_SECONDS, 'count': count,
                'elapsed_seconds': self.elapsed_seconds, 'filling_seconds': self.filling_seconds,
                'learning': not usable or self.filling_seconds < WINDOW_SECONDS,
                'priming': {'active': bool(self.restored), 'restored_samples': len(self.restored),
                            'fresh_seconds': self.filling_seconds,
                            'checkpoint_age_seconds': now - self.restored_at
                            if self.restored and now is not None else None},
                'period_us': period, 'bpm': 60_000_000.0 / period if period else None,
                'tick_us': tick, 'tock_us': tock, 'tick_block_us': tick_block,
                'tock_block_us': tock_block, 'tick_delta_us': tick_delta,
                'block_delta_us': block_delta,
                'half_delta_us': tick_delta + block_delta if usable else None,
                'open_imbalance_pct': 100 * tick_delta / period if period else None,
                'block_imbalance_pct': 200 * block_delta / (tick_block + tock_block) if usable else None}


class PpsClock:
    def __init__(self):
        self.previous = None
        self.candidate = None
        self.candidate_at = None
        self.rejected_edges = 0
        self.received_at = None
        self.nominal_hz = 0
        self.fast_hz = self.slow_hz = self.blended_hz = 0.0
        self.observation_hz = 0
        self.calibrated_at = None
        self.last_accepted = False
        self.mean_samples = deque()
        self.mean_total_ticks = 0

    @property
    def mean_hz(self):
        count = len(self.mean_samples)
        return self.mean_total_ticks / count if count else 0.0

    def observe(self, values, nominal_hz, now):
        previous = self.previous
        step = ((values["seq"] - previous["seq"]) & MASK32) if previous else None
        if step == 0:
            return False
        restarted = step is not None and step >= 0x80000000
        if restarted or nominal_hz != self.nominal_hz:
            self.__init__()
        candidate = self.candidate
        # A full timer wrap during silence makes modulo subtraction ambiguous.
        if candidate is not None and now - self.candidate_at >= (MASK32 + 1) / max(nominal_hz, 1):
            candidate = None
        ticks = ((values["edge_tcb0"] - candidate["edge_tcb0"]) & MASK32) if candidate else 0
        reference = self.blended_hz or nominal_hz
        # Match Nano's learned ±1.25 ms gate; cold start uses its nominal ±10% band.
        tolerance = nominal_hz / 800 if self.calibrated_at is not None else nominal_hz / 10
        plausible = candidate is not None and nominal_hz >= 1000 and abs(ticks - reference) <= tolerance
        valid = plausible and values["gps_status"] == candidate["gps_status"] == LOCKED
        # Keep the expected edge anchored across extras, including reported queue
        # losses. Sequence adjacency describes RAW captures, not one-second pulses.
        if candidate is None or plausible or ticks > reference + tolerance:
            self.candidate, self.candidate_at = dict(values), now
        if candidate is not None and not valid:
            self.rejected_edges += 1
        if valid:
            self.observation_hz = ticks
            if self.calibrated_at is not None and now - self.calibrated_at >= 10:
                self.mean_samples.clear()
                self.mean_total_ticks = 0
            self.mean_samples.append(ticks)
            self.mean_total_ticks += ticks
            if len(self.mean_samples) > WINDOW_SECONDS:
                self.mean_total_ticks -= self.mean_samples.popleft()
            if self.calibrated_at is None:
                self.fast_hz = self.slow_hz = float(ticks)
            else:
                self.fast_hz += -math.expm1(-math.log(2) / 20) * (ticks - self.fast_hz)
                self.slow_hz += -math.expm1(-math.log(2) / 3600) * (ticks - self.slow_hz)
            self.blended_hz = .75 * self.fast_hz + .25 * self.slow_hz
            self.calibrated_at = now
        self.previous, self.nominal_hz, self.last_accepted = dict(values), nominal_hz, valid
        self.received_at = now
        return restarted

    def stale(self, now):
        return self.calibrated_at is not None and now - self.calibrated_at >= 5

    def expired(self, now, holdover_seconds):
        return self.calibrated_at is not None and now - self.calibrated_at >= holdover_seconds

    def holding(self, now):
        return self.calibrated_at is not None and (self.stale(now) or
                self.previous is not None and self.previous["gps_status"] != LOCKED)

    def corrected_hz(self, nominal_hz, now, holdover_seconds=180):
        if nominal_hz == self.nominal_hz and self.calibrated_at is not None and not self.expired(now, holdover_seconds):
            return self.blended_hz
        return 0.0

    def mean_corrected_hz(self, nominal_hz, now, holdover_seconds=180):
        if nominal_hz == self.nominal_hz and not self.expired(now, holdover_seconds):
            return self.mean_hz
        return 0.0

    def snapshot(self, now, holdover_seconds=180):
        if self.expired(now, holdover_seconds):
            status = "EXPIRED"
        elif self.holding(now):
            status = "HOLDOVER"
        elif self.previous is None:
            status = "WAIT"
        elif self.previous["gps_status"] != LOCKED:
            status = {0: "NO PPS", 1: "ACQUIRING", 3: "HOLDOVER"}.get(
                self.previous["gps_status"], "REJECTED")
        elif self.calibrated_at is None:
            status = "WAIT"
        else:
            status = "LOCKED" if self.last_accepted else "REJECTED"
        return {"status": status, "initialized": self.calibrated_at is not None,
                "last_accepted_monotonic": self.calibrated_at,
                "calibration_age_seconds": now - self.calibrated_at if self.calibrated_at is not None else None,
                "holdover_limit_seconds": holdover_seconds,
                "expired": self.expired(now, holdover_seconds),
                "rejected_edges": self.rejected_edges,
                "record_age_seconds": now - self.received_at if self.received_at is not None else None,
                "holdover_age_ms": self.previous.get("holdover_age_ms") if self.previous else None,
                "gps_status": self.previous.get("gps_status") if self.previous else None,
                "stale": self.stale(now), "observation_hz": self.observation_hz,
                "fast_hz": self.fast_hz, "slow_hz": self.slow_hz,
                "blended_hz": self.blended_hz,
                "mean_hz": self.mean_hz if self.mean_samples else None,
                "mean_samples": len(self.mean_samples),
                "mean_learning": len(self.mean_samples) < WINDOW_SECONDS}


class DisplayEstimator:
    def __init__(self, pps_holdover_seconds=180, forecast_cycle_length=0):
        self.pps_holdover_seconds = pps_holdover_seconds
        self.forecaster = SwingForecaster(forecast_cycle_length)
        self.reset()

    def reset(self):
        """Start a new capture timeline without inheriting its clock scale."""
        self.clock = PpsClock()
        self.previous_swing = None
        self.nominal_hz = 0
        self._reset_estimates('capture timeline reset')

    def _reset_estimates(self, reason='capture discontinuity'):
        self.estimate_revision = getattr(self, 'estimate_revision', 0) + 1
        self.window = RollingSwingMean()
        self.forecaster.reset(reason)
        self.last_swing_at = self.last_capture_at = None
        self.calibration_paused = False

    def configure(self, pps_holdover_seconds=None, forecast_cycle_length=None):
        if pps_holdover_seconds is not None:
            if type(pps_holdover_seconds) is not int or not 5 <= pps_holdover_seconds <= 86400:
                raise ValueError('pps_holdover_seconds must be an integer from 5 to 86400')
            self.pps_holdover_seconds = pps_holdover_seconds
        if forecast_cycle_length is not None:
            return self.forecaster.configure(forecast_cycle_length)
        return False

    def observe(self, tag, values, nominal_hz, now, epoch=None):
        """Accept validated raw CSW/CPS dictionaries from the protocol parser."""
        if tag not in ('CSW', 'CPS'):
            return
        if nominal_hz != self.nominal_hz:
            self.reset()
            self.nominal_hz = nominal_hz
        if tag == 'CPS':
            if self.clock.observe(values, nominal_hz, now):
                self._reset_estimates('PPS sequence restarted')
                self.previous_swing = None
            return
        previous = self.previous_swing
        if previous and values['seq'] == previous['seq']:
            return
        if previous and (((values['seq'] - previous['seq']) & MASK32) != 1
                         or values['edge0_tcb0'] != previous['edge4_tcb0']
                         or values['drop_ir'] != previous['drop_ir']
                         or values['drop_swing'] != previous['drop_swing']):
            self._reset_estimates()
        self.previous_swing = dict(values)
        intervals = [((values[f'edge{i + 1}_tcb0'] - values[f'edge{i}_tcb0']) & MASK32)
                     for i in range(4)]
        ticks = sum(intervals)
        if not all(intervals) or ticks > 0x7FFFFFFF or nominal_hz < 1000:
            self._reset_estimates('invalid captured swing')
            return
        if self.last_capture_at is not None and now - self.last_capture_at >= 10:
            self._reset_estimates('capture silence')
        self.last_capture_at = now
        calibrated = self.clock.corrected_hz(nominal_hz, now, self.pps_holdover_seconds)
        if not calibrated:
            if not self.calibration_paused:
                self.window.pause()
                self.forecaster.reset('PPS calibration unavailable')
                self.calibration_paused = True
            return
        self.calibration_paused = False
        intervals_us = tuple(interval * 1_000_000.0 / calibrated for interval in intervals)
        if not self.window.observe(intervals_us, now):
            self._reset_estimates('mean window capacity exceeded')
            return
        self.last_capture_at = self.last_swing_at = now
        window = self.window.snapshot(True, now=now)
        timebase = 'HOLDOVER' if self.clock.holding(now) else 'PPS'
        self.forecaster.observe(values['seq'], sum(intervals_us), window['period_us'],
                                sum(intervals_us) / 1_000_000.0,
                                baseline_learning=window['learning'], timebase=timebase, observed_epoch=epoch)

    def snapshot(self, now):
        available = self.last_swing_at is not None
        expired = self.clock.expired(now, self.pps_holdover_seconds)
        stale = available and (now - self.last_swing_at >= 10 or expired or self.calibration_paused)
        holding = available and self.clock.holding(now) and not expired
        timebase = 'WAIT' if not available else 'STALE' if stale else 'HOLDOVER' if holding else 'PPS'
        from .rating import display_rows
        state = {'available': available, 'stale': stale,
                 'last_swing_monotonic': self.last_swing_at,
                 'estimate_revision': self.estimate_revision, 'timebase': timebase,
                 'window': self.window.snapshot(available, stale, now),
                 'pps': self.clock.snapshot(now, self.pps_holdover_seconds),
                 'forecast': self.forecaster.snapshot(available=available, stale=stale)}
        state['rows'] = display_rows(state)
        return state
