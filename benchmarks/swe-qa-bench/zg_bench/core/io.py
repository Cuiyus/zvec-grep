"""JSON evidence loading shared by the evaluation pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zg_bench.core.errors import SweQaError


def load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SweQaError(f"could not read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise SweQaError(f"{label} must be a JSON object: {path}")
    return value
