// Convert production renderer captures into review sheets without changing their text.
// node rust/scripts/render-cli-style.mjs fixtures.json output-dir [pty-captures.json]
// Screenshot the resulting HTML with a browser at viewport width 1120.
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const [input, destination, interactions] = process.argv.slice(2);
if (!input || !destination) throw new Error('Expected captures.json and output directory');
const fixtures = JSON.parse(await readFile(input, 'utf8'));
if (interactions) fixtures.push(...JSON.parse(await readFile(interactions, 'utf8')));
await mkdir(destination, { recursive: true });
const escape = (text) => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
function ansiHtml(text) {
  let color = '', dim = false, bold = false, result = '', start = 0;
  const append = (part) => {
    if (part) result += `<span style="${color ? `color:${color};` : ''}${dim ? 'opacity:.55;' : ''}${bold ? 'font-weight:700;' : ''}">${escape(part)}</span>`;
  };
  // Captures already select a single screen/frame; ignore remaining cursor commands.
  const content = text.replace(/\x1b\[(?![\d;]*m)[\d;?]*[A-Za-z]/g, '').replaceAll('\r', '').trimEnd();
  for (const match of content.matchAll(/\x1b\[([\d;]*)m/g)) {
    append(content.slice(start, match.index));
    const codes = (match[1] || '0').split(';').map(Number);
    for (let i = 0; i < codes.length; i++) {
      const code = codes[i];
      if (code === 0) { color = ''; dim = false; bold = false; }
      else if (code === 1) bold = true;
      else if (code === 2) dim = true;
      else if (code === 22) { dim = false; bold = false; }
      else if (code === 39) color = '';
      else if (code === 38 && codes[i + 1] === 2) { color = `rgb(${codes.slice(i + 2, i + 5).join(',')})`; i += 4; }
      else if (code === 31) color = '#ed8e87';
      else if (code === 32) color = '#6fcf91';
      else if (code === 33) color = '#d9bb75';
    }
    start = match.index + match[0].length;
  }
  append(content.slice(start));
  return result;
}
const groups = [
  ['index-lifecycle', 'Index lifecycle', ['indexing', 'optimizing', 'saving', 'finishing', 'ready', 'error']],
  ['command-output', 'Consistent command output', ['status', 'config', 'auth', 'refresh', 'partial', 'drop']],
  ['interaction', 'Keyboard interactions', ['model-picker', 'install-picker', 'consent']],
];
for (const [name, title, ids] of groups) {
  const cards = ids.map((id) => fixtures.find((fixture) => fixture.id === id)).filter(Boolean);
  const html = `<!doctype html><html lang="en"><meta charset="utf-8"><title>${title}</title><style>
  *{box-sizing:border-box}body{margin:0;padding:36px;background:#0b100e;color:#dce4df;font-family:system-ui,sans-serif;width:1120px}
  header{margin:0 0 24px}h1{font-size:25px;letter-spacing:-.4px;font-weight:600;margin:0 0 8px}header p{font-size:13px;color:#8e9d94;margin:0}
  section{background:#111916;border:1px solid #28342d;border-radius:12px;margin:14px 0 0;padding:19px 24px}
  .label{font-size:11px;letter-spacing:.8px;color:#a5b3aa;margin-bottom:13px;text-transform:uppercase}
  .command{font:14px/1.5 Menlo,Consolas,monospace;color:#85978b;margin-bottom:14px}
  pre{font:18px/1.55 Menlo,Consolas,monospace;font-variant-ligatures:none;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}
  footer{font-size:12px;color:#75867b;margin-top:22px}
  </style><header><h1>zg · ${title}</h1><p>Production Rust renderers · green blocks · neutral text · dim secondary information</p></header>
  ${cards.map((card) => `<section><div class="label">${escape(card.title)}</div><div class="command">$ ${escape(card.command)}</div><pre>${ansiHtml(card.ansi)}</pre></section>`).join('')}
  <footer>Fixture cards use sample data through the actual renderers. PTY cards capture the running CLI. No business action is performed by fixture generation.</footer></html>`;
  await writeFile(join(destination, `${name}.html`), html);
}
