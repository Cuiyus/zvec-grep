import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { auditVisibleSource } from "../semble-run.mjs";
import {
  createSembleResponseParser,
  scoreSembleResponse,
} from "../semble-scoring.mjs";

const query = "Where is wanted defined?";
const parse = createSembleResponseParser({ expectedQuery: query });
const chunk = (content, start = 1, end = start, path = "pkg/main.py") => ({
  file_path: path,
  start_line: start,
  end_line: end,
  content,
});
const response = (native, visibleContent = native.content) => ({
  content: [
    {
      type: "text",
      text: JSON.stringify({
        query,
        results: [{ ...native, content: visibleContent, score: 0.9 }],
      }),
    },
  ],
});
const items = (native, visibleContent) =>
  parse(response(native, visibleContent)).items;

async function corpus(t, source) {
  const directory = await mkdtemp(join(tmpdir(), "semble-visible-source-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const root = join(directory, "repo");
  await mkdir(join(root, "pkg"), { recursive: true });
  await writeFile(join(root, "pkg/main.py"), source);
  return { root, directory };
}

test("Semble source audit maps an exact contiguous snippet to CRLF source", async (t) => {
  const { root } = await corpus(
    t,
    "# header\r\n    def wanted():\r\n        return state\r\n",
  );
  const native = chunk("    def wanted():\n        return state", 2, 3);
  assert.deepEqual(await auditVisibleSource(items(native), root, [native]), []);
});

test("Semble source audit accepts AST start indentation omission and partial final line", async (t) => {
  const { root } = await corpus(
    t,
    "class Parent:\n    def wanted():\n        return state\n",
  );
  const native = chunk("def wanted():\n        return", 2, 3);
  assert.deepEqual(await auditVisibleSource(items(native), root, [native]), []);
});

test("Semble source audit accepts a one-line AST node wholly inside a source line", async (t) => {
  const { root } = await corpus(t, "if flag: invoke(x); finish()\n");
  const native = chunk("invoke(x)");
  assert.deepEqual(await auditVisibleSource(items(native), root, [native]), []);
});

test("Semble source audit requires a persisted carrier with the same path, range and snippet", async (t) => {
  const { root } = await corpus(t, "def wanted():\n    return state\n");
  const native = chunk("def wanted():\n    return state", 1, 2);
  for (const carriers of [
    [],
    [{ ...native, file_path: "pkg/other.py" }],
    [{ ...native, start_line: 2 }],
    [{ ...native, end_line: 3 }],
    [{ ...native, content: "def other():\n    return state" }],
  ]) {
    const errors = await auditVisibleSource(items(native), root, carriers);
    assert.ok(errors.some((error) => error.includes("persisted native chunk")));
  }
});

test("Semble source audit rejects a persisted snippet at the wrong source line", async (t) => {
  const { root } = await corpus(t, "# unrelated\ndef wanted():\n");
  const native = chunk("def wanted():", 1, 1);
  const errors = await auditVisibleSource(items(native), root, [native]);
  assert.deepEqual(errors, [
    "rank 1 pkg/main.py:1: visible line does not map to locked source",
  ]);
});

test("Semble source audit never grants partial-line exceptions to an interior line", async (t) => {
  const { root } = await corpus(
    t,
    "def wanted():\n    state = lookup()\n    return state\n",
  );
  for (const middle of ["state = lookup()", "    state", "lookup()"]) {
    const native = chunk(`def wanted():\n${middle}\n    return state`, 1, 3);
    const errors = await auditVisibleSource(items(native), root, [native]);
    assert.ok(
      errors.some((error) => error.includes("pkg/main.py:2: visible line")),
    );
  }
});

test("Semble source audit rejects a corpus-escaping symlink even with matching carrier content", async (t) => {
  const { root, directory } = await corpus(t, "def wanted():\n");
  const outside = join(directory, "outside.py");
  await writeFile(outside, "def wanted():\n");
  await symlink(outside, join(root, "escaped.py"));
  const native = chunk("def wanted():", 1, 1, "escaped.py");
  await assert.rejects(
    auditVisibleSource(items(native), root, [native]),
    /visible source escapes corpus/,
  );
});

test("Semble source audit catches Python splitlines separators that shift advertised source lines", async (t) => {
  const physical = [
    "first\fsecond",
    ...Array.from({ length: 11 }, (_, index) => `physical_${index + 2}`),
  ];
  const { root } = await corpus(t, `${physical.join("\n")}\n`);
  const native = chunk(physical.join("\n"), 1, physical.length);
  const visible = ["first", "second", ...physical.slice(1, 9)].join("\n");
  const errors = await auditVisibleSource(items(native, visible), root, [
    native,
  ]);
  assert.ok(
    errors.some((error) => error.includes("does not map to locked source")),
  );
  assert.ok(!errors.some((error) => error.includes("persisted native chunk")));
});

test("Semble source audit validates only the visible prefix and never exposes hidden chunk content to scoring", async (t) => {
  const prefix = Array.from(
    { length: 10 },
    (_, index) => `# context ${index + 1}`,
  );
  const hidden = "def wanted():";
  const source = `${prefix.join("\n")}\n${hidden}`;
  const { root } = await corpus(t, `${source}\n`);
  const native = chunk(source, 1, 11);
  const raw = response(native, prefix.join("\n"));
  const labels = {
    schema_version: 1,
    status: "reviewed",
    targets: [
      {
        id: "wanted",
        path: "pkg/main.py",
        kind: "symbol",
        symbol: "wanted",
        source_sha256: "a".repeat(64),
        role: "accepted",
        anchors: [
          {
            start_line: 11,
            end_line: 11,
            text: hidden,
            sha256: createHash("sha256").update(hidden).digest("hex"),
          },
        ],
      },
    ],
    ndcg: { enabled: false, groups: [] },
  };
  const scored = scoreSembleResponse(raw, labels, { expectedQuery: query });
  const before = structuredClone(scored.items);
  assert.deepEqual(await auditVisibleSource(scored.items, root, [native]), []);
  assert.deepEqual(scored.items, before);
  assert.equal(scored.status, "scored");
  assert.equal(scored.hit_at_10, 0);
  assert.equal(scored.target_matches.length, 0);
  assert.equal(scored.items[0].source_lines.at(-1).line, 10);
});
