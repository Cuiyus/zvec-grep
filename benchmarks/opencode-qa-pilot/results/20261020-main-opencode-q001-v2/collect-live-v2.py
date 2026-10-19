#!/usr/bin/env python3
"""Retrieve the one scheduled v2 run without redispatch or artifact mutation."""
from pathlib import Path, PurePosixPath
import hashlib
import io
import json
import subprocess
import zipfile

REPO = 'Cuiyus/zvec-grep'
RUN = 39315776778
HEAD = 'cbf62676ff7a3cc4f815deaedf275f167107d061'
ROOT = Path(__file__).resolve().parent / 'live-v2-ci'

def api(path):
    return subprocess.check_output(['gh', 'api', 'repos/' + REPO + '/' + path])

def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')

def sha(data):
    return hashlib.sha256(data).hexdigest()

def main():
    metadata = json.loads(api('actions/runs/' + str(RUN)))
    assert metadata['status'] == 'completed', 'wait for completion; do not redispatch'
    assert metadata['head_sha'] == HEAD and metadata['run_attempt'] == 1
    assert metadata['event'] == 'workflow_dispatch'
    assert metadata['repository']['full_name'] == REPO
    assert not ROOT.exists(), 'existing evidence must not be overwritten'
    ROOT.mkdir()
    write(ROOT / 'run-metadata.json', metadata)
    write(ROOT / 'job-metadata.json', json.loads(api('actions/runs/' + str(RUN) + '/jobs')))
    logs = subprocess.check_output(['gh', 'run', 'view', str(RUN), '--repo', REPO, '--log'])
    (ROOT / 'job-log.txt').write_bytes(logs)
    artifacts = json.loads(api('actions/runs/' + str(RUN) + '/artifacts'))
    write(ROOT / 'artifacts-metadata.json', artifacts)
    matches = [a for a in artifacts['artifacts'] if a['name'] == 'main-opencode-q001-v2-' + str(RUN)]
    assert len(matches) == 1 and not matches[0]['expired'], 'required retained artifact unavailable'
    artifact = matches[0]
    assert artifact['workflow_run']['id'] == RUN and artifact['workflow_run']['head_sha'] == HEAD
    data = api('actions/artifacts/' + str(artifact['id']) + '/zip')
    zip_sha = sha(data)
    assert artifact['digest'] == 'sha256:' + zip_sha
    (ROOT / 'artifact.zip').write_bytes(data)
    raw = ROOT / 'raw'; raw.mkdir()
    rows = []
    seen = set()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            rel = PurePosixPath(info.filename)
            assert not rel.is_absolute() and '..' not in rel.parts and rel.as_posix() not in seen
            assert ((info.external_attr >> 16) & 0o170000) != 0o120000
            seen.add(rel.as_posix())
            if info.is_dir():
                continue
            body = archive.read(info)
            destination = raw.joinpath(*rel.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(body)
            assert destination.read_bytes() == body
            rows.append({'path': rel.as_posix(), 'bytes': len(body), 'sha256': sha(body)})
    assert len(rows) == sum(p.is_file() for p in raw.rglob('*'))
    report = {'run_id': RUN, 'head': HEAD, 'conclusion': metadata['conclusion'],
              'artifact_id': artifact['id'], 'artifact_sha256': zip_sha,
              'zip_bytes': len(data), 'files': sorted(rows, key=lambda r: r['path'])}
    write(ROOT / 'artifact-byte-verification.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'files'} | {'file_count': len(rows), 'root': str(ROOT)}))

if __name__ == '__main__':
    main()
