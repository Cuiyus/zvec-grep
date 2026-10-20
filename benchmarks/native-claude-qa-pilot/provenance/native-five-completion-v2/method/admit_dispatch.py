"""One explicit supplemental dispatch after retained same-head provider-free preflight."""
from pathlib import Path
import argparse,datetime,hashlib,json,subprocess,sys,zipfile
B=Path(__file__).resolve().parent.parent;W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep');H=W/'benchmarks/native-claude-qa-pilot'
sys.path[:0]=[str(H),str(W/'benchmarks/opencode-qa-pilot'),str(B/'upstream-main/benchmarks/swe-qa-bench')]
import five_completion_v2 as completion
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,data):
 with p.open('x') as f:json.dump(data,f,indent=2);f.write('\n')
def api(endpoint):return json.loads(subprocess.run(['gh','api',endpoint],capture_output=True,text=True,check=True).stdout)
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--head',required=True);parser.add_argument('--preflight-run',type=int,required=True);args=parser.parse_args()
 root=B/'native-five-completion-v2-ci';assert root.is_dir()
 plan=completion.validate_completion(json.loads(completion.PLAN.read_text()))
 assert subprocess.run(['git','rev-parse','HEAD'],cwd=W,capture_output=True,text=True,check=True).stdout.strip()==args.head
 assert subprocess.run(['git','ls-remote','fork','refs/heads/dev/opencode-qa-repair-v2'],cwd=W,capture_output=True,text=True,check=True).stdout.split()[0]==args.head
 old=api('repos/Cuiyus/zvec-grep/actions/workflows/native-five-completion-v2.yml/runs?event=workflow_dispatch&per_page=100');assert old['total_count']==0
 preflight=api(f'repos/Cuiyus/zvec-grep/actions/runs/{args.preflight_run}')
 assert preflight['head_sha']==args.head and preflight['event']=='push' and preflight['run_attempt']==1 and preflight['conclusion']=='success'
 assert preflight['path']=='.github/workflows/native-five-completion-v2.yml' and preflight['repository']['full_name']=='Cuiyus/zvec-grep'
 pre=B/'native-five-completion-v2-preflight-ci'/str(args.preflight_run)/'preflight'
 receipt=json.loads((pre/'artifact-byte-verification.json').read_text());assert receipt['complete'] and receipt['matches_api_digest'] and receipt['exact_extracted_inventory']
 assert receipt['run_id']==args.preflight_run and receipt['head_sha']==args.head and receipt['event']=='push' and receipt['job_conclusion']=='success'
 assert sha(pre/'artifact.zip')==receipt['artifact_sha256']
 raw=pre/'raw';files=receipt['files']
 assert not any(p.is_symlink() for p in raw.rglob('*'))
 assert sorted(p.relative_to(raw).as_posix() for p in raw.rglob('*') if p.is_file())==sorted(f['path'] for f in files)
 with zipfile.ZipFile(pre/'artifact.zip') as archive:
  assert archive.testzip() is None
  assert sorted(n for n in archive.namelist() if not n.endswith('/'))==sorted(f['path'] for f in files)
  for f in files:
   p=raw/f['path'];assert p.is_file() and sha(p)==f['sha256'] and p.stat().st_size==f['bytes'] and p.read_bytes()==archive.read(f['path'])
 raw=pre/'raw';assert (raw/'five-completion-plan-v2.json').read_bytes()==completion.PLAN.read_bytes()
 assert sha(raw/'zg-install.tgz')=='03af5790a2e7a1e1eb4e9c857e64218b72c8a9c3ebbed046a177e5600de460cb'
 tests=(raw/'tests.log').read_text();assert 'Ran 7 tests' in tests and '\nOK\n' in tests
 matrix=json.loads((raw/'previews.jsonl').read_text().splitlines()[-1]);assert matrix=={'include':plan['pairs']}
 for pair in plan['pairs']:
  record=json.loads((raw/('dry-'+pair['key'])/'execution.json').read_text());assert record['dry_run'] is True and len(record['slots'])==2
  assert [s['profile'] for s in record['slots']]==pair['profiles']
 original=api('repos/Cuiyus/zvec-grep/actions/runs/39392884840');jobs=api('repos/Cuiyus/zvec-grep/actions/runs/39392884840/jobs?per_page=100')
 assert completion.missing_pairs(original,jobs)==plan['pairs'] and completion.parallel_capacity(jobs,len(plan['pairs']))
 for name,data in [('preflight-run-api.json',preflight),('prior-dispatch-runs-api.json',old),('original-at-dispatch-run.json',original),('original-at-dispatch-jobs.json',jobs)]:write(root/name,data)
 intent={'schema_version':1,'created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head_sha':args.head,'supplement_plan_sha256':sha(completion.PLAN),'scheduled_supplemental_agent_executions':2*len(plan['pairs']),'preflight_run_id':args.preflight_run,'preflight_receipt_sha256':sha(pre/'artifact-byte-verification.json'),'dispatch_attempt':1,'duplicates_or_implicit_retries_allowed':False,'started_original_agents_retried':0,'original_primary_missing_rows_preserved':True}
 write(root/'dispatch-intent.json',intent)
 request={'ref':'dev/opencode-qa-repair-v2','inputs':{'execute_missing':True}};write(root/'dispatch-request.json',request)
 result=subprocess.run(['gh','api','--method','POST','repos/Cuiyus/zvec-grep/actions/workflows/native-five-completion-v2.yml/dispatches','--input',str(root/'dispatch-request.json')],capture_output=True,text=True)
 (root/'dispatch-response.stdout').write_text(result.stdout);(root/'dispatch-response.stderr').write_text(result.stderr)
 response={'returncode':result.returncode,'outcome':'accepted' if result.returncode==0 else 'uncertain_or_failed_do_not_retry','explicit_attempt_count':1};write(root/'dispatch-response.json',response)
 print(json.dumps(response));return result.returncode
if __name__=='__main__':raise SystemExit(main())
