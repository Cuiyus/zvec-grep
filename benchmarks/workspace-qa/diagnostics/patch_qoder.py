"""Add two passive probes to the exact published Qoder 1.1.45 bundle, diagnostics only."""
import hashlib
import json
from pathlib import Path
import sys

PIN = '86565469a3a0dd2dcede554c6056678cb7423f12e1929b4b23b9d6c340559418'

def patch(path: Path, observer: str) -> dict:
    original = path.read_bytes()
    if hashlib.sha256(original).hexdigest() != PIN:
        raise ValueError('Published Qoder 1.1.45 bundle hash mismatch')
    text = original.decode()
    replacements = {
        'this.cachedUserContext=e,e}clearUserContextCache(){':
            'this.cachedUserContext=e,__zgRoutingObserve("memory_context",e),e}clearUserContextCache(){',
        'function U3e(A,e){':
            'function U3e(A,e){__zgRoutingObserve("transport_request",A);',
    }
    for before, after in replacements.items():
        if text.count(before) != 1:
            raise ValueError('Probe anchor is not unique')
        text = text.replace(before, after, 1)
    first, rest = text.split('\n', 1)
    text = first + '\nimport { observe as __zgRoutingObserve } from ' + json.dumps(observer) + ';\n' + rest
    path.write_text(text)
    return {'diagnostic_only': True, 'published_bundle_sha256': PIN,
            'instrumented_bundle_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'observer_sha256': hashlib.sha256(Path(observer).read_bytes()).hexdigest(),
            'probe_points': ['MemoryContextManager.getUserContext', 'U3e transport request normalization'],
            'changes_request_or_model_response': False, 'server_received_body_independently_verified': False}

if __name__ == '__main__':
    evidence = patch(Path(sys.argv[1]), sys.argv[2])
    Path(sys.argv[3]).write_text(json.dumps(evidence, indent=2) + '\n')
