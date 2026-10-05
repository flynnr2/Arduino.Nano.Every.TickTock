"""Single source of truth for input, derived, and flag column names."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class InputColumns:
    tick_block: str = "tick_block"
    tick: str = "tick"
    tock_block: str = "tock_block"
    tock: str = "tock"
    dropped: str = "dropped"
    gps_status: str = "gps_status"
    timestamp: str = "timestamp"
    swing_index: str = "swing_index"
    temp_C: str = "temp_C"
    humidity_pct: str = "humidity_pct"
    pressure_hPa: str = "pressure_hPa"
    holdover_age_ms: str = "holdover_age_ms"
    row_index: str = "row_index"


@dataclass(frozen=True)
class CanonicalSwingColumns:
    seq: str = "seq"
    edge0_tcb0: str = "edge0_tcb0"
    edge1_tcb0: str = "edge1_tcb0"
    edge2_tcb0: str = "edge2_tcb0"
    edge3_tcb0: str = "edge3_tcb0"
    edge4_tcb0: str = "edge4_tcb0"
    drop_ir: str = "drop_ir"
    drop_pps: str = "drop_pps"
    drop_swing: str = "drop_swing"
    temperature_C: str = "temperature_C"


@dataclass(frozen=True)
class CanonicalPpsColumns:
    seq: str = "seq"
    edge_tcb0: str = "edge_tcb0"
    gps_status: str = "gps_status"
    holdover_age_ms: str = "holdover_age_ms"
    cap16: str = "cap16"
    latency16: str = "latency16"
    now32: str = "now32"
    drop_pps: str = "drop_pps"
    temperature_C: str = "temperature_C"


@dataclass(frozen=True)
class DerivedColumns:
    f_used_hz: str = "f_used_hz"
    tick_half_cycles: str = "tick_half_cycles"
    tock_half_cycles: str = "tock_half_cycles"
    period_cycles: str = "period_cycles"
    A_half_cycles: str = "A_half_cycles"
    period_s: str = "period_s"
    rate_ppm: str = "rate_ppm"
    sec_per_day: str = "sec_per_day"
    min_per_day: str = "min_per_day"
    A_half_ms: str = "A_half_ms"
    phase_index: str = "phase_index"
    open_A: str = "open_A"
    block_A: str = "block_A"
    open_B: str = "open_B"
    block_B: str = "block_B"
    half_A: str = "half_A"
    half_B: str = "half_B"
    full: str = "full"
    half_asymmetry: str = "half_asymmetry"
    normalized_half_asymmetry: str = "normalized_half_asymmetry"


@dataclass(frozen=True)
class FlagColumns:
    is_locked: str = "is_locked"
    is_dropped: str = "is_dropped"
    is_valid: str = "is_valid"
    is_analysis: str = "is_analysis"
    is_robust_summary: str = "is_robust_summary"
    is_clean_primary: str = "is_clean_primary"
    is_period_outlier: str = "is_period_outlier"
    is_asymmetry_outlier: str = "is_asymmetry_outlier"
    is_clean_robust: str = "is_clean_robust"


INPUT = InputColumns()
CANONICAL_SWING = CanonicalSwingColumns()
CANONICAL_PPS = CanonicalPpsColumns()
DERIVED = DerivedColumns()
FLAGS = FlagColumns()

REQUIRED_COLUMNS: List[str] = [
    INPUT.tick_block,
    INPUT.tick,
    INPUT.tock_block,
    INPUT.tock,
    INPUT.dropped,
    INPUT.gps_status,
]

OPTIONAL_COLUMNS: List[str] = [
    INPUT.timestamp,
    INPUT.swing_index,
    INPUT.temp_C,
    INPUT.humidity_pct,
    INPUT.pressure_hPa,
    INPUT.holdover_age_ms,
]

REQUIRED_NUMERIC_COLUMNS: List[str] = REQUIRED_COLUMNS

CANONICAL_SWING_REQUIRED_COLUMNS: List[str] = [
    CANONICAL_SWING.seq,
    CANONICAL_SWING.edge0_tcb0,
    CANONICAL_SWING.edge1_tcb0,
    CANONICAL_SWING.edge2_tcb0,
    CANONICAL_SWING.edge3_tcb0,
    CANONICAL_SWING.edge4_tcb0,
    CANONICAL_SWING.drop_ir,
    CANONICAL_SWING.drop_pps,
    CANONICAL_SWING.drop_swing,
]

CANONICAL_PPS_REQUIRED_COLUMNS: List[str] = [
    CANONICAL_PPS.seq,
    CANONICAL_PPS.edge_tcb0,
    CANONICAL_PPS.gps_status,
    CANONICAL_PPS.holdover_age_ms,
    CANONICAL_PPS.drop_pps,
]

METRIC_COLUMNS: List[str] = [
    DERIVED.period_s,
    DERIVED.rate_ppm,
    DERIVED.sec_per_day,
    DERIVED.min_per_day,
    DERIVED.tick_half_cycles,
    DERIVED.tock_half_cycles,
    DERIVED.A_half_cycles,
    DERIVED.A_half_ms,
    DERIVED.open_A,
    DERIVED.block_A,
    DERIVED.open_B,
    DERIVED.block_B,
    DERIVED.half_A,
    DERIVED.half_B,
    DERIVED.full,
    DERIVED.half_asymmetry,
    INPUT.tick_block,
    INPUT.tock_block,
    INPUT.tick,
    INPUT.tock,
]

PHASEFOLD_COLUMNS: List[str] = [
    DERIVED.period_s,
    DERIVED.rate_ppm,
    DERIVED.tick_half_cycles,
    DERIVED.tock_half_cycles,
    DERIVED.A_half_ms,
    INPUT.tick_block,
    INPUT.tock_block,
]
