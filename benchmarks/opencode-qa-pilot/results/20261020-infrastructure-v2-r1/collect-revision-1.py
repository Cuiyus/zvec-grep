#!/usr/bin/env python3
"""Read and verify the one already-dispatched run; never dispatch or retry it."""
import datetime, hashlib, json, pathlib, subprocess, zipfile, io
RUN=39314027006
REPO='Cuiyus/zvec-grep'
HEAD='cd0f4dbc321f4fe141c9d11de92be03eadfce877'
ROOT=pathlib.Path(__file__).resolve().parent/'revision-1-ci'
def api(suffix):
    return subprocess.check_output(['gh','api',f'repos/{REPO}/{suffix}'])
run=json.loads(api(f'actions/runs/{RUN}'))
assert run['id']==RUN and run['head_sha']==HEAD
assert run['event']=='push' and run['run_attempt']==1
assert run['repository']['full_name']==REPO
assert run['status']=='completed', 'Run is still active; no download or redispatch'
assert not ROOT.exists(), 'Refuse overwrite; inspect existing retained evidence'
ROOT.mkdir()
def save(name,data):
    (ROOT/name).write_bytes(data if isinstance(data,bytes) else (json.dumps(data,indent=2)+'\n').encode())
save('run-metadata.json',run)
jobs=api(f'actions/runs/{RUN}/jobs'); save('job-metadata.json',jobs)
artifacts=json.loads(api(f'actions/runs/{RUN}/artifacts')); save('artifacts-metadata.json',artifacts)
selected=[a for a in artifacts['artifacts'] if a['name']==f'q001-infrastructure-v2-{RUN}']
assert len(selected)==1, 'Required raw evidence artifact absent or ambiguous'
a=selected[0]
assert not a['expired'] and a['workflow_run']['id']==RUN
raw=api(f"actions/artifacts/{a['id']}/zip")
zipsha=hashlib.sha256(raw).hexdigest()
assert a['digest']=='sha256:'+zipsha, 'ZIP digest mismatch'
save('artifact.zip',raw)
out=ROOT/'raw';out.mkdir()
files=[]
with zipfile.ZipFile(io.BytesIO(raw)) as archive:
    seen=set()
    for item in archive.infolist():
        rel=pathlib.PurePosixPath(item.filename)
        assert not rel.is_absolute() and '..' not in rel.parts
        assert item.filename not in seen
        seen.add(item.filename)
        assert ((item.external_attr>>16)&0o170000)!=0o120000, 'No symlinks permitted'
        if item.is_dir(): continue
        b=archive.read(item); dst=out.joinpath(*rel.parts)
        dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(b)
        files.append({'path':rel.as_posix(),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
save('artifact-byte-verification.json',{'run_id':RUN,'artifact_id':a['id'],'zip_sha256':zipsha,'file_count':len(files),'files':sorted(files,key=lambda x:x['path']),'verified_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
log=subprocess.check_output(['gh','run','view',str(RUN),'--repo',REPO,'--log'])
save('job-log.txt',log)
print(json.dumps({'run_id':RUN,'conclusion':run['conclusion'],'artifact_id':a['id'],'sha256':zipsha,'files':len(files),'root':str(ROOT)}))
