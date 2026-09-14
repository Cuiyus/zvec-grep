#!/usr/bin/env python3
"""One CI job: prepare, execute a pair series, score, and always retain its ledger."""
from __future__ import annotations
import argparse
import json
import hashlib
import math
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time
from urllib import request, error

HERE = Path(__file__).resolve().parent


def sdk_preflight(output: Path):
    """Validate actual zg SDK authorization/index/query before the full corpus."""
    from runner import docker_command, embedding_endpoint, run_named, with_embedding_environment
    source, logs, cache, index = [output / name for name in ("source", "runtime", "model-cache", "index")]
    for path in (source, logs, cache, index, source / ".zvec-grep", index / "locks"):
        path.mkdir(parents=True, exist_ok=True)
    (source / "probe.md").write_text("代码仓库问答：检索源代码并引用文件。 Repository code search finds source files.\n")
    for args in (["init", "-q", str(source)], ["-C", str(source), "add", "probe.md"],
                 ["-C", str(source), "-c", "user.name=Workspace QA", "-c", "user.email=benchmark@localhost",
                  "-c", "gc.auto=0", "commit", "-qm", "Synthetic SDK connectivity fixture"]):
        subprocess.run(["git", *args], check=True)
    image = "zg-readonly-qa:0.2.2"
    command = with_embedding_environment(docker_command(image, source, logs, cache, index=index), embedding_endpoint())
    try:
        run_named(command + [image, "node", "/opt/qa/embedding-probe.mjs"],
                  "workspaceqa-sdk-probe", timeout=240, diagnostic_path=logs / "failure.json")
        result = json.loads((logs / "result.json").read_text())
        if result.get("status") != "completed" or result.get("vector_query_retrieved_fixture") is not True:
            raise RuntimeError("zg SDK remote embedding probe did not complete")
        print(json.dumps({"phase": "sdk_preflight", "status": "completed", "wall_seconds": result["wall_seconds"]}), flush=True)
    finally:
        for path in (source, cache, index):
            shutil.rmtree(path)


def embedding_preflight(output: Path):
    from runner import EMBEDDING, embedding_endpoint
    endpoint = embedding_endpoint()
    payload = {"model": EMBEDDING.split("/", 1)[1], "input": ["代码仓库问答 / repository question answering"],
               "dimensions": 1024, "encoding_format": "float"}
    started = time.monotonic()
    result = {"requested_model": payload["model"], "endpoint": endpoint, "dimension": 1024,
              "phase": "setup_connectivity_probe", "included_in_agent_tokens": False, "status": "failed"}
    try:
        req = request.Request(endpoint, data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["QWEN_API_KEY"]})
        with request.urlopen(req, timeout=90) as response:
            body = json.load(response)
        data = body.get("data", [])
        if len(data) != 1 or len(data[0].get("embedding", [])) != 1024:
            raise ValueError("Remote embedding preflight returned an unexpected vector shape")
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in data[0]["embedding"]):
            raise ValueError("Remote embedding preflight returned an invalid vector")
        if body.get("model") not in (None, payload["model"]):
            raise ValueError("Embedding endpoint returned a different model")
        result.update(status="completed", resolved_model=body.get("model"), usage=body.get("usage"),
                      request_sha256=hashlib.sha256(json.dumps(payload).encode()).hexdigest())
    except Exception as exc:
        result.update(error_type=type(exc).__name__)
        if isinstance(exc, error.HTTPError):
            result["http_status"] = exc.code
        raise
    finally:
        result["wall_seconds"] = time.monotonic() - started
        output.write_text(json.dumps(result, indent=2) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task-id", required=True)
    p.add_argument("--repetitions", type=int, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--upstream", type=Path, required=True)
    args = p.parse_args()
    lock = json.loads((HERE / "data/lock.json").read_text())
    task = next(t for t in lock["tasks"] if t["task_id"] == args.task_id)
    if args.repetitions < 1:
        raise ValueError("repetitions must be positive")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    preparation, runs = root / "dataset", root / "runs"
    from runner import make_plan, collect_results
    plan = make_plan(args.task_id, args.repetitions)
    # A setup failure must still account for every planned trial.
    root.joinpath("planned.json").write_text(json.dumps(plan, indent=2) + "\n")
    scoped = {**lock, "tasks": [task], "repetitions": args.repetitions}
    root.joinpath("selection.json").write_text(json.dumps(scoped, ensure_ascii=False, indent=2) + "\n")
    outcome = 1
    try:
        # Stop before downloads, while retaining the already-frozen trial ledger.
        for name in ("QODER_PERSONAL_ACCESS_TOKEN", "GLM_API_KEY", "QWEN_API_KEY"):
            if not os.environ.get(name):
                raise RuntimeError(f"Required GitHub Actions secret is missing: {name}")
        embedding_preflight(root / "embedding-preflight.json")
        print(json.dumps({"phase": "sdk_preflight", "status": "starting"}), flush=True)
        sdk_preflight(root / "sdk-preflight")
        print(json.dumps({"phase": "dataset_preparation", "status": "starting"}), flush=True)
        subprocess.run([sys.executable, str(HERE / "dataset.py"), "--task-id", args.task_id,
                        "--output", str(preparation), "--upstream", str(args.upstream)], check=True)
        print(json.dumps({"phase": "paired_trials", "status": "starting"}), flush=True)
        result = subprocess.run([sys.executable, str(HERE / "runner.py"), "--task-id", args.task_id,
                                 "--source-root", str(preparation / "source"), "--question-file", str(preparation / "question.txt"),
                                 "--answer-filename", task["answer_filename"], "--output", str(runs),
                                 "--repetitions", str(args.repetitions), "--timeout", "900"])
        # Retain and judge completed candidates even if a different trial failed.
        judged = subprocess.run([sys.executable, str(HERE / "judge.py"), "--metadata",
                                 str(preparation / "tasks" / args.task_id / "metadata.json"), "--task-dir",
                                 str(preparation / "tasks" / args.task_id), "--runs-dir", str(runs)])
        outcome = 0 if result.returncode == 0 and judged.returncode == 0 else 1
    except Exception as error:
        root.joinpath("setup-failure.json").write_text(json.dumps({"status": "failed", "error_type": type(error).__name__}) + "\n")
        raise
    finally:
        if not (runs / "trial-results.json").exists():
            runs.mkdir(parents=True, exist_ok=True)
            collect_results(runs, plan)
        reported = subprocess.run([sys.executable, str(HERE / "report.py"), "--runs-dir", str(runs),
                                   "--manifest", str(root / "selection.json"), "--output", str(root / "report"), "--require-complete"])
        if reported.returncode:
            outcome = 1
    return outcome


if __name__ == "__main__":
    raise SystemExit(main())
