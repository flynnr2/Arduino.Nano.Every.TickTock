"""Artifact registry for stable analysis output IDs and paths."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


NAMESPACES = {"pcps", "pcsw", "combined", "report", "metadata"}
ARTIFACT_TYPES = {"plot", "csv", "summary", "report", "metadata"}
NAMESPACE_ID_PREFIX = {
    "pcps": "PCPS",
    "pcsw": "PCSW",
    "combined": "COMB",
    "report": "REPORT",
    "metadata": "META",
}
ARTIFACT_TYPE_DIR = {
    "plot": "plots",
    "csv": "csv",
    "summary": "summary",
    "report": "",
    "metadata": "",
}
ARTIFACT_TYPE_ID_PART = {
    "csv": "CSV",
    "summary": "SUM",
    "report": "REPORT",
    "metadata": "META",
}


@dataclass(frozen=True)
class Artifact:
    id: str
    namespace: str
    artifact_type: str
    slug: str
    title: str
    section: str
    relative_path: str
    source_files: List[str]
    analysis_name: str
    created_at: str
    description: Optional[str] = None
    caption: Optional[str] = None


class ArtifactRegistry:
    """Centralized artifact numbering, path allocation, and manifest writing."""

    def __init__(self, root: Path, *, created_at: Optional[str] = None) -> None:
        self.root = root
        self.created_at = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._artifacts: List[Artifact] = []
        self._counters: Dict[Tuple[str, str], int] = {}
        self._keys: Dict[Tuple[str, str, str], Artifact] = {}
        self._ids: Dict[str, Artifact] = {}
        self.ensure_directories()

    @property
    def artifacts(self) -> List[Artifact]:
        return list(self._artifacts)

    def ensure_directories(self) -> None:
        for namespace in ["pcps", "pcsw", "combined"]:
            for artifact_type in ["plots", "csv", "summary"]:
                (self.root / namespace / artifact_type).mkdir(parents=True, exist_ok=True)
        (self.root / "report").mkdir(parents=True, exist_ok=True)
        (self.root / "metadata").mkdir(parents=True, exist_ok=True)

    def register_plot(
        self,
        *,
        namespace: str,
        slug: str,
        title: str,
        section: str,
        source_files: Optional[Iterable[str]] = None,
        analysis_name: str = "",
        description: Optional[str] = None,
        caption: Optional[str] = None,
        extension: str = ".png",
    ) -> Artifact:
        return self.register(
            namespace=namespace,
            artifact_type="plot",
            slug=slug,
            title=title,
            section=section,
            source_files=source_files,
            analysis_name=analysis_name,
            description=description,
            caption=caption,
            extension=extension,
        )

    def register_csv(
        self,
        *,
        namespace: str,
        slug: str,
        title: str,
        section: str,
        source_files: Optional[Iterable[str]] = None,
        analysis_name: str = "",
        description: Optional[str] = None,
    ) -> Artifact:
        return self.register(
            namespace=namespace,
            artifact_type="csv",
            slug=slug,
            title=title,
            section=section,
            source_files=source_files,
            analysis_name=analysis_name,
            description=description,
            extension=".csv",
        )

    def register_summary(
        self,
        *,
        namespace: str,
        slug: str,
        title: str,
        section: str,
        source_files: Optional[Iterable[str]] = None,
        analysis_name: str = "",
        description: Optional[str] = None,
        extension: str = ".json",
    ) -> Artifact:
        return self.register(
            namespace=namespace,
            artifact_type="summary",
            slug=slug,
            title=title,
            section=section,
            source_files=source_files,
            analysis_name=analysis_name,
            description=description,
            extension=extension,
        )

    def register_report(
        self,
        *,
        slug: str,
        title: str,
        section: str = "Report",
        source_files: Optional[Iterable[str]] = None,
        analysis_name: str = "canonical_v2_report",
        description: Optional[str] = None,
        extension: str = ".md",
    ) -> Artifact:
        return self.register(
            namespace="report",
            artifact_type="report",
            slug=slug,
            title=title,
            section=section,
            source_files=source_files,
            analysis_name=analysis_name,
            description=description,
            extension=extension,
        )

    def register_metadata(
        self,
        *,
        slug: str,
        title: str,
        section: str = "Metadata",
        source_files: Optional[Iterable[str]] = None,
        analysis_name: str = "canonical_v2_metadata",
        description: Optional[str] = None,
        extension: str = ".json",
    ) -> Artifact:
        return self.register(
            namespace="metadata",
            artifact_type="metadata",
            slug=slug,
            title=title,
            section=section,
            source_files=source_files,
            analysis_name=analysis_name,
            description=description,
            extension=extension,
        )

    def register(
        self,
        *,
        namespace: str,
        artifact_type: str,
        slug: str,
        title: str,
        section: str,
        source_files: Optional[Iterable[str]] = None,
        analysis_name: str = "",
        description: Optional[str] = None,
        caption: Optional[str] = None,
        extension: str,
    ) -> Artifact:
        if namespace not in NAMESPACES:
            raise ValueError(f"Unsupported artifact namespace: {namespace}")
        if artifact_type not in ARTIFACT_TYPES:
            raise ValueError(f"Unsupported artifact type: {artifact_type}")
        clean_slug = sanitize_slug(slug)
        key = (namespace, artifact_type, clean_slug)
        if key in self._keys:
            raise ValueError(f"Duplicate artifact registration: {namespace}/{artifact_type}/{clean_slug}")

        number = self._next_number(namespace, artifact_type)
        artifact_id = self._artifact_id(namespace, artifact_type, number)
        if artifact_id in self._ids:
            raise ValueError(f"Duplicate artifact id: {artifact_id}")

        relative_path = self._relative_path(namespace, artifact_type, number, clean_slug, extension)
        artifact = Artifact(
            id=artifact_id,
            namespace=namespace,
            artifact_type=artifact_type,
            slug=clean_slug,
            title=title,
            section=section,
            relative_path=relative_path,
            source_files=list(source_files or []),
            analysis_name=analysis_name,
            created_at=self.created_at,
            description=description,
            caption=caption,
        )
        self._keys[key] = artifact
        self._ids[artifact_id] = artifact
        self._artifacts.append(artifact)
        (self.root / relative_path).parent.mkdir(parents=True, exist_ok=True)
        return artifact

    def get(self, namespace: str, artifact_type: str, slug: str) -> Artifact:
        key = (namespace, artifact_type, sanitize_slug(slug))
        try:
            return self._keys[key]
        except KeyError as exc:
            raise KeyError(f"Unknown artifact: {namespace}/{artifact_type}/{sanitize_slug(slug)}") from exc

    def path(self, artifact: Artifact) -> Path:
        return self.root / artifact.relative_path

    def markdown_path(self, artifact: Artifact, *, from_dir: str = "report") -> str:
        base = self.root / from_dir
        return Path("../" + artifact.relative_path).as_posix() if base.name == "report" else artifact.relative_path

    def to_manifest(self) -> Dict[str, object]:
        return {
            "schema": "pendulum_analysis.artifacts.v1",
            "created_at": self.created_at,
            "root": ".",
            "artifacts": [asdict(artifact) for artifact in self._artifacts],
        }

    def write_manifest(self) -> Path:
        path = self.root / "report" / "manifest.json"
        path.write_text(json.dumps(self.to_manifest(), indent=2, sort_keys=True), encoding="utf-8")
        return path

    def _next_number(self, namespace: str, artifact_type: str) -> int:
        key = (namespace, artifact_type)
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    def _artifact_id(self, namespace: str, artifact_type: str, number: int) -> str:
        prefix = NAMESPACE_ID_PREFIX[namespace]
        if artifact_type == "plot":
            return f"{prefix}-{number:02d}"
        return f"{prefix}-{ARTIFACT_TYPE_ID_PART[artifact_type]}-{number:02d}"

    def _relative_path(self, namespace: str, artifact_type: str, number: int, slug: str, extension: str) -> str:
        if not extension.startswith("."):
            extension = f".{extension}"
        type_dir = ARTIFACT_TYPE_DIR[artifact_type]
        if artifact_type in {"report", "metadata"}:
            filename = f"{slug}{extension}"
        else:
            filename = f"{number:02d}_{slug}{extension}"
        parts = [namespace]
        if type_dir:
            parts.append(type_dir)
        parts.append(filename)
        return Path(*parts).as_posix()


def sanitize_slug(slug: str) -> str:
    value = slug.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        raise ValueError("Artifact slug cannot be empty after sanitization")
    return value
