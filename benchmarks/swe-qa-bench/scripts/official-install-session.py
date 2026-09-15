#!/usr/bin/env python3
"""Run a pinned QA session through unchanged official zg install integration.

This entry point runs in a new container with its real, clean user home. It does
not inject guidance, override tool descriptions, proxy MCP, or rewrite arguments.
Only the model HTTP traffic may be observed through qa-session's existing tap.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

VERSIONS = {"zg": "0.2.2", "opencode": "1.18.4", "qodercli": "1.1.45"}
EMBEDDING = "local/potion-code-16m-v2"
SEARCH = "zvec_grep_search"
QODER_ALLOW = ["mcp__zvec_grep__zvec_grep_search", "mcp__zvec_grep__zvec_grep_rg"]
DENIED = ["Bash", "Edit", "Write", "NotebookEdit", "Agent", "Task", "Skill", "WebFetch", "WebSearch", "ImageGen", "ImageSearch", "Workflow"]
START = "<!-- ZVEC_GREP_START -->"
END = "<!-- ZVEC_GREP_END -->"
REDIRECT_ENV = ("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR", "OPENCODE_PERMISSION",
                "QODER_CONFIG_DIR", "QODER_IDE_MCP_PATH", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                "ZVEC_GREP_HOME", "ZVEC_GREP_SERVER_URL", "ZVEC_GREP_MODE", "ZVEC_GREP_MCP_TOOLSET",
                "ZVEC_GREP_INSTALL_SKIP_SERVER", "ZVEC_GREP_EMBEDDING", "ZVEC_GREP_ENDPOINT")


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def scrub(text, env):
    for name in ("OPENAI_API_KEY", "GLM_API_KEY", "QWEN_API_KEY", "QODER_PERSONAL_ACCESS_TOKEN"):
        if env.get(name): text = text.replace(env[name], "[REDACTED]")
    return text


def validate_spec(spec):
    if spec.get("agent") not in {"opencode", "qodercli"}: raise ValueError("Unsupported pinned agent")
    if spec.get("model") not in {"glm-5.2", "qwen3.8-max"}: raise ValueError("Unsupported pinned model")
    if spec["agent"] == "qodercli" and spec["model"] != "qwen3.8-max": raise ValueError("Qoder requires qwen3.8-max")
    if spec.get("profile") not in {"baseline", "zvec-grep"}: raise ValueError("profile must be baseline or zvec-grep")
    if spec.get("prompt_variant", "P00") != "P00": raise ValueError("Official installation run only accepts P00")
    for key in ("guidance_override", "description_overrides", "append_system_prompt", "system_prompt", "instructions", "mcp_command", "base_config"):
        if key in spec: raise ValueError(f"Official installation forbids {key}")
    if spec["profile"] == "zvec-grep" and spec.get("prepare_index", True) is not True: raise ValueError("Every official zg trial must build a fresh index")
    if not isinstance(spec.get("instruction"), str) or not spec["instruction"].strip(): raise ValueError("Exact QA instruction is required")
    if (spec.get("replay_plan") or spec.get("replay_plan_inline")) and spec["profile"] != "zvec-grep": raise ValueError("Replay requires the installed zg profile")
    if spec.get("replay_plan") and spec.get("replay_plan_inline"): raise ValueError("Only one replay plan source is allowed")
    for name in ("root", "log_dir"):
        if not isinstance(spec.get(name), str) or not Path(spec[name]).is_absolute(): raise ValueError(f"{name} must be absolute")
    for name in ("model_requests", "tool_calls", "input_tokens", "wall_seconds"):
        if type(spec.get("limits", {}).get(name)) is not int or spec["limits"][name] < 1: raise ValueError("Explicit positive session limits are required")
    if spec["agent"] == "opencode":
        url = urlsplit(spec.get("base_url", ""))
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("OpenCode requires a credential-free provider base URL")


def default_paths(agent, home=None):
    home = Path.home() if home is None else Path(home)
    directory = home / ".config/opencode" if agent == "opencode" else home / ".qoder"
    return {"config": directory / ("opencode.json" if agent == "opencode" else "settings.json"),
            "guidance": directory / "AGENTS.md", "ide_config": home / ".qoder/mcp.json"}


def base_config(spec, provider_url=None):
    if spec["agent"] == "opencode":
        permissions = {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow"}
        if spec["profile"] == "zvec-grep": permissions["zvec_grep_*"] = "allow"
        return {"$schema": "https://opencode.ai/config.json", "model": "custom-openai/" + spec["model"],
                "autoupdate": False, "share": "disabled", "lsp": False,
                "provider": {"custom-openai": {"npm": "@ai-sdk/openai-compatible",
                    "options": {"apiKey": "{env:OPENAI_API_KEY}", "baseURL": provider_url or spec["base_url"]},
                    "models": {spec["model"]: {"name": spec["model"], "temperature": True}}}},
                "agent": {"build": {"temperature": 0, "steps": spec["limits"]["model_requests"]}},
                "permission": permissions}
    return {"model": {"name": "Qwen3.8-Max"}, "tools": {"core": ["Read", "Grep", "Glob"], "useRipgrep": True},
            "general": {"defaultPermissionMode": "dont_ask", "enableAutoUpdate": False, "enableAutoUpdateNotification": False},
            "disableAllHooks": True, "permissions": {"allow": ["Read", "Grep", "Glob"], "deny": DENIED},
            "security": {"disableYoloMode": True, "environmentVariableRedaction": {"enabled": True}},
            "mcp": {"lazyLoad": False}, "mcpServers": {}}


def agent_command(spec):
    if spec["agent"] == "opencode":
        return ["opencode", "--model", "custom-openai/" + spec["model"], "run", "--format", "json", "--thinking", "--", spec["instruction"]]
    return ["qodercli", "--print", "--output-format", "stream-json", "--no-session-persistence",
            "--permission-mode", "dont_ask", "--tools", "Read,Grep,Glob", "--disallowed-tools", ",".join(DENIED),
            "--max-model-request-retries", "0", "--max-turns", str(spec["limits"]["model_requests"]),
            "--model", "Qwen3.8-Max", "--", spec["instruction"]]


def isolated_environment(spec):
    environment = dict(os.environ)
    polluted = [k for k in REDIRECT_ENV if environment.get(k)]
    if polluted: raise ValueError("Official default discovery requires a fresh container; conflicting environment: " + ", ".join(polluted))
    # HOME and CODEX_HOME are never reassigned. The image supplies its real user.
    environment.update(OPENCODE_DISABLE_AUTOUPDATE="true", OPENCODE_DISABLE_LSP_DOWNLOAD="true",
                       QODER_EXPOSE_TOKEN_USAGE="1", ZVEC_GREP_MODEL_CACHE=spec.get("model_cache", "/models"),
                       NO_COLOR="1")
    return environment


def verify_installed(before, after, guidance, agent):
    if guidance.count(START) != 1 or guidance.count(END) != 1 or guidance.index(START) >= guidance.index(END):
        raise ValueError("Official managed AGENTS guidance is missing or malformed")
    if guidance.strip() != guidance[guidance.index(START):guidance.index(END) + len(END)]:
        raise ValueError("Clean install must not contain additional guidance")
    if "instructions" in after or "systemPrompt" in after: raise ValueError("Unexpected manual prompt configuration")
    if agent == "opencode":
        if set(after.get("mcp", {})) != {"zvec_grep"}: raise ValueError("Official OpenCode MCP catalog differs")
        server = after["mcp"]["zvec_grep"]
        if set(server) != {"type", "command", "enabled", "timeout"}: raise ValueError("Unexpected modified OpenCode MCP fields")
        command = server.get("command")
        if server.get("type") != "local" or server.get("enabled") is not True or server.get("timeout") != 600000:
            raise ValueError("Official OpenCode transport defaults differ")
        stripped = {k:v for k,v in after.items() if k != "mcp"}
        if stripped != before: raise ValueError("Installer changed unrelated base configuration")
    else:
        if set(after.get("mcpServers", {})) != {"zvec_grep"}: raise ValueError("Official Qoder MCP catalog differs")
        server = after["mcpServers"]["zvec_grep"]
        if set(server) != {"command", "args", "timeout", "trust", "description", "alwaysAllow"}: raise ValueError("Unexpected modified Qoder MCP fields")
        command = [server.get("command"), *server.get("args", [])]
        if server.get("trust") is not True or server.get("timeout") != 600000:
            raise ValueError("Official Qoder transport defaults differ")
        if set(server.get("alwaysAllow", [])) != {"zvec_grep_search", "zvec_grep_rg"}:
            raise ValueError("Official Qoder tool preapprovals differ")
        stripped = copy.deepcopy(after); stripped["mcpServers"] = {}
        allow = stripped.get("permissions", {}).get("allow", [])
        if set(allow) != set(before["permissions"]["allow"]) | set(QODER_ALLOW): raise ValueError("Qoder installer permissions differ")
        stripped["permissions"]["allow"] = [v for v in allow if v not in QODER_ALLOW]
        if stripped != before: raise ValueError("Installer changed unrelated base configuration")
    if command != ["zg", "server", "--stdio"]: raise ValueError("Installed MCP command is not the unchanged official default")
    return {"valid": True, "native_mcp_command": command, "managed_guidance_sha256": digest(guidance)}


def run_checked(command, *, cwd, env, log_prefix, timeout=1200):
    prefix = Path(log_prefix); prefix.parent.mkdir(parents=True, exist_ok=True); started = time.monotonic()
    save(prefix.with_suffix(".command.json"), {"command": command, "cwd": str(cwd)})
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        out, err, code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        out, err, code = exc.stdout or b"", exc.stderr or b"", None
        out = out.decode(errors="replace") if isinstance(out, bytes) else out
        err = err.decode(errors="replace") if isinstance(err, bytes) else err
    prefix.with_suffix(".stdout.txt").write_text(scrub(out, env)); prefix.with_suffix(".stderr.txt").write_text(scrub(err, env))
    save(prefix.with_suffix(".status.json"), {"returncode": code, "wall_seconds": time.monotonic()-started, "status": "completed" if code==0 else "failed"})
    if code != 0: raise RuntimeError("Command failed: " + command[0] + " " + (command[1] if len(command)>1 else ""))
    return out.strip()


def assert_source_readonly(root):
    if not (os.statvfs(root).f_flag & os.ST_RDONLY): raise ValueError("QA source must be mounted read-only")


def source_identity(root):
    paths = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True).stdout.decode().split("\0")
    return {p:digest((root/p).read_bytes()) for p in sorted(paths) if p and (root/p).is_file()}


def load_qa_session():
    path = Path(__file__).with_name("qa-session.py")
    loader = importlib.util.spec_from_file_location("official_qa_session", path)
    module = importlib.util.module_from_spec(loader); loader.loader.exec_module(module)
    return module


def probe_installed_mcp(command, *, cwd, env, log_dir):
    """A tools/list client of the unchanged installed command; not an MCP proxy."""
    dest = Path(log_dir)/"native-mcp-preflight"; dest.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
    incoming = queue.Queue(); transcript=[]
    def reader():
        for line in process.stdout:
            (incoming.put(line))
    worker=threading.Thread(target=reader,daemon=True); worker.start()
    def send(value):
        transcript.append({"direction":"client","message":value}); process.stdin.write(json.dumps(value)+"\n"); process.stdin.flush()
    def receive(request_id):
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try: line=incoming.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None: raise RuntimeError("Installed MCP exited during preflight")
                continue
            value=json.loads(line); transcript.append({"direction":"server","message":value})
            if value.get("id")==request_id:
                if "error" in value: raise RuntimeError("Installed MCP preflight returned an error")
                return value["result"]
        raise RuntimeError("Installed MCP preflight timed out")
    try:
        send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2026-07-28","capabilities":{},"clientInfo":{"name":"official-install-contract","version":"1"}}})
        initialized=receive(1)
        send({"jsonrpc":"2.0","method":"notifications/initialized"})
        send({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}); catalog=receive(2)
        if catalog.get("nextCursor") or [t.get("name") for t in catalog.get("tools",[])] != [SEARCH]: raise ValueError("Official default MCP catalog is not search-only")
        result={"valid":True,"native_mcp_command":command,"initialize":initialized,"tools":catalog["tools"],"search_calls":0,"model_calls":0}
        save(dest/"catalog.json",result)
        return result
    finally:
        save(dest/"transcript.json",transcript)
        process.stdin.close()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
        worker.join(timeout=2); process.stdout.close()
        (dest/"stderr.txt").write_text(scrub(process.stderr.read(),env));process.stderr.close()


def verify_guidance_delivery(spec, paths, log_dir):
    if spec["profile"] == "baseline": return {"guidance_loaded":None,"verification":"not_applicable_baseline"}
    if spec["agent"] == "qodercli":
        return {"guidance_loaded":None,"verification":"installer_wrote_default_global_AGENTS_path; native user discovery enabled; native stream does not expose complete system prompt", "guidance_path":str(paths["guidance"])}
    files=sorted((p for p in Path(log_dir).glob("wire-requests/request-*.json")
                  if re.fullmatch(r"request-\d+\.json",p.name)),
                 key=lambda p:int(p.stem.split("-")[1]))
    guide=paths["guidance"].read_text().strip()
    def texts(content):
        if isinstance(content,str):return [content]
        if isinstance(content,list):return [p.get("text","") for p in content if isinstance(p,dict)]
        return []
    task_request=None;request=None
    for path in files:
        candidate=json.loads(path.read_text())
        user_text="\n".join(t for m in candidate.get("messages",[]) if m.get("role")=="user" for t in texts(m.get("content")))
        if candidate.get("tools") and spec["instruction"] in user_text:
            task_request=path;request=candidate;break
    system=[]
    if request:
        for message in request.get("messages",[]):
            if message.get("role")!="system": continue
            content=message.get("content","")
            if isinstance(content,str):system.append(content)
            elif isinstance(content,list):system += [p.get("text","") for p in content if isinstance(p,dict)]
    count="\n".join(system).count(guide)
    return {"guidance_loaded":count==1 if request else None,"verification":"first_native_QA_request_with_tools_system_content",
            "guidance_occurrences_in_first_task_system":count if request else None,
            "wire_request":str(task_request) if task_request else None,"installed_guidance_sha256":digest(paths["guidance"].read_bytes())}


def run(spec):
    validate_spec(spec); root=Path(spec["root"]).resolve(); logs=Path(spec["log_dir"]).resolve()
    if logs==root or root in logs.parents: raise ValueError("Logs must be outside the corpus")
    env=isolated_environment(spec); paths=default_paths(spec["agent"])
    for key in ("config","guidance",*(("ide_config",) if spec["agent"]=="qodercli" else ())):
        if paths[key].exists(): raise ValueError("Official installation requires clean native paths: "+str(paths[key]))
    assert_source_readonly(root); logs.mkdir(parents=True,exist_ok=True)
    manifest={"schema_version":1,"integration":"official_zg_install","profile":spec["profile"],"prompt_variant":"P00",
              "installation_verified":False,"native_mcp_command":None,"guidance_delivery":"native_discovery",
              "guidance_loaded":None,"config_path":str(paths["config"]),"guidance_path":str(paths["guidance"]),
              "home_source":"container_user_default; HOME not reassigned","instruction_sha256":digest(spec["instruction"]),
              "agent":spec["agent"],"model":spec["model"],"versions":{},"prepare_only":bool(spec.get("prepare_only")),
              "embedding":EMBEDDING if spec["profile"]=="zvec-grep" else None,
              "new_index_builds":0,"agent_model_calls_started":False,"status":"preparing"}
    save(logs/"install-manifest.json",manifest); tap=None; old_cwd=Path.cwd(); daemon_started=False
    before_source=source_identity(root); save(logs/"source-before.json",before_source)
    try:
        for cli in (("zg",spec["agent"]) if spec["profile"]=="zvec-grep" else (spec["agent"],)):
            version=run_checked([cli,"--version"],cwd=root,env=env,log_prefix=logs/("version-"+cli),timeout=60)
            if version!=VERSIONS[cli]:raise ValueError(f"Pinned {cli} version differs: {version}")
            manifest["versions"][cli]=version
        if spec["profile"]=="zvec-grep":
            index=root/".zvec-grep"
            if index.exists() and any(index.iterdir()):raise ValueError("Every zg trial requires an empty index mount")
            run_checked(["zg","config","model","set",EMBEDDING,"--default","--device","cpu"],cwd=root,env=env,log_prefix=logs/"model-config")
            index_command=["zg","index",str(root),"--embedding",EMBEDDING,"--device","cpu","--model-cache",spec.get("model_cache","/models")]
            index_started=time.monotonic()
            manifest["index_build"]={"status":"running","command":index_command,"embedding":EMBEDDING}
            save(logs/"install-manifest.json",manifest)
            run_checked(index_command,cwd=root,env=env,log_prefix=logs/"index-build",timeout=1800)
            manifest["index_build"].update(status="completed",wall_seconds=time.monotonic()-index_started)
            manifest["new_index_builds"]=1;manifest["index_manifest_sha256"]=digest((index/"manifest.json").read_bytes())
        else:
            manifest["index_build"]={"status":"not_applicable_baseline","wall_seconds":0}
        qa=load_qa_session(); provider=spec.get("base_url")
        if spec.get("replay_plan_inline"):
            save(logs/"replay-plan.json",spec["replay_plan_inline"])
        replay_plan=str(logs/"replay-plan.json") if spec.get("replay_plan_inline") else spec.get("replay_plan")
        if spec["agent"]=="opencode" and not spec.get("prepare_only") and not replay_plan:
            tap=qa.WireTap(logs,spec["base_url"]);provider=tap.start()
        before=base_config(spec,provider_url=provider);save(paths["config"],before);save(logs/"agent-config-before.json",before)
        if spec["profile"]=="zvec-grep":
            target="qoder" if spec["agent"]=="qodercli" else "opencode"
            command=["zg","install","--target",target,"--yes"]
            manifest["install_command"]=command;daemon_started=True
            run_checked(command,cwd=root,env=env,log_prefix=logs/"install",timeout=180)
            after=json.loads(paths["config"].read_text());guide=paths["guidance"].read_text()
            checked=verify_installed(before,after,guide,spec["agent"])
            manifest.update(installation_verified=True,native_mcp_command=checked["native_mcp_command"])
            (logs/"AGENTS-installed.md").write_text(guide)
            manifest["installed_guidance_sha256"]=digest(paths["guidance"].read_bytes())
            if spec["agent"]=="qodercli":
                (logs/"qoder-ide-mcp-installed.json").write_bytes(paths["ide_config"].read_bytes())
            manifest["native_catalog"]=probe_installed_mcp(checked["native_mcp_command"],cwd=root,env=env,log_dir=logs)
        else:
            after=before;manifest["installation_verified"]=True;manifest["install_command"]=None
        (logs/"agent-config-installed.json").write_bytes(paths["config"].read_bytes())
        manifest["installed_agent_config_sha256"]=digest(paths["config"].read_bytes())
        save(logs/"install-manifest.json",manifest)
        if replay_plan:
            run_checked(["node",str(Path(__file__).with_name("official-replay.mjs")),"--plan",str(replay_plan),"--log-dir",str(logs)],cwd=root,env=env,log_prefix=logs/"official-replay",timeout=1800)
            code=0
        elif spec.get("prepare_only"):
            save(logs/"session.json",{"status":"preparation_only","returncode":0,"model_calls":0,"search_calls":0})
            code=0
        else:
            command=agent_command(spec)
            session={"command":command,"env":env,"config_path":str(paths["config"]),"limits":spec["limits"],
                     "native_name":"opencode.txt" if spec["agent"]=="opencode" else "qodercli-stream.jsonl",
                     "log_dir":str(logs),"tap_upstream":None}
            public_session={**session,"env":{k:v for k,v in env.items() if k in ("OPENCODE_DISABLE_AUTOUPDATE","OPENCODE_DISABLE_LSP_DOWNLOAD","QODER_EXPOSE_TOKEN_USAGE","ZVEC_GREP_MODEL_CACHE","NO_COLOR")}}
            save(logs/"native-session-spec.json",public_session)
            manifest["agent_model_calls_started"]=True;save(logs/"install-manifest.json",manifest)
            os.chdir(root)
            code=qa.run(session)
            manifest.update(verify_guidance_delivery(spec,paths,logs))
            if spec["agent"]=="opencode" and spec["profile"]=="zvec-grep" and manifest["guidance_loaded"] is not True:
                manifest["status"]="guidance_verification_failed";code=4
        manifest["final_agent_config_sha256"]=digest(paths["config"].read_bytes())
        manifest["agent_config_unchanged"]=manifest["final_agent_config_sha256"]==manifest["installed_agent_config_sha256"]
        manifest["guidance_unchanged"]=(digest(paths["guidance"].read_bytes())==manifest["installed_guidance_sha256"]) if spec["profile"]=="zvec-grep" else not paths["guidance"].exists()
        if not manifest["agent_config_unchanged"] or not manifest["guidance_unchanged"]:raise ValueError("Agent changed installed configuration or guidance")
        manifest["source_unchanged"]=source_identity(root)==before_source
        if not manifest["source_unchanged"]:raise ValueError("QA source files changed")
        if manifest["status"]=="preparing":manifest["status"]="completed" if code==0 else "agent_failed"
        return code
    except Exception as exc:
        manifest.update(status="failed",error_type=type(exc).__name__,error=scrub(str(exc),env))
        save(logs/"official-failure.json",{"error":manifest["error"],"error_type":manifest["error_type"]})
        if not (logs/"session.json").exists():save(logs/"session.json",{"status":"preparation_failed","returncode":None,"model_calls":0 if not manifest["agent_model_calls_started"] else None})
        return 5
    finally:
        os.chdir(old_cwd)
        if tap:tap.server.shutdown();tap.server.server_close()
        if paths["config"].exists():manifest["final_agent_config_sha256"]=digest(paths["config"].read_bytes())
        if daemon_started:
            try:run_checked(["zg","server","off"],cwd=root,env=env,log_prefix=logs/"server-off",timeout=60)
            except Exception as exc:manifest["server_shutdown_error"]=type(exc).__name__
        save(logs/"install-manifest.json",manifest)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--spec",required=True,type=Path)
    args=parser.parse_args(argv)
    return run(json.loads(args.spec.read_text()))


if __name__=="__main__":raise SystemExit(main())
