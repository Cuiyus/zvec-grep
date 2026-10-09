"""Fetch the published Node release without rebuilding or modifying it."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import urllib.request

config = json.loads((Path(__file__).with_name('experiment.json')).read_text())['node']
output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
body = urllib.request.urlopen(config['tarball_url'], timeout=120).read()
assert hashlib.sha256(body).hexdigest() == config['tarball_sha256'], 'Node package SHA256 mismatch'
assert 'sha512-' + base64.b64encode(hashlib.sha512(body).digest()).decode() == config['registry_integrity'], 'npm integrity mismatch'
package = output / 'candidate.tgz'
package.write_bytes(body)
with tarfile.open(package) as archive:
    metadata = json.load(archive.extractfile('package/package.json'))
assert metadata['name'] == '@zvec/zvec-grep' and metadata['version'] == '0.2.2'
assert metadata['bin']['zg'] == 'dist/cli/index.js'
(output / 'manifest.json').write_text(json.dumps({
    'schema_version': 1, 'candidate_commit': config['source_commit'],
    'source_package': config['tarball_url'], 'tarball': 'candidate.tgz',
    'tarball_sha256': config['tarball_sha256'],
}, indent=2) + '\n')
