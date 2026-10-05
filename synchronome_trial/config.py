"""Explicit trial choices; none are fitted to maximise a result."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class TrialConfig:
    nominal_hz: float = 16_000_000.0
    pps_window_seconds: float = 61.0
    phase_origin: int = 0
    baseline_seconds: float = 3600.0
    event_baseline_seconds: float = 1800.0
    event_guard_seconds: float = 300.0
    event_view_seconds: float = 3600.0
    event_separation_seconds: float = 1800.0
    max_events: int = 6
    difference_threshold_us: float = 80.0
    period_threshold_us: float = 8.0
    flag_threshold_us: float = 25.0
    impulse_phase: int | None = None
    impulse_side: str | None = None

    def __post_init__(self):
        for name in ("nominal_hz", "pps_window_seconds", "baseline_seconds",
                     "event_baseline_seconds", "event_guard_seconds",
                     "event_view_seconds", "event_separation_seconds",
                     "difference_threshold_us", "period_threshold_us", "flag_threshold_us"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not isinstance(self.max_events, int) or isinstance(self.max_events, bool) or self.max_events < 1:
            raise ValueError("max_events must be a positive integer")
        if not isinstance(self.phase_origin, int) or isinstance(self.phase_origin, bool):
            raise ValueError("phase_origin must be an integer")
        if (self.impulse_phase is None) != (self.impulse_side is None):
            raise ValueError("impulse phase and side must be supplied together")
        if self.impulse_phase is not None:
            if not isinstance(self.impulse_phase, int) or isinstance(self.impulse_phase, bool) or not 0 <= self.impulse_phase < 15:
                raise ValueError("impulse phase must be 0..14")
            if self.impulse_side not in ("tick", "tock"):
                raise ValueError("impulse side must be tick or tock")
