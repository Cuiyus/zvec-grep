// Diagnostic-only observer: never stores prompts, responses, reasoning or credentials.
import { appendFileSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
const sha = text => createHash('sha256').update(text).digest('hex');
const searchTool = 'mcp__zvec_grep__zvec_grep_search';
export function summarize(stage, value, guidance) {
  const body = guidance.replace(/<!--[\s\S]*?-->/g, '').trim();
  if (!body) throw new Error('Empty reference guidance');
  const lines = body.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  const fragments = stage === 'memory_context'
    ? [{ role: 'agentsMd', text: value.agentsMd ?? '' }]
    : (value.messages ?? []).flatMap(m => {
      // Do not inspect reasoning fields or assistant text; memory is a startup user prefix.
      if (!['system', 'user'].includes(m.role)) return [];
      const parts = typeof m.content === 'string' ? [m.content]
        : (Array.isArray(m.content) ? m.content.filter(b => b.type === 'text').map(b => b.text) : []);
      return parts.filter(t => typeof t === 'string').map(text => ({ role: m.role, text }));
    });
  const row = {
    stage, guidance_sha256: sha(guidance), guidance_body_sha256: sha(body),
    guidance_nonempty_lines: lines.length,
    full_guidance_present: fragments.some(f => f.text.includes(body)),
    fragments: fragments.map((f, index) => ({ index, role: f.role, chars: f.text.length,
      matched_guidance_lines: lines.filter(line => f.text.includes(line)).length,
      full_guidance_present: f.text.includes(body),
      mixed_routing_present: f.text.includes('For a mixed task with exact anchors'),
      cross_document_routing_present: f.text.includes('comparison or synthesis across files, sections, or documents'),
    })),
  };
  if (stage === 'transport_request') {
    const names = (value.tools ?? []).map(t => t.name ?? t.function?.name).filter(Boolean);
    Object.assign(row, { request_id: value.request_id, model: value.model_config?.display_name,
      message_count: value.messages?.length, zg_tool_schema_present: names.includes(searchTool),
      tool_names: names, max_tokens: value.parameters?.max_tokens });
  }
  return row;
}
export function observe(stage, value) {
  try {
    const guidance = readFileSync(`${process.env.HOME}/.qoder/AGENTS.md`, 'utf8');
    appendFileSync('/logs/prompt-audit.jsonl', JSON.stringify({
      timestamp: new Date().toISOString(), ...summarize(stage, value, guidance),
    }) + '\n');
  } catch {
    // Observability failures must not change native execution. A missing record fails the audit.
    try { appendFileSync('/logs/prompt-audit-errors.txt', 'observer_failed\n'); } catch {}
  }
}
