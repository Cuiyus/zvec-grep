"""Two bounded, separately labelled synthetic routing diagnostics; not benchmark trials."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runner as r
from native_runner import native_index, run_native_trial
from qoder_probe import native_mcp_evidence

IMAGE = 'zg-routing-diagnostic:0.2.2'
QUESTION = '根据工作区内各家公司的经营材料，比较收入与利润的变化，分析出现差异的原因，以及这些公司共同面临的风险。请引用来源，回答控制在300字以内。'
REMINDER = '\n请遵循当前会话已加载的用户级检索路由指引，先执行其中适用于跨文档比较与原因分析的检索步骤，再读取需要核对的片段。'
FIXTURE = {
    '经营材料/星舟.md': '# 星舟经营记录\n本期收入增长20%，利润下降12%。客户订单增加，但进口树脂价格上涨，合同中的调价周期长于原料采购周期。公司扩大了库存以保证交货，经营现金流转负。\n',
    '经营材料/松桥.md': '# 松桥经营记录\n本期收入增长8%，利润增长18%。研发项目进入量产，减少了高价进口材料用量。部分客户延迟付款，导致应收账款增长。\n',
    '经营材料/远岸.md': '# 远岸经营记录\n本期收入增长15%，利润增长2%。海外新工厂产能利用率偏低，固定折旧先于收入确认。结算账期延长，公司增借短期贷款补充流动资金。\n',
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source, seed, cache = root/'source', root/'index', root/'model-cache'
    source.mkdir()
    for name, text in FIXTURE.items():
        file = source/name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
    for cmd in [['git','init','-q',str(source)], ['git','-C',str(source),'add','.'],
                ['git','-C',str(source),'-c','user.name=Workspace QA','-c','user.email=benchmark@localhost','commit','-qm','Synthetic routing fixture']]:
        subprocess.run(cmd, check=True)
    (source/'.zvec-grep').mkdir()
    before = r.directory_identity(source, skip_git=True)
    patch = subprocess.run(['docker','run','--rm',IMAGE,'cat','/opt/qa/diagnostic-patch.json'], check=True, capture_output=True, text=True)
    r.write_json(root/'instrumentation.json', json.loads(patch.stdout))
    plan = {'diagnostic_only': True, 'included_in_benchmark': False, 'judge': None,
        'fixture_files': before, 'question': QUESTION, 'cases': ['natural', 'routing-reminder'],
        'limits': {'model_requests': 6, 'tool_calls': 12, 'input_tokens': 180000, 'wall_seconds': 180},
        'max_output_tokens': 2048, 'model_request_retries': 1,
        'install_command': ['zg','install','--target','qoder','--yes'],
        'model': r.MODEL, 'embedding': r.EMBEDDING,
        'ci': {k: os.environ.get(k) for k in ['GITHUB_SHA','GITHUB_RUN_ID']}}
    r.write_json(root/'plan.json', plan)
    print(json.dumps({'phase':'diagnostic_index','status':'starting'}), flush=True)
    native_index(source, seed, root/'index-preparation', cache, image=IMAGE)
    rows = []
    for case in plan['cases']:
        case_root = root/case
        prompt = r.instruction(QUESTION + (REMINDER if case == 'routing-reminder' else ''), '回答.md', zg=True)
        working = r.working_index(seed, root/'working-indexes'/case)
        print(json.dumps({'phase':'routing_diagnostic','case':case,'status':'starting'}), flush=True)
        result = run_native_trial(source, case_root/'agent', working, cache, prompt=prompt,
            profile='with-zg', limits=plan['limits'], image=IMAGE, model_request_retries=1, max_output_tokens=2048)
        result.update(diagnostic_only=True, included_in_benchmark=False, case=case,
                      source_unchanged=r.directory_identity(source, skip_git=True)==before)
        r.write_json(case_root/'result.json', result)
        audit_file = case_root/'agent/prompt-audit.jsonl'
        audits = [json.loads(l) for l in audit_file.read_text().splitlines()] if audit_file.exists() else []
        requests = [a for a in audits if a['stage']=='transport_request']
        memories = [a for a in audits if a['stage']=='memory_context']
        mcp = native_mcp_evidence(case_root/'agent')
        row = {k:result.get(k) for k in ['case','status','input_tokens','tool_calls','zg_tool_calls','wall_seconds','source_unchanged']}
        row.update(requests_observed=len(requests), loaded_memory_observed=len(memories),
            loaded_memory_contains_guidance=any(a['full_guidance_present'] for a in memories),
            first_request_contains_guidance=requests[0]['full_guidance_present'] if requests else None,
            first_request_has_zg_schema=requests[0]['zg_tool_schema_present'] if requests else None,
            all_requests_contain_guidance=all(a['full_guidance_present'] for a in requests) if requests else None,
            mcp_connected=mcp['mcp_registered_and_connected'], zg_successes=mcp['native_successes'],
            standard_install_valid=result.get('installation',{}).get('valid') is True,
            observer_errors=(case_root/'agent/prompt-audit-errors.txt').exists())
        row['observation_valid'] = bool(requests and memories and row['source_unchanged'] and
            row['standard_install_valid'] and row['mcp_connected'] and not row['observer_errors'])
        rows.append(row)
        r.write_json(root/'summary.json', {'diagnostic_only':True,'rows':rows})
        print(json.dumps(row), flush=True)
    lines = ['## Qoder routing diagnostic — synthetic, excluded from benchmark', '',
        'Same zg install, Qoder 1.1.45 / Qwen3.8-Max and remote Qwen embedding. Passive diagnostic instrumentation only.', '',
        '| Case | Status | Guidance in first request | zg schema | zg calls / success | Seconds |',
        '|---|---|---|---|---|---|']
    for row in rows:
        lines.append(f"| {row['case']} | {row['status']} | {row['first_request_contains_guidance']} | {row['first_request_has_zg_schema']} | {row['zg_tool_calls']} / {row['zg_successes']} | {row['wall_seconds']} |")
    (root/'summary.md').write_text('\n'.join(lines)+'\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as out:out.write('\n'.join(lines)+'\n')
    return 0 if all(row['observation_valid'] for row in rows) else 1

if __name__=='__main__':
    raise SystemExit(main())
