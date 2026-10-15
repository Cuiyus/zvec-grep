"""Verify the exact packages reused from the first three-arm benchmark round."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import tarfile


def verify(directory: Path, variant: str, experiment: dict) -> dict:
    config = experiment[variant]
    if {path.name for path in directory.iterdir()} != {'candidate.tgz', 'manifest.json'}:
        raise ValueError('Package directory must contain only candidate.tgz and manifest.json')
    package = directory / 'candidate.tgz'
    manifest = json.loads((directory / 'manifest.json').read_text())
    if (manifest.get('schema_version') != 1
            or manifest.get('candidate_commit') != config['source_commit']
            or manifest.get('tarball') != 'candidate.tgz'):
        raise ValueError('Package manifest does not match the frozen source identity')
    body = package.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if digest != config['tarball_sha256'] or digest != manifest.get('tarball_sha256'):
        raise ValueError('Package SHA256 differs from the first benchmark round')
    if variant == 'node':
        integrity = 'sha512-' + base64.b64encode(hashlib.sha512(body).digest()).decode()
        if integrity != config['registry_integrity']:
            raise ValueError('Published Node registry integrity mismatch')
    with tarfile.open(package) as archive:
        metadata = json.load(archive.extractfile('package/package.json'))
        entrypoint = metadata['bin']['zg'] if isinstance(metadata['bin'], dict) else metadata['bin']
        expected_entrypoint = 'dist/cli/index.js' if variant == 'node' else 'bin/zg'
        if metadata.get('name') != '@zvec/zvec-grep' or entrypoint != expected_entrypoint:
            raise ValueError('Unexpected zg package name or CLI entrypoint')
        if variant == 'node' and metadata.get('version') != '0.2.2':
            raise ValueError('Unexpected Node release')
        member = archive.getmember('package/' + entrypoint)
        if not member.isfile():
            raise ValueError('CLI entrypoint must be a regular file')
        cli_sha = hashlib.file_digest(archive.extractfile(member), 'sha256').hexdigest()
    if cli_sha != config['cli_sha256']:
        raise ValueError('CLI SHA256 differs from the first benchmark round')
    return {
        'source_commit': config['source_commit'],
        'package_sha256': digest,
        'cli_sha256': cli_sha,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--variant', required=True, choices=('node', 'rust'))
    parser.add_argument('--experiment', type=Path, default=Path(__file__).with_name('experiment.json'))
    args = parser.parse_args()
    result = verify(args.directory, args.variant, json.loads(args.experiment.read_text()))
    print(json.dumps(result, sort_keys=True))
