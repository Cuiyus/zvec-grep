#!/usr/bin/env python3
"""Read-only replay of retained v2 infrastructure evidence; writes only --report.

No Docker, provider, network, npm, or native agent process is invoked. The audit
uses original artifact bytes, retained execution evidence, the pinned repository
files, and a separately retained frozen q001 source export. It never grades QA.
"""
from __future__ import annotations

import argparse
import binascii
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
import tarfile
import zipfile

BASE = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
REPO = Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
ZIP_SHA = 'e64eb1cc73fb2ca8d6b2b33a94ffe2416a333f80bb1f640da9092634f57f1fc1'
PROOF_SHA = '085d1d40a31216b1246fd9df68607048aff8d22d19f8d28411735997bc9824e3'
INPUT_SHA = '9063a175a5db909988b5775230e0523de7965e4cd41f41066559798922825e87'
INSTALL_SHA = 'fbe4bd201c8a0520f2373f1039e1d0805a38781c6e2f3ce11b83310d23dc6522'
ORIGINAL_SHA = '470c2a11b8b19a9e6458c977918260ee6f535595488ed8b44233db5aa032ad4d'
MANIFEST_SHA = '655afc79049c02ca4b2c278c823aefa5d292256c46f4b92febcccf577f63b479'
BINARY_SHA = '645ef8b62d62ff604c06f3c8aa34790c3c28bc65c946714ecdbf7175131b61dd'
OPENCODE_SHA = '6ce6570e7db9a40e7bd3304ebdfff607920bde8cafd2eb5587bd7a26f89ba0b5'
SOURCE_SHA = '741d06d8fc216150933c5ed87f9a100e0d2120b2dc09a9109084365e92d307ba'
SENTINEL = 'OFFLINE_V2_NATIVE_TRANSPORT_COMPLETE'
CHILD_SENTINEL = 'OFFLINE_V2_CHILD_TRANSPORT_COMPLETE'
QUERY = 'collection storage vector search implementation'
CHECKS = []


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def check(label, condition, detail=None):
    row = {'check': label, 'passed': bool(condition)}
    if detail is not None:
        row['detail'] = detail
    CHECKS.append(row)


def totals(messages, tools):
    return {
        'llm_calls': len(messages), 'tool_calls': len(tools),
        'input_tokens': sum(m['input'] + m['cache_read'] + m['cache_write'] for m in messages),
        'uncached_input_tokens': sum(m['input'] for m in messages),
        'cache_read_tokens': sum(m['cache_read'] for m in messages),
        'cache_write_tokens': sum(m['cache_write'] for m in messages),
        'output_tokens': sum(m['output'] + m['reasoning'] for m in messages),
        'text_output_tokens': sum(m['output'] for m in messages),
        'reasoning_tokens': sum(m['reasoning'] for m in messages), 'cost_usd': None,
    }


def archive_members(path):
    with tarfile.open(path, 'r:gz') as archive:
        headers = archive.getmembers()
        check('plain_safe_seven_members:' + path.name, len(headers) == 7 and len({m.name for m in headers}) == 7
              and all(m.type == tarfile.REGTYPE and not m.linkname and not m.pax_headers
                      and not PurePosixPath(m.name).is_absolute() and '..' not in PurePosixPath(m.name).parts for m in headers))
        return {m.name: archive.extractfile(m).read() for m in headers}, headers


def verify_capture(directory):
    capture = load(directory / 'capture.json')
    for name, record in capture['files'].items():
        path = directory / name
        if record.get('complete'):
            check('capture_bytes:' + directory.name + '/' + name,
                  path.is_file() and path.stat().st_size == record['bytes'] and digest(path) == record['sha256'])
        else:
            check('capture_incomplete_retained:' + directory.name + '/' + name,
                  bool(record.get('error_type')) and not capture['valid'])
    if 'transport_file' in capture:
        record = capture['transport_file']
        path = directory / record['filename']
        check('transport_bytes:' + directory.name, path.stat().st_size == record['bytes'] and digest(path) == record['sha256'])
        check('transport_record:' + directory.name, load(path) == capture['transport'])
    return capture


def audit(args):
    evidence, repo, source = args.evidence_root, args.repo_root, args.source_root
    raw = evidence / 'raw'
    integration = raw / 'integration'
    proof = load(integration / 'proof.json')
    check('github_zip_sha256', digest(evidence / 'artifact.zip') == ZIP_SHA)
    with zipfile.ZipFile(evidence / 'artifact.zip') as archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        names = [i.filename for i in infos]
        check('zip_130_unique_safe_members', len(names) == len(set(names)) == 130
              and all(not PurePosixPath(n).is_absolute() and '..' not in PurePosixPath(n).parts for n in names))
        check('raw_exact_zip_file_set', set(names) == {p.relative_to(raw).as_posix() for p in raw.rglob('*') if p.is_file()})
        for info in infos:
            path = raw / info.filename
            equal = path.is_file() and path.stat().st_size == info.file_size
            with archive.open(info) as zipped, path.open('rb') as retained:
                while chunk := zipped.read(1024 * 1024):
                    equal = equal and chunk == retained.read(len(chunk))
                equal = equal and not retained.read(1)
            check('zip_byte_equality:' + info.filename, equal)
    run = load(evidence / 'run-metadata.json')
    artifact = load(evidence / 'artifacts-metadata.json')['artifacts'][0]
    check('run_identity_and_success', run['id'] == 39314027006 and run['head_sha'] == 'cd0f4dbc321f4fe141c9d11de92be03eadfce877'
          and run['repository']['full_name'] == 'Cuiyus/zvec-grep' and run['run_attempt'] == 1
          and run['event'] == 'push' and run['status'] == 'completed' and run['conclusion'] == 'success')
    check('artifact_identity_and_digest', artifact['id'] == 12216879282 and artifact['digest'] == 'sha256:' + ZIP_SHA
          and artifact['workflow_run']['id'] == run['id'] and artifact['workflow_run']['head_sha'] == run['head_sha'])
    check('proof_exact_identity', digest(integration / 'proof.json') == PROOF_SHA)
    check('proof_reports_infrastructure_only', proof['valid'] is True and proof['errors'] == []
          and proof['scope'] == 'provider-offline-infrastructure-only' and proof['quality_evaluated'] is False
          and proof['live_provider_calls_authorized'] is False)
    for record in proof['evidence_files']:
        path = integration / record['path']
        check('proof_evidence_digest:' + record['path'], path.stat().st_size == record['bytes'] and digest(path) == record['sha256'])
    check('proof_inventory_exact', {r['path'] for r in proof['evidence_files']} ==
          {p.relative_to(integration).as_posix() for p in integration.rglob('*') if p.is_file() and p.name != 'proof.json'})
    inputs = load(raw / 'inputs.json')
    check('inputs_identity', digest(raw / 'inputs.json') == INPUT_SHA == proof['inputs_sha256'])
    check('inputs_current_copy', (raw / 'inputs.json').read_bytes() == (repo / 'benchmarks/opencode-qa-pilot/repair_v2/inputs.json').read_bytes())
    check('proof_bound_to_input_inventory', inputs['files'] == proof['input_files'] and inputs['revision'] == proof['inputs_revision'] == 1)
    for name, identity in inputs['files'].items():
        check('frozen_input:' + name, digest(repo / name) == identity)
    v1 = load(repo / 'benchmarks/opencode-qa-pilot/experiment.json')
    check('closed_v1_plan_immutable', digest(repo / 'benchmarks/opencode-qa-pilot/experiment.json') == inputs['v1_plan_sha256'])
    for name, identity in v1['frozen_harness_sha256'].items():
        check('v1_file_immutable:' + name, digest(repo / name) == identity)
    for name, identity in v1['main_file_sha256'].items():
        check('pinned_main_file:' + name, digest(args.upstream_root / name) == identity)
    check('old_claude_results_immutable', digest(repo / 'benchmarks/claude-qa-pilot/results/20261019/results.json') ==
          '6d8e87b366449e4203ddd00d3fc4e4272b97fc42cc192b3e6abf04cf368e104b')

    # Reconstruct package proof without importing the builder under audit.
    manifest = load(raw / 'package-derivation.json')
    check('derivation_manifest_identity', digest(raw / 'package-derivation.json') == MANIFEST_SHA
          and (raw / 'package-derivation.json').read_bytes() == (integration / 'package-manifest.json').read_bytes())
    check('archive_identities', digest(args.original_tgz) == ORIGINAL_SHA and digest(raw / 'zg-install.tgz') == INSTALL_SHA
          == manifest['install_package_sha256'] == inputs['install_package_sha256'] == proof['candidate_sha256'])
    original, _ = archive_members(args.original_tgz)
    derived, headers = archive_members(raw / 'zg-install.tgz')
    check('seven_member_names_match_manifest', set(original) == set(derived) == {r['path'] for r in manifest['members']})
    changed = [name for name in original if original[name] != derived[name]]
    check('only_package_json_bytes_change', changed == ['package/package.json'])
    before, after = json.loads(original['package/package.json']), json.loads(derived['package/package.json'])
    added = after.pop('bin')
    check('semantic_diff_only_npm_bin', added == {'zg': 'bin/zg'} and after == before and 'scripts' not in after)
    check('builder_provenance', manifest['builder']['sha256'] == digest(repo / manifest['builder']['path'])
          and manifest['install_package_is_unmodified_upstream_archive'] is False)
    for record in manifest['members']:
        name = record['path']
        check('package_member_hash:' + name, sha(original[name]) == record['source_sha256'] and len(original[name]) == record['source_bytes']
              and sha(derived[name]) == record['derived_sha256'] and len(derived[name]) == record['derived_bytes']
              and record['unchanged'] == (name != 'package/package.json'))
        check('package_member_mode:' + name, next(h.mode for h in headers if h.name == name) == int(record['mode'], 8))
    checksums = json.loads(derived['package/checksums.json'])['files']
    check('runtime_checksums_exact_coverage', len(checksums) == 4 and {'package/' + n for n in checksums} == {r['path'] for r in manifest['runtime_payloads']})
    for name, entry in checksums.items():
        check('runtime_checksum:' + name, sha(derived['package/' + name]) == entry['sha256'] and len(derived['package/' + name]) == entry['bytes'])
    check('canonical_ustar_metadata', [h.name for h in headers] == sorted(derived)
          and all((h.uid, h.gid, h.mtime, h.uname, h.gname) == (0, 0, 0, '', '') for h in headers))
    crc = size = blocks = 0
    stored_valid = True
    with (raw / 'zg-install.tgz').open('rb') as handle:
        check('canonical_gzip_header', handle.read(10) == bytes.fromhex('1f8b08000000000000ff'))
        while True:
            control = handle.read(1)
            length, complement = struct.unpack('<HH', handle.read(4))
            stored_valid &= control in (b'\0', b'\1') and length ^ complement == 65535
            stored_valid &= length == 65535 if control == b'\0' else 0 < length <= 65535
            block = handle.read(length)
            crc = binascii.crc32(block, crc)
            size += length
            blocks += 1
            if control == b'\1':
                break
        check('canonical_gzip_stored_blocks_and_trailer', stored_valid and struct.unpack('<II', handle.read(8)) == (crc & 0xffffffff, size & 0xffffffff) and handle.read() == b'')
    check('canonical_gzip_length', (raw / 'zg-install.tgz').stat().st_size == 123165898 == 10 + size + blocks * 5 + 8)
    check('manifest_compression_independent', manifest['normalization']['compression_library'] is None)

    source_inventory = [{'path': p.relative_to(source).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)}
                        for p in sorted(source.rglob('*')) if p.is_file()]
    check('frozen_q001_source_1232', len(source_inventory) == 1232 and sha(json.dumps(source_inventory, sort_keys=True, separators=(',', ':')).encode()) == SOURCE_SHA)
    source_map = {r['path']: r for r in source_inventory}
    source_manifest = source.parents[3] / 'source-manifest.json'
    check('source_manifest_matches_proof', digest(source_manifest) == proof['source_manifest_sha256'])
    collection = load(integration / 'collection/collection.json')
    native = load(integration / 'collection/native-pair.json')
    check('collector_structural_pass_scope', collection['structural_valid'] is True and collection['errors'] == []
          and collection['quality_evaluated'] is False and collection['protocol_valid'] is None
          and collection['protocol_status'] == 'pending_tool_audit' and native['valid'] is True)
    execution = load(integration / 'run/execution.json')
    check('exact_two_single_attempt_slots', [s['profile'] for s in execution['slots']] == ['baseline', 'zvec-grep']
          and all(s['command'][s['command'].index('--n-attempts') + 1] == '1' and s['command'][s['command'].index('--max-retries') + 1] == '0' for s in execution['slots']))
    check('execution_record_matches_proof', execution == proof['execution'])
    results = {}
    mcp_line_checks = []
    mcp_window = None
    for profile, expected_calls, expected_tools in [('baseline', 5, 3), ('zvec-grep', 6, 4)]:
        parent = integration / 'run/runs' / ('q001-' + profile)
        trials = list(parent.glob('zvec-q001__*/'))
        check(profile + ':one_trial', len(trials) == 1)
        trial = trials[0]
        result = load(trial / 'result.json')
        check(profile + ':native_trial_completed', result['exception_info'] is None and result['agent_execution'] is not None and result['agent_result'] is not None)
        audit = load(trial / 'qa-source-audit.json')
        check(profile + ':source_and_exports_valid', audit['valid'] is True and audit['errors'] == [] and audit['session_exports_complete'] is True
              and len(audit['expected_sessions']) == 2 and set(audit['expected_sessions']) == set(audit['exported_sessions']))
        for phase in ('before', 'after'):
            observed = load(trial / ('qa-evidence-v2/source-' + phase + '/stdout.json'))
            check(profile + ':' + phase + ':audit_copy', observed == audit[phase]['observed'] and audit[phase]['valid'] is True)
            files = {r['path']: {k: r.get(k) for k in ('path', 'bytes', 'sha256')} for r in observed['files'] if r.get('kind') == 'file'}
            check(profile + ':' + phase + ':1232_source_bytes', all(files.get(name) == record for name, record in source_map.items()))
            additions = set(files) - set(source_map)
            check(profile + ':' + phase + ':only_allowed_additions', not additions if profile == 'baseline' else all(n == '.zg-bench-index.ignore' or n.startswith('.zvec-grep/') for n in additions))
            runtime = observed['runtime']
            check(profile + ':' + phase + ':opencode_attestation', runtime['opencode']['version'] == '1.18.4'
                  and runtime['opencode']['binary_sha256'] == runtime['opencode']['entrypoint_sha256'] == OPENCODE_SHA)
            if profile == 'baseline':
                check(profile + ':' + phase + ':no_zg_or_embedding', observed['zg_executable_present'] is False and observed['zg_home_present'] is False
                      and observed['mcp_config_present'] is False and observed['embedding_environment_names'] == [] and 'zg' not in runtime)
            else:
                zg = runtime['zg']
                check(profile + ':' + phase + ':rust_installed_attestation', zg['version'] == '0.0.1' and zg['binary_sha256'] == zg['entrypoint_sha256'] == BINARY_SHA
                      and '/lib/node_modules/@zvec/zvec-grep-linux-x64-gnu/bin/zg' in zg['binary_path'])
                for record in manifest['runtime_payloads']:
                    installed = zg['payloads'][record['path'].removeprefix('package/')]
                    check(profile + ':' + phase + ':installed_payload:' + record['path'], installed == {'bytes': record['derived_bytes'], 'sha256': record['derived_sha256'], 'mode': record['mode']})
        for capture_path in sorted((trial / 'qa-evidence-v2').glob('*/capture.json')):
            capture = verify_capture(capture_path.parent)
            check(profile + ':capture_valid:' + capture_path.parent.name, capture['valid'] is True)
        snapshot_path = trial / 'agent/opencode-session-snapshot.json'
        check(profile + ':native_snapshot_direct_download', snapshot_path.read_bytes() == (trial / 'qa-evidence-v2/usage-snapshot/stdout.json').read_bytes())
        rows = load(snapshot_path)
        sessions = [json.loads(r['data']) for r in rows if r['kind'] == 'session']
        messages = [json.loads(r['data']) for r in rows if r['kind'] == 'message']
        tools = [json.loads(r['data']) for r in rows if r['kind'] == 'tool']
        root = next(s['id'] for s in sessions if s['parent_id'] is None)
        child = next(s['id'] for s in sessions if s['parent_id'] == root)
        check(profile + ':complete_session_tree', len(sessions) == 2 and set(audit['expected_sessions']) == {root, child})
        check(profile + ':messages_complete_exact_model', len(messages) == expected_calls
              and all(m['finished'] == 1 and m['has_error'] == 0 and m['tokens_present'] == 1
                      and (m['provider_id'], m['model_id']) == ('custom-openai', 'qwen3.8-max') for m in messages))
        check(profile + ':tools_terminal_complete', len(tools) == expected_tools and all(t['status'] == 'completed' for t in tools))
        usage = load(trial / 'agent/session-usage.json')
        computed = totals(messages, tools)
        check(profile + ':full_usage_recomputed', usage['complete'] is True and usage['errors'] == [] and usage['total'] == computed)
        check(profile + ':root_usage_recomputed', usage['root'] == totals([m for m in messages if m['session_id'] == root], [t for t in tools if t['session_id'] == root]))
        check(profile + ':child_usage_recomputed', usage['descendants'] == totals([m for m in messages if m['session_id'] == child], [t for t in tools if t['session_id'] == child]))
        exported_tools = []
        finals = {}
        for sid in (root, child):
            export_path = trial / 'qa-session-exports' / (sid + '.json')
            ex = load(export_path)
            directory = trial / 'qa-evidence-v2' / ('export-' + sid)
            check(profile + ':native_export_bytes:' + sid, export_path.read_bytes() == (directory / 'stdout.json').read_bytes()
                  and (directory / 'status.txt').read_text().strip() == '0'
                  and (directory / 'stderr.txt').read_text().strip() == 'Exporting session: ' + sid)
            check(profile + ':export_identity:' + sid, ex['info']['id'] == sid and ex['info'].get('parentID') == (None if sid == root else root)
                  and ex['info']['directory'] == '/workspace' and ex['info']['version'] == '1.18.4')
            assistant = [m for m in ex['messages'] if m['info']['role'] == 'assistant']
            expected_messages = {m['id']: m for m in messages if m['session_id'] == sid}
            check(profile + ':export_message_ids:' + sid, set(expected_messages) == {m['info']['id'] for m in assistant})
            for message in assistant:
                info = message['info']; db = expected_messages[info['id']]; tokens = info['tokens']
                check(profile + ':export_message_usage:' + info['id'], info['providerID'] == db['provider_id'] and info['modelID'] == db['model_id']
                      and info['time'].get('completed') and not info.get('error') and tokens['input'] == db['input'] and tokens['output'] == db['output']
                      and tokens['reasoning'] == db['reasoning'] and tokens['cache']['read'] == db['cache_read'] and tokens['cache']['write'] == db['cache_write'])
                exported_tools.extend(p for p in message['parts'] if p['type'] == 'tool')
            last = assistant[-1]
            finals[sid] = ''.join(p['text'] for p in last['parts'] if p['type'] == 'text')
            check(profile + ':terminal_final:' + sid, last['info']['finish'] == 'stop'
                  and finals[sid] == (SENTINEL if sid == root else CHILD_SENTINEL))
        check(profile + ':export_tool_ids', {p['id'] for p in exported_tools} == {t['id'] for t in tools})
        for part in exported_tools:
            db = next(t for t in tools if t['id'] == part['id'])
            state = part['state']
            check(profile + ':export_tool_state:' + part['id'], state['status'] == db['status'] == 'completed'
                  and (part['tool'], part['callID'], part['sessionID'], part['messageID']) == (db['name'], db['call_id'], db['session_id'], db['message_id']))
            if part['tool'] == 'read':
                display = state['metadata']['display']
                expected_text = '\n'.join((source / 'README.md').read_text().splitlines()[:12])
                check(profile + ':source_only_read:' + part['id'], state['input'] == {'filePath': '/workspace/README.md', 'limit': 12}
                      and display['text'] == expected_text and display['lineStart'] == 1 and display['lineEnd'] == 12)
            elif part['tool'] == 'task':
                check(profile + ':source_only_child:' + part['id'], state['metadata']['sessionId'] == db['child_session_id'] == child
                      and state['input']['prompt'] == 'Read the first twelve lines of /workspace/README.md. This is an offline transport fixture.'
                      and CHILD_SENTINEL in state['output'])
            elif part['tool'] == 'zvec_grep_zvec_grep_search':
                check(profile + ':successful_actual_mcp', profile == 'zvec-grep' and state['input'] == {'root': '/workspace', 'query': QUERY}
                      and 'results: served_from_current_index' in state['output'] and 'freshness: fresh' in state['output'])
                mcp_window = state['time']
                current_path = None
                for line in state['output'].splitlines():
                    match = re.search(r'matchedBy=\w+ (.+?):\d+(?:-\d+)?$', line)
                    if match:
                        current_path = match[1]
                    match = re.match(r'^(\d+)\t(.*)$', line)
                    if match and current_path:
                        number, snippet = int(match[1]), match[2]
                        original_line = (source / current_path).read_text().splitlines()[number - 1]
                        # Search presentation strips first-line indentation and may
                        # truncate long lines with an explicit ellipsis.
                        same = original_line.lstrip() == snippet.lstrip()
                        if snippet.endswith('...'):
                            same = original_line.lstrip().startswith(snippet[:-3].lstrip())
                        check('mcp_source_line:' + current_path + ':' + str(number), same)
                        mcp_line_checks.append({'path': current_path, 'line': number, 'match': same})
            else:
                check(profile + ':unexpected_tool:' + part['tool'], False)
        stream = [json.loads(line) for line in (trial / 'agent/opencode.txt').read_text().splitlines() if line.strip()]
        final_event = [e for e in stream if e['type'] == 'text' and e['sessionID'] == root][-1]
        atif = load(trial / 'agent/trajectory.json')
        atif_final = [s for s in atif['steps'] if s['source'] == 'agent'][-1]['message']
        row = next(p for p in collection['profiles'] if p['profile'] == profile)
        native_row = native['profiles'][profile]['trials'][0]
        check(profile + ':final_text_agreement', final_event['part']['text'] == atif_final == row['final']['answer'] == native_row['answer'] == SENTINEL
              and row['final']['message_id'] == final_event['part']['messageID'] and row['final']['root_session_id'] == root
              and row['final']['answer_sha256'] == sha(SENTINEL.encode()))
        check(profile + ':final_db_completion_only', any(m['id'] == row['final']['message_id'] and m['finished'] == 1 and m['has_error'] == 0 for m in messages))
        check(profile + ':collector_full_tree_usage', all(row['metrics'][k] == v == native_row[k] for k, v in computed.items()))
        slot = next(s for s in execution['slots'] if s['profile'] == profile)
        check(profile + ':fresh_install_cache_and_cleanup', slot['install_cache_absent_before_prepare'] is True and slot['container_cleanup']['complete'] is True
              and slot['container_cleanup']['remaining_running'] == [] and slot['returncode'] == 0)
        containers = load(integration / 'run' / (profile + '-containers.json'))
        check(profile + ':real_container_observed', len(containers) == 1 and all(c['running_when_observed'] for c in containers.values()))
        results[profile] = {'root_session_id': root, 'child_session_id': child, 'recomputed_usage': computed,
                            'source_only_tool_scope_audit': 'passed', 'final_sentinel_confirmed': True,
                            'quality_evaluated': False, 'trial': trial.name}
        if profile == 'zvec-grep':
            setup = load(trial / 'agent/zvec-grep-setup.json')
            check('zg_actual_install_grant_index_mcp', setup['status'] == 'ready' and setup['install_reused_cache'] is False
                  and setup['index_cache_hit'] is False and setup['index_seed_available'] is False
                  and setup['package_sha256'] == INSTALL_SHA and setup['authorization_scope'] == 'workspace'
                  and '1,143 / 1,143 files' in setup['index_status'] and '22,224' in setup['index_status']
                  and 'Server: ready' in setup['mcp_server_status'])
            check('zg_exact_index_ignore', setup['index_ignore_sha256'] == digest(args.upstream_root / 'zg_bench/swe_qa/data/index.ignore'))

    requests = [json.loads(line) for line in (integration / 'fake-services/requests.jsonl').read_text().splitlines()]
    check('all_observed_stub_credentials_fake', all(r['authorization_is_exact_fixture'] is True for r in requests))
    check('only_known_stub_request_types', set(r['kind'] for r in requests) == {'chat', 'embedding'})
    for profile in ('baseline', 'zvec-grep'):
        chats = [r for r in requests if r['profile'] == profile and r['kind'] == 'chat']
        embeddings = [r for r in requests if r['profile'] == profile and r['kind'] == 'embedding']
        counts = Counter(r['stage'] for r in chats)
        expected_stages = {'background', 'root_read', 'root_child', 'child_read', 'child_final', 'root_final'} | ({'root_mcp'} if profile == 'zvec-grep' else set())
        check(profile + ':stub_stages_once', set(counts) == expected_stages and set(counts.values()) == {1})
        check(profile + ':persisted_and_background_distinct', len(chats) == results[profile]['recomputed_usage']['llm_calls'] + 1)
        contract = {'model': 'qwen3.8-max', 'temperature': 0, 'seed': 42, 'enable_thinking': True, 'reasoning_effort': 'high', 'max_tokens': 32000}
        check(profile + ':all_stub_request_contracts', all(all(r['body'].get(k) == v and type(r['body'].get(k)) is type(v) for k, v in contract.items())
              and 'response_format' not in r['body'] and r['path'] == '/v1/chat/completions'
              and not {'websearch', 'webfetch'} & {t['function']['name'] for t in r['body'].get('tools', [])} for r in chats))
        for request in chats:
            sid = results[profile]['child_session_id'] if request['stage'].startswith('child') else results[profile]['root_session_id']
            check(profile + ':stub_session_id:' + request['stage'], request['session_id'] == sid)
        check(profile + ':embedding_counts', len(embeddings) == (1409 if profile == 'zvec-grep' else 0)
              and sum(r['body_shape']['batch_size'] for r in embeddings) == (22225 if profile == 'zvec-grep' else 0))
        check(profile + ':embedding_contract', all(r['path'] == '/v1/embeddings' and r['body_shape']['dimensions'] == 1024
              and r['body_shape']['model'] == 'qwen3.7-text-embedding' and r['body_shape']['encoding_format'] == 'float'
              and 1 <= r['body_shape']['batch_size'] <= 20 and r['body_shape']['batch_size'] == len(r['body_shape']['inputs']) for r in embeddings))
        results[profile]['observed_stub_chat_requests'] = len(chats)
        results[profile]['background_chat_requests_excluded_from_persisted_usage'] = 1
        results[profile]['observed_embedding_batches'] = len(embeddings)
        results[profile]['observed_embedding_inputs'] = sum(r['body_shape']['batch_size'] for r in embeddings)
    queries = [r for r in requests if r['kind'] == 'embedding' and r['body_shape']['is_query_text']]
    check('single_query_embedding_during_actual_mcp', len(queries) == 1 and mcp_window['start'] <= queries[0]['at_unix'] * 1000 <= mcp_window['end']
          and queries[0]['body_shape']['inputs'] == [{'bytes': len(QUERY.encode()), 'sha256': sha(QUERY.encode())}])
    check('mcp_source_snippets_checked', len(mcp_line_checks) > 50 and all(r['match'] for r in mcp_line_checks))

    negative = load(integration / 'docker-negative-cases.json')
    for case in negative['cases']:
        directory = integration / 'docker-evidence' / case['label']
        capture = verify_capture(directory)
        check('negative_expected_result:' + case['label'], case['succeeded'] == case['expected_success'] == capture['valid'])
        if 'capture_sha256' in case:
            check('negative_capture_identity:' + case['label'], digest(directory / 'capture.json') == case['capture_sha256'])
    error_root = integration / 'docker-evidence'
    check('stderr_json_separate_valid', load(error_root / 'stderr-json/stdout.json') == {'ok': True}
          and (error_root / 'stderr-json/stderr.txt').read_text() == 'native warning\n'
          and (error_root / 'stderr-json/status.txt').read_text().strip() == '0')
    check('nonzero_valid_json_rejected', load(error_root / 'nonzero-json/stdout.json') == {'ok': True}
          and (error_root / 'nonzero-json/status.txt').read_text().strip() == '7'
          and load(error_root / 'nonzero-json/capture.json')['validation_error'] == 'command_status_not_zero')
    malformed = error_root / 'malformed-json/stdout.json'
    try:
        load(malformed)
        broken_json = False
    except json.JSONDecodeError:
        broken_json = True
    check('malformed_bytes_preserved_rejected', broken_json and malformed.stat().st_size > 0)
    timeout = load(error_root / 'timeout-partial/capture.json')
    check('timeout_partial_and_missing_status_retained', timeout['valid'] is False and timeout['timeout_seconds'] == 1
          and 'timed out' in timeout['transport']['error_message'] and timeout['files']['status.txt']['complete'] is False
          and (error_root / 'timeout-partial/stdout.json').read_bytes() == malformed.read_bytes())
    check('actual_docker_cleanup_and_uncertainty_policy', negative['backend'] == 'harbor.environments.docker.docker.DockerEnvironment'
          and negative['cleanup']['complete'] is True and negative['cleanup']['remaining_running'] == []
          and negative['uncertain_cleanup_policy']['next_slot_started'] is False
          and negative['uncertain_cleanup_policy']['injected_status']['complete'] is False)
    check('network_limitation_declared', proof['network_scope']['airgapped'] is False and proof['network_scope']['network_isolation_claimed'] is False)
    check('real_linux_harbor_versions', proof['system']['harbor_version'] == '0.18.0' and proof['system']['docker_architecture'] == 'x86_64'
          and proof['system']['os'].startswith('Linux-') and proof['system']['docker_server_version'] == '28.0.4')
    check('unit_checks_50_pass_no_skip', 'Ran 50 tests' in (raw / 'unit-tests.txt').read_text()
          and (raw / 'unit-tests.txt').read_text().rstrip().endswith('OK') and 'skipped' not in (raw / 'unit-tests.txt').read_text().lower())
    return {'profiles': results, 'mcp_source_lines_verified': len(mcp_line_checks), 'mcp_source_line_checks': mcp_line_checks,
            'run_event': run['event'], 'system': proof['system'], 'source_inventory_sha256': SOURCE_SHA}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-root', type=Path, default=BASE / 'repair-v2/revision-1-ci')
    parser.add_argument('--repo-root', type=Path, default=REPO)
    parser.add_argument('--source-root', type=Path, default=BASE / 'prepared-v2/tasks/zvec-q001/environment/source')
    parser.add_argument('--upstream-root', type=Path, default=BASE / 'upstream-main/benchmarks/swe-qa-bench')
    parser.add_argument('--original-tgz', type=Path, default=BASE / 'runtime/zg-native.tgz')
    parser.add_argument('--report', type=Path, default=BASE / 'repair-v2/independent-revision-1-ci-audit.json')
    args = parser.parse_args()
    result = audit(args)
    failures = [r for r in CHECKS if not r['passed']]
    report = {
        'schema_version': 1, 'scope': 'independent retained infrastructure evidence audit; no QA grading',
        'status': 'passed' if not failures else 'failed', 'run_id': 39314027006, 'artifact_id': 12216879282,
        'artifact_zip_sha256': ZIP_SHA, 'proof_sha256': PROOF_SHA, 'inputs_sha256': INPUT_SHA,
        'audit_script_sha256': digest(Path(__file__)), 'check_count': len(CHECKS), 'failure_count': len(failures),
        'findings': failures, 'results': result, 'checks': CHECKS,
        'limitations': [
            'No packet-level capture or enforced network isolation; native dependency/bootstrap/background traffic is not proven absent. Explicit provider config and all retained stub requests use local fake services/credentials.',
            'The DB snapshot records message completion/identity/usage, not answer text. Answer text equality is verified across native export, stream, ATIF, and collector; DB confirms that same message completed.',
            'One unpersisted background chat request per arm is outside native persisted session-tree totals. Stub totals include these requests; neither is a billable/provider quality observation.',
            'The uncertain-cleanup stop-next-slot policy is an injected controller branch. Timeout and container cleanup themselves ran through actual Harbor Docker.',
            'This is synthetic installation/index/MCP/evidence proof. Sentinel completion is not QA success, zg benefit, live-provider availability, or a stable benchmark result.',
            'Replay requires the retained original tgz, immutable repository files, pinned upstream export, and frozen q001 source path in addition to the CI artifact; paths are configurable CLI arguments.',
        ],
        'actions_this_audit': {'provider_calls': 0, 'network_calls': 0, 'CI_calls': 0, 'native_agent_runs': 0,
                               'repository_edits': 0, 'quality_grading': False},
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': report['status'], 'check_count': len(CHECKS), 'failure_count': len(failures),
                      'findings': failures, 'report': str(args.report), 'report_sha256': digest(args.report)}, indent=2))
    raise SystemExit(1 if failures else 0)


if __name__ == '__main__':
    main()
