"""Run-directory discovery and input manifest generation."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from .io import sha256_file


RUN_FILE_CANDIDATES = {
    "pcsw": ["PCSW.CSV", "pcsw.csv", "CSW.CSV", "_csw.csv"],
    "pcps": ["PCPS.CSV", "pcps.csv", "CPS.CSV", "_cps.csv"],
    "sts": ["STS.CSV", "sts.csv"],
    "uno": ["UNO.CSV", "uno.csv"],
    "cfg": ["CFG.CSV", "cfg.csv"],
    "hdr": ["HDR.CSV", "hdr.csv"],
}


def discover_run_files(input_path: Path) -> Dict[str, Optional[Path]]:
    if input_path.is_file():
        files: Dict[str, Optional[Path]] = {key: None for key in RUN_FILE_CANDIDATES}
        lower = input_path.name.lower()
        if "pcsw" in lower or lower.endswith("_csw.csv") or lower == "csw.csv":
            files["pcsw"] = input_path
        else:
            files["pcsw"] = input_path
        sibling_pcps = _find_first(input_path.parent, RUN_FILE_CANDIDATES["pcps"])
        files["pcps"] = sibling_pcps
        return files

    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")
    if not input_path.is_dir():
        raise ValueError(f"Input path is neither a CSV file nor a directory: {input_path}")
    return {key: _find_first(input_path, names) for key, names in RUN_FILE_CANDIDATES.items()}


def select_primary_csv(input_path: Path, discovered: Dict[str, Optional[Path]]) -> Path:
    if input_path.is_file():
        return input_path
    pcsw = discovered.get("pcsw")
    if pcsw is not None:
        return pcsw
    raise ValueError("Input directory does not contain PCSW.CSV/CSW.CSV")


def build_manifest(input_path: Path, outdir: Path, config: dict, discovered: Dict[str, Optional[Path]], warnings: List[str]) -> Dict[str, object]:
    files = {}
    for key, path in discovered.items():
        if path is None:
            files[key] = {"present": False}
            warnings.append(f"Optional input file for {key.upper()} was not found")
            continue
        files[key] = _file_manifest(path)
    return {
        "run_name": input_path.stem if input_path.is_file() else input_path.name,
        "input_path": str(input_path),
        "output_dir": str(outdir),
        "config": config,
        "files": files,
        "warnings": warnings,
    }


def write_manifest(manifest: Dict[str, object], path: Path) -> None:
    import json

    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def load_optional_csv(path: Optional[Path], warnings: List[str], label: str) -> Optional[pd.DataFrame]:
    if path is None:
        return None
    try:
        return pd.read_csv(path)
    except Exception as exc:
        warnings.append(f"Could not read optional {label} file {path}: {exc}")
        return None


def _find_first(directory: Path, names: Iterable[str]) -> Optional[Path]:
    for name in names:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def _file_manifest(path: Path) -> Dict[str, object]:
    try:
        columns = list(pd.read_csv(path, nrows=0).columns)
        rows = max(0, sum(1 for _ in path.open("rb")) - 1)
        return {
            "present": True,
            "path": str(path),
            "rows": int(rows),
            "sha256": sha256_file(path),
            "columns": columns,
        }
    except Exception as exc:
        return {"present": True, "path": str(path), "error": str(exc)}
