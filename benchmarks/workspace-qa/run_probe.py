#!/usr/bin/env python3
"""Check embedding and Qoder MCP connectivity without preparing benchmark tasks."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import run_task
import runner


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    root = parser.parse_args(argv).output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = {"schema_version": 1, "phase": "setup_only", "included_in_benchmark": False,
              "status": "failed", "model": runner.MODEL, "embedding_model": runner.EMBEDDING}
    try:
        for name in ("QODER_PERSONAL_ACCESS_TOKEN", "QWEN_API_KEY"):
            if not os.environ.get(name):
                raise RuntimeError(f"Required GitHub Actions secret is missing: {name}")
        run_task.embedding_preflight(root / "embedding-preflight.json")
        run_task.sdk_preflight(root / "sdk-preflight")
        result["status"] = "completed"
    except Exception as error:
        result.update(error_type=type(error).__name__, error=runner.redact(str(error)))
    finally:
        for relative, source_key, target_key in (
            ("embedding-preflight.json", "resolved_model", "resolved_embedding_model"),
            ("sdk-preflight/qoder/result.json", "model_identity", "model_identity"),
        ):
            try:
                diagnostic = json.loads((root / relative).read_text(encoding="utf-8"))
                if isinstance(diagnostic, dict) and source_key in diagnostic:
                    result[target_key] = diagnostic[source_key]
            except (OSError, ValueError):
                pass  # Keep the original failure when a diagnostic is incomplete.
        result["wall_seconds"] = round(time.monotonic() - started, 3)
        runner.write_json(root / "result.json", result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
