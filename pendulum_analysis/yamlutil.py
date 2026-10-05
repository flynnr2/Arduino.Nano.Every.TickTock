"""Small YAML compatibility layer for product configuration files.

PyYAML is used when it is installed. The fallback parser intentionally supports
only the subset used by this package's bundled profiles/defaults.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def load_yaml(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        return loaded or {}
    except ImportError:
        return _parse_subset(text)


def dump_yaml(data: Dict[str, Any]) -> str:
    try:
        import yaml  # type: ignore

        return str(yaml.safe_dump(data, sort_keys=True))
    except ImportError:
        return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _parse_subset(text: str) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    stack: List[tuple[int, Any]] = [(-1, root)]
    pending_key: List[tuple[int, Dict[str, Any], str]] = []

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if content.startswith("- "):
            item_text = content[2:].strip()
            if not isinstance(parent, list):
                if not pending_key:
                    raise ValueError("YAML list item without a list parent")
                _, mapping, key = pending_key.pop()
                values: List[Any] = []
                mapping[key] = values
                stack.append((indent - 2, values))
                parent = values
            parent.append(_parse_scalar(item_text))
            continue
        key, sep, value = content.partition(":")
        if not sep:
            raise ValueError(f"Unsupported YAML line: {raw_line}")
        key = key.strip()
        value = value.strip()
        if value:
            if not isinstance(parent, dict):
                raise ValueError(f"YAML key/value under non-mapping: {raw_line}")
            parent[key] = _parse_scalar(value)
            pending_key.clear()
        else:
            if not isinstance(parent, dict):
                raise ValueError(f"YAML mapping under non-mapping: {raw_line}")
            child: Dict[str, Any] = {}
            parent[key] = child
            pending_key.append((indent, parent, key))
            stack.append((indent, child))
    return root


def _parse_scalar(value: str) -> Any:
    if value in {"null", "None", "~"}:
        return None
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value.startswith("[") or value.startswith("{"):
        return json.loads(value)
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        if any(part in value for part in [".", "e", "E"]):
            return float(value)
        return int(value)
    except ValueError:
        return value
