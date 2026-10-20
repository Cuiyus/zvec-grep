"""Preserve partial rubric credit, unresolved scores and gate independence."""
import unittest
from quality_scores_v1 import score

class QualityScoreTests(unittest.TestCase):
    def setUp(self):
        self.rubric={'maximum_score':10,'criteria':[{'id':f'C{i}','points':2} for i in range(1,6)]}
    def test_nine_points_stays_nine_even_when_a_mandatory_gate_fails(self):
        points={f'C{i}':2 for i in range(1,6)};points['C4']=1
        result=score({'criterion_points':points,'source_grounded_success':False},self.rubric)
        self.assertEqual(result['score'],9)
        self.assertEqual(result['score_percent'],90)
        self.assertFalse(result['source_grounded_success'])
    def test_material_unknown_is_a_score_interval_not_zero_or_pass(self):
        points={f'C{i}':2 for i in range(1,6)};points['C4']=None
        result=score({'criterion_points':points,'criterion_score_bounds':{'C4':[1,2]},'source_grounded_success':None},self.rubric)
        self.assertIsNone(result['score']);self.assertEqual((result['score_lower'],result['score_upper']),(9,10))
        self.assertIsNone(result['source_grounded_success'])
    def test_missing_criteria_and_out_of_range_scores_rejected(self):
        for points in ({'C1':2},{f'C{i}':3 for i in range(1,6)},{f'C{i}':0.5 for i in range(1,6)}):
            with self.assertRaises(ValueError):score({'criterion_points':points},self.rubric)

if __name__=='__main__':unittest.main()
