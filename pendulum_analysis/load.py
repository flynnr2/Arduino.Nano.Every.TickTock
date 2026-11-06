"""CSV loading and schema validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from .io import sha256_file
from .schema import (
    CANONICAL_PPS,
    CANONICAL_PPS_REQUIRED_COLUMNS,
    CANONICAL_SWING,
    CANONICAL_SWING_REQUIRED_COLUMNS,
    DERIVED,
    INPUT,
    OPTIONAL_COLUMNS,
    REQUIRED_NUMERIC_COLUMNS,
)
from .timeline import elapsed_u32, unwrap_u32


@dataclass(frozen=True)
class LoadResult:
    data: pd.DataFrame
    input_sha256: str
    warnings: List[str]
    validation: dict


def load_csv(path: Path) -> LoadResult:
    warnings: List[str] = []
    if not path.exists():
        raise FileNotFoundError(f"Input CSV does not exist: {path}")

    data = pd.read_csv(path)
    original_columns = list(data.columns)
    missing = [column for column in CANONICAL_SWING_REQUIRED_COLUMNS if column not in data.columns]
    if missing:
        raise ValueError(f"Input CSV is missing required PCSW/CSW column(s): {', '.join(missing)}")
    data = _add_swing_intervals(data, path, warnings)

    missing_optional = [column for column in OPTIONAL_COLUMNS if column not in data.columns]
    if missing_optional:
        warnings.append(f"Missing optional column(s): {', '.join(missing_optional)}")

    if INPUT.row_index not in data.columns:
        data.insert(0, INPUT.row_index, range(len(data)))

    for column in REQUIRED_NUMERIC_COLUMNS:
        coerced = pd.to_numeric(data[column], errors="coerce")
        bad = coerced.isna() & data[column].notna()
        if bad.any():
            rows = data.index[bad].tolist()[:10]
            raise ValueError(f"Required numeric column {column!r} contains non-numeric values at row(s): {rows}")
        if coerced.isna().any():
            rows = data.index[coerced.isna()].tolist()[:10]
            raise ValueError(f"Required numeric column {column!r} contains missing values at row(s): {rows}")
        data[column] = coerced

    validation = {
        "ok": True,
        "input_format": "canonical_swing",
        "required_columns": CANONICAL_SWING_REQUIRED_COLUMNS,
        "missing_required_columns": [],
        "missing_optional_columns": missing_optional,
        "original_columns": original_columns,
    }
    return LoadResult(data=data, input_sha256=sha256_file(path), warnings=warnings, validation=validation)


def _add_swing_intervals(data: pd.DataFrame, path: Path, warnings: List[str]) -> pd.DataFrame:
    result = data.copy()
    for column in CANONICAL_SWING_REQUIRED_COLUMNS:
        result[column] = _required_numeric(result, column)

    result[DERIVED.open_A] = elapsed_u32(result[CANONICAL_SWING.edge0_tcb0], result[CANONICAL_SWING.edge1_tcb0])
    result[DERIVED.block_A] = elapsed_u32(result[CANONICAL_SWING.edge1_tcb0], result[CANONICAL_SWING.edge2_tcb0])
    result[DERIVED.open_B] = elapsed_u32(result[CANONICAL_SWING.edge2_tcb0], result[CANONICAL_SWING.edge3_tcb0])
    result[DERIVED.block_B] = elapsed_u32(result[CANONICAL_SWING.edge3_tcb0], result[CANONICAL_SWING.edge4_tcb0])
    result[DERIVED.half_A] = elapsed_u32(result[CANONICAL_SWING.edge0_tcb0], result[CANONICAL_SWING.edge2_tcb0])
    result[DERIVED.half_B] = elapsed_u32(result[CANONICAL_SWING.edge2_tcb0], result[CANONICAL_SWING.edge4_tcb0])
    result[DERIVED.full] = elapsed_u32(result[CANONICAL_SWING.edge0_tcb0], result[CANONICAL_SWING.edge4_tcb0])
    result[DERIVED.half_asymmetry] = result[DERIVED.half_A] - result[DERIVED.half_B]
    result[DERIVED.normalized_half_asymmetry] = result[DERIVED.half_asymmetry] / result[DERIVED.full].replace(0, np.nan)

    # Calculate the component intervals used by the analysis metrics.
    result[INPUT.tick] = result[DERIVED.open_A]
    result[INPUT.tick_block] = result[DERIVED.block_A]
    result[INPUT.tock] = result[DERIVED.open_B]
    result[INPUT.tock_block] = result[DERIVED.block_B]
    result[INPUT.dropped] = (
        (result[CANONICAL_SWING.drop_ir] != 0)
        | (result[CANONICAL_SWING.drop_pps] != 0)
        | (result[CANONICAL_SWING.drop_swing] != 0)
    ).astype(int)
    result[INPUT.swing_index] = result[CANONICAL_SWING.seq]

    if CANONICAL_SWING.temperature_C in result.columns and INPUT.temp_C not in result.columns:
        result[INPUT.temp_C] = pd.to_numeric(result[CANONICAL_SWING.temperature_C], errors="coerce")

    pps_path = _find_sibling_pps(path)
    if pps_path is None:
        result[INPUT.gps_status] = 0
        warnings.append("Canonical swing input has no sibling PCPS.CSV/CPS.CSV; gps_status defaulted to 0")
        return result

    pps = pd.read_csv(pps_path)
    missing_pps = [column for column in CANONICAL_PPS_REQUIRED_COLUMNS if column not in pps.columns]
    if missing_pps:
        result[INPUT.gps_status] = 0
        warnings.append(f"Sibling PPS file {pps_path.name} missing column(s) {', '.join(missing_pps)}; gps_status defaulted to 0")
        return result

    for column in CANONICAL_PPS_REQUIRED_COLUMNS:
        pps[column] = _required_numeric(pps, column)

    swing_key = unwrap_u32(result[CANONICAL_SWING.edge0_tcb0]).rename("_edge0_unwrapped")
    pps_key = unwrap_u32(pps[CANONICAL_PPS.edge_tcb0]).rename("_pps_edge_unwrapped")
    swing = pd.DataFrame({"_row": np.arange(len(result)), "_edge0_unwrapped": swing_key})
    pps_lookup = pd.DataFrame(
        {
            "_pps_edge_unwrapped": pps_key,
            INPUT.gps_status: pps[CANONICAL_PPS.gps_status],
            INPUT.holdover_age_ms: pps[CANONICAL_PPS.holdover_age_ms],
            "_pps_drop_pps": pps[CANONICAL_PPS.drop_pps],
        }
    )
    merged = pd.merge_asof(
        swing.sort_values("_edge0_unwrapped"),
        pps_lookup.sort_values("_pps_edge_unwrapped"),
        left_on="_edge0_unwrapped",
        right_on="_pps_edge_unwrapped",
        direction="backward",
    ).sort_values("_row")

    result[INPUT.gps_status] = merged[INPUT.gps_status].fillna(0).astype(int).to_numpy()
    result[INPUT.holdover_age_ms] = merged[INPUT.holdover_age_ms].to_numpy()
    result[INPUT.dropped] = (result[INPUT.dropped].astype(bool) | (merged["_pps_drop_pps"].fillna(0) != 0).to_numpy()).astype(int)
    warnings.append(f"Loaded canonical swing input and aligned gps_status from sibling {pps_path.name}")
    return result


def _required_numeric(data: pd.DataFrame, column: str) -> pd.Series:
    coerced = pd.to_numeric(data[column], errors="coerce")
    bad = coerced.isna() & data[column].notna()
    if bad.any() or coerced.isna().any():
        rows = data.index[bad | coerced.isna()].tolist()[:10]
        raise ValueError(f"Required numeric column {column!r} contains invalid values at row(s): {rows}")
    return coerced


def _find_sibling_pps(path: Path) -> Optional[Path]:
    for name in ["PCPS.CSV", "CPS.CSV", "pcps.csv", "cps.csv", "_cps.csv"]:
        candidate = path.with_name(name)
        if candidate.exists():
            return candidate
    return None
