import copy,datetime,gzip,hashlib,json,shutil
from pathlib import Path
w=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');qa=w/'benchmarks/zvec-issue-qa';b=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot');draft=b/'zvec-next-runtime-qa-preparation-v1';prov=qa/'evaluator/provenance/q005';prov.mkdir(parents=True)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_bytes())
def write(p,x):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')
review=load(draft/'independent-draft-review.json');assert not review['blocking_findings']
for p in draft.iterdir():
 if p.is_file():shutil.copyfile(p,prov/p.name)
pool=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/architecture-pilot/closed-issues-search-raw.json');raw=pool.read_bytes();assert hashlib.sha256(raw).hexdigest()=='9107051d444f4639f123f476887e0d3c9f83d6a8e0afd9beb5e8cd3cfb507d9c'
(prov/'closed-issues-pool-original.json.gz').write_bytes(gzip.compress(raw,mtime=0))
ordered=sorted(load(pool)['items'],key=lambda r:(r['closed_at'],r['number']),reverse=True)
assert [r['number'] for r in ordered[:5]]==[794,758,294,622,395]
question=qa/'tasks/q005/instruction.md';question.parent.mkdir(parents=True);shutil.copyfile(draft/'question-draft.md',question)
r=load(draft/'rubric-draft.json');original=copy.deepcopy(r)
r['task_id']='q005';r['status']='Source-reviewed semantics; canonical acceptance is recorded separately in provenance.';r['task_type']='runtime_api_behavior_qa'
r['evidence_spans']=r.pop('evidence');r['success_rule']=r.pop('proposed_source_grounded_success')
r['limits']=[x for x in r['limits'] if not x.startswith('Draft semantics')]
r['limits'].insert(0,'Exact source review and canonical/export receipts are host-only provenance; execution requires a separate frozen native plan. No native q005 answer exists at authoring.')
r['provenance']='evaluator/provenance/q005/canonical-normalization.json'
rubric=qa/'evaluator/q005.json';write(rubric,r)
assert r['units']==original['units'] and r['scoring_rules']==original['scoring_rules'] and r['success_rule']==original['proposed_source_grounded_success'] and r['evidence_spans']==original['evidence']
base=load(qa/'dataset-architecture-v1.json');task=copy.deepcopy(base['tasks'][0]);task.update(id='q005',instruction='tasks/q005/instruction.md',instruction_sha256=sha(question),evaluator='evaluator/q005.json',evaluator_sha256=sha(rubric),forbidden_corpus_markers=['https://github.com/alibaba/zvec/issues/395','zvec-closed-issue-runtime-qa-v1','zvec-next-runtime-qa-preparation-v1','q005-draft'])
dataset={'schema_version':1,'dataset_id':'zvec-closed-issue-runtime-qa-v1','created_date':'2026-10-20','split':'runtime-api-development','independent_task_count':1,'selection_before_candidate_solver_results':True,'development_selection_disclosure':'Continues the saved bounded first-100 closed-issue pool after q004, in the original closed_at/number order. #294 tooling and #622 build-only cases were excluded by the prospectively recorded runtime/API scope. #395 was the first source-verified eligible case. The parent knows historical other-case outcomes; q004 native execution was running and its answers were not collected/read during this selection and source review. No q005 solver answer, query or index build exists. This is purposive development, not a random or contamination-free holdout.','selection_policy':'evaluator/provenance/q005/selection-policy.json','candidate_selection':'evaluator/provenance/q005/candidate-selection.json','tasks':[task],'status_at_authoring':'source-reviewed_semantics_pending_exact_canonical_review_and_native_execution_freeze'}
dataset_path=qa/'dataset-runtime-v1.json';write(dataset_path,dataset)
write(prov/'canonical-normalization.json',{'schema_version':1,'normalized_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'reviewed_draft':{'question_sha256':sha(draft/'question-draft.md'),'rubric_sha256':sha(draft/'rubric-draft.json'),'independent_review_sha256':sha(draft/'independent-draft-review.json'),'blocking_findings':0},'canonical_assets':{str(p.relative_to(w)):{'sha256':sha(p),'bytes':p.stat().st_size} for p in [question,rubric,dataset_path]},'changes':['Question bytes unchanged.','Rubric task ID q005-draft becomes q005; authoring/status/provenance metadata updated.','evidence renamed evidence_spans and proposed_source_grounded_success renamed success_rule; values byte-equivalent as JSON data.','All ten unit claims/weights, scoring rules, success threshold/mandatory units and 18 source spans unchanged.','Dataset uses the existing complete 1596-file immutable 1a5730 source; source/export verification and exact canonical acceptance remain separate.'],'ordered_pool_first_five_reconstructed':[r['number'] for r in ordered[:5]],'pool_decompressed_sha256':hashlib.sha256(raw).hexdigest(),'no_solver_or_retrieval_execution':True})
print(json.dumps({'question_sha256':sha(question),'rubric_sha256':sha(rubric),'dataset_sha256':sha(dataset_path),'source_files':task['source_file_count'],'source_inventory_sha256':task['source_inventory_sha256']}))
