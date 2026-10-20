"""Export exact separately assigned supplemental finals, never original-row replacement."""
import argparse,datetime,importlib.util,json,os,shutil,sys,tempfile
from pathlib import Path
METHOD=Path(__file__).resolve().parent;B=METHOD.parent
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');H=W/'benchmarks/native-claude-qa-pilot'
sys.path[:0]=[str(H),str(W/'benchmarks/opencode-qa-pilot'),str(B/'upstream-main/benchmarks/swe-qa-bench')]
import five_completion_v2 as completion
spec=importlib.util.spec_from_file_location('_original_blind_export',B/'native-five-repeat-v1-method/build_blind_packets.py')
original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
sha,load,write,safe=original.sha,original.load,original.write,original.safe
def build(task,run,head):
 plan=completion.validate_completion(load(completion.PLAN));pairs=[p for p in plan['pairs'] if p['task_id']==task]
 if not pairs:return False
 ci=B/'native-five-completion-v2-ci'/str(run);private=B/'native-five-completion-v2-grading-private';grading=B/'native-five-completion-v2-grading'
 if not all((ci/p['key']/'artifact-byte-verification.json').is_file() for p in pairs):
  print(json.dumps({'task_id':task,'ready':False,'reason':'awaiting_all_selected_supplement_receipts'}));return False
 original.CI=ci;original.RUN=run;original.HEAD=head
 roots={p['key']:original.verify_artifact(p['key']) for p in pairs}
 assignment=private/'assignment.json';assert sha(assignment)==plan['grading']['assignment_sha256']
 allrows=load(assignment)['rows'];assert len(allrows)==2*len(plan['pairs'])==len({r['anonymous_id'] for r in allrows})
 selected=[r for r in allrows if r['task_id']==task];assert len(selected)==2*len(pairs)
 expected={(p['key'],profile,p['trial_index']) for p in pairs for profile in p['profiles']}
 assert {(r['pair_key'],r['profile'],r['repetition']) for r in selected}==expected
 original_plan=load(H/'five-repeat-plan-v1.json');ref=original_plan['prepared']['tasks'][task]
 prepared=original.source_root(task);source=prepared/'tasks'/('zvec-'+task)/'environment/source'
 assert sha(prepared/'prepared.json')==ref['metadata_sha256'] and sha(prepared/'source-manifest.json')==ref['source_manifest_sha256']
 inventory=load(prepared/'source-manifest.json');assert inventory['source_commit']==ref['source_commit'] and inventory['inventory_sha256']==ref['source_inventory_sha256']
 assert not any(p.is_symlink() for p in source.rglob('*'))
 assert sorted(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file())==sorted(f['path'] for f in inventory['files'])
 for f in inventory['files']:
  p=safe(source,f['path']);assert sha(p)==f['sha256'] and p.stat().st_size==f['bytes']
 grading.mkdir(exist_ok=True);destination=grading/task;construction=private/(task+'-packet-construction.json')
 assert not destination.exists() and not construction.exists()
 out=Path(tempfile.mkdtemp(prefix='.'+task+'-',dir=grading));public=[];private_rows=[]
 for key,name in [('question','question.md'),('rubric','rubric.json')]:
  path=safe(W/'benchmarks/zvec-issue-qa',ref[key]['path']);assert sha(path)==ref[key]['sha256'];shutil.copyfile(path,out/name)
 contract=H/original.CONTRACTS[task];assert sha(contract)==original_plan['frozen_harness_sha256'][str(contract.relative_to(W))]
 shutil.copyfile(contract,out/'grader-contract.json');shutil.copyfile(prepared/'source-manifest.json',out/'source-manifest.json')
 for row in selected:
  raw=roots[row['pair_key']];context_path=raw/'live-five-context.json';answer=None;reason=None
  if context_path.is_file():
   context=load(context_path);assert context['plan_sha256']==completion.ORIGINAL_PLAN_SHA and context['task_id']==task and context['trial_index']==row['repetition']
   assert context['key']==row['pair_key'] and context['dry_run'] is False and context['prepared']==original_plan['prepared']
   sidecar=load(raw/'live-supplement-context.json');assert sidecar['protocol_id']==completion.PROTOCOL and sidecar['supplement_plan_sha256']==sha(completion.PLAN)
   assert sidecar['pair']['key']==row['pair_key'] and sidecar['dry_run'] is False
   cp=raw/'live/collection/collection.json'
   if cp.is_file():
    matches=[p for p in load(cp)['profiles'] if p['profile']==row['profile']];assert len(matches)<=1
    if matches and matches[0].get('answer_file'):
     answer=safe(cp.parent,matches[0]['answer_file']);assert sha(answer)==matches[0]['answer_sha256']
    else:reason='exact_final_not_available_in_supplement_collection'
   else:reason='supplement_collection_unavailable'
  else:reason='supplement_execution_not_started_or_context_unavailable'
  item={'blinded_answer_id':row['anonymous_id'],'file':row['anonymous_id']+'.txt' if answer else None,'answer_sha256':sha(answer) if answer else None,'bytes':answer.stat().st_size if answer else None,'final_answer_available':answer is not None}
  if answer:shutil.copyfile(answer,out/item['file']);assert sha(out/item['file'])==item['answer_sha256']
  public.append(item);private_rows.append({**row,'source_path':str(answer) if answer else None,'absence_reason':reason,'answer_sha256':item['answer_sha256'],'receipt_sha256':sha(ci/row['pair_key']/'artifact-byte-verification.json')})
 public.sort(key=lambda row:row['blinded_answer_id'])
 manifest={'schema_version':1,'task_id':task,'scope':'Separately assigned exact supplemental finals under the unchanged original rubric. Conditions, repetition, trajectories and resources withheld. Original planned absences are preserved separately.','source_commit':ref['source_commit'],'source_root':str(source),'source_inventory_sha256':ref['source_inventory_sha256'],'source_manifest_sha256':sha(out/'source-manifest.json'),'question_sha256':sha(out/'question.md'),'rubric_sha256':sha(out/'rubric.json'),'grader_contract_sha256':sha(out/'grader-contract.json'),'answers':public,'planned_answers':len(public),'question_original_planned_answers':20,'packet_kind':'complete_separate_supplement_question_packet','available_answers':sum(a['final_answer_available'] for a in public),'quality_or_scope_judgments_included':False}
 write(out/'manifest.json',manifest)
 record={'schema_version':1,'built_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'task_id':task,'assignment_sha256':sha(assignment),'supplement_plan_sha256':sha(completion.PLAN),'run_id':run,'head_sha':head,'builder_sha256':sha(Path(__file__)),'packet_manifest_sha256':sha(out/'manifest.json'),'packet_directory':str(destination),'rows':private_rows,'original_missing_rows_preserved':True}
 os.replace(out,destination);write(construction,record);os.chmod(construction,0o600)
 print(json.dumps({'task_id':task,'ready':True,'packet':str(destination),'manifest_sha256':sha(destination/'manifest.json'),'planned_answers':len(public),'available_answers':manifest['available_answers'],'parent_mapping_read':False}));return True
if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-id',type=int,required=True);parser.add_argument('--head',required=True);parser.add_argument('--task',choices=original.TASKS);args=parser.parse_args()
 for task in [args.task] if args.task else original.TASKS:build(task,args.run_id,args.head)
