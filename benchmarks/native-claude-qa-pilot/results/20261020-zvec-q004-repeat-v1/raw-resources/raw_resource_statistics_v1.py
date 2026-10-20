"""Complete raw resource estimates only, before quality grades or mapping read."""
import datetime, hashlib, json, random, sys
from pathlib import Path

B=Path('/Users/cc/workspace/zvec-grep/output/claude-qwen-qa-pilot-20261019/main-agent-pilot')
W=Path('/Users/cc/.codex/worktrees/claude-zvec-qa-pilot/zvec-grep')
ROOT=B/'native-q004-repeat-v1-audit'
sys.path.insert(0,str(W/'benchmarks/native-claude-qa-pilot'))
import architecture_repeat_analysis_v1 as frozen
stats=frozen.implementation
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())

check=load(ROOT/'execution-parent-verification.json');assert check['passed'] and check['pairs_verified']==10
rows=[];references={}
for i in range(1,11):
    p=ROOT/'execution/pairs'/f'q004-r{i:02d}'/'execution-review.json';d=load(p)
    expected=next(r for r in check['reports'] if r['pair_key']==d['pair_key']);assert sha(p)==expected['sha256']
    profiles={r['profile']:r for r in d['profiles']}
    row={'trial_index':i,'pair_key':d['pair_key'],'profiles':{}}
    for name in ('baseline','zvec-grep'):
        r=profiles[name];assert r['strict_complete'] and r['reported_metric_eligible']
        row['profiles'][name]={'input_tokens':r['primary_usage']['input_tokens'],'output_tokens':r['primary_usage']['output_tokens'],'agent_wall_seconds':r['timing_seconds']['agent_execution'],'outer_slot_seconds':r['timing_seconds']['outer_slot']}
    rows.append(row);references[str(p)]=sha(p)

metrics={'input_reduction_pct':'input_tokens','agent_time_reduction_pct':'agent_wall_seconds','outer_slot_reduction_pct':'outer_slot_seconds'}
def estimates(selected):
    means={name:{metric:stats.mean_or_none([r['profiles'][name][metric] for r in selected]) for metric in ('input_tokens','output_tokens','agent_wall_seconds','outer_slot_seconds')} for name in ('baseline','zvec-grep')}
    comparison={out:(means['baseline'][metric]-means['zvec-grep'][metric])/means['baseline'][metric]*100 for out,metric in metrics.items()}
    return means,comparison
point,comparison=estimates(rows)
rng=random.Random(stats.BOOTSTRAP_SEED);draws={k:[] for k in metrics}
for _ in range(stats.BOOTSTRAP_RESAMPLES):
    indexes=[rng.randrange(1,11) for _ in range(10)]
    _,c=estimates([rows[i-1] for i in indexes])
    for key,value in c.items():draws[key].append(value)
report={'schema_version':1,'computed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'All original ten pairs, raw resource description only. Not a quality, strict-source-validity, filtered or favorable-stability conclusion. No grading, private assignment, historical pooling or dropped rows.','scheduled_solver_trials':20,'retained_solver_trials':20,'profiles':point,'comparison':comparison,'intervals':{k:stats._interval(v,stats.BOOTSTRAP_RESAMPLES) for k,v in draws.items()},'bootstrap':{'seed':stats.BOOTSTRAP_SEED,'resamples':stats.BOOTSTRAP_RESAMPLES,'unit':'whole baseline/zg repetition pair','confidence_level':0.95,'percentile_method':'linear_n_minus_1'},'measurement_scope':'Complete Claude-reported input=uncached+cache-read+cache-write. Output separate. All original strict session totals reconciled; provider/embedding billing unknown.','time_scope':'Original native agent execution excludes setup. Outer slot includes fresh setup/indexing and capture overhead. No shared timing with later q005 independent workers.','quality_known':False,'source_scope_semantics_complete':False,'favorable_valid_benchmark_concluded':False,'method_sha256':sha(Path(__file__)),'frozen_statistics_sha256':{str(p):sha(p) for p in [W/'benchmarks/native-claude-qa-pilot/architecture_repeat_analysis_v1.py',W/'benchmarks/native-claude-qa-pilot/repeat_analysis_v1.py']},'execution_parent_verification_sha256':sha(ROOT/'execution-parent-verification.json'),'references':references,'rows':rows}
out=ROOT/'raw-resource-statistics.json'
with out.open('x') as f:json.dump(report,f,indent=2,allow_nan=False);f.write('\n')
print(json.dumps({'raw_resource_estimates':{k:{'reduction_pct':v,'interval':report['intervals'][k]} for k,v in comparison.items()},'quality_or_favorable_conclusion':False}))
