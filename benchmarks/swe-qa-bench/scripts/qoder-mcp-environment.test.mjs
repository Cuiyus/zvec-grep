import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';

// Exercise released Qoder code without launching Qoder, a model, or a real MCP
// transport. The pinned bundle is supplied explicitly; drift must fail loudly.
const bundlePath = process.env.QODER_MCP_TEST_BUNDLE;
assert.ok(bundlePath, 'Set QODER_MCP_TEST_BUNDLE to @qoder-ai/qodercli@1.1.45 cli.js');
const bundle = readFileSync(bundlePath, 'utf8');
function section(start, end) {
  const first = bundle.indexOf(start);
  const last = bundle.indexOf(end, first + start.length);
  assert.ok(first >= 0 && last > first, `Pinned Qoder implementation changed: ${start}`);
  return bundle.slice(first, last);
}
const implementation = [
  section('function mR(A,e)', 'var IZa'),
  section('var IZa,D$i,S$i,BZa,biA=', 'import xZa'),
  section('function PM(A,e)', 'var sml'),
  'var sml=/\\$\\{([^}]+)\\}/g;',
  section('async function art(A,e,t,i)', 'function Vyn(A)'),
  'art',
].join('\n');

function transportFactory(environment) {
  return runInNewContext(implementation, {
    process: { env: environment },
    D: initialize => initialize(), Kn() {}, cT: 'TEST_ALLOWED_ONE', wO: 'TEST_ALLOWED_TWO',
    Vyn: () => ({}), QZ: () => null, Qae: 'TEST_CLIENT_NAME', Cae: 'qoder',
    jhA: class CapturedStdioTransport { constructor(options) { this.options = options; } },
  });
}

const names = ['QWEN_API_KEY', 'ZVEC_GREP_ENDPOINT', 'ZG_QA_ALLOW_REMOTE_EMBEDDING'];
const config = { command: 'node', args: ['/bridge.mjs'], env: Object.fromEntries(names.map(name => [name, '${' + name + '}'])) };
const context = { isTrustedFolder: () => true, sanitizationConfig: { enableEnvironmentVariableRedaction: true } };

for (const github of [false, true]) {
  test(`pinned Qoder passes explicit references after sanitization (GitHub=${github})`, async () => {
    const environment = {
      PATH: '/usr/bin', QWEN_API_KEY: 'offline-fixture-credential',
      ZVEC_GREP_ENDPOINT: 'https://example.invalid/v1/embeddings', ZG_QA_ALLOW_REMOTE_EMBEDDING: '1',
      UNRELATED_API_KEY: 'unrelated-fixture-credential', ...(github ? { GITHUB_SHA: 'fixture-sha' } : {}),
    };
    const createTransport = transportFactory(environment);
    const inherited = await createTransport('zvec_grep', { command: config.command, args: config.args }, false, context);
    assert.equal(inherited.options.env.QWEN_API_KEY, undefined, 'Reproduces the missing-key failure without explicit references');
    const fixed = await createTransport('zvec_grep', config, false, context);
    for (const name of names) assert.equal(fixed.options.env[name], environment[name]);
    assert.equal(fixed.options.env.UNRELATED_API_KEY, undefined, 'Unrelated credential redaction remains active');
    assert.equal(JSON.stringify(config).includes(environment.QWEN_API_KEY), false);
    assert.equal(JSON.stringify(fixed.options.args).includes(environment.QWEN_API_KEY), false);
  });
}
