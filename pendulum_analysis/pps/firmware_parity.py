"""Diagnostic replay of the current firmware FreqDiscipliner.

This is deliberately separate from canonical metrology. Inputs must be the
actual observe() calls: validator class/validity, foreground millis(), anomaly
flag, reset events, and effective tunables. Raw PCPS alone cannot reconstruct
queue timing, timeout calls or configuration changes and is not an exact replay.
The Q16 implementation models the fixed firmware, not older integer-only logs.
"""
from dataclasses import dataclass
from enum import IntEnum


class DiscState(IntEnum):
    FREE_RUN = 0
    ACQUIRE = 1
    DISCIPLINED = 2
    HOLDOVER = 3


@dataclass
class FirmwareConfig:
    fast_shift: int = 3
    slow_shift: int = 8
    blend_lo_ppm: int = 50
    blend_hi_ppm: int = 150
    lock_r_ppm: int = 175
    lock_mad_ticks: int = 600
    unlock_r_ppm: int = 300
    unlock_mad_ticks: int = 900
    lock_count: int = 30
    unlock_count: int = 5
    holdover_ms: int = 60000
    acquire_min_ms: int = 60000


def _elapsed(now, then):
    return (now - then) & 0xFFFFFFFF


def _update(state, sample, shift):
    error = (sample << 16) - state
    # C++ signed integer division truncates toward zero.
    delta = abs(error) // (1 << max(1, min(15, shift)))
    state += delta if error >= 0 else -delta
    return state, (state + 32768) >> 16


def _mad(values):
    magnitudes = sorted(abs(v) for v in values)
    median = magnitudes[len(values) // 2]
    return sorted(abs(v - median) for v in magnitudes)[len(values) // 2]


class FirmwareParity:
    """Stateful replay; call reset() whenever firmware resets its discipliner.

    config contains effective firmware tunables; changes take effect on the next
    observation. This intentionally does not infer validator decisions from
    GNSS lock or substitute capture timestamps for foreground time.
    """

    def __init__(self, nominal_hz=16_000_000, config=None):
        self.config = config or FirmwareConfig()
        self.reset(nominal_hz)

    def reset(self, nominal_hz=16_000_000):
        if not 0 <= nominal_hz <= 0x7FFFFFFF:
            raise ValueError("nominal_hz must fit the firmware signed error domain")
        self.state = DiscState.FREE_RUN
        self.fast = self.slow = self.applied = self.last_good_slow = nominal_hz
        self.fast_q16 = self.slow_q16 = nominal_hz << 16
        for name in ("r_ppm", "lock_streak", "unlock_streak", "transition_streak",
                     "holdover_age_ms", "holdover_start_ms", "acq_start_ms",
                     "lock_pass_mask", "unlock_breach_mask", "fast_err_ticks",
                     "slow_err_ticks", "applied_err_ticks", "fast_err_ppm",
                     "slow_err_ppm", "applied_err_ppm", "slow_mad_ticks",
                     "applied_mad_ticks", "mad_ticks"):
            setattr(self, name, 0)
        self._slow_residuals = []
        self._applied_residuals = []
        self._legacy_residuals = []

    def observe(self, sample_class, pps_valid, interval_ticks, now_ms, had_recent_anomaly=False):
        """Apply one firmware call (class 0/OK, 1/GAP, 2/DUP, 3/HARD_GLITCH)."""
        if not 0 <= interval_ticks <= 0x7FFFFFFF:
            raise ValueError("interval_ticks must fit the firmware signed error domain")
        if sample_class not in (0, 1, 2, 3, "OK", "GAP", "DUP", "HARD_GLITCH"):
            raise ValueError("unknown validator sample class")
        c = self.config
        now_ms &= 0xFFFFFFFF
        self.holdover_age_ms = _elapsed(now_ms, self.holdover_start_ms) if self.state == DiscState.HOLDOVER else 0
        self.transition_streak = self.lock_pass_mask = self.unlock_breach_mask = 0
        if pps_valid and self.state == DiscState.FREE_RUN:
            self.state = DiscState.ACQUIRE
            self.acq_start_ms = now_ms
            self.lock_streak = self.unlock_streak = 0
        elif not pps_valid and self.state == DiscState.DISCIPLINED:
            self.state = DiscState.HOLDOVER
            self.holdover_start_ms = now_ms
            self.last_good_slow = self.applied = self.slow
        elif not pps_valid and self.state == DiscState.ACQUIRE:
            self.state = DiscState.FREE_RUN
            self.unlock_streak = 0
        elif pps_valid and self.state == DiscState.HOLDOVER:
            self.state = DiscState.ACQUIRE
            self.acq_start_ms = now_ms
            self.lock_streak = self.unlock_streak = 0

        if pps_valid and sample_class in (0, "OK"):
            self.fast_q16, self.fast = _update(self.fast_q16, interval_ticks, c.fast_shift)
            self.slow_q16, self.slow = _update(self.slow_q16, interval_ticks, c.slow_shift)
            self.r_ppm = 1_000_000 * abs(self.fast - self.slow) // self.slow if self.slow else 0
            hi = c.blend_hi_ppm if c.blend_hi_ppm > c.blend_lo_ppm else (c.blend_lo_ppm + 1) & 0xFFFF
            weight = 0 if self.r_ppm <= c.blend_lo_ppm else (65535 if self.r_ppm >= hi else (self.r_ppm - c.blend_lo_ppm) * 65535 // (hi - c.blend_lo_ppm))
            self.applied = (self.slow * (65535 - weight) + self.fast * weight) // 65535
            for name in ("fast", "slow", "applied"):
                value = getattr(self, name)
                error = interval_ticks - value
                setattr(self, name + "_err_ticks", error)
                setattr(self, name + "_err_ppm", 1_000_000 * abs(error) // value if value else 0)
            legacy = self.slow_err_ticks if self.state in (DiscState.DISCIPLINED, DiscState.HOLDOVER) else self.applied_err_ticks
            for samples, value, name in ((self._slow_residuals, self.slow_err_ticks, "slow_mad_ticks"),
                                         (self._applied_residuals, self.applied_err_ticks, "applied_mad_ticks"),
                                         (self._legacy_residuals, legacy, "mad_ticks")):
                samples.append(value)
                del samples[:-31]
                setattr(self, name, _mad(samples))
            metrics = (self.applied_err_ppm, self.applied_mad_ticks, self.slow_err_ppm,
                       self.slow_mad_ticks, self.r_ppm)
            lock_limits = (c.lock_r_ppm, c.lock_mad_ticks, c.lock_r_ppm, c.lock_mad_ticks, c.lock_r_ppm)
            self.lock_pass_mask = sum(1 << i for i, (v, limit) in enumerate(zip(metrics, lock_limits)) if v < limit)
            if not had_recent_anomaly:
                self.lock_pass_mask |= 32
            lock_ok = self.r_ppm < c.lock_r_ppm and self.mad_ticks < c.lock_mad_ticks and not had_recent_anomaly
            self.lock_streak = min(255, self.lock_streak + 1) if lock_ok else 0
            if self.state == DiscState.ACQUIRE and _elapsed(now_ms, self.acq_start_ms) >= max(1, c.acquire_min_ms) and self.lock_streak >= max(1, min(60, c.lock_count)):
                self.state = DiscState.DISCIPLINED
                self.transition_streak = self.lock_streak
                self.applied = self.last_good_slow = self.slow
            if self.state == DiscState.DISCIPLINED:
                self.applied = self.last_good_slow = self.slow
                unlock_limits = (c.unlock_r_ppm, c.unlock_mad_ticks, c.unlock_r_ppm, c.unlock_mad_ticks, c.unlock_r_ppm)
                self.unlock_breach_mask = sum(1 << i for i, (v, limit) in enumerate(zip(metrics, unlock_limits)) if v > limit)
                if had_recent_anomaly:
                    self.unlock_breach_mask |= 32
                unlock_bad = self.r_ppm > c.unlock_r_ppm or self.mad_ticks > c.unlock_mad_ticks or had_recent_anomaly
                self.unlock_streak = min(255, self.unlock_streak + 1) if unlock_bad else 0
                if self.unlock_streak >= max(1, c.unlock_count):
                    self.transition_streak = self.unlock_streak
                    self.state = DiscState.ACQUIRE
                    self.acq_start_ms = now_ms
                    self.lock_streak = self.unlock_streak = 0
        if self.state == DiscState.HOLDOVER:
            self.applied = self.last_good_slow
            if _elapsed(now_ms, self.holdover_start_ms) > max(1, c.holdover_ms):
                self.state = DiscState.FREE_RUN
        if self.state == DiscState.FREE_RUN:
            self.lock_streak = self.unlock_streak = 0
        return self.state
