from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory

from zg_bench.agents.qodercli import QODER_CONFIG_DIR, QoderCLI, read_stream_events
from zg_bench.agents.zvec_qodercli import ZvecQoderCLI


class QoderCLITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.logs_dir = Path(self.directory.name)
        self.agent = QoderCLI(
            logs_dir=self.logs_dir,
            model_name="qwen3.8-max",
            version="1.1.45",
            extra_env={
                "QODER_PERSONAL_ACCESS_TOKEN": "test-private-pat",
                "PUBLIC": "yes",
            },
        )

    def _write_events(self, events: list[dict[str, Any]]) -> None:
        (self.logs_dir / "qodercli-stream.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

    def test_auth_never_enters_execution_environment_or_command(self) -> None:
        command = self.agent._run_command("Read '$HOME'; answer precisely.")
        self.assertEqual(self.agent.extra_env, {"PUBLIC": "yes"})
        self.assertNotIn("test-private-pat", command)
        self.assertIn("--model Qwen3.8-Max", command)
        self.assertIn("--output-format stream-json", command)
        self.assertIn("--no-session-persistence", command)
        self.assertIn("trap 'rm -f /tmp/qoder-benchmark-auth/token' EXIT", command)

    async def test_auth_upload_uses_private_file_and_always_removes_it(self) -> None:
        uploaded: dict[str, Any] = {}

        async def upload(source: Path, target: str) -> None:
            uploaded["source"] = source
            uploaded["target"] = target
            uploaded["token"] = source.read_text()
            uploaded["mode"] = source.stat().st_mode & 0o777

        environment = SimpleNamespace(
            default_user="benchmark",
            upload_file=upload,
            exec=AsyncMock(),
        )
        self.agent.exec_as_root = AsyncMock()
        self.agent.exec_as_agent = AsyncMock(
            side_effect=[None, RuntimeError("CLI failed")]
        )
        with self.assertRaisesRegex(RuntimeError, "CLI failed"):
            await self.agent.run("question", environment, AgentContext())

        self.assertEqual(uploaded["token"], "test-private-pat")
        self.assertEqual(uploaded["mode"], 0o600)
        self.assertFalse(uploaded["source"].exists())
        environment.exec.assert_awaited_once_with(
            command="rm -f /tmp/qoder-benchmark-auth/token"
        )
        for call in [
            *self.agent.exec_as_root.call_args_list,
            *self.agent.exec_as_agent.call_args_list,
        ]:
            self.assertNotIn("test-private-pat", str(call))

    async def test_missing_pat_fails_without_trying_interactive_login(self) -> None:
        self.agent._personal_access_token = ""
        environment = SimpleNamespace(exec=AsyncMock(), upload_file=AsyncMock())
        with self.assertRaisesRegex(ValueError, "QODER_PERSONAL_ACCESS_TOKEN"):
            await self.agent.run("question", environment, AgentContext())
        environment.upload_file.assert_not_awaited()

    async def test_success_exit_with_error_result_is_rejected(self) -> None:
        self._write_events(
            [{"type": "result", "subtype": "error_during_execution", "is_error": True}]
        )
        self.agent._upload_auth = AsyncMock()
        self.agent.exec_as_agent = AsyncMock()
        environment = SimpleNamespace(exec=AsyncMock(), download_file=AsyncMock())
        with self.assertRaisesRegex(RuntimeError, "unsuccessful result"):
            await self.agent.run("question", environment, AgentContext())

    async def test_different_native_model_is_rejected(self) -> None:
        self._write_events(
            [
                {
                    "type": "assistant",
                    "message": {
                        "model": "other-model",
                        "content": [{"type": "text", "text": "answer"}],
                    },
                },
                {"type": "result", "subtype": "success", "result": "answer"},
            ]
        )
        self.agent._upload_auth = AsyncMock()
        self.agent.exec_as_agent = AsyncMock()
        environment = SimpleNamespace(exec=AsyncMock(), download_file=AsyncMock())
        with self.assertRaisesRegex(RuntimeError, "response model differs"):
            await self.agent.run("question", environment, AgentContext())

    async def test_final_model_evidence_is_required_when_assistant_model_is_missing(
        self,
    ) -> None:
        self.agent._upload_auth = AsyncMock()
        self.agent.exec_as_agent = AsyncMock()
        environment = SimpleNamespace(exec=AsyncMock(), download_file=AsyncMock())
        for model_usage, error in (
            ({}, "did not report"),
            ({"auto": {}}, "response model differs"),
            ({"Qwen3.8-Max": {}}, None),
        ):
            with self.subTest(model_usage=model_usage):
                self._write_events(
                    [
                        {
                            "type": "result",
                            "subtype": "success",
                            "result": "answer",
                            "modelUsage": model_usage,
                        }
                    ]
                )
                if error:
                    with self.assertRaisesRegex(RuntimeError, error):
                        await self.agent.run("question", environment, AgentContext())
                else:
                    context = AgentContext()
                    await self.agent.run("question", environment, context)
                    self.assertIs(context.metadata["token_usage_available"], False)

    def test_stream_parser_counts_tools_once_and_keeps_observations(self) -> None:
        tool_message = {
            "id": "message1",
            "model": "qwen3.8-max",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call1",
                    "name": "mcp__zvec-grep__search",
                    "input": {"query": "needle"},
                },
            ],
        }
        self._write_events(
            [
                {"type": "system", "session_id": "test-session"},
                {"type": "stream_event", "event": {"type": "content_block_delta"}},
                {"type": "assistant", "message": tool_message},
                {"type": "assistant", "message": tool_message},
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "call1",
                                "content": [{"type": "text", "text": "src/main.py"}],
                            },
                        ]
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "id": "message2",
                        "content": [{"type": "text", "text": "The answer."}],
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "The answer.",
                    "usage": {
                        "input_tokens": 125,
                        "output_tokens": 15,
                        "cache_read_input_tokens": 50,
                        "cache_creation_input_tokens": 10,
                    },
                    "modelUsage": {"qwen3.8-max": {"costUSD": 0.025}},
                },
            ]
        )
        context = AgentContext()
        self.agent.populate_context_post_run(context)
        trajectory = Trajectory.model_validate_json(
            (self.logs_dir / "trajectory.json").read_text()
        )
        self.assertEqual(trajectory.agent.name, "qodercli")
        self.assertEqual(trajectory.session_id, "test-session")
        self.assertEqual(len(trajectory.steps), 2)
        self.assertEqual(len(trajectory.steps[0].tool_calls), 1)
        self.assertEqual(
            trajectory.steps[0].observation.results[0].content, "src/main.py"
        )
        self.assertEqual(trajectory.steps[-1].message, "The answer.")
        self.assertEqual(
            context.n_input_tokens, 125
        )  # Qoder input already includes cache.
        self.assertEqual(context.n_output_tokens, 15)
        self.assertEqual(context.n_cache_tokens, 50)
        self.assertEqual(context.cost_usd, 0.025)

    def test_partial_stream_preserves_answer_without_fabricating_metrics(self) -> None:
        self._write_events(
            [
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "Partial"}]},
                }
            ]
        )
        with (self.logs_dir / "qodercli-stream.jsonl").open("a") as stream:
            stream.write('{"type":"assistant"')
        self.assertEqual(
            len(read_stream_events(self.logs_dir / "qodercli-stream.jsonl")), 1
        )
        context = AgentContext()
        self.agent.populate_context_post_run(context)
        self.assertIsNone(context.n_input_tokens)
        self.assertIsNone(context.n_output_tokens)
        self.assertIsNone(context.cost_usd)
        self.assertTrue((self.logs_dir / "trajectory.json").is_file())

    def test_native_zeroed_usage_is_unavailable_not_zero_consumption(self) -> None:
        self._write_events(
            [
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "A real model answer",
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                    "total_cost_usd": 0,
                    "total_credits": 1.5,
                }
            ]
        )
        context = AgentContext()
        self.agent.populate_context_post_run(context)
        self.assertIsNone(context.n_input_tokens)
        self.assertIsNone(context.n_output_tokens)
        self.assertIsNone(context.n_cache_tokens)
        self.assertIsNone(context.cost_usd)
        self.assertIs(context.metadata["token_usage_available"], False)
        self.assertEqual(context.metadata["qoder_total_credits"], 1.5)
        self.assertIn(
            "does not expose", context.metadata["token_usage_unavailable_reason"]
        )

    def test_invalid_usage_is_not_reclassified_as_hidden(self) -> None:
        for invalid in (-1, float("nan"), float("inf"), "oops", True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                ValueError, "invalid numeric usage"
            ):
                self.agent._trajectory(
                    [
                        {
                            "type": "result",
                            "result": "answer",
                            "usage": {
                                "input_tokens": invalid,
                                "output_tokens": invalid,
                            },
                        }
                    ]
                )
        with self.assertRaisesRegex(ValueError, "invalid numeric usage"):
            self.agent._trajectory(
                [
                    {
                        "type": "result",
                        "result": "answer",
                        "total_cost_usd": -2,
                    }
                ]
            )

    def test_subagent_ids_and_results_do_not_overwrite_parent_evidence(self) -> None:
        events = []
        for parent, tool_name, observation in (
            (None, "Bash", "root output"),
            ("delegate1", "Grep", "child output"),
        ):
            scope = {"session_id": "session", "parent_tool_use_id": parent}
            events.extend(
                [
                    {
                        **scope,
                        "type": "assistant",
                        "message": {
                            "id": "message1",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "call1",
                                    "name": tool_name,
                                    "input": {},
                                }
                            ],
                        },
                    },
                    {
                        **scope,
                        "type": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "call1",
                                    "content": observation,
                                }
                            ]
                        },
                    },
                ]
            )
        events.extend(
            [
                {"type": "result", "result": "root final answer"},
                {
                    "type": "result",
                    "parent_tool_use_id": "delegate1",
                    "result": "child final answer",
                },
            ]
        )
        trajectory = self.agent._trajectory(events)
        tools = [tool for step in trajectory.steps for tool in (step.tool_calls or [])]
        self.assertEqual([tool.function_name for tool in tools], ["Bash", "Grep"])
        self.assertEqual(len({tool.tool_call_id for tool in tools}), 2)
        self.assertEqual(
            trajectory.steps[0].observation.results[0].content, "root output"
        )
        self.assertEqual(
            trajectory.steps[1].observation.results[0].content, "child output"
        )
        self.assertEqual(trajectory.steps[-1].message, "root final answer")

    def test_treatment_uses_same_private_qoder_config_directory(self) -> None:
        agent = object.__new__(ZvecQoderCLI)
        self.assertEqual(
            agent._mcp_install_environment(), {"QODER_CONFIG_DIR": QODER_CONFIG_DIR}
        )
        self.assertNotIn("/logs", QODER_CONFIG_DIR)

    async def test_install_requires_a_pinned_release(self) -> None:
        self.agent._version = None
        with self.assertRaisesRegex(ValueError, "pinned release"):
            await self.agent.install(object())


if __name__ == "__main__":
    unittest.main()
