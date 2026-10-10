"""Frozen MCP wire schemas and a bounded provider-side type-expression experiment.

No Agent/Judge runs are added to the sealed benchmark. Requests are saved without
authorization headers; raw provider tool argument strings are retained.
"""
from __future__ import annotations
import concurrent.futures
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

HASHES = {
 'node':'a958bb24f161c6d092fe555ddeeb44989f933354d9e21392d89af4bc75bd4393',
 'rust':'95390deef047eae83f0720ff12d53488952f43cd0fa53aba8c70c7ad17d5002e',
}
RUST_CLI_HASH='f62b862d3e5a127f1c1e079910100689d14e9230ac2889ec444d825c82986b83'
ENDPOINT='https://llm-67x4s810wr6kl2i4.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions'


def save(path, value):
 path.parent.mkdir(parents=True, exist_ok=True)
 path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def prepare(packages, output):
 identities={}
 for runtime,expected in HASHES.items():
  archive=packages/runtime/'candidate.tgz'
  actual=hashlib.sha256(archive.read_bytes()).hexdigest()
  assert actual==expected,(runtime,actual)
  identities[runtime]={'tarball_sha256':actual}
  if runtime=='rust':
   with tarfile.open(archive) as t:t.extractall(packages/'rust/unpacked',filter='data')
   executable=packages/'rust/unpacked/package/bin/zg'
   digest=hashlib.sha256(executable.read_bytes()).hexdigest()
   assert digest==RUST_CLI_HASH,digest
   executable.chmod(0o755)
   identities[runtime]['cli_sha256']=digest
 save(output/'identities.json',identities)


def parse_rpc_body(body, message_id):
 if not body.strip():return None
 if body.lstrip().startswith('data:') or 'event:' in body:
  for block in body.replace('\r\n','\n').split('\n\n'):
   payload='\n'.join(line[5:].lstrip() for line in block.splitlines() if line.startswith('data:')).strip()
   if not payload or payload=='[DONE]':continue
   data=json.loads(payload)
   if isinstance(data,dict) and data.get('id')==message_id:return data
  raise ValueError('Missing JSON-RPC response in SSE')
 return json.loads(body)


def rpc(url,message,session=None):
 headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
 if session:headers.update({'Mcp-Session-Id':session,'MCP-Protocol-Version':'2025-11-25'})
 req=urllib.request.Request(url,json.dumps(message).encode(),headers)
 with urllib.request.urlopen(req,timeout=25) as r:
  body=r.read().decode();sid=r.headers.get('mcp-session-id',session)
 return parse_rpc_body(body,message.get('id')),sid


def capture(packages,output):
 binary=packages/'rust/unpacked/package/bin/zg'
 env=dict(os.environ,ZVEC_GREP_HOME=str(output/'rust-home'))
 url='http://127.0.0.1:18001/mcp'
 with (output/'rust-server.stdout').open('w') as stdout,(output/'rust-server.stderr').open('w') as stderr:
  process=subprocess.Popen([str(binary),'--server','run','--listen','127.0.0.1:18001','--mcp-toolset','agent'],env=env,stdout=stdout,stderr=stderr)
  try:
   response=None
   for _ in range(40):
    if process.poll() is not None:raise RuntimeError('Rust server exited before initialize')
    try:
     response,session=rpc(url,{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'schema-diagnosis','version':'1'}}})
     break
    except (urllib.error.URLError,ConnectionError):time.sleep(.5)
   assert response and 'result' in response,response
   rpc(url,{'jsonrpc':'2.0','method':'notifications/initialized'},session)
   tools,_=rpc(url,{'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}},session)
   save(output/'rust-tools.json',{'instructions':response['result'].get('instructions'),**tools['result']})
   checks=[]
   for n,values in enumerate([{'limit':'15'},{'fuse':'true'},{'limit':15,'fuse':True}]):
    args={'root':str(output),'query':'needle',**values}
    result,_=rpc(url,{'jsonrpc':'2.0','id':n+3,'method':'tools/call','params':{'name':'zvec_grep_search','arguments':args}},session)
    checks.append({'args':args,'response':result})
   save(output/'rust-parsing.json',checks)
  finally:
   process.terminate()
   try:process.wait(timeout=10)
   except subprocess.TimeoutExpired:process.kill();process.wait()
 build_arms(output)


def build_arms(output):
 originals={r:next(t for t in json.loads((output/f'{r}-tools.json').read_text())['tools'] if t['name']=='zvec_grep_search') for r in ['node','rust']}
 arms={f'{r}-original':copy.deepcopy(t) for r,t in originals.items()}
 for runtime,donor,label in [('rust','node','rust-node-types'),('node','rust','node-rust-types')]:
  t=copy.deepcopy(originals[runtime])
  for field in ['limit','fuse']:
   t['inputSchema']['properties'][field]['type']=copy.deepcopy(originals[donor]['inputSchema']['properties'][field]['type'])
  arms[label]=t
 save(output/'schema-arms.json',arms)
 save(output/'schema-difference.json',{r:{k:t['inputSchema']['properties'].get(k) for k in ['query','fts','limit','fuse']} for r,t in originals.items()})
 return arms


def wrong_types(args):
 errors={}
 if 'limit' in args and args['limit'] is not None and type(args['limit']) is not int:errors['limit']=type(args['limit']).__name__
 if 'fuse' in args and args['fuse'] is not None and type(args['fuse']) is not bool:errors['fuse']=type(args['fuse']).__name__
 return errors


def representation_arms(output):
 original=next(t for t in json.loads((output/'rust-tools.json').read_text())['tools'] if t['name']=='zvec_grep_search')
 arms={}
 for label in ['rust-nullable-array','rust-scalar','rust-singleton-array','rust-nullable-anyof']:
  tool=copy.deepcopy(original)
  for field,scalar in [('limit','integer'),('fuse','boolean')]:
   prop=tool['inputSchema']['properties'][field]
   if label=='rust-scalar':prop['type']=scalar
   elif label=='rust-singleton-array':prop['type']=[scalar]
   elif label=='rust-nullable-anyof':
    prop.pop('type');prop['anyOf']=[{'type':scalar},{'type':'null'}]
  arms[label]=tool
 save(output/'representation-arms.json',arms)
 return arms


def request_body(tool,repetition):
 # Description and all other schema keywords stay unchanged within each pair.
 return {
  'model':'qwen3.8-max','temperature':0,'seed':42,'enable_thinking':True,'reasoning_effort':'high','max_tokens':2048,
  'messages':[
   {'role':'system','content':'You are a coding assistant. Search the workspace using the supplied tool before answering. Workspace root is /app.'},
   {'role':'user','content':'Find the integration test and control flow connecting a timestamp separation delay, targeted remote upload, and --update with remote filtering to the status that the cached version is newer than the remote version. Search with a limit of 15 and fuse the results.'},
  ],
  'tools':[{'type':'function','function':{'name':'zvec_grep_search','description':tool.get('description',''),'parameters':tool['inputSchema']}}],
  'tool_choice':'auto',
 }


def call_model(label,repetition,tool,output,credential):
 stem=f'{repetition:02d}-{label}';body=request_body(tool,repetition)
 save(output/'requests'/f'{stem}.json',body)
 started=time.monotonic()
 req=urllib.request.Request(ENDPOINT,json.dumps(body).encode(),{'Content-Type':'application/json','Authorization':'Bearer '+credential})
 try:
  with urllib.request.urlopen(req,timeout=180) as response:raw=response.read().decode();status=response.status
 except urllib.error.HTTPError as e:
  save(output/'responses'/f'{stem}.json',{'http_status':e.code,'body':e.read().decode().replace(credential,'REDACTED')})
  return {'arm':label,'repetition':repetition,'http_status':e.code,'completed':False}
 except Exception as e:
  save(output/'responses'/f'{stem}.json',{'exception':type(e).__name__})
  return {'arm':label,'repetition':repetition,'completed':False,'exception':type(e).__name__}
 data=json.loads(raw.replace(credential,'REDACTED'));save(output/'responses'/f'{stem}.json',data)
 calls=[]
 for choice in data.get('choices',[]):
  for call in choice.get('message',{}).get('tool_calls',[]):
   argument_string=call['function']['arguments']
   try:args=json.loads(argument_string)
   except ValueError:calls.append({'arguments_raw':argument_string,'invalid_json':True});continue
   calls.append({'arguments_raw':argument_string,'arguments':args,'wrong_types':wrong_types(args),'target_fields_present':all(k in args for k in ['limit','fuse'])})
 return {'arm':label,'repetition':repetition,'completed':True,'http_status':status,'seconds':time.monotonic()-started,'calls':calls,'usage':data.get('usage'),'response_model':data.get('model'),'finish_reasons':[c.get('finish_reason') for c in data.get('choices',[])]}


def model(output):
 credential=os.environ.get('GLM_API_KEY');assert credential,'GLM_API_KEY missing'
 count=int(os.environ.get('DIAG_REPETITIONS','5'));assert 1<=count<=5
 arms=representation_arms(output) if os.environ.get('DIAG_SCHEMA_SET')=='representations' else build_arms(output)
 rows=[]
 # At most four concurrent requests, no automatic retries or extra samples.
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for repetition in range(1,count+1):
   labels=list(arms)
   if repetition%2==0:labels.reverse()
   futures=[pool.submit(call_model,label,repetition,arms[label],output,credential) for label in labels]
   for f in futures:
    row=f.result();rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    save(output/'model-results.json',{'endpoint':ENDPOINT,'rows':rows})
 summary={}
 for label in arms:
  selected=[r for r in rows if r['arm']==label];calls=[c for r in selected for c in r.get('calls',[])]
  summary[label]={'requests':len(selected),'completed':sum(r['completed'] for r in selected),'tool_calls':len(calls),'wrong_type_calls':sum(bool(c.get('wrong_types')) for c in calls),'both_fields_present':sum(c.get('target_fields_present',False) for c in calls)}
 save(output/'model-summary.json',summary);print(json.dumps(summary,indent=2),flush=True)
 assert all(r['completed'] and r.get('calls') for r in rows),'Provider errors or no tool calls: inspect raw evidence'


if __name__=='__main__':
 stage=sys.argv[1];packages=Path(sys.argv[2]);output=Path(sys.argv[3]);output.mkdir(parents=True,exist_ok=True)
 if stage=='prepare':prepare(packages,output)
 elif stage=='capture':capture(packages,output)
 elif stage=='model':model(output)
 else:raise ValueError(stage)
