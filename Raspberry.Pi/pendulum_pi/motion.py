"""Optical passage speed and harmonic semi-amplitude estimates.

The fixed geometry is provisional, not an electrical switching calibration.
No turning point, force history or physical left/right direction is measured.
"""
from collections import deque
import math
from statistics import median

MODEL = 'optical_motion_3.79mm_1242mm_v1'
PATH_MM = 3.79
RADIUS_MM = 1242.0
WINDOW = 300
CYCLE_SWINGS = 15
CYCLE_LIMIT = 20
FIELDS = ('tick_velocity_mm_s', 'tock_velocity_mm_s',
          'tick_amplitude_deg', 'tock_amplitude_deg')


class MotionEstimator:
    def __init__(self):
        # An extra cycle permits twenty complete cycles despite partial ends.
        self.samples = deque(maxlen=WINDOW + CYCLE_SWINGS)
        self.sequence = None
        self.extended = None
        self.revision = 0

    def observe(self, seq, intervals_us):
        step = ((seq - self.sequence) & 0xffffffff) if self.sequence is not None else None
        if step == 0:
            return
        if step is not None and step != 1:
            self.__init__()
        self.extended = self.extended + 1 if self.extended is not None else seq
        self.sequence = seq
        parts = [value / 1e6 for value in intervals_us]
        period = math.fsum(parts)
        # Conservative optical clearance / seconds-pendulum plausibility gates.
        # A rejected passage is a gap, not an invitation to carry old estimates.
        if (not all(math.isfinite(v) and v > 0 for v in parts)
                or not 1.5 <= period <= 2.5
                or any(not .75 <= sum(parts[i:i+2]) <= 1.25
                       or parts[i+1] / sum(parts[i:i+2]) > .20 for i in (0, 2))):
            self.samples.clear()
            self.revision += 1
            return
        speeds = [PATH_MM / parts[i] for i in (1, 3)]
        amplitudes = [math.degrees(v * period / (2 * math.pi * RADIUS_MM)) for v in speeds]
        self.samples.append([self.extended, *speeds, *amplitudes])

    def snapshot(self, available, stale=False):
        usable = bool(available and not stale and self.samples)
        rows = list(self.samples)[-WINDOW:]
        latest = dict(zip(FIELDS, rows[-1][1:])) if usable else dict.fromkeys(FIELDS)
        means = {name: math.fsum(row[i+1] for row in rows) / len(rows) if usable else None
                 for i, name in enumerate(FIELDS)}
        cycles = {}
        for row in self.samples:
            cycles.setdefault(row[0] // CYCLE_SWINGS, []).append(row)
        complete = [rows for rows in cycles.values() if len(rows) == CYCLE_SWINGS][-CYCLE_LIMIT:]
        profile = []
        for phase in range(30):
            side, swing_phase = phase % 2, phase // 2
            selected = [row for cycle in complete for row in cycle
                        if row[0] % CYCLE_SWINGS == swing_phase]
            profile.append(dict(phase=phase, side='tick' if side == 0 else 'tock',
                                count=len(selected) if usable else 0,
                                velocity_mm_s=median(row[1+side] for row in selected) if usable and selected else None,
                                amplitude_deg=median(row[3+side] for row in selected) if usable and selected else None))
        # Compare like-direction passages two phases apart. An optical candidate
        # does not independently locate the impulse or identify contact angles.
        candidate = None
        if usable and len(complete) >= 3:
            gains = [point['velocity_mm_s'] / profile[(i-2) % 30]['velocity_mm_s'] - 1
                     for i, point in enumerate(profile)]
            peak = max(gains)
            if peak > 0:
                candidate = gains.index(peak)
        return dict(model=MODEL, revision=self.revision, available=usable, stale=stale,
                    effective_path_mm=PATH_MM, pivot_to_beam_mm=RADIUS_MM,
                    latest=latest, mean=means, count=len(rows), window_swings=WINDOW,
                    learning=len(rows) < WINDOW, cycle=dict(full_swings=15, half_phases=30,
                    complete_cycles=len(complete), candidate_phase=candidate, points=profile))
