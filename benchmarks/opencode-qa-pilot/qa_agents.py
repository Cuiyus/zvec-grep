"""Integrity instrumentation around the unchanged, pinned upstream agents."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
import time

from zg_bench.agents.opencode import ResilientOpenCode
from zg_bench.agents.zvec_opencode import ZvecOpenCode


# Executed by the controller, never passed to the model or installed in source.
INVENTORY_SCRIPT = r'''
import hashlib,json,os,pathlib,shutil,subprocess
root=pathlib.Path('/workspace')
files=[]
for current,dirs,names in os.walk(root,followlinks=False):
    for name in sorted(dirs+names):
        p=pathlib.Path(current)/name
        rel=p.relative_to(root).as_posix()
        if p.is_symlink():
            files.append({'path':rel,'kind':'symlink'})
        elif p.is_file():
            row={'path':rel,'kind':'file','bytes':p.stat().st_size}
            if not rel.startswith('.zvec-grep/'):
                row['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
            files.append(row)
home=pathlib.Path.home()
config=home/'.config/opencode/opencode.json'
mcp=False
if config.exists():
    try: mcp=bool(json.loads(config.read_text()).get('mcp'))
    except Exception: mcp=True
runtime={}
for name in ('opencode','zg'):
    found=shutil.which(name)
    if found:
        path=pathlib.Path(found).resolve()
        binary=path.with_name('opencode.exe') if name=='opencode' and path.with_name('opencode.exe').is_file() else path
        runtime[name]={'entrypoint':str(path),'entrypoint_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'binary_path':str(binary),'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'version':subprocess.check_output([str(path),'--version'],text=True,timeout=20).strip()}
runtime['resolved_config_sha256']=hashlib.sha256(config.read_bytes()).hexdigest() if config.is_file() else None
agents=home/'.config/opencode/AGENTS.md'
runtime['global_agents_sha256']=hashlib.sha256(agents.read_bytes()).hexdigest() if agents.is_file() else None
print(json.dumps({'files':sorted(files,key=lambda x:x['path']),
 'zg_executable_present':shutil.which('zg') is not None,
 'zg_home_present':(home/'.zvec-grep').exists() or (home/'.config/zvec-grep').exists(),
 'embedding_environment_names':[k for k in ('ZVEC_GREP_API_KEY','DASHSCOPE_API_KEY','QWEN_API_KEY','QWEN_EMBEDDING_API_KEY') if os.environ.get(k)],
 'mcp_config_present':mcp,'runtime':runtime}))
'''


def compare_inventory(observed: dict, expected: list[dict], profile: str) -> dict:
    wanted = {row['path']: row for row in expected}
    actual = {row['path']: row for row in observed['files']}
    errors = []
    for name, row in wanted.items():
        got = actual.get(name, {})
        if got.get('kind') != 'file' or any(got.get(k) != row[k] for k in ('bytes', 'sha256')):
            errors.append('source_changed_or_missing:' + name)
    additions = [row for name, row in actual.items() if name not in wanted]
    for row in additions:
        name = row['path']
        allowed = profile == 'zvec-grep' and (
            name.startswith('.zvec-grep/') or name == '.zg-bench-index.ignore'
        )
        if not allowed or row['kind'] != 'file':
            errors.append('unapproved_added_path:' + name)
    if profile == 'baseline':
        for key in ('zg_executable_present', 'zg_home_present', 'mcp_config_present'):
            if observed.get(key) is not False:
                errors.append('baseline_contamination:' + key)
        if observed.get('embedding_environment_names'):
            errors.append('baseline_contamination:embedding_environment')
    return {'valid': not errors, 'errors': errors, 'additions': additions,
            'observed': observed}


class IntegrityAuditMixin:
    """No instruction, model, tool or budget changes; host-side before/after audit."""

    def __init__(self, *args, qa_source_manifest: str, qa_profile: str, **kwargs):
        if qa_profile not in ('baseline', 'zvec-grep'):
            raise ValueError('invalid QA profile')
        self._qa_manifest = json.loads(Path(qa_source_manifest).read_text())
        self._qa_profile = qa_profile
        self._qa_audit = {'schema_version': 1, 'profile': qa_profile, 'valid': False,
                          'before': None, 'after': None, 'errors': [],
                          'audit_wall_seconds': 0.0, 'export_wall_seconds': 0.0,
                          'session_exports_complete': False}
        super().__init__(*args, **kwargs)

    def _save_qa_audit(self):
        # Outside downloaded /logs/agent, and never uploaded into the container.
        path = self.logs_dir.parent / 'qa-source-audit.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._qa_audit, indent=2, sort_keys=True) + '\n')

    async def _audit_source(self, environment):
        started = time.monotonic()
        try:
            result = await environment.exec(
                command='. ~/.nvm/nvm.sh; python3 -c ' + shlex.quote(INVENTORY_SCRIPT), timeout_sec=120)
            if result.return_code != 0:
                raise RuntimeError('source_inventory_command_failed')
            return compare_inventory(json.loads(result.stdout),
                                     self._qa_manifest['files'], self._qa_profile)
        finally:
            self._qa_audit['audit_wall_seconds'] += time.monotonic() - started

    async def run(self, instruction, environment, context):
        try:
            self._qa_audit['before'] = await self._audit_source(environment)
            if not self._qa_audit['before']['valid']:
                raise RuntimeError('pre_agent_source_or_isolation_audit_failed')
            await super().run(instruction, environment, context)
        finally:
            try:
                self._qa_audit['after'] = await self._audit_source(environment)
            except Exception as exc:
                self._qa_audit['errors'].append(type(exc).__name__)
            started = time.monotonic()
            try:
                # Native usage export is already present in the container at this point.
                captured = await environment.exec(
                    command='cat /logs/agent/opencode-session-snapshot.json', timeout_sec=30)
                if captured.return_code != 0:
                    raise RuntimeError('native_usage_snapshot_missing')
                sessions = []
                for row in json.loads(captured.stdout):
                    data = row['data']
                    if isinstance(data, str):
                        data = json.loads(data)
                    if row['kind'] == 'session':
                        sessions.append(data)
                roots = [s['id'] for s in sessions if not s.get('parent_id')]
                if len(roots) != 1:
                    raise RuntimeError('fresh_trial_requires_exactly_one_root_session')
                selected = {roots[0]}
                while True:
                    expanded = selected | {s['id'] for s in sessions if s.get('parent_id') in selected}
                    if expanded == selected:
                        break
                    selected = expanded
                exports = self.logs_dir.parent / 'qa-session-exports'
                exports.mkdir(exist_ok=True)
                for session_id in sorted(selected):
                    if not session_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in session_id):
                        raise RuntimeError('unsafe_session_id')
                    result = await environment.exec(
                        command='. ~/.nvm/nvm.sh; opencode export ' + shlex.quote(session_id),
                        timeout_sec=60)
                    if result.return_code != 0:
                        raise RuntimeError('session_export_failed')
                    data = json.loads(result.stdout)
                    if data.get('info', {}).get('id') != session_id:
                        raise RuntimeError('session_export_identity_mismatch')
                    (exports / (session_id + '.json')).write_text(result.stdout)
                self._qa_audit['exported_root_session_id'] = roots[0]
                self._qa_audit['exported_sessions'] = sorted(selected)
                self._qa_audit['session_exports_complete'] = True
            except Exception as exc:
                self._qa_audit['errors'].append('session_export:' + type(exc).__name__)
            finally:
                self._qa_audit['export_wall_seconds'] = time.monotonic() - started
            self._qa_audit['valid'] = (
                not self._qa_audit['errors']
                and all(isinstance(self._qa_audit[k], dict)
                        and self._qa_audit[k].get('valid') is True
                        for k in ('before', 'after')))
            self._save_qa_audit()


class QABaseline(IntegrityAuditMixin, ResilientOpenCode):
    pass


class QAZvec(IntegrityAuditMixin, ZvecOpenCode):
    pass
