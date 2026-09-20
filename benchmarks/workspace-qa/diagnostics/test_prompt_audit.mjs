import test from 'node:test';
import assert from 'node:assert/strict';
import { summarize } from './prompt-audit.mjs';
const guidance = '<!-- marker -->\nChoose evidence.\nFor a mixed task with exact anchors use search.\n<!-- end -->\n';
const body = 'Choose evidence.\nFor a mixed task with exact anchors use search.';
test('recognizes actual startup user-prefix injection and removes only comments', () => {
  const r = summarize('transport_request', { messages: [{ role: 'system', content: 'base' },
    { role: 'user', content: `<system-reminder>${body}</system-reminder>` }] }, guidance);
  assert.equal(r.full_guidance_present, true);
  assert.equal(r.fragments[1].role, 'user');
  assert.equal(r.fragments[1].matched_guidance_lines, 2);
});
test('tool schema presence and a partial instruction are not full guidance injection', () => {
  const r = summarize('transport_request', { tools: [{ name: 'mcp__zvec_grep__zvec_grep_search', description: body }],
    messages: [{ role: 'user', content: 'Choose evidence.' }] }, guidance);
  assert.equal(r.zg_tool_schema_present, true);
  assert.equal(r.full_guidance_present, false);
});
test('never emits prompt text, credential-like fields, or hidden reasoning', () => {
  const secret = 'SECRET_SENTINEL_not_for_artifacts';
  const r = summarize('transport_request', { api_key: secret, messages: [
    { role: 'user', content: body + secret },
    { role: 'assistant', content: secret, reasoning_content: secret },
  ] }, guidance);
  assert.equal(JSON.stringify(r).includes(secret), false);
  assert.equal(JSON.stringify(r).includes('Choose evidence.'), false);
  assert.equal(r.fragments.length, 1);
});
test('detects missing loaded memory and rejects an empty reference', () => {
  assert.equal(summarize('memory_context', {}, guidance).full_guidance_present, false);
  assert.throws(() => summarize('memory_context', {}, '<!-- none -->'));
});
