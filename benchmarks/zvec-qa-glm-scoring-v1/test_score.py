from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import unittest
import score


class PostrunScoreTests(unittest.TestCase):
    def test_all100_missing_positions_are_retained_without_model_calls(self):
        with TemporaryDirectory() as name:
            root = Path(name); plan = root / 'plan.json'
            score.cohort.write(plan, {'schedule': score.cohort.schedule()})
            records, found = score.load_records(root, plan)
            self.assertEqual(len(records), 100); self.assertEqual(found, {})
            def forbidden(record):
                raise AssertionError('no answer must never invoke a model')
            result = score.evaluate(records, forbidden)
            self.assertEqual(len(result), 100)
            self.assertTrue(all(r['status'] == 'unavailable_no_complete_answer' for r in result))

    def test_uniform_wave_ignores_initial_score_and_retains_new_failures(self):
        with TemporaryDirectory() as name:
            root = Path(name); plan = root / 'plan.json'
            pairs = score.cohort.schedule(); score.cohort.write(plan, {'schedule': pairs})
            pair = pairs[0]; path = root / 'original/collection.json'; answer = 'original retained answer'
            profiles = [{'task_id': pair['task_id'], 'trial_index': pair['trial_index'], 'profile': p,
                         'answer': answer, 'answer_sha256': hashlib.sha256(answer.encode()).hexdigest()}
                        for p in score.cohort.PROFILES]
            payload = {'protocol_id': score.cohort.PROTOCOL,'plan_sha256': score.cohort.sha(plan),
                       'pair': pair,'profiles': profiles}
            score.cohort.write(path, payload)
            score.cohort.write(path.parent/'judgements.json', {'profiles': [{'profile':'baseline','status':'scored','judge':{'total':100}}, {'profile':'zvec-grep','status':'judge_failed'}]})
            records, found = score.load_records(root, plan); calls=[]
            def grade(record):
                calls.append((record['pair_key'],record['profile']))
                if record['profile']=='zvec-grep':raise RuntimeError('preserved new failure')
                return {'total':50}
            result = score.evaluate(records, grade)
            self.assertEqual(len(result),100); self.assertEqual(len(found),1); self.assertEqual(len(calls),2)
            completed=[r for r in result if r['status']=='scored'];self.assertEqual(completed[0]['judge']['total'],50)
            self.assertEqual(sum(r['status']=='judge_failed' for r in result),1)
            self.assertEqual(sum(r['status']=='unavailable_no_complete_answer' for r in result),98)
            payload['profiles'][0]['answer']='changed'
            score.cohort.write(path,payload)
            with self.assertRaisesRegex(ValueError,'answer_identity'):
                score.load_records(root,plan)

    def test_duplicate_original_pair_cannot_be_counted_twice(self):
        with TemporaryDirectory() as name:
            root=Path(name);plan=root/'plan.json';pair=score.cohort.schedule()[0]
            score.cohort.write(plan,{'schedule':score.cohort.schedule()})
            item={'protocol_id':score.cohort.PROTOCOL,'plan_sha256':score.cohort.sha(plan),'pair':pair,
                  'profiles':[{'profile':p,'task_id':pair['task_id'],'trial_index':pair['trial_index'],'answer':None,'answer_sha256':None} for p in score.cohort.PROFILES]}
            for folder in ('one','two'):score.cohort.write(root/folder/'collection.json',item)
            with self.assertRaisesRegex(ValueError,'duplicate_original_pair'):
                score.load_records(root,plan)


if __name__=='__main__':unittest.main()
