"""Rehash the completed independent execution reports and original references."""
import datetime, hashlib, json
from pathlib import Path

B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
ROOT=B/'native-q004-repeat-v1-audit'
def sha(p):
    digest=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1048576), b''):digest.update(chunk)
    return digest.hexdigest()
def load(p):return json.loads(p.read_text())

records=[];references=0;checks=0
for i in range(1,11):
    key=f'q004-r{i:02d}';path=ROOT/'execution/pairs'/key/'execution-review.json'
    r=load(path);ci=B/'native-q004-repeat-v1-ci/39380248003'/key;receipt=load(ci/'artifact-byte-verification.json')
    assert r['run_id']==39380248003 and r['head_sha']=='3978d4bbbbb1e37961c277cdf76696b84c69e3bc' and r['run_attempt']==1
    assert r['pair_key']==key and r['trial_index']==i and r['plan_sha256']=='c2d102654a6f11ab330f843c501b1820c0c7385595e0fdb1d69635880f218c76'
    assert r['artifact_sha256']==receipt['artifact_sha256'] and r['artifact_id']==receipt['artifact_id'] and r['job_id']==receipt['job_id']
    assert r['passed'] is True and r['check_count']==73 and r['passed_checks']==73 and not r['failed_checks']
    assert len(r['checks'])==73 and all(c['passed'] is True for c in r['checks'])
    for location, record in r['evidence_catalog'].items():
        p=Path(location);assert str(p)==record['path'] and p.is_file() and not p.is_symlink()
        assert sha(p)==record['sha256'] and p.stat().st_size==record['bytes'];references+=1
    for check in r['checks']:
        assert check['evidence'] and all(p in r['evidence_catalog'] for p in check['evidence'])
    assert {p['profile'] for p in r['profiles']}=={'baseline','zvec-grep'}
    for p in r['profiles']:
        assert p['complete_review'] and p['strict_complete'] and p['reported_metric_eligible']
        assert set(p['source_inventory_counts'])=={'source-after','source-before'}
        assert all(type(n) is int and n>=1596 for n in p['source_inventory_counts'].values())
        if p['profile']=='baseline':assert p['source_inventory_counts']=={'source-after':1596,'source-before':1596}
        u=p['primary_usage'];assert u['input_tokens']==u['uncached_input_tokens']+u['cache_read_tokens']+u['cache_write_tokens']
    checks+=len(r['checks'])
    records.append({'pair_key':key,'path':str(path),'sha256':sha(path),'catalog_references_rehashed':len(r['evidence_catalog']),'checks':len(r['checks'])})
report={'schema_version':1,'verified_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'passed':True,
    'pairs_verified':10,'original_checks_verified':checks,'catalog_reference_occurrences_rehashed':references,
    'private_assignment_or_quality_grades_accessed':False,'source_semantics_replaced':False,
    'scope':'Origin, report integrity, every catalog file byte/hash, check reference inclusion, complete usage arithmetic and full capture inventory counts (zg includes predeclared generated runtime/index artifacts). Frozen 1596 source-file immutability is verified separately by the original source audit. This does not replace independent execution-method or source/quality semantic review.',
    'method_sha256':sha(Path(__file__)),'reports':records}
out=ROOT/'execution-parent-verification.json'
with out.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
print(json.dumps({k:report[k] for k in ['passed','pairs_verified','original_checks_verified','catalog_reference_occurrences_rehashed']}))
