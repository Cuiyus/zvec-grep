"""Frozen Qwen harness with an explicitly fingerprinted description-only Rust package.

Formal inference, corpus, prompts, permissions, scheduling and scoring are inherited
unchanged. Smoke covers three original questions; controlled probes are separate.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
BASE_RUST = '732a44014654c7acf08553716af67865bcfb8430'
DESCRIPTION_SOURCE = '1bf6c0073d297d6f78fae133214568d09fe99941'
EXPECTED_TARBALL_SHA = None
EXPECTED_CLI_SHA = None
SMOKE_TASKS = ['conan-19', 'django-32', 'xarray-32']


def prepare(packages, output):
    assert EXPECTED_TARBALL_SHA and EXPECTED_CLI_SHA, 'Freeze package fingerprints before model calls'
    package = packages / 'rust' / 'candidate.tgz'
    manifest = json.loads((package.parent / 'manifest.json').read_text())
    assert manifest['candidate_commit'] == DESCRIPTION_SOURCE
    digest = hashlib.file_digest(package.open('rb'), 'sha256').hexdigest()
    assert digest == manifest['tarball_sha256'] == EXPECTED_TARBALL_SHA
    with tarfile.open(package) as archive:
        cli = hashlib.file_digest(archive.extractfile('package/bin/zg'), 'sha256').hexdigest()
    assert cli == EXPECTED_CLI_SHA
    config_path = ROOT.parent / 'version-comparison/experiment.json'
    old = json.loads(config_path.read_text())
    assert old['rust']['source_commit'] in (BASE_RUST, DESCRIPTION_SOURCE)
    identity = dict(source_commit=DESCRIPTION_SOURCE, source_repository='Cuiyus/zvec-grep',
                    source_base=BASE_RUST, tarball_sha256=digest, cli_sha256=cli,
                    artifact_name='three-arm-package-rust')
    new = {**old, 'rust': identity}
    assert {k:v for k,v in old.items() if k != 'rust'} == {k:v for k,v in new.items() if k != 'rust'}
    config_path.write_text(json.dumps(new, indent=2) + '\n')
    output.mkdir(parents=True, exist_ok=True)
    (output/'package-override.json').write_text(json.dumps(dict(
        excluded_from_answer_evaluation=False, original_rust=old['rust'], candidate_rust=identity,
        configuration_delta='Only Rust package source and binary fingerprints; inference controls unchanged',
        original_experiment=old, runtime_experiment=new), indent=2)+'\n')
    print(json.dumps(identity))


def entry():
    from zg_bench import qwen_subagent_e2e as qwen
    if getattr(qwen, '_description_wrapper', False):
        return qwen
    original_configure = qwen.configure
    original_tasks = qwen.selected_tasks
    def tasks(phase):
        return list(SMOKE_TASKS) if phase == 'smoke' else original_tasks(phase)
    def configure(phase):
        experiment = original_configure(phase)
        assert experiment['rust']['source_commit'] == DESCRIPTION_SOURCE
        experiment['package_source'] = dict(repository='Cuiyus/zvec-grep',
            run_id=int(os.environ['MCP_DESCRIPTION_PACKAGE_RUN_ID']))
        experiment['description_intervention'] = dict(source_base=BASE_RUST,
            source_commit=DESCRIPTION_SOURCE, original_run=40441908468,
            production_delta='MCP search tool and queries/fts/vector descriptions only',
            package_reused_from=os.environ.get('MCP_VERIFIED_PACKAGE_RUN') or None)
        return experiment
    qwen.selected_tasks = tasks
    qwen.configure = configure
    qwen._description_wrapper = True
    return qwen


def selected_tasks(phase):
    if phase == 'smoke':
        return list(SMOKE_TASKS)
    from zg_bench.qwen_subagent_e2e import selected_tasks as original
    return original(phase)


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'prepare':
        parser = argparse.ArgumentParser()
        parser.add_argument('action')
        parser.add_argument('--packages', type=Path, required=True)
        parser.add_argument('--output', type=Path, required=True)
        args = parser.parse_args()
        prepare(args.packages.resolve(), args.output.resolve())
    else:
        entry().main()

if __name__ == '__main__':
    main()
