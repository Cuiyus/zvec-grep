"""Copy exact final answers using the pre-execution anonymous assignment."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json

BASE = Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
PRIVATE = BASE / 'native-expansion-v1-grading-private'
PLAN_SHA = '275deb11a9212d7fcc5cb849b2a478d3fda82553a1075f496b7d038f613a1067'
ASSIGNMENT_SHA = 'f8a11310a04d7a6ed4781e41e79f3e0951052c8d0a89c88db1c379a346bf0bb0'

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('task', choices=['q002', 'q003'])
    task = parser.parse_args().task
    assignment_bytes = (PRIVATE / 'assignment.json').read_bytes()
    assert digest(assignment_bytes) == ASSIGNMENT_SHA
    assignment = json.loads(assignment_bytes)
    assert assignment['plan_sha256'] == PLAN_SHA
    assert assignment['created_before_answers'] and assignment['created_before_dispatch']
    mapping = assignment['assignment']
    assert {(x['task'], x['profile']) for x in mapping.values()} == {
        (t, p) for t in ['q002', 'q003'] for p in ['baseline', 'zvec-grep']}
    assert len(mapping) == 4
    raw = BASE / 'native-expansion-v1-ci' / task / 'raw'
    context = json.loads((raw / 'live-expansion-context.json').read_text())
    assert context['plan_sha256'] == PLAN_SHA and context['task_id'] == task
    assert context['dry_run'] is False
    collection = raw / 'live/collection'
    report = json.loads((collection / 'collection.json').read_text())
    assert report['plan_sha256'] == PLAN_SHA
    profiles = {x['profile']: x for x in report['profiles']}
    assert len(report['profiles']) == 2 and set(profiles) == {'baseline', 'zvec-grep'}
    packet = BASE / 'native-expansion-v1-blind-packet' / task
    records, private_records, payloads = [], [], []
    for anonymous_id, selected in sorted(mapping.items()):
        if selected['task'] != task:
            continue
        profile = selected['profile']
        entry = profiles[profile]
        source = collection / (profile + '-answer.txt')
        assert entry['answer_file'] == source.name and entry['answer_present']
        answer = source.read_bytes()
        assert answer and digest(answer) == entry['answer_sha256']
        filename = anonymous_id + '.txt'
        record = dict(id=anonymous_id, file=filename, sha256=digest(answer), bytes=len(answer))
        records.append(record)
        private_records.append(dict(record, source=str(source), task=task, profile=profile))
        payloads.append((filename, answer))
    assert len(records) == 2
    packet.mkdir(parents=True, exist_ok=False)
    for filename, answer in payloads:
        (packet / filename).write_bytes(answer)
    manifest = dict(schema_version=1, task_id=task, answers=records,
        method='Exact-byte final answers; anonymous IDs assigned before execution. Text is unredacted, including any self-disclosure. No condition labels, trajectories or resource metrics are supplied.')
    manifest_path = packet / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    receipt = dict(created_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        assignment_sha256=ASSIGNMENT_SHA, plan_sha256=PLAN_SHA,
        manifest_sha256=digest(manifest_path.read_bytes()), answers=private_records)
    with (PRIVATE / (task + '-packet-construction.json')).open('x') as handle:
        json.dump(receipt, handle, indent=2)
        handle.write('\n')
    print(json.dumps(dict(task=task, answer_count=len(records), packet=str(packet),
        manifest_sha256=receipt['manifest_sha256'])))

if __name__ == '__main__':
    main()
