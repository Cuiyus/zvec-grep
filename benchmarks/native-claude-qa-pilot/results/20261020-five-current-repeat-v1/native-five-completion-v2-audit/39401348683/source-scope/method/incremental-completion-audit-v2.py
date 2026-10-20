"""Incremental independent supplemental-wave read-only scope preparation; original cohort is immutable. No semantic automatic approval and no logged-command execution."""
from pathlib import Path
import argparse,collections,datetime,json,sys,types,shutil
sys.dont_write_bytecode=True
from scope_support import load,write,ref,sha,crosscheck,read_check,bash_hints,mcp_text_check
from source_snapshot import source_snapshot
D=Path(__file__).parent;M=D.parent
FIELDS=['strict_source_scope_valid','factual_evidence_scope_valid','external_content_observed','external_directory_metadata_observed','external_code_or_answer_facts_observed','source_unchanged','relevant_correct_workspace_zg_retrieval']
def cfg_read():
 cfg=load(D/'frozen-inputs.json');assert cfg['run_id']==39401348683 and cfg['head_sha']=='1bcd2b80df42de344fed9ff67e9f5c326fb19c24';assert {p['key']for p in cfg['schedule']}=={'q004-r04','q005-r05','q001-r07','q004-r08','q001-r09'}
 for key in ['plan','completion_plan','policy','extractor','accounting']:
  r=cfg[key];assert sha(Path(r['path']))==r['sha256'],key
 assert sha(Path(cfg['workspace_context']['control']['path']))==cfg['workspace_context']['control']['sha256']
 return cfg
def unknown(profile,reason):
 return {'profile':profile,**dict.fromkeys(FIELDS),'scope_and_retrieval_gate_passed':False,'tool_count':None,'root_tool_count':None,'child_tool_count':None,'mcp_hit_count':None,'unresolved_evidence':[reason]}
def ready(cfg,pair):
 f=Path(cfg['raw_run_root'])/pair['key'];p=f/'artifact-byte-verification.json'
 if not p.is_file():return None
 r=load(p)
 if r.get('complete') is not True:return None
 for k,v in {'run_id':cfg['run_id'],'head_sha':cfg['head_sha'],'run_attempt':1,'pair_key':pair['key'],'task':pair['task_id'],'trial_index':pair['trial_index'],'matches_api_digest':True,'exact_extracted_inventory':True,'repository':'Cuiyus/zvec-grep'}.items():assert r.get(k)==v,(pair['key'],k)
 assert sha(f/'artifact.zip')==r['artifact_sha256']
 return f,r

def prepare(cfg,pair):
 input_ready=ready(cfg,pair)
 if input_ready is None:return {'pair':pair['key'],'status':'receipt_not_complete','created':False}
 o=M/'pairs'/pair['key']
 if o.exists():return {'pair':pair['key'],'status':'existing_output_retained','created':False}
 o.mkdir();f,receipt=input_ready;raw=f/'raw';task=cfg['tasks'][pair['task_id']];S=Path(task['source_root']);manifest={x['path']:x for x in load(Path(task['source_manifest']['path']))['files']}
 local={**task,'run_id':cfg['run_id'],'head_sha':cfg['head_sha'],'protocol_id':cfg['protocol_id'],'policy':cfg['policy'],'plan':cfg['plan'],'pair':pair,'context':cfg['workspace_context'],'current_rust':cfg['rust'],'completion_plan':cfg['completion_plan'],'audit_wave_protocol_id':cfg['audit_wave_protocol_id'],'original_run_id':cfg['original_run_id'],'original_primary_rows_modified':False}
 write(o/'pair-inputs.json',local)
 receipt_files={x['path']:x for x in receipt['files']}
 def raw_ref(p):
  item=ref(p);rel=p.relative_to(raw).as_posix();rec=receipt_files.get(rel)
  assert rec and all(item[k]==rec[k] for k in ['bytes','sha256']),(pair['key'],rel)
  return item
 identity={'audit_wave_protocol_id':cfg['audit_wave_protocol_id'],'completion_plan':cfg['completion_plan'],'original_run_id':cfg['original_run_id'],'original_job_id':pair['original_job_id'],'original_primary_rows_modified':False,'receipt':ref(f/'artifact-byte-verification.json'),'zip':ref(f/'artifact.zip'),'task_source_verification':task['source_verification'],'all_consumed_raw_bytes_match_receipt':True,'context':None,'context_matches':None,'runtime_provider_context_not_inferred_from_sidecar':True}
 contextpath=raw/'live-five-context.json'
 if contextpath.is_file():
  context=load(contextpath);identity['context']=raw_ref(contextpath)
  expected={'protocol_id':cfg['protocol_id'],'key':pair['key'],'task_id':pair['task_id'],'trial_index':pair['trial_index'],'profiles':pair['profiles'],'plan_sha256':cfg['plan']['sha256'],'workspace_context_sha256':cfg['workspace_context']['sha256'],'dry_run':False}
  identity['context_field_checks']=[{'field':k,'expected':v,'actual':context.get(k),'matches':context.get(k)==v} for k,v in expected.items()]
  identity['context_matches']=all(r['matches'] for r in identity['context_field_checks'])
  identity['recorded_prepared_root']=context.get('recorded_prepared_root');identity['actual_supplied_root']='/workspace'
 else:identity['missing_context_note']='No native pair context exists; do not infer execution or scope pass.'
 supplement_path=raw/'live-supplement-context.json'
 if supplement_path.is_file():
  supplement=load(supplement_path);identity['supplement_context']=raw_ref(supplement_path)
  expected_supplement={'schema_version':1,'protocol_id':cfg['audit_wave_protocol_id'],'supplement_plan_sha256':cfg['completion_plan']['sha256'],'original_run_id':cfg['original_run_id'],'original_job_id':pair['original_job_id'],'pair':pair,'original_execution_step_skipped':True,'replace_original_missing_primary_rows':False,'original_missing_rows_preserved':True,'dry_run':False}
  identity['supplement_context_field_checks']=[{'field':k,'expected':v,'actual':supplement.get(k),'matches':supplement.get(k)==v}for k,v in expected_supplement.items()]
  identity['supplement_context_matches']=all(x['matches']for x in identity['supplement_context_field_checks'])
 else:
  identity['supplement_context_matches']=None;identity['missing_supplement_context_note']='Cannot bind supplemental wave without the retained sidecar; no approval inferred.'
 write(o/'input-identity-check.json',identity)
 source_checks=[]
 assert {p.relative_to(S).as_posix() for p in S.rglob('*') if p.is_file()}==set(manifest)
 assert not any(p.is_symlink() for p in S.rglob('*'))
 for rel,expected in manifest.items():
  item=ref(S/rel);assert all(item[k]==expected[k] for k in ['bytes','sha256']);source_checks.append(item)
 write(o/'source-file-verification.json',{'task':pair['task_id'],'file_count':len(manifest),'complete_inventory_matches':True,'source_files':source_checks})
 source_lines={rel:(S/rel).read_text(errors='replace').split('\n') for rel in manifest}
 extractor=Path(cfg['extractor']['path']);mod=types.ModuleType('frozen_scope_extractor');mod.__file__=str(extractor);exec(compile(extractor.read_bytes(),str(extractor),'exec'),mod.__dict__)
 profiles=[]
 for profile in pair['profiles']:
  out=o/profile;out.mkdir();matches=list((raw/'live/runs'/('q001-'+profile)).glob('zvec-'+pair['task_id']+'__*/agent'))
  if len(matches)!=1:
   row=unknown(profile,{'kind':'missing_or_ambiguous_agent_directory','matches':[str(p) for p in matches],'absence_is_not_no_access_or_compliance':True});row['status']='native_evidence_unavailable';write(out/'missing-evidence.json',row);profiles.append(row);continue
  agent=matches[0]
  try:
   queue=mod.extract(agent,out/'extracted',Path(cfg['accounting']['path']));cross=crosscheck(queue);write(out/'extraction-crosscheck.json',cross)
   checks=[raw_ref(Path(r['path'])) for r in cross['raw_files']]
   accpath=raw/'live/collection'/(profile+'-strict-accounting.json');acc_match=None;acc_ref=None
   if accpath.is_file():
    acc=load(accpath);acc_ref=raw_ref(accpath);acc_match={(t['session'],t['id']):t for t in acc['tools']}=={(t['session'],t['id']):t['frozen_accounting_identity'] for t in queue['tools']}
   sp=agent.parent/'qa-source-audit.json'
   if sp.is_file():
    checks.append(raw_ref(sp));snap=source_snapshot(sp,manifest,profile);write(out/'source-snapshots.json',snap)
   else:snap={'source_unchanged':None,'unresolved_evidence':['No before/after source audit file.']};write(out/'source-snapshots.json',snap)
   for t in queue['tools']:
    review={'ordinal':t['ordinal'],'session':t['session'],'name':t['name'],'tool_id':t['id'],'evidence':ref(out/'extracted'/t['evidence_path']),'manual_review_status':'pending',**dict.fromkeys(FIELDS)}
    if len(t['call_variants'])==len(t['result_variants'])==1:
     args=t['call_variants'][0]['input'];result=t['result_variants'][0]
     if t['name']=='Read':check=read_check(args,result,source_lines,manifest)
     elif t['name']=='Bash':check=bash_hints(args,result,source_lines,manifest)
     elif t['name']=='mcp__zvec_grep__zvec_grep_search':
      content=result['content'];texts=[content] if isinstance(content,str) else [x['text'] for x in content if isinstance(x,dict) and isinstance(x.get('text'),str)] if isinstance(content,list) else []
      check={'requested_root':args.get('root'),'root_equals_workspace':args.get('root')=='/workspace','native_is_error':result['is_error'],'result_metadata_matches_content':result['metadata']==content,'text_blocks':[mcp_text_check(text,source_lines,manifest) for text in texts],'new_rust_tree':cfg['rust']['tree'],'all_differences_and_metadata_require_semantic_review':True}
     else:check={'other_tool':t['name'],'requires_complete_native_tool_review':True}
     review['mechanical_check']=check
    else:review['variant_diagnostic']='Missing/multiple call or result variants retained, no preferred variant selected.'
    write(out/f'tool-check-{t["ordinal"]:04}.json',review)
   row=unknown(profile,{'kind':'semantic_review_pending','complete_commands_and_results_not_yet_approved':True});row.update(status='mechanically_prepared',tool_count=len(queue['tools']),root_tool_count=queue['root_tool_count'],child_tool_count=queue['child_tool_count'],tool_counts=dict(collections.Counter(t['name'] for t in queue['tools'])),source_unchanged=snap['source_unchanged'],extraction=ref(out/'extracted/tool-audit.json'),extraction_complete=queue['extraction_complete'],extraction_errors=queue['errors'],exhaustive_crosscheck=ref(out/'extraction-crosscheck.json'),collection_accounting=acc_ref,collection_accounting_tool_identity_match=acc_match,source_snapshots=ref(out/'source-snapshots.json'),authorized_raw_receipt_refs=checks)
   profiles.append(row)
  except Exception as e:
   row=unknown(profile,{'kind':'preparation_diagnostic','type':type(e).__name__,'message':str(e),'existing_evidence_preserved':True});row['status']='mechanical_preparation_incomplete';write(out/'preparation-failure.json',row);profiles.append(row)
 write(o/'review-pending.json',{'pair_key':pair['key'],'task_id':pair['task_id'],'run_id':cfg['run_id'],'head_sha':cfg['head_sha'],'status':'semantic_review_pending','profiles':profiles,'identity':ref(o/'input-identity-check.json'),'frozen_inputs':ref(D/'frozen-inputs.json'),'scope_and_retrieval_gate_passed':False})
 for name in ['static-return-check-v2.py','static-return-check-v3.py','verify-references.py']:
  with (o/name).open('xb') as dest:dest.write((D/name).read_bytes())
 return {'pair':pair['key'],'status':'prepared','profiles':[{'profile':p['profile'],'status':p['status'],'tools':p['tool_count']} for p in profiles]}

def snapshot(cfg):
 rows=[]
 for pair in cfg['schedule']:
  o=M/'pairs'/pair['key'];rpath=o/'report.json';ppath=o/'review-pending.json'
  record=load(rpath) if rpath.is_file() else load(ppath) if ppath.is_file() else None
  for profile in pair['profiles']:
   base={'pair_key':pair['key'],'task_id':pair['task_id'],'trial_index':pair['trial_index'],'profile':profile,'profile_order':pair['profiles'].index(profile)+1}
   row=unknown(profile,{'kind':'complete_receipt_and_review_not_yet_available'});row['status']='pending_artifact';row['audit_wave_protocol_id']=cfg['audit_wave_protocol_id'];row['original_run_id']=cfg['original_run_id'];row['original_job_id']=pair['original_job_id'];row['original_primary_rows_modified']=False
   if record:
    candidates=[p for p in record['profiles'] if p['profile']==profile];assert len(candidates)==1
    row.update(candidates[0]);row['status']=row.get('status',record['status']);row['evidence']=ref(rpath if rpath.is_file() else ppath)
   for field in FIELDS:assert row.get(field) is None or type(row.get(field)) is bool
   identity=load(o/'input-identity-check.json')if(o/'input-identity-check.json').is_file()else{}
   binding=identity.get('context_matches')is True and identity.get('supplement_context_matches')is True and identity.get('all_consumed_raw_bytes_match_receipt')is True
   row['identity_binding_valid']=binding if record else None
   row['scope_and_retrieval_gate_passed']=binding and row.get('status')=='review_complete' and row.get('strict_source_scope_valid') is True and row.get('factual_evidence_scope_valid') is True and row.get('source_unchanged') is True and (profile=='baseline' or row.get('relevant_correct_workspace_zg_retrieval') is True)
   rows.append({**base,**row})
 assert len(rows)==10 and len({(r['pair_key'],r['profile']) for r in rows})==10
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');path=M/'snapshots'/('planned-rows-'+stamp+'.json')
 write(path,{'run_id':cfg['run_id'],'head_sha':cfg['head_sha'],'frozen_plan':cfg['plan'],'completion_plan':cfg['completion_plan'],'audit_wave_protocol_id':cfg['audit_wave_protocol_id'],'original_run_id':cfg['original_run_id'],'original_primary_rows_modified':False,'planned_pairs':5,'planned_rows':10,'rows':rows,'quality_or_resource_assessment':False,'missing_fields_never_zero_or_pass':True,'report_selection_note':'Separate supplemental wave only; report.json when saved, otherwise unknown pending rows. No original report or missing row replaced.'});return ref(path)
def main():
 a=argparse.ArgumentParser();a.add_argument('command',choices=['prepare','prepare-ready','snapshot']);a.add_argument('--pair');args=a.parse_args();cfg=cfg_read()
 if args.command=='snapshot':print(json.dumps(snapshot(cfg)));return
 selected=[p for p in cfg['schedule'] if p['key']==args.pair] if args.command=='prepare' else cfg['schedule'];assert args.command!='prepare' or len(selected)==1
 for pair in selected:
  if args.command=='prepare-ready' and ready(cfg,pair) is None:continue
  print(json.dumps(prepare(cfg,pair)),flush=True)
 print(json.dumps({'snapshot':snapshot(cfg)}))
if __name__=='__main__':main()
