"""Aggregate only completed scope reports and rehash all their already-declared references."""
from pathlib import Path
import json,hashlib,collections
M=Path(__file__).parent.parent.parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ref(p):return {'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)}
rows=[]; counts=collections.Counter(); rechecks=0
for i in range(1,11):
 o=M/'pairs'/f'q004-r{i:02d}'
 r=json.loads((o/'report.json').read_text());v=json.loads((o/'reference-verification.json').read_text())
 assert v['all_references_match'] and v['report']['sha256']==sha(o/'report.json')
 for item in v['checks']:
  p=Path(item['path']); assert p.stat().st_size==item['bytes'] and sha(p)==item['sha256'],str(p)
  rechecks+=1
 for profile in r['profiles']:
  counts.update(profile['tool_counts'])
  assert profile['factual_evidence_scope_valid'] and profile['source_unchanged']
  assert not profile['external_content_observed'] and not profile['external_code_or_answer_facts_observed']
 row={'pair':o.name,'strict_source_scope_valid':r['strict_source_scope_valid'],'factual_evidence_scope_valid':r['factual_evidence_scope_valid'],'external_content_observed':r['external_content_observed'],'external_directory_metadata_observed':r.get('external_directory_metadata_observed'),'external_code_or_answer_facts_observed':r['external_code_or_answer_facts_observed'],'source_unchanged':r['source_unchanged'],'relevant_correct_workspace_zg_retrieval':r['relevant_correct_workspace_zg_retrieval'],'scope_and_retrieval_gate_passed':r['scope_and_retrieval_gate_passed'],'tool_count':r['tool_count'],'root_tool_count':r['root_tool_count'],'child_tool_count':r['child_tool_count'],'mcp_hit_count':r['mcp_hit_count'],'unresolved_evidence':r['unresolved_evidence'],'profiles':[{'profile':p['profile'],'strict_source_scope_valid':p['strict_source_scope_valid'],'tools':p.get('tools',p.get('tool_count')),'tool_counts':p['tool_counts'],'mcp_hits':p.get('mcp_hits',p.get('mcp_hit_count'))} for p in r['profiles']],'report':ref(o/'report.json'),'reference_verification':ref(o/'reference-verification.json'),'rechecked_reference_count':v['reference_count']}
 rows.append(row)
 print(json.dumps({k:row[k] for k in ['pair','strict_source_scope_valid','scope_and_retrieval_gate_passed','tool_count','mcp_hit_count','report']},ensure_ascii=False))
summary={'audit_kind':'source_tool_scope_only','run_id':39380248003,'head_sha':'3978d4bbbbb1e37961c277cdf76696b84c69e3bc','all_10_pairs_complete':True,'pair_count':10,'profile_count':20,'root_tool_count':sum(r['root_tool_count'] for r in rows),'child_tool_count':sum(r['child_tool_count'] for r in rows),'tool_counts':dict(counts),'mcp_hit_count':sum(r['mcp_hit_count'] for r in rows),'all_report_references_rehashed_after_publication':True,'rechecked_reference_count':rechecks,'source_files_per_profile':1596,'strict_policy_sha256':'2233de3a9413bbad6c8af4a977c4da406213ac25674bb7b38e90b08fb4ac8d06','pairs':rows,'scope_blockers':[{'pair':'q004-r02','profile':'baseline','strict':False,'reason':'Unconditional outside-home directory probes, names-only output observed; no external file bodies/code/answer facts observed.'},{'pair':'q004-r03','profile':'zvec-grep','strict':None,'reason':'Home-directory ls fallback may run after first ls failure; no independent first-command status or branch trace/expanded home. Retained unknown and gate false.'},{'pair':'q004-r04','profile':'zvec-grep','strict':False,'reason':'Unconditional /logs/agent/sessions/ names-only enumeration; no listed file opened.'},{'pair':'q004-r07','profile':'zvec-grep','strict':True,'retrieval':False,'reason':'Complete 35 Bash calls, outputs and all tools inspected; no successful zg MCP/CLI/API/other retrieval occurred.'}],'no_answer_grades_private_mappings_or_resource_benefits_assessed':True,'no_new_questions_or_runs_started':True,'summary_script':ref(Path(__file__)),'summary_schema_note':'Older r03 profile keys tool_count/mcp_hit_count are normalized as tools/mcp_hits without modifying its report. external_directory_metadata_observed=null means the earlier report did not expose this separate field, not a new scope adjudication. The unsuccessful preliminary aggregation stopped on the older mcp_hit_count key before creating a summary; all underlying reports and evidence remained unchanged.'}
p=M/'cohort-source-summary-v1.json'
with p.open('x') as f:json.dump(summary,f,ensure_ascii=False,indent=2);f.write('\n')
print('SUMMARY',json.dumps(ref(p)))
print('COUNTS',json.dumps({'tools':sum(counts.values()),'types':dict(counts),'hits':summary['mcp_hit_count'],'references':rechecks}))
