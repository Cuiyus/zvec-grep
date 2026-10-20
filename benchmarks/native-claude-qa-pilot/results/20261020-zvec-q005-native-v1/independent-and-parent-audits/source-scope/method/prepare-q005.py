"""Read-only raw-tool extraction and source/receipt checks; no semantic pass decisions."""
from pathlib import Path
import sys,types,collections
sys.dont_write_bytecode=True
from scope_support import load,write,ref,sha,crosscheck,read_check,bash_hints,mcp_text_check
from prepare_pair import source_snapshot
M=Path(__file__).parent;O=M.parent;cfg=load(M/'frozen-inputs.json');R=Path(cfg['raw_run_root']);raw=R/'joint/raw'
for key in ['plan','policy','extractor','accounting','source_manifest','question']:
 assert sha(Path(cfg[key]['path']))==cfg[key]['sha256'],key
receipts={k:load(R/k/'artifact-byte-verification.json') for k in ['joint','baseline','zvec-grep','preflight']}
identity=[]
for k,r in receipts.items():
 for field,expected in {'run_id':cfg['run_id'],'head_sha':cfg['head_sha'],'run_attempt':1,'complete':True,'exact_extracted_inventory':True,'matches_api_digest':True,'repository':'Cuiyus/zvec-grep'}.items():assert r[field]==expected,(k,field)
 assert sha(R/k/'artifact.zip')==r['artifact_sha256']
 identity.append({'artifact':k,'receipt':ref(R/k/'artifact-byte-verification.json'),'zip':ref(R/k/'artifact.zip')})
for profile in cfg['profiles']:
 p=R/profile/'raw/live-q005-worker-context.json';c=load(p)
 assert c['profile']==profile and c['task_id']=='q005' and c['plan_sha256']==cfg['plan']['sha256']
 assert sha(p)==sha(raw/'workers'/profile/'raw/live-q005-worker-context.json')
 zip_matches=[p for p in (raw/'workers'/profile).iterdir() if p.suffix=='.zip']
 assert len(zip_matches)==1 and sha(zip_matches[0])==receipts[profile]['artifact_sha256']
 identity.append({'profile':profile,'context':ref(p),'actual_context':c,'joint_embedded_context':ref(raw/'workers'/profile/'raw/live-q005-worker-context.json'),'joint_worker_zip':ref(zip_matches[0])})
assert sha(raw/'runtime-live-plan-v1.json')==cfg['plan']['sha256']
write(O/'input-identity-check.json',{'checks':identity,'all_match':True,'frozen_plan':cfg['plan'],'joint_plan':ref(raw/'runtime-live-plan-v1.json')})
md=load(Path(cfg['source_manifest']['path']));manifest={x['path']:x for x in md['files']};S=Path(cfg['source_root'])
assert len(manifest)==1596 and md['source_commit']==cfg['source_commit'] and md['inventory_sha256']==cfg['source_inventory_sha256']
assert {p.relative_to(S).as_posix() for p in S.rglob('*') if p.is_file()}==set(manifest)
assert not any(p.is_symlink() for p in S.rglob('*'))
items=[]
for name,row in manifest.items():
 item=ref(S/name);assert all(item[k]==row[k] for k in ['bytes','sha256']);item['matches_manifest']=True;items.append(item)
write(O/'source-file-verification.json',{'complete_inventory_matches':True,'source_commit':cfg['source_commit'],'inventory_sha256':cfg['source_inventory_sha256'],'file_count':1596,'source_files':items})
source_lines={k:(S/k).read_text(errors='replace').split('\n') for k in manifest}
extractor=Path(cfg['extractor']['path']);module=types.ModuleType('frozen_extractor');module.__file__=str(extractor);exec(compile(extractor.read_bytes(),str(extractor),'exec'),module.__dict__)
profiles=[];receiptmap={x['path']:x for x in receipts['joint']['files']}
for profile in cfg['profiles']:
 out=O/profile;out.mkdir();trials=list((raw/'live/runs'/('q001-'+profile)).glob('zvec-q005__*/agent'));assert len(trials)==1;agent=trials[0]
 q=module.extract(agent,out/'extracted',Path(cfg['accounting']['path']));cross=crosscheck(q);write(out/'extraction-crosscheck.json',cross)
 accpath=raw/'live/collection'/(profile+'-strict-accounting.json');acc=load(accpath)
 assert {(t['session'],t['id']):t for t in acc['tools']}=={(t['session'],t['id']):t['frozen_accounting_identity'] for t in q['tools']}
 snapshots=source_snapshot(agent.parent/'qa-source-audit.json',manifest,profile);write(out/'source-snapshots.json',snapshots)
 for t in q['tools']:
  assert len(t['call_variants'])==len(t['result_variants'])==1
  args=t['call_variants'][0]['input'];result=t['result_variants'][0]
  if t['name']=='Read':check=read_check(args,result,source_lines,manifest)
  elif t['name']=='Bash':check=bash_hints(args,result,source_lines,manifest)
  elif t['name']=='mcp__zvec_grep__zvec_grep_search':
   blocks=result['content'];texts=[blocks] if isinstance(blocks,str) else [x['text'] for x in blocks if isinstance(x,dict) and isinstance(x.get('text'),str)]
   check={'requested_root':args.get('root'),'root_equals_workspace':args.get('root')=='/workspace','native_is_error':result['is_error'],'result_metadata_matches_content':result['metadata']==blocks,'text_blocks':[mcp_text_check(text,source_lines,manifest) for text in texts]}
  else:check={'other_tool':t['name'],'requires_semantic_review':True}
  write(out/f'tool-check-{t["ordinal"]:04}.json',{'ordinal':t['ordinal'],'session':t['session'],'name':t['name'],'tool_id':t['id'],'evidence':ref(out/'extracted'/t['evidence_path']),'mechanical_check':check,'manual_review_status':'pending'})
 refs=cross['raw_files']+[ref(accpath),ref(agent.parent/'qa-source-audit.json')];checked=[]
 for item in refs:
  p=Path(item['path']);rel=p.relative_to(raw).as_posix();r=receiptmap[rel];assert all(r[k]==item[k] for k in ['bytes','sha256'])
  worker_rel=p.relative_to(raw/'live').as_posix() if p.is_relative_to(raw/'live') else None
  copies=[]
  # Raw tools and source snapshots must match both original worker and the worker embedded in joint; collection accounting is joint-derived.
  if worker_rel and not worker_rel.startswith('collection/'):
   for wp in [R/profile/'raw/live'/worker_rel,raw/'workers'/profile/'raw/live'/worker_rel]:
    assert sha(wp)==item['sha256'];copies.append(ref(wp))
  checked.append({'reference':item,'receipt_record':r,'original_worker_copies':copies,'all_match':True})
 profiles.append({'profile':profile,'tools':len(q['tools']),'root':q['root_tool_count'],'child':q['child_tool_count'],'tool_counts':dict(collections.Counter(t['name'] for t in q['tools'])),'extraction_complete':q['extraction_complete'],'errors':q['errors'],'crosscheck':ref(out/'extraction-crosscheck.json'),'all_raw_receipt_and_worker_copies_match':True,'raw_checks':checked,'source_snapshots':ref(out/'source-snapshots.json'),'source_unchanged':snapshots['source_unchanged']})
write(O/'review-pending.json',{'status':'mechanical_preparation_complete_semantic_pending','profiles':profiles,'source_identity':ref(O/'source-file-verification.json'),'artifact_identity':ref(O/'input-identity-check.json'),'strict_source_scope_valid':None,'gate':False})
print([{k:p[k] for k in ['profile','tools','root','child','tool_counts','extraction_complete','errors','source_unchanged']} for p in profiles])
