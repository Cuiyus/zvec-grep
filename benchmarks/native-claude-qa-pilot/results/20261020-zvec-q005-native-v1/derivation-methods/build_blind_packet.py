"""Build two exact anonymous q005 finals after API-bound retention is complete."""
import datetime, hashlib, json, os, shutil, tempfile, zipfile
from pathlib import Path, PurePosixPath

B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
H=W/'benchmarks/native-claude-qa-pilot'
RUN=39383511290
HEAD='30fe7fc4b80027cf62006bbe9d3de59445f10a16'
PLAN_SHA='d29aa89c2c541fcb7088833310b87ed712d910c43084678fa5d6f1880ded4027'
ASSIGNMENT_SHA='d2bad030dbf0bd964fe2c5052418c87a54238f3488f60a50f5941168bd10c502'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p): return json.loads(p.read_text())
def write(p,x):
    with p.open('x') as f: json.dump(x,f,indent=2);f.write('\n')
def safe(root,relative):
    p=PurePosixPath(relative)
    if not relative or p.is_absolute() or '..' in p.parts or str(p)!=relative: raise ValueError('unsafe_relative')
    path=root/relative
    if path.is_symlink() or not path.is_file() or root.resolve() not in path.resolve().parents: raise ValueError('unsafe_input')
    return path

def verify_artifact(folder,key):
    r=load(folder/'artifact-byte-verification.json');meta=load(folder/'artifact-metadata.json');zp=folder/'artifact.zip';raw=folder/'raw'
    assert r['complete'] and r['exact_extracted_inventory'] and r['matches_api_digest']
    assert r['run_id']==RUN and r['head_sha']==HEAD and r['run_attempt']==1 and r['artifact_key']==key
    assert sha(zp)==r['artifact_sha256'] and meta['digest']=='sha256:'+sha(zp) and zp.stat().st_size==meta['size_in_bytes']
    assert sorted(p.relative_to(raw).as_posix() for p in raw.rglob('*') if p.is_file())==sorted(f['path'] for f in r['files'])
    with zipfile.ZipFile(zp) as z:
        assert sorted(n for n in z.namelist() if not n.endswith('/'))==sorted(f['path'] for f in r['files'])
        for f in r['files']:
            p=safe(raw,f['path']);assert sha(p)==f['sha256'] and p.stat().st_size==f['bytes'] and z.read(f['path'])==p.read_bytes()
    return raw

def build():
    ci=B/'native-q005-live-v1-ci'/str(RUN)
    keys=['baseline','zvec-grep','joint']
    if not all((ci/k/'artifact-byte-verification.json').is_file() for k in keys):
        print(json.dumps({'ready':False,'required_verified_live_artifacts':keys,'private_assignment_read':False}));return
    assert sha(H/'runtime-live-plan-v1.json')==PLAN_SHA
    roots={k:verify_artifact(ci/k,k) for k in keys}
    private=B/'native-q005-live-v1-grading-private';assignment=private/'assignment.json'
    assert sha(assignment)==ASSIGNMENT_SHA
    rows=load(assignment)['rows'];assert len(rows)==2 and {r['profile'] for r in rows}=={'baseline','zvec-grep'} and len({r['anonymous_id'] for r in rows})==2
    assert all(r['task_id']=='q005' and r['repetition']==1 for r in rows)
    prepared=B/'native-runtime-q005-preparation-v1/prepared/q005';metadata=load(prepared/'prepared.json');source=prepared/'tasks/zvec-q005/environment/source'
    inventory=load(prepared/'source-manifest.json');files=inventory['files']
    assert len(files)==1596 and inventory['inventory_sha256']==metadata['source_inventory_sha256']=='129579aae937735ed79c444bd6f83a05e42058a679e0077ff39599c9ce5d4eef'
    assert sorted(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file())==sorted(f['path'] for f in files)
    for f in files:
        p=safe(source,f['path']);assert sha(p)==f['sha256'] and p.stat().st_size==f['bytes']
    execution=load(roots['joint']/'live/execution.json');assert execution['plan_sha256']==PLAN_SHA and execution['dry_run'] is False
    for profile in ['baseline','zvec-grep']:
        context=load(roots[profile]/'live-q005-worker-context.json')
        assert context['plan_sha256']==PLAN_SHA and context['task_id']=='q005' and context['profile']==profile and context['dry_run'] is False
    packetroot=B/'native-q005-live-v1-grading';packetroot.mkdir(exist_ok=True);destination=packetroot/'packet'
    assert not destination.exists() and not (private/'packet-construction.json').exists()
    out=Path(tempfile.mkdtemp(prefix='.packet-',dir=packetroot));public=[];private_rows=[]
    for key,name in [('question','question.md'),('rubric','rubric.json')]:
        p=safe(W/'benchmarks/zvec-issue-qa',metadata[key]['path']);assert sha(p)==metadata[key]['sha256'];shutil.copyfile(p,out/name)
    shutil.copyfile(H/'runtime-grader-contract-v1.json',out/'grader-contract.json');shutil.copyfile(prepared/'source-manifest.json',out/'source-manifest.json')
    collection_path=roots['joint']/'live/collection/collection.json';collection=load(collection_path) if collection_path.is_file() else None
    for row in rows:
        answer=None
        if collection:
            matches=[p for p in collection['profiles'] if p['profile']==row['profile']];assert len(matches)<=1
            if matches and matches[0].get('answer_file'):
                answer=safe(collection_path.parent,matches[0]['answer_file']);assert sha(answer)==matches[0]['answer_sha256']
        item={'blinded_answer_id':row['anonymous_id'],'file':row['anonymous_id']+'.txt' if answer else None,'answer_sha256':sha(answer) if answer else None,'bytes':answer.stat().st_size if answer else None,'final_answer_available':answer is not None}
        if answer: shutil.copyfile(answer,out/item['file']);assert sha(out/item['file'])==item['answer_sha256']
        public.append(item);private_rows.append({**row,'source_path':str(answer) if answer else None,'answer_sha256':item['answer_sha256'],'absence_reason':None if answer else 'joint_exact_final_unavailable'})
    manifest={'schema_version':1,'task_id':'q005','scope':'Exact anonymous finals and original grading inputs only; no conditions, trajectories or resources. Unavailable is not an invented zero score.','source_commit':metadata['source_commit'],'source_root':str(source),'source_inventory_sha256':metadata['source_inventory_sha256'],'source_manifest_sha256':sha(out/'source-manifest.json'),'question_sha256':sha(out/'question.md'),'rubric_sha256':sha(out/'rubric.json'),'grader_contract_sha256':sha(out/'grader-contract.json'),'answers':public,'planned_answers':2,'available_answers':sum(x['final_answer_available'] for x in public),'quality_or_scope_judgments_included':False}
    write(out/'manifest.json',manifest)
    record={'schema_version':1,'built_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'assignment_sha256':ASSIGNMENT_SHA,'plan_sha256':PLAN_SHA,'run_id':RUN,'head_sha':HEAD,'builder_sha256':sha(Path(__file__)),'packet_manifest_sha256':sha(out/'manifest.json'),'original_artifact_receipt_sha256':{k:sha(ci/k/'artifact-byte-verification.json') for k in keys},'rows':private_rows}
    os.replace(out,destination);write(private/'packet-construction.json',record);os.chmod(private/'packet-construction.json',0o600)
    print(json.dumps({'ready':True,'packet':str(destination),'manifest_sha256':sha(destination/'manifest.json'),'planned_answers':2,'available_answers':manifest['available_answers'],'quality_graded':False}))

if __name__=='__main__':build()
