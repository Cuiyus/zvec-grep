// Diagnostic-only HTTP observers for the frozen OpenCode E2E experiment.
import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import zlib from 'node:zlib';

const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
fs.mkdirSync(config.output, { recursive: true });
const secrets = new Set();
const active = new Map();
const servers = [];
const httpAgent = new http.Agent({ keepAlive: true, timeout: 0 });
const httpsAgent = new https.Agent({ keepAlive: true, timeout: 0 });
const safeHeaders = new Set(['content-type', 'content-length', 'content-encoding', 'retry-after',
  'request-id', 'x-request-id', 'x-maas-request-id', 'x-trace-id', 'traceparent',
  'x-session-id', 'x-opencode-session-id', 'x-parent-session-id', 'mcp-session-id', 'mcp-protocol-version']);
const hopHeaders = new Set(['host', 'connection', 'proxy-connection', 'keep-alive',
  'transfer-encoding', 'te', 'trailer', 'upgrade']);
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
function scrub(value) {
  if (typeof value === 'string') {
    for (const secret of secrets) value = value.split(secret).join('[REDACTED]');
    return value;
  }
  if (Array.isArray(value)) return value.map(scrub);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) =>
    [k, /^(authorization|api_?key|access_token|client_secret|cookie|set-cookie)$/i.test(k) ? '[REDACTED]' : scrub(v)]));
  return value;
}
function event(type, fields = {}) {
  fs.appendFileSync(path.join(config.output, 'events.jsonl'), JSON.stringify(scrub({
    time: new Date().toISOString(), monotonic_ms: performance.now(), type, ...fields,
  })) + '\n');
}
function headersForLog(headers) {
  return Object.fromEntries(Object.entries(headers).filter(([key]) => safeHeaders.has(key.toLowerCase())));
}
function snapshot(reason) {
  const processes = [];
  try {
    for (const pid of fs.readdirSync('/proc').filter(p => /^\d+$/.test(p))) {
      try {
        const comm = fs.readFileSync(`/proc/${pid}/comm`, 'utf8').trim();
        if (!/opencode|zg|node|git|grep|python|bash|sh/.test(comm)) continue;
        const status = fs.readFileSync(`/proc/${pid}/status`, 'utf8').split('\n')
          .filter(line => /^(Name|State|Pid|PPid|Threads|VmRSS|voluntary_ctxt_switches|nonvoluntary_ctxt_switches):/.test(line));
        processes.push({ pid, comm, status, wchan: fs.readFileSync(`/proc/${pid}/wchan`, 'utf8'),
          stat: fs.readFileSync(`/proc/${pid}/stat`, 'utf8') });
      } catch { /* A process may exit during the snapshot. */ }
    }
  } catch { /* /proc is absent in local macOS protocol tests. */ }
  event('snapshot', { reason, active: [...active.values()].map(row => ({ ...row,
    elapsed_ms: performance.now() - row.start_ms, idle_ms: performance.now() - row.last_ms })), processes });
}
function copyNativeLogs() {
  for (const [name, folder] of [['opencode', path.join(os.homedir(), '.local/share/opencode/log')],
    ['zg', path.join(os.homedir(), '.zvec-grep/logs')]]) {
    if (!fs.existsSync(folder)) continue;
    const out = path.join(config.output, 'native-logs', name);
    fs.mkdirSync(out, { recursive: true });
    for (const file of fs.readdirSync(folder)) {
      const source = path.join(folder, file);
      if (!fs.statSync(source).isFile() || !/\.log$/.test(file)) continue;
      fs.writeFileSync(path.join(out, file), scrub(fs.readFileSync(source, 'utf8')));
    }
  }
}
function observer(id, kind, contentType, encoding) {
  let pending = '', json = '', seq = 0;
  const decoder = new TextDecoder();
  function text(chunk, final = false) {
    const value = decoder.decode(chunk, { stream: !final });
    if (!contentType.includes('text/event-stream')) { json += value; return; }
    pending += value;
    let end;
    while ((end = pending.indexOf('\n')) !== -1) {
      const line = pending.slice(0, end).replace(/\r$/, ''); pending = pending.slice(end + 1);
      if (!line) continue;
      let data = line;
      if (line.startsWith('data:')) {
        try { data = JSON.parse(line.slice(5).trim()); } catch { data = line.slice(5).trim(); }
      }
      fs.appendFileSync(path.join(config.output, `${id}.response.jsonl`),
        JSON.stringify(scrub({ time: new Date().toISOString(), seq: ++seq, data })) + '\n');
      if (data && typeof data === 'object') {
        if (data.id || data.error || data.usage) event('response_metadata', { id, kind,
          response_id: data.id, error: data.error, usage: data.usage });
        for (const choice of data.choices ?? []) {
          for (const tool of choice.delta?.tool_calls ?? []) event('tool_argument_delta', {
            id, index: tool.index, call_id: tool.id, name: tool.function?.name,
            arguments: tool.function?.arguments });
          if (choice.finish_reason) event('finish_reason', { id, reason: choice.finish_reason });
        }
        if (kind === 'rpc') event('rpc_response', { id, rpc_id: data.id,
          error: data.error, has_result: Object.hasOwn(data, 'result') });
      }
    }
  }
  let inflate;
  if (/gzip|deflate/.test(encoding ?? '')) {
    inflate = encoding.includes('gzip') ? zlib.createGunzip() : zlib.createInflate();
    inflate.on('data', chunk => text(chunk));
    inflate.on('error', error => event('observer_decode_error', { id, error: error.message }));
  }
  return {
    write(chunk) { if (inflate) inflate.write(chunk); else text(chunk); },
    end() {
      const finish = () => {
        text(new Uint8Array(), true);
        if (pending.trim()) event('incomplete_sse_line', { id, line: pending });
        if (json) {
          let data = json; try { data = JSON.parse(json); } catch { /* Retain error response text. */ }
          fs.writeFileSync(path.join(config.output, `${id}.response.json`), JSON.stringify(scrub(data)) + '\n');
          if (kind === 'rpc' && data && typeof data === 'object') event('rpc_response', {
            id, rpc_id: data.id, error: data.error, has_result: Object.hasOwn(data, 'result') });
        }
      };
      if (inflate) { inflate.once('end', finish); inflate.end(); } else finish();
    },
  };
}
async function handle(kind, origin, req, res) {
  if (req.url === '/__trace/health' || req.url === '/__trace/snapshot') {
    if (req.url.endsWith('snapshot')) { snapshot('harness_finally'); copyNativeLogs(); }
    res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"ready":true}'); return;
  }
  const id = `${kind}-${crypto.randomUUID()}`;
  const start = performance.now();
  const row = { id, kind, method: req.method, path: req.url.split('?')[0],
    start_ms: start, last_ms: start, phase: 'request_body', bytes: 0 };
  active.set(id, row);
  const auth = req.headers.authorization;
  if (auth) { secrets.add(auth); const key = auth.replace(/^Bearer\s+/i, ''); if (key) secrets.add(key); }
  event('request_start', { ...row, headers: headersForLog(req.headers) });
  let outgoing;
  let response;
  let obs;
  let finished = false;
  function fail(error, phase) {
    if (finished) return;
    finished = true; obs?.end();
    event('request_error', { id, kind, phase, name: error.name, code: error.code,
      error: error.message, elapsed_ms: performance.now() - start });
    active.delete(id);
    if (!res.headersSent) res.writeHead(502, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: { message: 'Diagnostic proxy upstream transport failed', type: error.code ?? error.name } }));
  }
  try {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    let payload = body.toString('utf8'); try { payload = JSON.parse(payload); } catch { /* GET has no body. */ }
    fs.writeFileSync(path.join(config.output, `${id}.request.json`), JSON.stringify(scrub(payload)) + '\n');
    event('request_body', { id, kind, bytes: body.length, sha256: sha(body),
      model: payload?.model, rpc_method: payload?.method, rpc_id: payload?.id,
      tool: payload?.params?.name, tool_names: payload?.tools?.map(t => t.function?.name) });
    const target = new URL(req.url, origin);
    const headers = Object.fromEntries(Object.entries(req.headers).filter(([key]) => !hopHeaders.has(key)));
    const transport = target.protocol === 'https:' ? https : http;
    row.phase = 'upstream_headers'; row.last_ms = performance.now();
    outgoing = transport.request(target, { method: req.method, headers,
      agent: target.protocol === 'https:' ? httpsAgent : httpAgent, timeout: 0 }, upstream => {
      response = upstream;
      row.phase = 'upstream_body'; row.last_ms = performance.now();
      event('response_headers', { id, kind, status: upstream.statusCode,
        elapsed_ms: performance.now() - start, headers: headersForLog(upstream.headers) });
      const forwarded = Object.fromEntries(Object.entries(upstream.headers).filter(([key]) => !hopHeaders.has(key)));
      res.writeHead(upstream.statusCode, forwarded); res.flushHeaders();
      obs = observer(id, kind, upstream.headers['content-type'] ?? '', upstream.headers['content-encoding']);
      upstream.on('data', chunk => {
        if (!row.bytes) event('first_byte', { id, kind, elapsed_ms: performance.now() - start });
        row.bytes += chunk.length; row.last_ms = performance.now();
        event('response_chunk', { id, bytes: chunk.length, elapsed_ms: performance.now() - start });
        obs.write(chunk);
        if (!res.write(chunk)) { upstream.pause(); res.once('drain', () => upstream.resume()); }
      });
      upstream.on('end', () => {
        if (finished) return;
        finished = true; obs.end();
        event('response_end', { id, kind, bytes: row.bytes, elapsed_ms: performance.now() - start });
        active.delete(id); res.end();
      });
      upstream.on('error', error => fail(error, 'upstream_body'));
      upstream.on('aborted', () => fail(new Error('Upstream response aborted'), 'upstream_body'));
    });
    outgoing.on('socket', socket => {
      event('socket_assigned', { id, kind });
      for (const name of ['lookup', 'connect', 'secureConnect']) socket.once(name, () =>
        event(`socket_${name}`, { id, kind, elapsed_ms: performance.now() - start }));
    });
    outgoing.on('error', error => fail(error, 'upstream_connect_or_headers'));
    outgoing.end(body);
    res.on('close', () => {
      if (finished) return;
      event('client_closed', { id, kind, phase: row.phase, elapsed_ms: performance.now() - start });
      finished = true; obs?.end(); active.delete(id); outgoing.destroy(); response?.destroy();
    });
  } catch (error) { fail(error, row.phase); }
}
for (const [kind, port, origin] of [['model', config.model_port, config.model_origin],
  ['rpc', config.rpc_port, config.rpc_origin]]) {
  const server = http.createServer((req, res) => { void handle(kind, origin, req, res); });
  server.timeout = 0; server.requestTimeout = 0;
  await new Promise((resolve, reject) => server.listen(port, '127.0.0.1', resolve).once('error', reject));
  servers.push(server);
}
event('proxy_ready', { model_port: config.model_port, rpc_port: config.rpc_port,
  policy: 'No retries or upstream deadline; preserve HTTP status, bodies and SSE; log only allowlisted headers.' });
setInterval(() => {
  event('heartbeat', { active: [...active.values()].map(row => ({ ...row,
    elapsed_ms: performance.now() - row.start_ms, idle_ms: performance.now() - row.last_ms })) });
  if ([...active.values()].some(row => performance.now() - row.last_ms >= 60000)) snapshot('idle_at_least_60s');
}, 15000).unref();
process.on('SIGTERM', () => {
  snapshot('proxy_sigterm'); copyNativeLogs();
  event('proxy_stop', { aborted_requests: [...active.keys()] });
  for (const server of servers) server.close();
  process.exit(0);
});
