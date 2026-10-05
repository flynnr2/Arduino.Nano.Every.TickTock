#!/usr/bin/env python3
"""Identify a clean installation source before any services are interrupted."""
from pathlib import Path
import subprocess
import sys


def source_revision(root, expected=None):
    root = Path(root).resolve()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args],
                                       stderr=subprocess.PIPE).decode().strip()

    try:
        toplevel = git("rev-parse", "--show-toplevel")
    except (FileNotFoundError, subprocess.CalledProcessError):
        if (root / '.git').exists():
            raise ValueError("Cannot verify the Git checkout; check Git availability and ownership") from None
        if expected:
            raise ValueError("A revision-pinned installation requires a Git checkout")
        return "Source copied without Git revision information."
    if Path(toplevel).resolve() != root:
        raise ValueError("The application must be the Git checkout root")
    revision = git("rev-parse", "--verify", "HEAD")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Refusing a dirty checkout; commit or preserve changes before installing")
    if expected:
        try:
            target = git("rev-parse", "--verify", "--end-of-options", expected + "^{commit}")
        except subprocess.CalledProcessError:
            raise ValueError("The requested source revision does not resolve to a commit") from None
        if target != revision:
            raise ValueError("Checkout HEAD does not match the requested source revision")
    return revision


if __name__ == "__main__":
    try:
        print(source_revision(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
    except (ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
