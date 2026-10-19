#!/usr/bin/env python3
"""Provisional v2 controller: execution requires a separately frozen live plan.

This file creates no plan or authorization. Its CLI refuses preparation and
execution until an immutable successful provider-offline proof is supplied.
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

import pilot
from repair_v2.gate_inputs import validate as validate_repair_inputs
from repair_v2.package import EXPECTED_MEMBERS, RUNTIME_PATHS

HERE = Path(__file__).resolve().parent
PROTOCOL = 'main-opencode-q001-v2'
PHASE = 'main-opencode-development-v2'
V1_PLAN_SHA256 = 'ce13c13785117c49c515a79cfbba13ad70eaf7113dbcb3d6e91b29ec3a8dcc43'
FIXED = {**pilot.FIXED, 'protocol_id': PROTOCOL, 'phase': PHASE}
UNCHANGED_BLOCKS = ('provider', 'embedding', 'agent', 'runtime', 'accounting',
                    'evaluation', 'isolation', 'stopping', 'main_file_sha256')


def _hash(value, label, length=64):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{%d}' % length, value) is None:
        raise ValueError('invalid ' + label)
    return value


def validate_offline_proof(plan: dict, inputs: dict, proof_dir: Path) -> dict:
    """Verify the frozen proof and every retained byte, without network access.

    The workflow/root must verify the GitHub run/head/artifact ZIP provenance
    before invoking this CLI; the plan binds those identifiers. This function
    verifies the downloaded proof's identity, contents and current input match.
    """
    binding = plan.get('offline_proof', {})
    for name in ('proof_sha256', 'inputs_sha256', 'artifact_sha256'):
        _hash(binding.get(name), 'offline_proof.' + name)
    _hash(binding.get('ci_head_sha'), 'offline_proof.ci_head_sha', 40)
    if type(binding.get('ci_run_id')) is not int or binding['ci_run_id'] <= 0:
        raise ValueError('offline proof needs a concrete CI run')
    if (binding.get('inputs_revision') != inputs['revision']
            or binding['inputs_sha256'] != pilot.digest(HERE / 'repair_v2/inputs.json')):
        raise ValueError('offline proof is stale against current repair inputs')
    proof_path = pilot.safe_file(proof_dir, 'proof.json')
    if pilot.digest(proof_path) != binding['proof_sha256']:
        raise ValueError('offline proof SHA256 mismatch')
    proof = pilot.load(proof_path)
    if (proof.get('schema_version') != 1 or proof.get('valid') is not True
            or proof.get('scope') != 'provider-offline-infrastructure-only'
            or proof.get('quality_evaluated') is not False
            or proof.get('live_provider_calls_authorized') is not False
            or proof.get('errors') != []):
        raise ValueError('successful infrastructure-only proof required')
    if (proof.get('inputs_revision') != inputs['revision']
            or proof.get('inputs_sha256') != binding['inputs_sha256']
            or proof.get('input_files') != inputs['files']
            or proof.get('candidate_sha256') != inputs['install_package_sha256']):
        raise ValueError('offline proof input/package binding mismatch')
    actual = [row for row in pilot.file_inventory(proof_dir) if row['path'] != 'proof.json']
    if not actual or actual != proof.get('evidence_files'):
        raise ValueError('offline proof retained bytes missing, changed, or augmented')
    system = proof.get('system', {})
    if system.get('harbor_version') != '0.18.0' or system.get('docker_architecture') not in ('x86_64', 'amd64'):
        raise ValueError('proof did not run the pinned Linux Harbor Docker stack')
    offline_plan = pilot.load(pilot.safe_file(proof_dir, 'offline-plan.json'))
    for name, expected in pilot.FIXED.items():
        if name not in ('protocol_id', 'phase') and offline_plan.get(name) != expected:
            raise ValueError('offline proof used a different task/native bound:' + name)
    if (offline_plan.get('main_file_sha256') != plan['main_file_sha256']
            or offline_plan.get('candidate_sha256') != plan['candidate_sha256']):
        raise ValueError('offline proof used a different main/install artifact')
    negative = proof.get('negative_proof', {})
    if (negative.get('complete') is not True or negative.get('cleanup', {}).get('complete') is not True
            or negative.get('uncertain_cleanup_policy', {}).get('next_slot_started') is not False):
        raise ValueError('Docker negative capture/timeout/cleanup proof incomplete')
    expected_cases = {'stderr-json': True, 'nonzero-json': False, 'malformed-json': False,
                      'timeout-partial': False, 'missing-child-download': False}
    cases = negative.get('cases', [])
    if len(cases) != 5 or any(type(r.get('succeeded')) is not bool for r in cases) or {r.get('label'): r.get('succeeded') for r in cases} != expected_cases:
        raise ValueError('Docker negative case results incomplete')
    execution = proof.get('execution', {})
    slots = execution.get('slots', [])
    if ([s.get('profile') for s in slots] != plan['profiles'] or execution.get('dry_run') is not False
            or any(s.get('status') != 'exited' or s.get('returncode') != 0
                   or s.get('container_cleanup', {}).get('complete') is not True for s in slots)):
        raise ValueError('offline native pair not successfully completed and cleaned up')
    native_proof = proof.get('native_proof', {})
    stub = native_proof.get('stub', {})
    if (native_proof.get('valid') is not True or stub.get('valid') is not True
            or stub.get('fake_credentials_only_at_stub') is not True
            or stub.get('real_provider_calls_authorized') is not False
            or set(native_proof.get('profiles', {})) != set(plan['profiles'])):
        raise ValueError('offline native/provider proof incomplete')
    for profile in plan['profiles']:
        row = native_proof['profiles'][profile]
        if (row.get('final_sentinel_confirmed') is not True
                or not row.get('root_session_id') or not row.get('child_session_id')
                or row['root_session_id'] == row['child_session_id']
                or row.get('llm_calls') != (5 if profile == 'baseline' else 6)
                or row.get('tool_count') != (3 if profile == 'baseline' else 4)
                or row.get('mcp_tool_count') != (0 if profile == 'baseline' else 1)):
            raise ValueError('offline root/child/MCP synthetic execution incomplete:' + profile)
    collection = pilot.load(pilot.safe_file(proof_dir, 'collection/collection.json'))
    if (collection.get('structural_valid') is not True
            or [p.get('profile') for p in collection.get('profiles', [])] != plan['profiles']
            or any(p.get('structural_valid') is not True for p in collection['profiles'])):
        raise ValueError('offline collector did not validate both profiles')
    package = pilot.load(pilot.safe_file(proof_dir, 'package-manifest.json'))
    if (package.get('upstream_archive_sha256') != inputs['original_package_sha256']
            or package.get('install_package_sha256') != inputs['install_package_sha256']
            or package.get('binary_sha256') != inputs['rust_binary_sha256']):
        raise ValueError('offline installed package identity mismatch')
    expected_payloads = {name: (EXPECTED_MEMBERS[name][0], EXPECTED_MEMBERS[name][1], f'{EXPECTED_MEMBERS[name][2]:04o}')
                         for name in RUNTIME_PATHS}
    rows = package.get('runtime_payloads', [])
    actual_payloads = {r['path']: (r['derived_bytes'], r['derived_sha256'], r['mode']) for r in rows}
    if len(rows) != 4 or actual_payloads != expected_payloads:
        raise ValueError('offline package did not bind all four unchanged runtime payloads')
    return proof


def validate_plan(plan_path: Path, repo_root: Path, main_root: Path, proof_dir: Path) -> dict:
    original_path = repo_root / 'benchmarks/opencode-qa-pilot/experiment.json'
    if pilot.digest(original_path) != V1_PLAN_SHA256:
        raise ValueError('closed v1 plan was changed')
    original = pilot.validate_plan(original_path, repo_root, main_root)
    inputs = validate_repair_inputs(repo_root, main_root)
    plan = pilot.load(plan_path)
    if plan.get('frozen') is not True:
        raise ValueError('v2 live plan must be explicitly frozen after offline proof')
    for key, value in FIXED.items():
        if plan.get(key) != value or type(plan.get(key)) is not type(value):
            raise ValueError('frozen v2 protocol mismatch:' + key)
    for key in UNCHANGED_BLOCKS:
        if plan.get(key) != original[key]:
            raise ValueError('unchanged native v1 contract differs:' + key)
    if (plan.get('candidate_sha256') != inputs['install_package_sha256']
            or plan.get('upstream_archive_sha256') != inputs['original_package_sha256']
            or plan.get('candidate', {}).get('binary_sha256') != inputs['rust_binary_sha256']
            or plan.get('candidate', {}).get('commit') != original['candidate']['commit']):
        raise ValueError('v2 must use the verified metadata-only package with unchanged Rust')
    workflow = plan.get('execution_workflow', '')
    decision = plan.get('execution_decision', '')
    if (not workflow.startswith('.github/workflows/') or not workflow.endswith(('.yml', '.yaml'))
            or workflow in ('.github/workflows/opencode-qa-pilot.yml', '.github/workflows/opencode-qa-repair-v2.yml')):
        raise ValueError('a separately named live v2 workflow must be bound')
    required = set(original['frozen_harness_sha256']) | set(inputs['files']) | {
        'benchmarks/opencode-qa-pilot/repair_v2/inputs.json',
        'benchmarks/opencode-qa-pilot/pilot_v2.py',
        'benchmarks/opencode-qa-pilot/test_pilot_v2.py', workflow, decision}
    pins = plan.get('frozen_harness_sha256', {})
    if not isinstance(pins, dict) or not required <= pins.keys() or not decision:
        raise ValueError('pin closed v1, repair inputs, live controller, workflow and decision')
    for relative, expected in pins.items():
        if pilot.digest(pilot.safe_file(repo_root, relative)) != expected:
            raise ValueError('frozen v2 harness hash mismatch:' + relative)
    record = pilot.load(pilot.safe_file(repo_root, decision))
    if (record.get('status') != 'approved_for_one_fresh_pair' or record.get('protocol_id') != PROTOCOL
            or record.get('n_attempts') != 1 or record.get('max_retries') != 0
            or record.get('offline_proof_sha256') != plan.get('offline_proof', {}).get('proof_sha256')):
        raise ValueError('separate concrete one-pair decision missing or mismatched')
    validate_offline_proof(plan, inputs, proof_dir)
    return plan


def build_command(runner, suite, profile, prepared, output, candidate, candidate_hash,
                  plan, harbor_executable):
    command = pilot.build_command(runner, suite, profile, prepared, output, candidate,
                                  candidate_hash, plan, harbor_executable)
    command[command.index('--agent') + 1] = 'repair_v2.qa_agents:' + (
        'QABaseline' if profile == 'baseline' else 'QAZvec')
    return command


def _execute_slot(main_root, prepared, output, candidate, plan, profile, harbor, dry_run):
    os.setsid()
    slot = {'profile': profile, 'status': 'running'}
    try:
        with (output / (profile + '-job.log')).open('ab', buffering=0) as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            runner = pilot.native(main_root)
            suite = runner.load_suite(prepared / 'suite.yaml')
            environment = runner.execution_environment(agent='opencode', model=plan['agent']['model'])
            environment['PYTHONPATH'] = os.pathsep.join([str(HERE), str(main_root)])
            package, package_hash = str(candidate), plan['candidate_sha256']
            if not dry_run:
                volume = runner.setup_cache_volume_name('opencode', profile,
                    zvec_grep_package=str(candidate),
                    zvec_grep_package_sha256=package_hash if profile == 'zvec-grep' else None,
                    embedding_model=plan['embedding']['model'])
                if subprocess.run(['docker', 'volume', 'inspect', volume],
                                  capture_output=True, timeout=20).returncode == 0:
                    raise ValueError('a fresh profile installation cache is required')
                cache = runner.prepare_setup_cache('opencode', profile,
                    zvec_grep_package=str(candidate), embedding_model=plan['embedding']['model'])
                if pilot.load(cache.compose_path)['volumes']['agent-setup-cache']['name'] != volume:
                    raise ValueError('unexpected native install-cache identity')
                slot['install_cache_absent_before_prepare'] = True
                slot['install_cache_volume'] = volume
                if profile == 'zvec-grep':
                    package, package_hash = cache.zvec_grep_package, cache.zvec_grep_package_sha256
                    if package_hash != plan['candidate_sha256']:
                        raise ValueError('prepared package identity mismatch')
            command = build_command(runner, suite, profile, prepared, output, package,
                                    package_hash, plan, harbor)
            slot['command'] = command
            pilot.write(output / (profile + '-slot.json'), slot)
            if dry_run:
                slot['status'] = 'dry_run'
            else:
                result = subprocess.run(command, cwd=main_root, env=environment, check=False)
                slot.update(returncode=result.returncode, status='exited')
    except Exception as exc:
        slot.update(status='failed', error_type=type(exc).__name__)
    finally:
        pilot.write(output / (profile + '-slot.json'), slot)


def _group_exists(group_id):
    try:
        os.killpg(group_id, 0)
        return True
    except ProcessLookupError:
        return False


def stop_slot_group(process, grace_seconds=5.0, kill_seconds=5.0):
    """Confirm the entire private slot group is gone, including an exited leader.

    A successful direct-child join alone is insufficient. Permission/inspection
    failure remains incomplete even if a best-effort direct-child stop succeeds.
    """
    group_id = process.pid
    result = {'complete': False, 'process_group_id': group_id, 'signals': []}
    try:
        if not group_id or group_id == os.getpgrp():
            raise ValueError('slot has no safe private process group')
        for sig, duration in ((signal.SIGTERM, grace_seconds), (signal.SIGKILL, kill_seconds)):
            if _group_exists(group_id):
                try:
                    os.killpg(group_id, sig)
                    result['signals'].append(sig.name)
                except ProcessLookupError:
                    pass
            elif process.is_alive():
                # A child interrupted before setsid must be stopped directly;
                # after it exits, check again that no group appeared meanwhile.
                process.terminate()
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                process.join(timeout=0)
                if not process.is_alive() and not _group_exists(group_id):
                    result['complete'] = True
                    return result
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        process.join(timeout=0)
        result['complete'] = not process.is_alive() and not _group_exists(group_id)
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        try:
            if process.is_alive():
                process.terminate()
                process.join(5)
        except Exception as fallback:
            result['direct_child_error_type'] = type(fallback).__name__
    return result


def execution_failed(record):
    dry_run = record.get('dry_run') is True
    return any(s.get('status') not in (('dry_run',) if dry_run else ('exited',))
               or s.get('returncode', 0) != 0
               or s.get('process_cleanup', {}).get('complete') is not True
               or (not dry_run and s.get('container_cleanup', {}).get('complete') is not True)
               for s in record['slots'])


def run_pair(plan, plan_path, main_root, prepared, output, candidate, proof_dir, dry_run=False):
    pilot.validate_prepared(prepared, plan_path)
    if candidate.is_symlink() or pilot.digest(candidate) != plan['candidate_sha256']:
        raise ValueError('v2 install candidate tgz hash mismatch')
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
    record = {'schema_version': 1, 'protocol_id': PROTOCOL, 'plan_sha256': pilot.digest(plan_path),
              'prepared': str(prepared), 'candidate_sha256': pilot.digest(candidate),
              'upstream_archive_sha256': plan['upstream_archive_sha256'],
              'offline_proof': plan['offline_proof'], 'dry_run': dry_run, 'slots': []}
    pilot.write(output / 'execution.json', record)
    for profile in plan['profiles']:
        slot = {'profile': profile, 'started_at_unix': time.time(), 'status': 'planned'}
        record['slots'].append(slot)
        pilot.write(output / 'execution.json', record)
        containers, process = {}, None
        old_handler = signal.signal(signal.SIGALRM, pilot.deadline_expired)
        try:
            process = multiprocessing.get_context('fork').Process(target=_execute_slot,
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
            process_cleanup = stop_slot_group(process) if process is not None else {'complete': True}
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
                record['slots'].append({'profile': pending, 'status': 'not_started',
                    'reason': 'previous_slot_cleanup_unconfirmed', 'returncode': 125})
            pilot.write(output / 'execution.json', record)
            break
    return record


def collect(plan, plan_path, main_root, run_dir, output, proof_dir):
    record = pilot.load(run_dir / 'execution.json')
    if (record.get('protocol_id') != PROTOCOL or record.get('offline_proof') != plan['offline_proof']
            or record.get('upstream_archive_sha256') != plan['upstream_archive_sha256']):
        raise ValueError('v2 run identity or offline proof reference missing')
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
    summary['offline_proof'] = plan['offline_proof']
    pilot.write(output / 'collection.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'repo-root', 'main-root', 'proof-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('validate')
    prepare = sub.add_parser('prepare')
    prepare.add_argument('--zvec-repo', type=Path, required=True)
    prepare.add_argument('--output', type=Path, required=True)
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
    plan = validate_plan(args.plan, args.repo_root, args.main_root, args.proof_dir)
    if args.command == 'prepare':
        pilot.prepare(plan, args.plan, args.repo_root, args.main_root, args.zvec_repo, args.output)
    elif args.command == 'run':
        result = run_pair(plan, args.plan, args.main_root, args.prepared, args.output,
                          args.candidate_tgz, args.proof_dir, args.dry_run)
        return int(execution_failed(result))
    elif args.command == 'collect':
        result = collect(plan, args.plan, args.main_root, args.run_dir, args.output, args.proof_dir)
        return int(not result['structural_valid'])
    else:
        import json
        print(json.dumps({'valid': True, 'protocol_id': PROTOCOL,
                          'plan_sha256': pilot.digest(args.plan), 'offline_proof': plan['offline_proof']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
