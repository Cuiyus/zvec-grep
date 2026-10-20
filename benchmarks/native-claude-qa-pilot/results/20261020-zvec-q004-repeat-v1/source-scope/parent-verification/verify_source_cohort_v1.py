"""Verify complete source-review identities/coverage while preserving tri-state scope."""
import datetime, hashlib, json
from pathlib import Path

B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
ROOT=B/'native-q004-repeat-v1-audit'
SOURCE=ROOT/'source-scope/pairs'
def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def tri_all(values):
    values=list(values);assert values and all(v is None or type(v) is bool for v in values)
    return False if any(v is False for v in values) else True if all(v is True for v in values) else None

def verify():
    paths=[SOURCE/f'q004-r{i:02d}'/'report.json' for i in range(1,11)]
    if not all(p.is_file() for p in paths):
        print(json.dumps({'ready':False,'source_reviews_saved':sum(p.is_file() for p in paths),'required':10,'partial_reviews_promoted':False}));return
    references={};rows=[];reports=[];tools_total=0;occurrences=0
    def refcheck(r):
        p=Path(r['path']);assert p.is_file() and not p.is_symlink() and any(root.resolve() in p.resolve().parents for root in (B,W))
        assert 'grading-private' not in str(p) and p.name!='assignment.json'
        assert sha(p)==r['sha256'] and p.stat().st_size==r['bytes']
        references[str(p)]={'sha256':r['sha256'],'bytes':r['bytes']}
    def refs(node):
        if isinstance(node,dict):
            if {'path','sha256','bytes'}<=set(node) and isinstance(node['path'],str):refcheck(node)
            for v in node.values():refs(v)
        elif isinstance(node,list):
            for v in node:refs(v)
    for i,path in enumerate(paths,1):
        key=f'q004-r{i:02d}';r=load(path);assert r['pair_key']==key and r['run_id']==39380248003 and r['head_sha']=='3978d4bbbbb1e37961c277cdf76696b84c69e3bc' and r['attempt']==1
        assert r['status'].startswith('review_complete') and r['frozen_policy']['sha256']=='2233de3a9413bbad6c8af4a977c4da406213ac25674bb7b38e90b08fb4ac8d06'
        assert r['source_file_count']==1596 and r['source_inventory_sha256']=='129579aae937735ed79c444bd6f83a05e42058a679e0077ff39599c9ce5d4eef'
        assert r['final_answers_grades_private_assignments_or_packets_read'] is False and r['quality_or_resource_comparison_performed'] is False
        refs(r);reviewed={}
        for reference in r['reviewed_tools']:
            refcheck(reference);tool=load(Path(reference['path']));refs(tool)
            identity=(tool['session'],tool['tool_id']);assert identity not in reviewed;reviewed[identity]=tool
            assert tool['all_call_and_result_variants_reviewed'] is True and tool['manual_review_status'].startswith('complete')
        covered=set();assert {p['profile'] for p in r['profiles']}=={'baseline','zvec-grep'} and len(r['profiles'])==2
        for profile in r['profiles']:
            extract=load(Path(profile['extraction_and_accounting']['path']));assert extract['extraction_complete'] is True and not extract['errors']
            actual={(t['session'],t['id']):t for t in extract['tools']};assert len(actual)==len(extract['tools'])==profile['tool_count']
            selected=[]
            for identity,expected in actual.items():
                assert identity in reviewed and identity not in covered;covered.add(identity);tool=reviewed[identity];selected.append(tool)
                assert tool['ordinal']==expected['ordinal'] and tool['name']==expected['name']
                assert len(expected['call_variants'])==len(expected['result_variants'])==1
                assert tool['full_input']==expected['call_variants'][0]['input'] and tool['input_sha256']==expected['call_variants'][0]['input_sha256']
                assert tool['result_block_sha256']==expected['result_variants'][0]['block_sha256']
            assert profile['source_file_count_before_after']==[1596,1596] and profile['source_unchanged'] is True
            for field in ('strict_source_scope_valid','factual_evidence_scope_valid'):
                assert profile[field] is tri_all(t[field] for t in selected)
            assert profile['strict_scope_gate_passed'] is (profile['strict_source_scope_valid'] is True and not profile['unresolved_evidence'])
            if profile['strict_source_scope_valid'] is None:assert profile['unresolved_evidence'] and profile['strict_scope_gate_passed'] is False
            expected_gate=profile['strict_scope_gate_passed'] is True and profile['factual_evidence_scope_valid'] is True and profile['source_unchanged'] is True and (profile['profile']=='baseline' or profile['relevant_correct_workspace_zg_retrieval'] is True)
            assert profile['scope_and_retrieval_gate_passed'] is expected_gate
            rows.append({'pair_key':key,'trial_index':i,**{k:profile[k] for k in ('profile','strict_source_scope_valid','strict_scope_gate_passed','scope_and_retrieval_gate_passed','factual_evidence_scope_valid','external_content_observed','external_code_or_answer_facts_observed','source_unchanged','relevant_correct_workspace_zg_retrieval','tool_count','raw_tool_occurrences','known_strict_deviations','unresolved_evidence')}})
        assert covered==set(reviewed) and len(covered)==r['tool_count'];tools_total+=len(covered);occurrences+=r['raw_tool_occurrences']
        reports.append({'pair_key':key,'path':str(path),'sha256':sha(path),'tools_covered':len(covered)})
    report={'schema_version':1,'verified_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_reviews_verified':10,'profile_rows':20,'all_actual_tools_fully_covered':tools_total,'raw_tool_occurrences':occurrences,'distinct_original_reference_files_rehashed':len(references),'rows':rows,'reports':reports,'references':references,'quality_or_resource_comparison_performed':False,'private_assignment_accessed':False,'semantic_review_replaced':False,'unknown_handling':'Retain explicit null; it never passes a scope gate or becomes a zero quality score. All scheduled observations remain.','method_sha256':sha(Path(__file__))}
    out=ROOT/'source-cohort-evidence.json'
    with out.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    print(json.dumps({k:report[k] for k in ['source_reviews_verified','profile_rows','all_actual_tools_fully_covered','distinct_original_reference_files_rehashed']}))

if __name__=='__main__':verify()
