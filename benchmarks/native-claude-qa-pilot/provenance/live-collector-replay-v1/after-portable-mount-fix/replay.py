from pathlib import Path
import copy, json, shutil, sys
WORKTREE=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
MAIN=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot/upstream-main/benchmarks/swe-qa-bench')
sys.path[:0]=[str(WORKTREE/'benchmarks/native-claude-qa-pilot'), str(WORKTREE/'benchmarks/opencode-qa-pilot'), str(MAIN)]
from types import SimpleNamespace
import live
BASE=Path(__file__).resolve().parent
SOURCE=WORKTREE/'benchmarks/native-claude-qa-pilot/results/20261020-offline-r2/ci/raw/integration'
DEST=BASE/'offline-integration-copy'
shutil.copytree(SOURCE,DEST)
proof=live.pilot.load(SOURCE/'proof.json')
slots=copy.deepcopy(proof['slots'])
for slot in slots:
    if slot['profile']=='zvec-grep':
        containers=live.pilot.load(DEST/(slot['profile']+'-containers.json'))
        sources={m['Source'] for c in containers.values() for m in c['mounts'] if m['Destination']=='/tmp/zg-bench-zvec-grep.tgz'}
        assert len(sources)==1
        slot.update(install_package_source=next(iter(sources)),install_package_sha256=live.PACKAGE_SHA)
plan={'classification':'OFFLINE REPLAY ONLY. Synthetic provider sentinels; not a live trial or quality result.',
      'offline_proof':{'proof_sha256':live.pilot.digest(SOURCE/'proof.json')}}
plan_path=BASE/'offline-replay-plan.json'
live.pilot.write(plan_path,plan)
execution={'protocol_id':live.PROTOCOL,'plan_sha256':live.pilot.digest(plan_path),
           'offline_proof_sha256':plan['offline_proof']['proof_sha256'],'candidate_sha256':live.PACKAGE_SHA,
           'dry_run':False,'slots':slots,'offline_replay_only':True,'not_a_live_trial':True}
live.pilot.write(DEST/'execution.json',execution)
live.pilot.write(BASE/'offline-replay-label.json',{'offline_replay_only':True,'not_a_live_trial':True,
 'source':str(SOURCE),'source_proof_valid':proof['valid'],'source_proof_sha256':live.pilot.digest(SOURCE/'proof.json'),
 'script_sha256':live.pilot.digest(Path(__file__)),'collector_sha256':live.pilot.digest(Path(live.__file__)),
 'calls':'collect() directly; validate_plan() and run_pair() not invoked; no Docker or provider calls',
 'mutations':'Only copied execution.json and slot install_package_source/sha metadata created from retained native mounts; original archive unchanged.'})
args=SimpleNamespace(plan=plan_path, output=DEST)
report=live.collect(args,plan,proof)
result={'offline_replay_only':True,'not_a_live_trial':True,'structural_valid':report['structural_valid'],
 'strict_usage_complete':report['strict_usage_complete'],'reported_metric_pair_eligible':report['reported_metric_pair_eligible'],
 'favorable_conclusion_allowed':report['favorable_conclusion_allowed'],
 'profiles':[{k:r.get(k) for k in ('profile','structural_valid','errors','stream_errors','strict_accounting_errors','strict_usage','claude_reported_model_usage','metric_consistency','native_atif_final_matches','native_log_copy_matches','source_audit_valid','container_mounts_valid','native_run_wall_seconds','source_audit_wall_seconds','log_export_wall_seconds','harbor_timing','native_zg_setup_timing','outer_slot_wall_seconds')} for r in report['profiles']]}
live.pilot.write(BASE/'replay-result.json',result)
print(json.dumps(result,indent=2))
