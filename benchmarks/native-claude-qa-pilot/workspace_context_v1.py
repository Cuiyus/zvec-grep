"""Shared native Claude environment context; no task or quality-rule changes."""
from __future__ import annotations

import hashlib
import json
import shlex


CONTEXT = (
    'Execution environment: the supplied zvec repository is already present at '
    '/workspace, which is the working directory. Use /workspace as the repository '
    'root for source reads, local searches and retrieval. Stay within the supplied '
    'source tree; do not list, read or search filesystem roots, home directories, '
    'runtime/log directories or evaluator artifacts outside it. This is a '
    'read-only source QA task; the original task instructions and answer '
    'requirements still apply.'
)
CONTEXT_SHA256 = hashlib.sha256(CONTEXT.encode('utf8')).hexdigest()
# Harbor 0.18's string-flag renderer appends values verbatim into a shell
# command. Preserve one shell-quoting layer through its JSON-aware kwarg parser;
# Claude receives the exact CONTEXT text after shell tokenization.
KWARG = 'append_system_prompt=' + json.dumps(shlex.quote(CONTEXT))


def append_context(command: list[str]) -> list[str]:
    """Use Harbor's existing Claude flag while preserving every original arg."""
    if not isinstance(command, list) or not command or any(not isinstance(v, str) for v in command):
        raise ValueError('native_command_required')
    if '--agent' not in command or '--model' not in command:
        raise ValueError('native_agent_and_model_required')
    if any(v.startswith(('append_system_prompt=', '--append-system-prompt')) for v in command):
        raise ValueError('existing_system_append_must_not_be_replaced_or_duplicated')
    return [*command, '--agent-kwarg', KWARG]


def verify_context_command(original: list[str], actual: list[str]) -> dict:
    if actual != append_context(original):
        raise ValueError('command_changed_beyond_shared_environment_context')
    return {'schema_version': 1, 'context_sha256': CONTEXT_SHA256,
            'original_arguments_unchanged': True, 'native_append_count': 1,
            'question_and_rubric_changed': False, 'tools_or_native_loop_changed': False}
