"""Exercise production task containers with scripted, loopback-only model replies.

This is environment evidence, not a model evaluation: no benchmark question is
answered, no judge is called, and synthetic token usage must never be aggregated
with smoke/formal answers. Both entrypoints and every arm use their real adapter
setup, including the pinned ZG package, local index and MCP where applicable.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
MODELS = {"qwen": "qwen_subagent_e2e", "glm": "no_subagent_e2e"}
FAKE_KEY = "offline-rg-probe-not-a-secret"
PROVIDER_PORT = 18765
PROBE_PROMPT = "Run the scripted ripgrep environment check and report its result."


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def command_value(command: list[str], name: str, default=None):
    return command[command.index(name) + 1] if name in command else default


def agent_spec(command: list[str]) -> tuple[str, str, dict]:
    """Recover the real adapter constructor values from the production runner."""
    kwargs = {}
    for position, value in enumerate(command[:-1]):
        if value != "--agent-kwarg":
            continue
        key, raw = command[position + 1].split("=", 1)
        try:
            kwargs[key] = json.loads(raw)
        except ValueError:
            kwargs[key] = raw
    return command_value(command, "--agent"), command_value(command, "--model"), kwargs


def fixture_command(version: str) -> str:
    """Runs inside OpenCode's bash tool with its inherited identity/PATH/cwd."""
    return (
        "set -euo pipefail; "
        "printf 'RG_PROBE_UID=%s\\n' \"$(id -u)\"; "
        "printf 'RG_PROBE_USER=%s\\n' \"$(id -un)\"; "
        "printf 'RG_PROBE_CWD=%s\\n' \"$(pwd -P)\"; "
        "printf 'RG_PROBE_PATH=%s\\n' \"$PATH\"; "
        "printf 'RG_PROBE_BINARY=%s\\n' \"$(command -v rg)\"; "
        "command -v rg; rg --version; "
        f"test \"$(rg --version | head -n 1)\" = {shlex.quote('ripgrep ' + version)}; "
        "sha256sum \"$(readlink -f \"$(command -v rg)\")\"; "
        "printf 'RG_PROBE_SHA256=%s\\n' \"$(sha256sum \"$(readlink -f \"$(command -v rg)\")\" | cut -d ' ' -f 1)\"; "
        "test \"$(pwd -P)\" = /app; "
        "printf 'RG_PROBE_NEEDLE\\nother\\n' | rg -n -x -F RG_PROBE_NEEDLE; "
        "probe_file=README.md; test -f \"$probe_file\"; "
        "rg -n -m 1 '[A-Za-z]' \"$probe_file\"; "
        "printf 'RG_PROBE_SUCCESS\\n'"
    )


def fake_config(original: dict) -> dict:
    """Only probe-specific transport/runtime controls differ from production."""
    config = copy.deepcopy(original)
    if set(config["provider"]) != {"custom-openai"}:
        raise ValueError("Probe requires the original custom-openai provider")
    config["provider"]["custom-openai"]["options"].update(
        baseURL=f"http://127.0.0.1:{PROVIDER_PORT}/v1", apiKey=FAKE_KEY,
    )
    config.update(autoupdate=False, share="disabled", lsp=False)
    for name in ("build", "general", "explore"):
        config["agent"][name]["steps"] = 5
    return config


def provider_source(command: str, *, allow_children: bool) -> str:
    """Node is already installed by the real adapter; no extra container deps."""
    settings = json.dumps({"command": command, "allowChildren": allow_children,
                           "port": PROVIDER_PORT})
    return "const settings = " + settings + ";\n" + r'''
const http = require("http");
const fs = require("fs");
let sequence = 0;
const server = http.createServer((request, response) => {
  let raw = "";
  request.on("data", chunk => { raw += chunk; });
  request.on("end", () => {
    try {
      const body = JSON.parse(raw);
      const child = Boolean(request.headers["x-parent-session-id"]);
      fs.appendFileSync("/logs/agent/probe-requests.jsonl", JSON.stringify({
        sequence: ++sequence, child, parentSession: request.headers["x-parent-session-id"],
        session: request.headers["x-session-id"], body,
      }) + "\n");
      const toolResults = (body.messages || []).filter(m => m.role === "tool");
      const hasBash = toolResults.some(m => m.tool_call_id === "probe-bash");
      const hasTask = toolResults.some(m => (m.tool_call_id || "").startsWith("probe-task-"));
      const makeCall = (index, id, name, args) => ({index, id, type: "function",
        function: {name, arguments: JSON.stringify(args)}});
      let delta = {role: "assistant", content: "Scripted environment probe complete."};
      let finish = "stop";
      if (body.tools && body.tools.length && !hasBash) {
        delta = {role: "assistant", tool_calls: [makeCall(0, "probe-bash", "bash", {
          command: settings.command, description: "Verify real task-container ripgrep", timeout: 10000,
        })]};
        finish = "tool_calls";
      } else if (body.tools && body.tools.length && !child && settings.allowChildren && !hasTask) {
        delta = {role: "assistant", tool_calls: ["general", "explore"].map((name, index) =>
          makeCall(index, "probe-task-" + name, "task", {
            description: "Check rg as " + name, subagent_type: name,
            prompt: "Run the scripted ripgrep bash check in this " + name + " child session.",
          }))};
        finish = "tool_calls";
      }
      const base = {id: "rg-probe-" + sequence, object: "chat.completion.chunk", created: 1, model: body.model};
      const chunks = [
        {...base, choices: [{index: 0, delta, finish_reason: null}]},
        {...base, choices: [{index: 0, delta: {}, finish_reason: finish}],
          usage: {prompt_tokens: 100, completion_tokens: 50, total_tokens: 150}},
      ];
      const payload = chunks.map(c => "data: " + JSON.stringify(c) + "\n\n").join("") + "data: [DONE]\n\n";
      response.writeHead(200, {"Content-Type": "text/event-stream"});
      response.end(payload);
    } catch (error) {
      response.writeHead(500, {"Content-Type": "text/plain"});
      response.end(String(error));
    }
  });
});
server.listen(settings.port, "127.0.0.1", () => {
  fs.writeFileSync("/logs/agent/probe-provider.ready", String(process.pid));
});
'''


def validate_evidence(snapshot: list[dict], requests: list[dict], *,
                      allow_children: bool, model: str, version: str,
                      binary_sha256: str) -> dict:
    rows = [(row["kind"], json.loads(row["data"]) if isinstance(row["data"], str)
             else row["data"]) for row in snapshot]
    sessions = {row["id"]: row for kind, row in rows if kind == "session"}
    roots = [s for s in sessions.values() if not s.get("parent_id")]
    children = [s for s in sessions.values() if s.get("parent_id")]
    tools = [row for kind, row in rows if kind == "tool"]
    expected = 3 if allow_children else 1
    if len(roots) != 1 or len(sessions) != expected:
        raise ValueError("Unexpected root/child session tree in environment probe")
    if any(s["parent_id"] != roots[0]["id"] for s in children):
        raise ValueError("Environment probe created an unexpected nested child")
    bash = [t for t in tools if t["name"] == "bash"]
    if len(bash) != expected or {t["session_id"] for t in bash} != set(sessions):
        raise ValueError("Every expected session must execute exactly one real bash check")
    required = ["RG_PROBE_SUCCESS", "RG_PROBE_CWD=/app", "RG_PROBE_UID=",
                "RG_PROBE_PATH=", "RG_PROBE_BINARY=", "1:RG_PROBE_NEEDLE", "ripgrep " + version,
                "RG_PROBE_SHA256=" + binary_sha256]
    identities = []
    for tool in bash:
        output = tool.get("output") or ""
        if tool.get("status") != "completed" or any(marker not in output for marker in required):
            raise ValueError("A root/child bash check failed or lacks real search evidence")
        identities.append({line.split("=", 1)[0].removeprefix("RG_PROBE_").lower():
                           line.split("=", 1)[1] for line in output.splitlines()
                           if line.startswith("RG_PROBE_") and "=" in line})
    if any(identity != identities[0] for identity in identities):
        raise ValueError("Root and child bash identities/PATH/workdir differ")
    task_tools = [t for t in tools if t["name"] == "task"]
    if len(tools) != expected + (2 if allow_children else 0):
        raise ValueError("Unexpected extra or invalid tool calls in environment probe")
    if allow_children:
        if ({t["input"].get("subagent_type") for t in task_tools} != {"general", "explore"}
                or {t.get("child_session_id") for t in task_tools} != {s["id"] for s in children}
                or any(t["status"] != "completed" for t in task_tools)):
            raise ValueError("general/explore were not successfully delegated")
    if not requests or any(r["body"].get("model") != model for r in requests):
        raise ValueError("Scripted provider observed the wrong model ID")
    root_requests = [r for r in requests if not r["child"] and r["body"].get("tools")]
    if not root_requests or any(
        ("task" in {t["function"]["name"] for t in r["body"]["tools"]}) != allow_children
        for r in root_requests
    ):
        raise ValueError("Advertised delegation tools differ from the arm policy")
    return {"passed": True, "sessions": len(sessions), "child_sessions": len(children),
            "successful_bash_checks": len(bash), "delegated_agents": sorted(
                t["input"]["subagent_type"] for t in task_tools), "identity": identities[0],
            "model_requests": len(requests), "model_usage_is_synthetic": True}


async def run_case(model_key: str, arm: str, *, task, output: Path,
                   packages: Path, identities: dict, experiment: dict) -> dict:
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.agent.context import AgentContext
    from harbor.models.trial.paths import TrialPaths
    from zg_bench import runner
    from zg_bench.agents.ripgrep import RIPGREP_VERSION, RIPGREP_BINARY_SHA256
    from zg_bench.settings import OPENCODE_VERSION

    entry = importlib.import_module("zg_bench." + MODELS[model_key])
    allowed = arm == "baseline_allowed"
    paths = TrialPaths(output)
    paths.mkdir()
    paths.chmod_dir()
    source_command = entry.command(task.name, arm, output, packages, 1)
    profile = command_value(source_command, "--profile")
    model_name = command_value(source_command, "--model")
    embedding = experiment["embedding_model"]
    package = command_value(source_command, "--zvec-grep-package", "")
    env_changes = {"ZG_BENCH_DISABLE_SUBAGENTS": "0" if allowed else "1",
                   "GLM_API_KEY": FAKE_KEY, "OPENAI_API_KEY": FAKE_KEY}
    if arm in ("node", "rust"):
        env_changes.update(ZG_BENCH_CLI_RUNTIME=arm,
                           ZG_BENCH_EXPECTED_CLI_SHA256=identities[arm]["cli_sha256"],
                           ZG_BENCH_CAPTURE_INDEX_OUTPUT="1")
    with patch.dict(os.environ, env_changes):
        prepared = runner.prepare_setup_cache("opencode", profile,
                    zvec_grep_package=package, embedding_model=embedding)
        suite = runner.load_suite("swe-qa-bench", tier="full", task_overrides=[task.name])
        command = runner.build_harbor_command(suite, profile=profile, agent="opencode",
            model=model_name, jobs_dir=output, job_name="rg-environment-probe",
            zvec_grep_package=prepared.zvec_grep_package or package,
            zvec_grep_package_sha256=prepared.zvec_grep_package_sha256,
            embedding_model=embedding)
        adapter, model_name, kwargs = agent_spec(command)
        original_config = copy.deepcopy(kwargs["opencode_config"])
        kwargs["opencode_config"] = fake_config(original_config)
        write_json(output / "production-entrypoint.json", {
            "entrypoint": entry.__name__, "command": source_command,
            "harbor_command": command, "adapter": adapter, "model": model_name,
            "task_agent_user": task.config.agent.user, "host_environment": env_changes,
            "setup_cache_overlay": json.loads(prepared.compose_path.read_text()),
        })
        write_json(output / "production-opencode-config.json", original_config)
        write_json(output / "probe-opencode-config.json", kwargs["opencode_config"])
        module, class_name = adapter.split(":")
        agent = getattr(importlib.import_module(module), class_name)(
            logs_dir=paths.agent_dir, model_name=model_name, **kwargs)
        environment = DockerEnvironment(environment_dir=task.paths.environment_dir,
            environment_name=task.name, session_id="rg-probe-" + uuid.uuid4().hex[:16],
            trial_paths=paths, task_env_config=task.config.environment,
            extra_docker_compose=[prepared.compose_path], mounts=[
                {"type": "bind", "source": str(paths.agent_dir), "target": "/logs/agent"},
                {"type": "bind", "source": str(paths.verifier_dir), "target": "/logs/verifier"},
                {"type": "bind", "source": str(paths.artifacts_dir), "target": "/logs/artifacts"},
            ])
        provider_path = paths.agent_dir / "probe-provider.cjs"
        provider_path.write_text(provider_source(fixture_command(RIPGREP_VERSION), allow_children=allowed))
        started = time.monotonic()
        evidence = {"model_key": model_key, "arm": arm, "passed": False,
                    "paid_model_calls": 0, "adapter": adapter}
        try:
            await environment.start(force_build=False)
            with environment.with_default_user(task.config.agent.user), environment.scoped_exec_env({
                "OPENAI_API_KEY": FAKE_KEY, "OPENCODE_DISABLE_MODELS_FETCH": "true",
                "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true", "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
            }):
                setup_started = time.monotonic()
                await asyncio.wait_for(agent.setup(environment), timeout=1800)
                evidence["setup_wall_seconds"] = time.monotonic() - setup_started
                result = await agent.exec_as_agent(environment,
                    command=". ~/.nvm/nvm.sh; opencode --version", timeout_sec=30)
                if result.stdout.strip() != OPENCODE_VERSION:
                    raise ValueError("Installed OpenCode differs from the pinned version")
                await agent.exec_as_agent(environment, command=(
                    ". ~/.nvm/nvm.sh; "
                    "nohup node /logs/agent/probe-provider.cjs </dev/null "
                    ">/logs/agent/probe-provider.log 2>&1 &"), timeout_sec=10)
                for _ in range(50):
                    if (paths.agent_dir / "probe-provider.ready").is_file():
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise RuntimeError("Loopback scripted provider did not become ready")
                run_started = time.monotonic()
                await asyncio.wait_for(agent.run(PROBE_PROMPT, environment, AgentContext()), timeout=240)
                evidence["scripted_agent_wall_seconds"] = time.monotonic() - run_started
            await environment.prepare_logs_for_host()
            # The production adapter checks rg at the start of run(), before
            # opening a model session; setup() installs it but does not probe.
            preflight = json.loads((paths.agent_dir / "ripgrep-preflight.json").read_text())
            evidence["ripgrep_preflight"] = preflight
            if (preflight.get("status") != "ready" or preflight.get("version") != RIPGREP_VERSION
                    or preflight.get("binary_sha256") != RIPGREP_BINARY_SHA256):
                raise ValueError("Production rg preflight did not verify the pinned binary")
            snapshot = json.loads((paths.agent_dir / "opencode-session-snapshot.json").read_text())
            requests = [json.loads(line) for line in
                        (paths.agent_dir / "probe-requests.jsonl").read_text().splitlines()]
            evidence.update(validate_evidence(snapshot, requests, allow_children=allowed,
                model=entry.MODEL, version=RIPGREP_VERSION, binary_sha256=RIPGREP_BINARY_SHA256))
            identity = evidence["identity"]
            for before, after in (("uid", "uid"), ("cwd", "cwd"), ("command_path", "binary")):
                if str(preflight[before]) != identity[after]:
                    raise ValueError(f"Production preflight and actual bash differ: {before}")
            evidence["preflight_path_matches_bash"] = preflight["path"] == identity["path"]
            evidence["opencode_version"] = OPENCODE_VERSION
        except Exception as error:
            evidence.update(passed=False, error_type=type(error).__name__, error=str(error))
        finally:
            try:
                await environment.stop(delete=True)
            except Exception as error:
                evidence.update(passed=False, cleanup_error=type(error).__name__ + ": " + str(error))
            evidence["total_wall_seconds_including_setup"] = time.monotonic() - started
            write_json(output / "summary.json", evidence)
        return evidence


async def run(args) -> dict:
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.task.task import Task
    from zg_bench import version_e2e as versions

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    summary = {"schema_version": 1, "kind": "ripgrep-environment-probe",
        "passed": False, "task": args.task, "paid_model_calls": 0,
        "model_provider": "scripted loopback fake provider; real OpenCode and bash",
        "excluded_from_model_evaluation": True, "cases": [],
        "harbor_version": importlib.metadata.version("harbor"),
        "harness_commit": subprocess.check_output(["git", "rev-parse", "HEAD"],
            cwd=ROOT, text=True).strip(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write_json(output / "summary.json", summary)
    try:
        if summary["harbor_version"] != "0.18.0":
            raise ValueError("Probe requires the pinned Harbor 0.18.0")
        DockerEnvironment.preflight()
        selected = list(MODELS) if args.model == "all" else [args.model]
        variants = ("node", "rust") if "glm" in selected else ("rust",)
        identities = {arm: versions.identity(args.packages.resolve() / arm, arm) for arm in variants}
        write_json(output / "package-identities.json", identities)
        task_copy = output / "task" / args.task
        shutil.copytree(ROOT / "datasets" / args.task, task_copy)
        summary["task_image"] = versions.prebuild_task_image(task_copy, output / "task")
        task = Task(task_copy)
        for model_key in selected:
            entry = importlib.import_module("zg_bench." + MODELS[model_key])
            experiment = entry.configure("smoke")
            for arm in entry.ARMS:
                print(f"PROBE START {model_key} {arm}", flush=True)
                case = await run_case(model_key, arm, task=task,
                    output=output / model_key / arm, packages=args.packages.resolve(),
                    identities=identities, experiment=experiment)
                summary["cases"].append(case)
                write_json(output / "summary.json", summary)
                print(f"PROBE COMPLETE {model_key} {arm} passed={case['passed']}", flush=True)
        summary["passed"] = bool(summary["cases"]) and all(case["passed"] for case in summary["cases"])
    except BaseException as error:
        summary.update(error_type=type(error).__name__, error=str(error))
        raise
    finally:
        write_json(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("qwen", "glm", "all"), default="all")
    parser.add_argument("--task", choices=("conan-19",), default="conan-19")
    parser.add_argument("--packages", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args))["passed"] else 1)


if __name__ == "__main__":
    main()
