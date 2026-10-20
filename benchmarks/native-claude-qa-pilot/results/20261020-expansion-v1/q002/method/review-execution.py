#!/usr/bin/env python3
"""Read-only execution audit for the two fixed reserve tasks in run 39353782766.

Run only after the selected artifact has been independently downloaded and
verified. No provider, Docker, CI, collector execution, quality or scope grading.
Writes new host audit files exclusively; never rewrites the retained artifact.
"""
from pathlib import Path, PurePosixPath
import argparse
import datetime
import hashlib
import json
import math
import re
import subprocess
import sys
import tarfile
import zipfile

B = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
H = W / 'benchmarks/native-claude-qa-pilot'
GROUP = B / 'native-expansion-prepared-v1'
REFERENCE = B / 'native-claude-live-preflight-v1/prepared'
RUN = 39353782766
HEAD = 'bbc15485ca317427f6b19cab4dc449c50d3ef731'
PLAN_SHA = '275deb11a9212d7fcc5cb849b2a478d3fda82553a1075f496b7d038f613a1067'
PROTOCOL = 'native-claude-reserves-expansion-v1'
UPSTREAM = 'a424e0ce06868dc9ad8f142f57de59b8f9b58699'
MODEL = 'qwen3.8-max'
ORDERS = {'q002': ['baseline', 'zvec-grep'], 'q003': ['zvec-grep', 'baseline']}
PREVIOUS = B / 'native-claude-confirmation-v1-audit/review-c02.py'
PREVIOUS_SHA = '4d5f3570971d441d2fdced9e8d60aad9cbddf44360ca4371116432076ece09e1'
FIELDS = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')
USAGE_ONLY = {'missing_usage', 'invalid_usage_fields', 'invalid_usage_update', 'usage_regression'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hashed(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(), parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))


def rows(path):
    return [(i, json.loads(line)) for i, line in enumerate(Path(path).read_text().splitlines(), 1) if line.strip()]


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def relative(value):
    p = PurePosixPath(value)
    return bool(value) and not p.is_absolute() and '..' not in p.parts and str(p) == value


def options(command, flag):
    return [command[i + 1] if i + 1 < len(command) else None for i, token in enumerate(command) if token == flag]


def gitbytes(path):
    return subprocess.check_output(['git', 'show', HEAD + ':' + path], cwd=W, timeout=30)


def exclusive(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write('\n')


def independent_messages(agent, accounting):
    """Independently bind all persisted terminal messages and actual tool pairs.

    Event duplicates are skipped; streamed partial usage, result.modelUsage and
    Agent/Task result metadata are never added to the persisted sum.
    """
    messages, calls, results, session_rows = {}, {}, {}, {}
    seen, high_water, regressions, conflicts = set(), {}, [], []
    persisted = sorted((agent / 'sessions/projects').rglob('*.jsonl'))
    for path in persisted:
        rel = path.relative_to(agent).as_posix()
        child = re.fullmatch(r'agent-(.+)\.jsonl', path.name) if 'subagents' in path.parts else None
        for line, event in rows(path):
            if event.get('type') not in ('assistant', 'user'):
                continue
            aid = event.get('agentId') or (child[1] if child else None)
            session = 'agent:' + aid if isinstance(aid, str) and aid else 'root'
            signature = (session, hashed(event))
            if signature in seen:
                continue
            seen.add(signature)
            loc = {'file': rel, 'line': line}
            session_rows.setdefault(session, []).append((loc, event))
            message = event.get('message', {})
            content = message.get('content', [])
            if event['type'] == 'assistant':
                key = (session, message.get('id'))
                usage = message.get('usage')
                water = high_water.setdefault(key, {})
                if isinstance(usage, dict):
                    for field in FIELDS:
                        if field in usage and not number(usage[field]):
                            regressions.append({'kind': 'invalid_update', 'session': session, 'message_id': key[1], 'field': field, **loc})
                        if number(usage.get(field)):
                            if field in water and usage[field] < water[field]:
                                regressions.append({'kind': 'regression', 'session': session, 'message_id': key[1], 'field': field, **loc})
                            water[field] = max(usage[field], water.get(field, usage[field]))
                valid = isinstance(usage, dict) and all(number(usage.get(f)) for f in FIELDS)
                messages[key] = {'session': session, 'message_id': key[1], 'model': message.get('model'),
                                 'usage': {f: usage[f] for f in FIELDS} if valid else usage,
                                 'usage_valid': valid, 'terminal_location': loc}
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get('type') == 'tool_use':
                        k = (session, block['id'])
                        value = {'id': block['id'], 'session': session, 'message_id': key[1],
                                 'name': block['name'], 'input_sha256': hashed(block['input'])}
                        if k in calls and calls[k] != value:
                            conflicts.append({'kind': 'tool_call', 'session': session, 'id': k[1], **loc})
                        calls[k] = value
            else:
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get('type') == 'tool_result':
                        k = (session, block['tool_use_id'])
                        value = {'block': block, 'metadata': event.get('toolUseResult', event.get('tool_use_result', {}))}
                        if k in results and results[k] != value:
                            conflicts.append({'kind': 'tool_result', 'session': session, 'id': k[1], **loc})
                        results[k] = value
    expected_messages = {(m['session'], m['message_id']): m for m in accounting['messages']}
    message_match = set(messages) == set(expected_messages) and all(
        all(m[k] == expected_messages[key][k] for k in ('model', 'usage', 'usage_valid'))
        and m['terminal_location'] == expected_messages[key]['source_locations'][-1]
        for key, m in messages.items())
    expected_tools = {(t['session'], t['id']): t for t in accounting['tools']}
    tool_match = not conflicts and set(calls) == set(results) == set(expected_tools)
    for key, call in calls.items():
        result = results.get(key, {}).get('block', {})
        actual = dict(call, completed=key in results, is_error=result.get('is_error') is True,
                      result_sha256=hashed(result) if key in results else None)
        tool_match &= actual == expected_tools.get(key)
    valid = all(m['usage_valid'] for m in messages.values()) and bool(messages)
    totals = None
    if valid:
        sums = {f: sum(m['usage'][f] for m in messages.values()) for f in FIELDS}
        totals = {'uncached_input_tokens': sums['input_tokens'], 'cache_read_tokens': sums['cache_read_input_tokens'],
                  'cache_write_tokens': sums['cache_creation_input_tokens'], 'output_tokens': sums['output_tokens'],
                  'input_tokens': sums['input_tokens'] + sums['cache_read_input_tokens'] + sums['cache_creation_input_tokens']}
    session_map = {s['id']: s for s in accounting['sessions']}
    sessions_match = set(session_rows) == set(session_map) and all(s['reachable'] is True for s in session_map.values())
    children = [s for s in session_map.values() if s['id'] != 'root']
    for child in children:
        key = (child['parent_session'], child['parent_tool_use_id'])
        metadata = results.get(key, {}).get('metadata', {})
        sessions_match &= (key in calls and calls[key]['name'].lower() in ('agent', 'task')
                           and metadata.get('agentId') == child['agent_id'])
    return {'message_match': message_match, 'tool_match': tool_match, 'sessions_match': sessions_match,
            'messages': list(messages.values()), 'usage': totals, 'all_terminal_fields_available': valid,
            'usage_update_issues': regressions, 'tool_conflicts': conflicts, 'session_rows': session_rows,
            'session_count': len(session_rows), 'child_count': len(children),
            'tool_count': len(calls), 'persisted_files': persisted}


class Audit:
    def __init__(self, task):
        self.task = task
        self.c = B / 'native-expansion-v1-ci' / task
        self.raw = self.c / 'raw'
        self.live = self.raw / 'live'
        self.out = Path(__file__).resolve().parent
        self.checks, self.catalog, self.profiles, self.inits = [], {}, [], {}

    def ref(self, path):
        path = Path(path)
        key = str(path)
        if key not in self.catalog:
            self.catalog[key] = ({'path': key, 'bytes': path.stat().st_size, 'sha256': sha(path)}
                                 if path.is_file() and not path.is_symlink() else {'path': key, 'missing_or_unsafe': True})
        return key

    def check(self, name, passed, paths=(), *, category='execution', **observed):
        self.checks.append({'check': name, 'passed': bool(passed), 'category': category,
                            'evidence': [self.ref(p) for p in paths], 'observed': observed})
        return bool(passed)

    def verify_origin(self):
        # Until verified metadata AND original ZIP exist, do not emit any audit report.
        for name in ('artifact.zip', 'artifact-byte-verification.json', 'artifact-metadata.json',
                     'run-metadata.json', 'job-metadata.json'):
            if not (self.c / name).is_file() or (self.c / name).is_symlink():
                raise ValueError('verified_artifact_not_ready:' + name)
        receipt = load(self.c / 'artifact-byte-verification.json')
        if receipt.get('complete') is not True or receipt.get('exact_extracted_inventory') is not True:
            raise ValueError('verified_artifact_receipt_not_complete')
        if not self.raw.is_dir() or any(p.is_symlink() for p in self.raw.rglob('*')):
            raise ValueError('raw_missing_or_symlink')
        run = load(self.c / 'run-metadata.json')
        jobs = load(self.c / 'job-metadata.json')['jobs']
        matches = [j for j in jobs if j.get('name') == 'expansion (' + self.task + ')']
        job = matches[0] if len(matches) == 1 else {}
        meta = load(self.c / 'artifact-metadata.json')
        zp = self.c / 'artifact.zip'
        zsha = sha(zp)
        identity = (run.get('id') == RUN and run.get('run_attempt') == 1 and run.get('head_sha') == HEAD
                    and run.get('event') == 'workflow_dispatch' and run['repository']['full_name'] == 'Cuiyus/zvec-grep'
                    and job.get('run_id') == RUN and job.get('head_sha') == HEAD and job.get('run_attempt') == 1
                    and job.get('status') == 'completed'
                    and receipt.get('run_id') == RUN and receipt.get('head_sha') == HEAD and receipt.get('run_attempt') == 1
                    and receipt.get('repository') == 'Cuiyus/zvec-grep' and receipt.get('event', 'workflow_dispatch') == 'workflow_dispatch'
                    and receipt.get('task') == self.task and receipt.get('job_id') == job.get('id')
                    and receipt.get('artifact_id') == meta.get('id')
                    and meta.get('name') == f'native-claude-expansion-v1-{RUN}-{self.task}'
                    and meta['workflow_run']['id'] == RUN and meta['workflow_run']['head_sha'] == HEAD
                    and receipt.get('artifact_sha256') == zsha and meta.get('digest') == 'sha256:' + zsha
                    and meta.get('size_in_bytes') == receipt.get('artifact_bytes') == zp.stat().st_size
                    and receipt.get('matches_api_digest') is True)
        self.check('immutable_run_task_artifact_origin', identity,
                   [self.c / n for n in ('run-metadata.json', 'job-metadata.json', 'artifact-metadata.json', 'artifact-byte-verification.json', 'artifact.zip')],
                   category='binding', job_id=job.get('id'), job_conclusion=job.get('conclusion'),
                   global_status_at_collection=run.get('status'), global_conclusion_at_collection=run.get('conclusion'),
                   artifact_id=meta.get('id'), artifact_sha256=zsha)
        if not identity:
            raise ValueError('artifact_origin_mismatch')
        if 'artifact_metadata_sha256' in receipt:
            self.check('verification_binds_artifact_metadata', receipt['artifact_metadata_sha256'] == sha(self.c / 'artifact-metadata.json'),
                       [self.c / 'artifact-byte-verification.json', self.c / 'artifact-metadata.json'], category='binding')
        with zipfile.ZipFile(zp) as archive:
            names = [x.filename for x in archive.infolist() if not x.is_dir()]
            actual = sorted(p.relative_to(self.raw).as_posix() for p in self.raw.rglob('*') if p.is_file())
            safe = all(relative(n) for n in names) and len(set(names)) == len(names)
            bad = [n for n in names if safe and (not (self.raw / n).is_file() or archive.read(n) != (self.raw / n).read_bytes())]
            inventory = {r['path']: r for r in receipt['files']}
            receipt_ok = len(inventory) == len(receipt['files']) and sorted(inventory) == actual and all(
                r['bytes'] == (self.raw / n).stat().st_size and r['sha256'] == sha(self.raw / n) and r.get('matches_zip') is True
                for n, r in inventory.items())
            self.check('all_raw_files_equal_original_zip_and_verified_inventory', safe and archive.testzip() is None
                       and sorted(names) == actual and not bad and receipt_ok,
                       [zp, self.c / 'artifact-byte-verification.json'], category='binding', files=len(actual), mismatches=bad)
        peers = [j for j in jobs if j.get('name') in ('expansion (q002)', 'expansion (q003)') and j.get('runner_id')]
        self.check('recorded_fresh_hosted_runner', job.get('labels') == ['ubuntu-24.04']
                   and str(job.get('runner_name', '')).startswith('GitHub Actions ')
                   and len({j['runner_id'] for j in peers}) == len(peers), [self.c / 'job-metadata.json'],
                   visible_runner_ids={j['name']: j['runner_id'] for j in peers},
                   peer_runner_not_yet_assigned=len(peers) < 2,
                   limitation='Per-task record; verify distinct runner IDs across both final artifacts when both are available.')
        self.origin = {'run_id': RUN, 'head_sha': HEAD, 'run_attempt': 1, 'task_id': self.task,
                       'artifact_id': meta['id'], 'artifact_sha256': zsha, 'job_id': job['id'],
                       'job_conclusion': job.get('conclusion'), 'global_run_status_at_collection': run.get('status')}

    def prepare(self):
        r = self.raw
        plan, base, inputs = [load(r / n) for n in ('expansion-plan-v1.json', 'live-plan-v2.json', 'inputs.json')]
        self.plan, self.base = plan, base
        self.check('exact_frozen_expansion_plan', sha(r / 'expansion-plan-v1.json') == PLAN_SHA
                   and gitbytes('benchmarks/native-claude-qa-pilot/expansion-plan-v1.json') == (r / 'expansion-plan-v1.json').read_bytes()
                   and plan['frozen'] is True and plan['protocol_id'] == PROTOCOL,
                   [r / 'expansion-plan-v1.json'], category='binding')
        self.check('exact_base_plan', sha(r / 'live-plan-v2.json') == plan['base_plan']['sha256'],
                   [r / 'live-plan-v2.json', r / 'expansion-plan-v1.json'], category='binding')
        raw_inputs_ok = (sha(r / 'inputs.json') == base['offline_proof']['inputs_sha256']
                         and (r / 'inputs.json').read_bytes() == gitbytes('benchmarks/native-claude-qa-pilot/inputs.json')
                         and inputs['revision'] == base['offline_proof']['inputs_revision'])
        self.check('retained_native_inputs_exact_frozen_reference', raw_inputs_ok,
                   [r / 'inputs.json', r / 'live-plan-v2.json'], category='binding')
        if sha(r / 'live-plan-v2.json') != plan['base_plan']['sha256'] or not raw_inputs_ok:
            raise ValueError('base_plan_or_native_inputs_mismatch')
        allpins = {**inputs['repository_files'], **base['frozen_harness_sha256'], **plan['frozen_harness_sha256']}
        bad = [p for p, digest in allpins.items() if hashlib.sha256(gitbytes(p)).hexdigest() != digest]
        self.check('repository_pins_at_execution_head', not bad, [r / n for n in ('inputs.json', 'live-plan-v2.json', 'expansion-plan-v1.json')],
                   category='binding', checked_files=len(allpins), mismatches=bad)
        # Check imported code against the execution commit before any replay import.
        imported = [p for p in allpins if p.endswith('.py')]
        local_bad = [p for p in imported if not (W / p).is_file() or sha(W / p) != allpins[p]]
        self.check('local_readonly_replay_code_matches_frozen_pins', not local_bad, [W / p for p in imported], category='binding', mismatches=local_bad)
        if bad or local_bad or sha(r / 'expansion-plan-v1.json') != PLAN_SHA:
            raise ValueError('frozen_code_or_plan_mismatch')
        sys.path[:0] = [str(H), str(W / 'benchmarks/opencode-qa-pilot'), str(B / 'upstream-main/benchmarks/swe-qa-bench')]
        import accounting_v2
        import expansion_identity_v1
        import live
        self.accounting, self.identity, self.collector = accounting_v2, expansion_identity_v1, live
        self.proof = live.validate_offline(base, W)
        self.check('accepted_offline_runtime_proof_revalidated', True,
                   [W / base['offline_proof']['directory'] / 'proof.json', W / base['offline_proof']['review_path'], r / 'live-plan-v2.json'], category='binding')
        selected = GROUP / self.task
        metadata = load(selected / 'prepared.json')
        manifest = load(selected / 'source-manifest.json')
        self.metadata, self.manifest = metadata, manifest
        self.source = selected / 'tasks' / ('zvec-' + self.task) / 'environment/source'
        self.canonical = {x['path']: x for x in manifest['files']}
        self.question_path = W / 'benchmarks/zvec-issue-qa' / metadata['question']['path']
        self.question = self.question_path.read_text()
        self.check('selected_task_metadata_group_source_and_question',
                   sha(GROUP / 'group.json') == sha(r / 'prepared-group.json') == plan['prepared_group']['group_sha256']
                   and sha(selected / 'prepared.json') == sha(r / (self.task + '-prepared.json')) == plan['prepared_group']['metadata_sha256'][self.task]
                   and sha(selected / 'source-manifest.json') == sha(r / (self.task + '-source-manifest.json')) == metadata['source_manifest_sha256']
                   and metadata['task_id'] == self.task and metadata['scheduled_task_ids'] == ['q002', 'q003']
                   and metadata['dataset'] == plan['dataset'] and sha(W / plan['dataset']['path']) == plan['dataset']['sha256']
                   and sha(self.question_path) == metadata['question']['sha256']
                   and manifest['source_commit'] == metadata['source_commit']
                   and manifest['inventory_sha256'] == metadata['source_inventory_sha256']
                   and len(self.canonical) == len(manifest['files']) == metadata['source_file_count']
                   and all((self.source / n).stat().st_size == v['bytes'] and sha(self.source / n) == v['sha256'] for n, v in self.canonical.items()),
                   [GROUP / 'group.json', selected / 'prepared.json', selected / 'source-manifest.json', self.question_path,
                    r / 'prepared-group.json', r / (self.task + '-prepared.json'), r / (self.task + '-source-manifest.json'), W / plan['dataset']['path']],
                   category='binding', source_commit=metadata['source_commit'], source_file_count=len(self.canonical), source_root=str(self.source))
        self.ctx = load(r / 'live-expansion-context.json')
        dryctx = load(r / 'dry-run-expansion-context.json')
        self.pre = load(r / 'preflight.json')
        self.execution = load(self.live / 'execution.json')
        self.collection = load(self.live / 'collection/collection.json')
        remote = self.ctx.get('recorded_prepared_root')
        remote_path = PurePosixPath(remote)
        context = {'schema_version': 1, 'protocol_id': PROTOCOL, 'task_id': self.task,
                   'profiles': ORDERS[self.task], 'plan_sha256': PLAN_SHA, 'base_plan_sha256': plan['base_plan']['sha256'],
                   'dataset_sha256': plan['dataset']['sha256'], 'prepared_group_sha256': plan['prepared_group']['group_sha256'],
                   'prepared_metadata_sha256': plan['prepared_group']['metadata_sha256'][self.task],
                   'offline_proof_sha256': base['offline_proof']['proof_sha256'], 'candidate_sha256': base['candidate_sha256'],
                   'recorded_prepared_root': remote, 'dry_run': False}
        self.check('exclusive_task_context_and_run_provenance', self.ctx == context and dryctx == dict(context, dry_run=True)
                   and remote_path.is_absolute() and '..' not in remote_path.parts and str(remote_path) == remote
                   and all(d['protocol_id'] == PROTOCOL and d['plan_sha256'] == PLAN_SHA
                           and d['offline_proof_sha256'] == context['offline_proof_sha256'] for d in [self.pre, self.execution, self.collection])
                   and all(d['offline_inputs_sha256'] == base['offline_proof']['inputs_sha256'] for d in [self.pre, self.execution])
                   and self.pre['dry_run'] is True and self.execution['dry_run'] is False,
                   [r / 'live-expansion-context.json', r / 'dry-run-expansion-context.json', r / 'preflight.json', self.live / 'execution.json', self.live / 'collection/collection.json'], category='binding')
        self.check('scheduled_task_and_profile_order_one_attempt_no_retry', plan['tasks'] == [{'id': k, 'profiles': v} for k, v in ORDERS.items()]
                   and plan['n_attempts'] == 1 and plan['max_retries'] == 0
                   and [x['profile'] for x in self.execution['slots']] == ORDERS[self.task]
                   and [x['profile'] for x in self.collection['profiles']] == ORDERS[self.task],
                   [r / 'expansion-plan-v1.json', self.live / 'execution.json', self.live / 'collection/collection.json'])
        replay = expansion_identity_v1.verify(GROUP, self.task, self.live, reference_prepared=REFERENCE,
                   recorded_prepared_root=remote, expected_profiles=ORDERS[self.task], repo_root=W)
        replay_path = self.out / (self.task + '-task-identity-replay.json')
        exclusive(replay_path, replay)
        saved = load(self.live / 'task-identity.json')
        expected_saved = dict(replay, native_collection_completed=True, native_collection_error_type=None,
                             native_structural_valid=self.collection.get('structural_valid') is True,
                             context_sha256=sha(r / 'live-expansion-context.json'))
        expected_saved['joint_gate_valid'] = expected_saved['native_structural_valid'] and replay['task_identity_valid']
        self.check('actual_native_task_identity_replay_and_joint_gate', replay['task_identity_valid'] is True and saved == expected_saved
                   and saved.get('joint_gate_valid') is True, [self.live / 'task-identity.json', replay_path], errors=replay['errors'])
        slots = self.execution['slots']
        self.check('sequential_slots_and_fresh_profile_volumes', len(slots) == 2
                   and slots[0].get('finished_at_unix', math.inf) <= slots[1].get('started_at_unix', -math.inf)
                   and len([v for s in slots for v in s.get('fresh_volumes', [])]) == 4
                   and len({v for s in slots for v in s.get('fresh_volumes', [])}) == 4, [self.live / 'execution.json'])
        system = self.execution['system']
        self.check('native_system_versions', system['harbor_version'] == '0.18.0' and system['docker_architecture'] == 'x86_64'
                   and system['python'].startswith('3.12.'), [self.live / 'execution.json'])
        self.pkg = load(r / 'package-derivation.json')
        self.package_sha = sha(r / 'zg-install.tgz')
        self.payloads = {x['path'].removeprefix('package/'): {'bytes': x['derived_bytes'], 'sha256': x['derived_sha256'], 'mode': x['mode']}
                         for x in self.pkg['runtime_payloads']}
        with tarfile.open(r / 'zg-install.tgz') as archive:
            payload_ok = all(hashlib.sha256(archive.extractfile('package/' + k).read()).hexdigest() == v['sha256'] for k, v in self.payloads.items())
        self.check('canonical_package_and_four_unchanged_runtime_payloads', payload_ok and len(self.payloads) == 4
                   and self.package_sha == self.pkg['install_package_sha256'] == base['candidate_sha256'] == self.execution['candidate_sha256']
                   and self.pkg['upstream_commit'] == inputs['upstream_commit'] == UPSTREAM
                   and all(x['unchanged'] is True for x in self.pkg['runtime_payloads']),
                   [r / 'zg-install.tgz', r / 'package-derivation.json', r / 'inputs.json', self.live / 'execution.json'])

    def profile(self, profile):
        l, r = self.live, self.raw
        cp = next(x for x in self.collection['profiles'] if x['profile'] == profile)
        slot = next(x for x in self.execution['slots'] if x['profile'] == profile)
        detail = {'profile': profile, 'task_id': self.task, 'slot_status': slot.get('status'), 'complete_review': False}
        self.profiles.append(detail)
        trial_relative = cp.get('trial_directory', '')
        if not relative(trial_relative):
            raise ValueError('missing_or_invalid_trial_directory:' + profile)
        t = l / trial_relative
        agent = t / 'agent'
        sp = agent / 'claude-code.txt'
        stream = [v for _, v in rows(sp)]
        inits = [v for v in stream if v.get('type') == 'system' and v.get('subtype') == 'init']
        terminals = [v for v in stream if v.get('type') == 'result']
        init = inits[0] if len(inits) == 1 else {}
        terminal = terminals[0] if len(terminals) == 1 else {}
        self.inits[profile] = init
        sid = init.get('session_id')
        ap = l / 'collection' / (profile + '-strict-accounting.json')
        accounting = load(ap)
        replay = self.accounting.analyze(agent, expected_model=MODEL)
        cfg, lock, result, audit = [load(t / n) for n in ('config.json', 'lock.json', 'result.json', 'qa-source-audit.json')]
        atif = load(agent / 'trajectory.json')
        prefix = profile + ':'
        self.check(prefix + 'one_native_trial_and_original_slot_cleanup', len(list((l / 'runs' / ('q001-' + profile)).glob('*/result.json'))) == 1
                   and t.name == result['trial_name'] == cfg['trial_name'] and result.get('exception_info') is None
                   and slot == load(l / (profile + '-slot.json')) and slot.get('status') == 'exited' and slot.get('returncode') == 0
                   and self.collector.cleanup_complete(slot),
                   [t / 'config.json', t / 'result.json', l / (profile + '-slot.json'), l / 'execution.json', t / 'trial.log', l / (profile + '-job.log')],
                   process_cleanup=slot.get('process_cleanup'), container_cleanup=slot.get('container_cleanup'),
                   limitation='Recorded cleanup of the expired runner; no new host inspection.')
        command = slot['command']
        pcmd = list(next(x['command'] for x in self.pre['slots'] if x['profile'] == profile))
        pi = pcmd.index('--jobs-dir') + 1
        pcmd[pi] = str(PurePosixPath(pcmd[pi]).parent.parent / 'live' / 'runs')
        remote = PurePosixPath(self.ctx['recorded_prepared_root'])
        expected_agent = 'native_claude.agents:' + ('QABaseline' if profile == 'baseline' else 'QAZvec')
        checks = {'--include-task-name': 'zvec-' + self.task, '--path': str(remote / 'tasks'), '--agent': expected_agent,
                  '--n-attempts': '1', '--max-retries': '0', '--n-concurrent': '1', '--model': MODEL, '--env': 'docker'}
        self.check(prefix + 'actual_command_exact_preflight_and_native_task', command == pcmd
                   and all(options(command, f) == [v] for f, v in checks.items())
                   and cfg['agent']['model_name'] == lock['agent']['model_name'] == MODEL
                   and cfg['agent']['name'] == expected_agent
                   and all(cfg['agent']['kwargs'][k] == v for k, v in {'qa_profile': profile, 'version': '2.1.212',
                       'reasoning_effort': 'high', 'max_budget_usd': 4.0, 'qa_source_manifest': str(remote / 'source-manifest.json')}.items()),
                   [r / 'preflight.json', l / 'execution.json', t / 'config.json', t / 'lock.json'])
        containers, compose = [load(l / (profile + '-' + name + '.json')) for name in ('containers', 'compose')]
        self.check(prefix + 'mounts_and_fresh_volumes', len(containers) == 1 and self.collector.mounts_valid(containers, slot)
                   and {v['name'] for v in compose['volumes'].values()} == set(slot['fresh_volumes']),
                   [l / (profile + '-containers.json'), l / (profile + '-compose.json'), l / (profile + '-slot.json')])
        rr = l / 'collection' / (profile + '-root-result.json')
        self.check(prefix + 'one_successful_native_root_terminal', len(inits) == len(terminals) == 1
                   and stream[-1] == terminal and init.get('cwd') == '/workspace' and init.get('model') == MODEL
                   and init.get('claude_code_version') == '2.1.212' and terminal.get('session_id') == sid
                   and terminal.get('subtype') == 'success' and terminal.get('is_error') is False
                   and terminal.get('terminal_reason') == 'completed' and terminal.get('stop_reason') == 'end_turn'
                   and not terminal.get('permission_denials') and load(rr) == terminal,
                   [sp, rr, t / 'result.json'], root_session_id=sid)
        independent = independent_messages(agent, accounting)
        persisted = independent['persisted_files']
        root_users = [event for _, event in independent['session_rows'].get('root', []) if event.get('type') == 'user']
        self.check(prefix + 'actual_root_question_and_atif_exact', bool(root_users)
                   and root_users[0]['message']['content'] == self.question and atif['steps'][0]['message'] == self.question,
                   [*persisted, agent / 'trajectory.json', self.question_path])
        structural_errors = [e for e in accounting['errors'] if e.get('code') not in USAGE_ONLY]
        self.check(prefix + 'strict_reader_exact_replay_and_structural_tree', replay == accounting and not structural_errors
                   and accounting['root_count'] == 1 and independent['sessions_match']
                   and accounting['root_session_id'] == sid and accounting['session_count'] == independent['session_count']
                   and accounting['child_count'] == independent['child_count']
                   and cp['structural_accounting_errors'] == structural_errors,
                   [ap, sp, *persisted], errors=accounting['errors'], session_count=accounting['session_count'], child_count=accounting['child_count'],
                   async_completion_reconciliation=accounting.get('async_completion_reconciliation'),
                   complete=accounting['complete'], usage_only_errors=[e for e in accounting['errors'] if e.get('code') in USAGE_ONLY])
        self.check(prefix + 'every_actual_tool_and_message_identity', independent['message_match'] and independent['tool_match']
                   and all(m['model'] == MODEL for m in independent['messages']), [ap, *persisted],
                   actual_tool_count=independent['tool_count'], actual_message_count=len(independent['messages']), tool_conflicts=independent['tool_conflicts'])
        inventory = {p.relative_to(agent).as_posix(): {'bytes': p.stat().st_size, 'sha256': sha(p)} for p in [sp, *persisted]}
        expected_inventory = {f['path']: {k: f[k] for k in ('bytes', 'sha256')} for f in accounting['files']}
        self.check(prefix + 'strict_inventory_covers_all_native_root_child_files', len(expected_inventory) == len(accounting['files'])
                   and inventory == expected_inventory and self.collector.native_log_bytes_match(agent), [ap, sp, *persisted],
                   native_file_count=len(inventory))
        answer = l / 'collection' / cp['answer_file']
        agent_finals = [s for s in atif.get('steps', []) if s.get('source') == 'agent' and not s.get('tool_calls')]
        self.check(prefix + 'root_final_answer_identity', answer.read_bytes() == terminal['result'].encode()
                   and terminal['result'] == accounting['final']['text'] and sha(answer) == cp['answer_sha256']
                   and atif['session_id'] == sid and bool(agent_finals)
                   and agent_finals[-1]['message'].strip() == terminal['result'].strip(),
                   [answer, rr, ap, agent / 'trajectory.json'], answer_sha256=sha(answer), quality_evaluated=False)
        available = accounting.get('complete') is True
        totals = independent['usage']
        sum_matches = (not available or totals is not None and all(accounting['usage'][k] == v for k, v in totals.items()))
        self.check(prefix + 'independent_terminal_usage_and_collector_availability', sum_matches
                   and cp['strict_usage_available'] is available
                   and cp['strict_usage'] == (accounting['usage'] if available else None)
                   and accounting['usage']['llm_calls'] == len(independent['messages'])
                   and accounting['usage']['unknown_usage_messages'] == sum(not x['usage_valid'] for x in independent['messages']),
                   [ap, *persisted, l / 'collection/collection.json'], category='metric',
                   all_terminal_fields_available=independent['all_terminal_fields_available'],
                   strict_complete=available, complete_tree=accounting['complete_tree'],
                   complete_terminal_sum=totals if available else None,
                   usage_update_issues=independent['usage_update_issues'],
                   limitation='Unknown terminal usage is retained as unavailable, never imputed as zero.')
        reported = self.collector.reported_model_usage(terminal)
        consistency = self.collector.metric_consistency(reported, accounting)
        # Independent primary-field reconstruction and component comparison.
        raw = terminal.get('modelUsage')
        values = raw.get(MODEL, {}) if isinstance(raw, dict) else {}
        fmap = {'inputTokens': 'uncached_input_tokens', 'cacheReadInputTokens': 'cache_read_tokens',
                'cacheCreationInputTokens': 'cache_write_tokens', 'outputTokens': 'output_tokens'}
        reported_available = isinstance(raw, dict) and set(raw) == {MODEL} and all(number(values.get(k)) for k in fmap)
        independently_reported = None
        if reported_available:
            independently_reported = {v: values[k] for k, v in fmap.items()}
            independently_reported['input_tokens'] = values['inputTokens'] + values['cacheReadInputTokens'] + values['cacheCreationInputTokens']
        contradictions = []
        if any(e.get('code') == 'usage_regression' for e in accounting['errors']):
            contradictions.append('retained_usage_update_regressed')
        if reported_available and available:
            for key in ('uncached_input_tokens', 'cache_read_tokens', 'cache_write_tokens', 'output_tokens'):
                if independently_reported[key] < accounting['usage'][key]:
                    contradictions.append('reported_below_complete_persisted:' + key)
        self.check(prefix + 'reported_four_fields_and_metric_consistency', reported == cp['claude_reported_model_usage']
                   and reported['available'] is reported_available and reported['usage'] == independently_reported
                   and reported['scope'] == 'claude-reported-model-usage' and reported['cost_usd'] is None
                   and consistency == cp['metric_consistency'] and consistency['contradictions'] == contradictions
                   and cp['reported_metric_eligible'] == bool(cp['structural_valid'] and reported_available and not contradictions),
                   [rr, ap, l / 'collection/collection.json'], category='metric',
                   primary_usage=independently_reported, required_fields={k: values.get(k) for k in fmap},
                   corroboration=consistency, metric_eligible=cp['reported_metric_eligible'])
        phasecounts = {}
        expected_claude = self.proof['verification'][0]['source_audit']['before']['observed']['runtime']['claude']
        for phase in ('source-before', 'source-after', 'logs-before', 'logs-after'):
            e = t / 'qa-native-claude-evidence' / phase
            cap, data, transport = [load(e / n) for n in ('capture.json', 'stdout.json', 'transport.json')]
            self.check(prefix + 'capture_' + phase, (e / 'status.txt').read_text() == '0\n'
                       and transport['return_code'] == 0 and cap['valid'] is True
                       and all(v['complete'] is True and sha(e / k) == v['sha256'] and (e / k).stat().st_size == v['bytes'] for k, v in cap['files'].items())
                       and sha(e / 'transport.json') == cap['transport_file']['sha256'],
                       [e / n for n in ('capture.json', 'stdout.json', 'stderr.txt', 'status.txt', 'transport.json')])
            if phase.startswith('source'):
                sourceok = self.identity._source_matches(data['files'], self.manifest['files'], profile)
                additions = {x['path'] for x in data['files']} - set(self.canonical)
                self.check(prefix + 'frozen_task_tree_' + phase, sourceok and audit['valid'] is True
                           and audit[phase.split('-')[1]]['observed'] == data,
                           [e / 'stdout.json', GROUP / self.task / 'source-manifest.json', t / 'qa-source-audit.json'],
                           original_files=len(self.canonical), observed_files=len(data['files']), generated_files=len(additions))
                phasecounts[phase] = len(data['files'])
                runtime = data['runtime']
                claude = runtime['claude']
                runtimeok = (claude['version'] == '2.1.212 (Claude Code)'
                             and claude['binary_sha256'] == claude['entrypoint_sha256'] == expected_claude['binary_sha256'])
                if profile == 'zvec-grep':
                    runtimeok &= runtime['zg']['payloads'] == self.payloads and runtime['zg']['binary_sha256'] == self.pkg['binary_sha256']
                else:
                    runtimeok &= set(runtime) == {'claude'} and all(data.get(k) is False for k in ('zg_executable_present', 'zg_home_present', 'mcp_config_present')) and data['embedding_environment_names'] == []
                self.check(prefix + 'pinned_runtime_' + phase, runtimeok,
                           [e / 'stdout.json', r / 'package-derivation.json', W / self.base['offline_proof']['directory'] / 'proof.json'],
                           claude_binary_sha256=claude['binary_sha256'])
            else:
                snapshot = t / 'qa-native-claude-logs'
                expected = {x['path'] for x in data['files']}
                actual = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob('*') if p.is_file()}
                okay = all(relative(x['path']) and sha(snapshot / x['path']) == sha(agent / x['path']) == x['sha256']
                           and (snapshot / x['path']).stat().st_size == x['bytes'] for x in data['files'])
                self.check(prefix + 'complete_native_log_export_' + phase, okay and actual == expected and len(expected) == len(data['files'])
                           and data['root'] == '/logs/agent', [e / 'stdout.json', t / 'qa-source-audit.json', *persisted], files=len(expected))
        timings, timing_ok = {}, True
        for phase in ('environment_setup', 'agent_setup', 'agent_execution', 'verifier', 'trial'):
            value = result if phase == 'trial' else result[phase]
            start, end = [datetime.datetime.fromisoformat(value[k].replace('Z', '+00:00')) for k in ('started_at', 'finished_at')]
            seconds = (end - start).total_seconds()
            timings[phase] = seconds
            timing_ok &= number(seconds) and cp['harbor_timing'][phase]['seconds'] is not None and abs(seconds - cp['harbor_timing'][phase]['seconds']) < 1e-6
        outer = slot['finished_at_unix'] - slot['started_at_unix']
        timing_ok &= number(outer) and abs(outer - cp['outer_slot_wall_seconds']) < 1e-6 and all(
            number(cp[k]) and cp[k] == audit[ak] for k, ak in [('native_run_wall_seconds', 'native_run_wall_seconds'),
                ('source_audit_wall_seconds', 'audit_wall_seconds'), ('log_export_wall_seconds', 'export_wall_seconds')])
        self.check(prefix + 'separate_timing_scopes_match_raw', timing_ok,
                   [t / 'result.json', t / 'qa-source-audit.json', l / (profile + '-slot.json'), l / 'collection/collection.json'],
                   harbor_seconds=timings, outer_slot_seconds=outer, native_run_seconds=audit['native_run_wall_seconds'],
                   source_audit_seconds=audit['audit_wall_seconds'], log_export_seconds=audit['export_wall_seconds'],
                   native_stream_duration_ms=terminal.get('duration_ms'))
        setup_timing = None
        if profile == 'zvec-grep':
            setup = load(agent / 'zvec-grep-setup.json')
            setup_timing = cp['native_zg_setup_timing']
            self.check(prefix + 'fresh_index_install_and_setup_timing', setup['status'] == 'ready' and setup['workdir'] == '/workspace'
                       and setup['mcp_target'] == 'claude-code' and setup['package_sha256'] == self.package_sha
                       and setup['index_cache_hit'] is False and setup['index_seed_available'] is False and setup['install_reused_cache'] is False
                       and setup['embedding_model'] == 'qwen/qwen3.7-text-embedding' and setup['binding_package'] == '@zvec/bindings-linux-x64@0.5.0'
                       and setup['index_ignore_sha256'] == sha(GROUP / self.task / 'index.ignore')
                       and isinstance(setup_timing, dict) and all(setup[k] == v for k, v in setup_timing.items()),
                       [agent / 'zvec-grep-setup.json', GROUP / self.task / 'index.ignore', l / 'collection/collection.json'], timing=setup_timing)
        self.check(prefix + 'original_collection_execution_gate', cp['structural_valid'] is True
                   and cp['quality'] is None and cp['quality_status'] == 'not_evaluated', [l / 'collection/collection.json'])
        detail.update(complete_review=True, trial_directory=trial_relative, root_session_id=sid, answer_sha256=sha(answer),
                      session_count=accounting['session_count'], child_count=accounting['child_count'],
                      strict_complete=available, strict_errors=accounting['errors'], tool_count=independent['tool_count'],
                      native_unique_messages=len(independent['messages']),
                      terminal_message_evidence=independent['messages'],
                      primary_usage=independently_reported, strict_usage=totals if available else None,
                      metric_consistency=consistency, reported_metric_eligible=cp['reported_metric_eligible'],
                      source_inventory_counts=phasecounts, native_zg_setup_timing=setup_timing,
                      timing_seconds={**timings, 'outer_slot': outer, 'native_run': audit['native_run_wall_seconds'],
                                      'source_audit': audit['audit_wall_seconds'], 'log_export': audit['export_wall_seconds']})

    def run(self):
        destination = self.out / (self.task + '-execution-review.json')
        if destination.exists() or destination.is_symlink() or (self.out / (self.task + '-task-identity-replay.json')).exists():
            raise FileExistsError('audit_output_already_exists')
        self.verify_origin()
        try:
            self.prepare()
            for profile in ORDERS[self.task]:
                try:
                    self.profile(profile)
                except Exception as exc:
                    self.check(profile + ':required_evidence_read_or_schema', False, category='execution', error_type=type(exc).__name__, error=str(exc))
            roots = [v.get('root_session_id') for v in self.profiles]
            baseline, zg = self.inits.get('baseline', {}), self.inits.get('zvec-grep', {})
            tools_ok = (isinstance(baseline.get('tools'), list) and isinstance(zg.get('tools'), list)
                        and set(zg['tools']) - set(baseline['tools']) == {'mcp__zvec_grep__zvec_grep_search'}
                        and not(set(baseline['tools']) - set(zg['tools'])))
            self.check('both_scheduled_profiles_independent_root_sessions_and_same_tools', len(roots) == 2 and all(roots)
                       and len(set(roots)) == 2 and tools_ok and self.collection.get('structural_valid') is True,
                       [self.live / 'execution.json', self.live / 'collection/collection.json'])
        except Exception as exc:
            self.check('required_global_evidence_read_or_schema', False, category='execution', error_type=type(exc).__name__, error=str(exc))
        self.check('unchanged_prior_method_reference', sha(PREVIOUS) == PREVIOUS_SHA, [PREVIOUS], category='binding')
        execution_valid = all(x['passed'] for x in self.checks if x['category'] != 'metric')
        metric_checks_valid = all(x['passed'] for x in self.checks if x['category'] == 'metric')
        report = {'schema_version': 1, 'reviewed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'scope': 'Independent native execution/runtime/task identity/accounting audit; no source-tool semantic audit or quality grading.',
                  'protocol_id': PROTOCOL, 'plan_sha256': PLAN_SHA, **self.origin,
                  'raw_directory': str(self.raw), 'passed': all(x['passed'] for x in self.checks),
                  'execution_valid': execution_valid, 'metric_reconciliation_valid': metric_checks_valid,
                  'reported_metric_pair_eligible': execution_valid and metric_checks_valid and len(self.profiles) == 2
                      and all(x.get('reported_metric_eligible') is True for x in self.profiles),
                  'strict_usage_pair_complete': len(self.profiles) == 2 and all(x.get('strict_complete') is True for x in self.profiles),
                  'check_count': len(self.checks), 'passed_checks': sum(x['passed'] for x in self.checks),
                  'failed_checks': [x['check'] for x in self.checks if not x['passed']], 'checks': self.checks,
                  'profiles': self.profiles, 'evidence_catalog': self.catalog, 'quality_evaluated': False,
                  'source_scope_audit_status': 'separate_review_required', 'expansion_outcome': 'not_evaluated',
                  'method_reuse': {'path': str(PREVIOUS), 'sha256': PREVIOUS_SHA,
                                  'changes': ['Explicit expansion task/context/prepared identities replace q001 identity.',
                                              'All actual sessions, tools and terminal messages replace root-only assumptions.',
                                              'Verified inventory controls file set; no fixed artifact file count.',
                                              'Reported totals may match, exceed, contradict or be unavailable; no equality presumption.']},
                  'review_script': {'path': str(Path(__file__).resolve()), 'sha256': sha(Path(__file__))},
                  'limitations': ['No provider, Docker or CI calls; no grading, private assignment access or raw mutation.',
                                  'Execution validity still requires separate manual tool/source scope audit.',
                                  'CLI modelUsage is reported usage, not provider billing; persisted corroboration does not prove unlogged request coverage.',
                                  'Missing usage remains unavailable; it is not an answer-quality failure.',
                                  'Native, setup/index, audit/export, Harbor intervals and outer wall are distinct scopes; no pure-model-time inference.',
                                  'Cleanup/freshness are assessed from retained records, not live inspection of an expired runner.']}
        exclusive(destination, report)
        print(json.dumps({'path': str(destination), 'sha256': sha(destination), 'passed': report['passed'],
                          'execution_valid': execution_valid, 'check_count': len(self.checks), 'failed_checks': report['failed_checks']}, indent=2))
        return 0 if report['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True, choices=tuple(ORDERS))
    return Audit(parser.parse_args().task).run()


if __name__ == '__main__':
    raise SystemExit(main())
