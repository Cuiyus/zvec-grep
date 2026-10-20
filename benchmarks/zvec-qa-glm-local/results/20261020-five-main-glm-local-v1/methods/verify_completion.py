"""Check every completed position against its unchanged native/grade source."""
from pathlib import Path
from collections import Counter
import copy
import json
import sys

ROOT=Path(__file__).resolve().parent
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
sys.path.insert(0,str(W/'benchmarks/zvec-qa-glm-local'))
import cohort

primary=ROOT/'quality-ci/39424282413/raw/quality/pairs'
recovery=ROOT/'recovery-ci/39422897058/raw/execution/native'
completed=ROOT/'completion-inputs'
report=cohort.load(ROOT/'completion-report/report.json')
payload=cohort.load(ROOT/'completion-report/normalized-input.json')
manifest=cohort.load(completed/'completion-manifest.json')
rows=[r for c in payload['cases'] for p in c['profiles'].values() for r in p['trials']]
indexed={(r['pair_key'],r['profile']):r for r in rows}
assert len(indexed)==len(rows)==100
counts=Counter();roots=set();bindings=[]
for pair in cohort.schedule():
    original=cohort.load(primary/pair['key']/'collection.json')
    old_grade=cohort.load(primary/pair['key']/'judgements.json')
    derived=cohort.load(completed/pair['key']/'collection.json')
    derived_grade=cohort.load(completed/pair['key']/'judgements.json')
    assert derived['pair']==pair and derived_grade['collection_sha256']==cohort.sha(completed/pair['key']/'collection.json')
    for profile in cohort.PROFILES:
        is_recovery=(pair['key'],profile)==('q004-r09','zvec-grep')
        source=cohort.load(recovery/'collection.json')['profiles'][0] if is_recovery else next(r for r in original['profiles'] if r['profile']==profile)
        grade=cohort.load(recovery/'judgements.json')['profiles'][0] if is_recovery else next(r for r in old_grade['profiles'] if r['profile']==profile)
        target=next(r for r in derived['profiles'] if r['profile']==profile)
        target_grade=next(r for r in derived_grade['profiles'] if r['profile']==profile)
        expected=copy.deepcopy(source)
        if is_recovery:
            assert next(r for r in original['profiles'] if r['profile']==profile)['answer'] is None
            expected['metrics']['outer_slot_seconds']+=manifest['replacement_positions'][0]['original_failure_outer_seconds']
        assert target==expected
        assert target_grade==grade
        assert target['execution_valid'] and target['root_session_id'] not in roots
        roots.add(target['root_session_id']);counts[(pair['task_id'],profile)]+=1
        normalized=indexed[(pair['key'],profile)]
        assert normalized['answer_sha256']==target['answer_sha256']==grade['answer_sha256']
        assert grade['status']=='scored' and normalized['judge']['total']==grade['judge']['total']
        assert grade['judge']['total']==sum(grade['judge']['scores'].values())
        assert normalized['metrics']=={k:expected['metrics'][k] for k in ('input_tokens','output_tokens','tool_calls','agent_wall_seconds','outer_slot_seconds')}
        bindings.append({'pair_key':pair['key'],'profile':profile,'answer_sha256':target['answer_sha256'],
                         'native_source':'recovery_first_actual' if is_recovery else 'original_native',
                         'grade_source':'recovery_singleton_postrun' if is_recovery else 'uniform_original_postrun'})
assert len(roots)==100 and set(counts.values())=={10}
assert report['counts']=={'planned':100,'retained_pairs':50,'native_valid':100,'exact_final_answers':100,'scored':100}
assert payload['original_failure_records_retained_in_unchanged_primary'] is True
assert report['statistics']['bootstrap']['resamples']==10000
assert report['statistics']['pr261_compatibility']['task_filter']['excluded_task_ids']==[]
for entry in manifest['original_pair_bindings']:
    p=primary/entry['pair_key']
    assert cohort.sha(p/'collection.json')==entry['original_collection_sha256']
    assert cohort.sha(p/'judgements.json')==entry['original_uniform_judgements_sha256']
result={'all100_actual_native_positions_bound':True,'per_task_profile_actual_counts':{t:{p:counts[(t,p)] for p in cohort.PROFILES} for t in cohort.five.TASK_IDS},
        'unique_native_root_sessions':100,'all100_postrun_scores_bound':True,'new_successful_original_profiles_repeated':0,
        'original_setup_failure_preserved_separately':True,'only_cold_metric_adjustment':'q004-r09 zg adds measured original failed setup elapsed33.73340845108032 seconds',
        'original_primary_unchanged':True,'one_pair_crosses_hosts_and_waves':True,'semantic_scope_gate_not_asserted':True,
        'normalized_input_sha256':cohort.sha(ROOT/'completion-report/normalized-input.json'),
        'report_sha256':cohort.sha(ROOT/'completion-report/report.json'),'positions':bindings,'new_provider_calls':0}
cohort.write(ROOT/'completion-evidence-verification.json',result)
print(json.dumps({k:v for k,v in result.items() if k!='positions'}))
