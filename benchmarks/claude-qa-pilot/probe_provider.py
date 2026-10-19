"""Small protocol probe; credentials stay in the process environment."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

base = os.environ['ANTHROPIC_BASE_URL'].rstrip('/')
key = os.environ['ANTHROPIC_AUTH_TOKEN']
model = os.environ.get('ANTHROPIC_MODEL', 'qwen3.8-max')
payload = {'model': model, 'max_tokens': 64,
           'messages': [{'role': 'user', 'content': 'Reply with OK only.'}]}
request = urllib.request.Request(base + '/v1/messages',
    data=json.dumps(payload).encode(), headers={
        'Content-Type': 'application/json', 'x-api-key': key,
        'anthropic-version': '2023-06-01'})
result = {'requested_model': model, 'endpoint': base}
try:
    with urllib.request.urlopen(request, timeout=45) as response:
        data = json.load(response)
        result.update(http_status=response.status, model=data.get('model'),
                      usage=data.get('usage'), content=data.get('content'))
        result['ok'] = response.status == 200 and data.get('model') == model
except urllib.error.HTTPError as error:
    result.update(http_status=error.code, ok=False,
                  error=error.read().decode().replace(key, '[REDACTED]')[:1500])
except Exception as error:
    result.update(ok=False, error=str(error).replace(key, '[REDACTED]')[:1000])
Path('provider-probe.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(0 if result.get('ok') else 1)
