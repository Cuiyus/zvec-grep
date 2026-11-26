"""Rejudge one verified Harbor evidence artifact without starting an agent."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import time
import zipfile
from pathlib import Path

SOURCE_REPO = "Cuiyus/zvec-grep"
SOURCE_BRANCH = "bench/sweqa-search-tool-names"
SOURCE_SHA = "583836e3058d80f447eb8bcd7d3336c9790b7d2c"
MODEL_NAMES = {"glm-5.2", "qwen3.7-max", "qwen3.8-max", "qwen4-max"}
TOOL_NAMES = {"zvec_grep_search", "search", "semantic_search", "hybrid_search", "explore"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def api(path, *, binary=False):
    body = subprocess.check_output(["gh", "api", f"repos/{SOURCE_REPO}/{path}"])
    return body if binary else json.loads(body)


def authorized(login):
    require(re.fullmatch(r"[a-zA-Z0-9-]+", login), "invalid source actor")
    permission = api(f"collaborators/{login}/permission")["role_name"]
    require(permission in {"admin", "maintain"}, "unauthorized source actor")


def prepare(directory, source_code):
    require(os.environ["GITHUB_REPOSITORY"].lower() == SOURCE_REPO.lower(), "wrong repository")
    task = os.environ["SOURCE_TASK"]
    source_attempt = int(os.environ["SOURCE_ATTEMPT"])
    source_id = int(os.environ["SOURCE_RUN"])
    require(source_attempt > 0 and source_id > 0, "invalid source run or attempt")
    selection = json.loads((source_code / "benchmarks/swe-qa-bench/zg_bench/swe_qa/data/selection.json").read_text())
    allowed_tasks = {v["task_slug"] for v in selection["tasks"] if v["task_id"] != "astropy:38"}
    require(task in allowed_tasks, "task is outside the original 19")
    run_path = f"actions/runs/{source_id}"
    run = api(f"{run_path}/attempts/{source_attempt}")
    require(run["id"] == source_id and run["run_attempt"] == source_attempt, "source run mismatch")
    require(run["head_repository"]["full_name"].lower() == SOURCE_REPO.lower() and
            run["repository"]["full_name"].lower() == SOURCE_REPO.lower() and
            run["head_sha"] == SOURCE_SHA and run["head_branch"] == SOURCE_BRANCH and
            run["path"] == ".github/workflows/swe-qa-bench.yml" and
            run["event"] == "workflow_dispatch", "unexpected source workflow revision")
    for login in {run["actor"]["login"], run["triggering_actor"]["login"]}:
        authorized(login)
    title = run["display_title"].split(" / ")
    require(len(title) == 6 and title[:2] == ["SWE-QA Bench", f"Rust {SOURCE_SHA}"] and
            title[3:5] == ["local embedding", "tool-name-19"], "unexpected source configuration")
    model, tool = title[2], title[5]
    require(model in MODEL_NAMES and tool in TOOL_NAMES, "unsupported source arm")
    jobs = api(f"{run_path}/attempts/{source_attempt}/jobs?per_page=100")["jobs"]
    job, = (j for j in jobs if j["name"] == f"Pair + judge / {task}")
    steps = {s["name"]: s["conclusion"] for s in job["steps"]}
    require(job["head_sha"] == SOURCE_SHA and
            steps.get("Run the selected Harbor profiles") == "success" and
            steps.get("Judge tool-name trials and write the per-task report") == "failure" and
            steps.get("Reject secret leakage in artifacts") == "success",
            "source is not an executed, ungraded task")
    name = f"swe-qa-pair-{task}-{source_id}-{source_attempt}"
    artifact, = api(f"{run_path}/artifacts?name={name}&per_page=100")["artifacts"]
    binding = artifact["workflow_run"]
    require(not artifact["expired"] and artifact["name"] == name and
            binding["id"] == source_id and binding["head_sha"] == SOURCE_SHA and
            binding["head_branch"] == SOURCE_BRANCH and
            binding["repository_id"] == run["repository"]["id"] and
            binding["head_repository_id"] == run["head_repository"]["id"], "artifact run mismatch")
    raw = api(f"actions/artifacts/{artifact['id']}/zip", binary=True)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    require(artifact["digest"] == digest, "artifact checksum mismatch")
    job_name = f"gha-{source_id}-{source_attempt}-{task}-zvec-grep"
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        pair = json.loads(archive.read(f"pair-{task}.json"))
        require(pair["valid"] is True and pair["task_slug"] == task and
                pair["actual_trials"] == pair["expected_trials"] == 3 and
                set(pair["profiles"]) == {"zvec-grep"}, "invalid trial set")
        profile = pair["profiles"]["zvec-grep"]
        trials = profile["trials"]
        require(profile["job_name"] == job_name and len(trials) == 3 and
                len({t["trial_name"] for t in trials}) == 3 and
                {t["trial_index"] for t in trials} == {1, 2, 3}, "invalid trial identities")
        for trial in trials:
            sample = trial["trial_name"]
            require(trial["model"] == model and trial["job_name"] == job_name and
                    isinstance(trial["answer"], str) and trial["answer"].strip() and
                    re.fullmatch(re.escape(task) + r"__[a-zA-Z0-9]+", sample), "invalid original trial")
            setup_path = f"swe-qa-runs/{task}/{job_name}/{sample}/agent/zvec-grep-setup.json"
            setup = json.loads(archive.read(setup_path))
            require(setup["status"] == "ready" and setup["mcp_search_tool_name"] == tool and
                    set(setup["mcp_advertised_tools"]) & TOOL_NAMES == {tool}, "alias evidence mismatch")
    identity = {
        "model": model, "search_tool_name": tool, "task_slug": task,
        "candidate_commit": SOURCE_SHA, "harness_commit": SOURCE_SHA,
        "run_id": str(source_id), "run_attempt": source_attempt,
        "source_pair_artifact_id": artifact["id"], "source_pair_artifact_digest": digest,
        "source_job_id": job["id"],
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "identity.json").write_text(json.dumps(identity))
    (directory / "pair.json").write_text(json.dumps(pair))


def with_rate_limit_backoff(completion):
    deadline = time.monotonic() + 45 * 60

    def complete(**kwargs):
        delay = 30
        while time.monotonic() < deadline:
            try:
                return completion(**kwargs)
            except Exception as error:
                code = getattr(error, "status_code", None)
                if code != 429 and type(error).__name__ != "RateLimitError":
                    raise
                headers = getattr(getattr(error, "response", None), "headers", {}) or {}
                retry_after = headers.get("Retry-After", "")
                wait = max(delay, int(retry_after) if str(retry_after).isdigit() else 0)
                if time.monotonic() + wait >= deadline:
                    raise
                print(f"Judge rate limited; waiting {wait}s.", flush=True)
                time.sleep(wait)
                delay = min(300, delay * 2)
        raise TimeoutError("judge rate-limit recovery deadline exhausted")

    return complete


def judge(directory, source_code):
    from zg_bench.engines.judge import default_completion, judge_task_trials
    from zg_bench.engines.registry import resolve_opencode_model
    from zg_bench.metrics.summary import summarize_profile
    from zg_bench.swe_qa.pairs import load_references

    identity = json.loads((directory / "identity.json").read_text())
    pair = json.loads((directory / "pair.json").read_text())
    model = identity["model"]
    runtime = resolve_opencode_model(f"custom-openai/{model}", require_credentials=True)
    refs = source_code / "benchmarks/swe-qa-bench/zg_bench/swe_qa/data/references.json"
    reference = load_references(refs)[identity["task_slug"]]
    result = judge_task_trials(
        pair=pair, reference=reference,
        completion_fn=with_rate_limit_backoff(default_completion()),
        api_key=runtime.api_key, api_base=runtime.configuration.base_url,
        attempts=3, concurrency=1, model=model, profile_names=("zvec-grep",),
    )["zvec-grep"]
    report = {
        "schema_version": 1, "scope": "tool-name-19", **identity,
        "recovery_run_id": os.environ["GITHUB_RUN_ID"],
        "recovery_run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "recovery_workflow_sha": os.environ["RECOVERY_WORKFLOW_SHA"],
        "cases": [{"task_id": reference["task_id"], "trial_count": len(result),
                   "profiles": {"zvec-grep": summarize_profile(result)}}],
    }
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "judge"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("source_code", type=Path)
    args = parser.parse_args()
    (prepare if args.action == "prepare" else judge)(args.directory, args.source_code)
