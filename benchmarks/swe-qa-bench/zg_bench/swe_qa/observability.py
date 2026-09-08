"""Offline, conservative QA trace measurements; never runs an agent or judge.

Provider usage is kept in its original counter convention. Evidence matching
uses source-grounded, exact visible text and is a lower bound: paraphrases and
spans split across observations are not credited. Unknown data stays null.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import shlex
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

PROFILES = ("baseline", "zvec-grep")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(part) for part in value)
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return ""


def validate_case(case: dict[str, Any]) -> dict[str, Any]:
    """Validate versioned source evidence, not the quality of its annotation."""
    if not isinstance(case.get("case_id"), str) or not case["case_id"]:
        raise ValueError("case_id is required")
    if not isinstance(case.get("question"), str) or not case["question"].strip():
        raise ValueError("question is required")
    repo = case.get("repo")
    if not isinstance(repo, dict) or not all(repo.get(k) for k in ("url", "commit")):
        raise ValueError("repo.url and repo.commit are required")
    if not isinstance(repo["commit"], str) or not re.fullmatch(r"[0-9a-fA-F]{40}", repo["commit"]):
        raise ValueError("repo.commit must be a pinned 40-character Git commit")
    evidence = case.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("case.evidence must contain source evidence")
    ids: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("each evidence entry must be an object")
        evidence_id, path, text = item.get("id"), item.get("path"), item.get("text")
        if not isinstance(evidence_id, str) or not evidence_id or evidence_id in ids:
            raise ValueError("evidence IDs must be unique nonempty strings")
        if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
            raise ValueError(f"evidence {evidence_id}: path must be repository-relative")
        start, end = item.get("start_line"), item.get("end_line")
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            raise ValueError(f"evidence {evidence_id}: invalid line range")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"evidence {evidence_id}: source text is required")
        if len(text.splitlines()) != end - start + 1:
            raise ValueError(f"evidence {evidence_id}: text and line range disagree")
        if hashlib.sha256(text.encode()).hexdigest() != item.get("sha256"):
            raise ValueError(f"evidence {evidence_id}: text SHA-256 mismatch")
        ids.add(evidence_id)
    sets = case.get("sufficient_sets")
    if not isinstance(sets, list) or not sets:
        raise ValueError("sufficient_sets must be a nonempty OR-of-AND list")
    for required in sets:
        if not isinstance(required, list) or not required or any(x not in ids for x in required):
            raise ValueError("sufficient_sets must reference known evidence IDs")
    return case


def _canonical(text: str) -> str:
    # Common read tools prefix line numbers; preserve token order and line breaks.
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*\d+(?:[ \t]*[|:>→-][ \t]?|\t)", "", line)
        lines.append(" ".join(line.split()))
    return "\n".join(lines).strip()


def visible_evidence(text: str, arguments: Any, case: dict[str, Any]) -> list[str]:
    """Filename hits alone and hidden full chunks are never evidence hits."""
    context = text + "\n" + json.dumps(arguments, ensure_ascii=False)
    visible = _canonical(text)
    return [
        item["id"] for item in case["evidence"]
        if item["path"] in context and _canonical(item["text"]) in visible
    ]


def classify_call(name: str, arguments: Any) -> dict[str, Any]:
    """Classify only recognized calls; shell counts are explicit lower bounds."""
    args = arguments if isinstance(arguments, dict) else {}
    lower = name.lower()
    base = lower.split("__")[-1]
    search = {"grep", "glob", "search", "search_code", "code_search", "context", "zvec_grep", "zg"}
    reads = {"read", "read_file", "readfile", "list", "list_directory", "ls"}
    if base in search or ("zvec" in lower and "grep" in lower):
        queries = args.get("queries")
        routes = args.get("routes")
        count = len(queries) if isinstance(queries, list) else 0
        count += len(routes) if isinstance(routes, list) else 0
        if args.get("query") or args.get("pattern"):
            count += 1
        # Native grep/glob have a single invocation even if arguments are absent.
        if not count and base in {"grep", "glob"}:
            count = 1
        return {"category": "search", "logical_queries": count or None, "query_count_complete": bool(count)}
    if base in reads:
        return {"category": "read", "logical_queries": 0, "query_count_complete": True}
    if base not in {"bash", "shell", "terminal", "execute", "exec_command", "run_command", "command"}:
        return {"category": "unknown", "logical_queries": None, "query_count_complete": False}
    command = args.get("command") or args.get("cmd") or ""
    if not isinstance(command, str):
        return {"category": "unknown", "logical_queries": None, "query_count_complete": False}
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return {"category": "unknown", "logical_queries": None, "query_count_complete": False}
    categories: set[str] = set()
    queries = 0
    complete = not any(x in command for x in ("$(", "`", " for ", "while ", " xargs "))
    at_start = True
    for token_index, token in enumerate(tokens):
        if token and all(c in ";&|()\n" for c in token):
            at_start = True
            continue
        if not at_start:
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            continue
        at_start = False
        executable = Path(token).name
        if executable in {"rg", "grep", "ag", "ack", "zg"}:
            following = []
            for word in tokens[token_index + 1:]:
                if word and all(c in ";&|()\n" for c in word):
                    break
                following.append(word)
            if executable == "rg" and "--files" in following:
                categories.add("read")
            else:
                categories.add("search")
                queries += 1
            # zg may batch queries or consume them from stdin.
            if executable == "zg":
                complete = False
        elif executable in {"cat", "sed", "head", "tail", "nl", "less", "more", "ls", "find", "wc"}:
            categories.add("read")
        elif executable in {"cd", "pwd", "echo", "printf", "true"}:
            categories.add("other")
        else:
            categories.add("unknown")
            complete = False
    meaningful = categories - {"other"}
    category = next(iter(meaningful)) if len(meaningful) == 1 else "mixed" if meaningful else "other"
    return {"category": category, "logical_queries": queries if complete or queries else None, "query_count_complete": complete}


def native_tool_states(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Recover native completion/error states that ATIF may omit entirely."""
    states: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("type") != "tool_use":
            continue
        part = event.get("part") or {}
        state = part.get("state") or {}
        call_id = part.get("callID")
        if not isinstance(call_id, str) or not isinstance(state, dict):
            continue
        terminal = state.get("status") in {"completed", "error"}
        if call_id in states and states[call_id].get("status") in {"completed", "error"} and not terminal:
            continue
        error = state.get("error") if isinstance(state.get("error"), str) else None
        unavailable = bool(error and re.search(r"(?:unavailable|unknown) tool|tool .+ (?:not found|does not exist)", error, re.IGNORECASE))
        states[call_id] = {"status": state.get("status"), "error": error,
                           "failure_kind": "unavailable_tool" if unavailable else "tool_error" if state.get("status") == "error" else None,
                           "execution_confirmed": True if state.get("status") == "completed" else False if unavailable else None,
                           "function_name": part.get("tool"), "source": "opencode_native_tool_state"}
    return states


def _read_native_events(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.is_file():
        return [], []
    events, errors = [], []
    for index, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except json.JSONDecodeError:
            errors.append(f"unparsed native stream line {index}")
    return events, errors


def analyze_trajectory(trajectory: dict[str, Any], case: dict[str, Any], *,
                       native_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    steps = trajectory.get("steps")
    if not isinstance(steps, list):
        raise ValueError("trajectory.steps must be a list")
    calls: dict[str, dict[str, Any]] = {}
    native = native_tool_states(native_events or [])
    records = []
    visible: set[str] = set()
    seen_text: set[str] = set()
    seen_lines: set[str] = set()
    model_count = 0
    model_count_missing = 0
    prefix_input = 0
    prefix_input_complete = True
    first_sufficient = None
    observed_call_ids: set[str] = set()
    returned_bytes = repeated_bytes = repeated_line_bytes = 0
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            continue
        step_id = step.get("step_id", index)
        if step.get("source") == "agent":
            count = _number(step.get("llm_call_count"))
            if count is None:
                model_count_missing += 1
            else:
                model_count += count
            usage = step.get("metrics") or {}
            prompt = _number(usage.get("prompt_tokens")) if isinstance(usage, dict) else None
            if prompt is None:
                prefix_input_complete = False
            else:
                prefix_input += prompt
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("tool_call_id") or f"anonymous-{index}-{len(calls)}")
            if call_id in calls:
                continue
            record = {"tool_call_id": call_id, "step_id": step_id, "function_name": call.get("function_name", "unknown"), "arguments": call.get("arguments", {})}
            record.update(classify_call(record["function_name"], record["arguments"]))
            state = native.get(call_id, {})
            record.update({"observation_count": 0, "returned_text_bytes": 0, "evidence_ids": [],
                           "is_error": True if state.get("status") == "error" else False if state.get("status") == "completed" else None,
                           "native_tool_state": state or None,
                           "execution_confirmed": state.get("execution_confirmed"),
                           "execution_error": state.get("error"), "failure_kind": state.get("failure_kind")})
            calls[call_id] = record
            records.append(record)
        observation = step.get("observation") or {}
        for result in observation.get("results", []) if isinstance(observation, dict) else []:
            if not isinstance(result, dict):
                continue
            call_id = str(result.get("source_call_id", ""))
            record = calls.get(call_id)
            content = _text(result.get("content"))
            # Serialize text only: image or arbitrary JSON sizes are unavailable.
            size = len(content.encode("utf-8"))
            returned_bytes += size
            digest = hashlib.sha256(content.encode()).hexdigest()
            if content and digest in seen_text:
                repeated_bytes += size
            if content:
                seen_text.add(digest)
            # Report a separate lower bound for partial overlapping responses.
            # Ignore short boilerplate such as braces; this is content overlap,
            # not a claim that two reads addressed the same source location.
            current_lines: set[str] = set()
            for line in content.splitlines():
                canonical_line = _canonical(line)
                if len(canonical_line) >= 20:
                    if canonical_line in seen_lines:
                        repeated_line_bytes += len(line.encode("utf-8"))
                    current_lines.add(canonical_line)
            seen_lines.update(current_lines)
            matched = visible_evidence(content, record["arguments"] if record else {}, case)
            visible.update(matched)
            if record:
                observed_call_ids.add(call_id)
                record["observation_count"] += 1
                record["returned_text_bytes"] += size
                record["evidence_ids"] = sorted(set(record["evidence_ids"]) | set(matched))
                explicit_error = (result.get("extra") or {}).get("is_error")
                if explicit_error is True:
                    record["is_error"] = True
                elif explicit_error is False and record["is_error"] is None:
                    record["is_error"] = False
            if first_sufficient is None and any(set(required) <= visible for required in case["sufficient_sets"]):
                first_sufficient = {
                    "step_id": step_id,
                    "tool_calls_issued": len(calls),
                    "observed_tool_calls": len(observed_call_ids),
                    "model_requests": model_count if not model_count_missing else None,
                    "step_prompt_tokens_through_request": prefix_input if prefix_input_complete else None,
                    "evidence_ids": sorted(visible),
                }
    categories = {name: sum(r["category"] == name for r in records) for name in ("search", "read", "other", "mixed", "unknown")}
    query_complete = all(r["query_count_complete"] for r in records)
    search_records = [r for r in records if r["category"] == "search"]
    zg_records = [r for r in search_records if "zvec" in r["function_name"].lower()]
    execution_count_complete = query_complete and all(not r["logical_queries"] or r["execution_confirmed"] is not None for r in records)
    def confirmed_count(selected: list[dict[str, Any]]) -> int | None:
        return sum(r["execution_confirmed"] is True for r in selected) if all(r["execution_confirmed"] is not None for r in selected) else None
    return {
        "tool_calls": len(calls),
        "model_requests": model_count if not model_count_missing else None,
        "known_model_requests_lower_bound": model_count,
        "agent_steps_missing_request_count": model_count_missing,
        "calls_by_category": categories,
        "logical_queries": sum(r["logical_queries"] or 0 for r in records) if query_complete else None,
        "logical_queries_scope": "Attempted logical queries, including rejected calls; not successful backend executions.",
        "attempted_logical_queries": sum(r["logical_queries"] or 0 for r in records) if query_complete else None,
        "executed_logical_queries": sum(r["logical_queries"] or 0 for r in records if r["execution_confirmed"] is True) if execution_count_complete else None,
        "confirmed_executed_logical_queries_lower_bound": sum(r["logical_queries"] or 0 for r in records if r["execution_confirmed"] is True),
        "search_calls_attempted": len(search_records), "search_calls_executed": confirmed_count(search_records),
        "zg_tool_calls_attempted": len(zg_records), "zg_tool_calls_executed": confirmed_count(zg_records),
        "tool_errors": sum(r["is_error"] is True for r in records),
        "calls_missing_execution_status": sum(r["execution_confirmed"] is None for r in records),
        "unavailable_tool_errors": sum(r["failure_kind"] == "unavailable_tool" for r in records),
        "known_logical_queries_lower_bound": sum(r["logical_queries"] or 0 for r in records),
        "query_count_complete": query_complete,
        "returned_text_bytes": returned_bytes,
        "returned_text_bytes_scope": "ATIF observation text only; native errors omitted by ATIF are counted separately.",
        "native_only_error_text_bytes": sum(len(r["execution_error"].encode("utf-8")) for r in records if r["execution_error"] and not r["observation_count"]),
        "returned_text_tokens": None,
        "returned_text_tokenizer": None,
        "exact_repeated_observation_bytes": repeated_bytes,
        "repeated_nontrivial_line_bytes_lower_bound": repeated_line_bytes,
        "calls_without_observations": len(calls) - len(observed_call_ids),
        "observed_evidence_ids": sorted(visible),
        "known_evidence_coverage": len(visible) / len(case["evidence"]),
        "evidence_sufficient": first_sufficient is not None,
        "first_sufficient_evidence": first_sufficient,
        "calls": records,
    }


def _trace_diagnostics(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"available": False, "search_count": None}
    events, errors = [], []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except json.JSONDecodeError:
            errors.append(f"invalid JSONL line {index}")
    searches = [e for e in events if e.get("event") == "search"]
    return {
        "available": True, "path": str(path), "search_count": len(searches), "parse_errors": errors,
        "successful_search_count": sum(e.get("status") == "success" for e in searches),
        "failed_search_count": sum(e.get("status") == "error" for e in searches),
        "searches": [{k: e.get(k) for k in ("sequence", "status", "duration_ms", "request", "text_bytes", "text_sha256", "error")} for e in searches],
        "integrity_events": [e for e in events if e.get("event") in {"preflight", "integrity", "end"}],
        "evidence_policy": "Hidden raw results and uncorrelated sidecar text are not counted as agent-visible evidence.",
    }


def _task_matches(result: dict[str, Any], directory: Path, case_id: str) -> bool:
    names = {value for value in (result.get("task_name"), result.get("task")) if isinstance(value, str)}
    task_id = result.get("task_id")
    if isinstance(task_id, dict):
        names.update([task_id.get("name"), Path(str(task_id.get("path", ""))).name])
    slug = case_id.replace(":", "-")
    return case_id in names or slug in names or directory.name == slug or directory.name.startswith(slug + "__")


def _wall_seconds(result: dict[str, Any]) -> float | None:
    direct = _number(result.get("wall_seconds"))
    if direct is not None:
        return direct
    timing = result.get("agent_execution") or {}
    try:
        start = datetime.fromisoformat(timing["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(timing["finished_at"].replace("Z", "+00:00"))
        return _number((end - start).total_seconds())
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _summary(values: list[Any]) -> dict[str, Any]:
    known = [x for x in values if _number(x) is not None]
    return {"n": len(known), "missing": len(values) - len(known), "values": values,
            "mean": statistics.mean(known) if known else None,
            "sd": statistics.stdev(known) if len(known) > 1 else None,
            "min": min(known) if known else None, "max": max(known) if known else None}


def _repeat_comparison(baseline: list[Any], zg: list[Any]) -> dict[str, Any]:
    result = {"reduction_pct": None, "repeat_bootstrap_95_interval_pct": None,
              "scope": "Independent-repeat sampling for this fixed case only; not a cross-task population interval or a reliability guarantee."}
    if not baseline or not zg or any(_number(x) is None for x in baseline + zg) or statistics.mean(baseline) == 0:
        return result
    result["reduction_pct"] = 100 * (1 - statistics.mean(zg) / statistics.mean(baseline))
    if len(baseline) < 2 or len(zg) < 2:
        return result
    rng = random.Random(0)
    samples = []
    for _ in range(5000):
        base = statistics.mean(rng.choices(baseline, k=len(baseline)))
        treatment = statistics.mean(rng.choices(zg, k=len(zg)))
        if base:
            samples.append(100 * (1 - treatment / base))
    if samples:
        samples.sort()
        result["repeat_bootstrap_95_interval_pct"] = [samples[int((len(samples) - 1) * p)] for p in (0.025, 0.975)]
    return result


def analyze_runs(*, runs_dir: Path, case: dict[str, Any], expected_trials: int = 5,
                 profile: str = "all", pair: dict[str, Any] | None = None,
                 judged_report: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_case(case)
    if not runs_dir.is_dir() or type(expected_trials) is not int or expected_trials < 1:
        raise ValueError("runs_dir must exist and expected_trials must be positive")
    if profile not in (*PROFILES, "all"):
        raise ValueError("invalid profile")
    plan = _json(runs_dir / "plan.json") if (runs_dir / "plan.json").is_file() else None
    manifest = _json(runs_dir / "manifest.json") if (runs_dir / "manifest.json").is_file() else None
    if plan and plan.get("case_id") not in (None, case["case_id"]):
        raise ValueError("plan.json case_id differs from the requested case")
    selected = PROFILES if profile == "all" else (profile,)
    profiles: dict[str, Any] = {}
    for label in selected:
        job_trials: dict[Path, list[tuple[Path, dict[str, Any]]]] = {}
        if plan:
            for planned in plan.get("trials", []):
                if planned.get("profile") != label:
                    continue
                relative = Path(str(planned.get("trajectory_path") or ""))
                if not str(relative) or relative.is_absolute() or ".." in relative.parts or relative.name != "trajectory.json":
                    raise ValueError("plan trajectory_path must be a safe relative path ending in trajectory.json")
                trajectory = runs_dir / relative
                job_trials.setdefault(runs_dir, []).append((trajectory.parent.parent, planned))
        else:
            jobs = {p for p in runs_dir.rglob("*") if p.is_dir() and p.name.endswith("-" + label)}
            if runs_dir.name.endswith("-" + label):
                jobs.add(runs_dir)
            job_trials = {job: [(p, {}) for p in sorted(job.iterdir()) if p.is_dir()] for job in jobs}
        trials = []
        for job, entries in sorted(job_trials.items()):
            for trial_dir, planned in entries:
                result_path = trial_dir / "result.json"
                trajectory_path = runs_dir / planned["trajectory_path"] if planned else trial_dir / "agent" / "trajectory.json"
                artifact_present = result_path.is_file() or trajectory_path.is_file()
                if not artifact_present and not planned:
                    continue
                errors: list[str] = []
                try:
                    result = _json(result_path) if result_path.is_file() else {}
                except (ValueError, OSError) as error:
                    result = {}
                    errors.append(str(error))
                if not planned and not _task_matches(result, trial_dir, case["case_id"]):
                    continue
                context = result.get("agent_result") or {}
                metadata = context.get("metadata") or {}
                unavailable = metadata.get("token_usage_available") is False
                status = "failed" if result.get("exception_info") else "completed" if result.get("finished_at") else "unfinished"
                if isinstance(result.get("status"), str):
                    status = result["status"]
                if result.get("source_unchanged") is False or result.get("index_unchanged") is False:
                    status = "integrity_failure"
                if not artifact_present:
                    status = "missing"
                values: dict[str, Any] = {}
                final_metrics = result.get("final_metrics") or {}
                try:
                    if trajectory_path.is_file():
                        trajectory = _json(trajectory_path)
                        native_events, native_errors = _read_native_events(trial_dir / "agent" / "opencode.txt")
                        errors.extend(native_errors)
                        values = analyze_trajectory(trajectory, case, native_events=native_events)
                        final_metrics = trajectory.get("final_metrics") or final_metrics
                    else:
                        errors.append("trajectory unavailable")
                except (ValueError, OSError) as error:
                    errors.append(str(error))
                if (final_metrics.get("extra") or {}).get("token_usage_available") is False:
                    unavailable = True
                def counter(key: str, fallback: str) -> Any:
                    if unavailable:
                        return None
                    value = context.get(key)
                    return _number(value if value is not None else final_metrics.get(fallback))
                zg_trace = _trace_diagnostics(trial_dir / "agent" / "zg-trace.jsonl")
                trials.append({
                    "trial_name": str(result.get("trial_name") or trial_dir.name), "job_name": job.name,
                    "path": str(trial_dir), "status": status, "errors": errors,
                    "planned_trial": planned or None,
                    "started_at": result.get("started_at") or (result.get("agent_execution") or {}).get("started_at"),
                    "finished_at": result.get("finished_at"),
                    "exception_info": result.get("exception_info") or result.get("error"),
                    "agent_info": result.get("agent_info") or ({"name": manifest.get("agent"), "version": manifest.get("agent_version"), "model_name": manifest.get("model")} if manifest else None),
                    "source_unchanged": result.get("source_unchanged"), "index_unchanged": result.get("index_unchanged"),
                    "returncode": result.get("returncode"), "conversion_error": result.get("conversion_error"),
                    "input_tokens": counter("n_input_tokens", "total_prompt_tokens"),
                    "output_tokens": counter("n_output_tokens", "total_completion_tokens"),
                    "cached_tokens": counter("n_cache_tokens", "total_cached_tokens"),
                    "agent_wall_seconds": _wall_seconds(result),
                    "usage_convention": "Adapter final prompt counter, unchanged. Harbor OpenCode already sums native input + cache.read; cache.read is not added a second time. Native cache.write remains separate.",
                    "original_verifier": result.get("verifier_result"), "original_judge": None,
                    "trajectory": values or None, "zg_trace": zg_trace,
                    "successful_zg_backend_searches": zg_trace.get("successful_search_count") if label == "zvec-grep" else None,
                    "zero_successful_zg_search_observed": zg_trace.get("successful_search_count") == 0 if label == "zvec-grep" and zg_trace.get("available") and not zg_trace.get("parse_errors") else None,
                })
        trials.sort(key=lambda row: (str(row.get("started_at") or ""), row["trial_name"]))
        if pair:
            for known in pair.get("profiles", {}).get(label, {}).get("trials", []):
                row = next((t for t in trials if t["trial_name"] == known.get("trial_name")), None)
                if row:
                    row["collected_pair_metrics"] = {k: known.get(k) for k in ("input_tokens", "output_tokens", "tool_calls", "agent_wall_seconds")}
        if judged_report:
            judged_case = next((c for c in judged_report.get("cases", []) if c.get("task_id") == case["case_id"]), {})
            for known in judged_case.get("profiles", {}).get(label, {}).get("trials", []):
                row = next((t for t in trials if t["trial_name"] == known.get("trial_name")), None)
                if row:
                    row["original_judge"] = known.get("judge")
            # The read-only source-grounded judge uses one explicit trial ledger.
            if judged_report.get("case_id") in (None, case["case_id"]):
                for known in judged_report.get("trials", []):
                    if known.get("profile") != label:
                        continue
                    row = next((t for t in trials if known.get("trial_id") in {t["trial_name"], (t.get("planned_trial") or {}).get("trial_id")}), None)
                    if row:
                        row["original_judge"] = known
        actual = sum(t["status"] != "missing" for t in trials)
        for index in range(len(trials), expected_trials):
            trials.append({"trial_name": f"missing-planned-trial-{index + 1}", "status": "missing", "input_tokens": None, "output_tokens": None, "trajectory": None, "original_judge": None})
        metrics = {}
        for metric in ("input_tokens", "output_tokens", "agent_wall_seconds", "tool_calls", "model_requests", "attempted_logical_queries", "executed_logical_queries", "tool_errors", "unavailable_tool_errors", "returned_text_bytes", "native_only_error_text_bytes", "exact_repeated_observation_bytes", "repeated_nontrivial_line_bytes_lower_bound"):
            metrics[metric] = _summary([t.get(metric) if metric in {"input_tokens", "output_tokens", "agent_wall_seconds"} else (t.get("trajectory") or {}).get(metric) for t in trials])
        evidence_values = [(t.get("trajectory") or {}).get("evidence_sufficient") for t in trials]
        configurations = set()
        for trial in trials:
            info = trial.get("agent_info") or {}
            model_info = info.get("model_info") or {}
            model = model_info.get("name") or info.get("model_name")
            if info.get("name") and model:
                configurations.add((info["name"], model))
        statuses = sorted({"completed", "failed", "unfinished", "missing"} | {t["status"] for t in trials})
        profiles[label] = {"actual_trials": actual, "expected_trials": expected_trials,
                           "trial_count_matches_plan": actual == expected_trials and len(trials) == expected_trials,
                           "agent_model_configurations": sorted(configurations),
                           "configuration_consistent": len(configurations) <= 1,
                           "successful_zg_backend_searches": _summary([t.get("successful_zg_backend_searches") for t in trials]),
                           "trials_with_zero_successful_zg_search_observed": [t["trial_name"] for t in trials if t.get("zero_successful_zg_search_observed") is True],
                           "status_counts": {s: sum(t["status"] == s for t in trials) for s in statuses},
                           "metrics": metrics, "evidence_sufficient": {"successes": sum(v is True for v in evidence_values), "observed": sum(v is not None for v in evidence_values), "planned_or_observed": len(trials)}, "trials": trials}
    comparison = {}
    if all(p in profiles for p in PROFILES):
        configurations = {tuple(config) for p in PROFILES for config in profiles[p]["agent_model_configurations"]}
        for metric in ("input_tokens", "tool_calls"):
            comparison[metric] = _repeat_comparison(*(profiles[p]["metrics"][metric]["values"] for p in PROFILES))
            if len(configurations) > 1:
                comparison[metric].update({"reduction_pct": None, "repeat_bootstrap_95_interval_pct": None,
                                           "unavailable_reason": "Multiple agent/model configurations detected; analyze each configuration separately."})
    return {"schema_version": 1, "case_id": case["case_id"], "repo": case["repo"], "plan": plan, "manifest": manifest,
            "source_judge_summary": judged_report.get("summary") if judged_report else None,
            "scope": "read-only QA with index content frozen during evaluation; single-case repeated runs",
            "index_preparation": manifest.get("index_preparation") if manifest else None,
            "evidence_matching": "Conservative exact source-text visibility; each evidence span must appear in one observation with its path in output or call arguments. Filename-only, hidden raw chunks, paraphrases and partial spans do not qualify. Evidence is accumulated across observations; gold sufficient_sets are OR-of-AND.",
            "limitations": ["No final-quality or non-inferiority claim is derived from source visibility or completion rewards.", "Five repeats describe this case; they cannot establish cross-task generalization or rare-failure reliability.", "Tool-return tokens are N/A without a declared tokenizer; UTF-8 bytes are not model input tokens.", "Tool calls and attempted queries include rejected calls. Executed queries require native confirmation; unknown-tool errors count zero executions. Other ambiguous errors remain N/A.", "Native tool error details can be absent from ATIF; native-only error text bytes are reported separately from ATIF observation bytes.", "Atomic search counts are conservative; unknown shell programs are never assumed to contain zero searches.", "Bootstrap intervals assume independent repeat sampling; no pairing or randomized schedule is inferred from directory order."],
            "profiles": profiles, "comparison": comparison,
            "quality_gate": {"status": "not_evaluated", "reason": "Requires prespecified QA rubric, original judge review and non-inferiority margin; observability does not replace scoring."}}


def render_markdown(report: dict[str, Any]) -> str:
    def cell(value: Any) -> str:
        return "N/A" if value is None else f"{value:.2f}" if isinstance(value, float) else str(value)
    lines = [f"# Read-only QA observability: {report['case_id']}", "", report["scope"], "", "All planned and observed trials are retained. Quality scoring is separate.", "", "| Profile | Trial | Status | Input tokens | Tool calls | Model requests | Sufficient evidence | First sufficient step | Answer assessment | Successful ZG backend searches |", "|---|---|---|---:|---:|---:|---|---|---|---:|"]
    for profile, group in report["profiles"].items():
        for trial in group["trials"]:
            trace = trial.get("trajectory") or {}
            milestone = trace.get("first_sufficient_evidence") or {}
            values = (profile, trial["trial_name"], trial["status"], trial.get("input_tokens"), trace.get("tool_calls"), trace.get("model_requests"), trace.get("evidence_sufficient"), milestone.get("step_id"), (trial.get("original_judge") or {}).get("quality"), trial.get("successful_zg_backend_searches"))
            lines.append("| " + " | ".join(cell(v).replace("|", "\\|") for v in values) + " |")
    lines.extend(["", "| Profile | Metric | Known / missing | Mean | SD | Min | Max |", "|---|---|---|---:|---:|---:|---:|"])
    for profile, group in report["profiles"].items():
        for metric, summary in group["metrics"].items():
            values = (profile, metric, f"{summary['n']} / {summary['missing']}", *(summary[k] for k in ("mean", "sd", "min", "max")))
            lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    lines.extend(["", "Repeat uncertainty (fixed case only):", ""])
    for metric, comparison in report["comparison"].items():
        lines.append(f"- {metric}: reduction {cell(comparison['reduction_pct'])}%; descriptive repeat bootstrap 95% interval {comparison['repeat_bootstrap_95_interval_pct'] or 'N/A'}.")
    preparation = report.get("index_preparation") or {}
    if preparation:
        lines.extend(["", f"Index preparation: mode={preparation.get('mode', 'unknown')}, status={preparation.get('status', 'unknown')}, elapsed={cell(preparation.get('wall_seconds'))} seconds; reported separately from QA input tokens and tool calls."])
    lines.extend(["", "Native tool execution diagnostics:", ""])
    for profile, group in report["profiles"].items():
        for trial in group["trials"]:
            for call in (trial.get("trajectory") or {}).get("calls", []):
                if call.get("execution_error"):
                    lines.append(f"- {trial['trial_name']}: `{call['function_name']}` failed ({call.get('failure_kind')}); attempted query remains in cost counters, execution confirmed={call.get('execution_confirmed')}.")
            if trial.get("zero_successful_zg_search_observed") is True:
                lines.append(f"- {trial['trial_name']}: zero successful ZG backend searches observed; retained in the planned treatment arm.")
    lines.extend(["", "Evidence matching: " + report["evidence_matching"], "", "Limitations:", ""])
    lines.extend("- " + value for value in report["limitations"])
    lines.extend(["", "Quality gate: not evaluated. Original QA judge results, when supplied, are retained per trial in JSON.", ""])
    return "\n".join(lines)


def _evidence_result(ids: set[str], case: dict[str, Any]) -> dict[str, Any]:
    return {"evidence_ids": sorted(ids), "known_evidence_unit_coverage": len(ids) / len(case["evidence"]),
            "sufficient": any(set(required) <= ids for required in case["sufficient_sets"])}


def score_retrieval_event(event: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Measure the public short preview and raw returned items separately."""
    row: dict[str, Any] = {"run_id": event.get("run_id"), "sequence": event.get("sequence"),
                          "mode": event.get("mode"), "repetition": event.get("repetition"),
                          "status": event.get("status", "unknown"), "request": event.get("request"),
                          "duration_ms": _number(event.get("duration_ms")),
                          "context_duration_ms": _number(event.get("context_duration_ms")),
                          "error": event.get("error"), "visible_evidence": None,
                          "raw_item_evidence_diagnostic": None, "result_ids": None,
                          "visible_text_bytes": None, "visible_text_lines": None,
                          "visible_text_sha256": None, "first_visible_evidence_rank": None,
                          "first_sufficient_visible_rank": None, "visible_evidence_ranks": {},
                          "source_identity": event.get("source_identity"), "index_identity": event.get("index_identity")}
    if row["status"] != "success":
        return row
    text = event.get("text")
    if isinstance(text, str):
        visible = set(visible_evidence(text, {}, case))
        row.update({"visible_text_bytes": len(text.encode("utf-8")), "visible_text_lines": len(text.splitlines()),
                    "visible_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "visible_evidence": _evidence_result(visible, case)})
        # Only ranked public text blocks can establish visible-evidence rank.
        # Raw entity rank never substitutes for a truncated-away source span.
        headers = list(re.finditer(r"(?m)^#(\d+)\b[^\n]*matchedBy=[^\n]*$", text))
        ranks = {}
        for index, header in enumerate(headers):
            block = text[header.start():headers[index + 1].start() if index + 1 < len(headers) else len(text)]
            rank = int(header.group(1))
            for evidence_id in visible_evidence(block, {}, case):
                ranks[evidence_id] = min(rank, ranks.get(evidence_id, rank))
        row["visible_evidence_ranks"] = ranks
        if visible and visible <= ranks.keys():
            row["first_visible_evidence_rank"] = min(ranks.values())
        complete_sets = [max(ranks[e] for e in required) for required in case["sufficient_sets"] if set(required) <= ranks.keys()]
        row["first_sufficient_visible_rank"] = min(complete_sets) if complete_sets else None
    result = event.get("result") or {}
    items = result.get("items")
    if isinstance(items, list):
        raw_ids: set[str] = set()
        result_ids = []
        for item in items:
            if not isinstance(item, dict):
                continue
            file = item.get("file") or {}
            path = file.get("relativePath")
            content = item.get("content")
            if isinstance(path, str) and isinstance(content, str) and item.get("contentRole") != "outline":
                raw_ids.update(visible_evidence(content, {"path": path}, case))
            identity = item.get("entityId")
            if not isinstance(identity, str) or not identity:
                identity = json.dumps({"path": path, "range": item.get("range")}, sort_keys=True) if path and item.get("range") else None
            result_ids.append(identity)
        row["result_ids"] = result_ids if all(identity is not None for identity in result_ids) else None
        row["raw_item_evidence_diagnostic"] = _evidence_result(raw_ids, case)
    row["diagnostics"] = result.get("diagnostics")
    row["timings"] = result.get("timings")
    return row


def _repeat_order_agreement(trials: list[dict[str, Any]]) -> dict[str, Any]:
    observed = [row for row in trials if row["status"] == "success" and row.get("result_ids") is not None]
    identities = {json.dumps({k: row.get(k) for k in ("request", "source_identity", "index_identity")}, sort_keys=True) for row in observed}
    sequences = [row["result_ids"] for row in observed]
    output = {"observed_successes": len(sequences), "pair_count": 0, "identical_order_fraction": None,
              "identical_set_fraction": None, "mean_result_set_jaccard": None,
              "all_observed_orders_identical": None, "same_request_and_identity": len(identities) <= 1}
    if len(identities) > 1 or len(sequences) < 2:
        return output
    pairs = [(left, right) for i, left in enumerate(sequences) for right in sequences[i + 1:]]
    output.update({"pair_count": len(pairs), "identical_order_fraction": statistics.mean(a == b for a, b in pairs),
                   "identical_set_fraction": statistics.mean(set(a) == set(b) for a, b in pairs),
                   "mean_result_set_jaccard": statistics.mean(len(set(a) & set(b)) / len(set(a) | set(b)) if set(a) | set(b) else 1 for a, b in pairs),
                   "all_observed_orders_identical": all(a == b for a, b in pairs)})
    return output


def analyze_retrieval(*, events: list[dict[str, Any]], case: dict[str, Any], expected_trials: int = 5) -> dict[str, Any]:
    validate_case(case)
    if type(expected_trials) is not int or expected_trials < 1:
        raise ValueError("expected_trials must be positive")
    modes = ("hybrid", "fts", "vector")
    searches = [event for event in events if event.get("event") == "search" and event.get("origin") == "retrieval-only"]
    unknown_modes = [event for event in searches if event.get("mode") not in modes]
    profiles = {}
    for mode in modes:
        observed = [score_retrieval_event(event, case) for event in searches if event.get("mode") == mode]
        trials = list(observed)
        for repetition in range(1, expected_trials + 1):
            if not any(row.get("repetition") == repetition for row in observed):
                trials.append(score_retrieval_event({"mode": mode, "repetition": repetition, "status": "missing"}, case))
        trials.sort(key=lambda row: (row.get("repetition") if type(row.get("repetition")) is int else expected_trials + 1, str(row.get("run_id") or "")))
        metrics = {metric: _summary([row.get(metric) for row in trials]) for metric in ("duration_ms", "context_duration_ms", "visible_text_bytes", "visible_text_lines", "first_visible_evidence_rank", "first_sufficient_visible_rank")}
        for name in ("visible_evidence", "raw_item_evidence_diagnostic"):
            metrics[name + "_unit_coverage"] = _summary([(row.get(name) or {}).get("known_evidence_unit_coverage") for row in trials])
        profiles[mode] = {"expected_trials": expected_trials, "actual_trials": len(observed),
                          "trial_count_matches_plan": len(observed) == expected_trials and all(sum(row.get("repetition") == i for row in observed) == 1 for i in range(1, expected_trials + 1)),
                          "status_counts": {s: sum(row["status"] == s for row in trials) for s in ("success", "error", "missing", "unknown")},
                          "visible_sufficient_successes": sum((row.get("visible_evidence") or {}).get("sufficient") is True for row in trials),
                          "raw_item_sufficient_successes_diagnostic": sum((row.get("raw_item_evidence_diagnostic") or {}).get("sufficient") is True for row in trials),
                          "metrics": metrics, "result_order_agreement": _repeat_order_agreement(trials), "trials": trials}
    return {"schema_version": 1, "case_id": case["case_id"], "repo": case["repo"], "scope": "retrieval-only, fixed query, index content frozen during evaluation, no agent or QA answer generation",
            "expected_total_trials": len(modes) * expected_trials, "profiles": profiles,
            "unclassified_retrieval_events": unknown_modes,
            "integrity_events": [event for event in events if event.get("event") in {"preflight", "integrity", "end", "error"}],
            "llm_input_tokens": None, "llm_input_tokens_status": "not_applicable_no_agent_model_calls",
            "returned_text_tokens": None, "returned_text_tokenizer": None,
            "limitations": ["Visible evidence is a conservative exact-text lower bound; missing matches do not establish that the repository has no answer.",
                            "Raw returned-item evidence is a separate diagnostic and may include source content hidden by the public short preview. It is not agent-visible recall.",
                            "Known evidence unit coverage depends on this annotation; unjudged content is not assumed irrelevant.",
                            "Visible ranks are N/A when public text cannot map the evidence to an explicit ranked block. Raw item rank is not substituted.",
                            "Result order agreement describes repeated returned IDs under the same request and index; five identical results do not establish general reliability.",
                            "Latency includes actual query embedding and public context work; embedding usage is not agent LLM input token usage.",
                            "Queries, modes and repetitions must be frozen before execution. Multiple run IDs and duplicate repetitions are retained, never best-of selected."]}


def render_retrieval_markdown(report: dict[str, Any]) -> str:
    def cell(value: Any) -> str:
        return "N/A" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)
    lines = [f"# Retrieval-only observability: {report['case_id']}", "", report["scope"], "",
             "| Mode | Repetition | Status | Visible evidence | Visible sufficient | Raw evidence diagnostic | Bytes | Latency ms | First visible evidence rank |",
             "|---|---:|---|---:|---|---:|---:|---:|---:|"]
    for mode, group in report["profiles"].items():
        for trial in group["trials"]:
            visible, raw = trial.get("visible_evidence") or {}, trial.get("raw_item_evidence_diagnostic") or {}
            values = (mode, trial.get("repetition"), trial["status"], visible.get("known_evidence_unit_coverage"), visible.get("sufficient"), raw.get("known_evidence_unit_coverage"), trial.get("visible_text_bytes"), trial.get("duration_ms"), trial.get("first_visible_evidence_rank"))
            lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    lines.extend(["", "| Mode | Successful / planned | Exact result-order agreement | Visible coverage mean | Latency mean / SD ms |", "|---|---|---:|---:|---|"])
    for mode, group in report["profiles"].items():
        latency = group["metrics"]["duration_ms"]
        values = (mode, f"{group['status_counts']['success']} / {group['expected_trials']}", group["result_order_agreement"]["identical_order_fraction"], group["metrics"]["visible_evidence_unit_coverage"]["mean"], f"{cell(latency['mean'])} / {cell(latency['sd'])}")
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    lines.extend(["", "Agent LLM input tokens: N/A (retrieval-only).", "", "Limitations:", ""])
    lines.extend("- " + text for text in report["limitations"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze", help="Measure existing raw Harbor runs offline")
    analyze.add_argument("--runs-dir", type=Path, required=True)
    analyze.add_argument("--case", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument("--pair", type=Path)
    analyze.add_argument("--judged-report", type=Path)
    analyze.add_argument("--expected-trials", type=int, default=5)
    analyze.add_argument("--profile", choices=(*PROFILES, "all"), default="all")
    retrieval = commands.add_parser("retrieval", help="Score fixed-query retrieval JSONL traces offline")
    retrieval.add_argument("--events", type=Path, nargs="+", required=True)
    retrieval.add_argument("--case", type=Path, required=True)
    retrieval.add_argument("--output", type=Path, required=True)
    retrieval.add_argument("--expected-trials", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        if args.command == "retrieval":
            events = []
            for path in args.events:
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError as error:
                    events.append({"event": "error", "stage": "trace_load", "path": str(path), "error_type": type(error).__name__})
                    continue
                for index, line in enumerate(lines, 1):
                    if line.strip():
                        try:
                            value = json.loads(line)
                        except json.JSONDecodeError:
                            # A terminated process may leave a partial last event.
                            # Preserve earlier observations and all missing slots.
                            events.append({"event": "error", "stage": "trace_parse", "path": str(path), "line": index, "error_type": "JSONDecodeError"})
                            continue
                        if not isinstance(value, dict):
                            events.append({"event": "error", "stage": "trace_parse", "path": str(path), "line": index, "error_type": "NonObjectEvent"})
                            continue
                        events.append(value)
            report = analyze_retrieval(events=events, case=_json(args.case), expected_trials=args.expected_trials)
        else:
            report = analyze_runs(runs_dir=args.runs_dir, case=_json(args.case), expected_trials=args.expected_trials,
                                  profile=args.profile, pair=_json(args.pair) if args.pair else None,
                                  judged_report=_json(args.judged_report) if args.judged_report else None)
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    renderer = render_retrieval_markdown if args.command == "retrieval" else render_markdown
    args.output.with_suffix(".md").write_text(renderer(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
