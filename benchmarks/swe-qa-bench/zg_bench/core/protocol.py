"""SWE-QA evidence and scoring protocol, independent of execution backends."""

from __future__ import annotations

from zg_bench.core.errors import SweQaError

SCORE_KEYS = ("correctness", "completeness", "relevance", "clarity", "coherence")


JUDGE_MODELS = ("glm-5.2", "qwen3.8-max")


PROFILE_NAMES = ("baseline", "zvec-grep")


COMPARISON_KEYS = (
    "judge_delta",
    "input_token_reduction_pct",
    "toolcall_reduction_pct",
    "time_reduction_pct",
    "cost_reduction_pct",
)


JUDGE_GENERATION_METADATA_KEYS = (
    "enable_thinking",
    "reasoning_effort",
    "max_tokens",
    "response_format",
)


def judge_label(model: str) -> str:
    if model not in JUDGE_MODELS:
        raise SweQaError(f"unsupported judge model: {model}")
    return f"{model}-self-judge-v1"
