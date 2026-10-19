"""Controller boundaries; full native protocol is checked by the Docker gate."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from .integration import (build_command, fixture_environment,
                          installed_guidance_loaded_once, stop_process_group)


class ControllerTests(unittest.TestCase):
    def test_guidance_all_body_required_after_exact_boundary_comment_removal(self):
        body = '## zvec-grep\n\nFirst rule.\n- Second rule.'
        installed = '<!-- ZVEC_GREP_START -->\n' + body + '\n<!-- ZVEC_GREP_END -->\n'
        self.assertTrue(installed_guidance_loaded_once(installed, 'context\n' + body + '\nend'))
        self.assertTrue(installed_guidance_loaded_once(installed, installed))
        self.assertFalse(installed_guidance_loaded_once(installed, body + '\n' + body))
        self.assertFalse(installed_guidance_loaded_once(installed, body.replace('Second rule.', 'Changed rule.')))
        self.assertFalse(installed_guidance_loaded_once(installed, body.replace('\n\n', '\n')))
        self.assertFalse(installed_guidance_loaded_once(installed, 'First rule.'))
        self.assertFalse(installed_guidance_loaded_once('<!-- ZVEC_GREP_START -->\n<!-- ZVEC_GREP_END -->', ''))
        internal = body + '\n<!-- internal -->'
        self.assertFalse(installed_guidance_loaded_once(internal, body))

    def test_uninspectable_group_never_claims_cleanup(self):
        proc = Mock(pid=123456789, returncode=0)
        with patch('native_claude.integration.os.killpg', side_effect=PermissionError):
            result = stop_process_group(proc)
        self.assertFalse(result['complete'])
        self.assertEqual(result['error_type'], 'PermissionError')

    def test_provider_environment_drops_ambient_credentials_and_restores(self):
        old = dict(os.environ)
        try:
            os.environ.update(ANTHROPIC_AUTH_TOKEN='ambient-token',
                              OPENAI_API_KEY='ambient-openai', CLAUDE_CODE_USE_BEDROCK='1',
                              CLAUDE_CODE_OAUTH_TOKEN='ambient-oauth', CLAUDE_CODE_MAX_TURNS='2')
            with fixture_environment('http://127.0.0.1:1/anthropic', 'http://127.0.0.1:1/v1/embeddings', Path('/main')) as env:
                self.assertNotIn('ANTHROPIC_AUTH_TOKEN', env)
                self.assertNotIn('OPENAI_API_KEY', env)
                self.assertNotIn('CLAUDE_CODE_USE_BEDROCK', env)
                self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', env)
                self.assertNotIn('CLAUDE_CODE_MAX_TURNS', env)
                self.assertEqual(os.environ['ANTHROPIC_BASE_URL'], 'http://127.0.0.1:1/anthropic')
            self.assertEqual(os.environ['ANTHROPIC_AUTH_TOKEN'], 'ambient-token')
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_route_only_replaces_model_agent_and_adds_evidence_kwargs(self):
        original = ['harbor', 'run', '--agent', 'claude-code', '--model', 'claude-opus-5',
                    '--agent-kwarg', 'version=2.1.212', '--agent-kwarg', 'reasoning_effort=high',
                    '--agent-kwarg', 'max_budget_usd=4.0']
        seen = []
        def builder(suite, **kwargs):
            seen.append(kwargs)
            return list(original)
        runner = SimpleNamespace(build_harbor_command=builder)
        cache = SimpleNamespace(zvec_grep_package=None, zvec_grep_package_sha256=None)
        cmd = build_command(runner, None, 'baseline', Path('/prepared'), Path('/output'), cache, 'http://local/embeddings')
        self.assertEqual(seen[0]['agent'], 'claude-code')
        self.assertEqual(seen[0]['model'], 'claude-opus-5')
        expected = list(original)
        expected[3], expected[5] = 'native_claude.agents:QABaseline', 'qwen3.8-max'
        self.assertEqual(cmd[:len(expected)], expected)
        self.assertEqual(len(cmd) - len(expected), 4)

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX process groups')
    def test_cleanup_when_leader_exited_but_descendant_remains(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / 'child'
            script = ('import os,time,pathlib,signal\n'
                      'pid=os.fork()\n'
                      'if pid: os._exit(0)\n'
                      'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                      f'pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n'
                      'time.sleep(60)\n')
            proc = subprocess.Popen([sys.executable, '-c', script], start_new_session=True)
            try:
                proc.wait(timeout=3)
                deadline = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                child = int(marker.read_text())
                stop_process_group(proc)
                state = subprocess.check_output(['ps', '-o', 'stat=', '-p', str(child)], text=True).strip() if subprocess.run(['ps', '-p', str(child)], stdout=subprocess.DEVNULL).returncode == 0 else ''
                self.assertTrue(not state or state.startswith('Z'), state)
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


if __name__ == '__main__':
    unittest.main()
