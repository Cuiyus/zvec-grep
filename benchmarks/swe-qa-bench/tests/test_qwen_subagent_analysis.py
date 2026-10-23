from qwen_subagent_fixtures import *

class QwenSubagentAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_complete_audit_checks_all_arms_and_counts_output(self):
        tmp_path = self.root
        result = clean(tmp_path)
        assert result['complete']
        assert result['task_tool_calls'] == result['child_sessions'] == 0
        assert result['physical_attempts'] == 3
        assert result['attempts'][0]['usage']['output_tokens'] == 10

    def test_allowed_arm_preserves_optional_delegation_and_accepts_children(self):
        tmp_path = self.root
        clean(tmp_path)
        attempt(tmp_path, 'baseline_allowed', child=True)
        result = audit(tmp_path, 3)
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
            audit(tmp_path, 3)
        assert not audit(tmp_path)['per_arm']['baseline_allowed']['complete']

    def test_retry_child_is_not_hidden_by_successful_replacement(self):
        tmp_path = self.root
        clean(tmp_path)
        attempt(tmp_path, 'baseline', retry=True, child=True, failed=True)
        with self.assertRaisesRegex(ValueError, 'audit incomplete'):
            audit(tmp_path, 3)
        evidence = json.loads((tmp_path / 'no-subagent-audit.json').read_text())
        assert evidence['retry_attempts'] == 1
        assert evidence['child_sessions'] is None
        failed = next(row for row in evidence['attempts'] if row['failed'])
        assert failed['observed_child_sessions'] == 1
        assert failed['observed_task_tool_calls'] == 1
        assert not evidence['complete']

    def test_missing_retry_capture_is_unknown_not_zero(self):
        tmp_path = self.root
        clean(tmp_path)
        path, _ = attempt(tmp_path, 'rust', retry=True, failed=True)
        (path / 'agent/opencode-session-snapshot.json').unlink()
        with self.assertRaises(ValueError):
            audit(tmp_path, 3)
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
            audit(tmp_path, 3)
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
            audit(tmp_path, 3)
        assert any('agent_task_permission_not_deny:explore' in e
                   for row in audit(tmp_path)['attempts'] for e in row['errors'])

    def test_forged_usage_is_rejected(self):
        tmp_path = self.root
        clean(tmp_path)
        path, usage = attempt(tmp_path, 'baseline')
        usage['total']['output_tokens'] = 3
        write(path / 'agent/session-usage.json', usage)
        with self.assertRaises(ValueError):
            audit(tmp_path, 3)

    def test_summary_keeps_extreme_scores_and_zero_zg_trials(self):
        tmp_path = self.root
        value = report(tmp_path / 'raw', 3)
        for arm in ARMS:
            for trial, quality in zip(value['profiles'][arm]['trials'], (0, 10, 100)):
                trial['judge']['total'] = quality
        result = summarize([value], tmp_path / 'summary', expected_tasks=1, repetitions=3)
        assert result['complete']
        assert result['observed_samples'] == 9
        self.assertAlmostEqual(result['rows'][0]['means']['quality'], 110 / 3)
        assert result['rows'][1]['means']['zg_used'] == 0
        assert result['rows'][0]['means']['answer_output'] == 3
        assert result['rows'][0]['means']['reasoning'] == 7
        assert result['rows'][0]['means']['generated_total'] == 10

    def test_summary_separates_delegation_policy_from_zg_effect(self):
        tmp_path = self.root
        value = report(tmp_path / 'raw', child_allowed=True)
        for arm, quality in [('baseline_allowed', 20), ('baseline', 10), ('rust', 5)]:
            value['profiles'][arm]['trials'][0]['judge']['total'] = quality
        result = summarize([value], tmp_path / 'summary', expected_tasks=1, repetitions=1)
        assert result['complete']
        assert result['comparisons']['subagent_availability']['quality']['delta'] == -10
        assert result['comparisons']['subagent_availability']['input_tokens']['delta'] == -5
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
        assert result['observed_samples'] == 3

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
