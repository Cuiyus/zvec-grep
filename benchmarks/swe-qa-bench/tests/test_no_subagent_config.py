"""The ablation changes delegation access, not model options or ZG setup."""
import copy
import os
import unittest
from unittest.mock import patch

from zg_bench.engines.opencode.config import build_opencode_config
from zg_bench.engines.registry import resolve_opencode_model


class NoSubagentConfigTests(unittest.TestCase):
    def test_only_task_permission_changes_in_both_profiles(self):
        model = resolve_opencode_model("custom-openai/glm-5.2").configuration
        for profile in ("baseline", "zvec-grep"):
            with self.subTest(profile=profile):
                with patch.dict(os.environ, {"ZG_BENCH_DISABLE_SUBAGENTS": "0"}):
                    original = build_opencode_config(model, profile=profile)
                with patch.dict(os.environ, {"ZG_BENCH_DISABLE_SUBAGENTS": "1"}):
                    disabled = build_opencode_config(model, profile=profile)
                restored = copy.deepcopy(disabled)
                self.assertEqual(restored["permission"].pop("task"), "deny")
                self.assertEqual(len(restored["agent"]), 7)
                for agent in restored["agent"].values():
                    self.assertEqual(agent["permission"].pop("task"), "deny")
                self.assertEqual(restored, original)
                self.assertEqual("mcp" in disabled, profile == "zvec-grep")

    def test_invalid_value_cannot_silently_leave_delegation_enabled(self):
        model = resolve_opencode_model("custom-openai/glm-5.2").configuration
        with patch.dict(os.environ, {"ZG_BENCH_DISABLE_SUBAGENTS": "true"}):
            with self.assertRaisesRegex(ValueError, "must be 0 or 1"):
                build_opencode_config(model, profile="baseline")


if __name__ == "__main__":
    unittest.main()
