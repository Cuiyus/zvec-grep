"""Prove final-only recovery never turns partial failed usage into a total."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from qwen_subagent_fixtures import attempt, report, write
from zg_bench.agents.opencode_usage import summarize_session_usage
from zg_bench.qwen_subagent_audit import ARMS, audit, _tree_behavior
from zg_bench.qwen_subagent_qualification import (
    LIFECYCLE_CONTRACT, INSTALL_COMMAND, MISSING_ARTIFACTS, TIMEOUT_MESSAGE,
    qualify, verify_report_qualification,
)
from zg_bench.qwen_subagent_summary import summarize


class QwenQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'raw'

    def save_audit(self):
        try:
            audit(self.root, 3)
        except ValueError:
            pass
        return json.loads((self.root / 'no-subagent-audit.json').read_text())

    def failed(self, arm='rust', *, installation=False, child=False):
        value = report(self.root)
        meta = value['provenance']
        meta['installation_lifecycle_contract'] = copy.deepcopy(LIFECYCLE_CONTRACT)
        write(self.root / 'provenance.json', meta)
        path, _ = attempt(self.root, arm, retry=True, failed=True, child=child)
        result = json.loads((path / 'result.json').read_text())
        result.update(started_at='2026-10-23T00:00:00Z', finished_at='2026-10-23T00:30:04Z',
                      environment_setup={'started_at': '2026-10-23T00:00:00Z', 'finished_at': '2026-10-23T00:00:01Z'},
                      agent_setup={'started_at': '2026-10-23T00:00:01Z', 'finished_at': '2026-10-23T00:00:02Z'},
                      agent_execution={'started_at': '2026-10-23T00:00:02Z', 'finished_at': '2026-10-23T00:30:03Z'})
        if installation:
            for name in MISSING_ARTIFACTS:
                (path / name).unlink()
            frames = [('harbor/trial/trial.py', 'run'), ('harbor/trial/trial.py', '_prepare'),
                      ('harbor/trial/trial.py', '_setup_agent'), ('asyncio/tasks.py', 'wait_for')]
            if arm == 'rust':
                frames.append(('zg_bench/agents/zvec_grep.py', 'setup'))
            frames += [('harbor/agents/installed/base.py', 'setup'), ('zg_bench/agents/opencode.py', 'install'),
                       ('harbor/agents/installed/base.py', 'exec_as_root'), ('harbor/agents/installed/base.py', '_exec')]
            message = 'Command failed (exit 100): ' + INSTALL_COMMAND
            trace = 'Traceback (most recent call last):\n' + ''.join(
                f'  File "/frozen/{file}", line 1, in {method}\n    fixture()\n' for file, method in frames)
            trace += 'harbor.agents.installed.base.NonZeroAgentExitCodeError: ' + message + '\n'
            exception = 'NonZeroAgentExitCodeError'
            result.update(agent_execution=None, agent_result=None, verifier=None, verifier_result=None, step_results=None)
            (path / 'trial.log').write_text('Running command: ' + INSTALL_COMMAND + '\nCommand failed\n')
        else:
            snapshot = json.loads((path / MISSING_ARTIFACTS[0]).read_text())
            snapshot[1]['data']['finished'] = False
            write(path / MISSING_ARTIFACTS[0], snapshot)
            write(path / MISSING_ARTIFACTS[1], summarize_session_usage(snapshot, 'root', capture_complete=False))
            message = TIMEOUT_MESSAGE
            trace = ('Traceback (most recent call last):\n'
                     '  File "/frozen/zg_bench/agents/opencode.py", line 1, in run\n'
                     '  File "/frozen/harbor/trial/trial.py", line 2, in _run_agent_phase\n'
                     'harbor.trial.errors.AgentTimeoutError: ' + message + '\n')
            exception = 'AgentTimeoutError'
            (path / 'trial.log').write_text('Agent execution timed out\n')
        result['exception_info'] = dict(exception_type=exception, exception_message=message, exception_traceback=trace)
        write(path / 'result.json', result)
        (path / 'exception.txt').write_text(trace)
        history_path = path.parent.parent / 'history.json'
        history = json.loads(history_path.read_text())
        next(iter(history['trials'].values()))[0]['exception_type'] = exception
        write(history_path, history)
        meta['no_subagent_audit'] = self.save_audit()
        return path, value

    def test_pre_agent_failure_in_each_arm_qualifies_with_unknown_usage(self):
        for arm in ARMS:
            with self.subTest(arm=arm):
                self.root = Path(self.temp.name) / arm
                _, value = self.failed(arm, installation=True)
                q = qualify(self.root, expected_slots=3)
                self.assertTrue(q['eligible'], q['errors'])
                self.assertEqual(len(q['pre_agent_install_failures']), 1)
                self.assertEqual(q['agent_timeout_failures'], [])
                value['provenance']['final_answer_qualification'] = q
                verify_report_qualification(value)

    def test_timeout_final_answers_qualify_but_partial_usage_is_never_total(self):
        _, value = self.failed()
        audit = value['provenance']['no_subagent_audit']
        failed = next(row for row in audit['attempts'] if row['failed'])
        self.assertIsNone(failed['usage'])
        self.assertEqual(failed['observed_usage']['input_tokens'], 12)
        part = audit['per_arm']['rust']['usage']['input_tokens']
        self.assertEqual(part, {'known_subtotal': 24, 'unknown_attempts': 1, 'total': None})
        q = qualify(self.root, expected_slots=3)
        self.assertTrue(q['eligible'], q['errors'])
        self.assertFalse(q['full_attempt_audit_complete'])
        self.assertEqual(len(q['agent_timeout_failures']), 1)
        value['provenance']['final_answer_qualification'] = q
        verify_report_qualification(value)
        result = summarize([value], self.root / 'report', expected_tasks=1, repetitions=1)
        self.assertTrue(result['complete'], result['issues'])
        self.assertFalse(result['full_attempt_audit_complete'])
        self.assertIsNone(result['per_arm_audit']['rust']['physical_usage']['input_tokens']['total'])
        self.assertEqual(result['rows'][2]['means']['input_tokens'], 12)
        self.assertEqual(result['failed_attempt_categories']['agent_timeout_incomplete_capture'], 1)

    def test_timeout_missing_trajectory_is_unknown_but_corrupt_is_rejected(self):
        path, value = self.failed()
        trajectory = path / 'agent/trajectory.json'
        trajectory.unlink()
        value['provenance']['no_subagent_audit'] = self.save_audit()
        q = qualify(self.root, expected_slots=3)
        self.assertTrue(q['eligible'], q['errors'])
        self.assertEqual(q['agent_timeout_failures'][0]['missing_artifacts'], ['agent/trajectory.json'])
        value['provenance']['final_answer_qualification'] = q
        verify_report_qualification(value)
        trajectory.write_text('{')
        self.save_audit()
        self.assertFalse(qualify(self.root, expected_slots=3)['eligible'])

    def test_observed_denied_delegation_cannot_qualify_even_on_timeout(self):
        self.failed(child=True)
        q = qualify(self.root, expected_slots=3)
        self.assertFalse(q['eligible'])
        self.assertTrue(any('delegation' in error or 'permitted_incomplete' in error for error in q['errors']))

    def test_allowed_delegation_timeout_can_preserve_complete_finals(self):
        self.failed('baseline_allowed', child=True)
        q = qualify(self.root, expected_slots=3)
        self.assertTrue(q['eligible'], q['errors'])

    def test_timeout_requires_source_phase_config_and_history_agreement(self):
        for mutation in ('short_time', 'wrong_model', 'bad_history', 'missing_final', 'bad_exception', 'wrong_head', 'corrupt_snapshot'):
            with self.subTest(mutation=mutation):
                self.root = Path(self.temp.name) / mutation
                path, value = self.failed()
                if mutation in ('short_time', 'wrong_model'):
                    r = json.loads((path / 'result.json').read_text())
                    if mutation == 'short_time':
                        r['agent_execution']['finished_at'] = '2026-10-23T00:00:03Z'
                    else:
                        r['config']['agent']['model_name'] = 'custom-openai/other-model'
                    write(path / 'result.json', r)
                elif mutation == 'bad_history':
                    h = path.parent.parent / 'history.json'
                    data = json.loads(h.read_text()); next(iter(data['trials'].values()))[0]['exception_type'] = 'OtherError'
                    write(h, data)
                elif mutation == 'missing_final':
                    final = next(r for r in value['provenance']['no_subagent_audit']['attempts'] if not r['retry_history'])
                    (self.root / final['path'] / MISSING_ARTIFACTS[1]).unlink()
                elif mutation == 'bad_exception':
                    (path / 'exception.txt').write_text('different')
                elif mutation == 'wrong_head':
                    meta = json.loads((self.root / 'provenance.json').read_text()); meta['harness_commit'] = 'unknown'
                    write(self.root / 'provenance.json', meta)
                else:
                    (path / MISSING_ARTIFACTS[0]).write_text('{')
                self.save_audit()
                self.assertFalse(qualify(self.root, expected_slots=3)['eligible'])

    def test_embedded_proof_cannot_be_rebound_to_different_head_or_erased_audit(self):
        _, value = self.failed()
        value['provenance']['final_answer_qualification'] = qualify(self.root, expected_slots=3)
        for mutation in ('head', 'audit', 'proof', 'hash'):
            changed = copy.deepcopy(value)
            meta = changed['provenance'];q = meta['final_answer_qualification']
            if mutation == 'head':meta['harness_commit'] = 'e' * 40
            elif mutation == 'audit':meta['no_subagent_audit']['failed_attempts'] = 0
            elif mutation == 'proof':q['agent_timeout_failures'][0]['proof']['agent_execution'] = None
            else:q['source_sha256']['no-subagent-audit.json'] = None
            with self.assertRaises(ValueError):verify_report_qualification(changed)

    def test_tree_output_and_delegation_capture_is_known_or_explicitly_unknown(self):
        tool = dict(id='t', call_id='c', session_id='child', child_session_id='nested', name='task', status='completed',
                    input={'subagent_type': 'explore', 'prompt': 'find caller'}, output='中x', error=None)
        data = _tree_behavior({'t': tool}, 'root', None)
        self.assertEqual(data['native_output_chars'], 2)
        self.assertEqual(data['native_output_bytes'], 4)
        self.assertEqual(data['delegations'][0]['prompt'], 'find caller')
        self.assertFalse(data['delegations'][0]['root_call'])
        tool.pop('output')
        self.assertIsNone(_tree_behavior({'t': tool}, 'root', None)['native_output_chars'])

if __name__ == '__main__':
    unittest.main()
