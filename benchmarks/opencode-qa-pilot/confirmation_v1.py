#!/usr/bin/env python3
"""Two prospectively frozen q001 confirmation pairs, one repeat per fresh runner.

This controller creates no plan or authorization. Validation and tests are
provider-free; execution requires the bound parent plan, proof and new decision.
"""
from __future__ import annotations

import argparse
import multiprocessing
from pathlib import Path
import signal
import sys
import time

import pilot
import pilot_v2 as v2
from repair_v2.package import EXPECTED_MEMBERS, RUNTIME_PATHS

PROTOCOL = 'main-opencode-q001-confirmation-v1'
PHASE = 'main-opencode-confirmation-v1'
REPEATS = ['c01', 'c02']
PARENT_PLAN = 'benchmarks/opencode-qa-pilot/experiment-v2.json'
PARENT_PLAN_SHA256 = 'dd48a9bcb813ca2ef679971419c7f02726e008791350202ed1c53c7728ddff60'
FIXED = {**pilot.FIXED, 'protocol_id': PROTOCOL, 'phase': PHASE, 'repeats': REPEATS}
UNCHANGED_BLOCKS = tuple(k for k in v2.UNCHANGED_BLOCKS if k != 'stopping') + (
    'candidate', 'candidate_sha256', 'upstream_archive_sha256', 'offline_proof',
    'source_repository', 'upstream_repository')
CONFIRMATION = {
    'schedule': [{'repeat': r, 'profiles': ['baseline', 'zvec-grep'],
                  'n_attempts': 1, 'max_retries': 0} for r in REPEATS],
    'runner': 'fresh_github_linux_runner_per_repeat',
    'maximum_parallel_pairs': 1,
    'fail_fast': False,
    'success_rule': 'both_repeats_independently_meet_every_pair_condition',
    'pair_conditions': [
        'both_protocol_valid_with_complete_captured_session_trees',
        'both_pass_unchanged_original_quality_success_gate',
        'zg_factual_points_greater_than_or_equal_to_baseline',
        'zg_input_including_cache_strictly_lower_than_baseline',
        'at_least_one_actual_successful_relevant_correct_workspace_zg_MCP_retrieval'],
    'accounting_scope': 'opencode-session-tree-v1',
    'development_pair_pooled': False,
    'whole_provider_savings_claim': False,
}
STOPPING = {
    'no_automatic_repeats': True,
    'no_replacement': True,
    'no_expansion': True,
    'on_failure': 'Retain every failed, partial or skipped scheduled slot; no repair, retry, replacement or resampling in this phase.',
    'scheduled_slot_policy': 'Within each repeat attempt baseline then zg at most once, even if baseline fails. If prior process or container cleanup is unconfirmed, retain the later slot as not_started and abort that pair. Each repeat uses a separate fresh runner.',
    'after_scheduled_pairs': 'Close after c01 and c02. Confirm the scoped-input candidate only if BOTH pairs independently satisfy every frozen pair condition. No pooling with development and no substitution of a secondary metric.',
}

# Native command construction and slot execution remain in immutable v2.
build_command = v2.build_command


def validate_repeat(repeat):
    if repeat not in REPEATS or not isinstance(repeat, str):
        raise ValueError('repeat must be exactly c01 or c02')


def validate_plan(plan_path, repo_root, main_root, proof_dir, repeat):
    parent_path = pilot.safe_file(repo_root, PARENT_PLAN)
    if pilot.digest(parent_path) != PARENT_PLAN_SHA256:
        raise ValueError('frozen parent development plan changed')
    # Validate all original assets, native controls and accepted offline proof
    # before considering any new confirmation identity or execution request.
    parent = v2.validate_plan(parent_path, repo_root, main_root, proof_dir)
    validate_repeat(repeat)
    plan = pilot.load(plan_path)
    if plan.get('frozen') is not True:
        raise ValueError('confirmation plan must be explicitly frozen')
    for key, expected in FIXED.items():
        if plan.get(key) != expected or type(plan.get(key)) is not type(expected):
            raise ValueError('frozen confirmation identity mismatch:' + key)
    for key in UNCHANGED_BLOCKS:
        if plan.get(key) != parent[key]:
            raise ValueError('unchanged parent contract differs:' + key)
    if (plan.get('parent_development_plan') != PARENT_PLAN
            or plan.get('parent_development_plan_sha256') != PARENT_PLAN_SHA256
            or plan.get('parent_development_stopping') != parent['stopping']):
        raise ValueError('parent development identity or stopping contract differs')
    if plan.get('confirmation') != CONFIRMATION or plan.get('stopping') != STOPPING:
        raise ValueError('prospective confirmation schedule, success or stopping rules differ')
    workflow, decision = plan.get('execution_workflow', ''), plan.get('execution_decision', '')
    if (not workflow.startswith('.github/workflows/') or not workflow.endswith(('.yml', '.yaml'))
            or workflow in parent['frozen_harness_sha256'] or not decision
            or decision in parent['frozen_harness_sha256']):
        raise ValueError('confirmation needs a distinct workflow and decision')
    required = set(parent['frozen_harness_sha256']) | {PARENT_PLAN, workflow, decision,
        'benchmarks/opencode-qa-pilot/confirmation_v1.py',
        'benchmarks/opencode-qa-pilot/test_confirmation_v1.py'}
    pins = plan.get('frozen_harness_sha256', {})
    if not isinstance(pins, dict) or not required <= pins.keys():
        raise ValueError('pin parent inputs, confirmation controller, tests, workflow and decision')
    for relative, expected in pins.items():
        if pilot.digest(pilot.safe_file(repo_root, relative)) != expected:
            raise ValueError('frozen confirmation harness hash mismatch:' + relative)
    record = pilot.load(pilot.safe_file(repo_root, decision))
    if (record.get('status') != 'approved_for_two_confirmation_pairs'
            or record.get('protocol_id') != PROTOCOL or record.get('repeats') != REPEATS
            or record.get('n_attempts') != 1 or record.get('max_retries') != 0
            or record.get('parent_development_plan_sha256') != PARENT_PLAN_SHA256
            or record.get('development_scoped_input_candidate_accepted') is not True
            or record.get('offline_proof_sha256') != plan['offline_proof']['proof_sha256']):
        raise ValueError('separate accepted-candidate confirmation decision missing or mismatched')
    return plan


def provenance(plan, plan_path, repeat):
    validate_repeat(repeat)
    return {'protocol_id': PROTOCOL, 'phase': PHASE, 'repeat': repeat,
            'plan_sha256': pilot.digest(plan_path),
            'parent_development_plan_sha256': plan['parent_development_plan_sha256']}


def prepare(plan, plan_path, repo_root, main_root, zvec_repo, output, repeat):
    identity = provenance(plan, plan_path, repeat)
    pilot.prepare(plan, plan_path, repo_root, main_root, zvec_repo, output)
    metadata = pilot.load(output / 'prepared.json')
    metadata.update(identity)
    pilot.write(output / 'prepared.json', metadata)
    return metadata


def validate_prepared(prepared, plan, plan_path, repeat):
    metadata = pilot.validate_prepared(prepared, plan_path)
    if any(metadata.get(key) != value for key, value in provenance(plan, plan_path, repeat).items()):
        raise ValueError('prepared confirmation repeat identity differs')
    return metadata


def run_pair(plan, plan_path, main_root, prepared, output, candidate, proof_dir, repeat, dry_run=False):
    validate_prepared(prepared, plan, plan_path, repeat)
    if candidate.is_symlink() or pilot.digest(candidate) != plan['candidate_sha256']:
        raise ValueError('confirmation install candidate tgz hash mismatch')
    # Same source-manifest bytes as the successful offline image; absolute paths
    # and the new prepared plan hash do not alter this exporter-produced report.
    proof = pilot.load(proof_dir / 'proof.json')
    if pilot.digest(prepared / 'source-manifest.json') != proof['source_manifest_sha256']:
        raise ValueError('new prepared source manifest differs from offline proof')
    if not dry_run:
        pilot.validate_runtime_inputs(plan, main_root)  # Validate BOTH credentials before baseline.
    if output.exists():
        raise ValueError('run output must not exist; reruns/replacements forbidden')
    output.mkdir(parents=True)
    harbor = Path(sys.executable).parent / 'harbor'
    if not harbor.is_file():
        raise ValueError('use the exact-main locked Harbor Python')
    record = {'schema_version': 1, **provenance(plan, plan_path, repeat),
              'prepared': str(prepared), 'candidate_sha256': pilot.digest(candidate),
              'upstream_archive_sha256': plan['upstream_archive_sha256'],
              'offline_proof': plan['offline_proof'], 'dry_run': dry_run, 'slots': []}
    pilot.write(output / 'execution.json', record)
    for profile in plan['profiles']:
        slot = {**provenance(plan, plan_path, repeat), 'profile': profile, 'started_at_unix': time.time(), 'status': 'planned'}
        record['slots'].append(slot)
        pilot.write(output / 'execution.json', record)
        containers, process = {}, None
        old_handler = signal.signal(signal.SIGALRM, pilot.deadline_expired)
        try:
            process = multiprocessing.get_context('fork').Process(target=v2._execute_slot,
                args=(main_root, prepared, output, candidate, plan, profile, harbor, dry_run))
            process.start()
            # Covers native cache preparation as well as every Docker/agent call.
            signal.setitimer(signal.ITIMER_REAL, plan['slot_timeout_sec'])
            deadline = time.monotonic() + plan['slot_timeout_sec']
            while process.is_alive() and time.monotonic() < deadline:
                process.join(min(1, max(0, deadline - time.monotonic())))
                if not dry_run:
                    pilot.capture_slot_containers(output, profile, containers)
                    pilot.write(output / (profile + '-containers.json'), containers)
            if process.is_alive():
                slot.update(status='timeout', returncode=124)
            else:
                slot.update(pilot.load(output / (profile + '-slot.json')))
        except TimeoutError:
            slot.update(status='timeout', returncode=124)
        except Exception as exc:
            slot.update(status='failed', error_type=type(exc).__name__)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            process_cleanup = v2.stop_slot_group(process) if process is not None else {'complete': True}
            slot['process_cleanup'] = process_cleanup
            if not dry_run:
                try:
                    slot['container_cleanup'] = pilot.cleanup_slot_containers(output, profile, containers)
                except Exception as exc:
                    slot['container_cleanup'] = {'complete': False, 'error_type': type(exc).__name__}
                if process_cleanup['complete'] is not True:
                    slot['container_cleanup']['complete'] = False
                    slot['container_cleanup']['reason'] = 'slot_process_cleanup_unconfirmed'
                pilot.write(output / (profile + '-containers.json'), containers)
            slot['finished_at_unix'] = time.time()
            pilot.write(output / 'execution.json', record)
        if not dry_run and slot.get('container_cleanup', {}).get('complete') is not True:
            for pending in plan['profiles'][len(record['slots']):]:
                record['slots'].append({**provenance(plan, plan_path, repeat), 'profile': pending, 'status': 'not_started',
                    'reason': 'previous_slot_cleanup_unconfirmed', 'returncode': 125})
            pilot.write(output / 'execution.json', record)
            break
    return record


def collect(plan, plan_path, main_root, run_dir, output, proof_dir, repeat):
    record = pilot.load(run_dir / 'execution.json')
    identity = provenance(plan, plan_path, repeat)
    if (any(record.get(k) != value for k, value in identity.items())
            or record.get('candidate_sha256') != plan['candidate_sha256'] or record.get('offline_proof') != plan['offline_proof']
            or record.get('upstream_archive_sha256') != plan['upstream_archive_sha256']):
        raise ValueError('confirmation run/repeat identity or offline proof reference missing')
    # Preserve original source/session/final-answer collector; QA validity remains
    # pending source-only auditing and the unchanged blinded rubric after this.
    summary = pilot.collect(plan, plan_path, main_root, run_dir, output)
    proof_collection = pilot.load(proof_dir / 'collection/collection.json')
    expected_opencode = {p['profile']: p['runtime']['opencode']['binary_sha256'] for p in proof_collection['profiles']}
    expected_payloads = {name.removeprefix('package/'): {'bytes': EXPECTED_MEMBERS[name][0],
        'sha256': EXPECTED_MEMBERS[name][1], 'mode': f'{EXPECTED_MEMBERS[name][2]:04o}'} for name in RUNTIME_PATHS}
    for report in summary['profiles']:
        profile = report['profile']
        slot = next((s for s in record['slots'] if s.get('profile') == profile), {})
        if slot.get('install_cache_absent_before_prepare') is not True:
            report['errors'].append('fresh_native_install_cache_not_confirmed')
        if report.get('structural_valid'):
            runtime = report['runtime']
            if runtime['opencode']['binary_sha256'] != expected_opencode[profile]:
                report['errors'].append('OpenCode_binary_differs_from_successful_offline_proof')
            if profile == 'zvec-grep' and runtime.get('zg', {}).get('payloads') != expected_payloads:
                report['errors'].append('installed_zg_runtime_payloads_differ_from_offline_proof')
        if report['errors']:
            report['structural_valid'] = False
    summary['structural_valid'] = summary['structural_valid'] and all(p['structural_valid'] for p in summary['profiles'])
    summary.update(identity)
    for report in summary['profiles']:
        report.update(identity)
    summary['offline_proof'] = plan['offline_proof']
    pilot.write(output / 'collection.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'repo-root', 'main-root', 'proof-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--repeat', choices=REPEATS, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('validate')
    preparation = sub.add_parser('prepare')
    preparation.add_argument('--zvec-repo', type=Path, required=True)
    preparation.add_argument('--output', type=Path, required=True)
    run = sub.add_parser('run')
    for name in ('prepared', 'output', 'candidate-tgz'):
        run.add_argument('--' + name, type=Path, required=True)
    run.add_argument('--dry-run', action='store_true')
    collection = sub.add_parser('collect')
    collection.add_argument('--run-dir', type=Path, required=True)
    collection.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    plan = validate_plan(args.plan, args.repo_root, args.main_root, args.proof_dir, args.repeat)
    if args.command == 'prepare':
        prepare(plan, args.plan, args.repo_root, args.main_root, args.zvec_repo, args.output, args.repeat)
    elif args.command == 'run':
        result = run_pair(plan, args.plan, args.main_root, args.prepared, args.output,
                          args.candidate_tgz, args.proof_dir, args.repeat, args.dry_run)
        return int(v2.execution_failed(result))
    elif args.command == 'collect':
        result = collect(plan, args.plan, args.main_root, args.run_dir, args.output, args.proof_dir, args.repeat)
        return int(not result['structural_valid'])
    else:
        import json
        print(json.dumps({'valid': True, **provenance(plan, args.plan, args.repeat), 'offline_proof': plan['offline_proof']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
