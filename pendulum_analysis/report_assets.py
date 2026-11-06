"""Embed report figures so HTML can be copied independently of its exports."""
import base64
from pathlib import Path


def png_data_uri(path: Path) -> str:
    """Read an emitted PNG; missing charts fail report generation visibly."""
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")
