"""Semantic column registry for analysis modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

import pandas as pd


@dataclass(frozen=True)
class SemanticColumn:
    role: str
    candidates: List[str]
    description: str = ""


@dataclass
class ColumnMap:
    roles: Dict[str, str] = field(default_factory=dict)
    missing: Dict[str, List[str]] = field(default_factory=dict)

    def column(self, role: str) -> Optional[str]:
        return self.roles.get(role)

    def require(self, role: str) -> str:
        column = self.column(role)
        if column is None:
            expected = ", ".join(self.missing.get(role, []))
            raise KeyError(f"missing semantic column role {role!r}; expected one of: {expected}")
        return column

    def series(self, frame: pd.DataFrame, role: str) -> pd.Series:
        return frame[self.require(role)]

    def has_all(self, roles: Iterable[str]) -> bool:
        return all(role in self.roles for role in roles)


SEMANTIC_COLUMNS: Dict[str, SemanticColumn] = {
    "elapsed_time": SemanticColumn("elapsed_time", ["t_s", "pps_t_s", "elapsed_time_s"]),
    "period": SemanticColumn("period", ["full_s", "period_s"]),
    "residual": SemanticColumn("residual", ["full_resid_us", "residual_us"]),
    "tick_interval": SemanticColumn("tick_interval", ["half_A_s", "tick", "tick_half_cycles"]),
    "tock_interval": SemanticColumn("tock_interval", ["half_B_s", "tock", "tock_half_cycles"]),
    "block_interval": SemanticColumn("block_interval", ["block_A_s", "block_B_s", "block_A", "block_B"]),
    "temperature": SemanticColumn("temperature", ["temp_C", "temperature_C"]),
    "humidity": SemanticColumn("humidity", ["humidity_pct"]),
    "pressure": SemanticColumn("pressure", ["pressure_hPa"]),
    "pps_residual": SemanticColumn("pps_residual", ["offline_pps_adjusted_residual_ns", "pps_raw_error_ns", "eh"]),
    "oscillator_correction": SemanticColumn("oscillator_correction", ["r_ppm", "corr_inst_ppm", "corr_blend_ppm"]),
    "pps_jitter": SemanticColumn("pps_jitter", ["j_ticks", "pps_jitter_ticks"]),
    "latency16": SemanticColumn("latency16", ["latency16"]),
    "cap16": SemanticColumn("cap16", ["cap16"]),
    "gps_status": SemanticColumn("gps_status", ["gps_status"]),
}


def build_column_map(frame: pd.DataFrame, registry: Dict[str, SemanticColumn] = SEMANTIC_COLUMNS) -> ColumnMap:
    columns = set(frame.columns)
    roles: Dict[str, str] = {}
    missing: Dict[str, List[str]] = {}
    for role, spec in registry.items():
        for candidate in spec.candidates:
            if candidate in columns:
                roles[role] = candidate
                break
        if role not in roles:
            missing[role] = list(spec.candidates)
    return ColumnMap(roles=roles, missing=missing)
