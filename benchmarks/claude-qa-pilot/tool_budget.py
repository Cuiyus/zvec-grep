#!/usr/bin/env python3
"""Trusted, task-neutral Claude PreToolUse budget hook; never grants permission."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time

FINAL_MESSAGE = (
    "The investigation budget is exhausted. No further repository tools will run. "
    "Provide your final answer now using the evidence already available. "
    "Cite supporting source and explicitly state any remaining uncertainty. "
    "Do not make additional tool calls."
)


def decide(state: dict, event: dict, limit: int, research_seconds: int, now: float) -> dict:
    if event.get("hook_event_name") != "PreToolUse":
        raise ValueError("unexpected hook event")
    tool_id, name = event.get("tool_use_id"), event.get("tool_name")
    if not isinstance(tool_id, str) or not tool_id or not isinstance(name, str):
        raise ValueError("missing tool identity")
    elapsed = max(0.0, now - state["started_at_epoch"])
    calls = state["calls"]
    if tool_id not in calls:
        admitted = sum(call["admitted"] for call in calls.values())
        calls[tool_id] = {
            "name": name, "admitted": admitted < limit and elapsed < research_seconds,
            "elapsed_seconds": elapsed,
            "reason": "call_limit" if admitted >= limit else "time_limit" if elapsed >= research_seconds else "within_budget",
        }
    elif calls[tool_id]["name"] != name:
        raise ValueError("tool identity changed")
    call = calls[tool_id]
    admitted = sum(call["admitted"] for call in calls.values())
    output = {"hookEventName": "PreToolUse"}
    if not call["admitted"]:
        output.update(permissionDecision="deny", permissionDecisionReason=FINAL_MESSAGE,
                      additionalContext=FINAL_MESSAGE)
    elif admitted >= max(1, limit * 3 // 4) or elapsed >= research_seconds * 0.8:
        output["additionalContext"] = (
            f"Investigation budget: {admitted}/{limit} repository tool calls admitted; "
            f"{elapsed:.0f}/{research_seconds} seconds elapsed. "
            "Prioritize only material evidence gaps and finish your answer soon. "
            "Do not repeat searches merely to reconfirm established evidence."
        )
    # Omitting permissionDecision preserves all existing permission rules.
    return {"hookSpecificOutput": output}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--research-seconds", type=int, required=True)
    args = parser.parse_args()
    if args.limit < 1 or args.research_seconds < 1:
        parser.error("budget values must be positive")
    try:
        event = json.load(sys.stdin)
        # The controller creates this private state before starting Claude.
        # One lock covers read/decision/write, including concurrent tool batches.
        with args.state.open("r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            state = json.load(handle)
            output = decide(state, event, args.limit, args.research_seconds, time.time())
            handle.seek(0)
            json.dump(state, handle)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
        print(json.dumps(output), flush=True)
        return 0
    except Exception as error:
        # Exit 2 blocks this call on the pinned Claude version. The controller
        # separately classifies this marker as infrastructure failure.
        print("BUDGET_HOOK_INFRASTRUCTURE_ERROR: " + type(error).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
