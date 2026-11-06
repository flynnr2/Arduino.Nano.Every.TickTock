"""First-class pipeline stage contracts for the canonical analysis flow."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .artifacts import ArtifactRegistry
from .config import AnalysisConfig
from .pcps import PcpsAnalysis
from .results import AnalysisResult


@dataclass
class LoadStageOutput:
    primary_csv: Path
    discovered: Dict[str, Optional[Path]]
    data: pd.DataFrame
    validation: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)


@dataclass
class ValidateStageOutput:
    data: pd.DataFrame
    warnings: List[str] = field(default_factory=list)


@dataclass
class DeriveStageOutput:
    data: pd.DataFrame
    pcps: PcpsAnalysis
    allan: pd.DataFrame
    warnings: List[str] = field(default_factory=list)


@dataclass
class PlanStageOutput:
    bundle: Any
    results: List[AnalysisResult]


@dataclass
class PipelineContext:
    input_path: Path
    output_dir: Path
    config: AnalysisConfig
    registry: ArtifactRegistry
    exact_command: str
