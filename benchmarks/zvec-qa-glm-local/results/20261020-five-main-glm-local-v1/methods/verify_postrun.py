"""Verify one uniform score wave against all100 original native positions."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parent
WORKTREE=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
sys.path[:0]=[str(WORKTREE/'benchmarks'/p) for p in ('zvec-qa-glm-local','opencode-qa-pilot','native-claude-qa-pilot')]
import cohort
import judging


def main(quality_root):
    native_root=ROOT/'live-ci/39414480210'
    raw=quality_root/'raw';stage=raw/'quality'
    receipt=cohort.load(quality_root/'artifact-byte-verification.json')
    assert receipt['complete'] and receipt['matches_api_digest']
    assert cohort.sha(quality_root/'artifact.zip')==receipt['zip_sha256']
    for entry in receipt['files']:
        p=raw/entry['path'];assert p.stat().st_size==entry['bytes'] and cohort.sha(p)==entry['sha256']
    plan_path=WORKTREE/'benchmarks/zvec-qa-glm-local/plan-v1.json'
    plan=cohort.load(plan_path)
    assert cohort.sha(plan_path)=='a145cb195eac4bc729fea1b642c9db480a360ddc14e5217d7f157683b03bcc99'
    for rel,digest in plan['controls'].items():assert cohort.sha(WORKTREE/rel)==digest,rel
    main_root=ROOT/'main/benchmarks/swe-qa-bench'
    for rel,digest in plan['main_files'].items():assert cohort.sha(main_root/rel)==digest,rel
    cohort.native(main_root)
    from zg_bench.engines.judge import _judge_prompt
    manifest=cohort.load(stage/'input-manifest.json')
    amendment=raw/'amendment-v1.json';seal=raw/'source-seal-v1.json'
    assert cohort.sha(seal)=='7bf628055ed7d5644844b0bc7765e2738a699cbceee746d89420bfe8a6ab7d7e'
    assert manifest['amendment_sha256']==cohort.sha(amendment)
    assert manifest['planned_positions']==100 and manifest['native_run_id']==39414480210
    assert manifest['original_grade_used_for_selection'] is False
    assert manifest['native_agent_executions_added']==0
    native_metadata=cohort.load(raw/'native-terminal-metadata.json')
    assert native_metadata['status']=='completed' and native_metadata['head_sha']=='4e72601048a2eed840b5bcb99105c4d9aa6314a8'
    assert cohort.sha(raw/'native-terminal-metadata.json')==manifest['native_terminal_metadata_sha256']
    positions={(r['pair_key'],r['profile']):r for r in manifest['positions']}
    quality=cohort.load(stage/'all100-judgements.json')
    assert quality['input_manifest_sha256']==cohort.sha(stage/'input-manifest.json')
    values={(r['pair_key'],r['profile']):r for r in quality['profiles']}
    assert len(positions)==len(manifest['positions'])==len(values)==len(quality['profiles'])==100
    expected={(pair['key'],profile) for pair in plan['schedule'] for profile in cohort.PROFILES}
    assert set(positions)==set(values)==expected
    references={t:judging.reference(t) for t in cohort.five.TASK_IDS}
    rows={};provenance=[]
    for pair in plan['schedule']:
        path=native_root/pair['key']/'raw/output/collection.json'
        original=cohort.load(path) if path.exists() else {'profiles':[]}
        newpath=stage/'pairs'/pair['key']/'collection.json'
        assert newpath.exists()==path.exists()
        if path.exists():
            assert cohort.sha(path)==cohort.sha(newpath)
            j=cohort.load(newpath.parent/'judgements.json')
            assert j['collection_sha256']==cohort.sha(path) and j['amendment_sha256']==cohort.sha(amendment)
            assert j['reference']=={k:v for k,v in references[pair['task_id']].items() if k not in ('question','reference_answer')}
        for profile in cohort.PROFILES:
            key=(pair['key'],profile);p=positions[key];v=values[key]
            original_row=next((r for r in original['profiles'] if r['profile']==profile),{})
            assert p['collection_sha256']==(cohort.sha(path) if path.exists() else None)
            assert p['answer_sha256']==v['answer_sha256']==original_row.get('answer_sha256')
            assert p['task_id']==v['task_id']==pair['task_id'] and p['trial_index']==v['trial_index']==pair['trial_index']
            if original_row.get('answer') is None:
                assert v['status']=='unavailable_no_complete_answer' and v['judge'] is None
            elif v['status']=='scored':
                j=v['judge'];assert j['model']=='glm-5.2'
                assert set(j['scores'])=={'correctness','completeness','relevance','clarity','coherence'}
                assert all(type(x) is int and 1<=x<=20 for x in j['scores'].values())
                assert j['total']==sum(j['scores'].values())
            else:assert v['status']=='judge_failed'
            rows[key]=original_row
            provenance.append({'pair_key':key[0],'profile':profile,'answer_sha256':v['answer_sha256'],'status':v['status']})
    calls=cohort.load(stage/'judge-requests.json') if (stage/'judge-requests.json').exists() else []
    call_counts=Counter();error_counts=Counter()
    for call in calls:
        key=(call['pair_key'],call['profile']);assert key in expected
        assert call['answer_sha256']==values[key]['answer_sha256'] and rows[key].get('answer') is not None
        call_counts[key]+=1
        request=call['request'];assert request['model']=='openai/glm-5.2'
        assert request['temperature']==0 and request['seed']==42 and request['reasoning_effort']=='high'
        assert request['max_tokens']==32000 and request['extra_body']=={'enable_thinking':True}
        ref=references[values[key]['task_id']]
        prompt=_judge_prompt(question=ref['question'],reference=ref['reference_answer'],candidate=rows[key]['answer'])
        assert request['messages']==[{'role':'user','content':prompt}]
        if call.get('error_type'):error_counts[call['error_type']]+=1
    assert all(1<=call_counts[k]<=3 if rows[k].get('answer') is not None else call_counts[k]==0 for k in expected)
    assert quality['actual_judge_requests']==len(calls)
    report=cohort.load(raw/'report/report.json');normalized=cohort.load(raw/'report/normalized-input.json')
    finalrows=[r for c in normalized['cases'] for pro in c['profiles'].values() for r in pro['trials']]
    assert len(finalrows)==100 and report['quality_evaluation_wave']['source_native_run_id']==39414480210
    for row in finalrows:
        key=(row['pair_key'],row['profile']);expected_score=(values[key].get('judge') or {}).get('total')
        assert row['judge']['total']==expected_score
        assert row['metrics']=={metric:(rows[key].get('metrics') or {}).get(metric) for metric in ('input_tokens','output_tokens','tool_calls','agent_wall_seconds','outer_slot_seconds')}
    dimensions={}
    for task in cohort.five.TASK_IDS:
        dimensions[task]={}
        for profile in cohort.PROFILES:
            subset=[v for v in values.values() if v['task_id']==task and v['profile']==profile]
            known=[v for v in subset if v['status']=='scored']
            dimensions[task][profile]={'planned':10,'known':len(known),'means':None if len(known)!=10 else {k:statistics.mean(v['judge']['scores'][k] for v in known) for k in ('correctness','completeness','relevance','clarity','coherence')}}
    result={'schema_version':1,'quality_run_id':receipt['run_id'],'native_run_id':39414480210,
            'all100_bindings_valid':True,'new_native_agent_executions':0,
            'quality_statuses':dict(Counter(v['status'] for v in values.values())),
            'logged_completion_invocations':len(calls),'new_wave_transport_errors':dict(error_counts),
            'all_prompts_match_unchanged_main_prompt':True,'original_resources_unchanged':True,
            'per_task_dimension_means':dimensions,'positions':provenance}
    cohort.write(ROOT/'final-quality-verification.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('per_task_dimension_means','positions')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('quality_root',type=Path)
    main(parser.parse_args().quality_root)
