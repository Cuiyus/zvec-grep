"""Protect the pre-agent-only completion boundary without model execution."""
import copy, unittest
import five_completion_v1 as completion

def job():return {'name':'repeat (q004-r04)','head_sha':completion.ORIGINAL_HEAD,'run_id':completion.ORIGINAL_RUN,
    'run_attempt':1,'status':'completed','conclusion':'failure','steps':[
        {'name':'Run ./.github/actions/native-five-setup','conclusion':'failure'},
        {'name':'Execute exactly the scheduled original native pair','conclusion':'skipped'}]}
def run():return {'id':completion.ORIGINAL_RUN,'head_sha':completion.ORIGINAL_HEAD,'run_attempt':1,'status':'completed',
    'path':'.github/workflows/native-five-repeat-v1.yml','event':'workflow_dispatch','repository':{'full_name':'Cuiyus/zvec-grep'}}

class CompletionTests(unittest.TestCase):
    def test_only_exact_unstarted_setup_failure_is_eligible(self):
        self.assertTrue(completion.original_binding(run(),job(),require_terminal=True))
        for field,value in [('name','repeat (q001-r04)'),('run_attempt',2),('head_sha','other'),('conclusion','cancelled')]:
            changed=job();changed[field]=value
            with self.assertRaisesRegex(ValueError,'pre_agent_failure'):completion.original_binding(run(),changed,require_terminal=True)
    def test_any_native_attempt_prevents_replacement(self):
        for status in ['success','failure','cancelled',None]:
            changed=job();changed['steps'][1]['conclusion']=status
            self.assertFalse(completion.eligible_job(changed))
    def test_supplement_cannot_overlap_active_original_cohort(self):
        changed=run();changed['status']='in_progress'
        self.assertTrue(completion.original_binding(changed,job(),require_terminal=False))
        with self.assertRaisesRegex(ValueError,'must_finish'):completion.original_binding(changed,job(),require_terminal=True)
    def test_new_pair_reuses_exact_original_source_and_order(self):
        self.assertEqual(completion.PAIR,completion.five.selected_pair('q004',4))
        self.assertEqual(completion.PAIR['profiles'],['baseline','zvec-grep'])

if __name__=='__main__':unittest.main()
