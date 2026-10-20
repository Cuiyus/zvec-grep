import copy
import json
from pathlib import Path
import sys
import unittest
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cohort
import judging
import results
import statistics_v1


class CohortBoundaryTests(unittest.TestCase):
    def test_exact_five_ten_per_profile_and_balanced_pair_order(self):
        rows = cohort.schedule()
        self.assertEqual(len(rows), 50)
        self.assertEqual(len({r['key'] for r in rows}), 50)
        for task in cohort.five.TASK_IDS:
            pairs = [r for r in rows if r['task_id'] == task]
            self.assertEqual({r['trial_index'] for r in pairs}, set(range(1, 11)))
            self.assertEqual(sum(r['profiles'][0] == 'baseline' for r in pairs), 5)
            self.assertTrue(all(set(r['profiles']) == set(cohort.PROFILES) for r in pairs))

    def test_reference_uses_only_the_existing_frozen_bank(self):
        original = cohort.load(cohort.CLAUDE / 'five-repeat-plan-v1.json')
        for task in cohort.five.TASK_IDS:
            record = judging.reference(task)
            self.assertEqual(record['frozen_rubric_sha256'], original['prepared']['tasks'][task]['rubric']['sha256'])
            q = cohort.REPO / f'benchmarks/zvec-issue-qa/tasks/{task}/instruction.md'
            self.assertEqual(cohort.sha(q), original['prepared']['tasks'][task]['question']['sha256'])
            self.assertTrue(record['reference_answer'])
        self.assertIn('C5.2', judging.reference('q005')['reference_answer'])

    def test_native_local_model_command_does_not_add_remote_embedding(self):
        main = Path(__import__('os').environ['GLM_QA_MAIN_ROOT'])
        runner = cohort.native(main)
        suite = SimpleNamespace(dataset=None, path=Path('/fixture/tasks'), tasks=['zvec-q001'],
            index_ignore_file=Path('/fixture/index.ignore'))
        for profile in cohort.PROFILES:
            cmd = runner.build_harbor_command(suite, profile=profile, agent='opencode', model=cohort.MODEL,
                jobs_dir=Path('/fixture/jobs'), job_name='test-' + profile,
                n_attempts=1, max_retries=0, embedding_model=cohort.EMBEDDING,
                embedding_endpoint=None)
            self.assertEqual(cmd[cmd.index('--model') + 1], cohort.MODEL)
            self.assertNotIn('--retry-exclude', cmd)
            self.assertFalse(any(x.startswith('embedding_endpoint=') for x in cmd))
            config = json.loads(next(x.split('=', 1)[1] for x in cmd if x.startswith('opencode_config=')))
            self.assertEqual(config['permission'], {'websearch': 'deny', 'webfetch': 'deny'})
            self.assertEqual(set(config['provider']['custom-openai']['models']), {'glm-5.2'})
            self.assertEqual('mcp' in config, profile == 'zvec-grep')

    def test_missing_original_artifacts_do_not_create_success_only_means(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); raw = root / 'raw'; raw.mkdir()
            plan = root / 'plan.json'; cohort.write(plan, {'schedule': cohort.schedule()})
            payload = results.assemble(SimpleNamespace(plan=plan, raw_root=raw))
            self.assertEqual(len(payload['cases']), 5)
            for case in payload['cases']:
                for profile in cohort.PROFILES:
                    rows = case['profiles'][profile]['trials']
                    self.assertEqual(len(rows), 10)
                    self.assertTrue(all(r['judge']['total'] is None for r in rows))
                    summary = statistics_v1.profile(rows)
                    self.assertIsNone(summary['score_mean'])
                    self.assertIsNone(summary['input_tokens'])
                    self.assertEqual(summary['score_mean_upper'], 100)

    def test_quality100_scale_and_tied_tail_selection_are_explicit(self):
        self.assertEqual(statistics_v1.score_bounds({'judge': {'total': 100}}), (100, 100, 100))
        with self.assertRaises(ValueError):
            statistics_v1.score_bounds({'judge': {'total': 101}})
        rows = [{'trial_index': i, 'judge': {'total': 100},
            'metrics': {'input_tokens': 10*i, 'output_tokens': i, 'tool_calls': i,
                'agent_wall_seconds': i, 'outer_slot_seconds': i+2}} for i in range(1, 11)]
        original = copy.deepcopy(rows)
        result = statistics_v1.legacy.profile_statistics({'trials': rows})
        self.assertEqual(result['filter']['retained_trial_indexes'], list(range(2, 10)))
        self.assertEqual(rows, original)


if __name__ == '__main__': unittest.main()
