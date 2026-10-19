"""Freeze and validate the inputs of the provider-offline Docker proof."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pilot

RELATIVE = Path('benchmarks/opencode-qa-pilot/repair_v2')
# Keep the original phase identity explicit; do not infer it from mutable input.
V1_PLAN_SHA256 = 'ce13c13785117c49c515a79cfbba13ad70eaf7113dbcb3d6e91b29ec3a8dcc43'
ORIGINAL_SHA256 = '470c2a11b8b19a9e6458c977918260ee6f535595488ed8b44233db5aa032ad4d'
BINARY_SHA256 = '645ef8b62d62ff604c06f3c8aa34790c3c28bc65c946714ecdbf7175131b61dd'


def input_files(repo: Path) -> dict[str, str]:
    repair = repo / RELATIVE
    paths = list(repair.glob('*.py')) + [repair / 'decision.json',
        repo / '.github/workflows/opencode-qa-repair-v2.yml']
    return {p.relative_to(repo).as_posix(): pilot.digest(p) for p in sorted(paths)}


def validate(repo: Path, main_root: Path) -> dict:
    record = pilot.load(repo / RELATIVE / 'inputs.json')
    if (record.get('schema_version') != 1
            or record.get('proof_scope') != 'provider-offline-infrastructure-only'
            or record.get('upstream_main') != pilot.MAIN
            or record.get('v1_plan_sha256') != V1_PLAN_SHA256
            or record.get('original_package_sha256') != ORIGINAL_SHA256
            or record.get('rust_binary_sha256') != BINARY_SHA256):
        raise ValueError('offline proof identity mismatch')
    derived = record.get('install_package_sha256', '')
    if len(derived) != 64 or any(c not in '0123456789abcdef' for c in derived):
        raise ValueError('derived archive identity is not frozen')
    if record.get('files') != input_files(repo):
        raise ValueError('offline proof input files changed; freeze a new revision')
    old_plan = repo / 'benchmarks/opencode-qa-pilot/experiment.json'
    if pilot.digest(old_plan) != V1_PLAN_SHA256:
        raise ValueError('closed v1 plan changed')
    pilot.validate_plan(old_plan, repo, main_root)
    # The native package cache helper is outside the benchmark Python tree.
    exported = pilot.load(repo / 'benchmarks/opencode-qa-pilot/provenance/upstream-export.json')
    export_root = main_root.parent.parent
    for name, sha in exported['files'].items():
        if pilot.digest(pilot.safe_file(export_root, name)) != sha:
            raise ValueError('pinned upstream export changed: ' + name)
    decision = pilot.load(repo / RELATIVE / 'decision.json')
    basis = decision['basis']
    if pilot.digest(repo / basis['design']) != basis['design_sha256']:
        raise ValueError('repair design changed')
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('freeze', 'validate'))
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--main-root', type=Path, required=True)
    parser.add_argument('--revision', type=int)
    parser.add_argument('--install-package-sha256')
    args = parser.parse_args(argv)
    repo, main_root = args.repo_root.resolve(), args.main_root.resolve()
    target = repo / RELATIVE / 'inputs.json'
    if args.command == 'freeze':
        if not args.revision or args.revision < 1 or not args.install_package_sha256:
            parser.error('freeze requires a positive revision and install package SHA256')
        if target.exists():
            previous = pilot.load(target)
            if args.revision <= previous['revision']:
                raise ValueError('new input revision must increase')
            retained = target.with_name('inputs-revision-%d.json' % previous['revision'])
            if retained.exists():
                raise ValueError('prior revision retention already exists')
            retained.write_bytes(target.read_bytes())
        pilot.write(target, {
            'schema_version': 1, 'revision': args.revision,
            'proof_scope': 'provider-offline-infrastructure-only',
            'upstream_main': pilot.MAIN, 'v1_plan_sha256': V1_PLAN_SHA256,
            'original_package_sha256': ORIGINAL_SHA256,
            'install_package_sha256': args.install_package_sha256,
            'rust_binary_sha256': BINARY_SHA256,
            'files': input_files(repo),
        })
    record = validate(repo, main_root)
    print(json.dumps({'valid': True, 'revision': record['revision'],
        'inputs_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
