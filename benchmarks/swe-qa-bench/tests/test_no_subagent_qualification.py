"""Recovery may retain final answers, never erase unknown failed-attempt costs."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_no_subagent_analysis import attempt, clean, report, write
from zg_bench.no_subagent_audit import audit
from zg_bench.no_subagent_qualification import INSTALL_COMMAND, MISSING_ARTIFACTS, SOURCE_HEAD, qualify, verify_report_qualification
from zg_bench.no_subagent_summary import summarize


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def save_audit(self):
        try:
            audit(self.root, 4)
        except ValueError:
            pass
        return json.loads((self.root / 'no-subagent-audit.json').read_text())

    def missing_attempt(self, arm='baseline_allowed', *, retry=True, failed=True, missing=MISSING_ARTIFACTS):
        clean(self.root)
        path, _ = attempt(self.root, arm, retry=retry, failed=failed)
        for name in missing:
            (path / name).unlink()
        self.save_audit()
        return path

    def test_allowed_archived_failure_qualifies_without_mutating_false_audit(self):
        self.missing_attempt()
        before = (self.root / 'no-subagent-audit.json').read_bytes()
        result = qualify(self.root, expected_slots=4, schema_version=1)
        self.assertTrue(result['eligible'], result['errors'])
        self.assertFalse(result['full_attempt_audit_complete'])
        self.assertEqual(len(result['allowed_unknown_attempts']), 1)
        self.assertTrue(result['limitations'])
        self.assertEqual(before, (self.root / 'no-subagent-audit.json').read_bytes())

    def test_denied_failure_missing_evidence_is_rejected(self):
        self.missing_attempt('node')
        self.assertFalse(qualify(self.root, expected_slots=4, schema_version=1)['eligible'])

    def test_successful_final_missing_evidence_is_rejected(self):
        self.missing_attempt(retry=False, failed=False)
        self.assertFalse(qualify(self.root, expected_slots=4, schema_version=1)['eligible'])

    def test_allowed_unarchived_failure_is_rejected(self):
        self.missing_attempt(retry=False, failed=True)
        self.assertFalse(qualify(self.root, expected_slots=4, schema_version=1)['eligible'])

    def test_allowed_archived_nonfailure_is_rejected(self):
        path = self.missing_attempt()
        result = json.loads((path / 'result.json').read_text())
        result['exception_info'] = None
        write(path / 'result.json', result)
        self.save_audit()
        self.assertFalse(qualify(self.root, expected_slots=4, schema_version=1)['eligible'])

    def test_missing_config_or_damaged_history_cannot_qualify(self):
        for damaged in ('config', 'history'):
            with self.subTest(damaged=damaged):
                # Fresh isolated fixture each time.
                self.root = Path(self.temp.name) / damaged
                path = self.missing_attempt()
                if damaged == 'config':
                    (path / 'config.json').unlink()
                else:
                    history = next(self.root.rglob('history.json'))
                    history.write_text('{')
                self.save_audit()
                self.assertFalse(qualify(self.root, expected_slots=4, schema_version=1)['eligible'])

    def test_corrupt_existing_artifacts_are_not_equivalent_to_absence(self):
        path = self.missing_attempt()
        (path / MISSING_ARTIFACTS[0]).write_text('{')
        self.save_audit()
        self.assertFalse(qualify(self.root, expected_slots=4, schema_version=1)['eligible'])

    def test_partial_missing_set_is_not_the_approved_exception(self):
        self.missing_attempt(missing=MISSING_ARTIFACTS[:1])
        self.assertFalse(qualify(self.root, expected_slots=4, schema_version=1)['eligible'])

    def test_whole_failure_archive_missing_is_rejected(self):
        import shutil
        path = self.missing_attempt()
        shutil.rmtree(path)
        self.save_audit()
        self.assertFalse(qualify(self.root, expected_slots=4, schema_version=1)['eligible'])

    def test_failed_archive_history_exception_must_match_result(self):
        path = self.missing_attempt()
        history_path = path.parent.parent / 'history.json'
        history = json.loads(history_path.read_text())
        next(iter(history['trials'].values()))[0]['exception_type'] = 'DifferentFailure'
        write(history_path, history)
        self.save_audit()
        self.assertFalse(qualify(self.root, expected_slots=4, schema_version=1)['eligible'])

    def test_source_change_after_audit_is_rejected(self):
        path = self.missing_attempt()
        config = json.loads((path / 'config.json').read_text())
        config['agent']['kwargs']['opencode_config']['permission']['task'] = 'deny'
        write(path / 'config.json', config)
        self.assertFalse(qualify(self.root, expected_slots=4, schema_version=1)['eligible'])

    def test_summary_accepts_qualified_finals_but_preserves_unknown_and_false_audit(self):
        value = report(self.root)
        baseline = summarize([value], self.root / 'before', expected_tasks=1, repetitions=1)
        path, _ = attempt(self.root, 'baseline_allowed', retry=True, failed=True)
        for name in MISSING_ARTIFACTS:
            (path / name).unlink()
        evidence = self.save_audit()
        decision = qualify(self.root, expected_slots=4, schema_version=1, audit=evidence)
        self.assertTrue(decision['eligible'], decision['errors'])
        value['provenance']['no_subagent_audit'] = evidence
        value['provenance']['final_answer_qualification'] = decision
        verify_report_qualification(value)
        result = summarize([value], self.root / 'after', expected_tasks=1, repetitions=1)
        self.assertTrue(result['complete'])
        self.assertFalse(result['full_attempt_audit_complete'])
        self.assertEqual(result['rows'], baseline['rows'])
        self.assertEqual(result['comparisons'], baseline['comparisons'])
        self.assertIsNone(result['per_arm_audit']['baseline_allowed']['task_tool_calls'])
        self.assertFalse(value['provenance']['no_subagent_audit']['complete'])
        self.assertTrue(result['limitations'])
        value['provenance']['no_subagent_audit']['failed_attempts'] = 999
        with self.assertRaises(ValueError):
            verify_report_qualification(value)

    def test_clean_reports_remain_compatible_without_qualification(self):
        value = report(self.root)
        self.assertNotIn('final_answer_qualification', value['provenance'])
        result = summarize([value], self.root / 'summary', expected_tasks=1, repetitions=1)
        self.assertTrue(result['complete'])
        self.assertTrue(result['full_attempt_audit_complete'])
        self.assertEqual(result['limitations'], [])
        self.assertTrue(verify_report_qualification(value)['eligible'])


class InstallationQualificationTests(QualificationTests):
    """Schema 2 requires positive pre-agent evidence even for the allowed arm."""

    def setUp(self):
        super().setUp()
        self.root = Path(self.temp.name) / 'raw'

    def installation_failure(self, arm='node'):
        value = report(self.root)
        value['provenance']['harness_commit'] = SOURCE_HEAD
        write(self.root / 'provenance.json', value['provenance'])
        path, _ = attempt(self.root, arm, retry=True, failed=True)
        for name in MISSING_ARTIFACTS:
            (path / name).unlink()
        config = json.loads((path / 'config.json').read_text())
        config['agent']['name'] = ('zg_bench.agents.opencode:ResilientOpenCode' if arm.startswith('baseline')
                                   else 'zg_bench.agents.zvec_opencode:ZvecOpenCode')
        write(path / 'config.json', config)
        frames = [('harbor/trial/trial.py', 'run'), ('harbor/trial/trial.py', '_prepare'),
                  ('harbor/trial/trial.py', '_setup_agent'), ('asyncio/tasks.py', 'wait_for')]
        if arm in ('node', 'rust'):
            frames.append(('zg_bench/agents/zvec_grep.py', 'setup'))
        frames += [('harbor/agents/installed/base.py', 'setup'), ('zg_bench/agents/opencode.py', 'install'),
                   ('harbor/agents/installed/base.py', 'exec_as_root'), ('harbor/agents/installed/base.py', '_exec')]
        message = 'Command failed (exit 100): ' + INSTALL_COMMAND + '\nstdout: fixture\nstderr: None'
        trace = 'Traceback (most recent call last):\n' + ''.join(
            f'  File "/frozen/{file}", line 1, in {method}\n    fixture()\n' for file, method in frames)
        trace += 'harbor.agents.installed.base.NonZeroAgentExitCodeError: ' + message + '\n'
        result = json.loads((path / 'result.json').read_text())
        result.update(config=copy.deepcopy(config), started_at='2026-10-22T00:00:00Z', finished_at='2026-10-22T00:00:04Z',
                      environment_setup={'started_at': '2026-10-22T00:00:00Z', 'finished_at': '2026-10-22T00:00:01Z'},
                      agent_setup={'started_at': '2026-10-22T00:00:01Z', 'finished_at': '2026-10-22T00:00:03Z'},
                      agent_execution=None, agent_result=None, verifier=None, verifier_result=None, step_results=None,
                      exception_info={'exception_type': 'NonZeroAgentExitCodeError',
                                      'exception_message': message, 'exception_traceback': trace})
        write(path / 'result.json', result)
        (path / 'exception.txt').write_text(trace)
        (path / 'trial.log').write_text('Running command: ' + INSTALL_COMMAND + '\nCommand failed\n')
        history_path = path.parent.parent / 'history.json'
        history = json.loads(history_path.read_text())
        next(iter(history['trials'].values()))[0]['exception_type'] = 'NonZeroAgentExitCodeError'
        write(history_path, history)
        value['provenance']['no_subagent_audit'] = self.save_audit()
        return path, value

    def test_schema2_accepts_only_proven_installation_failure_in_each_arm(self):
        for arm in ('baseline_allowed', 'baseline', 'node', 'rust'):
            with self.subTest(arm=arm):
                self.root = Path(self.temp.name) / arm
                _, value = self.installation_failure(arm)
                before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                decision = qualify(self.root, expected_slots=4)
                self.assertTrue(decision['eligible'], decision['errors'])
                self.assertEqual(decision['schema_version'], 2)
                self.assertEqual([row['arm'] for row in decision['pre_agent_install_failures']], [arm])
                self.assertFalse(decision['full_attempt_audit_complete'])
                value['provenance']['final_answer_qualification'] = decision
                self.assertEqual(verify_report_qualification(value), decision)
                after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                self.assertEqual(before, after)

    def test_runtime_or_incomplete_proof_is_rejected_including_allowed_arm(self):
        mutations = {
            'execution_started': lambda r: r.update(agent_execution={'started_at': '2026-10-22T00:00:02Z'}),
            'missing_execution': lambda r: r.pop('agent_execution'),
            'agent_result_exists': lambda r: r.update(agent_result={}),
            'verifier_started': lambda r: r.update(verifier={'started_at': '2026-10-22T00:00:02Z'}),
            'missing_result': lambda r: r.pop('agent_result'),
            'later_command': lambda r: r['exception_info'].update(exception_message='Command failed (exit 100): opencode run'),
            'other_exit': lambda r: r['exception_info'].update(exception_message='Command failed (exit 1): ' + INSTALL_COMMAND),
            'runtime_trace': lambda r: r['exception_info'].update(exception_traceback=r['exception_info']['exception_traceback'].replace('in install\n', 'in run\n')),
            'missing_setup': lambda r: r.pop('agent_setup'),
            'bad_setup_order': lambda r: r['agent_setup'].update(started_at='2026-10-23T00:00:00Z'),
        }
        for arm in ('baseline_allowed', 'node'):
            for name, mutate in mutations.items():
                with self.subTest(arm=arm, mutation=name):
                    self.root = Path(self.temp.name) / arm / name
                    path, _ = self.installation_failure(arm)
                    result = json.loads((path / 'result.json').read_text())
                    mutate(result)
                    write(path / 'result.json', result)
                    # Keep exception.txt in agreement to test the phase rule itself.
                    (path / 'exception.txt').write_text(result['exception_info']['exception_traceback'])
                    self.save_audit()
                    self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_schema2_rejects_log_trace_config_history_or_source_head_disagreement(self):
        for name in ('extra_command', 'missing_command', 'exception_mismatch', 'missing_exception',
                     'config_mismatch', 'history_mismatch', 'wrong_source_head', 'missing_source_head'):
            with self.subTest(mutation=name):
                self.root = Path(self.temp.name) / name
                path, _ = self.installation_failure()
                if name == 'extra_command':
                    with (path / 'trial.log').open('a') as file:
                        file.write('Running command: opencode run\n')
                elif name == 'missing_command':
                    (path / 'trial.log').write_text('Command failed\n')
                elif name == 'exception_mismatch':
                    (path / 'exception.txt').write_text('Other traceback')
                elif name == 'missing_exception':
                    (path / 'exception.txt').unlink()
                elif name == 'config_mismatch':
                    config = json.loads((path / 'config.json').read_text())
                    config['agent']['name'] = 'other:Agent'
                    write(path / 'config.json', config)
                elif name == 'history_mismatch':
                    history_path = path.parent.parent / 'history.json'
                    history = json.loads(history_path.read_text())
                    next(iter(history['trials'].values()))[0]['exception_type'] = 'AgentTimeoutError'
                    write(history_path, history)
                elif name == 'wrong_source_head':
                    write(self.root / 'provenance.json', {'harness_commit': 'other-head'})
                else:
                    (self.root / 'provenance.json').unlink()
                self.save_audit()
                self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_schema2_does_not_expand_old_general_unknown_allowed_exception(self):
        self.missing_attempt()
        self.assertTrue(qualify(self.root, expected_slots=4, schema_version=1)['eligible'])
        self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_schema2_never_excuses_failed_or_missing_final_evidence(self):
        for mutation in ('failed', 'missing_artifact'):
            with self.subTest(mutation=mutation):
                self.root = Path(self.temp.name) / mutation
                _, value = self.installation_failure()
                final = next(row for row in value['provenance']['no_subagent_audit']['attempts']
                             if row['arm'] == 'node' and not row['retry_history'])
                path = self.root / final['path']
                if mutation == 'failed':
                    result = json.loads((path / 'result.json').read_text())
                    result['exception_info'] = {'exception_type': 'AgentTimeoutError'}
                    write(path / 'result.json', result)
                else:
                    (path / MISSING_ARTIFACTS[0]).unlink()
                self.save_audit()
                self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_schema2_rejects_contradictory_agent_outputs_or_a_denied_child(self):
        path, _ = self.installation_failure()
        (path / 'agent/stdout.json').write_text('{"sessionID":"unexpected"}')
        self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])
        (path / 'agent/stdout.json').unlink()
        attempt(self.root, 'baseline', child=True)
        self.save_audit()
        self.assertFalse(qualify(self.root, expected_slots=4)['eligible'])

    def test_qualified_node_summary_keeps_original_nulls_and_final_statistics(self):
        path, value = self.installation_failure()
        reference = report(Path(self.temp.name) / 'reference')
        baseline = summarize([reference], self.root / 'reference-summary', expected_tasks=1, repetitions=1)
        decision = qualify(self.root, expected_slots=4)
        value['provenance']['final_answer_qualification'] = decision
        result = summarize([value], self.root / 'summary', expected_tasks=1, repetitions=1)
        self.assertTrue(result['complete'], result['issues'])
        self.assertFalse(result['full_attempt_audit_complete'])
        self.assertEqual(result['rows'], baseline['rows'])
        self.assertEqual(result['comparisons'], baseline['comparisons'])
        self.assertIsNone(result['per_arm_audit']['node']['task_tool_calls'])
        self.assertFalse(value['provenance']['no_subagent_audit']['per_arm']['node']['complete'])
        self.assertIsNone(next(row['usage'] for row in value['provenance']['no_subagent_audit']['attempts'] if row['failed']))

    def test_embedded_schema2_proof_hash_binding_and_original_audit_are_required(self):
        _, value = self.installation_failure()
        decision = qualify(self.root, expected_slots=4)
        value['provenance']['final_answer_qualification'] = decision
        for mutation in ('proof', 'source_hash', 'lifecycle', 'original_audit', 'head', 'scope'):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(value)
                q = changed['provenance']['final_answer_qualification']
                item = q['pre_agent_install_failures'][0]
                if mutation == 'proof':
                    item['proof']['agent_execution'] = {}
                elif mutation == 'source_hash':
                    q['source_sha256'][item['path'] + '/exception.txt'] = '0' * 64
                elif mutation == 'lifecycle':
                    q['lifecycle_contract']['harbor_version'] = 'other'
                elif mutation == 'original_audit':
                    changed['provenance']['no_subagent_audit']['per_arm']['node']['task_tool_calls'] = 0
                elif mutation == 'head':
                    changed['provenance']['harness_commit'] = 'other'
                else:
                    item['arm'] = 'baseline_allowed'
                with self.assertRaises(ValueError):
                    verify_report_qualification(changed)


if __name__ == '__main__':
    unittest.main()
