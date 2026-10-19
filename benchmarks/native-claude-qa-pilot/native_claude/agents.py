"""Evidence-only wrappers around the pinned native Claude adapters.

The inherited setup, run command, prompt rendering and post-run trajectory
conversion are unchanged. Harbor downloads its own agent directory before
calling populate_context_post_run, including in SingleStepTrial failure
recovery. Our separate byte snapshot is taken while the environment still
exists; it never replaces that native download or invokes the converter early.

Raw native logs are preserved exactly. The current fake-provider gate contains
no live credentials; publishing future live logs requires a separate secret
scan. Configuration *audit content* is redacted inside the container before
transport, while the original byte hashes are retained.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shlex
import time

from harbor.agents.installed.claude_code import ClaudeCode
from zg_bench.agents.zvec_claude_code import ZvecClaudeCode
from qa_agents import compare_inventory
from repair_v2.evidence import capture_json


RUNTIME_PAYLOADS = ('bin/zg', 'bin/libzvec_c_api.so',
                    'bin/data/jieba_dict/hmm_model.utf8',
                    'bin/data/jieba_dict/jieba.dict.utf8')


# Shared source inventory rules match the existing frozen q001 protocol. Only
# Claude runtime/configuration paths replace the OpenCode-specific inspection.
INVENTORY_SCRIPT = r'''
import hashlib,json,os,pathlib,re,shutil,subprocess
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
sessions=pathlib.Path('/logs/agent/sessions')
secret_key=re.compile(r'(api.?key|auth.?token|authorization|password|secret|credential|oauth.?token|access.?token|bearer)',re.I)
secret_values=sorted({v for k,v in os.environ.items() if v and secret_key.search(k)},key=len,reverse=True)
def clean_text(value):
    for secret in secret_values:
        value=value.replace(secret,'[REDACTED]')
    return value
def clean_json(value):
    if isinstance(value,dict):
        return {k:('[REDACTED]' if secret_key.search(k) and v is not None else clean_json(v)) for k,v in value.items()}
    if isinstance(value,list): return [clean_json(v) for v in value]
    if isinstance(value,str): return clean_text(value)
    return value
def has_mcp(value):
    if isinstance(value,dict):
        return bool(value.get('mcpServers')) or any(has_mcp(v) for v in value.values())
    if isinstance(value,list): return any(has_mcp(v) for v in value)
    return False
configs={}
mcp=False
paths=[home/'.claude.json',root/'.mcp.json',root/'CLAUDE.md',root/'.claude/settings.json',root/'.claude/settings.local.json']
for directory in (home/'.claude',sessions):
    paths.extend(directory/name for name in ('.claude.json','settings.json','settings.local.json','CLAUDE.md'))
for path in paths:
    row={'present':path.exists() or path.is_symlink()}
    if row['present']:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('configuration_not_regular:'+str(path))
        raw=path.read_bytes()
        text=raw.decode('utf-8')
        row.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        if path.suffix=='.json':
            data=json.loads(text)
            mcp=mcp or has_mcp(data)
            clean=clean_json(data)
            row.update(content=clean,content_format='json',redacted=clean!=data)
        else:
            clean=clean_text(text)
            row.update(content=clean,content_format='text',redacted=clean!=text)
    configs[str(path)]=row
runtime={}
for name in ('claude','zg'):
    found=shutil.which(name)
    if found:
        path=pathlib.Path(found).resolve()
        runtime[name]={'entrypoint':str(path),'entrypoint_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'binary_path':str(path),'binary_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'version':subprocess.check_output([str(path),'--version'],text=True,timeout=20).strip()}
if 'zg' in runtime:
    package_root=pathlib.Path(runtime['zg']['binary_path']).parent.parent
    payloads={}
    for relative in ('bin/zg','bin/libzvec_c_api.so','bin/data/jieba_dict/hmm_model.utf8','bin/data/jieba_dict/jieba.dict.utf8'):
        payload=package_root/relative
        if payload.is_symlink() or not payload.is_file() or not payload.resolve().is_relative_to(package_root):
            raise RuntimeError('installed_runtime_payload_not_regular:'+relative)
        payloads[relative]={'bytes':payload.stat().st_size,'sha256':hashlib.sha256(payload.read_bytes()).hexdigest(),
            'mode':format(payload.stat().st_mode & 0o777,'04o')}
    runtime['zg'].update(package_root=str(package_root),payloads=payloads)
print(json.dumps({'files':sorted(files,key=lambda x:x['path']),
 'zg_executable_present':shutil.which('zg') is not None,
 'zg_home_present':(home/'.zvec-grep').exists() or (home/'.config/zvec-grep').exists(),
 'embedding_environment_names':[k for k in ('ZVEC_GREP_API_KEY','DASHSCOPE_API_KEY','QWEN_API_KEY','QWEN_EMBEDDING_API_KEY') if os.environ.get(k)],
 'mcp_config_present':mcp,'runtime':runtime,'configurations':configs,
 'claude_config_dir':'/logs/agent/sessions'}))
'''


LOG_INVENTORY_SCRIPT = r'''
import hashlib,json,os,pathlib
root=pathlib.Path('/logs/agent')
if not root.is_dir() or root.is_symlink(): raise RuntimeError('native_log_directory_missing')
files=[]
for current,dirs,names in os.walk(root,followlinks=False):
    for name in sorted(dirs+names):
        p=pathlib.Path(current)/name
        relative=p.relative_to(root).as_posix()
        if p.is_symlink(): raise RuntimeError('native_log_symlink:'+relative)
        if p.is_file():
            raw=p.read_bytes()
            files.append({'path':relative,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
print(json.dumps({'root':str(root),'files':sorted(files,key=lambda row:row['path'])}))
'''


def local_log_inventory(directory: Path) -> list[dict]:
    """Hash all downloaded root and child files without following symlinks."""
    files = []
    for current, dirs, names in os.walk(directory, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(current) / name
            relative = path.relative_to(directory).as_posix()
            if path.is_symlink():
                raise ValueError('native_log_symlink:' + relative)
            if path.is_file():
                raw = path.read_bytes()
                files.append({'path': relative, 'bytes': len(raw),
                              'sha256': hashlib.sha256(raw).hexdigest()})
    return sorted(files, key=lambda row: row['path'])


def compare_claude_inventory(observed: dict, expected: list[dict], profile: str) -> dict:
    result = compare_inventory(observed, expected, profile)
    errors = result['errors']
    if not observed.get('runtime', {}).get('claude'):
        errors.append('claude_runtime_missing')
    if profile == 'zvec-grep':
        if set(observed.get('runtime', {}).get('zg', {}).get('payloads', {})) != set(RUNTIME_PAYLOADS):
            errors.append('zg_runtime_payloads_missing')
        for name in ('.claude.json', 'settings.json', 'CLAUDE.md'):
            path = '/logs/agent/sessions/' + name
            if not observed.get('configurations', {}).get(path, {}).get('present'):
                errors.append('claude_treatment_configuration_missing:' + name)
        if observed.get('mcp_config_present') is not True:
            errors.append('claude_treatment_mcp_missing')
    result['valid'] = not errors
    return result


class IntegrityAuditMixin:
    def __init__(self, *args, qa_source_manifest: str, qa_profile: str, **kwargs):
        if qa_profile not in ('baseline', 'zvec-grep'):
            raise ValueError('invalid QA profile')
        self._qa_manifest = json.loads(Path(qa_source_manifest).read_bytes())
        self._qa_profile = qa_profile
        self._qa_audit = {
            'schema_version': 1, 'engine': 'native-claude', 'profile': qa_profile,
            'valid': False, 'before': None, 'after': None, 'errors': [],
            'native_run_completed': False, 'native_run_error_type': None,
            'native_run_wall_seconds': None, 'audit_wall_seconds': 0.0,
            'export_wall_seconds': 0.0, 'native_logs_complete': False,
            'native_logs': {}, 'evidence_transport': 'file-download-v2',
            'native_context_population': 'unchanged Harbor after native log download',
        }
        super().__init__(*args, **kwargs)

    @property
    def _qa_evidence_dir(self) -> Path:
        return self.logs_dir.parent / 'qa-native-claude-evidence'

    def _save_qa_audit(self) -> None:
        path = self.logs_dir.parent / 'qa-source-audit.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._qa_audit, indent=2, sort_keys=True) + '\n')

    async def _capture(self, environment, label: str, script: str):
        command = ('set -e; if [ -s "$HOME/.nvm/nvm.sh" ]; then . "$HOME/.nvm/nvm.sh"; fi; '
                   'export PATH="$HOME/.local/bin:$PATH"; python3 -c ' + shlex.quote(script))
        return await capture_json(environment, self._qa_evidence_dir, label, command, 120)

    async def _audit_source(self, environment, phase: str) -> dict:
        started = time.monotonic()
        try:
            captured = await self._capture(environment, 'source-' + phase, INVENTORY_SCRIPT)
            return compare_claude_inventory(captured.data, self._qa_manifest['files'], self._qa_profile)
        finally:
            self._qa_audit['audit_wall_seconds'] += time.monotonic() - started

    async def _export_logs(self, environment) -> None:
        """Try every transfer even if the first inventory fails; retain partials."""
        destination = self.logs_dir.parent / 'qa-native-claude-logs'
        record = self._qa_audit['native_logs']
        record.update(source='/logs/agent', host_directory=destination.name,
                      download_complete=False, before=None, after=None, files=None,
                      errors=[])
        # No overwrite of earlier attempts or Harbor's native destination.
        destination.mkdir(exist_ok=False)
        cancellation = None
        for phase in ('before', 'download', 'after', 'local'):
            try:
                if phase == 'download':
                    await asyncio.wait_for(environment.download_dir('/logs/agent', destination), 120)
                    record['download_complete'] = True
                elif phase == 'local':
                    record['files'] = local_log_inventory(destination)
                else:
                    captured = await self._capture(environment, 'logs-' + phase, LOG_INVENTORY_SCRIPT)
                    record[phase] = captured.data
            except (Exception, asyncio.CancelledError) as exc:
                record['errors'].append(phase + ':' + type(exc).__name__)
                if isinstance(exc, asyncio.CancelledError):
                    cancellation = exc
        before, after = record['before'], record['after']
        self._qa_audit['native_logs_complete'] = bool(
            not record['errors'] and record['download_complete']
            and isinstance(before, dict) and isinstance(after, dict)
            and before.get('root') == after.get('root') == '/logs/agent'
            and before.get('files') == record['files'] == after.get('files')
            and record['files'])
        if not self._qa_audit['native_logs_complete']:
            self._qa_audit['errors'].append('native_logs_incomplete_or_changed')
        if cancellation is not None:
            raise cancellation

    async def run(self, instruction, environment, context):
        original_error = None
        cancellation = None
        try:
            self._qa_audit['before'] = await self._audit_source(environment, 'before')
            if not self._qa_audit['before']['valid']:
                raise RuntimeError('pre_agent_source_or_isolation_audit_failed')
            started = time.monotonic()
            try:
                await super().run(instruction, environment, context)
                self._qa_audit['native_run_completed'] = True
            finally:
                self._qa_audit['native_run_wall_seconds'] = time.monotonic() - started
        except (Exception, asyncio.CancelledError) as exc:
            original_error = exc
            self._qa_audit['native_run_error_type'] = type(exc).__name__
            raise
        finally:
            try:
                self._qa_audit['after'] = await self._audit_source(environment, 'after')
            except (Exception, asyncio.CancelledError) as exc:
                self._qa_audit['errors'].append('source_after:' + type(exc).__name__)
                if isinstance(exc, asyncio.CancelledError):
                    cancellation = exc
            started = time.monotonic()
            try:
                await self._export_logs(environment)
            except (Exception, asyncio.CancelledError) as exc:
                self._qa_audit['errors'].append('native_log_export:' + type(exc).__name__)
                if isinstance(exc, asyncio.CancelledError):
                    cancellation = exc
            finally:
                self._qa_audit['export_wall_seconds'] = time.monotonic() - started
                self._qa_audit['valid'] = bool(
                    self._qa_audit['native_run_completed'] and not self._qa_audit['errors']
                    and self._qa_audit['native_logs_complete']
                    and all(isinstance(self._qa_audit[k], dict)
                            and self._qa_audit[k].get('valid') is True for k in ('before', 'after')))
                self._save_qa_audit()
            if original_error is None:
                if cancellation is not None:
                    raise cancellation
                if not self._qa_audit['valid']:
                    raise RuntimeError('native_claude_evidence_invalid')


class QABaseline(IntegrityAuditMixin, ClaudeCode):
    pass


class QAZvec(IntegrityAuditMixin, ZvecClaudeCode):
    pass
