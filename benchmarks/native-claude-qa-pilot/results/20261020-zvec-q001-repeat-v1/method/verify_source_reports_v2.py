"""Verify source-review identity, complete tool coverage and every file reference.

Revision 2 preserves explicit unknown source-scope evidence. It does not replace
the source review's semantic judgments or execute its methods.
"""
import argparse,datetime,hashlib,json
from pathlib import Path
B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot');W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');A=B/'native-zvec-q001-repeat-v1-audit';CI=B/'native-zvec-q001-repeat-v1-ci'
RUN=39364155852;HEAD='aa230d91eb426eb295cf95fe0fa2d80d52f588e2';POLICY='2233de3a9413bbad6c8af4a977c4da406213ac25674bb7b38e90b08fb4ac8d06'

def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def refcheck(record):
 p=Path(record['path']);real=p.resolve()
 assert not p.is_symlink() and p.is_file()
 assert any(root.resolve() in real.parents for root in (B,W))
 assert 'grading-private' not in str(p) and p.name!='assignment.json'
 assert p.stat().st_size==record['bytes'] and sha(p)==record['sha256']

def tri_all(values):
 values=list(values)
 assert values and all(v is None or type(v) is bool for v in values)
 if any(v is False for v in values):return False
 return True if all(v is True for v in values) else None

def verify(key):
 root=A/'source-reviews'/key;path=root/'report.json';r=load(path);v=load(root/'reference-verification.json')
 assert r['pair_id']==key and r['task_id']=='q001' and r['run_id']==RUN and r['head_sha']==HEAD
 assert r['policy']['sha256']==POLICY and r['source_file_count']==1232
 assert r['source_inventory_sha256']=='741d06d8fc216150933c5ed87f9a100e0d2120b2dc09a9109084365e92d307ba'
 assert r['quality_graded'] is False and r['resources_compared'] is False
 assert v['report_sha256']==sha(path) and v['all_match'] is True and not v['errors']
 assert len(v['references'])==v['checked_distinct_file_references']
 for p,record in v['references'].items():refcheck(dict(record,path=p))
 refcheck(r['artifact_verification']);receipt=load(CI/key/'artifact-byte-verification.json')
 assert r['artifact_verification']['sha256']==sha(CI/key/'artifact-byte-verification.json')
 assert receipt['complete'] is True and receipt['pair_key']==key and receipt['head_sha']==HEAD
 assert {p['profile'] for p in r['profiles']}=={'baseline','zvec-grep'} and len(r['profiles'])==2
 checked=0;profiles=[]
 for p in r['profiles']:
  name=p['profile'];extract=load(A/'scope'/key/name/'tool-audit.json')
  assert extract['extraction_complete'] is True and not extract['errors']
  assert p['accounting_complete'] is True and p['all_tool_identities_equal_frozen_v2'] is True
  original={(t['session'],t['id']):t for t in extract['tools']};assert len(original)==len(extract['tools'])==p['tool_count']
  reviews=[];seen=set()
  for reference in p['per_tool_reviews']:
   refcheck(reference);tool=load(Path(reference['path']));identity=(tool['session'],tool['tool_id']);assert identity not in seen;seen.add(identity)
   expected=original[identity];assert len(expected['call_variants'])==len(expected['result_variants'])==1
   call,result=expected['call_variants'][0],expected['result_variants'][0]
   assert tool['ordinal']==expected['ordinal'] and tool['name']==expected['name']
   assert tool['input']==call['input'] and tool['input_sha256']==call['input_sha256']
   assert tool['result_block_sha256']==result['block_sha256'] and tool['is_error']==result['is_error']
   assert tool['quality_graded'] is False
   refcheck(tool['tool_evidence']);assert Path(tool['tool_evidence']['path']).resolve()==(A/'scope'/key/name/expected['evidence_path']).resolve()
   reviews.append(tool)
  assert seen==set(original) and len(reviews)==p['tool_count']
  for field in ('strict_source_scope_valid','factual_evidence_scope_valid'):
   assert (p[field] is None or type(p[field]) is bool) and p[field] is tri_all(t[field] for t in reviews)
  for field in ('external_content_observed','external_code_or_answer_facts_observed'):
   assert type(p[field]) is bool and p[field]==any(t[field] is True for t in reviews)
  assert p['source_unchanged'] is True and isinstance(p['unresolved_evidence'],list)
  unknown=any(t.get('strict_source_scope_valid') is None or t.get('factual_evidence_scope_valid') is None for t in reviews)
  if unknown:assert p['unresolved_evidence'] and p.get('strict_scope_gate_pass') is False
  else:assert not p['unresolved_evidence']
  if 'strict_scope_gate_pass' in p:assert p['strict_scope_gate_pass'] is (p['strict_source_scope_valid'] is True and not p['unresolved_evidence'])
  checked+=len(reviews);profiles.append({k:p[k] for k in ('profile','tool_count','strict_source_scope_valid','factual_evidence_scope_valid','external_content_observed','external_code_or_answer_facts_observed','source_unchanged','relevant_correct_workspace_zg_retrieval','unresolved_evidence')})
 assert checked==r['total_unique_tool_calls']
 assert r['strict_source_scope_valid'] is tri_all(p['strict_source_scope_valid'] for p in profiles)
 assert r['factual_evidence_scope_valid'] is tri_all(p['factual_evidence_scope_valid'] for p in profiles)
 return {'pair_key':key,'report_sha256':sha(path),'references_rehashed':len(v['references']),'actual_tools_fully_covered':checked,'profiles':profiles,'semantic_review_replaced':False}

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--keys',nargs='+',required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 result={'schema_version':1,'verified_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'run_id':RUN,'head_sha':HEAD,'reports':[verify(k) for k in args.keys],'quality_or_benefit_evaluated':False,'unknown_handling':'unknown retained explicitly; never treated as gate pass','method_sha256':sha(Path(__file__))}
 with args.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
 print(json.dumps({'pairs_verified':len(result['reports']),'references_rehashed':sum(r['references_rehashed'] for r in result['reports']),'actual_tools_fully_covered':sum(r['actual_tools_fully_covered'] for r in result['reports']),'quality_or_benefit_evaluated':False}))
