"""Compare actual tools/list on frozen and description-patched native binaries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import tarfile
import threading

BASE = '732a44014654c7acf08553716af67865bcfb8430'
PATCHED = '514d0134069cad50c4022b00e6061df22c5229b3'
OLD_TARBALL_SHA = '30592b2be9c2cfc10e10211b38b7fb956b35c93ceee01ae890f6d7bdff768221'


def unannotated(value):
    if isinstance(value,dict): return {k:unannotated(v) for k,v in value.items() if k != 'description'}
    if isinstance(value,list): return [unannotated(v) for v in value]
    return value


def capture(binary, home, toolset):
    home.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    stderr=(home/'bridge.stderr').open('w')
    process=subprocess.Popen([str(binary),'--server','--stdio','--home',str(home),
        '--listen',f'127.0.0.1:{port}','--mcp-toolset',toolset],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,bufsize=1)
    replies=queue.Queue()
    def reader():
        for line in process.stdout:
            try: replies.put(json.loads(line))
            except json.JSONDecodeError: pass
    threading.Thread(target=reader,daemon=True).start()
    def send(value):
        process.stdin.write(json.dumps(value)+'\n');process.stdin.flush()
    def receive(identity):
        while True:
            reply=replies.get(timeout=60)
            if reply.get('id')==identity:
                assert 'error' not in reply,reply
                return reply['result']
    try:
        send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{
            'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'description-contract-audit','version':'1'}}})
        initialized=receive(1)
        send({'jsonrpc':'2.0','method':'notifications/initialized'})
        send({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})
        return dict(initialize=initialized,tools=receive(2)['tools'])
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        subprocess.run([str(binary),'--server','off','--home',str(home)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
        stderr.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--before',required=True,type=Path)
    p.add_argument('--after',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    results={}
    for name,directory,commit in [('before',args.before,BASE),('after',args.after,PATCHED)]:
        manifest=json.loads((directory/'manifest.json').read_text())
        assert manifest['candidate_commit']==commit
        package=directory/'candidate.tgz'
        digest=hashlib.file_digest(package.open('rb'),'sha256').hexdigest()
        assert digest==manifest['tarball_sha256']
        if name=='before':assert digest==OLD_TARBALL_SHA
        extracted=args.output/name
        extracted.mkdir()
        with tarfile.open(package) as archive:archive.extractall(extracted,filter='data')
        binary=(extracted/'package/bin/zg').resolve()
        results[name]=dict(manifest=manifest,cli_sha256=hashlib.file_digest(binary.open('rb'),'sha256').hexdigest(),toolsets={})
        for toolset in ['agent','full']:
            snapshot=capture(binary,(args.output/f'home-{name}-{toolset}').resolve(),toolset)
            results[name]['toolsets'][toolset]=snapshot
            (args.output/f'{name}-{toolset}-mcp.json').write_text(json.dumps(snapshot,indent=2)+'\n')
    for toolset in ['agent','full']:
        before=results['before']['toolsets'][toolset];after=results['after']['toolsets'][toolset]
        assert before['initialize']['instructions']==after['initialize']['instructions']
        assert unannotated(before['tools'])==unannotated(after['tools']), 'Schema types/constraints or metadata changed'
        search=next(t for t in after['tools'] if t['name']=='zvec_grep_search')
        assert 'Do not JSON-stringify arrays.' in search['description']
        query_type=search['inputSchema']['$defs']['QueryListInput']
        assert 'one literal query' in query_type['description']
        assert 'not decoded' in query_type['anyOf'][0]['description']
        assert 'array of strings directly' in query_type['anyOf'][1]['description']
        for f in ['queries','fts','vector']:
            assert 'Do not JSON-stringify arrays.' in search['inputSchema']['properties'][f]['description']
    results['passed']=True
    results['contract']='Actual MCP tool schema identical after removing description fields; server instructions identical'
    (args.output/'schema-comparison.json').write_text(json.dumps(results,indent=2)+'\n')
    print(results['contract'])

if __name__=='__main__':main()
