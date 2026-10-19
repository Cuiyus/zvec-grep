"""Bound retained question/tool/final coverage to existing grades, without regrading."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

BASE = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
REPO = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
OUT = Path(__file__).resolve().parent
ARCHIVE = REPO / 'benchmarks/opencode-qa-pilot/results'
EXPECTED_HEAD = '7f827f7a8755b4aebcb5d145cf179357d4684d2a'
QUESTION_SHA = '94aad1d075ef44984c2e6c330bca8d3dc6c273c1c7044c20b4bdd47c6c7854ef'
RUBRIC_SHA = '6da038c670a93213095bb3ac07c77a67f9863defa23f8ac4a3b6fd2af8a81cb9'
SOURCE = BASE / 'confirmation-v1-local/c02/prepared/tasks/zvec-q001/environment/source'
SEGMENT = 'src/db/index/segment/segment.cc'
COLLECTION = 'src/db/collection.cc'
TEST = 'tests/db/crash_recovery/write_recovery_test.cc'

def load(p):
    return json.loads(p.read_text())

def digest(data):
    return hashlib.sha256(data).hexdigest()

def sha(p):
    return digest(p.read_bytes())

def canon(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

def write(name, value):
    with (OUT/name).open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')

def git(*args):
    return subprocess.check_output(['git', '-C', str(REPO), *args], text=True).strip()

def ref(p):
    return {'path': str(p), 'sha256': sha(p), 'bytes': p.stat().st_size}

assert git('rev-parse', 'HEAD') == EXPECTED_HEAD
assert git('status', '--porcelain') == ''
run = load(BASE/'confirmation-v1-ci/run-metadata.json')
assert (run['id'], run['head_sha'], run['status'], run['conclusion'], run['run_attempt']) == (
    39321551973, '10348d1c2c863fc498f71e1dd76f04835aa92153', 'completed', 'success', 1)

phase_dirs = {'v2': ARCHIVE/'20261020-main-opencode-q001-v2',
              'confirmation': ARCHIVE/'20261020-main-opencode-q001-confirmation-v1'}
summaries = {k:load(v/'summary.json') for k,v in phase_dirs.items()}
reviews = {k:load(v/'blinded-review/review.json') for k,v in phase_dirs.items()}
question = (phase_dirs['confirmation']/'blinded-review/question.md').read_text()
rubric = load(phase_dirs['confirmation']/'blinded-review/rubric.json')
assert digest(question.encode()) == QUESTION_SHA
for p in phase_dirs.values():
    assert sha(p/'blinded-review/question.md') == QUESTION_SHA
    assert sha(p/'blinded-review/rubric.json') == RUBRIC_SHA
assert summaries['confirmation']['analysis_status'] == 'complete'
assert summaries['confirmation']['phase_status'] == 'closed_after_all_scheduled_slots'
assert summaries['confirmation']['scoped_input_candidate_confirmed_in_both_repeats'] is False

audits = {'v2':load(BASE/'live-v2-audit/final/audit.json')}
audit_paths = {'v2':BASE/'live-v2-audit/final/audit.json'}
for repeat, artifact_sha in [('c01','2cda61479b6e6ebbf9cdaa124639f6a8c1bb17ac0bdb6035325ec9517eef5a34'),
                             ('c02','0b3c0e39ded80762138da8ff2b20511dccf9cc82281ab1ea00929091c8870142')]:
    ap = BASE/f'confirmation-v1-audit/{repeat}/final/audit.json'
    audit = load(ap)
    assert audit['provenance_valid'] is True and audit['protocol_valid'] is True and not audit['errors']
    assert audit['run_id'] == run['id'] and audit['head'] == run['head_sha']
    assert audit['plan_sha256'] == 'c88bdf837cd420ae0837c64ed66402b0100b02467426600fad3126d951832050'
    assert sha(BASE/f'confirmation-v1-ci/{repeat}/artifact.zip') == artifact_sha
    assert ap.read_bytes() == (phase_dirs['confirmation']/f'native-audit/{repeat}/final/audit.json').read_bytes()
    audits[repeat] = audit; audit_paths[repeat] = ap
assert audits['v2']['protocol_valid'] is True

raw_roots = {'v2':BASE/'live-v2-ci/raw/qa-v2-run/runs',
             'c01':BASE/'confirmation-v1-ci/c01/raw/qa-confirmation-run/runs',
             'c02':BASE/'confirmation-v1-ci/c02/raw/qa-confirmation-run/runs'}
datasets = {}
for repeat, root in raw_roots.items():
    phase = 'v2' if repeat == 'v2' else 'confirmation'
    for profile in ['baseline','zvec-grep']:
        key = repeat+'/'+profile
        rows = [r for r in summaries[phase]['rows'] if r['profile']==profile and (repeat=='v2' or r['repeat']==repeat)]
        assert len(rows)==1
        row=rows[0]
        aid=row['blinded_answer_id']
        grade=next(g for g in reviews[phase]['reviews'] if g['blinded_answer_id']==aid)
        assert grade['criterion_scores']==row['criterion_points']
        assert grade['total_points']==row['quality_points']
        answer_path=phase_dirs[phase]/'blinded-review'/(aid+'.md')
        answer=answer_path.read_text()
        assert sha(answer_path)==row['answer_sha256']==reviews[phase]['inputs_hashes'][aid+'.md']
        profile_audit=next(p for p in audits[repeat]['profiles'] if p['profile']==profile)
        assert profile_audit['terminal_answer']['answer_sha256']==sha(answer_path)
        export_files=list((root/('q001-'+profile)).glob('*/qa-session-exports/*.json'))
        assert len(export_files)==1  # independently audited complete root-only trees
        export_path=export_files[0];export=load(export_path)
        final_message=next(m for m in export['messages'] if m['info']['id']==profile_audit['terminal_answer']['message_id'])
        native_answer='\n'.join(p['text'] for p in final_message['parts'] if p['type']=='text')
        assert native_answer==answer
        tools=[p for m in export['messages'] for p in m['parts'] if p['type']=='tool']
        numbered_sections=[line for line in answer.splitlines() if re.match(r'^#{1,3} [1-4]\. ',line)]
        assert len(numbered_sections)==4
        datasets[key]=dict(repeat=repeat,profile=profile,blinded_answer_id=aid,phase=phase,
            answer=answer,answer_ref=ref(answer_path),grade=grade,summary_row=row,
            export_ref=ref(export_path),tools=tools,numbered_sections=numbered_sections,
            final_message_id=final_message['info']['id'])

question_quotes={
    'Q2':'2. The call sequence and exact condition that allow optimization to succeed without preserving that data for the next reopen.',
    'Q3':'3. Why a successful return and same-handle document/index statistics do not establish durability here.',
    'Q4':'4. The smallest repair principle that restores the intended lifecycle, and a regression-test sequence that would detect this failure. Describe the repair and test; do not implement them.',
    'control':'A control collection populated and optimized in the original process, without a crash in between, does not show this loss.',
    'fresh_process':'Statistics from the same open handle still look healthy, but opening the collection again in another process reveals that the recovered documents are missing.'}
assert all(q in question for q in question_quotes.values())

tool_evidence={}
def evidence(key,part_id,path,ranges,role):
    d=datasets[key];p=next(p for p in d['tools'] if p['id']==part_id)
    output=p['state']['output'];assert p['state']['status']=='completed'
    source_lines=(SOURCE/path).read_text().splitlines()
    # Map only actually returned lines, using exact source equality. For native
    # read and bare cat/sed, full window identity is independently established.
    shown={};mode=None;inp=p['state']['input']
    if p['tool']=='read':
        disp=p['state']['metadata']['display'];assert disp['path']=='/workspace/'+path
        start,end=disp['lineStart'],disp['lineEnd']
        assert '\n'.join(source_lines[start-1:end])==disp['text']
        shown={i:source_lines[i-1] for i in range(start,min(end,len(source_lines))+1)}
        mode='exact_native_read_display_window'
    elif p['tool']=='bash':
        command=inp['command']
        sed=re.search(r"sed -n ['\"]?(\d+),(\d+)p['\"]? (\S+)$",command)
        if sed:
            start,end=int(sed[1]),int(sed[2]);assert sed[3].removeprefix('/workspace/')==path
            expected=''.join((SOURCE/path).read_text().splitlines(keepends=True)[start-1:end])
            assert expected==output
            shown={i:source_lines[i-1] for i in range(start,min(end,len(source_lines))+1)}
            mode='exact_full_bare_sed_output'
        elif command.endswith('cat '+path) or command.endswith('cat /workspace/'+path):
            assert (SOURCE/path).read_text()==output
            shown={i+1:line for i,line in enumerate(source_lines)};mode='exact_full_cat_output'
        else:
            for n,text in re.findall(r'^(\d+)[:-](.*)$',output,re.M):
                n=int(n)
                if 1<=n<=len(source_lines) and text==source_lines[n-1]:shown[n]=text
            mode='exact_numbered_grep_output_lines'
    else:
        raise ValueError('selected evidence must be a recorded source read')
    snippets=[]
    for lo,hi in ranges:
        assert all(n in shown for n in range(lo,hi+1)),(key,part_id,lo,hi)
        snippets.append(dict(path=path,start_line=lo,end_line=hi,
            frozen_source_file_sha256=sha(SOURCE/path),
            text='\n'.join(f'{n}: {shown[n]}' for n in range(lo,hi+1))))
    eid=key+'/'+part_id+'/'+role
    tool_evidence[eid]=dict(id=eid,part_id=part_id,profile_tool_ordinal=d['tools'].index(p)+1,
        tool_name=p['tool'],native_message_id=p['messageID'],native_session_id=p['sessionID'],
        input=inp,input_sha256=digest(canon(inp)),output_sha256=digest(output.encode()),
        output_bytes=len(output.encode()),export_sha256=d['export_ref']['sha256'],
        state_status=p['state']['status'],source_verification=mode,role=role,source_excerpts=snippets)
    return eid

helpers={
    'v2/baseline':('prt_155c34991001P43nQPrRY94BzS','prt_155c37301001rQBSzSKmDPmyBW'),
    'v2/zvec-grep':('prt_155d206b5001wZDXNobXRJtLHW','prt_155d2b95b0018j8sK6VTX568km'),
    'c01/baseline':('prt_155e89634001RI3HmnRojDKXd5','prt_155e8f786001eKc06oXgMdcBVu'),
    'c01/zvec-grep':('prt_155f757b7001MylpyLB3H634o0','prt_155f706eb001xY0F0UtXaAeubv'),
    'c02/baseline':('prt_156050a6f00161En1TNicCCk7E','prt_156047434001r15XBaJJP3lwno'),
    'c02/zvec-grep':('prt_15616bcb9001ne03ytSYT3AfVs','prt_15616c11f001uK54jym2t5Kg37')}
c6_evidence={}
for key,(helper,flush) in helpers.items():
    c6_evidence[key]=[
        evidence(key,helper,SEGMENT,[(4206,4215),(4303,4330)],'reader_ownership_member_open_error_and_caller_propagation'),
        evidence(key,flush,SEGMENT,[(2288,2315)],'persistence_and_member_gated_WAL_cleanup')]

special={
    ('v2/baseline','C7'):[evidence('v2/baseline','prt_155c6df7b001V4Ln39Hf41puee',TEST,[(164,179)],'normal_insert_and_reopen_fixture')],
    ('v2/zvec-grep','C4'):[evidence('v2/zvec-grep','prt_155d4e5ba001rPC7nP6lDvZr3U',COLLECTION,[(403,439)],'complete_Stats_derivation'),
        evidence('v2/zvec-grep','prt_155d29e58001DO8ni0uTYxr3si',SEGMENT,[(589,600)],'live_doc_ids_count')],
    ('c01/zvec-grep','C4'):[evidence('c01/zvec-grep','prt_155fa5f68001xZ9zPSOVy0PHFF',COLLECTION,[(403,439)],'complete_Stats_derivation'),
        evidence('c01/zvec-grep','prt_155f70303001VW6W7yJltJantd',SEGMENT,[(589,600)],'live_doc_ids_count')],
    ('c02/baseline','C2'):[evidence('c02/baseline','prt_156047434001r15XBaJJP3lwno',SEGMENT,[(2217,2229),(2288,2302)],'full_predicate_and_skipped_finalization')],
    ('c02/baseline','C7'):[evidence('c02/baseline','prt_156096167001tFMFA2N53rRJ6E',TEST,[(164,179)],'normal_insert_and_reopen_fixture')]}

def section(answer,number):
    start=re.search(r'(?m)^#{1,3} '+str(number)+r'\. .*$',answer).start()
    following=re.search(r'(?m)^#{1,3} [1-4]\. ',answer[start+1:])
    end=start+1+following.start() if following else len(answer)
    return dict(start_character=start,end_character=end,text=answer[start:end],
                sha256=digest(answer[start:end].encode()))

def missing_description(key,criterion):
    if criterion=='C4':
        return ('Final explains live doc_ids-based counts and cites Stats, but does not connect index_completeness to meta()->vector_indexed and indexed_doc_count / stats.doc_count. The exact code was returned before the final answer.')
    if criterion=='C2':
        return ('Final causal explanation cites 2220-2222 but states only the null-member arm; it does not state the full null-or-no-record predicate. The exact predicate and complete flush body were already returned. Mentions of has_record in ordinary-insert/repair discussion are preserved and do not change the existing review.')
    if criterion=='C7' and key=='v2/baseline':
        return ('Final regression reopens a handle without explicitly moving the final check to a fresh process and substitutes an extra-write-after-recovery control for the supplied no-crash control. The question contained both obligations and a normal-insert/reopen test fixture had been read.')
    if criterion=='C7':
        return ('Final regression sequence lacks a no-crash or already-flushed control arm, despite separate final prose explaining the ordinary-insert control, the question specifying that control, and a normal-insert/reopen fixture in the returned source.')
    return ('Final offers member-WAL attachment/reopen but does not require checking and propagating a failed member reopen before recovery can return success. The recorded context contains the member helper InternalError return, append_wal CHECK_RETURN_STATUS, local deferred close, and member-gated cleanup. Existing review additionally rejects any unproved dirty-only alternative; its full rationale is retained below.')

matrix=[];failed_count=0
for key,d in datasets.items():
    fails=[]
    for c in rubric['criteria']:
        cid=c['id'];points=d['grade']['criterion_scores'][cid]
        if points==c['points']:continue
        failed_count+=1
        qids={'C2':['Q2'],'C4':['Q3'],'C6':['Q4'],'C7':['Q4','control','fresh_process']}[cid]
        ids=c6_evidence[key] if cid=='C6' else special[(key,cid)]
        fails.append(dict(criterion_id=cid,existing_points=points,maximum_points=c['points'],
            question_obligations=[dict(id=q,text=question_quotes[q]) for q in qids],
            requirement_origin=('The question asks for an intended-lifecycle repair; explicit member-reopen error handling is in the frozen rubric and is a source-informed completeness obligation, not literal question wording.' if cid=='C6' else 'Explicit question wording or supplied scenario.'),
            existing_review_rationale=d['grade']['criterion_rationales'][cid],
            existing_review_supporting_quotes=d['grade'].get('supporting_answer_quotes',{}).get(cid,[]),
            recorded_source_evidence_ids=ids,
            evidence_availability=('question_requirement_and_supporting_fixture_present' if cid=='C7' else 'required_source_mechanism_present_in_returned_tool_output'),
            final_gap_as_diagnosed=missing_description(key,cid),
            final_section=section(d['answer'],{'C2':2,'C4':3,'C6':4,'C7':4}[cid]),
            regraded=False))
    matrix.append(dict(id=key,repeat=d['repeat'],profile=d['profile'],blinded_answer_id=d['blinded_answer_id'],
        existing_total_points=d['grade']['total_points'],existing_criterion_scores=d['grade']['criterion_scores'],
        existing_source_grounded_success=d['grade']['source_grounded_success'],
        answer=d['answer_ref'],native_export=d['export_ref'],native_final_message_id=d['final_message_id'],
        four_question_aligned_sections_already_present=d['numbered_sections'],failed_criteria=fails))
assert failed_count==9 and len(matrix)==6

c6_note=dict(kind='existing_C6_review_application_difference_not_a_regrade',
    all_six_member_open_failure_and_propagation_source_available=True,
    all_six_final_repair_sections_omit_explicit_member_reopen_failure_propagation=True,
    existing_scores_preserved=True,
    rubric_C6_claim=next(c['claim'] for c in rubric['criteria'] if c['id']=='C6'),
    rubric_C6_full_credit=next(c['full_credit'] for c in rubric['criteria'] if c['id']=='C6'),
    interpretation_boundary='The claim explicitly includes handling errors, while full_credit broadly accepts an equivalent minimal lifecycle repair. This is an application/calibration difference; neither original grader is declared wrong and no original score is corrected.',
    statement='Development v2 gave both C6=1 for the lifecycle principle while expressly noting missing or under-elaborated error handling. Confirmation gave all four C6=0 for missing failed-member-reopen propagation, with additional alternate-design concerns where stated. The scores are retained as separate existing judgments; C6 cannot be pooled as a clean across-phase comparable failure rate or treatment effect.',
    question_explicitness='Error handling is explicit in rubric C6. The question asks for the smallest repair principle restoring the intended lifecycle but does not literally demand checking the reopen return status. This distinction matters when proposing a question-derived structure without answer/rubric leakage.',
    rows=[dict(id=k,existing_points=d['grade']['criterion_scores']['C6'],
        existing_rationale=d['grade']['criterion_rationales']['C6'],
        existing_quotes=d['grade'].get('supporting_answer_quotes',{}).get('C6',[]),
        recorded_source_evidence_ids=c6_evidence[k],final_repair_section=section(d['answer'],4)) for k,d in datasets.items()])

# Requested bounded arithmetic robustness analysis. Only C6 points vary; this
# does not reassess factual correctness, citations or any archived judgment.
plans={k:load(REPO/'benchmarks/opencode-qa-pilot'/name) for k,name in
       [('v2','experiment-v2.json'),('confirmation','confirmation-v1.json')]}
assert plans['v2']['evaluation']==plans['confirmation']['evaluation']
relevance_counts={'v2':summaries['v2']['relevant_mcp_retrieval']['successful_calls']}
relevance_counts.update({p['repeat']:p['successful_relevant_correct_workspace_calls'] for p in summaries['confirmation']['pairs']})
max_points={c['id']:c['points'] for c in rubric['criteria']}
def arithmetic(c6_override):
    rows={}
    for key,d in datasets.items():
        scores=dict(d['grade']['criterion_scores'])
        if c6_override is not None:scores['C6']=c6_override
        checks=dict(minimum_points=sum(scores.values())>=rubric['success_rule']['minimum_points'],
            required_full_credit=all(scores[c]==max_points[c] for c in rubric['success_rule']['required_full_credit']),
            core_citations=all(d['grade']['core_citation_verification'][c] is True for c in ['C1','C2','C3']),
            critical_contradictions=len(d['grade']['critical_contradictions'])<=rubric['success_rule']['critical_contradictions_allowed'])
        rows[key]=dict(criterion_points=scores,total_points=sum(scores.values()),original_quality_gate_checks=checks,
            original_quality_gate_pass=all(checks.values()))
        if c6_override is None:assert rows[key]['original_quality_gate_pass']==d['grade']['source_grounded_success']
    pairs={}
    for repeat in ['v2','c01','c02']:
        bk,zk=repeat+'/baseline',repeat+'/zvec-grep';b,z=rows[bk],rows[zk]
        br,zr=datasets[bk]['summary_row'],datasets[zk]['summary_row']
        conditions=dict(both_protocol_valid_with_complete_captured_session_trees=
            all(x['protocol_valid'] and x['usage_complete'] for x in [br,zr]),
            both_pass_unchanged_original_quality_success_gate=b['original_quality_gate_pass'] and z['original_quality_gate_pass'],
            zg_factual_points_greater_than_or_equal_to_baseline=z['total_points']>=b['total_points'],
            zg_input_including_cache_strictly_lower_than_baseline=zr['usage']['input_tokens']<br['usage']['input_tokens'],
            at_least_one_actual_successful_relevant_correct_workspace_zg_MCP_retrieval=relevance_counts[repeat]>=1)
        assert set(conditions)==set(plans['confirmation']['confirmation']['pair_conditions'])
        quality=all(x['protocol_valid'] for x in [br,zr]) and z['original_quality_gate_pass'] and not b['original_quality_gate_pass'] and z['total_points']>=b['total_points'] and relevance_counts[repeat]>=1
        pairs[repeat]=dict(baseline_total=b['total_points'],zg_total=z['total_points'],
            baseline_quality_gate=b['original_quality_gate_pass'],zg_quality_gate=z['original_quality_gate_pass'],
            quality_candidate_under_original_evaluation_rule=quality,
            scoped_input_candidate_under_original_evaluation_rule=all(conditions.values()),
            frozen_confirmation_pair_conditions=conditions)
    return dict(answers=rows,pairs=pairs,
        development_quality_candidate=pairs['v2']['quality_candidate_under_original_evaluation_rule'],
        development_scoped_efficiency_candidate=pairs['v2']['scoped_input_candidate_under_original_evaluation_rule'],
        confirmation_both_repeats_meet_every_pair_condition=all(pairs[x]['scoped_input_candidate_under_original_evaluation_rule'] for x in ['c01','c02']))
scenarios={'original_retained_points':arithmetic(None),'hypothetical_uniform_C6_0':arithmetic(0),'hypothetical_uniform_C6_1':arithmetic(1)}
original=scenarios['original_retained_points']
assert original['development_quality_candidate']==summaries['v2']['quality_candidate']
assert original['development_scoped_efficiency_candidate']==summaries['v2']['scoped_efficiency_candidate']
assert original['confirmation_both_repeats_meet_every_pair_condition']==summaries['confirmation']['scoped_input_candidate_confirmed_in_both_repeats']
for pair in summaries['confirmation']['pairs']:
    assert original['pairs'][pair['repeat']]['scoped_input_candidate_under_original_evaluation_rule']==pair['scoped_input_candidate']
for mode in ['hypothetical_uniform_C6_0','hypothetical_uniform_C6_1']:
    s=scenarios[mode]
    assert all(s[k]==original[k] for k in ['development_quality_candidate','development_scoped_efficiency_candidate','confirmation_both_repeats_meet_every_pair_condition'])
    assert all(s['pairs'][r][k]==original['pairs'][r][k] for r in ['v2','c01','c02'] for k in ['baseline_quality_gate','zg_quality_gate','quality_candidate_under_original_evaluation_rule','scoped_input_candidate_under_original_evaluation_rule'])
sensitivity=dict(kind='hypothetical_C6_arithmetic_robustness_analysis_NOT_regrading',
    interpretation='Hold every other original criterion, citation judgment, contradiction judgment, protocol validity, usage and retrieval fact fixed. Substitute C6=0 everywhere or C6=1 everywhere. These are hypothetical point assignments, not corrected scores or new primary results.',
    frozen_quality_success_rule=rubric['success_rule'],frozen_evaluation=plans['v2']['evaluation'],
    frozen_confirmation_rule=plans['confirmation']['confirmation'],scenarios=scenarios,
    all_answer_quality_gate_decisions_unchanged=True,
    all_phase_and_pair_candidate_decisions_unchanged=True,
    explanation='Uniform C6=0 makes development 8/8 and leaves confirmation at 9/8 and 7/9. Uniform C6=1 makes development 9/9 and confirmation 10/9 and 8/10. C01 retains the zg-versus-baseline point deficit. C02 baseline still fails required full-credit C2 even when total reaches 8. Development retains its scoped-input candidate, and confirmation still fails both-repeat confirmation. C02 alone continues to meet the separate quality-candidate formula, but that is not the frozen confirmation objective and does not rescue confirmation.')

preflight=dict(worktree_head=EXPECTED_HEAD,worktree_clean=True,
    confirmation_run_metadata=ref(BASE/'confirmation-v1-ci/run-metadata.json'),
    completed_confirmation=dict(run_id=run['id'],head=run['head_sha'],status=run['status'],conclusion=run['conclusion'],run_attempt=run['run_attempt']),
    retained_audits={k:ref(v) for k,v in audit_paths.items()},
    artifact_zips={k:ref(BASE/f'confirmation-v1-ci/{k}/artifact.zip') for k in ['c01','c02']},
    archive_summaries={k:ref(v/'summary.json') for k,v in phase_dirs.items()},
    original_reviews={k:ref(v/'blinded-review/review.json') for k,v in phase_dirs.items()},
    frozen_plans={k:ref(REPO/'benchmarks/opencode-qa-pilot'/name) for k,name in [('v2','experiment-v2.json'),('confirmation','confirmation-v1.json')]},
    question_sha256=QUESTION_SHA,rubric_sha256=RUBRIC_SHA,
    all_six_native_final_blind_answer_review_summary_audit_hashes_agree=True,
    exact_source_files={p:ref(SOURCE/p) for p in [SEGMENT,COLLECTION,TEST]},
    independent_c02_C2_C7_check=ref(OUT/'c2-independent.json'),
    prior_c01_C4_diagnostic=ref(BASE/'confirmation-v1-diagnostics/c01/c4-diagnostic.json'))

write('provenance.json',preflight)
write('evidence-excerpts.json',dict(scope='Only actual tool outputs and final text; model internal reasoning is not inspected.',tools=tool_evidence))
write('evidence-matrix.json',dict(schema_version=1,kind='native_q001_failed_criterion_coverage_diagnosis',
    frozen_question=question,frozen_question_sha256=QUESTION_SHA,frozen_rubric_sha256=RUBRIC_SHA,
    existing_grade_source='Independent blinded model source audits already retained; not newly graded and not human or official benchmark scores.',
    answers=matrix,answer_count=6,failed_criterion_cells=failed_count,
    no_recorded_source_absence_identified_for_selected_failed_mechanisms=True,
    caveat='Text availability is not evidence of attention or comprehension. C7 includes question/scenario obligations and a supporting fixture, not a prewritten complete regression solution. Source availability alone does not prove why omissions occurred.',
    no_new_trial=True,no_provider_or_CI_call=True,no_plan_question_rubric_edit=True,
    quality_regraded=False,causality_established=False,counterfactual_success_established=False))
write('c6-review-application-note.json',c6_note)
write('c6-arithmetic-sensitivity.json',sensitivity)

report='''All six retained native q001 answers have source/scenario coverage gaps in the final answer despite the relevant recorded context. No new grading or experiment was performed.

Preflight: clean worktree HEAD 7f827f7a8755b4aebcb5d145cf179357d4684d2a; confirmation run 39321551973 is completed/success, attempt 1. Both final audits and ZIP hashes were revalidated. Native final, blind answer, review, summary and audit hashes agree for all six answers. The frozen question and rubric are unchanged.

Compact matrix (existing grades; tool ordinals are within each answer's native root session):

v2 development baseline, 9/10 — C7: question explicitly describes a no-crash control and a later process reopen; tool 43 read BasicInsertAndReopen at write_recovery_test.cc:164-179. The proposed regression only specifies another handle reopen and an extra-write-after-recovery control. Recorded requirement/fixture present; final test design omits/substitutes the required elements.

v2 development zg, 9/10 — C4: tool 34 returned all Stats:403-439; tool 17 returned doc_count:589-600. Final explains live counts but omits vector_indexed-derived index completeness. Complete mechanism present, compound explanation incomplete.

c01 baseline, 9/10 — C6: tool 17 returned reader defer-close, member open failure and append_wal error propagation (segment.cc:4206-4215,4303-4330); tool 20 returned member-gated cleanup:2288-2315. Final names adoption/reopen and mentions the existing reader-open error swallowing as a nearby issue, but does not require failed member reopen to abort recovery. Source present; repair failure handling omitted.

c01 zg, 8/10 — C4 and C6: Stats tool 32 and doc_count tool 14 supplied both statistics mechanisms. Helper tool 16 and flush tool 15 supplied member-open status/propagation and cleanup. Final omits metadata-derived completeness and failed-member-reopen propagation; the review also finds the claimed dirty-test-only alternative unproved for cleanup. Required source present.

c02 baseline, 7/10 — C2, C6 and C7: tool 18 returned the exact null-or-no-record guard and persistence body; helper tool 20 returned the member-open failure/propagation path; tool 51 returned the complete test file including the normal-insert/reopen fixture. Final causal explanation states only the null arm, repair omits failure propagation, and the regression lacks a control arm. All cited underlying source/scenario requirements were available. An independent read-only check confirmed the C2 predicate appeared in five tool outputs.

c02 zg, 9/10 — C6: read tool 9 returned the member-open error/propagation and reader-ownership code; read tool 10 returned member-gated cleanup. Final adoption/reopen direction omits failure propagation. Source present.

What this does and does not support:
- All six answers already have four numbered headings matching the question's four asks. A generic four-section answer template is therefore already present; headings alone are not a distinct supported explanation for the misses.
- A narrower coverage-check hypothesis could target the contents of compound asks: the complete exact condition, document AND index statistics, and regression steps preserving the supplied control/process distinctions. Recorded failures make that a concrete hypothesis, not evidence that it would improve quality or favor zg.
- C6 needs special care: explicit reopen error handling comes from the frozen rubric/source-informed lifecycle completeness, not literal question wording. Both development answers received C6=1 despite missing explicit error propagation, while all confirmation answers received C6=0. These existing decisions are preserved; do not pool them as a clean across-phase rate or treatment effect.
- The C6 claim says "handling errors" while its full-credit text accepts a minimal equivalent lifecycle repair. This is an application difference, not a declaration that either prior grader was wrong. Requested arithmetic sensitivity changes only the C6 point uniformly, without regrading: C6=0 gives development baseline/zg 8/8, c01 9/8, c02 7/9; C6=1 gives 9/9, 10/9, 8/10. Every answer quality-gate and phase/pair candidate decision stays unchanged. C01 retains its point deficit; c02 baseline still fails full-credit C2 even at 8 total. Development remains an input candidate; two-repeat confirmation remains unsuccessful. C02 alone satisfies the separate quality-candidate formula in all scenarios, which does not satisfy the frozen confirmation objective.
- Retrieval insufficiency alone does not explain the selected misses: relevant source was in actual completed tool output, including direct reads after MCP. Availability does not prove comprehension, attention, or causal use. No counterfactual success, favorable benchmark set, or generalization is established.

Detailed matrix, question wording, exact tool IDs/source ranges, output hashes and final-section excerpts are in evidence-matrix.json and evidence-excerpts.json. c6-review-application-note.json preserves the review difference. No plans, questions, rubrics, scores, frozen source, raw trace or prior audit were changed; no new trial, provider or CI call occurred.
'''
with (OUT/'report.txt').open('x') as f:f.write(report)
assert git('rev-parse','HEAD')==EXPECTED_HEAD and git('status','--porcelain')==''
write('file-inventory.json',dict(scope='This diagnosis only; inventory excludes itself',
    files={p.name:dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(OUT.iterdir()) if p.is_file()}))
print(json.dumps(dict(output=str(OUT),answers=len(matrix),failed_criteria=failed_count,
    selected_source_evidence_records=len(tool_evidence),regraded=False,worktree_head_unchanged=True)))
