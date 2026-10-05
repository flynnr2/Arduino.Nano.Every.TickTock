"""Wrap-safe 32-bit counter timeline helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd


U32_MODULUS = 2**32
U32_WRAP_THRESHOLD = 2**31


def elapsed_u32(start: pd.Series, end: pd.Series) -> pd.Series:
    return ((end.astype("uint64") - start.astype("uint64")) % U32_MODULUS).astype("int64")


def delta_u32(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    delta = numeric.diff()
    return delta.where(delta >= 0, delta + U32_MODULUS)


def unwrap_u32(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").astype("int64")
    diffs = numeric.diff().fillna(0)
    wraps = (diffs < -U32_WRAP_THRESHOLD).cumsum()
    return numeric + wraps * U32_MODULUS


def suspicious_reverse_jumps(values: pd.Series) -> int:
    numeric = pd.to_numeric(values, errors="coerce")
    diffs = numeric.diff()
    return int(((diffs < 0) & (diffs > -U32_WRAP_THRESHOLD)).sum())


def duplicate_timestamps(values: pd.Series) -> int:
    numeric = pd.to_numeric(values, errors="coerce")
    return int((numeric.diff() == 0).sum())
