"""Verify retained original artifacts and export anonymous fixed-bank finals.

The assignment is consumed only inside this process and written only to its
private construction receipt. No condition, repetition, resource or run label
is exported with an answer. Missing scheduled finals remain explicit unknowns.
"""
import argparse, datetime, hashlib, json, os, shutil, tempfile, zipfile
from pathlib import Path, PurePosixPath

W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
H=W/'benchmarks/native-claude-qa-pilot'
CI=B/'native-five-repeat-v1-ci/39392884840'
G=B/'native-five-repeat-v1-grading'
PRIVATE=B/'native-five-repeat-v1-grading-private'
PLAN_SHA='cbd1e29c7badfe005c5e36392b9eeb7e26b016b890a92e44d5251096f6af9d54'
ASSIGNMENT_SHA='09c5d767e250fb50a43d4c5035d70827083930663ad38b129fe38112675ad9d1'
RUN=39392884840
HEAD='df0d37796435a596b4f6d62215a8fe28f401774a'
TASKS=('q001','q002','q003','q004','q005')
CONTRACTS={'q001':'grader-contract-v1.json','q002':'expansion-grader-contract-v1.json',
           'q003':'expansion-grader-contract-v1.json','q004':'architecture-grader-contract-v1.json',
           'q005':'runtime-grader-contract-v1.json'}

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def write(p,x):
    with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def safe(root,relative):
    p=PurePosixPath(relative)
    if not relative or p.is_absolute() or '..' in p.parts or str(p)!=relative:raise ValueError('unsafe_relative')
    target=root/relative
    if target.is_symlink() or not target.is_file() or root.resolve() not in target.resolve().parents:raise ValueError('unsafe_input')
    return target
def verify_artifact(key):
    folder=CI/key;r=load(folder/'artifact-byte-verification.json');meta=load(folder/'artifact-metadata.json')
    raw=folder/'raw';zp=folder/'artifact.zip'
    assert r['complete'] and r['exact_extracted_inventory'] and r['matches_api_digest']
    assert r['run_id']==RUN and r['head_sha']==HEAD and r['run_attempt']==1 and r['pair_key']==key
    assert meta['digest']=='sha256:'+sha(zp)=='sha256:'+r['artifact_sha256']
    assert zp.stat().st_size==r['artifact_bytes']==meta['size_in_bytes']
    names=sorted(f['path'] for f in r['files'])
    assert not any(p.is_symlink() for p in raw.rglob('*'))
    assert sorted(p.relative_to(raw).as_posix() for p in raw.rglob('*') if p.is_file())==names
    with zipfile.ZipFile(zp) as archive:
        assert archive.testzip() is None
        assert sorted(n for n in archive.namelist() if not n.endswith('/'))==names
        for f in r['files']:
            p=safe(raw,f['path'])
            assert sha(p)==f['sha256'] and p.stat().st_size==f['bytes'] and p.read_bytes()==archive.read(f['path'])
    return raw
def source_root(task):
    if task=='q001':return B/'native-claude-live-preflight-v1/prepared'
    group='reserves' if task in ('q002','q003') else 'architecture' if task=='q004' else 'runtime'
    return B/'native-five-repeat-v1-preparation/prepared'/group/task
def index(row):
    keys=[k for k in ('trial_index','repetition') if k in row]
    assert len(keys)==1 and type(row[keys[0]]) is int
    return row[keys[0]]
def build(task):
    keys=[f'{task}-r{i:02d}' for i in range(1,11)]
    ready=sum((CI/k/'artifact-byte-verification.json').is_file() for k in keys)
    if ready!=10:
        print(json.dumps({'task_id':task,'ready':False,'retained_pairs':ready,'required_pairs':10,'private_assignment_read':False}));return False
    assert sha(H/'five-repeat-plan-v1.json')==PLAN_SHA
    plan=load(H/'five-repeat-plan-v1.json');ref=plan['prepared']['tasks'][task]
    prepared=source_root(task);source=prepared/'tasks'/('zvec-'+task)/'environment/source'
    assert sha(prepared/'prepared.json')==ref['metadata_sha256']
    assert sha(prepared/'source-manifest.json')==ref['source_manifest_sha256']
    inventory=load(prepared/'source-manifest.json');files=inventory['files']
    assert inventory['source_commit']==ref['source_commit'] and inventory['inventory_sha256']==ref['source_inventory_sha256']
    assert not any(p.is_symlink() for p in source.rglob('*'))
    assert sorted(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file())==sorted(f['path'] for f in files)
    for f in files:
        p=safe(source,f['path']);assert sha(p)==f['sha256'] and p.stat().st_size==f['bytes']
    roots={key:verify_artifact(key) for key in keys}
    assignment=PRIVATE/'assignment.json'
    assert sha(assignment)==ASSIGNMENT_SHA==plan['grading']['assignment_sha256']
    rows=load(assignment)['rows']
    assert len(rows)==100 and len({r['anonymous_id'] for r in rows})==100
    for t in TASKS:
        for p in ('baseline','zvec-grep'):
            assert {index(r) for r in rows if r['task_id']==t and r['profile']==p}==set(range(1,11))
    selected=[r for r in rows if r['task_id']==task];assert len(selected)==20
    G.mkdir(exist_ok=True);destination=G/task;construction=PRIVATE/(task+'-packet-construction.json')
    assert not destination.exists() and not construction.exists()
    out=Path(tempfile.mkdtemp(prefix='.'+task+'-',dir=G));public=[];private_rows=[]
    for key,name in [('question','question.md'),('rubric','rubric.json')]:
        p=safe(W/'benchmarks/zvec-issue-qa',ref[key]['path']);assert sha(p)==ref[key]['sha256'];shutil.copyfile(p,out/name)
    contract=H/CONTRACTS[task]
    assert sha(contract)==plan['frozen_harness_sha256'][str(contract.relative_to(W))]
    shutil.copyfile(contract,out/'grader-contract.json');shutil.copyfile(prepared/'source-manifest.json',out/'source-manifest.json')
    for row in selected:
        key=f'{task}-r{index(row):02d}';raw=roots[key];context_path=raw/'live-five-context.json'
        context=load(context_path) if context_path.is_file() else None
        answer=None;reason=None
        if context is not None:
            assert context['plan_sha256']==PLAN_SHA and context['task_id']==task and context['trial_index']==index(row)
            assert context['dry_run'] is False and context['key']==key and context['protocol_id']=='native-claude-fixed-five-repeat-v1'
            assert context['prepared']==plan['prepared'] and context['candidate_manifest_sha256']==plan['candidate_manifest']['sha256']
        collection_path=raw/'live/collection/collection.json'
        if context is None:reason='execution_not_started_or_context_unavailable'
        elif collection_path.is_file():
            matches=[p for p in load(collection_path)['profiles'] if p['profile']==row['profile']];assert len(matches)<=1
            if matches and matches[0].get('answer_file'):
                answer=safe(collection_path.parent,matches[0]['answer_file']);assert sha(answer)==matches[0]['answer_sha256']
            else:reason='exact_final_not_available_in_original_collection'
        else:reason='original_collection_unavailable'
        item={'blinded_answer_id':row['anonymous_id'],'file':row['anonymous_id']+'.txt' if answer else None,
              'answer_sha256':sha(answer) if answer else None,'bytes':answer.stat().st_size if answer else None,
              'final_answer_available':answer is not None}
        if answer:shutil.copyfile(answer,out/item['file']);assert sha(out/item['file'])==item['answer_sha256']
        public.append(item);private_rows.append({**row,'pair_key':key,'source_path':str(answer) if answer else None,
            'absence_reason':reason,'answer_sha256':item['answer_sha256'],'receipt_sha256':sha(CI/key/'artifact-byte-verification.json')})
    public.sort(key=lambda row:row['blinded_answer_id'])
    manifest={'schema_version':1,'task_id':task,'scope':'Exact anonymous final answers and unchanged grading inputs. Conditions, repetition, trajectories and resources withheld. Missing planned finals remain unknown.',
        'source_commit':ref['source_commit'],'source_root':str(source),'source_inventory_sha256':ref['source_inventory_sha256'],
        'source_manifest_sha256':sha(out/'source-manifest.json'),'question_sha256':sha(out/'question.md'),
        'rubric_sha256':sha(out/'rubric.json'),'grader_contract_sha256':sha(out/'grader-contract.json'),
        'answers':public,'planned_answers':20,'available_answers':sum(a['final_answer_available'] for a in public),
        'quality_or_scope_judgments_included':False}
    write(out/'manifest.json',manifest)
    record={'schema_version':1,'built_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'task_id':task,
        'assignment_sha256':ASSIGNMENT_SHA,'plan_sha256':PLAN_SHA,'run_id':RUN,'head_sha':HEAD,
        'builder_sha256':sha(Path(__file__)),'packet_manifest_sha256':sha(out/'manifest.json'),'rows':private_rows}
    os.replace(out,destination);write(construction,record);os.chmod(construction,0o600)
    print(json.dumps({'task_id':task,'ready':True,'packet':str(destination),'manifest_sha256':sha(destination/'manifest.json'),
        'planned_answers':20,'available_answers':manifest['available_answers'],'quality_graded':False,'parent_mapping_read':False}))
    return True
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task',choices=TASKS,required=True);a=p.parse_args();build(a.task)
