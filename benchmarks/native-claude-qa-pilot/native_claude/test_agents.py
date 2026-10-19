"""No Docker or provider calls: exercise native dispatch and evidence failures."""
from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.models.agent.context import AgentContext
from harbor.trial.single_step import SingleStepTrial
from harbor.trial.trial import Trial
from zg_bench.agents.zvec_claude_code import ZvecClaudeCode
from native_claude.agents import (INVENTORY_SCRIPT, RUNTIME_PAYLOADS, QABaseline, QAZvec,
                                 compare_claude_inventory, local_log_inventory)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


SOURCE = [{'path': 'source.cc', 'bytes': 4, 'sha256': digest(b'code')}]
OBSERVED = {'files': [dict(SOURCE[0], kind='file')],
            'zg_executable_present': False, 'zg_home_present': False,
            'embedding_environment_names': [], 'mcp_config_present': False,
            'runtime': {'claude': {'version': '2.1.114 (Claude Code)'}},
            'configurations': {}}
LOGS = {'claude-code.txt': b'{"type":"result"}\n',
        'sessions/projects/-workspace/root.jsonl': b'{"uuid":"root"}\n',
        'sessions/projects/-workspace/root/subagents/agent-child.jsonl': b'{"uuid":"child"}\n'}


class Environment:
    """Merged exec output plus real file-download semantics, with partials."""
    def __init__(self, native_error=None, download_error=False, after_changed=False,
                 source_error=False, observed=None):
        self.native_error = native_error
        self.download_error = download_error
        self.after_changed = after_changed
        self.source_error = source_error
        self.observed = observed or copy.deepcopy(OBSERVED)
        self.files = {}
        self.native_calls = []
        self.log_inventory_calls = 0
        self.download_calls = []

    async def exec(self, **kwargs):
        command = kwargs['command']
        if 'qa-evidence-v2-' not in command:
            self.native_calls.append(kwargs)
            if self.native_error and 'claude --verbose' in command:
                raise self.native_error
            return SimpleNamespace(return_code=0, stdout='', stderr='')
        remote = re.search(r'/root/qa-evidence-v2-[a-f0-9]+', command)[0]
        if 'native_log_directory_missing' in command:
            self.log_inventory_calls += 1
            files = [{'path': k, 'bytes': len(v), 'sha256': digest(v)} for k, v in sorted(LOGS.items())]
            if self.after_changed and self.log_inventory_calls == 2:
                files[0]['sha256'] = '0' * 64
            data = {'root': '/logs/agent', 'files': files}
        else:
            if self.source_error:
                raise OSError('source capture failed')
            data = self.observed
        self.files.update({remote + '/stdout.json': json.dumps(data).encode(),
                           remote + '/stderr.txt': b'', remote + '/status.txt': b'0\n'})
        return SimpleNamespace(return_code=0, stdout='compose warning, not JSON', stderr=None)

    async def download_file(self, source, target):
        Path(target).write_bytes(self.files[source])

    async def download_dir(self, source, target):
        self.download_calls.append((source, Path(target)))
        for name, raw in LOGS.items():
            path = Path(target) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        if self.download_error:
            raise OSError('partial transfer')


class AgentsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifest = self.root / 'source-manifest.json'
        self.manifest.write_text(json.dumps({'files': SOURCE}))
        self.env_patch = patch.dict(os.environ, {
            'ANTHROPIC_API_KEY': 'offline-fake-key',
            'ANTHROPIC_BASE_URL': 'http://127.0.0.1:7777',
        }, clear=True)
        self.env_patch.start()

    async def asyncTearDown(self):
        self.env_patch.stop()
        self.temp.cleanup()

    def agent(self, cls=QABaseline, **kwargs):
        profile = 'zvec-grep' if cls is QAZvec else 'baseline'
        return cls(logs_dir=self.root / 'trial' / 'agent', model_name='qwen3.8-max',
                   qa_source_manifest=str(self.manifest), qa_profile=profile, **kwargs)

    def saved(self):
        return json.loads((self.root / 'trial/qa-source-audit.json').read_bytes())

    async def test_baseline_actual_native_commands_and_env_exact(self):
        common = dict(model_name='qwen3.8-max', version='2.1.114', max_turns=4,
                      thinking='disabled', reasoning_effort='high')
        native = ClaudeCode(logs_dir=self.root / 'native', **common)
        wrapper = QABaseline(logs_dir=self.root / 'trial/agent',
                            qa_source_manifest=str(self.manifest), qa_profile='baseline', **common)
        instruction = "Read the source's quoted `$literal` and explain."
        native_env, wrapper_env = Environment(), Environment()
        await native.run(instruction, native_env, AgentContext())
        context = AgentContext()
        await wrapper.run(instruction, wrapper_env, context)
        self.assertEqual(native_env.native_calls, wrapper_env.native_calls)
        self.assertEqual(len(wrapper_env.native_calls), 2)
        self.assertTrue(context.is_empty())  # No premature native conversion.
        self.assertTrue(self.saved()['valid'])
        self.assertTrue(self.saved()['native_logs_complete'])
        self.assertEqual(wrapper_env.download_calls[0][1], self.root / 'trial/qa-native-claude-logs')
        self.assertFalse((self.root / 'trial/agent').exists())
        self.assertEqual(len(self.saved()['native_logs']['files']), 3)

    async def test_treatment_actual_native_commands_and_env_exact(self):
        common = dict(logs_dir=self.root / 'trial/agent', model_name='qwen3.8-max',
                      embedding_model='local/potion-code-16m-v2', mcp_target='claude')
        native = ZvecClaudeCode(**common)
        wrapper = QAZvec(qa_source_manifest=str(self.manifest), qa_profile='zvec-grep', **common)
        observed = copy.deepcopy(OBSERVED)
        observed.update(zg_executable_present=True, zg_home_present=True, mcp_config_present=True)
        observed['runtime']['zg'] = {'payloads': {n: {'sha256': 'a' * 64} for n in RUNTIME_PAYLOADS}}
        observed['configurations'] = {'/logs/agent/sessions/' + n: {'present': True}
                                      for n in ('.claude.json', 'settings.json', 'CLAUDE.md')}
        native_env, wrapper_env = Environment(), Environment(observed=observed)
        await native.run('unchanged question', native_env, AgentContext())
        await wrapper.run('unchanged question', wrapper_env, AgentContext())
        self.assertEqual(native_env.native_calls, wrapper_env.native_calls)
        self.assertEqual(wrapper._mcp_install_environment(), native._mcp_install_environment())
        self.assertTrue(self.saved()['valid'])

    async def test_native_failure_keeps_root_and_child_logs_and_exception(self):
        error = RuntimeError('native failed')
        with self.assertRaises(RuntimeError) as found:
            await self.agent().run('question', Environment(native_error=error), AgentContext())
        self.assertIs(found.exception, error)
        audit = self.saved()
        self.assertFalse(audit['valid'])
        self.assertTrue(audit['native_logs_complete'])
        self.assertEqual(audit['native_run_error_type'], 'RuntimeError')
        self.assertGreaterEqual(audit['native_run_wall_seconds'], 0)
        self.assertEqual(len(audit['native_logs']['files']), 3)

    async def test_native_cancellation_keeps_root_and_child_logs(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.agent().run('question', Environment(native_error=asyncio.CancelledError()), AgentContext())
        self.assertTrue(self.saved()['native_logs_complete'])
        self.assertFalse(self.saved()['valid'])
        self.assertEqual(self.saved()['native_run_error_type'], 'CancelledError')

    async def test_partial_directory_failure_retained_and_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'native_claude_evidence_invalid'):
            await self.agent().run('question', Environment(download_error=True), AgentContext())
        self.assertFalse(self.saved()['native_logs_complete'])
        self.assertEqual(len(self.saved()['native_logs']['files']), 3)
        self.assertEqual(self.saved()['native_logs']['errors'], ['download:OSError'])

    async def test_mutating_logs_fail_even_if_download_succeeds(self):
        with self.assertRaisesRegex(RuntimeError, 'native_claude_evidence_invalid'):
            await self.agent().run('question', Environment(after_changed=True), AgentContext())
        self.assertFalse(self.saved()['native_logs_complete'])
        self.assertTrue(self.saved()['native_logs']['download_complete'])

    async def test_pre_source_failure_still_retains_logs_without_solver(self):
        env = Environment(source_error=True)
        with self.assertRaises(Exception):
            await self.agent().run('question', env, AgentContext())
        self.assertEqual(env.native_calls, [])
        self.assertTrue(self.saved()['native_logs_complete'])
        self.assertFalse(self.saved()['valid'])

    async def test_existing_log_snapshot_never_overwritten(self):
        wrapper = self.agent()
        destination = self.root / 'trial/qa-native-claude-logs'
        destination.mkdir(parents=True)
        (destination / 'previous').write_text('retained')
        env = Environment()
        with self.assertRaisesRegex(RuntimeError, 'native_claude_evidence_invalid'):
            await wrapper.run('question', env, AgentContext())
        self.assertEqual(env.download_calls, [])
        self.assertEqual((destination / 'previous').read_text(), 'retained')

    def test_native_setup_and_post_context_remain_inherited(self):
        self.assertIs(QABaseline.setup, ClaudeCode.setup)
        self.assertIs(QAZvec.setup, ZvecClaudeCode.setup)
        self.assertIs(QABaseline.populate_context_post_run, ClaudeCode.populate_context_post_run)
        self.assertIs(QAZvec.populate_context_post_run, ZvecClaudeCode.populate_context_post_run)

    async def test_real_harbor_sync_orders_download_before_native_population(self):
        calls = []
        class FakeTrial:
            async def _download_agent_logs(self): calls.append('download')
            def _populate_agent_context(self, context): calls.append(('populate', context))
            async def _sync_agent_output(self, target):
                await Trial._sync_agent_output(self, target)
            async def _collect_artifacts(self, **kwargs): calls.append('artifacts')
            async def _stop_agent_environment(self): calls.append('stop')
        fake = FakeTrial()
        fake.result = SimpleNamespace(agent_result=AgentContext())
        await SingleStepTrial._recover_outputs(fake)
        self.assertEqual(calls, ['download', ('populate', fake.result.agent_result), 'artifacts', 'stop'])

    def test_source_comparison_rejects_change_addition_and_baseline_mcp(self):
        observed = copy.deepcopy(OBSERVED)
        observed['files'][0]['sha256'] = '0' * 64
        observed['files'].append({'path': 'CLAUDE.md', 'kind': 'file'})
        observed['mcp_config_present'] = True
        result = compare_claude_inventory(observed, SOURCE, 'baseline')
        self.assertFalse(result['valid'])
        self.assertEqual(len(result['errors']), 3)

    def test_downloaded_symlinks_rejected(self):
        path = self.root / 'logs'
        path.mkdir()
        (path / 'escape').symlink_to(self.manifest)
        with self.assertRaisesRegex(ValueError, 'native_log_symlink'):
            local_log_inventory(path)

    def test_treatment_requires_all_four_payloads_and_native_config_files(self):
        observed = copy.deepcopy(OBSERVED)
        observed['runtime']['zg'] = {'payloads': {'bin/zg': {}}}
        result = compare_claude_inventory(observed, SOURCE, 'zvec-grep')
        self.assertFalse(result['valid'])
        self.assertIn('zg_runtime_payloads_missing', result['errors'])
        self.assertEqual(sum('configuration_missing' in e for e in result['errors']), 3)
        self.assertIn('claude_treatment_mcp_missing', result['errors'])

    def test_inventory_exec_captures_config_hash_content_and_redacts_secrets(self):
        source, home, sessions = [self.root / n for n in ('source', 'home', 'sessions')]
        for directory in (source, home, sessions): directory.mkdir()
        (source / 'source.cc').write_bytes(b'code')
        config = {'mcpServers': {'zvec_grep': {'command': 'zg', 'env': {'API_KEY': 'private'}}},
                  'apiKey': 'private', 'ordinary': 'offline-fake-key'}
        raw = json.dumps(config).encode()
        (sessions / '.claude.json').write_bytes(raw)
        (sessions / 'CLAUDE.md').write_text('guide, not instructions for this test')
        script = INVENTORY_SCRIPT.replace("pathlib.Path('/workspace')", f'pathlib.Path({str(source)!r})')
        script = script.replace('pathlib.Path.home()', f'pathlib.Path({str(home)!r})')
        script = script.replace("pathlib.Path('/logs/agent/sessions')", f'pathlib.Path({str(sessions)!r})')
        output = io.StringIO()
        with patch('shutil.which', return_value=None), contextlib.redirect_stdout(output):
            exec(compile(script, '<inventory-test>', 'exec'), {})
        result = json.loads(output.getvalue())
        captured = result['configurations'][str(sessions / '.claude.json')]
        self.assertEqual(captured['sha256'], digest(raw))
        self.assertTrue(captured['redacted'])
        self.assertEqual(captured['content']['apiKey'], '[REDACTED]')
        self.assertEqual(captured['content']['ordinary'], '[REDACTED]')
        self.assertNotIn('private', json.dumps(result))
        self.assertTrue(result['mcp_config_present'])
        self.assertEqual(result['files'], [dict(SOURCE[0], kind='file')])


if __name__ == '__main__':
    unittest.main()
