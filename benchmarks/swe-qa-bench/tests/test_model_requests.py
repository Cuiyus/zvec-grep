"""Execution and judging must use the same declared model requests."""

from __future__ import annotations

import unittest

from zg_bench.core.protocol import JUDGE_MODELS, model_request_spec
from zg_bench.engines.judge import judge_generation_metadata, judge_temperature
from zg_bench.engines.registry import resolve_agent_model


class ModelRequestTests(unittest.TestCase):
    def test_custom_execution_and_judging_share_parameters(self) -> None:
        for model in JUDGE_MODELS:
            with self.subTest(model=model):
                execution = resolve_agent_model("opencode", f"custom-openai/{model}")
                provider = execution.opencode
                self.assertIsNotNone(provider)
                assert provider is not None
                request = model_request_spec(model)
                self.assertEqual(provider.temperature, request.temperature)
                self.assertEqual(provider.enable_thinking, request.enable_thinking)
                self.assertEqual(provider.reasoning_effort, request.reasoning_effort)
                self.assertEqual(provider.output_limit, request.max_tokens)
                self.assertEqual(judge_temperature(model), request.temperature)
                self.assertEqual(
                    judge_generation_metadata(model),
                    {
                        "enable_thinking": request.enable_thinking,
                        "reasoning_effort": request.reasoning_effort,
                        "max_tokens": request.max_tokens,
                        "response_format": None,
                    },
                )
