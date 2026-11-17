"""Export and compare native FTS after the public benchmark has stopped its daemons."""
import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import urllib.parse

p = argparse.ArgumentParser()
p.add_argument('--suite', choices=['sweqa', 'quarry'], required=True)
p.add_argument('--evidence', type=pathlib.Path, required=True)
p.add_argument('--binary', required=True)
p.add_argument('--output', type=pathlib.Path, required=True)
args = p.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
groups = sorted(args.evidence.rglob('runtime-home'))
assert len(groups) == (11 if args.suite == 'sweqa' else 20), len(groups)
manifest = []
native_library = pathlib.Path(os.environ['ZVEC_LIB_DIR']) / ('libzvec_c_api.dylib' if os.uname().sysname == 'Darwin' else 'libzvec_c_api.so')
native_sha256 = hashlib.sha256(native_library.read_bytes()).hexdigest()
for home in groups:
    evidence = home.parent
    queries = []
    roots = set()
    if args.suite == 'sweqa':
        calls = [json.loads(s) for s in (evidence / 'requests.jsonl').read_text().splitlines()]
        for call in calls:
            if call['mode'] == 'fts' and call['repetition'] == 1:
                query = call['request']['arguments']['fts']
                assert len(query) == 1
                queries.append({'id': call['task_id'], 'text': query[0]})
                roots.add(call['request']['arguments']['root'])
    else:
        for raw in sorted((evidence / 'raw').glob('*-fts-1.json')):
            item = json.loads(raw.read_text())
            query = item['request']['arguments']['fts']
            assert len(query) == 1
            tid = urllib.parse.unquote(raw.name[:-len('-fts-1.json')])
            queries.append({'id': tid, 'text': query[0]})
            roots.add(item['request']['arguments']['root'])
    assert queries, evidence
    assert len(roots) == 1, roots
    corpus_root = pathlib.Path(next(iter(roots)))
    collections = sorted(x for x in (corpus_root / '.zvec-grep').rglob('fragments_*')
                         if x.is_dir() and list(x.glob('manifest.*')))
    assert len(collections) == 1, (corpus_root, collections)
    directory = args.output / evidence.name
    directory.mkdir()
    spec = {'collection': str(collections[0]), 'queries': queries, 'output': str(directory)}
    spec_path = directory / 'spec.json'
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + '\n')
    # Byte hashes exclude no files: the read-only native export must not mutate its source collection.
    def inventory():
        return {str(f.relative_to(collections[0])): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in collections[0].rglob('*') if f.is_file()}
    before = inventory()
    with (directory / 'run.log').open('w') as log:
        subprocess.run([args.binary, str(spec_path)], check=True, stdout=log, stderr=subprocess.STDOUT)
    after = inventory()
    assert before == after, f'source collection changed: {evidence.name}'
    fragment_hash = hashlib.sha256((directory / 'fragments.jsonl').read_bytes()).hexdigest()
    native = json.loads((directory / 'manifest.json').read_text())
    row = {'group': evidence.name, 'queries': [q['id'] for q in queries],
           'fragments_sha256': fragment_hash, 'source_collection_unchanged': True,
           'source_collection_file_count': len(before), 'native_library_sha256': native_sha256,
           'native': native}
    manifest.append(row)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(f'{evidence.name}: {native["fragments"]} fragments, {len(queries)} queries', flush=True)
assert sum(len(x['queries']) for x in manifest) == 20
