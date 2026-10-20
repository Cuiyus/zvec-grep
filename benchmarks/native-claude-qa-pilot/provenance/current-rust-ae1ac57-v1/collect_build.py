"""Retain and verify the single fork-built current Rust platform artifact."""
import datetime,hashlib,json,os,subprocess,sys,tarfile,zipfile
from pathlib import Path,PurePosixPath

B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
sys.path[:0]=[str(W/'benchmarks/native-claude-qa-pilot'),str(W/'benchmarks/opencode-qa-pilot')]
import rust_candidate_v1 as candidate

RUN=39388715134
HEAD='aac39e3d7946e2c7a42abfad7bd2f26b8c2bbf0b'
REPO='Cuiyus/zvec-grep'
D=B/'native-current-rust-v1-ci'/str(RUN)
NAME='benchmark-current-rust-linux-x64-gnu'

def sha(p):return candidate.digest(p)
def write(p,data):candidate.write(p,data)
def api(path):return json.loads(subprocess.check_output(['gh','api',path]))

def main():
    run=api(f'repos/{REPO}/actions/runs/{RUN}')
    jobs=api(f'repos/{REPO}/actions/runs/{RUN}/jobs?per_page=100')
    assert run['head_sha']==HEAD and run['run_attempt']==1 and run['path']=='.github/workflows/benchmark-current-rust-build-v1.yml'
    D.parent.mkdir(parents=True,exist_ok=True)
    observed={'run':run,'jobs':jobs,'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if run['status']!='completed':
        p=D.parent/('observation-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        write(p,observed)
        print(json.dumps({'ready':False,'status':run['status'],'jobs':[{'id':j['id'],'status':j['status'],'conclusion':j['conclusion'],'step':[s['name'] for s in j['steps'] if s['status']=='in_progress']} for j in jobs['jobs']]}));return
    assert run['conclusion']=='success' and len(jobs['jobs'])==1 and jobs['jobs'][0]['conclusion']=='success'
    if D.exists():
        assert (D/'candidate/candidate-manifest.json').is_file()
        print(json.dumps({'already_retained':True,'manifest_sha256':sha(D/'candidate/candidate-manifest.json')}));return
    artifacts=api(f'repos/{REPO}/actions/runs/{RUN}/artifacts?per_page=100')
    matches=[a for a in artifacts['artifacts'] if a['name']==NAME];assert len(matches)==1
    artifact=matches[0];assert not artifact['expired'] and artifact['workflow_run']['id']==RUN and artifact['workflow_run']['head_sha']==HEAD
    D.mkdir();write(D/'run-api.json',run);write(D/'jobs-api.json',jobs)
    write(D/'artifacts-api.json',artifacts);write(D/'artifact-metadata.json',artifact)
    download=D/'artifact.download.zip'
    with download.open('xb') as f:
        subprocess.run(['gh','api',f'repos/{REPO}/actions/artifacts/{artifact["id"]}/zip'],stdout=f,check=True)
    assert sha(download)==artifact['digest'].removeprefix('sha256:') and download.stat().st_size==artifact['size_in_bytes']
    os.rename(download,D/'artifact.zip');raw=D/'raw';raw.mkdir();records=[]
    with zipfile.ZipFile(D/'artifact.zip') as z:
        assert z.testzip() is None
        names=[i.filename for i in z.infolist() if not i.is_dir()]
        assert len(names)==len(set(names))
        for name in names:
            relative=PurePosixPath(name)
            assert not relative.is_absolute() and '..' not in relative.parts and str(relative)==name
            info=z.getinfo(name);assert ((info.external_attr>>16)&0o170000)!=0o120000
            data=z.read(name);target=raw/name;target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as f:f.write(data)
            records.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    binding=json.loads((raw/'source-binding.json').read_text())
    assert binding['source_commit']==candidate.SOURCE_COMMIT and binding['rust_tree']==candidate.RUST_TREE
    assert binding['source_repository']=='zvec-ai/zvec-grep' and binding['build_repository']==REPO
    assert binding['build_head_sha']==HEAD and binding['run_id']==RUN and binding['run_attempt']==1
    assert binding['target']=='linux-x64-gnu' and binding['providers_called'] is False and binding['published'] is False
    verification=json.loads((raw/'package-verification.json').read_text())
    assert verification['upstream_build_and_packaged_smoke_passed'] and verification['providers_called'] is False and verification['published'] is False
    assert len(verification['packages'])==1;package_record=verification['packages'][0]
    source=raw/package_record['file'];assert sha(source)==package_record['sha256'] and source.stat().st_size==package_record['bytes']
    members={};original=candidate.original('package')
    with tarfile.open(source,'r:gz') as archive:
        assert not archive.pax_headers
        infos=archive.getmembers();assert len(infos)==7 and {i.name for i in infos}==set(original.EXPECTED_MEMBERS)
        for info in infos:
            assert info.type==tarfile.REGTYPE and not info.linkname and not info.pax_headers and not info.issparse()
            data=archive.extractfile(info).read();assert len(data)==info.size
            members[info.name]=[len(data),hashlib.sha256(data).hexdigest(),info.mode]
    pins={'schema_version':1,'source':{'repository':'zvec-ai/zvec-grep','commit':candidate.SOURCE_COMMIT,'rust_tree':candidate.RUST_TREE,'target':'linux-x64-gnu'},
        'build':{'repository':REPO,'run_id':RUN,'run_attempt':1,'head_sha':HEAD,'job_id':jobs['jobs'][0]['id'],'runner_id':jobs['jobs'][0]['runner_id'],'artifact_id':artifact['id'],'artifact_sha256':sha(D/'artifact.zip')},
        'upstream_archive':{'file':package_record['file'],'bytes':source.stat().st_size,'sha256':sha(source)},'members':members}
    candidate.validate_source_pins(pins);write(D/'source-pins.json',pins)
    write(D/'artifact-byte-verification.json',{'schema_version':1,'complete':True,'run_id':RUN,'head_sha':HEAD,'run_attempt':1,'artifact_id':artifact['id'],'artifact_sha256':sha(D/'artifact.zip'),'matches_api_digest':True,'exact_extracted_inventory':True,'files':records,'method_sha256':sha(Path(__file__))})
    output=D/'candidate';output.mkdir()
    manifest=candidate.derive(source,D/'source-pins.json',sha(D/'source-pins.json'),output/'zg-install.tgz',output/'package-derivation.json',output/'candidate-manifest.json')
    print(json.dumps({'ready':True,'source_commit':candidate.SOURCE_COMMIT,'candidate_sha256':manifest['install_archive']['sha256'],'manifest_sha256':sha(output/'candidate-manifest.json'),'binary_sha256':members['package/bin/zg'][1]}))

if __name__=='__main__':main()
