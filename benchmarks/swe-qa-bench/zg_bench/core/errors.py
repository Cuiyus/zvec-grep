"""Failures surfaced by the SWE-QA evaluation pipeline."""


class SweQaError(RuntimeError):
    """A user-facing failure in the SWE-QA benchmark pipeline."""
