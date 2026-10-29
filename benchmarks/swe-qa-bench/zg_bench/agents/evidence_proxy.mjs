// Transparent, loopback-only recording for the case-analysis harness.
// Headers containing credentials are forwarded but never persisted.
import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const servers = [];
for (const route of config.routes) {
  const upstream = new URL(route.upstream);
  if (upstream.username || upstream.password) throw new Error('URL credentials prohibited');
  const destination = path.join(config.output, route.name);
  fs.mkdirSync(destination, { recursive: true });
  const server = http.createServer((request, response) => {
    const id = `${Date.now()}-${randomUUID()}`;
    const prefix = path.join(destination, id);
    const started = Date.now();
    const chunks = [];
    request.on('data', chunk => chunks.push(chunk));
    request.on('end', () => {
      const body = Buffer.concat(chunks);
      fs.writeFileSync(`${prefix}.request.bin`, body);
      const safeHeaders = Object.fromEntries(Object.entries(request.headers).filter(([key]) =>
        ['content-type', 'accept', 'mcp-session-id', 'mcp-protocol-version',
          'x-opencode-session-id', 'x-parent-session-id'].includes(key)));
      const target = new URL(upstream.href);
      target.pathname = upstream.pathname.replace(/\/$/, '') + request.url.split('?')[0];
      target.search = new URL(request.url, 'http://localhost').search;
      fs.writeFileSync(`${prefix}.metadata.json`, JSON.stringify({
        id, route: route.name, method: request.method, path: request.url,
        started_ms: started, request_headers: safeHeaders,
      }, null, 2));
      const headers = { ...request.headers, host: target.host };
      const transport = target.protocol === 'https:' ? https : http;
      const outgoing = transport.request(target, { method: request.method, headers }, incoming => {
        response.writeHead(incoming.statusCode, incoming.headers);
        const file = fs.createWriteStream(`${prefix}.response.bin`);
        incoming.pipe(file);
        incoming.pipe(response);
        let ended = false;
        incoming.on('end', () => { ended = true; });
        incoming.on('close', () => fs.writeFileSync(`${prefix}.completion.json`, JSON.stringify({
          status: incoming.statusCode, complete: ended, finished_ms: Date.now(),
          response_headers: Object.fromEntries(Object.entries(incoming.headers).filter(([key]) =>
            ['content-type', 'content-encoding', 'mcp-session-id', 'mcp-protocol-version'].includes(key))),
        }, null, 2)));
      });
      outgoing.on('error', error => {
        fs.writeFileSync(`${prefix}.completion.json`, JSON.stringify({
          complete: false, error_code: error.code || 'transport_error', finished_ms: Date.now(),
        }));
        if (!response.headersSent) response.writeHead(502);
        response.end();
      });
      response.on('close', () => { if (!response.writableEnded) outgoing.destroy(); });
      outgoing.end(body);
    });
  });
  await new Promise(resolve => server.listen(route.port, '127.0.0.1', resolve));
  servers.push(server);
}
fs.writeFileSync(config.ready, JSON.stringify({ pid: process.pid, routes: config.routes.map(x => x.name) }));
process.on('SIGTERM', () => {
  for (const server of servers) { server.close(); server.closeAllConnections(); }
});
