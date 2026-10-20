"""Numeric validation and reductions for normalized benchmark measurements."""

from __future__ import annotations

import math
from typing import Any, Sequence

from zg_bench.core.errors import SweQaError


def require_number(
    value: Any,
    *,
    label: str,
    integer: bool = False,
    allow_none: bool = False,
    positive: bool = False,
) -> int | float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SweQaError(f"{label} is missing or is not numeric")
    if integer and not isinstance(value, int):
        raise SweQaError(f"{label} must be an integer")
    if not math.isfinite(float(value)) or value < 0:
        raise SweQaError(f"{label} must be finite and non-negative")
    if positive and value == 0:
        raise SweQaError(f"{label} must be positive")
    return value


def same_metric(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)


def sum_or_none(values: Sequence[int | float | None]) -> int | float | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def mean_or_none(values: Sequence[int | float | None]) -> float | None:
    if not values or any(value is None for value in values):
        return None
    return sum(float(value) for value in values if value is not None) / len(values)
