import assert from "node:assert/strict";
import test from "node:test";
import { loadPilot } from "../expansion/datasets.mjs";
import { summarizePilotRows, markdownPilotReport } from "../expansion/run.mjs";
import { buildCombined, markdownCombined } from "../expansion/combined.mjs";
import { parseVisibleResponse } from "../engines/zg/parse.mjs";
import { scoreFileRetrieval } from "../metrics/files.mjs";
import { scoreNdcg } from "../metrics/ndcg.mjs";

test("BEIR and Quarry locks contain ten distinct original-query identities and the requested models", async () => {
  const beir = await loadPilot("beir");
  const quarry = await loadPilot("quarry");
  assert.equal(beir.lock.tasks.length, 10);
  assert.equal(quarry.lock.tasks.length, 10);
  assert.equal(beir.lock.model, "local/potion-multilingual-128m");
  assert.equal(quarry.lock.model, "local/potion-code-16m-v2");
  assert.equal(
    new Set(quarry.lock.tasks.map((task) => task.source_task_id)).size,
    10,
  );
  assert.equal(
    new Set(quarry.lock.tasks.map((task) => task.revision)).size,
    10,
  );
  assert.ok(
    quarry.lock.tasks.every((task) =>
      task.positive_units.every((unit) => unit.revision === task.revision),
    ),
  );
  assert.ok(
    beir.lock.tasks.every((task) =>
      task.qrels.every((row) => row.relevance > 0),
    ),
  );
});

test("pilot report preserves completed scores and calls out missing tasks", () => {
  const success = {
    task_id: "a",
    mode: "hybrid",
    status: "success",
    file: { hit_at_1: 1, hit_at_5: 1, hit_at_10: 1, rr_at_10: 1 },
    ndcg: { ndcg_at_10: 1 },
    output_bytes: 1024,
    calls: [100, 110, 120, 130, 140].map((latency_ms) => ({
      status: "success",
      latency_ms,
    })),
  };
  const failed = {
    task_id: "b",
    mode: "hybrid",
    status: "failed",
    calls: [{ status: "failed", latency_ms: 1 }],
  };
  const summary = summarizePilotRows([success, failed]);
  assert.equal(summary[0].completed, 1);
  assert.equal(summary[0].metrics.file_hit_at_1, 1);
  assert.equal(summary[0].measurements.latency_sample_count, 5);
  assert.equal(summary[0].measurements.latency_ms_p50, 120);
  assert.equal(summary[1].metrics.ndcg_at_10, null);
  const markdown = markdownPilotReport({
    label: "Pilot",
    status: "failed",
    model: "local/test",
    suite: "quarry10",
    summary,
    rows: [success, failed],
    failures: [{ task_id: "b", reason: "index failed" }],
  });
  assert.match(markdown, /1\/10/);
  assert.match(markdown, /index failed/);
  assert.match(markdown, /Per-query results/);
  assert.match(markdown, /\| a \| zg-hybrid \| ✅ Scored/);
  assert.match(markdown, /not the official Quarry function recall/);
});

test("unified results page identifies missing pilot artifacts independently", async () => {
  const result = await buildCombined({
    zg: null,
    beir: null,
    quarry: null,
    candidateCommit: "a".repeat(40),
  });
  assert.equal(result.status, "failed");
  assert.equal(result.pilots.beir.status, "unavailable");
  assert.equal(result.pilots.quarry.status, "unavailable");
  const markdown = markdownCombined(result);
  assert.match(markdown, /SWE-QA20/);
  assert.match(markdown, /BEIR \/ SciFact/);
  assert.match(markdown, /Quarry \/ quic-go/);
  assert.match(markdown, /report artifact missing/);
});

test("a failed pilot query keeps partial aggregate and all per-query conclusions", async () => {
  const pilot = await loadPilot("beir");
  const rows = pilot.lock.tasks.flatMap((task) => {
    const targets = task.qrels.map((item) => ({
      path: `docs/${item.document_id}.md`,
    }));
    return pilot.modes.map((mode) => ({
      task_id: task.id,
      mode,
      query: task.query,
      targets,
      calls: [],
      status: "failed",
      reason: "index failed",
    }));
  });
  const scored = rows[0];
  scored.status = "success";
  delete scored.reason;
  scored.calls = [1, 2, 3, 4, 5].map((repetition) => ({
    repetition,
    status: "success",
    latency_ms: 10,
  }));
  scored.items = [];
  scored.file = scoreFileRetrieval([], scored.targets);
  scored.ndcg = scoreNdcg([], scored.targets);
  scored.output_bytes = 0;
  const report = {
    schema_version: 1,
    suite: pilot.lock.suite,
    label: "BEIR / SciFact (test)",
    model: pilot.lock.model,
    candidate_commit: "a".repeat(40),
    status: "failed",
    rows,
    failures: [{ task_id: rows[1].task_id, reason: "index failed" }],
    summary: summarizePilotRows(rows),
  };
  const result = await buildCombined({
    zg: null,
    beir: report,
    quarry: null,
    candidateCommit: "a".repeat(40),
  });
  assert.equal(result.pilots.beir.status, "failed");
  assert.equal(result.pilots.beir.report.summary[0].completed, 1);
  const markdown = markdownCombined(result);
  assert.match(markdown, /BEIR \/ SciFact \| ❌ Incomplete \| 1\/10/);
  assert.match(markdown, /Per-query results \(all 10 queries × 3 modes\)/);
  assert.match(markdown, /index failed/);
});

test("pilot parser accepts only one trailing empty Markdown line outside the public range", () => {
  const response = (last) => ({
    content: [
      {
        type: "text",
        text: `freshness: fresh\n#1 matchedBy=fts docs/1.md:1-3\nsource:\n1\tTitle\n2\t\n3\tBody\n4\t${last}\n`,
      },
    ],
  });
  assert.throws(
    () => parseVisibleResponse(response("")),
    /outside the item's public range/,
  );
  const parsed = parseVisibleResponse(response(""), {
    allowTrailingBlankOutsideRange: true,
  });
  assert.equal(parsed.items[0].path, "docs/1.md");
  assert.throws(
    () =>
      parseVisibleResponse(response("unexpected"), {
        allowTrailingBlankOutsideRange: true,
      }),
    /outside the item's public range/,
  );
});
