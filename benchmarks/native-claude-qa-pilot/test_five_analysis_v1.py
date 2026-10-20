"""Verify raw paired effects, score-conditioned sensitivity and missingness."""
import copy
import unittest
import five_analysis_v1 as analysis

def cases():
    # Synthetic data only, never an actual solver answer or benchmark result.
    result=[]
    for task in analysis.TASKS:
        profiles={}
        for p in analysis.PROFILES:
            rows=[]
            for index in range(1,11):
                value=1000 if index==1 else 10 if index==10 else 100
                rows.append({'trial_index':index,'judge':{'total':10},'quality_pass':True,
                    'execution_and_scope_valid':True,'reported_metric_eligible':True,
                    'metrics':{'input_tokens':value,'output_tokens':10,'tool_calls':1,'agent_wall_seconds':value,'outer_slot_seconds':value}})
            profiles[p]={'trials':rows}
        result.append({'task_id':task,'profiles':profiles})
    return result

class FiveAnalysisTests(unittest.TestCase):
    def test_primary_and_paired_robust_keep_zero_resource_effect_despite_score_rank_selection(self):
        sample=cases();base=[0]+[9]*8+[10];zg=[9,0]+[9]*6+[10,9]
        for case in sample:
            for name,scores in (('baseline',base),('zvec-grep',zg)):
                for row,value in zip(case['profiles'][name]['trials'],scores):row['judge']['total']=value
        before=copy.deepcopy(sample);report=analysis.analyze(sample,resamples=20)
        overall=report['estimates']['all_tasks']
        self.assertEqual(overall['raw']['comparison']['input_reduction_pct'],0)
        self.assertLess(overall['pr261_score_trimmed']['comparison']['input_reduction_pct'],-100)
        self.assertEqual(overall['paired_robust']['input_winsorized_mean_reduction_pct'],0)
        self.assertEqual(report['pointwise_intervals']['all_tasks']['raw']['input_reduction_pct']['lower'],0)
        self.assertEqual(report['estimates']['q001']['raw']['profiles']['baseline']['score_median'],9)
        self.assertEqual(sample,before)

    def test_unknown_quality_retains_all_rows_and_resource_inference_without_zero_filling(self):
        sample=cases();sample[0]['profiles']['zvec-grep']['trials'][0]['judge']={'total':None,'lower':9,'upper':10}
        report=analysis.analyze(sample,resamples=20);overall=report['estimates']['all_tasks']
        self.assertIsNone(overall['raw']['profiles']['zvec-grep']['score_mean'])
        self.assertIsNone(report['estimates']['q001']['raw']['profiles']['zvec-grep']['score_median'])
        self.assertAlmostEqual(overall['raw']['profiles']['zvec-grep']['score_mean_lower'],9.98)
        self.assertEqual(overall['raw']['profiles']['zvec-grep']['score_mean_upper'],10)
        self.assertEqual(report['scheduled_solver_rows'],100)
        self.assertEqual(report['pointwise_intervals']['all_tasks']['raw']['input_reduction_pct']['status'],'available')
        self.assertIsNone(overall['pr261_score_trimmed'])
        self.assertEqual(report['pr261_compatibility']['status'],'unavailable_incomplete_original_scores_or_metrics')

    def test_missing_planned_row_is_not_silently_dropped(self):
        sample=cases();sample[4]['profiles']['baseline']['trials'].pop()
        with self.assertRaisesRegex(ValueError,'all_ten_planned_rows'):analysis.analyze(sample,resamples=2)

    def test_invalid_scope_stays_visible_instead_of_disappearing_with_a_trim(self):
        sample=cases();sample[0]['profiles']['baseline']['trials'][0]['execution_and_scope_valid']=False
        report=analysis.analyze(sample,resamples=2)
        self.assertEqual(report['validity_counts']['q001']['baseline']['execution_and_scope_valid'],{'true':9,'false':1,'unknown':0})
        self.assertEqual(report['estimates']['q001']['raw']['profiles']['baseline']['n'],10)

if __name__=='__main__':unittest.main()
