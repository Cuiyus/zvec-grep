"""Opt-in evidence adapter; the measured zg package is unchanged."""
from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path

from .zvec_opencode import ZvecOpenCode


# Separate from the intentionally content-free usage SQL. No auth/config table.
FULL_SESSION_SQL = """
SELECT 'session' AS kind,
       json_object('id', id, 'parent_id', parent_id) AS data FROM session
UNION ALL
SELECT 'message' AS kind,
       json_object('id', id, 'session_id', session_id, 'content', json(data)) AS data
       FROM message
UNION ALL
SELECT 'part' AS kind,
       json_object('id', id, 'session_id', session_id, 'message_id', message_id,
                   'content', json(data)) AS data FROM part
""".strip()


def corpus_manifest_script() -> str:
    return r"""const fs=require('fs'),cp=require('child_process'),crypto=require('crypto');
const names=[...new Set(cp.execFileSync('git',['ls-files','--cached','--others','--exclude-standard','-z'],{cwd:'/app'}).toString().split('\0').filter(Boolean))].sort();
const entries=names.map(name=>{const p='/app/'+name;try{const b=fs.readFileSync(p);return {path:name,sha256:crypto.createHash('sha256').update(b).digest('hex'),size:b.length}}catch(e){return {path:name,error:e.code}}});
process.stdout.write(JSON.stringify({commit:cp.execFileSync('git',['rev-parse','HEAD'],{cwd:'/app'}).toString().trim(),files:entries}));"""


class CaseEvidenceOpenCode(ZvecOpenCode):
    def __init__(self, *args, evidence_upstream: str, **kwargs):
        self._evidence_upstream = evidence_upstream
        super().__init__(*args, **kwargs)

    async def run(self, instruction, environment, context):
        errors = []
        # Recording endpoints and files are outside the searchable corpus.
        config = {
            "output": "/logs/agent/wire",
            "ready": "/logs/agent/proxy-ready.json",
            "routes": [
                {"name": "model", "port": 18768, "upstream": self._evidence_upstream},
                {"name": "mcp", "port": 18769, "upstream": "http://127.0.0.1:7999"},
            ],
        }
        await environment.upload_file(Path(__file__).with_name("evidence_proxy.mjs"), "/tmp/zg-case-proxy.mjs")
        # Use argv-safe literals, never place an API key in a command.
        await self._checked(environment, command=(
            "set -e; . ~/.nvm/nvm.sh; mkdir -p /logs/agent; "
            f"printf '%s' {shlex.quote(json.dumps(config))} > /tmp/zg-case-proxy.json; "
            "node /tmp/zg-case-proxy.mjs /tmp/zg-case-proxy.json "
            "> /logs/agent/proxy.log 2>&1 & echo $! > /logs/agent/proxy.pid"
        ))
        await self._checked(environment, command=(
            "set -e; for i in $(seq 1 50); do "
            "test ! -f /logs/agent/proxy-ready.json || exit 0; sleep 0.1; done; exit 1"
        ), timeout_sec=10)
        await self._manifest(environment, "before")
        try:
            await super().run(instruction, environment, context)
        finally:
            for label, command in [
                ("sessions", "set -e; . ~/.nvm/nvm.sh; "
                 f"opencode db {shlex.quote(FULL_SESSION_SQL)} --format json "
                 "> /logs/agent/full-sessions.json.tmp; "
                 "mv /logs/agent/full-sessions.json.tmp /logs/agent/full-sessions.json"),
                ("corpus", "set -e; . ~/.nvm/nvm.sh; "
                 f"node -e {shlex.quote(corpus_manifest_script())} > /logs/agent/corpus-after.json"),
                ("proxy", "kill -TERM $(cat /logs/agent/proxy.pid)"),
            ]:
                try:
                    await asyncio.wait_for(self._checked(environment, command=command, timeout_sec=60), 65)
                except Exception as error:
                    errors.append({"stage": label, "error": type(error).__name__})
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            (self.logs_dir / "evidence-capture.json").write_text(json.dumps({
                "schema_version": 1, "errors": errors, "complete": not errors,
            }, indent=2) + "\n")

    async def _manifest(self, environment, label):
        await self._checked(environment, command=(
            "set -e; . ~/.nvm/nvm.sh; "
            f"node -e {shlex.quote(corpus_manifest_script())} > /logs/agent/corpus-{label}.json"
        ), timeout_sec=60)

    async def _checked(self, environment, **kwargs):
        result = await self.exec_as_agent(environment, **kwargs)
        if result.return_code != 0:
            raise RuntimeError("evidence command failed")
        return result
