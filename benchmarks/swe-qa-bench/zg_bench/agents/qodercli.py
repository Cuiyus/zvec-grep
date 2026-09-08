from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shlex
import tempfile
from pathlib import Path
from typing import Any, override

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    NonZeroAgentExitCodeError,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)
from harbor.models.trial.paths import EnvironmentPaths

from .opencode import resilient_nvm_node_install_snippet

QODER_CONFIG_DIR = "/tmp/qoder-benchmark-config"
_AUTH_FILE = "/tmp/qoder-benchmark-auth/token"
_OUTPUT_NAME = "qodercli-stream.jsonl"
_NVM_INIT = '. "$HOME/.nvm/nvm.sh"; '


def read_stream_events(path: Path) -> list[dict[str, Any]]:
    """Read completed SDK messages, ignoring deltas and a truncated last line."""
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") in {
            "system",
            "assistant",
            "user",
            "result",
        }:
            events.append(event)
    return events


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (integer and not isinstance(value, int))
    ):
        raise ValueError("Qoder CLI reported invalid numeric usage")
    return value


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


class QoderCLI(BaseInstalledAgent):
    """Run the pinned Qoder CLI with a CI PAT and explicit JSONL output only."""

    SUPPORTS_ATIF = True

    def __init__(
        self, *args: Any, extra_env: dict[str, str] | None = None, **kwargs: Any
    ):
        resolved_env = dict(extra_env or {})
        self._personal_access_token = (
            resolved_env.pop("QODER_PERSONAL_ACCESS_TOKEN", None)
            or os.environ.get("QODER_PERSONAL_ACCESS_TOKEN", "")
        ).strip()
        # Harbor logs exec environment values. Credentials travel only through
        # a private uploaded file; never through extra_env or command arguments.
        super().__init__(*args, extra_env=resolved_env, **kwargs)

    @staticmethod
    @override
    def name() -> str:
        return "qodercli"

    @override
    def get_version_command(self) -> str:
        return _NVM_INIT + "qodercli --version"

    @override
    def parse_version(self, stdout: str) -> str:
        match = re.search(r"\b\d+\.\d+\.\d+\b", stdout)
        return match.group(0) if match else stdout.strip()

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        if not self._version or not re.fullmatch(r"\d+\.\d+\.\d+", self._version):
            raise ValueError("Qoder CLI benchmark requires a pinned release version")
        await self.exec_as_root(
            environment,
            command="apt-get update && apt-get install -y curl",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        package = shlex.quote(f"@qoder-ai/qodercli@{self._version}")
        version = shlex.quote(self._version)
        command = (
            "set -euo pipefail; "
            + resilient_nvm_node_install_snippet()
            + '; installed_qoder_version="$(qodercli --version 2>/dev/null || true)"; '
            + f'if [ "$installed_qoder_version" != {version} ]; then '
            + "npm_config_fetch_retries=4 npm_config_fetch_retry_mintimeout=2000 "
            + f"npm_config_fetch_retry_maxtimeout=20000 npm install --global {package}; fi; "
            + f'test "$(qodercli --version)" = {version}'
        )
        for attempt in range(3):
            try:
                await self.exec_as_agent(environment, command=command)
                break
            except NonZeroAgentExitCodeError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)
        await self.exec_as_agent(
            environment,
            command=f"mkdir -p {QODER_CONFIG_DIR} && chmod 700 {QODER_CONFIG_DIR}",
        )

    async def _upload_auth(self, environment: BaseEnvironment) -> None:
        if not self._personal_access_token:
            raise ValueError(
                "Qoder CLI requires QODER_PERSONAL_ACCESS_TOKEN in CI; "
                "interactive email/browser login is unavailable in benchmark containers"
            )
        await self.exec_as_agent(
            environment,
            command="mkdir -p /tmp/qoder-benchmark-auth && chmod 700 /tmp/qoder-benchmark-auth",
        )
        with tempfile.TemporaryDirectory(prefix="qoder-bench-secret-") as directory:
            path = Path(directory) / "token"
            path.write_text(self._personal_access_token, encoding="utf-8")
            path.chmod(0o600)
            await environment.upload_file(path, _AUTH_FILE)
        command = f"chmod 600 {_AUTH_FILE}"
        if environment.default_user is not None:
            command += (
                f" && chown {shlex.quote(str(environment.default_user))} {_AUTH_FILE}"
            )
        await self.exec_as_root(environment, command=command)

    def _run_command(self, instruction: str) -> str:
        if not self.model_name:
            raise ValueError("Qoder CLI benchmark requires an explicit model")
        # Native catalog models are selected by display name, unlike Custom
        # model IDs. Keep Harbor's model identity stable and explicit.
        cli_model = (
            "Qwen3.8-Max" if self.model_name == "qwen3.8-max" else self.model_name
        )
        output = EnvironmentPaths.agent_dir / _OUTPUT_NAME
        stderr = EnvironmentPaths.agent_dir / "qodercli-stderr.txt"
        return (
            "set -euo pipefail; "
            + _NVM_INIT
            + f"trap 'rm -f {_AUTH_FILE}' EXIT; "
            + f'export QODER_PERSONAL_ACCESS_TOKEN="$(cat {_AUTH_FILE})"; '
            + f"export QODER_CONFIG_DIR={QODER_CONFIG_DIR}; "
            + "qodercli --print --output-format stream-json --no-session-persistence "
            + "--permission-mode bypass_permissions "
            + f"--model {shlex.quote(cli_model)} -- {shlex.quote(instruction)} "
            + f"> {shlex.quote(output.as_posix())} 2> {shlex.quote(stderr.as_posix())}"
        )

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        try:
            await self._upload_auth(environment)
            await self.exec_as_agent(
                environment, command=self._run_command(instruction)
            )
            await environment.download_file(
                (EnvironmentPaths.agent_dir / _OUTPUT_NAME).as_posix(),
                self.logs_dir / _OUTPUT_NAME,
            )
            events = read_stream_events(self.logs_dir / _OUTPUT_NAME)
            results = [
                event
                for event in events
                if event.get("type") == "result" and not event.get("parent_tool_use_id")
            ]
            if not results:
                raise RuntimeError("Qoder CLI exited without a completed result event")
            result = results[-1]
            if result.get("is_error") or result.get("subtype") != "success":
                raise RuntimeError(
                    "Qoder CLI reported an unsuccessful result; see its stream log"
                )
            observed = {
                event["message"]["model"].lower()
                for event in events
                if event.get("type") == "assistant"
                and not event.get("parent_tool_use_id")
                and isinstance(event.get("message"), dict)
                and isinstance(event["message"].get("model"), str)
            }
            if not observed and isinstance(result.get("modelUsage"), dict):
                # Token masking retains model names in the final result. Use
                # that evidence when the stream omits assistant model fields.
                observed = {
                    name.lower()
                    for name in result["modelUsage"]
                    if isinstance(name, str)
                }
            if not observed:
                raise RuntimeError(
                    "Qoder CLI did not report the executed model identity"
                )
            if observed != {self.model_name.lower()}:
                raise RuntimeError(
                    "Qoder CLI response model differs from the requested benchmark model"
                )
            self.populate_context_post_run(context)
        finally:
            # Also remove the token after setup/download errors or cancellation.
            # No Qoder configuration directory is exported as an artifact.
            await environment.exec(command=f"rm -f {_AUTH_FILE}")

    def _trajectory(self, events: list[dict[str, Any]]) -> Trajectory | None:
        steps: list[Step] = []
        calls: dict[tuple[str, str, str], tuple[Step, str]] = {}
        messages: dict[tuple[str, str, str], Step] = {}
        result: dict[str, Any] = {}
        session_id = self.session_id or "qoder-benchmark"
        for event in events:
            if not event.get("parent_tool_use_id"):
                session_id = event.get("session_id") or session_id
            scope = (
                str(event.get("session_id") or session_id),
                str(event.get("parent_tool_use_id") or ""),
            )
            kind = event.get("type")
            if kind == "result":
                if not event.get("parent_tool_use_id"):
                    result = event
                continue
            message = event.get("message")
            if kind not in {"assistant", "user"} or not isinstance(message, dict):
                continue
            content = message.get("content", [])
            if kind == "user":
                for block in content if isinstance(content, list) else []:
                    if (
                        not isinstance(block, dict)
                        or block.get("type") != "tool_result"
                    ):
                        continue
                    call_id = block.get("tool_use_id")
                    call_key = (*scope, call_id)
                    if call_key not in calls:
                        continue
                    step, trajectory_call_id = calls[call_key]
                    observation = ObservationResult(
                        source_call_id=trajectory_call_id,
                        content=_text(block.get("content")),
                        extra={"is_error": bool(block.get("is_error"))},
                    )
                    if step.observation is None:
                        step.observation = Observation(results=[])
                    if not any(
                        item.source_call_id == trajectory_call_id
                        for item in step.observation.results
                    ):
                        step.observation.results.append(observation)
                text = _text(content)
                if text:
                    steps.append(
                        Step(step_id=len(steps) + 1, source="user", message=text)
                    )
                continue
            message_id = message.get("id")
            message_key = (*scope, message_id)
            step = messages.get(message_key) if message_id else None
            if step is None:
                step = Step(
                    step_id=len(steps) + 1,
                    source="agent",
                    model_name=message.get("model") or self.model_name,
                    message="",
                    llm_call_count=1,
                )
                steps.append(step)
                if message_id:
                    messages[message_key] = step
            text = _text(content)
            if text and text not in step.message:
                step.message = "\n".join(filter(None, [step.message, text]))
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                call_id = block.get("id")
                name = block.get("name")
                call_key = (*scope, call_id)
                if (
                    not isinstance(call_id, str)
                    or not isinstance(name, str)
                    or call_key in calls
                ):
                    continue
                arguments = block.get("input")
                trajectory_call_id = f"qoder-call-{len(calls) + 1}"
                tool = ToolCall(
                    tool_call_id=trajectory_call_id,
                    function_name=name,
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
                step.tool_calls = [*(step.tool_calls or []), tool]
                calls[call_key] = (step, trajectory_call_id)
        answer = result.get("result")
        if (
            isinstance(answer, str)
            and answer
            and (
                not steps or steps[-1].source != "agent" or steps[-1].message != answer
            )
        ):
            steps.append(Step(step_id=len(steps) + 1, source="agent", message=answer))
        if not steps:
            return None
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        model_usage = (
            result.get("modelUsage")
            if isinstance(result.get("modelUsage"), dict)
            else {}
        )
        cost = _number(result.get("total_cost_usd"))
        costs = [
            _number(value.get("costUSD"))
            for value in model_usage.values()
            if isinstance(value, dict)
        ]
        if cost is None and costs and all(value is not None for value in costs):
            cost = sum(costs)
        # The native account protocol hardcodes total_cost_usd=0; its billing
        # unit is credits. Keep credits separate and never claim a free run.
        if cost == 0:
            cost = None
        token_counts = {
            key: _number(usage.get(key), integer=True)
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens")
        }
        # Native account responses in Qoder 1.1.45 can mask every token field
        # to zero. Such a completed model response did not consume zero tokens.
        token_usage_available = any(
            value and value > 0 for value in token_counts.values()
        )
        return Trajectory(
            schema_version="ATIF-v1.7",
            session_id=session_id,
            agent=Agent(
                name=self.name(),
                version=self._version or "unknown",
                model_name=self.model_name,
            ),
            steps=steps,
            final_metrics=FinalMetrics(
                # Qoder 1.1.45 normalizes provider usage to inclusive input
                # tokens before emitting the SDK result (including cache).
                total_prompt_tokens=(
                    token_counts["input_tokens"] if token_usage_available else None
                ),
                total_completion_tokens=(
                    token_counts["output_tokens"] if token_usage_available else None
                ),
                total_cached_tokens=(
                    token_counts["cache_read_input_tokens"]
                    if token_usage_available
                    else None
                ),
                total_cost_usd=cost,
                total_steps=len(steps),
                extra={
                    "qoder_usage": usage,
                    "qoder_model_usage": model_usage,
                    "qoder_total_credits": _number(result.get("total_credits")),
                    "token_usage_available": token_usage_available,
                },
            ),
        )

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        trajectory = self._trajectory(read_stream_events(self.logs_dir / _OUTPUT_NAME))
        if trajectory is None:
            return
        (self.logs_dir / "trajectory.json").write_text(
            json.dumps(trajectory.to_json_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        metrics = trajectory.final_metrics
        if metrics:
            context.n_input_tokens = metrics.total_prompt_tokens
            context.n_output_tokens = metrics.total_completion_tokens
            context.n_cache_tokens = metrics.total_cached_tokens
            context.cost_usd = metrics.total_cost_usd
            context.metadata = {**(context.metadata or {}), **(metrics.extra or {})}
            if not context.metadata.get("token_usage_available"):
                context.metadata["token_usage_unavailable_reason"] = (
                    "Qoder native account output does not expose token counts"
                )
