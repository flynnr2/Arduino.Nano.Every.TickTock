#!/usr/bin/env python3
"""Check or align Markdown pipe tables, preserving text and alignment colons."""
import argparse
from pathlib import Path
import re
import subprocess


def cells(line):
    line = line.strip()
    parts = []
    start = slashes = 0
    for index, char in enumerate(line):
        if char == "|" and slashes % 2 == 0:
            parts.append(line[start:index])
            start = index + 1
        slashes = slashes + 1 if char == "\\" else 0
    parts.append(line[start:])
    if parts[0] == "":
        parts.pop(0)
    if parts and parts[-1] == "":
        parts.pop()
    return [part.strip() for part in parts]


def format_tables(text):
    lines = text.splitlines(keepends=True)
    output = []
    i = 0
    fence = None
    while i < len(lines):
        match = re.match(r"^\s*(`{3,}|~{3,})", lines[i])
        if match:
            marker = match[1]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            output.append(lines[i])
            i += 1
            continue
        separator = cells(lines[i + 1]) if i + 1 < len(lines) else []
        if (fence or "|" not in lines[i] or not separator
                or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)):
            output.append(lines[i])
            i += 1
            continue
        start = i
        indent = re.match(r"^[ \t]*", lines[i])[0]
        rows = [cells(lines[i]), separator]
        i += 2
        while i < len(lines) and "|" in lines[i] and not re.match(r"^\s*(`{3,}|~{3,})", lines[i]):
            rows.append(cells(lines[i]))
            i += 1
        if any(len(row) != len(separator) for row in rows):
            raise ValueError(f"inconsistent table column count at line {start + 1}")
        widths = [max(len(row[col]) for row in rows) for col in range(len(separator))]
        for index, row in enumerate(rows):
            if index == 1:
                row = [(":" if cell.startswith(":") else "")
                       + "-" * (width - int(cell.startswith(":")) - int(cell.endswith(":")))
                       + (":" if cell.endswith(":") else "")
                       for cell, width in zip(row, widths)]
            ending = "\n" if lines[start + index].endswith("\n") else ""
            output.append(indent + "| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths))
                          + " |" + ending)
    return "".join(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="align tables in place")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    paths = args.paths or [root / name for name in subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "*.md"],
        cwd=root).decode().split("\0") if name]
    failed = False
    for path in sorted(set(paths)):
        if not path.is_file():
            continue
        before = path.read_text()
        try:
            after = format_tables(before)
        except ValueError as error:
            print(f"{path}: {error}")
            failed = True
            continue
        if before != after:
            print(f"{'Aligned' if args.write else 'Unaligned tables:'} {path}")
            if args.write:
                path.write_text(after)
            else:
                failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
