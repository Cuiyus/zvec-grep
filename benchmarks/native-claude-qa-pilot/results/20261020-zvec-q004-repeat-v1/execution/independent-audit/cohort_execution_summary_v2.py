#!/usr/bin/env python3
"""Read-only execution/accounting reconciliation of ten independently audited pairs.
No grades, packets, assignments, quality judgments or benefit calculations.
"""
from pathlib import Path
import datetime,hashlib,json,math,sys
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent
B=ROOT.parents[1]
CI=B/'native-q004-repeat-v1-ci/39380248003'
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
PLAN=W/'benchmarks/native-claude-qa-pilot/architecture-repeat-plan-v1.json'
RUN=39380248003
HEAD='3978d4bbbbb1e37961c277cdf76696b84c69e3bc'
PROTOCOL='native-claude-zvec-q004-repeat-v1'
PLAN_SHA='c2d102654a6f11ab330f843c501b1820c0c7385595e0fdb1d69635880f218c76'
METHOD_SHA='bb770cf8baab29e2513ef45d27c603d76805598b6980e55cee7c202fde3b1fd9'
KEYS=[f'q004-r{i:02d}' for i in range(1,11)]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_text())
def normal(path):return path.is_file() and not path.is_symlink()
def finite(value):return type(value) in (int,float) and math.isfinite(value) and value>=0

def main():
    destination=ROOT/'cohort-execution-summary-v2.json'
    if destination.exists() or destination.is_symlink():raise FileExistsError('exclusive_cohort_summary_exists')
    waiting=[]
    for key in KEYS:
        receipt=CI/key/'artifact-byte-verification.json'; report=ROOT/'pairs'/key/'execution-review.json'
        if not normal(receipt) or not normal(report):waiting.append(key);continue
        value=load(receipt)
        if value.get('complete') is not True or value.get('exact_extracted_inventory') is not True:waiting.append(key)
    if waiting:
        print(json.dumps({'status':'waiting_for_all_complete_receipts_and_pair_audits','pending_pairs':waiting,'summary_written':False}));return 3
    plan=load(PLAN)
    checks=[]
    def check(name,passed,**details):checks.append({'check':name,'passed':bool(passed),**details})
    check('frozen_plan_and_pair_method_unchanged',sha(PLAN)==PLAN_SHA and sha(ROOT/'q004_repeat_execution_audit_v1.py')==METHOD_SHA)
    schedule={x['key']:x for x in plan['schedule']}
    check('exact_twenty_new_slots_and_balanced_order',sorted(schedule)==KEYS and plan['scheduled_pairs']==10 and plan['scheduled_solver_trials']==20 and plan['n_attempts_per_pair']==1 and plan['max_retries']==0 and sum(p['profiles'][0]=='baseline' for p in schedule.values())==5)
    reports=[];catalog={};profiles=[];runners=[];jobs=[];artifacts=[];ziphashes=[];volumes=[];roots=[];failures=[]
    for key in KEYS:
        report_path=ROOT/'pairs'/key/'execution-review.json'; r=load(report_path);pair=schedule[key]
        receipt_path=CI/key/'artifact-byte-verification.json';receipt=load(receipt_path)
        metadata_path=CI/key/'job-metadata.json';metadata=load(metadata_path)
        selected=[j for j in metadata['jobs'] if j['name']=='repeat ('+key+')']
        job=selected[0] if len(selected)==1 else {}
        execution_path=CI/key/'raw/live/execution.json';execution=load(execution_path)
        identity=(r['run_id']==RUN and r['head_sha']==HEAD and r['run_attempt']==1 and r['protocol_id']==PROTOCOL and r['plan_sha256']==PLAN_SHA and r['pair_key']==key and r['trial_index']==pair['trial_index'] and r['scheduled_profile_order']==pair['profiles'] and r['method']['audit_sha256']==METHOD_SHA and receipt['pair_key']==key and receipt['trial_index']==pair['trial_index'] and receipt['run_id']==RUN and receipt['head_sha']==HEAD and receipt['run_attempt']==1 and receipt['artifact_id']==r['artifact_id'] and receipt['artifact_sha256']==r['artifact_sha256'] and receipt['job_id']==r['job_id']==job.get('id'))
        check(key+':exact_report_receipt_job_and_cohort_binding',identity)
        check(key+':all_original_pair_checks_retained_and_passed',r['check_count']==73 and r['passed_checks']==73 and r['passed'] is True and not r['failed_checks'] and len(r['checks'])==73 and all(c['passed'] for c in r['checks']))
        check(key+':both_usage_views_complete_and_execution_valid',r['execution_valid'] is True and r['metric_reconciliation_valid'] is True and r['strict_usage_pair_complete'] is True and r['reported_metric_pair_eligible'] is True and len(r['profiles'])==2)
        jobs.append(job.get('id'));runners.append(job.get('runner_id'));artifacts.append(r['artifact_id']);ziphashes.append(r['artifact_sha256'])
        volumes.extend((job.get('runner_id'),v) for slot in execution['slots'] for v in slot['fresh_volumes'])
        pair_profiles=[]
        for p in r['profiles']:
            roots.append(p['root_session_id'])
            fields=('uncached_input_tokens','cache_read_tokens','cache_write_tokens','input_tokens','output_tokens')
            complete=(p['complete_review'] is True and p['strict_complete'] is True and p['primary_usage']==p['strict_usage'] and all(finite(p['primary_usage'].get(f)) for f in fields) and not p['strict_errors'] and not p['metric_consistency']['contradictions'])
            check(key+':'+p['profile']+':complete_matching_usage',complete)
            selected_fields=('profile','root_session_id','session_count','child_count','native_unique_messages','tool_count','tool_name_counts','strict_tool_errors','primary_usage','strict_usage','complete_strict_usage_record','timing_seconds','cold_setup','strict_errors','metric_consistency')
            item={k:p[k] for k in selected_fields if k in p};item.update(pair_key=key,trial_index=pair['trial_index'])
            profiles.append(item);pair_profiles.append(item)
        for path,entry in r['evidence_catalog'].items():
            if path in catalog and catalog[path]!=entry:failures.append({'path':path,'reason':'cross_report_catalog_conflict'})
            catalog[path]=entry
        reports.append({'pair_key':key,'trial_index':pair['trial_index'],'profile_order':pair['profiles'],'report_path':str(report_path),'report_sha256':sha(report_path),'check_count':r['check_count'],'passed_checks':r['passed_checks'],'failed_checks':r['failed_checks'],'evidence_catalog_entries':len(r['evidence_catalog']),'artifact_id':r['artifact_id'],'artifact_sha256':r['artifact_sha256'],'job_id':r['job_id'],'runner_id':job.get('runner_id'),'receipt_sha256':sha(receipt_path),'job_metadata_sha256':sha(metadata_path),'execution_sha256':sha(execution_path),'profiles':pair_profiles})
    check('ten_distinct_jobs_artifacts_zip_bytes_and_hosted_runners',all(len(set(values))==10 and all(values) for values in (jobs,artifacts,ziphashes,runners)))
    check('twenty_distinct_native_root_sessions',len(roots)==len(set(roots))==20 and all(roots))
    check('forty_distinct_runner_scoped_fresh_volume_identities',len(volumes)==len(set(volumes))==40,identity_scope='(runner_id, Docker volume name); names can repeat on independent hosts')
    # Opaque byte verification only, including final-answer files in the catalog.
    for path,entry in catalog.items():
        f=Path(path)
        if not normal(f) or entry.get('bytes')!=f.stat().st_size or entry.get('sha256')!=sha(f):failures.append({'path':path,'reason':'catalog_bytes_or_hash_mismatch'})
    check('every_unique_pair_evidence_catalog_entry_rehashed',not failures,files=len(catalog),failures=failures)
    aggregates={}
    for name in ('baseline','zvec-grep'):
        group=[p for p in profiles if p['profile']==name]
        complete=len(group)==10 and all(isinstance(p.get('primary_usage'),dict) and isinstance(p.get('strict_usage'),dict) for p in group)
        usage={f:sum(p['primary_usage'][f] for p in group) for f in ('uncached_input_tokens','cache_read_tokens','cache_write_tokens','input_tokens','output_tokens')} if complete else None
        timing_keys=sorted(set.intersection(*(set(p['timing_seconds']) for p in group))) if group else []
        timing={key:{'sum_seconds':sum(p['timing_seconds'][key] for p in group),'mean_seconds':sum(p['timing_seconds'][key] for p in group)/len(group),'min_seconds':min(p['timing_seconds'][key] for p in group),'max_seconds':max(p['timing_seconds'][key] for p in group)} for key in timing_keys if all(finite(p['timing_seconds'][key]) for p in group)}
        aggregates[name]={'observed_trials':len(group),'complete_usage':complete,'usage_sum':usage,'usage_mean':{k:v/10 for k,v in usage.items()} if usage else None,'session_count':sum(p['session_count'] for p in group),'child_count':sum(p['child_count'] for p in group),'native_unique_messages':sum(p['native_unique_messages'] for p in group),'tool_count':sum(p['tool_count'] for p in group),'tool_error_count':sum(p['strict_tool_errors'] for p in group),'timing':timing}
        if name=='zvec-grep':
            setup_complete=all(isinstance(p.get('cold_setup'),dict) for p in group) and len(group)==10
            aggregates[name]['cold_setup']={'complete':setup_complete,'four_stage_sum_seconds':{s:sum(p['cold_setup']['four_stages_seconds'][s] for p in group) for s in ('install_duration_seconds','authorization_duration_seconds','index_duration_seconds','mcp_setup_duration_seconds')} if setup_complete else None,'setup_total_sum_seconds':sum(p['cold_setup']['total_seconds'] for p in group) if setup_complete else None,'embedding_request_tokens':None,'embedding_cost':None}
    final_path=CI/'final-run-metadata.json'
    final=None
    if normal(final_path):
        f=load(final_path)
        check('retained_final_run_identity_and_completion',f['id']==RUN and f['head_sha']==HEAD and f['run_attempt']==1 and f['event']=='workflow_dispatch' and f['repository']['full_name']=='Cuiyus/zvec-grep' and f['path']=='.github/workflows/native-claude-q004-repeat-v1.yml' and f['status']=='completed')
        final={'path':str(final_path),'sha256':sha(final_path),'status':f['status'],'conclusion':f['conclusion']}
    result={'schema_version':1,'generated_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Independent execution and accounting only; no answer quality, source-tool semantic scope, comparative benefit or statistical endpoint.','run_id':RUN,'head_sha':HEAD,'protocol_id':PROTOCOL,'plan_sha256':PLAN_SHA,'all_planned_pairs_observed':len(reports)==10,'all_planned_trials_observed':len(profiles)==20,'passed':all(c['passed'] for c in checks),'checks':checks,'check_count':len(checks),'failed_checks':[c['check'] for c in checks if not c['passed']],'pair_check_count':sum(r['check_count'] for r in reports),'pair_passed_check_count':sum(r['passed_checks'] for r in reports),'pairs':reports,'aggregate_execution_measurements':aggregates,'unique_evidence_catalog_entries_rehashed':len(catalog),'retained_final_run':final,'method':{'path':str(Path(__file__)),'sha256':sha(Path(__file__)),'pair_method_sha256':METHOD_SHA},'unknowns':['Embedding request/token usage and billable cost are not retained and remain null.','Terminal and modelUsage measurements are not provider billing or proof of unlogged background request coverage.','Per-host freshness/cleanup and source invariance rely on retained controller evidence; expired runners were not reinspected.'],'excluded_judgments':{'quality':False,'source_tool_scope':False,'efficiency_or_benefit':False,'statistical_endpoint':False},'limitations':['All 10 new pairs are included; the development pair and historical answers are excluded.','Per-profile elapsed sums are sums over independent hosted jobs, not cohort calendar elapsed time. No cold-total speedup is inferred.','Complete usage/CI success does not establish source-scope or answer quality validity. Separate reviews are required.','Private assignments, anonymous packets, grading and answer semantics were not inspected.']}
    previous=ROOT/'cohort-execution-summary.json'
    result['audit_revision']={'previous_summary_path':str(previous),'previous_summary_sha256':sha(previous),'correction':'The v1 cohort-only check incorrectly required Docker volume names to be globally unique across independent hosted runners. Native names are deterministic and host-local. V2 binds each volume name to its verified runner_id; the original per-pair checks and every other cohort check remain unchanged. All previous evidence and reports are preserved.','native_runtime_or_frozen_rule_changed':False}
    prior=load(previous)
    previous_other=[c for c in prior['checks'] if c['check']!='forty_distinct_fresh_volume_identities']
    current_other=[c for c in checks if c['check']!='forty_distinct_runner_scoped_fresh_volume_identities']
    assert previous_other==current_other, 'unrelated_cohort_check_changed'
    assert prior['pairs']==result['pairs'] and prior['aggregate_execution_measurements']==result['aggregate_execution_measurements'], 'raw_or_pair_metric_changed'
    with destination.open('x') as f:json.dump(result,f,indent=2,sort_keys=True,ensure_ascii=False);f.write('\n')
    print(json.dumps({'path':str(destination),'sha256':sha(destination),'passed':result['passed'],'pair_checks':result['pair_check_count'],'cohort_checks':len(checks),'failed_checks':result['failed_checks'],'evidence_entries':len(catalog)},indent=2))
    return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
