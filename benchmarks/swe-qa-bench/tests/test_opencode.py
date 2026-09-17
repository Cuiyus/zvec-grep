from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from harbor.agents.installed.base import NonZeroAgentExitCodeError

from zg_bench import runner
from zg_bench.agents.opencode import (
    ResilientOpenCode,
    resilient_nvm_node_install_snippet,
)
from zg_bench.agents.zvec_opencode import ZvecOpenCode


class _InstallHarness(ResilientOpenCode):
    def __init__(self, *, failures: int = 0) -> None:
        self._version = "1.18.4"
        self.failures = failures
        self.root_commands: list[str] = []
        self.agent_commands: list[str] = []

    async def exec_as_root(
        self, environment: Any, command: str, **kwargs: Any
    ) -> None:
        self.root_commands.append(command)

    async def exec_as_agent(
        self, environment: Any, command: str, **kwargs: Any
    ) -> None:
        self.agent_commands.append(command)
        if self.failures:
            self.failures -= 1
            raise NonZeroAgentExitCodeError("transient install failure")


class ResilientOpenCodeTests(unittest.IsolatedAsyncioTestCase):
    def test_nvm_install_is_cache_first_and_does_not_pipe_to_bash(self) -> None:
        snippet = resilient_nvm_node_install_snippet()

        self.assertIn('if [ ! -s "$NVM_DIR/nvm.sh" ]', snippet)
        self.assertIn("--fail", snippet)
        self.assertIn("--retry 3", snippet)
        self.assertIn("--retry-all-errors", snippet)
        self.assertIn("--retry-max-time 90", snippet)
        self.assertIn('--output "$nvm_installer"', snippet)
        self.assertNotIn("| bash", snippet)

    async def test_install_retries_transient_nonzero_failures(self) -> None:
        agent = _InstallHarness(failures=2)

        with patch(
            "zg_bench.agents.opencode.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            await agent.install(object())

        self.assertEqual(len(agent.root_commands), 1)
        self.assertEqual(len(agent.agent_commands), 3)
        self.assertEqual(sleep.await_count, 2)
        command = agent.agent_commands[0]
        self.assertIn("opencode-ai@1.18.4", command)
        self.assertIn('installed_opencode_version="$(opencode --version', command)

    async def test_install_reraises_after_bounded_attempts(self) -> None:
        agent = _InstallHarness(failures=3)

        with (
            patch("zg_bench.agents.opencode.asyncio.sleep", new=AsyncMock()),
            self.assertRaises(NonZeroAgentExitCodeError),
        ):
            await agent.install(object())

        self.assertEqual(len(agent.agent_commands), 3)

    def test_zvec_profile_uses_the_same_resilient_adapter(self) -> None:
        self.assertTrue(issubclass(ZvecOpenCode, ResilientOpenCode))


@unittest.skipUnless(
    os.environ.get("OPENCODE_BENCHMARK_TEST_BINARY"),
    "requires pinned OpenCode binary; sends requests only to a local fake provider",
)
class OpenCodeSamplingContractTests(unittest.TestCase):
    def test_task_subagent_and_background_request_contract(self) -> None:
        requested_binary = os.environ["OPENCODE_BENCHMARK_TEST_BINARY"]
        binary = shutil.which(requested_binary) or str(
            Path(requested_binary).resolve()
        )
        self.assertEqual(
            subprocess.check_output(
                [binary, "--version"], text=True, timeout=10
            ).strip(),
            runner.OPENCODE_VERSION,
        )
        suite = runner.load_suite("swe-qa-bench", tier="smoke")
        cases = (
            ("custom-openai/glm-5.2", True),
            ("aliyun-glm-5.2", True),
            # Prove that neither provider defaults nor the fixture itself hide
            # web tools: removing the benchmark denies must expose both tools.
            ("custom-openai/glm-5.2", False),
        )
        for model, disable_web_tools in cases:
            with (
                self.subTest(model=model, disable_web_tools=disable_web_tools),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                # macOS /var is a symlink to /private/var. Match the runtime's
                # canonical cwd so a local child read is not an external path.
                root = Path(temp_dir).resolve()
                corpus = root / "corpus"
                corpus.mkdir()
                (corpus / "README.md").write_text("The fixture value is 42.\n")
                requests: list[dict[str, Any]] = []
                child_requests: list[dict[str, Any]] = []

                class FakeProvider(BaseHTTPRequestHandler):
                    def log_message(self, *_args: Any) -> None:
                        pass

                    def do_POST(self) -> None:
                        body = json.loads(
                            self.rfile.read(int(self.headers["Content-Length"]))
                        )
                        requests.append(body)
                        is_child = bool(self.headers.get("x-parent-session-id"))
                        if is_child:
                            child_requests.append(body)
                        task = bool(body.get("tools"))
                        after_tool = any(
                            message.get("role") == "tool"
                            for message in body.get("messages", [])
                        )
                        if task and not after_tool and not is_child:
                            delta = {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "id": f"delegate-{agent}",
                                        "type": "function",
                                        "function": {
                                            "name": "task",
                                            "arguments": json.dumps(
                                                {
                                                    "description": "Read local fixture value",
                                                    "prompt": "Read README.md and report its fixture value.",
                                                    "subagent_type": agent,
                                                }
                                            ),
                                        },
                                    }
                                    for index, agent in enumerate(("general", "explore"))
                                ],
                            }
                            finish = "tool_calls"
                        elif task and not after_tool:
                            delta = {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "read-fixture",
                                        "type": "function",
                                        "function": {
                                            "name": "read",
                                            "arguments": json.dumps(
                                                {"filePath": str(corpus / "README.md")}
                                            ),
                                        },
                                    }
                                ],
                            }
                            finish = "tool_calls"
                        else:
                            delta = {
                                "role": "assistant",
                                "content": "The fixture value is 42.",
                            }
                            finish = "stop"
                        envelope = {
                            "id": "fixture",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": body["model"],
                        }
                        chunks = [
                            {
                                **envelope,
                                "choices": [
                                    {"index": 0, "delta": delta, "finish_reason": None}
                                ],
                            },
                            {
                                **envelope,
                                "choices": [
                                    {"index": 0, "delta": {}, "finish_reason": finish}
                                ],
                                "usage": {
                                    "prompt_tokens": 10,
                                    "completion_tokens": 5,
                                    "total_tokens": 15,
                                },
                            },
                        ]
                        payload = (
                            "".join(
                                "data: " + json.dumps(chunk) + "\n\n"
                                for chunk in chunks
                            )
                            + "data: [DONE]\n\n"
                        ).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)

                server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
                server.daemon_threads = True
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    command = runner.build_harbor_command(
                        suite,
                        profile="baseline",
                        agent="opencode",
                        model=model,
                        job_name="wire-sampling-test",
                    )
                    config = json.loads(
                        next(
                            value.removeprefix("opencode_config=")
                            for value in command
                            if value.startswith("opencode_config=")
                        )
                    )
                    provider = next(iter(config["provider"].values()))
                    provider["options"]["baseURL"] = (
                        f"http://127.0.0.1:{server.server_port}/v1"
                    )
                    config.update(autoupdate=False, share="disabled", lsp=False)
                    if not disable_web_tools:
                        for permissions in (
                            config["permission"],
                            *(agent["permission"] for agent in config["agent"].values()),
                        ):
                            for tool in ("webfetch", "websearch"):
                                permissions.pop(tool)
                    for agent in ("build", "general", "explore"):
                        config["agent"][agent]["steps"] = 3
                    config_path = root / "opencode.json"
                    config_path.write_text(json.dumps(config))
                    env = {
                        "PATH": os.environ.get("PATH", os.defpath),
                        "HOME": str(root),
                        "LANG": "en_US.UTF-8",
                        "OPENAI_API_KEY": "offline-fixture-not-a-real-key",
                        "OPENCODE_CONFIG": str(config_path),
                        "OPENCODE_DISABLE_MODELS_FETCH": "true",
                        "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
                        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true",
                        # websearch is otherwise absent for these providers.
                        "OPENCODE_ENABLE_EXA": "true",
                        "XDG_CONFIG_HOME": str(root / "config"),
                        "XDG_DATA_HOME": str(root / "data"),
                        "XDG_STATE_HOME": str(root / "state"),
                        "XDG_CACHE_HOME": str(root / "cache"),
                    }
                    result = subprocess.run(
                        [
                            binary,
                            "--model",
                            command[command.index("--model") + 1],
                            "run", "--format", "json",
                            "--dangerously-skip-permissions", "--",
                            "Read README.md and report its fixture value.",
                        ],
                        env=env,
                        cwd=corpus,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    task_requests = [body for body in requests if body.get("tools")]
                    self.assertEqual(
                        len(task_requests), 6, result.stdout + result.stderr
                    )
                    self.assertEqual(
                        len([body for body in child_requests if body.get("tools")]),
                        4,
                        "Expected a read and continuation for both general and explore",
                    )
                    self.assertGreater(
                        len(requests), len(task_requests),
                        "Expected a title/summary request",
                    )
                    for request in requests:
                        self.assertEqual(request["model"], "glm-5.2")
                        self.assertEqual(request.get("temperature"), 0)
                        self.assertEqual(request.get("seed"), 42)
                        self.assertIs(request.get("enable_thinking"), True)
                        self.assertEqual(request.get("reasoning_effort"), "high")
                        self.assertNotIn("reasoningEffort", request)
                    for request in task_requests:
                        tool_names = {
                            tool["function"]["name"] for tool in request["tools"]
                        }
                        self.assertTrue({"read", "grep", "glob"} <= tool_names)
                        for tool in ("webfetch", "websearch"):
                            self.assertEqual(
                                tool in tool_names,
                                not disable_web_tools,
                                f"Unexpected {tool} availability: {sorted(tool_names)}",
                            )
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)
