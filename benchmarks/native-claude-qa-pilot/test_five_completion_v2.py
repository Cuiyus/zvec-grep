"""Select proven unstarted pairs only after every original setup settles."""
import copy, unittest
import five_completion_v2 as completion

def fixture():
    run={'id':completion.ORIGINAL_RUN,'head_sha':completion.ORIGINAL_HEAD,'run_attempt':1,'status':'completed',
        'path':'.github/workflows/native-five-repeat-v1.yml','event':'workflow_dispatch','conclusion':'failure',
        'repository':{'full_name':'Cuiyus/zvec-grep'}}
    jobs=[]
    for i,pair in enumerate(completion.five.schedule()):
        failed=pair['key'] in ('q004-r04','q005-r05')
        jobs.append({'id':100+i,'name':'repeat ('+pair['key']+')','head_sha':completion.ORIGINAL_HEAD,
            'run_id':completion.ORIGINAL_RUN,'run_attempt':1,'status':'completed','conclusion':'failure' if failed else 'success',
            'steps':[{'name':'Run ./.github/actions/native-five-setup','conclusion':'failure' if failed else 'success'},
                {'name':'Execute exactly the scheduled original native pair','conclusion':'skipped' if failed else 'success'}]})
    return run,{'jobs':jobs}

class CompletionTests(unittest.TestCase):
    def test_entire_final_pre_agent_set_keeps_exact_original_order(self):
        run,jobs=fixture();selected=completion.missing_pairs(run,jobs)
        self.assertEqual({p['key'] for p in selected},{'q004-r04','q005-r05'})
        for row in selected:self.assertEqual({k:v for k,v in row.items() if k!='original_job_id'},completion.five.selected_pair(row['task_id'],row['trial_index']))
    def test_started_native_failure_is_never_selected(self):
        run,jobs=fixture()
        for job in jobs['jobs']:
            if job['conclusion']=='failure':
                job['steps'][0]['conclusion']='success'
                job['steps'][1]['conclusion']='failure'
        self.assertEqual(completion.missing_pairs(run,jobs),[])
    def test_cancelled_wrong_head_and_foreign_run_rejected(self):
        for key,value in [('conclusion','cancelled'),('head_sha','other'),('run_attempt',2),('id',1)]:
            run,jobs=fixture();run[key]=value
            with self.assertRaisesRegex(ValueError,'remain_bound'):completion.missing_pairs(run,jobs)
    def test_incomplete_or_duplicate_original_jobs_rejected(self):
        run,jobs=fixture();jobs['jobs'].pop()
        with self.assertRaisesRegex(ValueError,'all_fifty'):completion.missing_pairs(run,jobs)
        run,jobs=fixture();jobs['jobs'].append(copy.deepcopy(jobs['jobs'][0]))
        with self.assertRaisesRegex(ValueError,'remain_bound'):completion.missing_pairs(run,jobs)
    def test_score_resources_and_answers_cannot_influence_selection(self):
        run,jobs=fixture();expected=completion.missing_pairs(run,jobs)
        for job in jobs['jobs']:job.update(score=0,resources=10**12,answer='ignored')
        self.assertEqual(completion.missing_pairs(run,jobs),expected)
    def test_active_agents_do_not_block_a_settled_setup_selection(self):
        run,jobs=fixture();expected=completion.missing_pairs(run,jobs)
        run.update(status='in_progress',conclusion=None)
        for job in jobs['jobs']:
            if job['conclusion']=='success':
                job.update(status='in_progress',conclusion=None)
                job['steps'][1]['conclusion']=None
        self.assertEqual(completion.missing_pairs(run,jobs),expected)
        jobs['jobs'][0]['steps'][0]['conclusion']=None
        with self.assertRaisesRegex(ValueError,'setups_must_settle'):completion.missing_pairs(run,jobs)
    def test_combined_parallelism_never_exceeds_original_twenty_pairs(self):
        _,jobs=fixture()
        for job in jobs['jobs'][:19]:job['status']='in_progress'
        self.assertTrue(completion.parallel_capacity(jobs,1))
        self.assertFalse(completion.parallel_capacity(jobs,2))

if __name__=='__main__':unittest.main()
