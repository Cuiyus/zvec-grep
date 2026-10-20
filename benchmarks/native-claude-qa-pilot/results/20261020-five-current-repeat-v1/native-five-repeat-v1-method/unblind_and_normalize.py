"""Unblind only after both complete independent comparison seals exist.

Preserve the original 100-row primary; create a separately named infrastructure
completion view by overlaying only the exact five originally unstarted pairs.
"""
from pathlib import Path
import argparse,copy,datetime,hashlib,json
B=Path(__file__).resolve().parent.parent
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
H=W/'benchmarks/native-claude-qa-pilot'
TASKS=('q001','q002','q003','q004','q005');PROFILES=('baseline','zvec-grep')
METRICS=('input_tokens','output_tokens','tool_calls','agent_wall_seconds','outer_slot_seconds')
ORIGINAL_RUN=39392884840;ORIGINAL_HEAD='df0d37796435a596b4f6d62215a8fe28f401774a'
SUPPLEMENT_RUN=39401348683;SUPPLEMENT_HEAD='1bcd2b80df42de344fed9ff67e9f5c326fb19c24'
ORIGINAL_PLAN_SHA='cbd1e29c7badfe005c5e36392b9eeb7e26b016b890a92e44d5251096f6af9d54'
SUPPLEMENT_PLAN_SHA='609ffc70056088d87c37152aa8d1042087b44dee6ed429c5e55ace4669d48896'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def write(p,value):
    with p.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
def all3(*values):
    if any(v is False for v in values):return False
    return True if all(v is True for v in values) else None
def ref(p):return {'path':str(p),'sha256':sha(p)}
def scope_path(root,key):
    index=root/'method/report-resolution-index-v1.json'
    overrides=load(index).get('overrides',{}) if index.is_file() else {}
    if key in overrides:
        saved=overrides[key]['active_report'];p=Path(saved['path']);assert sha(p)==saved['sha256'];return p
    return root/'pairs'/key/'report.json'
def audits(kind,key):
    if kind=='original':
        run,head=ORIGINAL_RUN,ORIGINAL_HEAD
        eroot=B/'native-five-repeat-v1-audit/execution';sroot=B/'native-five-repeat-v1-audit/source-scope'
    else:
        run,head=SUPPLEMENT_RUN,SUPPLEMENT_HEAD
        eroot=B/'native-five-completion-v2-audit'/str(run)/'execution';sroot=B/'native-five-completion-v2-audit'/str(run)/'source-scope'
    ep=eroot/key/'report.json';sp=scope_path(sroot,key)
    execution,scope=load(ep),load(sp)
    assert execution['run_id']==scope['run_id']==run and execution['head_sha']==scope['head_sha']==head
    assert execution['pair']['key']==scope['pair_key']==key
    assert execution['plan_sha256']==ORIGINAL_PLAN_SHA
    if kind=='supplement':
        binding=load(eroot/key/'completion-binding.json')
        assert binding['audit_report_sha256']==sha(ep) and binding['supplement_plan_sha256']==SUPPLEMENT_PLAN_SHA
    return execution,scope,{'execution_audit':ref(ep),'source_scope_audit':ref(sp)}
def verify_master(p,kind):
    result=load(p);assert result['condition_mapping_read'] is False and result['independent_comparisons_sealed_before_unblinding']
    for question in result['questions']:
        qp=Path(question['path']);assert sha(qp)==question['sha256'];q=load(qp)
        if kind=='original':
            for cp in q['chunk_comparison_bindings']:assert sha(Path(cp['path']))==cp['sha256']
            for role,full in q['original_full_report_bindings'].items():
                assert sha(Path(full['path']))==full['sha256']
                for chunk in full['chunks']:assert sha(Path(chunk['report_path']))==chunk['report_sha256']
        else:
            for role,report in q['input_reports'].items():assert sha(Path(report['path']))==report['sha256']
    return result
def row_from(kind,assignment,quality,audit_cache):
    task=assignment['task_id'];profile=assignment['profile'];index=assignment.get('trial_index',assignment.get('repetition'))
    assert task in TASKS and profile in PROFILES and type(index) is int and 1<=index<=10
    key=f'{task}-r{index:02d}';identity=assignment['anonymous_id'];assert quality['blinded_answer_id']==identity and quality['task_id']==task
    execution,scope,bindings=audit_cache[kind,key]
    native=next(r for r in execution['profiles'] if r['profile']==profile)
    source=next(r for r in scope['profiles'] if r['profile']==profile)
    assert native.get('answer_sha256')==quality['answer_sha256']
    metrics={key:(native.get('metrics') or {}).get(key) for key in METRICS}
    validity=all3(native.get('execution_valid'),source.get('scope_and_retrieval_gate_passed'))
    qpass=quality['source_grounded_success'];eligible=native.get('reported_metric_eligible')
    return {'trial_index':index,'pair_key':key,'task_id':task,'profile':profile,'execution_wave':kind,
        'run_id':ORIGINAL_RUN if kind=='original' else SUPPLEMENT_RUN,'anonymous_answer_id':identity,
        'answer_sha256':quality['answer_sha256'],'final_answer_available':quality['answer_sha256'] is not None,
        'judge':{'total':quality['score'],'lower':quality['score_lower'],'upper':quality['score_upper']},
        'criterion_points':quality['criterion_points'],'criterion_score_bounds':quality['criterion_score_bounds'],
        'quality_pass':qpass,'execution_and_scope_valid':validity,'reported_metric_eligible':eligible,
        'end_to_end_valid_success':all3(qpass,validity,eligible),'metrics':metrics,
        'native_execution_status':native.get('execution_status'),'native_execution_valid':native.get('execution_valid'),
        'native_pair_command_started':execution.get('native_execution_started'),
        'source_audit_native_profile_started':source.get('execution_started'),
        'retained_claude_session_count':native.get('session_count'),
        'retained_claude_session_tree_available':(native['session_count']>0 if type(native.get('session_count')) is int else False if native.get('execution_status')=='not_started' else None),
        'strict_source_scope_valid':source.get('strict_source_scope_valid'),
        'factual_evidence_scope_valid':source.get('factual_evidence_scope_valid'),
        'source_unchanged':source.get('source_unchanged'),
        'relevant_correct_workspace_zg_retrieval':source.get('relevant_correct_workspace_zg_retrieval'),
        'scope_and_retrieval_gate_passed':source.get('scope_and_retrieval_gate_passed'),
        'source_audit_retained_tool_count':source.get('tool_count'),
        'source_audit_extraction_complete':source.get('extraction_complete'),
        'strict_usage_available':native.get('strict_usage_available'),
        'strict_accounting_errors':native.get('strict_accounting_errors'),
        'harbor_timing':native.get('harbor_timing'),'native_zg_setup_timing':native.get('native_zg_setup_timing'),
        'native_adapter_run_wall_seconds':native.get('native_adapter_run_wall_seconds'),
        'quality_independent_disagreement_criteria':quality['point_or_bounds_disagreement_criteria'],
        'quality_gate_disagreement':quality['success_gate_disagreement'],'evidence':bindings}
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--admit-unblinding',action='store_true',required=True);args=parser.parse_args()
    original_master=B/'native-five-repeat-v1-grading/comparison/final-original-comparison.json'
    supplement_master=B/'native-five-completion-v2-grading/comparison/final-supplement-comparison.json'
    if not original_master.is_file() or not supplement_master.is_file():
        print(json.dumps({'ready':False,'reason':'both_complete_blinded_comparison_seals_required','mapping_read':False}));return 3
    assert sha(H/'five-repeat-plan-v1.json')==ORIGINAL_PLAN_SHA and sha(H/'five-completion-plan-v2.json')==SUPPLEMENT_PLAN_SHA
    original_plan=load(H/'five-repeat-plan-v1.json');supplement_plan=load(H/'five-completion-plan-v2.json')
    om,sm=verify_master(original_master,'original'),verify_master(supplement_master,'supplement')
    assert len(om['records'])==100 and len(sm['records'])==10
    oq={r['blinded_answer_id']:r for r in om['records']};sq={r['blinded_answer_id']:r for r in sm['records']}
    assert len(oq)==100 and len(sq)==10 and not set(oq)&set(sq)
    cache={};missing=[]
    keys=[f'{t}-r{i:02d}' for t in TASKS for i in range(1,11)]
    for kind,selected in [('original',keys),('supplement',[p['key'] for p in supplement_plan['pairs']])]:
        for key in selected:
            try:cache[kind,key]=audits(kind,key)
            except FileNotFoundError:missing.append(kind+':'+key)
    if missing:
        print(json.dumps({'ready':False,'reason':'all_execution_and_scope_audits_required','missing':missing,'mapping_read':False}));return 3
    oa=B/'native-five-repeat-v1-grading-private/assignment.json';sa=B/'native-five-completion-v2-grading-private/assignment.json'
    assert sha(oa)==original_plan['grading']['assignment_sha256'] and sha(sa)==supplement_plan['grading']['assignment_sha256']
    root=B/'native-five-repeat-v1-analysis';root.mkdir(exist_ok=False)
    admission={'schema_version':1,'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'original_comparison':ref(original_master),'supplement_comparison':ref(supplement_master),
        'original_assignment_sha256':sha(oa),'supplement_assignment_sha256':sha(sa),
        'all_110_comparisons_sealed_before_mapping_read':True,'all_55_execution_and_scope_audits_present':True,
        'method_sha256':sha(Path(__file__)),'original_primary_absences_will_be_preserved':True}
    write(root/'unblinding-admission.json',admission)
    original_assignments=load(oa)['rows'];supplement_assignments=load(sa)['rows']
    assert {r['anonymous_id'] for r in original_assignments}==set(oq) and {r['anonymous_id'] for r in supplement_assignments}==set(sq)
    original_rows=[row_from('original',a,oq[a['anonymous_id']],cache) for a in original_assignments]
    supplement_rows=[row_from('supplement',a,sq[a['anonymous_id']],cache) for a in supplement_assignments]
    def assemble(rows,view):
        cases=[]
        for task in TASKS:
            profiles={}
            for profile in PROFILES:
                trials=sorted((r for r in rows if r['task_id']==task and r['profile']==profile),key=lambda r:r['trial_index'])
                assert len(trials)==10 and {r['trial_index'] for r in trials}==set(range(1,11))
                profiles[profile]={'trials':trials}
            cases.append({'task_id':task,'profiles':profiles})
        return {'schema_version':1,'view':view,
            'measurement_scope':'Original native Claude terminal-reported model input/output usage and retained accounting tool-call list, not independent billing or embedding-token totals. Preserve measured values and all failed eligibility gates; unknown metrics remain null.',
            'time_scope':'Harbor agent_execution seconds and separate cold outer-slot wall seconds, including native setup/index and cleanup. Native adapter run wall is retained as a separately named diagnostic.',
            'original_plan_sha256':ORIGINAL_PLAN_SHA,'supplement_plan_sha256':SUPPLEMENT_PLAN_SHA,
            'unblinding_admission_sha256':sha(root/'unblinding-admission.json'),'cases':cases}
    original=assemble(original_rows,'original_frozen_100_row_primary_with_all_absences')
    write(root/'original-primary-input.json',original)
    supplemented=copy.deepcopy(original_rows);indexed={(r['task_id'],r['profile'],r['trial_index']):i for i,r in enumerate(supplemented)}
    expected={(p['task_id'],profile,p['trial_index']) for p in supplement_plan['pairs'] for profile in p['profiles']}
    assert {(r['task_id'],r['profile'],r['trial_index']) for r in supplement_rows}==expected
    overlay=[]
    for new in supplement_rows:
        identity=(new['task_id'],new['profile'],new['trial_index']);i=indexed[identity];old=supplemented[i]
        assert old['native_execution_status']=='not_started' and old['native_pair_command_started'] is False and not old['final_answer_available']
        overlay.append({'task_id':identity[0],'profile':identity[1],'repetition':identity[2],'original_missing_row':old,'separate_supplement_row':new})
        supplemented[i]=copy.deepcopy(new)
    completion=assemble(supplemented,'separate_post_dispatch_infrastructure_completion_100_native_attempt_view')
    completion.update(original_primary_input_sha256=sha(root/'original-primary-input.json'),
        amendment='Only the exact original pre-agent-skipped pairs are overlaid. Original native failures stay in their original slots. Supplemental execution occurs later, all five original orders were baseline-first. No new exchangeability, causal, primary-preregistration or historical-pooling claim.',
        wave_order_disclosure=ref(B/'native-five-completion-v2-ci/supplement-order-wave-disclosure.json'))
    write(root/'infrastructure-completion-input.json',completion)
    write(root/'technical-completion-overlay-provenance.json',{'schema_version':1,'original_primary_input_sha256':sha(root/'original-primary-input.json'),'completion_input_sha256':sha(root/'infrastructure-completion-input.json'),'overlaid_rows':overlay,'original_native_failures_retried_or_replaced':False})
    summary={'schema_version':1,'original_scheduled_rows':100,'supplement_scheduled_rows':10,
        'completion_native_attempted_rows':sum(r['native_execution_status'] not in (None,'not_started','pending','not_run','skipped') for r in supplemented),
        'completion_rows_with_retained_claude_session_tree':sum(r['retained_claude_session_tree_available'] is True for r in supplemented),
        'session_count_interpretation':'Presence of a retained Claude session tree, not an independent count of provider API requests or a claim of valid model identity. Native indexing can fail before a Claude session is created.',
        'completion_exact_final_rows':sum(r['final_answer_available'] for r in supplemented),
        'completion_known_exact_scores':sum(r['judge']['total'] is not None for r in supplemented),
        'all_execution_scope_and_accounting_valid':all(r['execution_and_scope_valid'] is True and r['reported_metric_eligible'] is True for r in supplemented),
        'original_primary_preserved':True,'historical_trials_pooled':False,'method_sha256':sha(Path(__file__))}
    write(root/'normalization-receipt.json',summary);print(json.dumps(summary));return 0
if __name__=='__main__':raise SystemExit(main())
