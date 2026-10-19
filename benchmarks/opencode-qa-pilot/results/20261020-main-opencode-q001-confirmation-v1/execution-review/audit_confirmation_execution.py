#!/usr/bin/env python3
"""Audit both saved confirmation repeats: execution/provenance only.

Do not run until the parent has confirmed both repeat artifacts are retained.
No answer grading, tool audit, usage arithmetic, or historical metric reuse.

No agent, Docker, provider, CI or network action is performed. Only --report is
written. Local git show is used solely to read the actual execution commit.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime
import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath
import subprocess
import zipfile

BASE = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
REPO = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
HEAD = '10348d1c2c863fc498f71e1dd76f04835aa92153'
RUN_ID = 39321551973
PROTOCOL = 'main-opencode-q001-confirmation-v1'
REPEATS = ('c01', 'c02')
PARENT_PLAN_SHA = 'dd48a9bcb813ca2ef679971419c7f02726e008791350202ed1c53c7728ddff60'
PLAN_SHA = 'c88bdf837cd420ae0837c64ed66402b0100b02467426600fad3126d951832050'
INSTALL_SHA = 'fbe4bd201c8a0520f2373f1039e1d0805a38781c6e2f3ce11b83310d23dc6522'
OPENCODE_SHA = '6ce6570e7db9a40e7bd3304ebdfff607920bde8cafd2eb5587bd7a26f89ba0b5'
CHECKS = []
CHECK_PREFIX = ''


def recompute_task_hashes(task_dir):
    """Independently implement the two native hashes for this frozen regular tree.

    Harbor 0.18.0 Packager: flat POSIX-relative lexical file order, append
    relative-path + NUL + SHA256(file bytes) + LF. Default-ignored files and
    unsupported top-level files are explicitly rejected for this frozen task.
    Legacy dirhash 0.5.0: recursive name/data descriptors, properties sorted and
    NUL-separated, entries sorted and double-NUL-separated; ignore empty dirs.
    Root name/absolute location, modes, owner and timestamps enter neither hash.
    """
    import fnmatch
    inventory = {p.relative_to(task_dir).as_posix(): digest(p)
                 for p in task_dir.rglob('*') if p.is_file()}
    assert not any(p.is_symlink() for p in task_dir.rglob('*'))
    assert '.gitignore' not in inventory
    for name in inventory:
        parts = PurePosixPath(name).parts
        assert '__pycache__' not in parts
        assert not any(fnmatch.fnmatch(parts[-1], pattern)
                       for pattern in ('*.pyc', '.DS_Store', '*.swp', '*.swo', '*~'))
        assert name in {'task.toml', 'instruction.md', 'README.md'} or parts[0] in {'environment', 'tests', 'solution', 'steps'}
    package_hash = sha(''.join(name + '\0' + inventory[name] + '\n'
                               for name in sorted(inventory)).encode())
    tree = {}
    for name, value in inventory.items():
        node = tree
        parts = PurePosixPath(name).parts
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def recursive(node):
        entries = []
        for name, value in node.items():
            properties = [('dirhash:' + recursive(value)) if isinstance(value, dict)
                          else ('data:' + value), 'name:' + name]
            entries.append('\0'.join(sorted(properties)))
        return sha('\0\0'.join(sorted(entries)).encode())

    return package_hash, recursive(tree), inventory


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def check(label, passed, detail=None):
    row = {'check': CHECK_PREFIX + label, 'passed': bool(passed)}
    if detail is not None:
        row['detail'] = detail
    CHECKS.append(row)


def read_git(repo, name):
    return subprocess.run(['git', 'show', HEAD + ':' + name], cwd=repo, check=True, capture_output=True).stdout


def duration(record):
    return (datetime.fromisoformat(record['finished_at'].replace('Z', '+00:00'))
            - datetime.fromisoformat(record['started_at'].replace('Z', '+00:00'))).total_seconds()


def options(command, flag):
    return [command[i + 1] for i, x in enumerate(command[:-1]) if x == flag]


def normalized_command(command):
    normalized = list(command)
    normalized[normalized.index('--jobs-dir') + 1] = '<jobs>'
    for i, value in enumerate(normalized):
        if value.startswith('zvec_grep_package='):
            normalized[i] = 'zvec_grep_package=<same-verified-derived-tgz>'
    return normalized


def audit_repeat(args, repeat, metadata, artifact, job):
    check_start = len(CHECKS)
    evidence_root = args.evidence_root / repeat
    zip_sha = artifact['digest'].removeprefix('sha256:')
    check('artifact_digest_valid', re.fullmatch('[0-9a-f]{64}', zip_sha) is not None)
    raw = evidence_root / 'raw'
    run_dir, reports = raw / 'qa-confirmation-run', raw / 'qa-confirmation-reports'
    plan_path = reports / 'confirmation-v1.json'
    plan, execution = load(plan_path), load(run_dir / 'execution.json')
    check('run_identity', metadata['id'] == RUN_ID and metadata['head_sha'] == HEAD
          and metadata['run_attempt'] == 1 and metadata['event'] == 'workflow_dispatch'
          and metadata['status'] == 'completed' and metadata['conclusion'] == 'success'
          and metadata['repository']['full_name'] == 'Cuiyus/zvec-grep')
    check('artifact_identity', isinstance(artifact['id'], int) and artifact['name'] == f'main-opencode-q001-confirmation-v1-{RUN_ID}-{repeat}'
          and artifact['digest'] == 'sha256:' + zip_sha
          and artifact['workflow_run']['id'] == metadata['id'] and artifact['workflow_run']['head_sha'] == HEAD)
    check('artifact_zip_digest', digest(evidence_root / 'artifact.zip') == zip_sha)
    saved_artifact = load(evidence_root / 'artifact-metadata.json')
    check('per_repeat_and_global_artifact_metadata_agree',
          all(saved_artifact[k] == artifact[k] for k in ('id', 'name', 'digest', 'workflow_run')))
    zip_failures = []
    with zipfile.ZipFile(evidence_root / 'artifact.zip') as archive:
        names = [n for n in archive.namelist() if not n.endswith('/')]
        safe = (len(names) == len(set(names)) and len(names) > 0
                and all(not PurePosixPath(n).is_absolute() and '..' not in PurePosixPath(n).parts for n in names)
                and all(((m.external_attr >> 16) & 0o170000) != 0o120000 for m in archive.infolist())
                and not any(p.is_symlink() for p in raw.rglob('*')))
        check('exact_safe_artifact_members', len(names) == len(set(names)) and len(names) > 0
              and set(names) == {p.relative_to(raw).as_posix() for p in raw.rglob('*') if p.is_file()}
              and safe)
        if not safe:
            raise ValueError('unsafe archive or extracted tree; refusing member reads')
        for name in names:
            if archive.read(name) != (raw / name).read_bytes():
                zip_failures.append(name)
    check('all_extracted_bytes_match', not zip_failures, zip_failures)
    check('exact_frozen_plan_at_execution_head', digest(plan_path) == PLAN_SHA == execution['plan_sha256']
          and plan_path.read_bytes() == read_git(args.repo_root, 'benchmarks/opencode-qa-pilot/confirmation-v1.json'))
    current_mismatches, commit_mismatches = [], []
    for name, identity in plan['frozen_harness_sha256'].items():
        if digest(args.repo_root / name) != identity:
            current_mismatches.append(name)
        if sha(read_git(args.repo_root, name)) != identity:
            commit_mismatches.append(name)
    check('frozen_harness_at_head_and_current', not current_mismatches and not commit_mismatches,
          {'files': len(plan['frozen_harness_sha256']), 'current_mismatches': current_mismatches, 'head_mismatches': commit_mismatches})
    main_mismatches = [name for name, identity in plan['main_file_sha256'].items() if digest(args.upstream_root / name) != identity]
    check('pinned_upstream_main_export', not main_mismatches and plan['upstream_main'] == 'a424e0ce06868dc9ad8f142f57de59b8f9b58699',
          {'files': len(plan['main_file_sha256']), 'mismatches': main_mismatches})
    old = load(args.repo_root / 'benchmarks/opencode-qa-pilot/experiment-v2.json')
    unchanged = ['evaluation', 'accounting', 'agent', 'provider', 'embedding', 'runtime',
                 'question_sha256', 'source_commit', 'source_file_count', 'source_inventory_sha256',
                 'dataset_sha256', 'rubric_sha256', 'agent_timeout_sec', 'slot_timeout_sec', 'n_attempts', 'max_retries', 'profiles']
    check('predeclared_model_task_evaluation_budgets_preserved', all(plan[k] == old[k] for k in unchanged), unchanged)
    check('fresh_phase_no_old_slot_reuse', plan['protocol_id'] == execution['protocol_id'] == PROTOCOL
          and plan['previous_phase']['reuse_old_slots'] is False and plan['previous_phase']['valid_pairs'] == 1
          and execution['repeat'] == repeat and execution['phase'] == plan['phase']
          and execution['parent_development_plan_sha256'] == plan['parent_development_plan_sha256'] == PARENT_PLAN_SHA
          and plan['parent_development_stopping'] == old['stopping'])
    binding = plan['offline_proof']
    archive_root = args.repo_root / binding['archive_path']
    proof = load(archive_root / 'proof.json')
    derivation = load(archive_root / 'package-derivation.json')
    origin = load(reports / 'offline-proof-origin.json')
    check('accepted_offline_proof_binding', execution['offline_proof'] == binding
          and digest(archive_root / 'proof.json') == binding['proof_sha256']
          and proof['valid'] is True and proof['quality_evaluated'] is False
          and origin['run']['id'] == binding['ci_run_id'] == 39314027006
          and origin['run']['head_sha'] == binding['ci_head_sha'] and origin['run']['conclusion'] == 'success'
          and origin['artifact']['id'] == binding['artifact_id'] and origin['verified_zip_sha256'] == binding['artifact_sha256']
          and origin['verified_file_count'] == 130)
    check('derived_package_binding_not_rust_rebuild', execution['candidate_sha256'] == plan['candidate_sha256'] == INSTALL_SHA == derivation['install_package_sha256']
          and execution['upstream_archive_sha256'] == plan['upstream_archive_sha256'] == derivation['upstream_archive_sha256']
          and derivation['binary_sha256'] == plan['candidate']['binary_sha256'])
    check('github_job_all_steps_success', job['conclusion'] == 'success'
          and all(step['conclusion'] == 'success' for step in job['steps']))
    step_names = [s['name'] for s in job['steps']]
    gate_names = ['Export and reverify current upstream main and fixed source',
                  'Verify and retrieve the exact successful Docker proof and install archive',
                  'Validate the separate frozen plan and all retained proof bytes',
                  'Prepare a fresh unchanged q001 task and verify both commands']
    check('pre_provider_gates_precede_only_execution', all(step_names.index(n) < step_names.index('Run this scheduled fresh baseline then zg pair') for n in gate_names))
    tests = (reports / 'controller-tests.txt').read_text()
    check('linux_controller_preflight_retained', 'Ran 23 tests' in tests and tests.rstrip().endswith('OK')
          and 'test_linux_descendant_ignores_term_after_leader_exits' in tests and 'skipped' not in tests.lower())
    check('one_live_baseline_then_one_live_zg', execution['dry_run'] is False and [s['profile'] for s in execution['slots']] == ['baseline', 'zvec-grep'])
    dry = load(reports / 'dry-run/execution.json')
    check('dry_run_separate_and_precedes_live', dry['dry_run'] is True and dry['plan_sha256'] == PLAN_SHA
          and dry['repeat'] == repeat and dry['protocol_id'] == PROTOCOL
          and [s['profile'] for s in dry['slots']] == ['baseline', 'zvec-grep']
          and all(s['status'] == 'dry_run' and s['repeat'] == repeat for s in dry['slots'])
          and max(s['finished_at_unix'] for s in dry['slots']) < execution['slots'][0]['started_at_unix'])
    source_inventory = [{'path': p.relative_to(args.source_root).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)}
                        for p in sorted(args.source_root.rglob('*')) if p.is_file()]
    check('independent_frozen_source_inventory', len(source_inventory) == plan['source_file_count'] == 1232
          and sha(json.dumps(source_inventory, sort_keys=True, separators=(',', ':')).encode()) == plan['source_inventory_sha256'])
    expected_files = {r['path']: r for r in source_inventory}
    instruction = (args.repo_root / 'benchmarks/zvec-issue-qa/tasks/q001/instruction.md').read_text()
    check('question_and_rubric_identity', sha(instruction.encode()) == plan['question_sha256']
          and digest(args.repo_root / 'benchmarks/zvec-issue-qa/evaluator/q001.json') == plan['rubric_sha256'])
    # Read frozen prepare literals via AST without invoking prepare or writing tasks.
    frozen_prepare = ast.parse(read_git(args.repo_root, 'benchmarks/opencode-qa-pilot/pilot.py'))
    prepare_fn = next(n for n in frozen_prepare.body if isinstance(n, ast.FunctionDef) and n.name == 'prepare')
    generated = {'instruction.md': instruction.encode()}
    for n in ast.walk(prepare_fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'write_text':
            target = n.func.value
            if isinstance(target, ast.BinOp) and isinstance(target.right, ast.Constant):
                name = target.right.value
                if name in ('Dockerfile', 'task.toml', 'tests/test.sh'):
                    generated['environment/Dockerfile' if name == 'Dockerfile' else name] = ast.literal_eval(n.args[0]).encode()
    task_root = args.source_root.parent.parent
    expected_task_names = {'environment/source/' + name for name in expected_files} | set(generated)
    check('four_generated_task_files_equal_frozen_prepare_bytes', len(generated) == 4
          and all((task_root / name).read_bytes() == value for name, value in generated.items()),
          {name: {'bytes': len(value), 'sha256': sha(value)} for name, value in generated.items()})
    native_package_hash, native_legacy_hash, task_inventory = recompute_task_hashes(task_root)
    check('complete_native_task_has_exact_1232_source_and_4_support_files',
          len(task_inventory) == 1236 and set(task_inventory) == expected_task_names
          and all(task_inventory['environment/source/' + name] == row['sha256'] for name, row in expected_files.items()))
    collection = load(reports / 'collection/collection.json')
    check('collection_repeat_and_plan_identity', collection['protocol_id'] == PROTOCOL
          and collection['repeat'] == repeat and collection['plan_sha256'] == PLAN_SHA
          and collection['parent_development_plan_sha256'] == PARENT_PLAN_SHA
          and [row['profile'] for row in collection['profiles']] == ['baseline', 'zvec-grep']
          and all(row['repeat'] == repeat and row['plan_sha256'] == PLAN_SHA for row in collection['profiles']))
    timing, containers, task_digests, identities = {}, {}, [], {}
    for slot in execution['slots']:
        profile, command = slot['profile'], slot['command']
        prefix = profile + ':'
        check(prefix + 'slot_confirmation_identity', slot['repeat'] == repeat and slot['protocol_id'] == PROTOCOL
              and slot['plan_sha256'] == PLAN_SHA and slot['phase'] == plan['phase']
              and slot['parent_development_plan_sha256'] == PARENT_PLAN_SHA)
        expected = {'--agent': 'repair_v2.qa_agents:QABaseline' if profile == 'baseline' else 'repair_v2.qa_agents:QAZvec',
                    '--model': 'custom-openai/qwen3.8-max', '--env': 'docker', '--n-attempts': '1', '--max-retries': '0',
                    '--n-concurrent': '1', '--agent-setup-timeout-multiplier': '5', '--include-task-name': 'zvec-q001'}
        check(prefix + 'native_command_limits', all(options(command, k) == [v] for k, v in expected.items()))
        check(prefix + 'dry_and_live_same_settings', normalized_command(command) == normalized_command(next(s['command'] for s in dry['slots'] if s['profile'] == profile)))
        check(prefix + 'single_slot_completed_cleanup', slot['status'] == 'exited' and slot['returncode'] == 0
              and slot['process_cleanup']['complete'] is True and isinstance(slot['process_cleanup']['process_group_id'], int)
              and slot['container_cleanup']['complete'] is True and slot['container_cleanup']['remaining_running'] == [])
        check(prefix + 'fresh_install_cache_checked_before_prepare', slot['install_cache_absent_before_prepare'] is True)
        parent = run_dir / 'runs' / ('q001-' + profile)
        trials = list(parent.glob('zvec-q001__*/'))
        check(prefix + 'one_retained_native_trial', len(trials) == 1)
        trial = trials[0]
        result, lock = load(trial / 'result.json'), load(trial / 'lock.json')
        cfg = result['config']; agent_cfg = cfg['agent']
        kwargs = dict(s.split('=', 1) for s in options(command, '--agent-kwarg'))
        opencode = json.loads(kwargs['opencode_config'])
        check(prefix + 'trial_native_config_matches_command', result['exception_info'] is None
              and agent_cfg['model_name'] == expected['--model'] and agent_cfg['name'] == expected['--agent']
              and agent_cfg['kwargs']['opencode_config'] == opencode
              and agent_cfg['kwargs']['collect_session_usage'] is True and agent_cfg['kwargs']['version'] == '1.18.4'
              and agent_cfg['env'] == {'OPENAI_API_KEY': '${OPENAI_API_KEY}'})
        check(prefix + 'no_extra_timeout_or_instruction_overrides', cfg['timeout_multiplier'] == 1
              and cfg['agent_setup_timeout_multiplier'] == 5 and cfg['extra_instruction_paths'] == []
              and all(agent_cfg[k] is None for k in ('override_timeout_sec', 'override_setup_timeout_sec', 'max_timeout_sec'))
              and cfg['environment']['type'] == 'docker' and cfg['environment']['delete'] is True)
        model = opencode['provider']['custom-openai']['models']['qwen3.8-max']
        check(prefix + 'model_request_configuration', opencode['provider']['custom-openai']['options'] == {'apiKey': '{env:OPENAI_API_KEY}', 'baseURL': plan['provider']['base_url']}
              and model['limit']['output'] == 32000 and model['interleaved']['field'] == 'reasoning_content'
              and model['options'] == {'enable_thinking': True, 'reasoningEffort': 'high'}
              and all(x['temperature'] == 0 and x['options']['seed'] == 42 and x['permission'] == {'websearch': 'deny', 'webfetch': 'deny'} for x in opencode['agent'].values()))
        # Only inspect the user instruction, not any assistant answer/tool content.
        atif = load(trial / 'agent/trajectory.json')
        users = [s['message'] for s in atif['steps'] if s['source'] == 'user']
        check(prefix + 'actual_q001_instruction', users and users[0] == instruction)
        task_digests.append(lock['task']['digest'])
        check(prefix + 'native_task_lock_and_legacy_checksum_independently_recomputed',
              lock['task']['name'] == 'zvec-q001'
              and lock['task']['digest'] == 'sha256:' + native_package_hash
              and result['task_checksum'] == native_legacy_hash,
              {'lock_packager_digest': lock['task']['digest'], 'result_legacy_dirhash': result['task_checksum'],
               'recomputed_packager_digest': 'sha256:' + native_package_hash, 'recomputed_legacy_dirhash': native_legacy_hash})
        observed_runtime = {}
        for when in ('before', 'after'):
            observed = load(trial / ('qa-evidence-v2/source-' + when + '/stdout.json'))
            file_map = {r['path']: {k: r.get(k) for k in ('path', 'bytes', 'sha256')} for r in observed['files'] if r['kind'] == 'file'}
            check(prefix + when + '_source_identity', all(file_map.get(name) == record for name, record in expected_files.items()))
            added = set(file_map) - set(expected_files)
            check(prefix + when + '_only_allowed_generated_files', not added if profile == 'baseline' else all(x == '.zg-bench-index.ignore' or x.startswith('.zvec-grep/') for x in added))
            runtime = observed['runtime']; observed_runtime[when] = runtime
            check(prefix + when + '_opencode_matches_offline_proof', runtime['opencode']['version'] == '1.18.4'
                  and runtime['opencode']['binary_sha256'] == runtime['opencode']['entrypoint_sha256'] == OPENCODE_SHA)
            if profile == 'baseline':
                check(prefix + when + '_baseline_has_no_zg', observed['zg_executable_present'] is False and observed['zg_home_present'] is False
                      and observed['mcp_config_present'] is False and observed['embedding_environment_names'] == [] and 'zg' not in runtime)
            else:
                zg = runtime['zg']
                check(prefix + when + '_original_rust_and_four_payloads', zg['version'] == '0.0.1'
                      and zg['binary_sha256'] == zg['entrypoint_sha256'] == derivation['binary_sha256']
                      and all(zg['payloads'][p['path'].removeprefix('package/')] == {'bytes': p['derived_bytes'], 'sha256': p['derived_sha256'], 'mode': p['mode']}
                              and p['source_sha256'] == p['derived_sha256'] and p['unchanged'] is True for p in derivation['runtime_payloads']))
        c = load(run_dir / (profile + '-containers.json'))
        check(prefix + 'one_container_observed', len(c) == 1 and all(x['running_when_observed'] for x in c.values()))
        container = next(iter(c.values())); containers[profile] = container
        mounts = container['mounts']
        volumes = [m for m in mounts if m['Type'] == 'volume']
        check(prefix + 'only_profile_nvm_cache_shared', len(volumes) == 1 and volumes[0]['Name'] == slot['install_cache_volume'] and volumes[0]['Destination'] == '/root/.nvm')
        check(prefix + 'no_source_home_db_or_index_mount', all(m['Destination'] in {'/root/.nvm', '/logs/verifier', '/logs/agent', '/logs/artifacts', '/tmp/zg-bench-zvec-grep.tgz'} for m in mounts))
        if profile == 'zvec-grep':
            check(prefix + 'candidate_mount_read_only', len([m for m in mounts if m['Destination'] == '/tmp/zg-bench-zvec-grep.tgz' and m['RW'] is False]) == 1)
        audit = load(trial / 'qa-source-audit.json')
        usage_record = load(trial / 'agent/session-usage.json')
        usage_seconds = usage_record['collection_wall_seconds']
        identities[profile] = {'root_session_id': usage_record['root_session_id'], 'trial_name': trial.name,
            'container_id': container['id'], 'install_cache_volume': slot['install_cache_volume'],
            'process_group_id': slot['process_cleanup']['process_group_id']}
        check(prefix + 'root_identity_matches_native_trajectory', usage_record['root_session_id'] == atif['session_id'])
        metrics = next(p['metrics'] for p in collection['profiles'] if p['profile'] == profile)
        raw_agent = duration(result['agent_execution'])
        adjusted = raw_agent - usage_seconds - audit['audit_wall_seconds'] - audit['export_wall_seconds']
        timing[profile] = {
            'native_environment_setup_seconds': duration(result['environment_setup']),
            'native_agent_setup_seconds': duration(result['agent_setup']),
            'native_agent_execution_raw_seconds': raw_agent,
            'usage_collection_seconds': usage_seconds,
            'source_runtime_audit_seconds': audit['audit_wall_seconds'],
            'session_export_seconds': audit['export_wall_seconds'],
            'solver_after_measured_collection_overhead_seconds': adjusted,
            'native_verifier_seconds': duration(result['verifier']),
            'native_trial_wall_seconds': duration(result),
            'outer_slot_wall_seconds': slot['finished_at_unix'] - slot['started_at_unix'],
            'slot_started_at_unix': slot['started_at_unix'], 'slot_finished_at_unix': slot['finished_at_unix'],
            'native_trial_started_at': result['started_at'], 'native_trial_finished_at': result['finished_at'],
            'docker_image_id': container['image'], 'docker_force_build': cfg['environment']['force_build'],
            'process_cleanup': slot['process_cleanup'], 'container_cleanup': slot['container_cleanup'],
        }
        check(prefix + 'collection_timing_recomputed', math.isclose(metrics['agent_wall_seconds'], raw_agent - usage_seconds, abs_tol=1e-6)
              and math.isclose(metrics['agent_wall_without_integrity_audit_seconds'], adjusted, abs_tol=1e-6)
              and metrics['integrity_audit_wall_seconds'] == audit['audit_wall_seconds']
              and metrics['session_export_wall_seconds'] == audit['export_wall_seconds']
              and metrics['usage_collection_wall_seconds'] == usage_seconds)
        check(prefix + 'native_and_outer_timing_bounds', raw_agent < plan['agent_timeout_sec'] and timing[profile]['outer_slot_wall_seconds'] < plan['slot_timeout_sec']
              and timing[profile]['native_agent_setup_seconds'] < plan['runtime']['setup_timeout_sec']
              and 0 < adjusted < raw_agent < timing[profile]['native_trial_wall_seconds'] < timing[profile]['outer_slot_wall_seconds'])
        if profile == 'zvec-grep':
            setup = load(trial / 'agent/zvec-grep-setup.json')
            check(prefix + 'native_embedding_and_setup_contract', kwargs['embedding_model'] == setup['embedding_model'] == plan['embedding']['model']
                  and kwargs['embedding_endpoint'] == setup['embedding_endpoint'] == plan['embedding']['endpoint']
                  and setup['package_sha256'] == INSTALL_SHA and setup['status'] == 'ready'
                  and setup['api_key_source'] == 'ZVEC_GREP_API_KEY' and setup['authorization_scope'] == 'workspace'
                  and '1,024 dimensions' in setup['index_status'] and 'cosine' in setup['index_status'])
            check(prefix + 'fresh_index_and_install', setup['install_reused_cache'] is False and setup['index_seed_available'] is False
                  and setup['index_cache_hit'] is False and '1143 scanned, 1143 added' in setup['index_stdout']
                  and '0 failed' in setup['index_stdout'] and 'Server: ready' in setup['mcp_server_status'])
            check(prefix + 'fixed_index_ignore', setup['index_ignore_sha256'] == digest(args.upstream_root / 'zg_bench/swe_qa/data/index.ignore'))
            timing[profile]['zg_setup'] = {k: setup[k] for k in ['install_duration_seconds', 'authorization_duration_seconds', 'index_duration_seconds',
                                                              'mcp_setup_duration_seconds', 'total_duration_seconds', 'started_at', 'finished_at']}
            check(prefix + 'zg_setup_timing_inside_agent_setup', math.isclose(duration(setup), setup['total_duration_seconds'], abs_tol=0.001)
                  and setup['total_duration_seconds'] <= timing[profile]['native_agent_setup_seconds'])
    check('same_task_digest_both_arms', len(set(task_digests)) == 1)
    check('distinct_fresh_container_ids_and_private_groups', containers['baseline']['id'] != containers['zvec-grep']['id']
          and execution['slots'][0]['process_cleanup']['process_group_id'] != execution['slots'][1]['process_cleanup']['process_group_id'])
    check('cleanup_before_next_slot', execution['slots'][0]['finished_at_unix'] <= execution['slots'][1]['started_at_unix'])
    check('native_docker_force_build_unchanged', all(t['docker_force_build'] is False for t in timing.values()))
    repeat_checks = CHECKS[check_start:]
    failures = [c for c in repeat_checks if not c['passed']]
    report = {
        'schema_version': 1, 'status': 'passed' if not failures else 'failed',
        'scope': 'saved execution and provenance only; root/child tool audit, usage arithmetic, and blind quality grading are separate',
        'run_id': RUN_ID, 'repeat': repeat, 'execution_head_sha': HEAD, 'plan_sha256': PLAN_SHA,
        'artifact_id': artifact['id'], 'artifact_sha256': zip_sha, 'artifact_file_count': len(names),
        'audit_script_sha256': digest(Path(__file__)), 'checks_passed': len(repeat_checks) - len(failures),
        'check_count': len(repeat_checks), 'findings': failures, 'checks': repeat_checks,
        'timing': timing, 'identities': identities, 'native_task_digest': task_digests[0],
        'controller_test_resource_warning_retained': 'ResourceWarning' in tests,
        'native_task_identity': {
            'regular_file_count': len(task_inventory), 'source_file_count': len(expected_files),
            'generated_support_file_count': len(generated),
            'lock_digest': 'sha256:' + native_package_hash, 'legacy_result_checksum': native_legacy_hash,
            'packager_algorithm': 'Harbor 0.18.0 publisher/packager.py: SHA256 of POSIX-relative file paths sorted lexically, each followed by NUL, file SHA256 hex and LF. All 1236 files survive the default ignores.',
            'legacy_algorithm': 'Harbor Task.checksum delegates to dirhash 0.5.0 defaults: recursively hash sorted name/data (or name/dirhash) descriptors; NUL between sorted properties, double NUL between sorted entries; ignore empty directories.',
            'path_semantics': 'Both hash file contents and relative names. Neither hashes the absolute task directory, root name, modes, owners or timestamps. The two algorithms intentionally produce different digests for identical task bytes.',
            'correction_history': 'The prior development execution audit initially equated these distinct hashes; its diagnostic and correction records remain in the immutable development archive. This confirmation audit independently recomputes both correctly from its first version.',
        },
        'timing_boundary': {
            'fresh': ['containers', 'profile-specific nvm installation cache volumes', 'per-container homes and sessions', 'zg index built with remote embeddings (no seed/cache hit)'],
            'not_isolated': 'Docker image/layer cache within each job; no independent image-cache clearing is configured.',
            'same_image_within_pair': containers['baseline']['image'] == containers['zvec-grep']['image'],
            'observed_environment_setup_seconds': {k: v['native_environment_setup_seconds'] for k, v in timing.items()},
            'interpretation': 'Report actual image IDs and environment setup times. A same-image pair can reuse Docker layers; image-ID equality alone does not identify which build steps hit cache.',
            'prohibited_claim': 'Outer-slot/native-trial wall is not a comparison with Docker image/build caches independently cleared before both arms.',
        },
        'limitations': [
            'Both repeats use the prospectively fixed baseline then zg order. Profiles were not counterbalanced or randomized; order effects cannot be separated from treatment effects by this design. This is an interpretation limit, not an added protocol gate.',
            'Cleanup proof is the saved controller check that each private process group and the recorded trial containers stopped. This audit does not inspect the now-finished remote runner or claim global process absence.',
            'The native CLI/config, pinned adapter/source, and recorded executable identities verify requested settings. Live HTTP request bodies and provider weight/version attestation were not retained.',
            'The source image uses ubuntu:24.04 and normal dependency installation; the observed image ID is not an immutable upstream OS/dependency lock.',
            'Controller-test ResourceWarnings, if present, are retained and flagged separately. Actual slot process/container cleanup is checked from each execution record.',
            'No QA benefit, pass/fail grade, candidate verdict, total-provider cost, or token-use conclusion is made in this execution-only report.',
            'Replay additionally requires the local execution Git commit, pinned upstream export, accepted proof archive, and frozen q001 source export; CLI paths are configurable.',
        ],
        'actions_this_audit': {'network_calls': 0, 'provider_calls': 0, 'CI_calls': 0, 'native_agent_runs': 0, 'frozen_or_raw_edits': 0, 'quality_grading': False},
    }
    return report


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def step_execution_interval(job):
    """Use executed step timestamps, never the queued job.started_at placeholder."""
    intervals, unconfirmed = [], []
    if job.get('status') != 'completed':
        unconfirmed.append({'reason': 'job_not_completed'})
    for step in job.get('steps', []):
        if step.get('conclusion') == 'skipped':
            continue  # A skipped step is not an observed execution interval.
        identity = {k: step.get(k) for k in ('number', 'name', 'started_at', 'completed_at')}
        try:
            if step.get('status') != 'completed':
                raise ValueError('step_not_completed')
            start = datetime.fromisoformat(step['started_at'].replace('Z', '+00:00'))
            finish = datetime.fromisoformat(step['completed_at'].replace('Z', '+00:00'))
            if start.utcoffset() is None or finish.utcoffset() is None or start > finish:
                raise ValueError('invalid_step_timestamp_interval')
            intervals.append({**identity, 'started_at_unix': start.timestamp(), 'finished_at_unix': finish.timestamp()})
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            unconfirmed.append({**identity, 'reason': type(exc).__name__ + ': ' + str(exc)})
    if not intervals:
        unconfirmed.append({'reason': 'no_executed_step_timestamps'})
    result = {'confirmed': not unconfirmed, 'unconfirmed': unconfirmed,
              'executed_steps': intervals, 'job_started_at_as_reported': job.get('started_at'),
              'method': 'Minimum actual executed-step started_at through maximum executed-step completed_at; job.started_at is not an execution boundary.'}
    if intervals:
        result.update(started_at_unix=min(row['started_at_unix'] for row in intervals),
                      finished_at_unix=max(row['finished_at_unix'] for row in intervals))
    return result


def serial_execution_evidence(job_rows, repeat_reports):
    """Require both actual job-step and native/outer-slot non-overlap, fail closed."""
    steps = {repeat: step_execution_interval(job) for repeat, job in job_rows.items()}
    complete = set(steps) == set(REPEATS) and all(row['confirmed'] for row in steps.values())
    distinct = len(job_rows) == 2 and job_rows['c01']['id'] != job_rows['c02']['id']
    step_serial = complete and steps['c01']['finished_at_unix'] <= steps['c02']['started_at_unix']
    outer, native, errors = {}, {}, []
    for repeat in REPEATS:
        try:
            timing = repeat_reports[repeat]['timing']
            if set(timing) != {'baseline', 'zvec-grep'}:
                raise ValueError('both_native_profiles_required')
            outer[repeat] = [(timing[p]['slot_started_at_unix'], timing[p]['slot_finished_at_unix'])
                             for p in ('baseline', 'zvec-grep')]
            native[repeat] = [(timestamp(timing[p]['native_trial_started_at']), timestamp(timing[p]['native_trial_finished_at']))
                              for p in ('baseline', 'zvec-grep')]
            if not all(math.isfinite(first) and math.isfinite(last) and first <= last
                       for first, last in outer[repeat] + native[repeat]):
                raise ValueError('invalid_native_or_outer_interval')
            if not (outer[repeat][0][1] <= outer[repeat][1][0] and native[repeat][0][1] <= native[repeat][1][0]):
                raise ValueError('profiles_overlap_within_repeat')
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({'repeat': repeat, 'error_type': type(exc).__name__, 'error': str(exc)})
    slots_complete = not errors and set(outer) == set(native) == set(REPEATS)
    outer_serial = slots_complete and max(last for _, last in outer['c01']) <= min(first for first, _ in outer['c02'])
    native_serial = slots_complete and max(last for _, last in native['c01']) <= min(first for first, _ in native['c02'])
    return {'confirmed': bool(distinct and step_serial and outer_serial and native_serial),
            'distinct_jobs': distinct, 'step_intervals_confirmed': complete, 'step_execution_nonoverlap': bool(step_serial),
            'outer_slots_nonoverlap': bool(outer_serial), 'native_trials_nonoverlap': bool(native_serial),
            'step_intervals': steps, 'outer_slot_intervals': outer, 'native_trial_intervals': native,
            'unconfirmed_slot_intervals': errors,
            'overlap_tolerance_seconds': 0,
            'job_started_at_is_not_used_to_establish_serial_execution': True}


def expected_artifacts(values):
    result = {}
    for value in values:
        repeat, identifier, checksum = value.split(':')
        if repeat not in REPEATS or repeat in result or not identifier.isdigit() or re.fullmatch('[0-9a-f]{64}', checksum) is None:
            raise ValueError('expected artifact format: c01:positive-id:sha256 (and c02)')
        if int(identifier) <= 0:
            raise ValueError('artifact id must be positive')
        result[repeat] = {'id': int(identifier), 'sha256': checksum}
    if set(result) != set(REPEATS):
        raise ValueError('both c01 and c02 artifact identities are required')
    return result


def main(argv=None):
    global CHECK_PREFIX
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-root', type=Path, default=BASE / 'confirmation-v1-ci')
    parser.add_argument('--repo-root', type=Path, default=REPO)
    parser.add_argument('--upstream-root', type=Path, default=BASE / 'upstream-main/benchmarks/swe-qa-bench')
    parser.add_argument('--source-root', type=Path, default=BASE / 'prepared-v2/tasks/zvec-q001/environment/source',
                        help='Verified fixed source/task reference only; never a historical metric input.')
    parser.add_argument('--expected-artifact', action='append', required=True,
                        metavar='REPEAT:ID:SHA256', help='Two externally supplied final artifact identities, one per repeat.')
    parser.add_argument('--report', type=Path, default=BASE / 'confirmation-v1-execution-review/execution-review.json')
    args = parser.parse_args(argv)
    expected = expected_artifacts(args.expected_artifact)
    CHECKS.clear()
    reports, job_rows = {}, {}
    try:
        metadata = load(args.evidence_root / 'run-metadata.json')
        jobs = load(args.evidence_root / 'job-metadata.json')['jobs']
        artifacts = load(args.evidence_root / 'artifacts-metadata.json')['artifacts']
        check('exact_two_scheduled_jobs', len(jobs) == 2 and {j['name'] for j in jobs} == {'confirmation c01', 'confirmation c02'})
        check('exact_two_repeat_artifacts', len(artifacts) == 2 and {a['name'] for a in artifacts}
              == {f'main-opencode-q001-confirmation-v1-{RUN_ID}-{repeat}' for repeat in REPEATS})
        for repeat in REPEATS:
            CHECK_PREFIX = repeat + ':'
            start = len(CHECKS)
            try:
                matching_jobs = [j for j in jobs if j['name'] == 'confirmation ' + repeat]
                matching_artifacts = [a for a in artifacts if a['name'] == f'main-opencode-q001-confirmation-v1-{RUN_ID}-{repeat}']
                if len(matching_jobs) != 1 or len(matching_artifacts) != 1:
                    raise ValueError('one exact job and artifact required per scheduled repeat')
                job, artifact = matching_jobs[0], matching_artifacts[0]
                job_rows[repeat] = job
                check('external_artifact_identity', artifact['id'] == expected[repeat]['id']
                      and artifact['digest'] == 'sha256:' + expected[repeat]['sha256'])
                check('completed_hosted_linux_job', job['status'] == 'completed' and job['conclusion'] == 'success'
                      and 'ubuntu-24.04' in job.get('labels', []))
                reports[repeat] = audit_repeat(args, repeat, metadata, artifact, job)
                bounds = [(v['slot_started_at_unix'], v['slot_finished_at_unix']) for v in reports[repeat]['timing'].values()]
                step_interval = step_execution_interval(job)
                check('actual_job_step_timestamps_confirmed', step_interval['confirmed'], step_interval)
                # API step times have whole-second precision; this bound check
                # retains the existing one-second endpoint resolution allowance.
                # Cross-job overlap checks below have zero tolerance.
                check('all_slots_within_actual_job_step_interval', step_interval['confirmed'] and len(bounds) == 2
                      and all(step_interval['started_at_unix'] <= first <= last
                              <= step_interval['finished_at_unix'] + 1 for first, last in bounds))
            except Exception as exc:
                check('evidence_read_or_validation_completed', False,
                      {'error_type': type(exc).__name__, 'error': str(exc)})
                reports[repeat] = {'repeat': repeat, 'status': 'incomplete',
                    'error_type': type(exc).__name__, 'error': str(exc),
                    'checks': CHECKS[start:], 'timing': {}, 'identities': {},
                    'interpretation': 'Missing or failed evidence cannot establish a completed valid pair. Other repeat is still audited.'}
            # Include outer identity/bounds checks in each repeat status.
            rows = CHECKS[start:]
            failures = [row for row in rows if not row['passed']]
            reports[repeat].update(checks=rows, check_count=len(rows), checks_passed=len(rows)-len(failures), findings=failures)
            if failures and reports[repeat]['status'] == 'passed':
                reports[repeat]['status'] = 'failed'
        CHECK_PREFIX = ''
        serial = serial_execution_evidence(job_rows, reports)
        check('two_distinct_serial_hosted_jobs_and_native_slots', serial['confirmed'], serial)
        identities = [row for report in reports.values() for row in report['identities'].values()]
        roots = [row['root_session_id'] for row in identities]
        check('four_distinct_root_sessions_without_development_reuse', len(roots) == len(set(roots)) == 4
              and all(isinstance(root, str) and root for root in roots)
              and not set(roots) & {'ses_eaa3df344ffeEuRq9OwAQ8pd1W', 'ses_eaa2e703effewhqbU6eJK6Yy00'})
        check('four_distinct_containers', len(identities) == 4 and len({row['container_id'] for row in identities}) == 4)
        check('two_independently_completed_execution_audits', set(reports) == set(REPEATS)
              and all(report['status'] == 'passed' for report in reports.values()))
    except Exception as exc:
        CHECK_PREFIX = ''
        check('global_evidence_read_completed', False, {'error_type': type(exc).__name__, 'error': str(exc)})
    CHECK_PREFIX = ''
    failures = [row for row in CHECKS if not row['passed']]
    result = {
        'schema_version': 1, 'status': 'passed' if not failures else 'failed',
        'scope': 'confirmation execution/provenance only; no answer grading, tool-scope evaluation, usage arithmetic or candidate verdict',
        'run_id': RUN_ID, 'execution_head_sha': HEAD, 'plan_sha256': PLAN_SHA,
        'expected_artifacts': expected, 'repeats': reports,
        'check_count': len(CHECKS), 'checks_passed': len(CHECKS)-len(failures), 'findings': failures, 'checks': CHECKS,
        'runner_observations': {repeat: {k: job.get(k) for k in ('id', 'name', 'runner_id', 'runner_name', 'runner_group_name', 'labels', 'started_at', 'completed_at')}
                                for repeat, job in job_rows.items()},
        'serial_execution': serial_execution_evidence(job_rows, reports),
        'interpretation_limits': [
            'Both repeats have the fixed baseline-to-zg profile order; no counterbalancing or randomized profile order. This is a design limitation, not a post hoc failure rule.',
            'Job-level started_at is retained as reported and may be a queued placeholder. Serial execution requires actual completed-step intervals and native/outer-slot intervals; missing step timestamps leave it unconfirmed.',
            'Separate GitHub-hosted jobs and per-profile absent-before-prepare installation volumes establish the configured fresh-runner boundary. This audit cannot inspect a terminated remote runner.',
            'Within each pair Docker image/layer cache may be shared. Raw outer/trial times are descriptive, not comparisons with independently cleared image caches.',
            'Only raw, measured usage-capture, evidence-audit and export durations are reported here. No historical metric is pooled, and no token/accounting or QA conclusion is made.',
            'An execution pass does not establish that either repeat meets the frozen scoped-input, quality and relevant-MCP conditions; those require the separate evidence and blinded-grade audits.',
        ],
        'source_reference': str(args.source_root), 'audit_script_sha256': digest(Path(__file__)),
        'actions_this_audit': {'network_calls': 0, 'provider_calls': 0, 'CI_calls': 0, 'native_agent_runs': 0,
                             'frozen_or_raw_edits': 0, 'quality_grading': False, 'confirmation_blind_key_read': False},
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': result['status'], 'checks': len(CHECKS), 'findings': failures,
                      'report': str(args.report), 'report_sha256': digest(args.report)}, indent=2))
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
