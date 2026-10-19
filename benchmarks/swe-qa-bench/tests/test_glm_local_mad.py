"""Reporting regression for current three groups and the common six-group mask."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_three_arm_e2e as fixtures
from zg_bench import glm_local_mad as report
from zg_bench import three_arm_e2e as runner


class GlmLocalMadTests(unittest.TestCase):
    def reports(self):
        result = {}
        for index, task in enumerate(report.expected_tasks()):
            row = fixtures.ThreeArmTests().make_report()
            row['task_id'] = task
            width = index % 5 + 1
            scores = [5] + [80-width,80+width]*4 + [100]
            for arm in runner.ARMS:
                for trial, score in zip(row['profiles'][arm]['trials'], scores):
                    trial['judge']['total'] = score
                    # Profile summary must agree with evidence before validation.
                    q, rem = divmod(score,5)
                    trial['judge']['scores'] = {k:q+(i<rem) for i,k in enumerate(runner.SCORE_KEYS)}
                row['profiles'][arm] = runner.summarize_profile(row['profiles'][arm]['trials'])
            row['judge'] = runner.judge_metadata(row['profiles'])
            row['provenance']['task_image'] = dict(base_image='same',repository_commit='same',
                dockerfile_sha256='same',original_task_config_sha256='same')
            result[task] = row
        return result

    def test_single_round_common_three_group_mask_and_raw_usage(self):
        rows = self.reports()
        task = list(rows)[-1]
        trials = rows[task]['profiles']['rust']['trials']
        scores = [5]+[10,90]*4+[100]
        for trial, score in zip(trials,scores):
            trial['judge']['total']=score
            q,rem=divmod(score,5)
            trial['judge']['scores']={k:q+(i<rem) for i,k in enumerate(runner.SCORE_KEYS)}
        rows[task]['profiles']['rust']=runner.summarize_profile(trials)
        rows[task]['judge']=runner.judge_metadata(rows[task]['profiles'])
        before=copy.deepcopy(rows)
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':''}):
            result=report.summarize(list(rows.values()),Path(temp))
        self.assertEqual(result['task_filter']['excluded_task_ids'],[task])
        self.assertEqual([r['retained_count'] for r in result['rows']],[152]*3)
        self.assertEqual([r['original_count'] for r in result['rows']],[200]*3)
        self.assertEqual(result['judged_trials'],600)
        self.assertEqual(rows,before)

    def test_six_groups_share_one_mask_and_previous_data_are_immutable(self):
        previous=self.reports(); current=copy.deepcopy(previous)
        task=list(current)[-1]
        for i,trial in enumerate(current[task]['profiles']['node']['trials']):
            if 0<i<9:
                trial['judge']['total']=10 if i%2 else 90
        before=copy.deepcopy(previous)
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':''}):
            result=report.compare(previous,current,Path(temp))
        self.assertEqual(result['task_filter']['excluded_task_ids'],[task])
        self.assertEqual(set(result['task_filter']['profiles']),
                         {f'{r}:{a}' for r in ('previous','current') for a in runner.ARMS})
        self.assertEqual(previous,before)
        for row in result['task_filter']['profiles'].values():
            self.assertEqual(row['final_count'],152)

    def test_missing_tasks_cannot_be_published_as_complete(self):
        rows=self.reports()
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':''}):
            result=report.summarize(list(rows.values())[:2],Path(temp))
        self.assertFalse(result['complete'])
        self.assertEqual(len(result['missing_tasks']),18)
        self.assertEqual(result['task_filter']['status'],'unavailable_insufficient_tasks')

    def test_product_and_judge_changes_are_not_comparable(self):
        previous=self.reports(); current=copy.deepcopy(previous)
        current[next(iter(current))]['judge']['seed']=99
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError,'Judge parameters'):
            report.compare(previous,current,Path(temp))


if __name__=='__main__':
    unittest.main()
