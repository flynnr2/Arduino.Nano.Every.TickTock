"""Typed analysis result objects shared by analysis modules and renderers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

import pandas as pd

from .artifacts import Artifact


AnalysisStatus = str


@dataclass
class AnalysisResult:
    name: str
    status: AnalysisStatus
    tier: str
    summary: Dict[str, Any] = field(default_factory=dict)
    tables: Dict[str, pd.DataFrame] = field(default_factory=dict)
    artifacts: List[Artifact] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)

    def to_manifest_entry(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["tables"] = {name: {"rows": int(len(frame)), "columns": list(frame.columns)} for name, frame in self.tables.items()}
        payload["artifacts"] = [asdict(artifact) for artifact in self.artifacts]
        return payload
