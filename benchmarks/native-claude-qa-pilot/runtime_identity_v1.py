"""Read-only q005 task binding using the unchanged native identity verifier."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import runtime_preparation_v1 as preparation

HERE = Path(__file__).resolve().parent
BASE = HERE / 'expansion_identity_v1.py'
BASE_SHA256 = '2cb52900f624f21c0f183b3789b4343b32ea34f217f22c8c628b0f40acbc6cc7'
PROFILES = ('baseline', 'zvec-grep')


def implementation():
    if hashlib.sha256(BASE.read_bytes()).hexdigest() != BASE_SHA256:
        raise ValueError('native_identity_verifier_changed')
    spec = importlib.util.spec_from_file_location('_native_q005_identity', BASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.preparation = preparation
    return module


def verify(prepared_group: Path, task_id: str, output: Path, *, reference_prepared: Path,
           recorded_prepared_root: str, repo_root: Path = preparation.REPO):
    if task_id != 'q005':
        raise ValueError('runtime_identity_requires_q005')
    return implementation().verify(prepared_group, task_id, output,
        reference_prepared=reference_prepared, recorded_prepared_root=recorded_prepared_root,
        expected_profiles=PROFILES, repo_root=repo_root)
