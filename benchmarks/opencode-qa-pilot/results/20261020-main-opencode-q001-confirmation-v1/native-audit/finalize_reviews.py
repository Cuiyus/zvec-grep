"""Materialize completed manual scope/relevance review; no native tools are replayed.

All c02 tool inputs, Bash output boundaries, full MCP output, and all native
read displays were inspected before this script was written. This script binds
those decisions to immutable exports and applies the unchanged final auditor.
"""
from pathlib import Path
import collections
import copy
import hashlib
import json
import re
import shlex
import subprocess

BASE = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
HERE = Path(__file__).resolve().parent
CI = BASE / 'confirmation-v1-ci'
PROTOCOL = 'main-opencode-q001-confirmation-v1'
RUN = 39321551973
HEAD = '10348d1c2c863fc498f71e1dd76f04835aa92153'
AUDITOR_SHA = 'f054253feb63254cb6d0fc89181b92dda3e7f5ff385bd21a53f5b53a6a42e2b0'

def load(p):
    return json.loads(p.read_text())

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def digest(b):
    return hashlib.sha256(b).hexdigest()

def write(p, data):
    with p.open('x') as f:
        json.dump(data, f, indent=2)
        f.write('\n')

def write_bytes(p, data):
    with p.open('xb') as f:
        f.write(data)

def exports(repeat):
    rows = {}
    for f in sorted((CI / repeat / 'raw/qa-confirmation-run/runs').glob('*/*/qa-session-exports/*.json')):
        d = load(f)
        for m in d['messages']:
            for p in m['parts']:
                if p['type'] == 'tool':
                    assert p['id'] not in rows
                    rows[p['id']] = (p, f)
    return rows

def header(repeat, audit, queue):
    return dict(schema_version=1, protocol_id=PROTOCOL, repeat=repeat,
                run_id=RUN, head=HEAD, artifact_id=audit['artifact_binding']['artifact_id'],
                artifact_zip_sha256=audit['artifact_binding']['integrity']['sha256'],
                tool_queue_sha256=queue['tool_queue_sha256'],
                evidence_status='final_artifact_and_completed_global_provenance_verified',
                quality_evaluated=False)

def pure_cat_sed_output(command, source):
    """Compare simple recorded cat/sed reads without executing any trace command."""
    words = shlex.split(command)
    if words[:3] == ['cd', '/workspace', '&&']:
        words = words[3:]
    units, unit, joins = [], [], []
    for w in words:
        if w in ('&&', '|'):
            units.append(unit); unit = []; joins.append(w)
        else:
            unit.append(w)
    units.append(unit)
    if any(not u or u[0] not in ('cat', 'sed') for u in units):
        return None
    def local(p):
        if p.startswith('/workspace/'):
            p = p[len('/workspace/'):]
        if Path(p).is_absolute() or '..' in Path(p).parts:
            raise ValueError('outside source')
        return source / p
    output = ''
    for i, unit in enumerate(units):
        incoming = output if i and joins[i-1] == '|' else None
        if unit[0] == 'cat' and incoming is None and all(not s.startswith('-') for s in unit[1:]):
            fragment = ''.join(local(p).read_text() for p in unit[1:])
        elif unit[0] == 'sed' and len(unit) in (3, 4) and unit[1] == '-n':
            match = re.fullmatch(r'(\d+),(\d+)p', unit[2])
            if not match:
                return None
            if len(unit) == 4:
                if incoming is not None:
                    return None
                text = local(unit[3]).read_text()
            else:
                if incoming is None:
                    return None
                text = incoming
            fragment = ''.join(text.splitlines(keepends=True)[int(match[1])-1:int(match[2])])
        else:
            return None
        output = fragment if incoming is not None else output + fragment
    return output

assert sha(HERE / 'audit.py') == AUDITOR_SHA
final_scope = {}

# Reuse c01 only after byte binding both artifacts, all files, and the exact queue.
interim = BASE / 'confirmation-v1-interim/c01'
assert (interim / 'artifact.zip').read_bytes() == (CI / 'c01/artifact.zip').read_bytes()
old_files = {p.relative_to(interim / 'raw'): p for p in (interim / 'raw').rglob('*') if p.is_file()}
new_files = {p.relative_to(CI / 'c01/raw'): p for p in (CI / 'c01/raw').rglob('*') if p.is_file()}
assert set(old_files) == set(new_files) and len(old_files) == 88
assert all(old_files[n].read_bytes() == new_files[n].read_bytes() for n in old_files)
assert (HERE / 'c01/interim-machine-pass/tool-review-queue.json').read_bytes() == (HERE / 'c01/machine-pass/tool-review-queue.json').read_bytes()
q1 = load(HERE / 'c01/machine-pass/tool-review-queue.json')
a1 = load(HERE / 'c01/machine-pass/audit.json')
assert a1['provenance_valid'] is True and not a1['errors']
s1path = HERE / 'c01/interim-manual-review/tool-scope-review.json'
s1 = load(s1path)
assert s1['tool_queue_sha256'] == q1['tool_queue_sha256']
assert set(s1['decisions']) == {x['id'] for x in q1['tools']}
e1 = exports('c01')
for t in q1['tools']:
    d = s1['decisions'][t['id']]
    assert d['input_sha256'] == t['input_sha256'] and d['output_sha256'] == t['output_sha256']
    assert d['native_export_sha256'] == sha(e1[t['part_id']][1])
s1.update(header('c01', a1, q1))
s1['final_protocol_valid'] = None  # The separate unchanged auditor decides this.
s1['reuse_binding'] = dict(interim_review_sha256=sha(s1path),
    interim_queue_file_sha256=sha(HERE / 'c01/interim-machine-pass/tool-review-queue.json'),
    final_queue_file_sha256=sha(HERE / 'c01/machine-pass/tool-review-queue.json'),
    artifact_zip_bytes_equal=True, all_88_raw_files_bytes_equal=True,
    queue_bytes_equal=True, all_tool_input_output_export_hashes_equal=True,
    parent_receipt_sha256=sha(CI / 'c01-interim-binding.json'))
s1['limitations'] = [x for x in s1['limitations'] if 'Final provenance' not in x]
s1['limitations'].append('The review is reused solely for identical retained c01 bytes; the final auditor independently checks completed global provenance.')
write(HERE / 'c01/tool-scope-review.json', s1)
final_scope['c01'] = HERE / 'c01/tool-scope-review.json'

# c02: every input and shell pipeline was read manually, with no observed violation.
a2 = load(HERE / 'c02/machine-pass/audit.json')
q2 = load(HERE / 'c02/machine-pass/tool-review-queue.json')
assert a2['provenance_valid'] is True and not a2['errors']
assert len(q2['tools']) == 156
source = BASE / 'confirmation-v1-local/c02/prepared/tasks/zvec-q001/environment/source'
e2 = exports('c02')
decisions, inspection, direct_matches = {}, [], []
for order, t in enumerate(q2['tools']):
    p, f = e2[t['part_id']]
    assert p['state']['status'] == 'completed'
    assert digest(p['state']['output'].encode()) == t['output_sha256']
    out = p['state']['output']; command = t['input'].get('command', '')
    obs = dict(tool_id=t['id'], part_id=t['part_id'], input=t['input'],
        input_sha256=t['input_sha256'], output_sha256=t['output_sha256'], native_export_sha256=sha(f),
        status=t['status'], flags=t['flags'], output_bytes=len(out.encode()))
    if t['tool'] == 'bash':
        if re.search(r'\bgit\b', command):
            rationale = ('Reviewed local git probe in /workspace. No repository history was obtained; '
                'unsuppressed probes report not a git repository and the suppressed probe returns only '
                'the requested listing/label. Pipeline status 0 is not evidence of git success. '
                'There is no clone, fetch, pull or network command. ')
        else:
            rationale = ('Reviewed the complete shell input, including every pipeline/chain, and its '
                'output boundaries. Commands only list/count/search/read local frozen source using '
                'ls/find/wc/grep/sed/cat/head and echo labels; no source/test execution, source write, '
                'private answer read or external web/network command. ')
        if t['flags']:
            assert t['flags'] == ['possible_write_command']
            rationale += ('The static write flag is a > inside a quoted C++ search expression or '
                'stderr suppression to /dev/null; it does not write source. ')
        if order in (85, 86):
            rationale += ('The directory listing incidentally shows .zvec-grep names created by '
                'the authorized index; no index-file content or private benchmark artifact is read. ')
        rationale += 'Command: ' + command
        lines = out.splitlines()
        obs.update(output_first_lines=lines[:3], output_last_lines=lines[-3:],
            output_line_count=len(lines), exit_code=p['state'].get('metadata', {}).get('exit'),
            tool_output_truncated=p['state'].get('metadata', {}).get('truncated'))
        expected = pure_cat_sed_output(command, source)
        if expected is not None:
            assert expected == out, t['id']
            direct_matches.append(t['id'])
            obs['full_output_exact_pure_source_read'] = True
    elif t['tool'] == 'read':
        display = p['state']['metadata']['display']
        sf = source / display['path'].removeprefix('/workspace/')
        expected = '\n'.join(sf.read_text().splitlines()[display['lineStart']-1:display['lineEnd']])
        assert expected == display['text']  # No strip or normalization for this additional check.
        rationale = ('Read is confined to the named frozen source file. The entire display text '
            'exactly equals its frozen line window, with no whitespace normalization. No external '
            'source, private artifact or file mutation. Path: ' + display['path'])
        obs.update(display_range=[display['lineStart'], display['lineEnd']],
            display_text_exact=True, frozen_source_file_sha256=sha(sf))
    else:
        assert t['tool'] == 'zvec_grep_zvec_grep_search'
        rationale = ('Reviewed the actual completed MCP request and entire 12-result output. It '
            'uses /workspace and current fresh local index, returns frozen source paths and code, '
            'including the flush early-out. It makes no external web retrieval. The query is one '
            'JSON-array-shaped string and is retained as one call, not three separate queries.')
    decisions[t['id']] = dict(source_only=True, no_web=True, rationale=rationale,
        review_order=order, input_sha256=t['input_sha256'], output_sha256=t['output_sha256'],
        native_export_sha256=sha(f))
    inspection.append(obs)
counts = {profile:dict(collections.Counter(t['tool'] for t in q2['tools'] if t['profile']==profile))
          for profile in ('baseline', 'zvec-grep')}
assert counts == {'baseline':{'bash':85}, 'zvec-grep':{'bash':37,'read':33,'zvec_grep_zvec_grep_search':1}}
scope2 = header('c02', a2, q2)
scope2.update(kind='manual_retained_tool_scope_review', final_protocol_valid=None,
    queue_file_sha256=sha(HERE / 'c02/machine-pass/tool-review-queue.json'), decisions=decisions,
    coverage=dict(baseline_tools=85, treatment_tools=71, root_sessions=2, child_sessions=0,
        by_profile_tool_name=counts, read_display_segments_exact=33,
        bash_cat_sed_full_outputs_exact=len(direct_matches), all_session_tree_tools_reviewed=True,
        violations_observed=0),
    method='All inputs/pipelines and Bash output boundaries manually inspected; all 33 read displays exactly compared; full MCP result reviewed. Simple cat/sed outputs additionally reconstructed from frozen files without executing transcript commands.',
    limitations=['Retained tools only; not network packet isolation or unpersisted provider-request audit.',
        'DB snapshots independently confirm both root-only trees; zero children is checked, not assumed.',
        'Local git probes failed; completed Bash or pipeline status must not be interpreted as successful git history retrieval.'])
write(HERE / 'c02/tool-scope-review.json', scope2)
write(HERE / 'c02/manual-inspection.json', dict(tool_queue_sha256=q2['tool_queue_sha256'],
    source_only_decisions_exclude_quality=True, tools=inspection,
    pure_cat_sed_exact_tool_ids=direct_matches))
final_scope['c02'] = HERE / 'c02/tool-scope-review.json'

# Apply the unchanged auditor to new final output directories.
finals = {}
for row in load(HERE / 'invocations.json'):
    repeat = row['repeat']; command = list(row['command'])
    command[command.index('--output')+1] = str(HERE / repeat / 'final')
    command += ['--tool-review', str(final_scope[repeat])]
    done = subprocess.run(command, text=True, capture_output=True)
    write(HERE / repeat / 'final-invocation.json', dict(command=command, returncode=done.returncode,
        stdout=done.stdout, stderr=done.stderr, auditor_sha256=sha(HERE / 'audit.py')))
    assert done.returncode == 0, done.stderr or done.stdout
    final = load(HERE / repeat / 'final/audit.json')
    assert final['provenance_valid'] is True and final['protocol_valid'] is True and not final['errors']
    finals[repeat] = final

# c01 relevance is reused only after all exact bindings and the final audit pass.
m1path = HERE / 'c01/interim-manual-review/mcp-relevance-review.json'
m1 = load(m1path); m1.update(header('c01', a1, q1))
m1['final_protocol_valid'] = True
m1['final_audit_sha256'] = sha(HERE / 'c01/final/audit.json')
m1['reuse_binding'] = dict(s1['reuse_binding'], interim_relevance_review_sha256=sha(m1path))
for call in m1['calls']:
    old = HERE / 'c01/interim-manual-review' / call['output_file']
    assert sha(old) == call['output_sha256']
    write_bytes(HERE / 'c01' / call['output_file'], old.read_bytes())
write(HERE / 'c01/mcp-relevance-review.json', m1)

# c02 MCP manual relevance: exact source evidence, all preview exceptions retained.
m2 = header('c02', a2, q2)
calls = []
for t in q2['tools']:
    if t['tool'] != 'zvec_grep_zvec_grep_search':
        continue
    p, f = e2[t['part_id']]; out = p['state']['output']
    output_name = 'mcp-' + p['id'] + '.txt'
    write_bytes(HERE / 'c02' / output_name, out.encode())
    blocks = {}
    for block in re.split(r'(?m)(?=^#\d+[ \[])', out):
        h = re.search(r'^#(\d+)[^\n]*? ([^\s]+):(\d+)(?:-(\d+))?$', block, re.M)
        if h:
            blocks[int(h[1])] = dict(path=h[2], text=block,
                definition_range=[int(h[3]), int(h[4] or h[3])])
    assert len(blocks) == 12
    exceptions = copy.deepcopy(t['retrieval']['unverified_lines'])
    for x in exceptions:
        block = blocks[x['rank']]
        rendered = re.search(r'^'+str(x['line'])+r'\t(.*)$', block['text'], re.M)[1]
        frozen = (source/x['path']).read_text().splitlines()[x['line']-1]
        x.update(rendered=rendered, frozen_source=frozen)
        if rendered != frozen and rendered == frozen.lstrip():
            x['resolution'] = 'Leading indentation removed from preview first line; not counted as exact.'
        else:
            assert x['path']=='src/db/index/storage/wal/wal_file.h' and rendered=='}' and frozen=='};'
            x['resolution'] = 'Preview omits trailing struct-declaration semicolon; not counted as exact.'
    relevant = []
    reasons = {2:'Directly displays flush returning OK when member wal_file_ is null or has no records, the critical persistence guard in the requested lifecycle.',
        3:'Shows the local WAL open helper differentiating creation from opening an existing log, relevant to the recovery/flush lifecycle.',
        4:'Displays the collection recovery entry and version-recovery cue at the exact frozen source path.',
        5:'Displays the crash-during-optimize regression-test entry, relevant to investigating test coverage of the requested sequence.'}
    for rank, why in reasons.items():
        block = blocks[rank]
        matches = [x for x in t['retrieval']['exact_nonblank_source_lines'] if x['rank']==rank]
        assert matches
        relevant.append(dict(rank=rank,path=block['path'],definition_range=block['definition_range'],
            display_range=[min(x['line'] for x in matches),max(x['line'] for x in matches)],
            exact_nonblank_lines=[x['line'] for x in matches],
            frozen_source_file_sha256=sha(source/block['path']),relevance=why))
    follow = [x for x in q2['tools'] if x['part_id'] in
        ['prt_15616bcb9001ne03ytSYT3AfVs','prt_15616c11f001uK54jym2t5Kg37','prt_156194bf3001CLd9HYJ1CibZ00']]
    calls.append(dict(tool_id=t['id'],part_id=t['part_id'],call_id=t['call_id'],profile=t['profile'],
        request=t['input'], input_sha256=t['input_sha256'], output_sha256=t['output_sha256'],
        output_file=output_name,output_bytes=t['output_bytes'],native_export_sha256=sha(f),
        status=t['status'],completed=True,correct_workspace=True,manual_relevance=True,
        successful_relevant_correct_workspace=True,freshness='fresh',returned_result_blocks=12,
        exact_nonblank_source_lines=len(t['retrieval']['exact_nonblank_source_lines']),
        source_paths_all_in_frozen_tree=all((source/x['path']).is_file() for x in blocks.values()),
        preview_nonexact_lines=exceptions,relevant_results=relevant,
        query_shape_note='Exactly one string containing JSON-array-shaped text with three phrases; retained output labels groups Q1. Counted as one tool call, without inferring separate query execution.',
        observed_followup=[dict(tool_id=x['id'],input=x['input'],input_sha256=x['input_sha256'],output_sha256=x['output_sha256']) for x in follow],
        limitations=['Partial function previews are not full function bodies.',
            'Relevant retrieval and subsequent reads do not establish causal contribution or answer quality.']))
m2.update(kind='manual_mcp_relevance_review', final_protocol_valid=True,
    final_audit_sha256=sha(HERE / 'c02/final/audit.json'),total_search_calls=len(calls),
    successful_relevant_correct_workspace_calls=sum(c['successful_relevant_correct_workspace'] for c in calls),
    calls=calls,decision_scope='Actual retained MCP calls only. No retrieval would fail the separate retrieval condition, not automatically protocol validity. Wrong-root or failed terminal searches would remain recorded behavior.',
    final_candidate_decision=None)
write(HERE / 'c02/mcp-relevance-review.json', m2)

summary = dict(protocol_id=PROTOCOL,run_id=RUN,head=HEAD,auditor_sha256=sha(HERE/'audit.py'),
    quality_evaluated=False,confirmation_success_evaluated=False,development_pair_pooled=False,
    repeats={repeat:dict(provenance_valid=f['provenance_valid'],protocol_valid=f['protocol_valid'],
        audit_path=str(HERE/repeat/'final/audit.json'),audit_sha256=sha(HERE/repeat/'final/audit.json'),
        relevance_review_sha256=sha(HERE/repeat/'mcp-relevance-review.json'),
        successful_relevant_correct_workspace_calls=load(HERE/repeat/'mcp-relevance-review.json')['successful_relevant_correct_workspace_calls'],
        profiles={p['profile']:dict(session_ids=p['session_ids'],usage=p['usage']['total'],
            answer_sha256=p['terminal_answer']['answer_sha256']) for p in f['profiles']}) for repeat,f in finals.items()})
write(HERE/'completion.json',summary)
print(json.dumps(summary,indent=2))
