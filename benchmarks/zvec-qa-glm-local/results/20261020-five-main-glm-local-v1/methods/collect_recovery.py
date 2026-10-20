"""Download one completed first-execution recovery run; never dispatch or retry it."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import zipfile

REPO = 'Cuiyus/zvec-grep'
HEAD = '3390a27bbb2234abb5590754ff04f65bbfed4541'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def api(endpoint):
    return json.loads(subprocess.check_output(['gh','api',f'repos/{REPO}/'+endpoint]))


def save(path, value):
    if path.exists():
        assert json.loads(path.read_text()) == value
    else:
        with path.open('x') as f:
            json.dump(value,f,indent=2);f.write('\n')


def collect(run_id, output):
    run=api(f'actions/runs/{run_id}')
    assert run['id']==run_id and run['head_sha']==HEAD and run['run_attempt']==1
    assert run['event']=='workflow_dispatch' and run['status']=='completed'
    assert run['repository']['full_name']==REPO
    assert run['path']=='.github/workflows/zvec-qa-glm-native-recovery-v1.yml'
    jobs=api(f'actions/runs/{run_id}/jobs?per_page=100')
    artifacts=api(f'actions/runs/{run_id}/artifacts?per_page=100')
    chosen=[a for a in artifacts['artifacts'] if a['name']==f'zvec-qa-glm-preagent-completion-{run_id}']
    assert len(chosen)==1
    item=chosen[0]
    assert not item['expired'] and item['workflow_run']['id']==run_id and item['workflow_run']['head_sha']==HEAD
    job=[j for j in jobs['jobs'] if j['name']=='first-agent-execution']
    assert len(job)==1 and job[0]['status']=='completed' and job[0]['run_attempt']==1
    output.mkdir(parents=True,exist_ok=True)
    for name,value in [('run-metadata.json',run),('job-metadata.json',jobs),('artifacts-metadata.json',artifacts),('artifact-metadata.json',item)]:
        save(output/name,value)
    archive=output/'artifact.zip'
    if not archive.exists():
        temporary=output/'artifact.zip.partial'
        with temporary.open('wb') as f:
            subprocess.run(['gh','api',f'repos/{REPO}/actions/artifacts/{item["id"]}/zip'],stdout=f,check=True)
        os.replace(temporary,archive)
    digest=sha(archive)
    assert item['digest']=='sha256:'+digest and archive.stat().st_size==item['size_in_bytes']
    raw=output/'raw';raw.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        names=[]
        for info in z.infolist():
            rel=PurePosixPath(info.filename)
            assert not rel.is_absolute() and '..' not in rel.parts and not stat.S_ISLNK(info.external_attr>>16)
            assert str(rel)==info.filename.rstrip('/')
            if info.is_dir():continue
            names.append(info.filename)
            target=raw/info.filename;target.parent.mkdir(parents=True,exist_ok=True);content=z.read(info)
            if target.exists():assert not target.is_symlink() and target.read_bytes()==content
            else:target.write_bytes(content)
        assert len(names)==len(set(names))
        actual=sorted(p.relative_to(raw).as_posix() for p in raw.rglob('*') if p.is_file())
        assert sorted(names)==actual and not any(p.is_symlink() for p in raw.rglob('*'))
    receipt={'schema_version':1,'complete':True,'run_id':run_id,'head_sha':HEAD,
             'artifact_id':item['id'],'zip_sha256':digest,'matches_api_digest':True,
             'job_conclusion':job[0]['conclusion'],'native_agent_execution_cap':1,
             'files':[{'path':n,'bytes':(raw/n).stat().st_size,'sha256':sha(raw/n)} for n in actual]}
    save(output/'artifact-byte-verification.json',receipt)
    print(json.dumps({'run_id':run_id,'conclusion':run['conclusion'],'artifact_id':item['id'],
                      'files':len(actual),'zip_sha256':digest}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-id',type=int,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();collect(a.run_id,a.output)
