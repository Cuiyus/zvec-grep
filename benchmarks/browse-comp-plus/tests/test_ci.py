from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zg_bench.ci import (
    _contains_secret,
    configure_auth,
    export_evidence,
    secret_values,
    verify_model,
)
from zg_bench.config import load_config
from zg_bench.runner import load_suite


class CIIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "codex-home"
        self.artifacts = self.root / "artifacts"
        self.output = self.root / "evidence"
        self.manifest = self.root / "candidate.json"
        self.manifest.write_text('{"commit":"candidate-sha"}')
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def write(self, relative, content):
        path = self.artifacts / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def export(self):
        export_evidence(self.artifacts, self.output, self.home, self.manifest)

    def configure(self):
        os.environ.update(
            ZVEC_GREP_API_KEY="embedding-secret",
            ZVEC_GREP_ENDPOINT="https://example.test",
        )

    @patch("zg_bench.ci.subprocess.run")
    def test_auth_json_is_private_and_masks_nested_tokens(self, run):
        self.configure()
        os.environ["CODEX_AUTH_JSON"] = json.dumps(
            {"tokens": {"access_token": "test-token", "refresh_token": "refresh-token"}}
        )
        run.return_value.stdout = "--ignore-rules --ephemeral --output-schema"
        with contextlib.redirect_stdout(io.StringIO()) as output:
            configure_auth(self.home)
        self.assertEqual((self.home / "auth.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.home.stat().st_mode & 0o777, 0o700)
        self.assertIn("::add-mask::test-token", output.getvalue())
        self.assertIn("::add-mask::refresh-token", output.getvalue())
        self.assertEqual(run.call_count, 1)

    @patch("zg_bench.ci.subprocess.run")
    def test_api_key_is_sent_via_stdin_to_isolated_login(self, run):
        self.configure()
        os.environ["OPENAI_API_KEY"] = "test-api-key"

        def command(args, **kwargs):
            if "--help" in args:
                return subprocess.CompletedProcess(
                    args, 0, "--ignore-rules --ephemeral --output-schema"
                )
            self.assertNotIn("test-api-key", args)
            self.assertEqual(kwargs["input"], "test-api-key\n")
            self.assertEqual(kwargs["env"]["CODEX_HOME"], str(self.home))
            (self.home / "auth.json").write_text('{"OPENAI_API_KEY":"test-api-key"}')
            return subprocess.CompletedProcess(args, 0)

        run.side_effect = command
        configure_auth(self.home)
        self.assertEqual(run.call_count, 2)
        self.assertEqual((self.home / "auth.json").stat().st_mode & 0o777, 0o600)

    def test_missing_or_ambiguous_credentials_fail_before_commands(self):
        self.configure()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            configure_auth(self.home)
        os.environ.update(OPENAI_API_KEY="key", CODEX_AUTH_JSON="{}")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            configure_auth(self.home)

    @patch("zg_bench.ci.subprocess.run")
    def test_incompatible_codex_fails_before_corpus_preparation(self, run):
        self.configure()
        os.environ["OPENAI_API_KEY"] = "key"
        run.return_value.stdout = "--ephemeral"
        with self.assertRaisesRegex(ValueError, "--ignore-rules"):
            configure_auth(self.home)
        self.assertFalse(self.home.exists())

    def test_export_keeps_partial_evidence_and_excludes_auth_and_corpus(self):
        self.write("runs/run-1/run.json", '{"status":"partial"}')
        self.write("runs/run-1/report/summary.md", "# Partial report\n")
        self.write("runs/run-1/cases/628/baseline/result.json", '{"status":"failed"}')
        self.write("runs/run-1/evaluation/summary.json", '{"status":"partial"}')
        self.write("logs/index.stderr.log", "index diagnostic")
        self.write("runtime/zvec-home/config.json", "credential")
        self.write("source/data.jsonl", "gold data")
        self.write("workspaces/a/doc.md", "corpus")
        credential = self.root / "auth.json"
        credential.write_text('{"tokens":{"access_token":"credential"}}')
        profile = self.artifacts / "runs/run-1/profiles/baseline/codex-home"
        profile.mkdir(parents=True)
        (profile / "auth.json").symlink_to(credential)
        summary = self.root / "step-summary.md"
        os.environ.update(
            GITHUB_STEP_SUMMARY=str(summary), BROWSE_CANDIDATE_COMMIT="candidate-sha"
        )
        self.export()
        self.assertEqual(
            (self.output / "runs/run-1/report/summary.md").read_text(),
            "# Partial report\n",
        )
        self.assertTrue((self.output / "runs/run-1/evaluation/summary.json").is_file())
        self.assertTrue((self.output / "logs/index.stderr.log").is_file())
        for name in ("profiles", "auth.json", "runtime", "source", "workspaces"):
            self.assertFalse(any(path.name == name for path in self.output.rglob("*")))
        self.assertIn("candidate-sha", summary.read_text())
        self.assertIn("Partial report", summary.read_text())

    def test_secret_leak_fails_closed_and_does_not_publish_summary(self):
        os.environ["ZVEC_GREP_API_KEY"] = "secret-from-provider"
        summary = self.root / "summary.md"
        os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
        self.write("runs/run-1/cases/628/events.jsonl", "secret-from-provider")
        with self.assertRaisesRegex(ValueError, "credential material"):
            self.export()
        self.assertFalse(self.output.exists())
        self.assertFalse(summary.exists())

    def test_nested_and_refreshed_tokens_are_scanned(self):
        os.environ["CODEX_AUTH_JSON"] = '{"tokens":{"access_token":"old-token"}}'
        self.home.mkdir()
        (self.home / "auth.json").write_text(
            '{"tokens":{"access_token":"new-token","refresh_token":"new-refresh"}}'
        )
        self.assertTrue(
            {b"old-token", b"new-token", b"new-refresh"} <= secret_values(self.home)
        )
        self.write("runs/run-1/cases/628/events.jsonl", "new-token")
        with self.assertRaisesRegex(ValueError, "credential material"):
            self.export()

    def test_secret_matching_crosses_read_boundaries(self):
        path = self.write("large.log", "x" * (1024 * 1024 - 3) + "my-secret")
        self.assertTrue(_contains_secret(path, {b"my-secret"}))
        self.assertFalse(_contains_secret(path, {b"absent"}))

    def test_refreshed_profile_credentials_are_scanned(self):
        self.write(
            "runs/run-1/profiles/baseline/codex-home/auth.json",
            '{"tokens":{"access_token":"profile-refreshed-token"}}',
        )
        self.write("runs/run-1/cases/628/events.jsonl", "profile-refreshed-token")
        with self.assertRaisesRegex(ValueError, "credential material"):
            self.export()

    @patch("zg_bench.ci.subprocess.run")
    def test_model_preflight_preserves_configured_model(self, run):
        def command(args, **kwargs):
            self.assertEqual(args[args.index("--model") + 1], load_config().run.model)
            self.assertEqual(kwargs["timeout"], 180)
            Path(args[args.index("-o") + 1]).write_text("OK")
            return subprocess.CompletedProcess(args, 0)

        run.side_effect = command
        verify_model()

    @patch("zg_bench.ci.subprocess.run")
    def test_model_preflight_failure_does_not_echo_provider_output(self, run):
        run.return_value.returncode = 1
        run.return_value.stderr = "unexpected credential in provider error"
        with self.assertRaisesRegex(ValueError, "verify credential access") as error:
            verify_model()
        self.assertNotIn("unexpected credential", str(error.exception))

    def test_symlinked_evidence_is_rejected(self):
        path = self.write("runs/run-1/cases/628/response.md", "answer")
        path.unlink()
        path.symlink_to(self.manifest)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.export()
        self.assertFalse(self.output.exists())

    def test_auth_file_in_an_allowed_directory_is_rejected(self):
        self.write("runs/run-1/cases/628/auth.json", "unknown credentials")
        with self.assertRaisesRegex(ValueError, "credential material"):
            self.export()

    def test_prepare_failure_still_exports_diagnostics(self):
        self.write("logs/index.stderr.log", "failed to build index")
        summary = self.root / "summary.md"
        os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
        self.export()
        self.assertIn("No paired report", summary.read_text())
        self.assertTrue((self.output / "logs/index.stderr.log").is_file())

    def test_ci_and_smoke_preserve_the_existing_protocol(self):
        config = load_config()
        self.assertEqual(config.run.trials_per_case, 3)
        queries = [
            {"query_id": value} for value in ("628", "700", "966", "827", "241", "770")
        ]
        self.assertEqual(
            load_suite("ci", queries).query_ids, ("628", "700", "966", "827", "241")
        )
        self.assertEqual(load_suite("smoke", queries).query_ids, ("770",))


if __name__ == "__main__":
    unittest.main()
