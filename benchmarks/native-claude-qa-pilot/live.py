#!/usr/bin/env python3
"""One frozen native-Claude q001 pair; no grading or implicit usage fallback.

Drafting/importing this module makes no network calls. All subcommands require
a separately frozen live plan and accepted, byte-bound offline Docker proof.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import math
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent / 'opencode-qa-pilot')]
import pilot
from accounting_v2 import analyze
from native_claude.integration import (MODEL, PACKAGE_SHA, build_command,
                                      docker_info, stop_process_group, validate_inputs)
from repair_v2.package import EXPECTED_MEMBERS, RUNTIME_PATHS

PROTOCOL = 'native-claude-q001-live-v2'
PROFILES = ['baseline', 'zvec-grep']
FIXED = {k: v for k, v in pilot.FIXED.items() if k not in ('protocol_id', 'phase', 'agent')}
FIXED.update(protocol_id=PROTOCOL, phase='native-claude-development', model=MODEL,
             agent={'name': 'claude-code', 'version': '2.1.212',
                    'reasoning_effort': 'high', 'max_budget_usd': 4.0})
SAFE_ENV = {'PATH', 'HOME', 'USER', 'SHELL', 'LANG', 'LC_ALL', 'TMPDIR',
            'SSL_CERT_FILE', 'SSL_CERT_DIR'}
MODEL_USAGE_FIELDS = ('inputTokens', 'outputTokens', 'cacheReadInputTokens', 'cacheCreationInputTokens')
USAGE_ONLY_ERRORS = {'missing_usage', 'invalid_usage_fields', 'invalid_usage_update', 'usage_regression'}


def https_endpoint(value):
    if not isinstance(value, str):
        raise ValueError('explicit_https_endpoint_required')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('explicit_noncredential_https_endpoint_required')
    return value


def validate_offline(plan, repo_root):
    ref = plan['offline_proof']
    proof_path = pilot.safe_file(repo_root, ref['directory'] + '/proof.json')
    if pilot.digest(proof_path) != ref['proof_sha256']:
        raise ValueError('offline_proof_hash_mismatch')
    proof = pilot.load(proof_path)
    if (proof.get('protocol') != 'native-claude-offline-v1' or proof.get('valid') is not True
            or proof.get('quality_evaluated') is not False or proof.get('errors') != []
            or [r.get('profile') for r in proof.get('slots', [])] != PROFILES
            or [r.get('profile') for r in proof.get('verification', [])] != PROFILES
            or set(proof.get('pair_checks', {})) != {'same_actual_claude_binary', 'same_native_init_tools_except_zg'}
            or not all(v is True for v in proof['pair_checks'].values())):
        raise ValueError('offline_pair_not_successful')
    for slot, verification in zip(proof['slots'], proof['verification']):
        if (slot.get('status') != 'exited' or slot.get('returncode') != 0 or not cleanup_complete(slot)
                or verification.get('valid') is not True or not verification.get('checks')
                or any(c.get('passed') is not True for c in verification['checks'])):
            raise ValueError('offline_slot_or_check_failed')
    # R2's frozen writer includes its previous intermediate proof.json in its
    # inventory. Bind FINAL proof bytes separately above, all other bytes here.
    expected = [r for r in proof['evidence_files'] if r['path'] != 'proof.json']
    actual = [r for r in pilot.file_inventory(proof_path.parent) if r['path'] != 'proof.json']
    if not expected or actual != expected:
        raise ValueError('offline_retained_evidence_changed')
    archived_inputs = proof_path.parent.parent / 'inputs.json'
    if archived_inputs.is_symlink() or pilot.digest(archived_inputs) != ref['inputs_sha256']:
        raise ValueError('offline_archive_inputs_mismatch')
    review = pilot.safe_file(repo_root, ref['review_path'])
    if pilot.digest(review) != ref['review_sha256']:
        raise ValueError('offline_review_hash_mismatch')
    accepted = pilot.load(review)
    if accepted.get('valid') is not True or accepted.get('proof_sha256') != ref['proof_sha256']:
        raise ValueError('offline_review_not_accepted')
    return proof


def validate_plan(plan_path, main_root, prepared, candidate, repo_root=REPO):
    inputs = validate_inputs(repo_root, main_root)
    plan = pilot.load(plan_path)
    if plan.get('frozen') is not True:
        raise ValueError('separately_frozen_live_plan_required')
    for key, expected in FIXED.items():
        if plan.get(key) != expected or type(plan.get(key)) is not type(expected):
            raise ValueError('live_protocol_mismatch:' + key)
    https_endpoint(plan['provider']['base_url'])
    https_endpoint(plan['embedding']['endpoint'])
    if plan['embedding']['model'] != 'qwen/qwen3.7-text-embedding':
        raise ValueError('embedding_model_mismatch')
    if (plan.get('candidate_sha256') != PACKAGE_SHA or candidate.is_symlink()
            or pilot.digest(candidate) != PACKAGE_SHA):
        raise ValueError('candidate_package_mismatch')
    native_inputs = repo_root / 'benchmarks/native-claude-qa-pilot/inputs.json'
    if (plan['offline_proof']['inputs_sha256'] != pilot.digest(native_inputs)
            or type(plan['offline_proof'].get('inputs_revision')) is not int
            or inputs.get('revision') != plan['offline_proof']['inputs_revision']):
        raise ValueError('offline_inputs_mismatch')
    original = repo_root / 'benchmarks/opencode-qa-pilot/experiment.json'
    pilot.validate_plan(original, repo_root, main_root)
    pilot.validate_prepared(prepared, original)
    question = repo_root / 'benchmarks/zvec-issue-qa/tasks/q001/instruction.md'
    rubric = repo_root / 'benchmarks/zvec-issue-qa/evaluator/q001.json'
    if pilot.digest(question) != plan['question_sha256'] or pilot.digest(rubric) != plan['rubric_sha256']:
        raise ValueError('question_or_rubric_changed')
    pins = plan.get('frozen_harness_sha256', {})
    workflow, decision_path = plan.get('execution_workflow', ''), plan.get('execution_decision', '')
    required = {str(HERE.relative_to(REPO) / name) for name in ('live.py', 'test_live.py', 'inputs.json', 'accounting_v2.py', 'test_accounting_v2.py')}
    if not workflow.startswith('.github/workflows/') or not decision_path:
        raise ValueError('live_workflow_and_decision_required')
    if not required.union({workflow, decision_path}) <= pins.keys():
        raise ValueError('live_harness_pins_missing')
    for relative, expected in pins.items():
        if pilot.digest(pilot.safe_file(repo_root, relative)) != expected:
            raise ValueError('live_harness_changed:' + relative)
    decision = pilot.load(pilot.safe_file(repo_root, decision_path))
    if (decision.get('protocol_id') != PROTOCOL or decision.get('status') != 'approved_for_one_fresh_pair'
            or decision.get('offline_proof_sha256') != plan['offline_proof']['proof_sha256']
            or decision.get('n_attempts') != 1 or decision.get('max_retries') != 0):
        raise ValueError('live_execution_decision_missing_or_mismatched')
    proof = validate_offline(plan, repo_root)
    return plan, proof


@contextmanager
def live_environment(plan, profile, main_root, *, require_credentials=True):
    """Set the parent route before Harbor constructs per-call Claude env."""
    if profile not in PROFILES:
        raise ValueError('invalid_profile')
    saved = dict(os.environ)
    env = {k: v for k, v in saved.items() if k in SAFE_ENV}
    for key in ('ANTHROPIC_API_KEY',) + (('ZVEC_GREP_API_KEY',) if profile == 'zvec-grep' else ()):
        value = saved.get(key, '').strip()
        if require_credentials and not value:
            raise ValueError('missing_credential:' + key)
        # Dry-run needs the same --agent-env reference, but never reads/saves keys.
        env[key] = value if require_credentials else 'dry-run-placeholder-not-a-credential'
    env.update(ANTHROPIC_BASE_URL=https_endpoint(plan['provider']['base_url']),
               PYTHONPATH=os.pathsep.join([str(HERE), str(pilot.HERE), str(main_root)]))
    if profile == 'zvec-grep':
        env['ZVEC_GREP_ENDPOINT'] = https_endpoint(plan['embedding']['endpoint'])
    os.environ.clear()
    os.environ.update(env)
    try:
        yield env
    finally:
        os.environ.clear()
        os.environ.update(saved)


def cleanup_complete(slot):
    return all(slot.get(k, {}).get('complete') is True for k in ('process_cleanup', 'container_cleanup'))


def run_slot(args, plan, profile):
    """Same native setup/slot bounds as the accepted fake-provider runner."""
    slot = {'profile': profile, 'status': 'planned', 'started_at_unix': time.time()}
    known, proc = {}, None
    old_handler = signal.signal(signal.SIGALRM, pilot.deadline_expired)
    signal.setitimer(signal.ITIMER_REAL, plan['slot_timeout_sec'])
    deadline = time.monotonic() + plan['slot_timeout_sec']
    try:
        with live_environment(plan, profile, args.main_root) as env:
            runner = pilot.native(args.main_root)
            package = runner.prepare_zvec_grep_package(str(args.candidate))
            volume = runner.setup_cache_volume_name('claude-code', profile,
                zvec_grep_package=package.install_spec,
                zvec_grep_package_sha256=package.sha256 if profile == 'zvec-grep' else None,
                embedding_model=plan['embedding']['model'])
            volumes = [volume, runner.claude_code_install_cache_volume_name(profile)]
            for name in volumes:
                found = subprocess.run(['docker', 'volume', 'inspect', name], capture_output=True, timeout=20)
                if found.returncode == 0:
                    raise ValueError('install_cache_not_fresh')
            cache = runner.prepare_setup_cache('claude-code', profile,
                zvec_grep_package=str(args.candidate), embedding_model=plan['embedding']['model'])
            overlay = pilot.load(cache.compose_path)
            if sorted(v['name'] for v in overlay['volumes'].values()) != sorted(volumes):
                raise ValueError('fresh_cache_identity_mismatch')
            slot['fresh_volumes'] = volumes
            if profile == 'zvec-grep':
                binds = [v for v in overlay['services']['main']['volumes']
                         if v.get('target') == '/tmp/zg-bench-zvec-grep.tgz']
                if len(binds) != 1 or binds[0].get('read_only') is not True:
                    raise ValueError('candidate_readonly_mount_missing')
                slot['install_package_source'] = str(Path(binds[0]['source']).resolve())
                slot['install_package_sha256'] = pilot.digest(Path(slot['install_package_source']))
                if slot['install_package_sha256'] != PACKAGE_SHA:
                    raise ValueError('mounted_candidate_hash_mismatch')
            pilot.write(args.output / (profile + '-compose.json'), overlay)
            command = build_command(runner, runner.load_suite(args.prepared / 'suite.yaml'),
                profile, args.prepared, args.output, cache, plan['embedding']['endpoint'])
            slot.update(command=command, status='running')
            pilot.write(args.output / (profile + '-slot.json'), slot)
            with (args.output / (profile + '-job.log')).open('wb', buffering=0) as log:
                proc = subprocess.Popen(command, cwd=args.main_root, env=env,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                while proc.poll() is None and time.monotonic() < deadline:
                    pilot.capture_slot_containers(args.output, profile, known)
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                slot.update(status='timeout' if proc.poll() is None else 'exited',
                            returncode=124 if proc.poll() is None else proc.returncode)
    except TimeoutError:
        slot.update(status='timeout', returncode=124)
    except Exception as exc:
        # Exception strings from subprocess/provider machinery may contain keys.
        slot.update(status='failed', error_type=type(exc).__name__)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        try:
            slot['process_cleanup'] = stop_process_group(proc) if proc is not None else {'complete': True, 'no_process_started': True}
        except Exception as exc:
            slot['process_cleanup'] = {'complete': False, 'error_type': type(exc).__name__}
        try:
            slot['container_cleanup'] = pilot.cleanup_slot_containers(args.output, profile, known)
        except Exception as exc:
            slot['container_cleanup'] = {'complete': False, 'error_type': type(exc).__name__}
        pilot.write(args.output / (profile + '-containers.json'), known)
        slot['finished_at_unix'] = time.time()
        pilot.write(args.output / (profile + '-slot.json'), slot)
    return slot


def run_pair(args, plan, *, dry_run=False):
    if not dry_run:
        for key in ('ANTHROPIC_API_KEY', 'ZVEC_GREP_API_KEY'):
            if not os.environ.get(key, '').strip():
                raise ValueError('missing_credential:' + key)
    args.output.mkdir(parents=True, exist_ok=False)
    record = {'schema_version': 1, 'protocol_id': PROTOCOL,
              'plan_sha256': pilot.digest(args.plan), 'candidate_sha256': PACKAGE_SHA,
              'offline_proof_sha256': plan['offline_proof']['proof_sha256'],
              'offline_inputs_sha256': plan['offline_proof']['inputs_sha256'],
              'dry_run': dry_run, 'slots': [], 'quality_evaluated': False}
    pilot.write(args.output / 'execution.json', record)
    if not dry_run:
        record['system'] = docker_info()
    for profile in PROFILES:
        if record['slots'] and not dry_run and not cleanup_complete(record['slots'][-1]):
            slot = {'profile': profile, 'status': 'not_started', 'reason': 'previous_slot_cleanup_unconfirmed'}
        elif dry_run:
            with live_environment(plan, profile, args.main_root, require_credentials=False):
                runner = pilot.native(args.main_root)
                cache = SimpleNamespace(zvec_grep_package='/tmp/zg-bench-zvec-grep.tgz' if profile == 'zvec-grep' else None,
                                        zvec_grep_package_sha256=PACKAGE_SHA if profile == 'zvec-grep' else None)
                command = build_command(runner, runner.load_suite(args.prepared / 'suite.yaml'),
                    profile, args.prepared, args.output, cache, plan['embedding']['endpoint'])
                slot = {'profile': profile, 'status': 'dry_run', 'command': command}
        else:
            slot = run_slot(args, plan, profile)
        record['slots'].append(slot)
        pilot.write(args.output / 'execution.json', record)
    return record


def root_stream(path):
    """Retain one unmodified terminal result without substituting a child final."""
    errors, results, inits = [], [], []
    try:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(row, dict):
                    raise ValueError()
            except ValueError:
                errors.append({'code': 'malformed_stream_line', 'line': number})
                continue
            if row.get('type') == 'result':
                results.append(row)
            if row.get('type') == 'system' and row.get('subtype') == 'init':
                inits.append(row)
    except (OSError, UnicodeError) as exc:
        errors.append({'code': 'stream_unreadable', 'type': type(exc).__name__})
    if len(results) != 1:
        errors.append({'code': 'expected_one_terminal_result', 'count': len(results)})
    result = results[0] if len(results) == 1 else None
    answer = result.get('result') if result else None
    success = bool(result and result.get('subtype') == 'success' and result.get('is_error') is False
                   and isinstance(answer, str) and answer)
    return {'result': result, 'errors': errors, 'root_result_success': success,
            'final_answer': answer if success else None, 'inits': inits}


def reported_model_usage(result):
    raw = result.get('modelUsage') if isinstance(result, dict) else None
    out = {'scope': 'claude-reported-model-usage', 'available': False, 'raw': raw,
           'usage': None, 'cost_usd': None, 'proves_complete_session_tree': False,
           'limitations': 'CLI-reported model totals; not independent provider billing or proof of persisted child completeness.'}
    if not isinstance(raw, dict) or set(raw) != {MODEL} or not isinstance(raw[MODEL], dict):
        out['unavailable_reason'] = 'missing_or_unexpected_model_usage'
        return out
    values = raw[MODEL]
    if any(type(values.get(k)) not in (int, float) or not math.isfinite(values[k]) or values[k] < 0 for k in MODEL_USAGE_FIELDS):
        out['unavailable_reason'] = 'missing_or_invalid_required_numeric_field'
        return out
    out.update(available=True, usage={
        'input_tokens': values['inputTokens'] + values['cacheReadInputTokens'] + values['cacheCreationInputTokens'],
        'uncached_input_tokens': values['inputTokens'], 'cache_read_tokens': values['cacheReadInputTokens'],
        'cache_write_tokens': values['cacheCreationInputTokens'], 'output_tokens': values['outputTokens']})
    return out


def native_log_bytes_match(agent):
    snapshot = agent.parent / 'qa-native-claude-logs'
    def relevant(path):
        return path.name == 'claude-code.txt' or path.suffix == '.jsonl' and 'projects' in path.parts
    def inventory(root):
        rows = {}
        for path in root.rglob('*'):
            if path.is_symlink():
                raise ValueError('native_log_symlink')
            if path.is_file() and relevant(path):
                rows[path.relative_to(root).as_posix()] = pilot.digest(path)
        return rows
    original = inventory(agent)
    return bool(original) and original == inventory(snapshot)


def metric_consistency(reported, accounting):
    """A broader CLI total cannot be smaller than fully retained message totals."""
    contradictions = []
    if any(e.get('code') == 'usage_regression' for e in accounting.get('errors', [])):
        contradictions.append('retained_usage_update_regressed')
    comparable = reported['available'] and accounting.get('complete') is True
    if comparable:
        for key in ('uncached_input_tokens', 'cache_read_tokens', 'cache_write_tokens', 'output_tokens'):
            if reported['usage'][key] < accounting['usage'][key]:
                contradictions.append('reported_below_complete_persisted:' + key)
    equal = comparable and all(reported['usage'][key] == accounting['usage'][key]
                              for key in ('uncached_input_tokens', 'cache_read_tokens', 'cache_write_tokens', 'output_tokens'))
    status = ('contradictory' if contradictions else 'complete-and-matching' if equal
              else 'reported-exceeds-persisted-coverage-unverified' if comparable else 'coverage-unverified')
    return {'compared_to_complete_persisted_tree': comparable, 'corroboration_status': status,
            'contradictions': contradictions,
            'metric_conclusion_withheld': bool(contradictions)}


def mounts_valid(containers, slot):
    if not containers or len(slot.get('fresh_volumes', [])) != 2:
        return False
    for container in containers.values():
        mounts = {m.get('Destination'): m for m in container['mounts']}
        for target, name in zip(('/root/.nvm', '/root/.local'), slot['fresh_volumes']):
            if mounts.get(target, {}).get('Type') != 'volume' or mounts[target].get('Name') != name:
                return False
        for target, mount in mounts.items():
            if target in ('/root/.nvm', '/root/.local'):
                continue
            # Docker paths belong to the recorded Linux host, not this analyst's
            # filesystem (e.g. macOS resolves /home through a different mount).
            source = PurePosixPath(mount.get('Source', '/'))
            if not source.is_absolute() or '..' in source.parts:
                return False
            if target == '/tmp/zg-bench-zvec-grep.tgz':
                if (slot['profile'] != 'zvec-grep' or mount.get('RW') is not False
                        or str(source) != slot.get('install_package_source')
                        or slot.get('install_package_sha256') != PACKAGE_SHA):
                    return False
            elif isinstance(target, str) and target.startswith('/logs/'):
                trial = PurePosixPath(container['trial_dir'])
                if not trial.is_absolute() or '..' in trial.parts:
                    return False
                if source != trial and trial not in source.parents:
                    return False
            else:
                return False
        if slot['profile'] == 'zvec-grep' and '/tmp/zg-bench-zvec-grep.tgz' not in mounts:
            return False
    return True


def timing_interval(value):
    record = {'timestamps': value, 'seconds': None}
    if isinstance(value, dict):
        try:
            start, end = [datetime.fromisoformat(value[k].replace('Z', '+00:00'))
                          for k in ('started_at', 'finished_at')]
            seconds = (end - start).total_seconds()
            if math.isfinite(seconds) and seconds >= 0:
                record['seconds'] = seconds
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
    return record


def collect(args, plan, offline_proof):
    collection_started = time.monotonic()
    execution = pilot.load(args.output / 'execution.json')
    if (execution.get('protocol_id') != PROTOCOL or execution.get('plan_sha256') != pilot.digest(args.plan)
            or execution.get('offline_proof_sha256') != plan['offline_proof']['proof_sha256']
            or execution.get('candidate_sha256') != PACKAGE_SHA or execution.get('dry_run') is not False
            or [s.get('profile') for s in execution.get('slots', [])] != PROFILES):
        raise ValueError('execution_identity_mismatch')
    destination = args.output / 'collection'
    destination.mkdir(exist_ok=False)
    rows = []
    for slot in execution['slots']:
        profile = slot['profile']
        row = {'profile': profile, 'slot_status': slot['status'], 'quality': None,
               'quality_status': 'not_evaluated', 'errors': [], 'answer_present': False,
               'structural_valid': False, 'strict_usage_available': False, 'strict_usage': None,
               'reported_metric_eligible': False, 'native_init_tools': None,
               'claude_reported_model_usage': reported_model_usage(None)}
        rows.append(row)
        paths = list((args.output / 'runs' / ('q001-' + profile)).glob('*/agent'))
        if len(paths) != 1:
            row['errors'].append('expected_one_retained_trial')
            continue
        agent = paths[0]
        row['trial_directory'] = str(agent.parent.relative_to(args.output))
        try:
            stream = root_stream(agent / 'claude-code.txt')
            row['stream_errors'] = stream['errors']
            row['root_result_success'] = stream['root_result_success']
            inits = stream['inits']
            row['native_init_tools'] = inits[0].get('tools') if len(inits) == 1 else None
            row['init_identity_valid'] = bool(len(inits) == 1 and inits[0].get('model') == MODEL
                and stream['result'] and inits[0].get('session_id') == stream['result'].get('session_id'))
            row['claude_reported_model_usage'] = reported_model_usage(stream['result'])
            pilot.write(destination / (profile + '-root-result.json'), stream['result'])
            if stream['final_answer'] is not None:
                answer_path = destination / (profile + '-answer.txt')
                answer_path.write_bytes(stream['final_answer'].encode())
                row.update(answer_present=True, answer_sha256=pilot.digest(answer_path),
                           answer_file=answer_path.name)
            accounting = analyze(agent, expected_model=MODEL)
            pilot.write(destination / (profile + '-strict-accounting.json'), accounting)
            row['strict_accounting_complete'] = accounting.get('complete') is True
            row['strict_accounting_errors'] = accounting.get('errors')
            row['structural_accounting_errors'] = [e for e in accounting.get('errors', []) if e.get('code') not in USAGE_ONLY_ERRORS]
            row['strict_usage_available'] = accounting.get('complete') is True
            row['strict_usage'] = accounting['usage'] if row['strict_usage_available'] else None
            row['session_count'] = accounting.get('session_count')
            row['child_count'] = accounting.get('child_count')
            row['tool_calls'] = accounting.get('tools')
            row['metric_consistency'] = metric_consistency(row['claude_reported_model_usage'], accounting)
            trial = pilot.load(agent.parent / 'result.json')
            audit = pilot.load(agent.parent / 'qa-source-audit.json')
            trajectory = pilot.load(agent / 'trajectory.json')
            pilot.write(destination / (profile + '-source-audit.json'), audit)
            row['source_audit_valid'] = audit.get('valid') is True
            row['native_atif_sha256'] = pilot.digest(agent / 'trajectory.json')
            row['native_trial_has_exception'] = bool(trial.get('exception_info'))
            row['native_log_copy_matches'] = native_log_bytes_match(agent)
            row['container_mounts_valid'] = mounts_valid(pilot.load(args.output / (profile + '-containers.json')), slot)
            runtime_valid = True
            for phase in ('before', 'after'):
                runtime = audit.get(phase, {}).get('observed', {}).get('runtime', {})
                expected_runtime = offline_proof['verification'][0]['source_audit']['before']['observed']['runtime']['claude']
                runtime_valid &= runtime.get('claude', {}).get('binary_sha256') == expected_runtime['binary_sha256']
                if profile == 'zvec-grep':
                    payloads = runtime.get('zg', {}).get('payloads', {})
                    runtime_valid &= all(payloads.get(p.removeprefix('package/'), {}).get('sha256') == EXPECTED_MEMBERS[p][1]
                                         and payloads.get(p.removeprefix('package/'), {}).get('bytes') == EXPECTED_MEMBERS[p][0] for p in RUNTIME_PATHS)
            row['pinned_runtime_valid'] = bool(runtime_valid)
            final_steps = [s for s in trajectory.get('steps', []) if s.get('source') == 'agent' and not s.get('tool_calls')]
            row['native_atif_final_matches'] = bool(final_steps and stream['final_answer'] is not None
                and final_steps[-1].get('message', '').strip() == stream['final_answer'].strip())
            row['native_run_wall_seconds'] = audit.get('native_run_wall_seconds')
            row['source_audit_wall_seconds'] = audit.get('audit_wall_seconds')
            row['log_export_wall_seconds'] = audit.get('export_wall_seconds')
            row['harbor_timing'] = {key: timing_interval(trial.get(key))
                for key in ('environment_setup', 'agent_setup', 'agent_execution', 'verifier')}
            row['harbor_timing']['trial'] = timing_interval({k: trial.get(k) for k in ('started_at', 'finished_at')})
            setup_path = agent / 'zvec-grep-setup.json'
            setup = pilot.load(setup_path) if setup_path.is_file() else None
            row['native_zg_setup_timing'] = ({k: setup.get(k) for k in (
                'started_at', 'finished_at', 'install_duration_seconds', 'authorization_duration_seconds',
                'index_duration_seconds', 'mcp_setup_duration_seconds', 'total_duration_seconds')}
                if isinstance(setup, dict) else None)
            row['timing_scope'] = 'Native adapter run, audit/export, Harbor phase intervals and outer wall are separate recorded scopes; no subtraction or pure-model-time claim.'
            row['outer_slot_wall_seconds'] = (slot['finished_at_unix'] - slot['started_at_unix']) if all(k in slot for k in ('finished_at_unix', 'started_at_unix')) else None
            row['structural_valid'] = bool(slot.get('status') == 'exited' and slot.get('returncode') == 0
                and cleanup_complete(slot) and not trial.get('exception_info') and audit.get('valid') is True
                and runtime_valid and row['native_log_copy_matches'] and row['native_atif_final_matches']
                and row['container_mounts_valid'] and row['init_identity_valid']
                and not row['structural_accounting_errors'] and stream['root_result_success'] and not stream['errors'])
            row['reported_metric_eligible'] = bool(row['structural_valid'] and row['claude_reported_model_usage']['available']
                and not row['metric_consistency']['metric_conclusion_withheld'])
        except Exception as exc:
            row['errors'].append(type(exc).__name__)
    toolsets = [sorted(t for t in r['native_init_tools'] if not t.startswith('mcp__zvec_grep__'))
                if isinstance(r.get('native_init_tools'), list) and all(isinstance(t, str) for t in r['native_init_tools']) else None for r in rows]
    same_tools = bool(toolsets[0]) and toolsets[0] == toolsets[1]
    report = {'schema_version': 1, 'protocol_id': PROTOCOL, 'plan_sha256': pilot.digest(args.plan),
              'offline_proof_sha256': plan['offline_proof']['proof_sha256'],
              'quality_evaluated': False, 'profiles': rows,
              'same_native_tools_except_zg': same_tools,
              'structural_valid': len(rows) == 2 and same_tools and all(r['structural_valid'] for r in rows),
              'reported_metric_pair_eligible': same_tools and all(r['reported_metric_eligible'] for r in rows),
              'strict_usage_complete': all(r['strict_usage_available'] for r in rows),
              'reported_model_usage_available': all(r['claude_reported_model_usage']['available'] for r in rows)}
    report['collection_wall_seconds'] = time.monotonic() - collection_started
    report.update(execution_validity_status='pending_independent_source_scope_audit',
                  favorable_conclusion_allowed=False,
                  source_scope_audit_queue=[{'profile': r['profile'], 'status': 'pending',
                      'trial_directory': r.get('trial_directory'),
                      'required': ['all actual tool calls and returned evidence stay within source-only/no-web task',
                                   'no answer/gold/reference leakage',
                                   'successful relevant zg query uses /workspace (treatment only)']}
                      for r in rows])
    pilot.write(destination / 'collection.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'main-root', 'prepared', 'candidate', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('command', choices=('dry-run', 'run', 'collect'))
    args = parser.parse_args(argv)
    for key in ('plan', 'main_root', 'prepared', 'candidate', 'output'):
        setattr(args, key, getattr(args, key).resolve())
    plan, proof = validate_plan(args.plan, args.main_root, args.prepared, args.candidate)
    if args.command == 'collect':
        report = collect(args, plan, proof)
        return 0 if report['structural_valid'] else 1
    execution = run_pair(args, plan, dry_run=args.command == 'dry-run')
    return 0 if args.command == 'dry-run' or all(s.get('status') == 'exited' and s.get('returncode') == 0 and cleanup_complete(s) for s in execution['slots']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
