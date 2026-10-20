"""Prepare reviewed q004 with the unchanged native task templates; no paid calls.

The private adapter reuses the validated reserve preparation format. It changes
only the declared dataset/task references; q001-q003 preparations stay frozen.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TASK_IDS = ('q004',)
DATASET = 'benchmarks/zvec-issue-qa/dataset-architecture-v1.json'
BASE_PREPARER = 'benchmarks/native-claude-qa-pilot/expansion_v1.py'
PINS = {
    BASE_PREPARER: '5e2738ae616b62730c1ac25c6bfd168172da29eaf34c1a3a3257994a24a24f6a',
    DATASET: '71a94c3947f821642abc0fdf93a87cb076567cdb01e9433be7cb1d7bf3af5003',
    'benchmarks/zvec-issue-qa/tasks/q004/instruction.md': '47d9ab3110be6e0f4fa6c91efdbefcfb19dc50e0b07374d2d8c0adcfe35d26f3',
    'benchmarks/zvec-issue-qa/evaluator/q004.json': '2040e092818867e68aa63f953ecc6642ee65a3b3b73b715a3f5b4d611f286a07',
    'benchmarks/zvec-issue-qa/evaluator/provenance/q004/independent-source-review.json': '6524c64d2569ad2fb4b5f04ea49542d81489b7abb765541f988d59c720b63b73',
    'benchmarks/zvec-issue-qa/evaluator/provenance/q004/source-review-clarifications.json': 'ea3bedbd9908ad5ac650f1a432825ae990a34732dc5b8c7c53ae9aea8657501c',
}


def validate_assets(repo_root=REPO):
    for relative, expected in PINS.items():
        path = repo_root / relative
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('architecture_asset_changed:' + relative)
    dataset = json.loads((repo_root / DATASET).read_text())
    review = json.loads((repo_root / 'benchmarks/zvec-issue-qa/evaluator/provenance/q004/independent-source-review.json').read_text())
    if [task['id'] for task in dataset['tasks']] != list(TASK_IDS):
        raise ValueError('architecture_requires_q004_only')
    if review['asset_review']['remaining_blocking_findings']:
        raise ValueError('architecture_asset_review_incomplete')
    for reviewed in review['asset_review']['reviewed_assets']:
        relative = next((key for key in PINS if str(reviewed['path']).endswith('/' + key)), None)
        if relative is None or PINS[relative] != reviewed['sha256']:
            raise ValueError('architecture_reviewed_identity_mismatch')
    return dataset


def implementation(repo_root=REPO):
    validate_assets(repo_root)
    spec = importlib.util.spec_from_file_location('_native_q004_preparation', repo_root / BASE_PREPARER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TASK_IDS = TASK_IDS
    module.PINS = {key: value for key, value in module.PINS.items() if key != module.DATASET}
    module.DATASET = DATASET
    module.PINS[DATASET] = PINS[DATASET]
    return module


def prepare(reference_prepared, zvec_repo, output, repo_root=REPO):
    return implementation(repo_root).prepare(reference_prepared, zvec_repo, output, repo_root)


def validate(prepared_group, reference_prepared, repo_root=REPO):
    return implementation(repo_root).validate(prepared_group, reference_prepared, repo_root)


def task_hashes(task_dir):
    return implementation().task_hashes(task_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'validate'])
    parser.add_argument('--reference-prepared', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--zvec-repo', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        if args.zvec_repo is None:
            parser.error('--zvec-repo is required to prepare')
        result = prepare(args.reference_prepared, args.zvec_repo, args.output)
    else:
        result = validate(args.output, args.reference_prepared)
    print(json.dumps({'task_ids': result['task_ids'],
                      'prepared_metadata_sha256': result['tasks'],
                      'solver_executed': False, 'paid_execution_authorized': False}))


if __name__ == '__main__':
    main()
