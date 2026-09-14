#!/usr/bin/env python3
"""The legacy bridge smoke cannot validate the standard zg install protocol."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def compatibility() -> dict:
    return {"reuse_eligible": False, "reason": "legacy_bridge_smoke_excluded_from_native_install_protocol"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-compatibility", action="store_true")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.github_output:
        with args.github_output.open("a") as output:
            output.write("reuse_eligible=false\n")
    print(json.dumps(compatibility()))
    return 0 if args.check_compatibility else 1


if __name__ == "__main__":
    raise SystemExit(main())
