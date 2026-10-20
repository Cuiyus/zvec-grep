"""Retain and reverify the two original q005 worker ZIPs; never invoke a solver."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import zipfile

import profile_assembly_v1 as assembly

REPOSITORY = 'Cuiyus/zvec-grep'
WORKFLOW = '.github/workflows/native-claude-q005-v1.yml'


def api(endpoint):
    return json.loads(subprocess.check_output(['gh', 'api', endpoint], text=True))


def retain_json(path, value):
    if path.exists():
        if assembly.load(path) != value:
            raise ValueError('retained_metadata_changed')
    else:
        assembly.write(path, value)


def zip_members(archive):
    infos = archive.infolist()
    names = [i.filename for i in infos if not i.is_dir()]
    if len(set(names)) != len(names) or archive.testzip() is not None:
        raise ValueError('worker_zip_duplicate_or_crc_failure')
    for item in infos:
        path = PurePosixPath(item.filename)
        if (not item.filename or path.is_absolute() or '..' in path.parts
                or str(path) != item.filename.rstrip('/')
                or stat.S_ISLNK(item.external_attr >> 16)):
            raise ValueError('worker_zip_unsafe_path')
    return infos, names


def verify_retained(root, *, run_id, head_sha, profile):
    if profile not in assembly.PROFILES:
        raise ValueError('undeclared_worker_profile')
    receipt = assembly.load(root / 'artifact-byte-verification.json')
    metadata = assembly.load(root / 'artifact-metadata.json')
    run = assembly.load(root / 'run-metadata.json')
    jobs = assembly.load(root / 'job-metadata.json')['jobs']
    matches = [j for j in jobs if j['name'] == 'worker (' + profile + ')']
    if len(matches) != 1:
        raise ValueError('worker_job_identity_missing')
    job = matches[0]
    if not (run['id'] == run_id and run['head_sha'] == head_sha and run['run_attempt'] == 1
            and run['event'] == 'workflow_dispatch' and run['path'] == WORKFLOW
            and run['repository']['full_name'] == REPOSITORY
            and job['run_id'] == run_id and job['head_sha'] == head_sha and job['run_attempt'] == 1
            and job['status'] == 'completed' and job['labels'] == ['ubuntu-24.04']
            and str(job['runner_name']).startswith('GitHub Actions ') and job['runner_id']
            and receipt['repository'] == REPOSITORY and receipt['run_id'] == run_id
            and receipt['head_sha'] == head_sha and receipt['run_attempt'] == 1
            and receipt['profile'] == profile and receipt['task'] == 'q005'
            and receipt['job_id'] == job['id'] and receipt['runner_id'] == job['runner_id']
            and receipt['job_conclusion'] == job['conclusion']
            and receipt['artifact_id'] == metadata['id'] and receipt['complete'] is True
            and receipt['exact_extracted_inventory'] is True
            and receipt['matches_api_digest'] is True
            and metadata['name'] == f'native-q005-v1-{run_id}-{profile}'
            and metadata['workflow_run']['id'] == run_id
            and metadata['workflow_run']['head_sha'] == head_sha):
        raise ValueError('worker_run_job_artifact_origin_mismatch')
    zp, raw = root / 'artifact.zip', root / 'raw'
    digest = assembly.digest(zp)
    if (metadata['digest'] != 'sha256:' + digest or receipt['artifact_sha256'] != digest
            or metadata['size_in_bytes'] != zp.stat().st_size
            or receipt['artifact_bytes'] != zp.stat().st_size
            or receipt['artifact_metadata_sha256'] != assembly.digest(root / 'artifact-metadata.json')):
        raise ValueError('worker_zip_api_digest_mismatch')
    files = assembly.inventory(raw)
    expected = {r['path']: {'bytes': r['bytes'], 'sha256': r['sha256']} for r in receipt['files']}
    if (len(expected) != len(receipt['files']) or files != expected
            or not all(r['matches_zip'] is True for r in receipt['files'])):
        raise ValueError('worker_raw_inventory_mismatch')
    with zipfile.ZipFile(zp) as archive:
        _, names = zip_members(archive)
        if sorted(names) != sorted(files):
            raise ValueError('worker_raw_zip_inventory_mismatch')
        for name in names:
            if archive.read(name) != (raw / name).read_bytes():
                raise ValueError('worker_raw_bytes_changed')
    return receipt


def collect(run_id, head_sha, root):
    root.mkdir(parents=True, exist_ok=True)
    run = api(f'repos/{REPOSITORY}/actions/runs/{run_id}')
    if (run['head_sha'] != head_sha or run['run_attempt'] != 1
            or run['event'] != 'workflow_dispatch' or run['path'] != WORKFLOW
            or run['repository']['full_name'] != REPOSITORY):
        raise ValueError('q005_worker_collection_wrong_origin')
    jobs = api(f'repos/{REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100')
    artifacts = api(f'repos/{REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100')
    if jobs['total_count'] > 100 or artifacts['total_count'] > 100:
        raise ValueError('q005_unexpected_artifact_or_job_count')
    available = []
    for profile in assembly.PROFILES:
        out = root / profile
        if (out / 'artifact-byte-verification.json').is_file():
            verify_retained(out, run_id=run_id, head_sha=head_sha, profile=profile)
            available.append(profile)
            continue
        matches = [a for a in artifacts['artifacts']
                   if a['name'] == f'native-q005-v1-{run_id}-{profile}']
        if not matches:
            continue
        selected_jobs = [j for j in jobs['jobs'] if j['name'] == 'worker (' + profile + ')']
        if len(matches) != 1 or len(selected_jobs) != 1 or selected_jobs[0]['status'] != 'completed':
            raise ValueError('q005_worker_artifact_not_unique_or_job_not_terminal')
        metadata, job = matches[0], selected_jobs[0]
        out.mkdir(exist_ok=True)
        zp = out / 'artifact.zip'
        if not zp.exists():
            temporary = out / 'artifact.zip.partial'
            with temporary.open('wb') as stream:
                subprocess.run(['gh', 'api', f'repos/{REPOSITORY}/actions/artifacts/{metadata["id"]}/zip'],
                               stdout=stream, stderr=subprocess.PIPE, check=True)
            os.replace(temporary, zp)
        if (metadata['digest'] != 'sha256:' + assembly.digest(zp)
                or metadata['size_in_bytes'] != zp.stat().st_size):
            raise ValueError('q005_downloaded_zip_not_original')
        raw = out / 'raw'
        raw.mkdir(exist_ok=True)
        assembly.inventory(raw)
        with zipfile.ZipFile(zp) as archive:
            infos, _ = zip_members(archive)
            for info in infos:
                if info.is_dir():
                    continue
                target = raw / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                content = archive.read(info)
                if target.exists():
                    if target.is_symlink() or target.read_bytes() != content:
                        raise ValueError('q005_partial_extraction_changed')
                else:
                    with target.open('xb') as stream:
                        stream.write(content)
        for name, value in [('run-metadata.json', run), ('job-metadata.json', jobs),
                            ('artifact-metadata.json', metadata)]:
            retain_json(out / name, value)
        files = assembly.inventory(raw)
        receipt = {'schema_version': 1, 'complete': True, 'exact_extracted_inventory': True,
            'repository': REPOSITORY, 'event': run['event'], 'run_id': run_id,
            'head_sha': head_sha, 'run_attempt': 1, 'task': 'q005', 'profile': profile,
            'job_id': job['id'], 'runner_id': job['runner_id'], 'job_conclusion': job['conclusion'],
            'artifact_id': metadata['id'], 'artifact_sha256': assembly.digest(zp),
            'artifact_bytes': zp.stat().st_size, 'matches_api_digest': True,
            'artifact_metadata_sha256': assembly.digest(out / 'artifact-metadata.json'),
            'files': [{'path': p, **v, 'matches_zip': True} for p, v in files.items()]}
        assembly.write(out / 'artifact-byte-verification.json', receipt)
        verify_retained(out, run_id=run_id, head_sha=head_sha, profile=profile)
        available.append(profile)
    report = {'schema_version': 1, 'run_id': run_id, 'head_sha': head_sha,
              'planned_profiles': list(assembly.PROFILES), 'available_profiles': available,
              'missing_profiles': [p for p in assembly.PROFILES if p not in available],
              'both_original_workers_available': len(available) == 2, 'solver_retries': 0}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    assembly.write(root / ('availability-' + stamp + '.json'), report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--head-sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = collect(args.run_id, args.head_sha, args.output.resolve())
    print(json.dumps(result))
    raise SystemExit(0 if result['both_original_workers_available'] else 1)
