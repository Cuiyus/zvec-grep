"""Reject benchmark artifacts that contain configured secret values."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path


def contains_secret(
    path: Path, secret: bytes, *, chunk_size: int = 1024 * 1024
) -> bool:
    """Scan without loading large artifacts; retain overlap across chunks."""
    overlap = max(len(secret) - 1, 0)
    tail = b""
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            window = tail + chunk
            if secret in window:
                return True
            tail = window[-overlap:] if overlap else b""
    return False


def artifact_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file():
            yield root
        elif root.exists():
            yield from (path for path in root.rglob("*") if path.is_file())


def find_secret_leaks(roots: Iterable[Path], secrets: Iterable[bytes]) -> list[Path]:
    files = tuple(artifact_files(roots))
    return [
        path
        for secret in set(secrets)
        if secret
        for path in files
        if contains_secret(path, secret)
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-env", action="append", default=[])
    parser.add_argument("--root", type=Path, action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    secrets = [
        value.encode()
        for name in args.secret_env
        if (value := os.environ.get(name, ""))
    ]
    try:
        leaks = find_secret_leaks(args.root, secrets)
    except OSError as error:
        print(f"error: could not scan artifacts: {error}", file=sys.stderr)
        return 1
    if leaks:
        for path in dict.fromkeys(leaks):
            print(f"secret material detected in artifact: {path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
