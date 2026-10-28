// Diagnostic transport only. Configuration is prepended by the Python runner.
const http = require('http');
const fs = require('fs');
const crypto = require('crypto');
const assert = require('assert/strict');
const stable = value => JSON.stringify(sortKeys(value));
function sortKeys(value) {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().map(key => [key, sortKeys(value[key])]));
  return value;
}
const digest = value => crypto.createHash('sha256').update(stable(value)).digest('hex');
const save = (name, value) => fs.appendFileSync(config.logs + '/' + name + '.jsonl', JSON.stringify(value) + '\n');
let sequence = 0;
let attempts = 0;

function transform(body) {
  const changed = structuredClone(body);
  const search = changed.tools.filter(t => t.function?.name === config.expected.name);
  assert.equal(search.length, 1);
  assert.deepEqual(search[0].function, config.expected);
  if (config.arm === 'inline') {
    const schema = search[0].function.parameters;
    const definition = schema.$defs.QueryListInput;
    for (const field of ['queries', 'fts', 'vector']) {
      assert.deepEqual(schema.properties[field].anyOf[0], {$ref: '#/$defs/QueryListInput'});
      schema.properties[field].anyOf[0] = structuredClone(definition);
    }
    delete schema.$defs.QueryListInput;
    assert.deepEqual(search[0].function, config.proposal);
    const restored = structuredClone(changed);
    restored.tools.find(t => t.function?.name === config.expected.name).function = structuredClone(config.expected);
    assert.deepEqual(restored, body);
  } else {
    assert.equal(config.arm, 'original');
    assert.deepEqual(changed, body);
  }
  return changed;
}

function stopLocally(response, body, id, reason) {
  save('local-responses', {id, reason, synthetic: true});
  const message = {role: 'assistant', content: 'Schema diagnostic complete.'};
  const usage = {prompt_tokens: 0, completion_tokens: 0, total_tokens: 0};
  const base = {id: 'local-schema-' + id, created: 1, model: body.model};
  if (!body.stream) {
    response.writeHead(200, {'content-type': 'application/json'});
    response.end(JSON.stringify({...base, object: 'chat.completion', choices: [{index: 0, message, finish_reason: 'stop'}], usage}));
    return;
  }
  response.writeHead(200, {'content-type': 'text/event-stream'});
  const chunks = [
    {...base, object: 'chat.completion.chunk', choices: [{index: 0, delta: message, finish_reason: null}]},
    {...base, object: 'chat.completion.chunk', choices: [{index: 0, delta: {}, finish_reason: 'stop'}], usage},
  ];
  response.end(chunks.map(c => 'data: ' + JSON.stringify(c) + '\n\n').join('') + 'data: [DONE]\n\n');
}

const server = http.createServer(async (request, response) => {
  const id = ++sequence;
  let forwarded = false;
  try {
    let raw = '';
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    const hasSearch = (body.tools || []).some(t => t.function?.name === config.expected.name);
    if (!hasSearch || attempts !== 0) {
      stopLocally(response, body, id, !hasSearch ? 'auxiliary_request' : 'generation_budget_spent');
      return;
    }
    assert.equal(body.model, 'qwen3.8-max');
    const outbound = transform(body);
    const normalizedBodyHash = digest(body);
    const localTextInMessages = JSON.stringify(body.messages || []).includes('Schema diagnostic complete.');
    assert.equal(localTextInMessages, false);
    save('model-requests', {id, model: outbound.model, tools: outbound.tools,
      controls: Object.fromEntries(Object.entries(outbound).filter(([key]) =>
        ['temperature', 'reasoning_effort', 'max_tokens', 'max_completion_tokens', 'stream', 'enable_thinking', 'seed'].includes(key))),
      normalized_body_sha256: normalizedBodyHash, outbound_body_sha256: digest(outbound),
      messages_sha256: digest(outbound.messages), local_text_in_messages: localTextInMessages,
      original_search_definition_sha256: digest(config.expected),
      schema_only_change_verified: true, arm: config.arm});
    // Spend the one-attempt budget synchronously BEFORE fetch; failed calls count.
    attempts += 1;
    forwarded = true;
    save('outbound-attempts', {id, attempt: attempts, arm: config.arm, event: 'started'});
    const headers = {'content-type': 'application/json'};
    if (request.headers.authorization) headers.authorization = request.headers.authorization;
    const result = await fetch(config.upstream + request.url.replace(/^\/v1/, ''), {
      method: 'POST', headers, body: config.arm === 'original' ? raw : JSON.stringify(outbound),
      redirect: 'error', signal: AbortSignal.timeout(300000),
    });
    save('outbound-attempts', {id, event: 'headers', status: result.status});
    response.writeHead(result.status, {'content-type': result.headers.get('content-type') || 'text/event-stream'});
    const decoder = new TextDecoder();
    let pending = '';
    const recordLine = line => {
      if (!line.startsWith('data: ') || line === 'data: [DONE]') return;
      const data = JSON.parse(line.slice(6));
      for (const choice of data.choices || []) {
        const calls = choice.delta?.tool_calls || choice.message?.tool_calls;
        if (calls) save('raw-tool-call-fragments', {request: id, model: data.model, calls});
      }
      if (data.usage) save('provider-usage', {request: id, usage: data.usage, model: data.model});
    };
    for await (const chunk of result.body) {
      response.write(chunk);
      pending += decoder.decode(chunk, {stream: true});
      const lines = pending.split('\n');
      pending = lines.pop();
      for (const line of lines) recordLine(line);
    }
    pending += decoder.decode();
    if (pending.trim()) recordLine(pending.trim());
    save('outbound-attempts', {id, event: 'completed', status: result.status});
    response.end();
  } catch (error) {
    save('proxy-errors', {id, type: error.name, forwarded});
    if (forwarded) save('outbound-attempts', {id, event: 'failed', type: error.name});
    if (!response.headersSent) response.writeHead(502);
    response.end('Schema diagnostic request failed');
  }
});
server.listen(config.port, '127.0.0.1', () => fs.writeFileSync(config.logs + '/proxy.ready', String(server.address().port)));
