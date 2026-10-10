"""Fork-only verification of the fixed binary's actual MCP schema and Qwen calls."""
import concurrent.futures
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error

import diagnose as d


def capture(binary, output, toolset):
    url = 'http://127.0.0.1:18001/mcp'
    env = dict(os.environ, ZVEC_GREP_HOME=str(output / (toolset + '-home')))
    with (output / (toolset + '.stdout')).open('w') as stdout, (output / (toolset + '.stderr')).open('w') as stderr:
        process = subprocess.Popen([str(binary), '--server', 'run', '--listen', '127.0.0.1:18001', '--mcp-toolset', toolset], env=env, stdout=stdout, stderr=stderr)
        try:
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError('Server exited before initialize')
                try:
                    initialized, session = d.rpc(url, {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'schema-fix-validation','version':'1'}}})
                    break
                except (urllib.error.URLError, ConnectionError):
                    time.sleep(.5)
            else:
                raise RuntimeError('Server initialization timeout')
            assert initialized.get('result'), initialized
            d.rpc(url, {'jsonrpc':'2.0','method':'notifications/initialized'}, session)
            response, _ = d.rpc(url, {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}, session)
            tools = response['result']
            d.save(output / (toolset + '-tools.json'), tools)
            checks = []
            for i, patch in enumerate([{'limit':'15'}, {'fuse':'true'}, {'limit':15,'fuse':True}]):
                args = {'root':str(output),'query':'needle','autoUpdate':False,**patch}
                result, _ = d.rpc(url, {'jsonrpc':'2.0','id':i+3,'method':'tools/call','params':{'name':'zvec_grep_search','arguments':args}}, session)
                checks.append({'arguments':args,'response':result})
                if i < 2:
                    assert result['error']['code'] == -32602, result
                else:
                    assert 'error' not in result and 'result' in result, result
            d.save(output / (toolset + '-parsing.json'), checks)
            return tools
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    binary = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    d.save(output / 'candidate.json', {'source_sha':os.environ['FIX_SHA'],'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()})
    agent = capture(binary, output, 'agent')
    full = capture(binary, output, 'full')
    fixed = next(t for t in agent['tools'] if t['name'] == 'zvec_grep_search')
    full_search = next(t for t in full['tools'] if t['name'] == 'zvec_grep_search')
    assert fixed['inputSchema'] == full_search['inputSchema']
    for field, scalar in [('limit','integer'),('embeddingConcurrency','integer'),('fuse','boolean'),('preferSymbol','boolean'),('trace','boolean')]:
        assert fixed['inputSchema']['properties'][field]['type'] == scalar
        assert field not in fixed['inputSchema']['required']
    baseline = next(t for t in json.loads((Path(__file__).parent / 'baseline-rust-tools.json').read_text())['tools'] if t['name'] == 'zvec_grep_search')
    expected = copy.deepcopy(baseline['inputSchema'])
    for field, scalar in [('limit','integer'),('embeddingConcurrency','integer'),('fuse','boolean'),('preferSymbol','boolean'),('trace','boolean')]:
        expected['properties'][field]['type'] = scalar
    assert expected == fixed['inputSchema'], 'Unexpected search schema changes relative to frozen benchmark'
    control = copy.deepcopy(fixed)
    for field, scalar in [('limit','integer'),('fuse','boolean')]:
        control['inputSchema']['properties'][field]['type'] = [scalar,'null']
    arms = {'fixed':fixed, 'nullable-control':control}
    d.save(output / 'schema-arms.json', arms)
    credential = os.environ['GLM_API_KEY']
    totals = {}
    for transport in ['json','sse']:
        os.environ['DIAG_TRANSPORT'] = transport
        destination = output / transport
        rows = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for repetition in range(1,6):
                labels = list(arms)
                if repetition % 2 == 0:
                    labels.reverse()
                futures = [pool.submit(d.call_model, label, repetition, arms[label], destination, credential) for label in labels]
                rows.extend(f.result() for f in futures)
                d.save(destination / 'model-results.json', rows)
        summary = {}
        for label in arms:
            selected = [r for r in rows if r['arm'] == label]
            calls = [c for r in selected for c in r.get('calls',[])]
            summary[label] = {'requests':len(selected),'completed':sum(r['completed'] for r in selected),'tool_calls':len(calls),'wrong_type_requests':sum(any(c.get('wrong_types') or c.get('invalid_json') for c in r.get('calls',[])) for r in selected),'wrong_type_calls':sum(bool(c.get('wrong_types') or c.get('invalid_json')) for c in calls),'both_fields_present':sum(c.get('target_fields_present',False) for c in calls)}
        d.save(destination / 'model-summary.json', summary)
        print(json.dumps({'transport':transport,'summary':summary}), flush=True)
        assert all(r['completed'] and r.get('calls') and any(c.get('target_fields_present') for c in r['calls']) for r in rows), 'Incomplete or uninformative samples'
        assert summary['fixed']['wrong_type_calls'] == 0, 'Fixed schema still emits wrong primitive types'
        assert summary['nullable-control']['wrong_type_requests'] == 5, 'Control did not reproduce regression'
        totals[transport] = summary
    d.save(output / 'summary.json', totals)


if __name__ == '__main__':
    main()
