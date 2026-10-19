"""Replay saved native mock output; no native/model/container calls are made."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent / 'diagnostic-fixtures'
checked = 0
for stdout in sorted(root.glob('case-*/*.export.json')):
    stderr = stdout.with_suffix('.stderr')
    json.loads(stdout.read_text())
    assert stderr.read_text().startswith('Exporting session: ')
    try:
        json.loads(stderr.read_text() + stdout.read_text())
    except json.JSONDecodeError:
        checked += 1
    else:
        raise AssertionError('Expected merged stderr/JSON parse failure')
assert checked == 12
print(json.dumps({'retained_native_exports': checked, 'merge_parse_failures': checked,
                  'live_export_payload_available': False, 'provider_calls': 0}))
