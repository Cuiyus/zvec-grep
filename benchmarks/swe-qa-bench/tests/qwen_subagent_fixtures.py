"""Missing/retry evidence must never certify the no-subagent treatment."""
import json
from pathlib import Path
import tempfile
import unittest

from zg_bench.agents.opencode_usage import summarize_session_usage
from zg_bench.qwen_subagent_audit import ARMS, BUILTIN_AGENTS, DENIED_ARMS, audit
from zg_bench.qwen_subagent_summary import summarize


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def attempt(root, arm, rep=1, *, retry=False, child=False, failed=False):
    base = root / (f'{arm}/runs' if arm in ('baseline_allowed', 'baseline') else f'runs/{arm}') / f'r{rep}' / 'job'
    name = f'task__{arm}{rep}'
    directory = base / (f'.retry-history/{name}/attempt-1' if retry else name)
    permissions = {'websearch': 'deny', 'webfetch': 'deny'}
    if arm in DENIED_ARMS:
        permissions['task'] = 'deny'
    config = dict(trial_name=name, task={}, agent={'kwargs': {'opencode_config': {
        'permission': dict(permissions),
        'agent': {key: {'permission': dict(permissions)} for key in BUILTIN_AGENTS}}}})
    cfg = config['agent']['kwargs']['opencode_config']
    config['agent'].update(name=('zg_bench.agents.opencode:ResilientOpenCode' if arm.startswith('baseline') else 'zg_bench.agents.zvec_opencode:ZvecOpenCode'),
                           model_name='custom-openai/qwen3.8-max')
    config['agent']['kwargs'].update(version='1.18.4', collect_session_usage=True)
    cfg.update(model='custom-openai/qwen3.8-max', provider={'custom-openai': {'models': {'qwen3.8-max': {
        'options': {'enable_thinking': True, 'reasoningEffort': 'high'},
        'interleaved': {'field': 'reasoning_content'}, 'limit': {'output': 32000}}}}})
    for agent in cfg['agent'].values():
        agent.update(temperature=0, options={'seed': 42})
    write(directory / 'config.json', config)
    identity = name + ('-failed' if retry else '-current')
    write(directory / 'result.json', dict(id=identity, trial_name=name, config=config, finished_at='2026-10-22T00:00:00Z',
                                          exception_info={'exception_type': 'TimeoutError'} if failed else None))
    snapshot = [{'kind': 'session', 'data': {'id': 'root', 'parent_id': None}},
                {'kind': 'message', 'data': dict(id='msg', session_id='root', provider_id='custom-openai',
                                               model_id='qwen3.8-max', cost_usd=0, input=2, output=3, reasoning=7,
                                               cache_read=10, cache_write=0, tokens_present=True,
                                               finished=True, has_error=False)}]
    if child:
        snapshot += [{'kind': 'session', 'data': {'id': 'child', 'parent_id': 'root'}},
                     {'kind': 'tool', 'data': dict(id='tool', name='task', session_id='root', message_id='msg',
                                                 status='completed', child_session_id='child')},
                     {'kind': 'message', 'data': dict(id='child-msg', session_id='child', provider_id='custom-openai',
                                                    model_id='qwen3.8-max', cost_usd=0, input=5, output=2, reasoning=3,
                                                    cache_read=0, cache_write=0, tokens_present=True,
                                                    finished=True, has_error=False)}]
    write(directory / 'agent/opencode-session-snapshot.json', snapshot)
    usage = summarize_session_usage(snapshot, 'root')
    write(directory / 'agent/session-usage.json', usage)
    write(directory / 'agent/trajectory.json', {'steps': []})
    history = {'schema_version': 1, 'max_retries': 2, 'trials': {name: [
        {'attempt': 1, 'trial_id': name + '-current', 'exception_type': None, 'archive': None}]}}
    if retry:
        history['trials'][name] = [
            {'attempt': 1, 'trial_id': identity, 'exception_type': 'TimeoutError', 'archive': f'{name}/attempt-1'},
            {'attempt': 2, 'trial_id': name + '-current', 'exception_type': None, 'archive': None}]
    write(base / '.retry-history/history.json', history)
    return directory, usage


def clean(root, reps=1):
    for arm in ARMS:
        for rep in range(1, reps + 1):
            attempt(root, arm, rep)
    return audit(root, len(ARMS) * reps)


def report(root, reps=1, task_id='astropy:38', *, child_allowed=False):
    evidence = clean(root, reps)
    if child_allowed:
        attempt(root, 'baseline_allowed', child=True)
        evidence = audit(root, len(ARMS) * reps)
    profiles = {}
    for arm in ARMS:
        trials = []
        for rep in range(1, reps + 1):
            entry = next(x for x in evidence['attempts'] if x['arm'] == arm and x['repetition'] == rep)
            path = root / entry['path']
            usage = json.loads((path / 'agent/session-usage.json').read_text())
            trials.append(dict(trial_index=rep, trial_name=entry['trial_name'], judge={'total': rep * 10},
                               metrics=dict(usage['total'], agent_wall_seconds=2, usage_scope=usage['scope'],
                                            session_usage=usage)))
        profiles[arm] = {'trial_count': len(trials), 'trials': trials}
    return dict(task_id=task_id, model='qwen3.8-max', profiles=profiles, provenance=dict(
        repetitions=reps, experiment={'no_subagent': {'enabled': True, 'mechanism': 'permission.task=deny',
                                                      'denied_arms': list(DENIED_ARMS), 'allowed_control': 'baseline_allowed'}},
        identities={'rust': 'frozen'}, harness_commit='f' * 40, no_subagent_audit=evidence,
        execution=[dict(arm=arm, repetition=rep, returncode=0, wall_seconds_including_setup_and_retries=2)
                   for arm in ARMS for rep in range(1, reps + 1)]))


