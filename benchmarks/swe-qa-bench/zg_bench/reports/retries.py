"""Render retry statistics from Harbor result files."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PROFILES = ("baseline", "zvec-grep")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read retry data from {path}: {error}") from error
    if not isinstance(value, dict):
        raise TypeError(f"retry data must be a JSON object: {path}")
    return value


def render_retry_summary(*, runs_dir: Path, trials: int, retries: int) -> str:
    """Return Markdown for one task's two profile retry outcomes."""
    lines = [
        "## Trial retries",
        "",
        (
            f"Each profile has {trials} independent trials. Failed trials receive at "
            f"most {retries} additional attempts within the job deadline; successful "
            "trials and low scores are not retried."
        ),
        (
            "Report metrics cover the final successful attempt of each trial and "
            "exclude failed-attempt overhead. Failed attempts are preserved in the "
            "Harbor evidence artifact under `.retry-history/`."
        ),
        "",
        "| Profile | Successful trials | Retry attempts | Final errors |",
        "|---|---:|---:|---:|",
    ]
    for profile in PROFILES:
        paths = list(runs_dir.glob(f"*-{profile}/result.json"))
        if len(paths) != 1:
            lines.append(f"| {profile} | unavailable | unavailable | unavailable |")
            continue
        result = _read_json(paths[0])
        try:
            stats = result["stats"]
            errors = int(stats["n_errored_trials"])
            completed = int(stats["n_completed_trials"])
            retry_count = int(stats["n_retries"])
            total = int(result["n_total_trials"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid retry data in {paths[0]}: {error}") from error
        lines.append(
            f"| {profile} | {completed - errors}/{total} | {retry_count} | {errors} |"
        )
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--append", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = _read_json(args.config)
        markdown = render_retry_summary(
            runs_dir=args.runs_dir,
            trials=int(config["trials_per_profile"]),
            retries=int(config["max_retries"]),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("a" if args.append else "w") as output:
            output.write(markdown)
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
