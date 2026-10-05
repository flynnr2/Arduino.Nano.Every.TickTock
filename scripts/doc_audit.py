#!/usr/bin/env python3
"""Lightweight repository documentation audit.

Checks Markdown links, duplicate tracked docs/config files, generated artifacts,
and stale references that commonly indicate documentation drift.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache"}
DOC_EXTS = {".md", ".rst", ".txt"}
YAML_EXTS = {".yaml", ".yml"}
GENERATED_NAMES = {".DS_Store", "__MACOSX"}
GENERATED_REFERENCE_PREFIXES = ("report/", "metadata/", "pcsw/", "pcps/", "combined/", "csv/", "plots/")
# These inline-code paths describe files on the deployed Pi, not this checkout.
# Actual Markdown links still go through check_markdown_links without exemptions.
PI_DEPLOYMENT_PREFIXES = ("/boot/", "/opt/pendulum/", "/var/lib/pendulum/", "/run/pendulum/",
                          "/run/pendulum-i2c/", "runtime_dir/")
STALE_REFERENCES = [
    "Docs/IMPLEMENTATION.md",
    "Docs/Pendulum_CSV_Semantics.md",
    "analysis_lineage 2.md",
    "analysis_mask_audit.md",
    "Docs/prompts_analysis/",
    "Docs/prompts_codex/",
]


def main() -> int:
    files = tracked_files()
    # Check repository candidates, not ignored recordings, virtualenvs or OS files.
    all_paths = [ROOT / path for path in files]
    issues: list[str] = []

    issues.extend(check_generated_artifacts(all_paths))
    issues.extend(check_duplicate_files(files))
    issues.extend(check_duplicate_markdown(files))
    issues.extend(check_duplicate_yaml(files))
    issues.extend(check_markdown_links(files))
    issues.extend(check_stale_references(files))
    issues.extend(check_missing_referenced_files(files))

    if issues:
        print("Documentation audit found issues:\n")
        for issue in issues:
            print(f"- {issue}")
        return 1

    print("Documentation audit passed.")
    return 0


def tracked_files() -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [path.relative_to(ROOT) for path in walk_repo() if path.is_file()]
    return [Path(line) for line in result.stdout.splitlines() if line]


def walk_repo() -> list[Path]:
    paths: list[Path] = []
    for directory, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        base = Path(directory)
        for name in filenames:
            paths.append(base / name)
        for name in dirnames:
            if name in GENERATED_NAMES:
                paths.append(base / name)
    return paths


def check_generated_artifacts(paths: list[Path]) -> list[str]:
    issues = []
    for path in paths:
        if path.name == ".DS_Store" or path.name == "__MACOSX" or path.name.endswith(".egg-info"):
            issues.append(f"generated artifact present: {rel(path)}")
    return issues


def check_duplicate_files(files: list[Path]) -> list[str]:
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        abs_path = ROOT / path
        if abs_path.is_file() and path.suffix.lower() in DOC_EXTS | YAML_EXTS:
            groups[file_hash(abs_path)].append(path)
    return format_duplicate_groups("duplicate file content", groups)


def check_duplicate_markdown(files: list[Path]) -> list[str]:
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        if path.suffix.lower() != ".md":
            continue
        abs_path = ROOT / path
        if abs_path.is_file():
            normalized = normalize_markdown(abs_path.read_text(encoding="utf-8", errors="replace"))
            groups[hashlib.sha256(normalized.encode("utf-8")).hexdigest()].append(path)
    return format_duplicate_groups("duplicate Markdown content", groups)


def check_duplicate_yaml(files: list[Path]) -> list[str]:
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        if path.suffix.lower() not in YAML_EXTS:
            continue
        abs_path = ROOT / path
        if abs_path.is_file():
            normalized = "\n".join(
                line.rstrip() for line in abs_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()
            )
            groups[hashlib.sha256(normalized.encode("utf-8")).hexdigest()].append(path)
    return format_duplicate_groups("duplicate YAML content", groups)


def check_markdown_links(files: list[Path]) -> list[str]:
    issues = []
    for path in files:
        if path.suffix.lower() != ".md":
            continue
        abs_path = ROOT / path
        if not abs_path.is_file():
            continue
        text = abs_path.read_text(encoding="utf-8", errors="replace")
        for target in markdown_links(text):
            if should_skip_link(target):
                continue
            target_path = resolve_link(abs_path.parent, target)
            if not target_path.exists():
                issues.append(f"broken Markdown link in {path}: {target}")
    return issues


def check_stale_references(files: list[Path]) -> list[str]:
    issues = []
    for path in files:
        if path.suffix.lower() not in DOC_EXTS:
            continue
        abs_path = ROOT / path
        if not abs_path.is_file():
            continue
        text = abs_path.read_text(encoding="utf-8", errors="replace")
        for stale in STALE_REFERENCES:
            if stale in text:
                issues.append(f"stale reference in {path}: {stale}")
    return issues


def check_missing_referenced_files(files: list[Path]) -> list[str]:
    issues = []
    known = {str(path) for path in files}
    pattern = re.compile(r"`([^`]+\.(?:md|yaml|yml|json|csv|py|ino|h|cpp|txt))`")
    for path in files:
        if path.suffix.lower() not in DOC_EXTS:
            continue
        abs_path = ROOT / path
        if not abs_path.is_file():
            continue
        for raw in pattern.findall(abs_path.read_text(encoding="utf-8", errors="replace")):
            if raw.startswith(("http://", "https://") + PI_DEPLOYMENT_PREFIXES) or "*" in raw:
                continue
            candidate = Path(raw)
            if candidate.is_absolute():
                exists = candidate.exists()
            else:
                exists = (
                    (abs_path.parent / candidate).exists()
                    or (ROOT / candidate).exists()
                    or str(candidate) in known
                    or raw.startswith(GENERATED_REFERENCE_PREFIXES)
                )
            if not exists and "/" in raw:
                issues.append(f"missing referenced file in {path}: {raw}")
    return issues


def markdown_links(text: str) -> list[str]:
    inline = re.findall(r"(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text)
    refs = re.findall(r"^\[[^\]]+\]:\s+(\S+)", text, flags=re.MULTILINE)
    return inline + refs


def should_skip_link(target: str) -> bool:
    parsed = urlparse(target)
    return bool(parsed.scheme) or target.startswith("#") or target.startswith("mailto:")


def resolve_link(base: Path, target: str) -> Path:
    clean = unquote(target.split("#", 1)[0])
    if not clean:
        return base
    path = Path(clean)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_markdown(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def format_duplicate_groups(label: str, groups: dict[str, list[Path]]) -> list[str]:
    issues = []
    for paths in groups.values():
        if len(paths) > 1:
            issues.append(f"{label}: {', '.join(str(path) for path in sorted(paths))}")
    return issues


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    sys.exit(main())
