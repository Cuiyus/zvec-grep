"""Opt-in, separately frozen shared answer-verification development pair."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import architecture_phase
import remote_embedding

PLAN_NAME = 'experiment-answer-verification-v1.json'
PHASE = 'answer-verification-development'
PROTOCOL = 'answer-verification-v1'
BASE_SHA256 = '992ee9b4a04994d56d6c3ef73638cd02d4df37bcf09ea6d0c90873605be4940d'
SUFFIX_NAME = 'answer-verification-instructions.txt'
SUFFIX_SHA256 = '9f2912e8bce359d51e0545819d043d711e31ae91274c18d2ebbb20b808bb2e35'
INPUTS = dict(architecture_phase.INPUTS, architecture_pilot=False, answer_verification_pilot=True)
SPEC = {'plan_name': PLAN_NAME, 'phase': PHASE, 'protocol': PROTOCOL,
        'metadata_key': 'answer_verification_plan', 'runner_args': ['--answer-verification-pilot']}


def select_phase(*, enabled, task, budget, root_guidance, confirmation, expansion, remote, architecture):
    """Default-off selection gate, shared by the pre-provider workflow and CI wrapper."""
    if not enabled:
        return None
    if (task != 'zvec-q004' or not budget or root_guidance or confirmation
            or expansion or remote or architecture):
        raise ValueError('answer verification requires one q004 completion-budget pair with no other phase')
    return SPEC


def compose_instructions(base: str, suffix: bytes) -> str:
    if hashlib.sha256(suffix).hexdigest() != SUFFIX_SHA256:
        raise ValueError('answer verification instruction asset differs from the fixed policy')
    return base + ' ' + suffix.decode('utf-8').rstrip('\n')


def validate_plan(root: Path, here: Path) -> dict:
    """Offline gate: inherit the exact old experiment and change only the shared prompt."""
    root, here = root.resolve(), here.resolve()
    path = here / PLAN_NAME
    plan = json.loads(path.read_text())
    if plan.get('frozen') is not True:
        raise ValueError('answer verification plan has not been frozen for execution')
    if (plan.get('experiment_id') != 'claude-qwen-rust-zvec-answer-verification-v1'
            or plan.get('phase') != PHASE or plan.get('protocol_version') != PROTOCOL):
        raise ValueError('answer verification plan identity differs from the declared phase')
    base_path = architecture_phase.checked_file(root, plan['base_experiment'])
    if (base_path != (here / architecture_phase.PLAN_NAME).resolve()
            or plan['base_experiment']['sha256'] != BASE_SHA256):
        raise ValueError('answer verification must inherit the exact frozen architecture experiment')
    base_metadata = architecture_phase.validate_plan(root, here)
    base = json.loads(base_path.read_text())
    for group in ('agent', 'completion_control', 'completion_hook', 'retrieval', 'preflight',
                  'accounting', 'task', 'dataset', 'evaluation'):
        if plan.get(group) != base[group]:
            raise ValueError('answer verification changed its inherited ' + group)
    expected_run = dict(base['run_plan'], inputs=INPUTS)
    if plan['run_plan'] != expected_run:
        raise ValueError('answer verification requires exactly its single frozen pair')
    policy = plan['shared_answer_verification']
    suffix_path = architecture_phase.checked_file(root, policy)
    if (suffix_path != (here / SUFFIX_NAME).resolve() or policy['sha256'] != SUFFIX_SHA256
            or policy.get('applies_identically_to') != ['baseline', 'zg']):
        raise ValueError('answer verification must use the same fixed instruction asset for both arms')
    from run import common_instructions
    original = common_instructions(False, 24, 600)
    original_sha = hashlib.sha256(original.encode()).hexdigest()
    effective = compose_instructions(original, suffix_path.read_bytes())
    effective_sha = hashlib.sha256(effective.encode()).hexdigest()
    if (original_sha != base['common_instructions_sha256']
            or original_sha != plan['base_common_instructions_sha256']
            or effective_sha != plan['common_instructions_sha256']):
        raise ValueError('answer verification common instruction hash mismatch')
    return {'path': str(path.relative_to(root)), 'sha256': architecture_phase.digest(path),
            'dataset_path': base_metadata['dataset_path'],
            'dataset_sha256': base_metadata['dataset_sha256'],
            'base_experiment': plan['base_experiment'],
            'shared_answer_verification': policy,
            'base_common_instructions_sha256': original_sha,
            'common_instructions_sha256': effective_sha}


def prepare_runner(args, base: str, root: Path, here: Path) -> tuple[str, dict]:
    """Validate the explicit low-level runner opt-in before Docker or model calls."""
    metadata = validate_plan(root, here)
    if (args.workspace_root_guidance or args.tool_budget != 24 or args.research_seconds != 600
            or args.repeats != 1 or args.start_repeat != 1 or args.max_turns != 30 or args.timeout != 900
            or args.model != 'qwen3.8-max' or args.max_output_tokens != 8192 or args.effort != 'high'
            or args.embedding_model != remote_embedding.MODEL or args.claude != 'claude' or args.zg != 'zg'
            or args.mcp_config is not None
            or args.mcp_args != ['--server', '--stdio', '--mcp-toolset', 'agent']
            or args.zg_tool != 'mcp__zvec_grep__zvec_grep_search'):
        raise ValueError('answer verification runner settings differ from the frozen pair')
    plan = json.loads((here / PLAN_NAME).read_text())
    if architecture_phase.digest(args.prompt) != plan['task']['question']['sha256']:
        raise ValueError('answer verification runner question differs from the frozen task')
    effective = compose_instructions(base, (here / SUFFIX_NAME).read_bytes())
    if hashlib.sha256(effective.encode()).hexdigest() != metadata['common_instructions_sha256']:
        raise ValueError('answer verification effective runner prompt hash mismatch')
    return effective, metadata
