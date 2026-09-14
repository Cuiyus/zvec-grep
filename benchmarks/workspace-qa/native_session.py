#!/usr/bin/env python3
"""Fresh Qoder session using the unmodified zg 0.2.2 Qoder installation.

Only this trusted harness is executed. Installation evidence is data, and the
offline validator never executes commands or reads paths supplied by artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

PROTOCOL = "workspace-qa-qoder-native-install-v3"
ZG_VERSION = "0.2.2"
QODER_VERSION = "1.1.45"
MODEL = "Qwen3.8-Max"
EMBEDDING_MODEL = "qwen/qwen3.7-text-embedding"
QODER_SEARCH_TOOL = "mcp__zvec_grep__zvec_grep_search"
READ_TOOLS = ("Read", "Grep", "Glob")
DENY_TOOLS = ("Bash", "Edit", "Write", "NotebookEdit", "Agent", "Task", "Skill",
              "WebFetch", "WebSearch", "ImageGen", "ImageSearch", "Workflow")
INSTALL_COMMAND = ["zg", "install", "--target", "qoder", "--yes"]
READY_COMMAND = ["zg", "server", "status", "--check-ready"]
INSTALL_FILES = ("settings.json", "AGENTS.md", "mcp.json")
# Exact output of the published 0.2.2 installer in an empty Qoder HOME. This
# binds the evidence to the standard guidance, rather than a hand-written copy.
STANDARD_GUIDANCE_SHA256 = "45cd2d41b63b6ba1a7afbd2e7c429b4918b415fb8b6a8d2cf1eb01fb0c286742"
SECRET_NAMES = ("OPENAI_API_KEY", "GLM_API_KEY", "QODER_PERSONAL_ACCESS_TOKEN", "QWEN_API_KEY",
                "ZVEC_GREP_API_KEY")
EMBEDDING_ENV_NAMES = ("QWEN_API_KEY", "ZVEC_GREP_API_KEY", "ZVEC_GREP_ENDPOINT",
                       "QWEN_EMBEDDING_ENDPOINT", "ZG_QA_ALLOW_REMOTE_EMBEDDING")
CONFIG_OVERRIDE_NAMES = ("QODER_CONFIG_DIR", "QODER_IDE_MCP_PATH", "ZVEC_GREP_HOME",
                         "ZVEC_GREP_AUTHORIZATION_KEY_FILE", "ZVEC_GREP_INSTALL_SKIP_SERVER",
                         "ZVEC_GREP_MCP_TOOLSET", "QODER_CLI_HOME", "GEMINI_CLI_HOME",
                         "QODER_CONFIG_DIR_NAME", "QODER_NO_RC")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scrub(value: str, environment: dict[str, str]) -> str:
    for name in SECRET_NAMES:
        if environment.get(name):
            value = value.replace(environment[name], "[REDACTED]")
    return value


def save(path: Path, value: dict, environment: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scrub(json.dumps(value, ensure_ascii=False, indent=2), environment) + "\n")


def auth_command(root: str) -> list[str]:
    return ["zg", "auth", "grant", root, "--capability", "embedding", "--scope", "workspace",
            "--embedding", EMBEDDING_MODEL]


def validate_spec(spec: dict) -> None:
    if not isinstance(spec, dict) or spec.get("protocol") != PROTOCOL:
        raise ValueError("native session protocol mismatch")
    if spec.get("profile") not in {"baseline", "with-zg"}:
        raise ValueError("native session profile must be baseline or with-zg")
    if spec.get("model") != MODEL or spec.get("embedding_model") != EMBEDDING_MODEL:
        raise ValueError("native session requires the pinned agent and embedding models")
    if not isinstance(spec.get("prompt"), str) or not spec["prompt"].strip():
        raise ValueError("native session needs a nonempty original prompt")
    if not isinstance(spec.get("root"), str) or not Path(spec["root"]).is_absolute():
        raise ValueError("native session root must be absolute")
    limits = spec.get("limits")
    if not isinstance(limits, dict) or set(limits) != {"model_requests", "tool_calls", "input_tokens", "wall_seconds"}:
        raise ValueError("native session needs all four observable limits")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in limits.values()):
        raise ValueError("native session limits must be positive finite numbers")
    if any(type(limits[k]) is not int for k in ("model_requests", "tool_calls", "input_tokens")):
        raise ValueError("native session count limits must be integers")
    # No alternate settings, argv or env injection through the session spec.
    allowed = {"protocol", "profile", "prompt", "model", "embedding_model", "root", "limits"}
    if set(spec) - allowed:
        raise ValueError("native session spec contains unsupported overrides")


def session_spec(spec: dict, log_dir: Path) -> dict:
    validate_spec(spec)
    allowed = [*READ_TOOLS, *([QODER_SEARCH_TOOL] if spec["profile"] == "with-zg" else [])]
    command = ["qodercli", "--print", "--output-format", "stream-json", "--no-session-persistence",
               "--permission-mode", "dont_ask", "--tools", ",".join(READ_TOOLS),
               "--allowed-tools", ",".join(allowed), "--disallowed-tools", ",".join(DENY_TOOLS),
               "--max-model-request-retries", "0", "--max-turns", str(spec["limits"]["model_requests"]),
               "--model", MODEL, "--", spec["prompt"]]
    return {"command": command, "env": {"QODER_EXPOSE_TOKEN_USAGE": "1", "QODER_MCP_LAZY": "0"},
            "limits": spec["limits"], "log_dir": str(log_dir), "native_name": "qodercli-stream.jsonl"}


def runtime_environment(spec: dict, environment: dict[str, str]) -> dict[str, str]:
    env = dict(environment)
    if any(env.get(name) for name in CONFIG_OVERRIDE_NAMES):
        raise ValueError("native session must use an isolated default HOME without configuration overrides")
    if not env.get("HOME") or not Path(env["HOME"]).is_absolute():
        raise ValueError("native session needs an absolute isolated HOME")
    if spec["profile"] == "baseline":
        for name in EMBEDDING_ENV_NAMES:
            env.pop(name, None)
    else:
        if not env.get("QWEN_API_KEY"):
            raise ValueError("native remote embedding requires QWEN_API_KEY in the container environment")
        endpoint = urlsplit(env.get("ZVEC_GREP_ENDPOINT", ""))
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username
                or endpoint.password or endpoint.query or endpoint.fragment):
            raise ValueError("native remote embedding requires a credential-free HTTP(S) endpoint")
        # The old SDK permit flag is not part of the native zg auth protocol.
        env.pop("ZG_QA_ALLOW_REMOTE_EMBEDDING", None)
    env["NO_COLOR"] = "1"
    return env


def run_command(argv: list[str], name: str, *, root: Path, log_dir: Path, env: dict,
                manifest: dict, timeout: float = 120) -> str:
    started = time.monotonic()
    output, errors, returncode = "", "", None
    try:
        result = subprocess.run(argv, cwd=root, env=env, text=True, capture_output=True, timeout=timeout)
        output, errors, returncode = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        errors = error.stderr or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        if isinstance(errors, bytes):
            errors = errors.decode(errors="replace")
        raise RuntimeError(f"native setup phase {name} timed out") from None
    finally:
        folder = log_dir / "installation"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.stdout.txt").write_text(scrub(output, env))
        (folder / f"{name}.stderr.txt").write_text(scrub(errors, env))
        manifest["commands"].append({"phase": name, "argv": argv, "returncode": returncode,
                                     "wall_seconds": time.monotonic() - started,
                                     "stdout_path": f"installation/{name}.stdout.txt",
                                     "stderr_path": f"installation/{name}.stderr.txt"})
    if returncode:
        raise RuntimeError(f"native setup phase {name} failed with exit code {returncode}")
    return output.strip()


def copy_installation(home: Path, log_dir: Path, env: dict) -> dict:
    files = {}
    for name in INSTALL_FILES:
        source = home / ".qoder" / name
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"standard installer did not produce {name}")
        data = source.read_bytes()
        if any(env.get(key) and env[key].encode() in data for key in SECRET_NAMES):
            raise ValueError("standard installation unexpectedly contains a credential; refusing artifact copy")
        path = log_dir / "installation" / name
        path.write_bytes(data)
        files[name] = {"path": f"installation/{name}", "sha256": sha256(path), "source_path": str(source)}
    return files


def _object(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"invalid object in {path.name}")
    return value


def validate_installation(agent: Path, *, profile: str = "with-zg") -> dict:
    """Validate copied evidence only; never execute artifact commands or scripts."""
    path = agent / "install-manifest.json"
    if path.is_symlink():
        raise ValueError("installation manifest must be a regular artifact")
    manifest = _object(path)
    if manifest.get("schema_version") != 1 or manifest.get("protocol") != PROTOCOL or manifest.get("profile") != profile:
        raise ValueError("installation manifest schema/protocol/profile mismatch")
    if manifest.get("qoder_version") != QODER_VERSION:
        raise ValueError("Qoder installation version mismatch")
    if profile == "baseline":
        if manifest.get("status") != "not_applicable" or manifest.get("standard_install") is not False or manifest.get("files") != {}:
            raise ValueError("baseline must have no zg installation")
    elif profile == "with-zg":
        expected = {"status": "completed", "standard_install": True, "zg_version": ZG_VERSION,
                    "embedding_model": EMBEDDING_MODEL, "install_command": INSTALL_COMMAND,
                    "auth_command": auth_command(manifest.get("root", "")), "daemon_ready": True,
                    "mcp_startup": {"command": "zg", "args": ["server", "--stdio"]}}
        if any(manifest.get(k) != v for k, v in expected.items()):
            raise ValueError("native installation identity/authorization/readiness mismatch")
        if not isinstance(manifest.get("root"), str) or not Path(manifest["root"]).is_absolute():
            raise ValueError("installation root must be absolute")
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != set(INSTALL_FILES):
            raise ValueError("installation must include all standard Qoder files")
        if (agent / "installation").is_symlink():
            raise ValueError("installation evidence directory must not be a symlink")
        for name in INSTALL_FILES:
            expected_path = f"installation/{name}"
            item = files[name]
            candidate = agent / expected_path
            if (not isinstance(item, dict) or item.get("path") != expected_path or candidate.is_symlink()
                    or not candidate.is_file() or candidate.resolve().parent != (agent / "installation").resolve()
                    or sha256(candidate) != item.get("sha256")):
                raise ValueError(f"standard installation artifact hash/path mismatch: {name}")
        if files["AGENTS.md"]["sha256"] != STANDARD_GUIDANCE_SHA256:
            raise ValueError("Qoder guidance differs from the published zg 0.2.2 installer")
        settings = _object(agent / "installation/settings.json")
        server = {"command": "zg", "args": ["server", "--stdio"], "timeout": 600000, "trust": True,
                  "description": "Managed by zg install; managed permissions=zvec_grep_search,zvec_grep_rg",
                  "alwaysAllow": ["zvec_grep_search", "zvec_grep_rg"]}
        if settings != {"mcpServers": {"zvec_grep": server}, "permissions": {"allow": [
                QODER_SEARCH_TOOL, "mcp__zvec_grep__zvec_grep_rg"]}}:
            raise ValueError("Qoder settings differ from the standard empty-HOME stdio installation")
        ide = _object(agent / "installation/mcp.json")
        ide_servers = ide.get("mcpServers")
        if not isinstance(ide_servers, dict) or set(ide_servers) != {"zvec_grep"} or set(ide) != {"mcpServers"}:
            raise ValueError("invalid standard Qoder IDE configuration")
        ide_server = ide_servers["zvec_grep"]
        ide_args = ide_server.get("args") if isinstance(ide_server, dict) else None
        if (not isinstance(ide_server, dict) or set(ide_server) != {"command", "args", "timeout", "description"}
                or not isinstance(ide_server.get("command"), str) or not Path(ide_server["command"]).is_absolute()
                or not isinstance(ide_args, list) or len(ide_args) != 3
                or not isinstance(ide_args[0], str) or not Path(ide_args[0]).is_absolute()
                # npm's PATH launcher preserves /.../.bin/zg in process.argv[1];
                # direct node invocation instead records dist/cli/index.js.
                or not (Path(ide_args[0]).name == "zg" or ide_args[0].endswith("/dist/cli/index.js"))
                or ide_args[1:] != ["server", "--stdio"]
                or ide_server.get("timeout") != 600000 or ide_server.get("description") != "Managed by zg install"):
            raise ValueError("Qoder IDE startup differs from the standard stdio installation")
        phases = {entry.get("phase"): entry for entry in manifest.get("commands", []) if isinstance(entry, dict)}
        for phase, argv in (("install", INSTALL_COMMAND), ("grant", expected["auth_command"]), ("ready", READY_COMMAND)):
            if phases.get(phase, {}).get("argv") != argv or phases.get(phase, {}).get("returncode") != 0:
                raise ValueError(f"standard {phase} command did not complete")
        if manifest.get("files_unchanged_after_session") is False:
            raise ValueError("Qoder installation changed during the agent session")
    else:
        raise ValueError("unknown installation profile")
    return {"valid": True, "manifest_path": "install-manifest.json", "manifest_sha256": sha256(path),
            "protocol": PROTOCOL, "profile": profile, "standard_install": profile == "with-zg",
            "files": manifest["files"], "setup_wall_seconds": manifest.get("setup_wall_seconds")}


def run(spec: dict, *, log_dir: Path = Path("/logs"), qa_session: Path = Path("/opt/qa/qa-session.py"),
        environment: dict[str, str] | None = None) -> int:
    validate_spec(spec)
    env = runtime_environment(spec, dict(os.environ) if environment is None else environment)
    home, root = Path(env["HOME"]), Path(spec["root"])
    log_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "protocol": PROTOCOL, "profile": spec["profile"], "root": str(root),
                "status": "started", "standard_install": spec["profile"] == "with-zg", "files": {},
                "embedding_model": EMBEDDING_MODEL, "commands": [], "daemon_ready": False,
                "install_command": INSTALL_COMMAND if spec["profile"] == "with-zg" else None,
                "auth_command": auth_command(str(root)) if spec["profile"] == "with-zg" else None}
    manifest_path = log_dir / "install-manifest.json"
    started = time.monotonic()
    daemon_started, launched = False, False
    try:
        if any((home / ".qoder" / name).exists() for name in INSTALL_FILES) or (home / ".zvec-grep/daemon/instance.lock").exists():
            raise ValueError("native session requires fresh Qoder settings and daemon state")
        manifest["qoder_version"] = run_command(["qodercli", "--version"], "qoder-version", root=root,
            log_dir=log_dir, env=env, manifest=manifest)
        if manifest["qoder_version"] != QODER_VERSION:
            raise ValueError("Qoder version differs from the pinned runtime")
        if spec["profile"] == "with-zg":
            version = run_command(["zg", "--version"], "zg-version", root=root, log_dir=log_dir, env=env, manifest=manifest)
            manifest["zg_version"] = version.removeprefix("zvec-grep ").removeprefix("zg ")
            if manifest["zg_version"] != ZG_VERSION:
                raise ValueError("zg version differs from the pinned runtime")
            # Install starts the official daemon with the container credentials.
            # Qoder's standard stdio launcher then connects to that same daemon.
            daemon_started = True
            run_command(INSTALL_COMMAND, "install", root=root, log_dir=log_dir, env=env, manifest=manifest)
            manifest["files"] = copy_installation(home, log_dir, env)
            run_command(auth_command(str(root)), "grant", root=root, log_dir=log_dir, env=env, manifest=manifest)
            grant = root / ".zvec-grep/authorization.json"
            signing_key = home / ".zvec-grep/authorization-signing.key"
            if not grant.is_file() or not signing_key.is_file():
                raise ValueError("native workspace authorization did not create its grant and signing key")
            manifest["authorization"] = {"scope": "workspace", "grant_sha256": sha256(grant),
                                           "signing_key_present": True}
            ready = run_command(READY_COMMAND, "ready", root=root, log_dir=log_dir, env=env, manifest=manifest)
            if "Server: ready" not in ready or "MCP toolset: agent" not in ready:
                raise ValueError("native default agent daemon is not ready")
            manifest.update(status="completed", daemon_ready=True,
                            mcp_startup={"command": "zg", "args": ["server", "--stdio"]})
        else:
            manifest["status"] = "not_applicable"
        manifest["setup_wall_seconds"] = time.monotonic() - started
        save(manifest_path, manifest, env)
        validate_installation(log_dir, profile=spec["profile"])
        child_spec = session_spec(spec, log_dir)
        save(log_dir / "session-spec.json", child_spec, env)
        print(json.dumps({"phase": "native-install", "status": manifest["status"],
                          "setup_wall_seconds": manifest["setup_wall_seconds"]}), flush=True)
        launched = True
        # No prompt/config rewriting, trace repair, model retry, or model call is
        # performed here. qa-session enforces the same native counters per arm.
        returncode = subprocess.run([sys.executable, str(qa_session), "--spec", str(log_dir / "session-spec.json")],
                                    cwd=root, env=env).returncode
        manifest["qa_session_returncode"] = returncode
        if spec["profile"] == "with-zg":
            manifest["files_unchanged_after_session"] = all(
                (home / ".qoder" / name).is_file() and sha256(home / ".qoder" / name) == item["sha256"]
                for name, item in manifest["files"].items())
            if not manifest["files_unchanged_after_session"]:
                raise ValueError("standard Qoder installation was modified during the session")
        return returncode
    except (OSError, ValueError, RuntimeError) as error:
        manifest["status"] = "failed"
        manifest["error"] = {"type": type(error).__name__, "message": scrub(str(error), env)}
        manifest["qa_session_launched"] = launched
        print(json.dumps({"phase": "native-install", "status": "failed", "error_type": type(error).__name__}), flush=True)
        return 2
    finally:
        manifest.setdefault("setup_wall_seconds", time.monotonic() - started)
        if daemon_started:
            try:
                run_command(["zg", "server", "off"], "stop", root=root, log_dir=log_dir, env=env,
                            manifest=manifest, timeout=45)
            except (OSError, RuntimeError) as error:
                manifest["cleanup_error_type"] = type(error).__name__
        save(manifest_path, manifest, env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    return run(_object(args.spec), log_dir=args.spec.resolve().parent)


if __name__ == "__main__":
    raise SystemExit(main())
