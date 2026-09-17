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
    def test_task_and_background_requests_use_fixed_sampling(self) -> None:
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
        for model in ("custom-openai/glm-5.2", "aliyun-glm-5.2"):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                corpus = root / "corpus"
                corpus.mkdir()
                (corpus / "README.md").write_text("The fixture value is 42.\n")
                requests: list[dict[str, Any]] = []

                class FakeProvider(BaseHTTPRequestHandler):
                    def log_message(self, *_args: Any) -> None:
                        pass

                    def do_POST(self) -> None:
                        body = json.loads(
                            self.rfile.read(int(self.headers["Content-Length"]))
                        )
                        requests.append(body)
                        task = bool(body.get("tools"))
                        after_read = any(
                            message.get("role") == "tool"
                            for message in body.get("messages", [])
                        )
                        if task and not after_read:
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
                    config["permission"] = {
                        "*": "deny", "read": "allow", "grep": "allow", "glob": "allow"
                    }
                    config["agent"]["build"]["steps"] = 3
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
                            "run", "--format", "json", "--",
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
                        len(task_requests), 2, result.stdout + result.stderr
                    )
                    self.assertGreater(
                        len(requests), len(task_requests),
                        "Expected a title/summary request",
                    )
                    for request in requests:
                        self.assertEqual(request["model"], "glm-5.2")
                        self.assertEqual(request.get("temperature"), 0)
                        self.assertEqual(request.get("seed"), 42)
                        self.assertIs(request.get("enable_thinking"), False)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)
