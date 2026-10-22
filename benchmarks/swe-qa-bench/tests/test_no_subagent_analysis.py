"""Missing/retry evidence must never certify the no-subagent treatment."""
import json
from pathlib import Path
import tempfile
import unittest

from zg_bench.agents.opencode_usage import summarize_session_usage
from zg_bench.no_subagent_audit import ARMS, BUILTIN_AGENTS, DENIED_ARMS, audit
from zg_bench.no_subagent_summary import summarize


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def attempt(root, arm, rep=1, *, retry=False, child=False, failed=False):
    base = root / (f'{arm}/runs' if arm in ('baseline_allowed', 'baseline') else f'runs/{arm}') / f'r{rep}' / 'job'
    name = f'task__{arm}{rep}'
    directory = base / (f'.retry-history/{name}/attempt-1' if retry else name)
    permissions = {'websearch': 'deny', 'webfetch': 'deny'}
    if arm in DENIED_ARMS:
        permissions['task'] = 'deny'
    config = dict(trial_name=name, task={}, agent={'kwargs': {'opencode_config': {
        'permission': dict(permissions),
        'agent': {key: {'permission': dict(permissions)} for key in BUILTIN_AGENTS}}}})
    write(directory / 'config.json', config)
    identity = name + ('-failed' if retry else '-current')
    write(directory / 'result.json', dict(id=identity, trial_name=name, config=config, finished_at='2026-10-22T00:00:00Z',
                                          exception_info={'exception_type': 'TimeoutError'} if failed else None))
    snapshot = [{'kind': 'session', 'data': {'id': 'root', 'parent_id': None}},
                {'kind': 'message', 'data': dict(id='msg', session_id='root', provider_id='custom-openai',
                                               model_id='glm-5.2', cost_usd=0, input=2, output=3, reasoning=7,
                                               cache_read=10, cache_write=0, tokens_present=True,
                                               finished=True, has_error=False)}]
    if child:
        snapshot += [{'kind': 'session', 'data': {'id': 'child', 'parent_id': 'root'}},
                     {'kind': 'tool', 'data': dict(id='tool', name='task', session_id='root', message_id='msg',
                                                 status='completed', child_session_id='child')},
                     {'kind': 'message', 'data': dict(id='child-msg', session_id='child', provider_id='custom-openai',
                                                    model_id='glm-5.2', cost_usd=0, input=5, output=2, reasoning=3,
                                                    cache_read=0, cache_write=0, tokens_present=True,
                                                    finished=True, has_error=False)}]
    write(directory / 'agent/opencode-session-snapshot.json', snapshot)
    usage = summarize_session_usage(snapshot, 'root')
    write(directory / 'agent/session-usage.json', usage)
    write(directory / 'agent/trajectory.json', {'steps': []})
    history = {'schema_version': 1, 'max_retries': 2, 'trials': {name: [
        {'attempt': 1, 'trial_id': name + '-current', 'exception_type': None, 'archive': None}]}}
    if retry:
        history['trials'][name] = [
            {'attempt': 1, 'trial_id': identity, 'exception_type': 'TimeoutError', 'archive': f'{name}/attempt-1'},
            {'attempt': 2, 'trial_id': name + '-current', 'exception_type': None, 'archive': None}]
    write(base / '.retry-history/history.json', history)
    return directory, usage


def clean(root, reps=1):
    for arm in ARMS:
        for rep in range(1, reps + 1):
            attempt(root, arm, rep)
    return audit(root, len(ARMS) * reps)


def report(root, reps=1, task_id='astropy:38', *, child_allowed=False):
    evidence = clean(root, reps)
    if child_allowed:
        attempt(root, 'baseline_allowed', child=True)
        evidence = audit(root, len(ARMS) * reps)
    profiles = {}
    for arm in ARMS:
        trials = []
        for rep in range(1, reps + 1):
            entry = next(x for x in evidence['attempts'] if x['arm'] == arm and x['repetition'] == rep)
            path = root / entry['path']
            usage = json.loads((path / 'agent/session-usage.json').read_text())
            trials.append(dict(trial_index=rep, trial_name=entry['trial_name'], judge={'total': rep * 10},
                               metrics=dict(usage['total'], agent_wall_seconds=2, usage_scope=usage['scope'],
                                            session_usage=usage)))
        profiles[arm] = {'trial_count': len(trials), 'trials': trials}
    return dict(task_id=task_id, model='glm-5.2', profiles=profiles, provenance=dict(
        repetitions=reps, experiment={'no_subagent': {'enabled': True, 'mechanism': 'permission.task=deny',
                                                      'denied_arms': list(DENIED_ARMS), 'allowed_control': 'baseline_allowed'}},
        identities={'node': 'frozen', 'rust': 'frozen'}, harness_commit='commit', no_subagent_audit=evidence,
        execution=[dict(arm=arm, repetition=rep, returncode=0, wall_seconds_including_setup_and_retries=2)
                   for arm in ARMS for rep in range(1, reps + 1)]))


class NoSubagentAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_complete_audit_checks_all_arms_and_counts_output(self):
        tmp_path = self.root
        result = clean(tmp_path)
        assert result['complete']
        assert result['task_tool_calls'] == result['child_sessions'] == 0
        assert result['physical_attempts'] == 4
        assert result['attempts'][0]['usage']['output_tokens'] == 10

    def test_allowed_arm_preserves_optional_delegation_and_accepts_children(self):
        tmp_path = self.root
        clean(tmp_path)
        attempt(tmp_path, 'baseline_allowed', child=True)
        result = audit(tmp_path, 4)
        assert result['complete']
        assert result['task_tool_calls'] == result['child_sessions'] == 1
        allowed = result['per_arm']['baseline_allowed']
        assert allowed['complete'] and allowed['task_policy'] == 'allow'
        for arm in DENIED_ARMS:
            assert result['per_arm'][arm]['task_tool_calls'] == 0
            assert result['per_arm'][arm]['child_sessions'] == 0

    def test_allowed_arm_must_keep_original_permission_policy(self):
        tmp_path = self.root
        clean(tmp_path)
        path, _ = attempt(tmp_path, 'baseline_allowed')
        config = json.loads((path / 'config.json').read_text())
        config['agent']['kwargs']['opencode_config']['permission']['task'] = 'allow'
        write(path / 'config.json', config)
        with self.assertRaises(ValueError):
            audit(tmp_path, 4)
        assert not audit(tmp_path)['per_arm']['baseline_allowed']['complete']

    def test_retry_child_is_not_hidden_by_successful_replacement(self):
        tmp_path = self.root
        clean(tmp_path)
        attempt(tmp_path, 'baseline', retry=True, child=True, failed=True)
        with self.assertRaisesRegex(ValueError, 'audit incomplete'):
            audit(tmp_path, 4)
        evidence = json.loads((tmp_path / 'no-subagent-audit.json').read_text())
        assert evidence['retry_attempts'] == 1
        assert evidence['child_sessions'] == 1
        assert evidence['task_tool_calls'] == 1
        assert not evidence['complete']

    def test_missing_retry_capture_is_unknown_not_zero(self):
        tmp_path = self.root
        clean(tmp_path)
        path, _ = attempt(tmp_path, 'node', retry=True, failed=True)
        (path / 'agent/opencode-session-snapshot.json').unlink()
        with self.assertRaises(ValueError):
            audit(tmp_path, 4)
        result = audit(tmp_path)
        assert result['task_tool_calls'] is None
        assert result['child_sessions'] is None
        assert result['failed_attempts'] == 1

    def test_wholly_missing_retry_directory_is_detected(self):
        import shutil
        tmp_path = self.root
        clean(tmp_path)
        path, _ = attempt(tmp_path, 'baseline', retry=True, failed=True)
        shutil.rmtree(path)
        with self.assertRaises(ValueError):
            audit(tmp_path, 4)
        result = audit(tmp_path)
        assert not result['complete']
        assert any('missing_declared_attempt' in issue for issue in result['issues'])
        assert result['task_tool_calls'] is None

    def test_agent_permission_and_actual_snapshot_must_agree(self):
        tmp_path = self.root
        clean(tmp_path)
        path, _ = attempt(tmp_path, 'rust')
        result = json.loads((path / 'result.json').read_text())
        result['config']['agent']['kwargs']['opencode_config']['agent']['explore']['permission']['task'] = 'allow'
        write(path / 'result.json', result)
        with self.assertRaises(ValueError):
            audit(tmp_path, 4)
        assert any('agent_task_permission_not_deny:explore' in e
                   for row in audit(tmp_path)['attempts'] for e in row['errors'])

    def test_forged_usage_is_rejected(self):
        tmp_path = self.root
        clean(tmp_path)
        path, usage = attempt(tmp_path, 'baseline')
        usage['total']['output_tokens'] = 3
        write(path / 'agent/session-usage.json', usage)
        with self.assertRaises(ValueError):
            audit(tmp_path, 4)

    def test_summary_keeps_extreme_scores_and_zero_zg_trials(self):
        tmp_path = self.root
        value = report(tmp_path / 'raw', 3)
        for arm in ARMS:
            for trial, quality in zip(value['profiles'][arm]['trials'], (0, 10, 100)):
                trial['judge']['total'] = quality
        result = summarize([value], tmp_path / 'summary', expected_tasks=1, repetitions=3)
        assert result['complete']
        assert result['observed_samples'] == 12
        self.assertAlmostEqual(result['rows'][0]['means']['quality'], 110 / 3)
        assert result['rows'][1]['means']['zg_used'] == 0
        assert result['rows'][0]['means']['answer_output'] == 3
        assert result['rows'][0]['means']['reasoning'] == 7
        assert result['rows'][0]['means']['generated_total'] == 10

    def test_summary_separates_delegation_policy_from_zg_effect(self):
        tmp_path = self.root
        value = report(tmp_path / 'raw', child_allowed=True)
        for arm, quality in [('baseline_allowed', 20), ('baseline', 10), ('node', 15), ('rust', 5)]:
            value['profiles'][arm]['trials'][0]['judge']['total'] = quality
        result = summarize([value], tmp_path / 'summary', expected_tasks=1, repetitions=1)
        assert result['complete']
        assert result['comparisons']['subagent_availability']['quality']['delta'] == -10
        assert result['comparisons']['subagent_availability']['input_tokens']['delta'] == -5
        assert result['comparisons']['node']['quality']['delta'] == 5
        assert result['comparisons']['node']['input_tokens']['delta'] == 0
        assert result['comparisons']['rust']['quality']['delta'] == -5
        assert result['per_arm_audit']['baseline_allowed']['child_sessions'] == 1
        means = result['rows'][0]['means']
        assert means['root_input_tokens'] == 12 and means['child_input_tokens'] == 5
        assert means['child_used'] == 1 and means['task_calls'] == 1
        assert 'root_native_output_chars' in means and 'native_output_chars' not in means

    def test_summary_reports_partial_population_without_imputing(self):
        tmp_path = self.root
        value = report(tmp_path / 'raw')
        result = summarize([value], tmp_path / 'summary', expected_tasks=20, repetitions=1)
        assert not result['complete']
        assert len(result['missing_tasks']) == 19
        assert result['observed_samples'] == 4

    def test_summary_rejects_duplicate_and_wrong_protocol(self):
        tmp_path = self.root
        value = report(tmp_path / 'raw')
        with self.assertRaisesRegex(ValueError, 'Duplicate task'):
            summarize([value, value], tmp_path / 'dup', expected_tasks=2, repetitions=1)
        value['provenance']['experiment']['no_subagent']['enabled'] = False
        with self.assertRaisesRegex(ValueError, 'protocol'):
            summarize([value], tmp_path / 'invalid', expected_tasks=1, repetitions=1)

    def test_summary_failed_execution_is_partial_even_if_scores_exist(self):
        tmp_path = self.root
        value = report(tmp_path / 'raw')
        value['provenance']['execution'][0]['returncode'] = 1
        result = summarize([value], tmp_path / 'summary', expected_tasks=1, repetitions=1)
        assert not result['complete']
        assert result['execution']['failed_slots'] == 1

    def test_task_means_receive_equal_weight(self):
        tmp_path = self.root
        first = report(tmp_path / 'first', reps=2, task_id='astropy:38')
        second = report(tmp_path / 'second', reps=2, task_id='conan:19')
        # Deliberately partial second task: keep the one observed row, don't fill with zero.
        for arm in ARMS:
            second['profiles'][arm]['trials'] = second['profiles'][arm]['trials'][:1]
            second['profiles'][arm]['trial_count'] = 1
            second['profiles'][arm]['trials'][0]['judge']['total'] = 45
        result = summarize([first, second], tmp_path / 'summary', expected_tasks=2, repetitions=2)
        assert not result['complete']
        assert result['rows'][0]['means']['quality'] == 30

if __name__ == "__main__":
    unittest.main()
