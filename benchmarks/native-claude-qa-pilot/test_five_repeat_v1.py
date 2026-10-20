"""Protect the authorized 100 slots, native bodies, profile parity and task set."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import live
import repeat_identity_v1 as original_identity
import five_repeat_v1 as five
import workspace_context_v1 as workspace

class FiveRepeatTests(unittest.TestCase):
    def test_fifty_unique_balanced_pairs_interleave_all_five_questions(self):
        rows=five.schedule()
        self.assertEqual(len(rows),50)
        self.assertEqual(len({r['key'] for r in rows}),50)
        for start in range(0,50,5):self.assertEqual({r['task_id'] for r in rows[start:start+5]},set(five.preparation.TASK_IDS))
        for task in five.preparation.TASK_IDS:
            selected=[r for r in rows if r['task_id']==task]
            self.assertEqual({r['trial_index'] for r in selected},set(range(1,11)))
            self.assertEqual(sum(r['profiles'][0]=='baseline' for r in selected),5)
        for task,index in [('q006',1),('q001',0),('q001',11),('q001',True)]:
            with self.assertRaises(ValueError):five.selected_pair(task,index)

    def test_count_retry_or_extra_runtime_override_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'plan.json'
            base={**copy.deepcopy(five.FIXED),**{k:None for k in five.EXTRA},'schedule':five.schedule()}
            for key,value in [('scheduled_solver_trials',200),('max_retries',1),('task_ids',['q005']),
                              ('repetitions_per_task_profile',True),('provider','other')]:
                changed={**base,key:value};path.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError,'cohort_changed'):
                    five.validate_plan(SimpleNamespace(plan=path))

    def test_current_real_candidate_keeps_native_bodies_and_shared_context_in_both_orders(self):
        manifest=five.candidate.load(five.HERE/'provenance/current-rust-ae1ac57-v1/candidate/candidate-manifest.json')
        old=(live.PROTOCOL,list(live.PROFILES),live.PACKAGE_SHA)
        runner=SimpleNamespace(build_harbor_command=lambda *a,**k:['harbor','run','--agent','claude-code','--model','claude-opus-5'])
        cache=SimpleNamespace(zvec_grep_package=None,zvec_grep_package_sha256=None)
        for index in (1,2):
            pair=five.selected_pair('q001',index);module=five.native(pair,manifest)
            self.assertEqual(module.PROFILES,pair['profiles'])
            for name in ('run_slot','run_pair','collect'):
                self.assertEqual(getattr(module,name).__code__.co_code,getattr(live,name).__code__.co_code)
            for profile in pair['profiles']:
                command=module.build_command(runner,None,profile,Path('/prepared'),Path('/output'),cache,'https://example.invalid/embedding')
                self.assertEqual(command[-2:],['--agent-kwarg',workspace.KWARG])
                self.assertEqual(command.count(workspace.KWARG),1)
        self.assertEqual(old,(live.PROTOCOL,live.PROFILES,live.PACKAGE_SHA))

    def test_original_identity_body_accepts_only_the_fixed_five_without_global_mutation(self):
        module=five.identity_module()
        self.assertEqual(module.verify.__code__.co_code,original_identity.verify.__code__.co_code)
        self.assertEqual(module.preparation.TASK_IDS,('q001','q002','q003','q004','q005'))
        self.assertEqual(original_identity.preparation.TASK_IDS,('q001','q002','q003'))

if __name__=='__main__':unittest.main()
