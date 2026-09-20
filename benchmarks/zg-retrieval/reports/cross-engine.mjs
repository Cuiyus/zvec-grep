import assert from "node:assert/strict";
import { loadSuite } from "../core/lib.mjs";
import {
  FILE_RETRIEVAL_CONTRACT,
  summarizeFileRetrieval,
} from "../metrics/files.mjs";
import { summarizeMeasurements } from "../metrics/measurements.mjs";
import { summarizeSembleOfficial } from "../metrics/summary.mjs";
import { SEMBLE_PROTOCOL } from "../engines/semble/protocol.mjs";
import {
  selectPreviewReport,
  validateZgReport,
  validateSembleReport,
  validateFrozenQualityRows,
} from "./validation.mjs";

/** Compare common Gold quality only. Engine protocol identities deliberately remain different. */
export async function compareSembleToZg(
  baseline,
  candidate,
  suite = undefined,
) {
  suite ??= await loadSuite();
  validateZgReport(baseline, "zg baseline", { suite });
  const after = await validateSembleReport(candidate, suite);
  if (baseline.previews) {
    const zgPreviews = {};
    for (const preview of ["short", "full"])
      zgPreviews[preview] = compareSelectedZgToSemble(
        selectPreviewReport(baseline, preview),
        candidate,
        suite,
        after,
      );
    return {
      ...zgPreviews.short,
      primary_preview: "short",
      zg_previews: zgPreviews,
    };
  }
  return compareSelectedZgToSemble(baseline, candidate, suite, after);
}

function compareSelectedZgToSemble(baseline, candidate, suite, after) {
  assert.equal(
    baseline.suite.protocol,
    suite.identity.protocol,
    "baseline: zg protocol mismatch",
  );
  assert.notEqual(
    candidate.suite.protocol,
    baseline.suite.protocol,
    "cross-tool protocols must retain their distinct identities",
  );
  const before = validateFrozenQualityRows(baseline, suite, "zg baseline");
  const tasks = suite.lock.tasks.map((task) => {
    const oldRow = before.find((row) => row.task_id === task.task_id),
      newRow = after.find((row) => row.task_id === task.task_id);
    const view = (row) => ({
      task_id: row.task_id,
      execution_status: row.execution_status,
      status: row.status,
      gold_status: row.gold_status,
      repository: row.repository,
      language: row.language,
      items: row.items,
      file_retrieval: row.file_retrieval,
      semble_official: row.semble_official,
    });
    return {
      task_id: task.task_id,
      category: task.category,
      repository: task.repository,
      zg: view(oldRow),
      semble: view(newRow),
      file_retrieval_delta: Object.fromEntries(
        ["hit_at_1", "hit_at_5", "hit_at_10", "rr_at_10"].map((key) => [
          key,
          newRow.file_retrieval[key] - oldRow.file_retrieval[key],
        ]),
      ),
      semble_official_delta: {
        ndcg_at_10:
          newRow.semble_official.ndcg_at_10 - oldRow.semble_official.ndcg_at_10,
      },
    };
  });
  const fileZg = summarizeFileRetrieval(before),
    fileSemble = summarizeFileRetrieval(after);
  return {
    schema_version: 2,
    file_retrieval_contract: FILE_RETRIEVAL_CONTRACT,
    kind: "cross-tool-quality-observation",
    zg_preview: baseline.preview ?? "short",
    delta_direction: "Semble minus zg",
    source: suite.identity.source,
    gold: suite.identity.gold,
    semble_gold: suite.identity.semble_gold,
    protocols: {
      zg: baseline.suite.protocol,
      semble: candidate.suite.protocol,
    },
    quality_gate:
      "report-only; no causal attribution to a single retrieval component",
    file_retrieval: {
      zg: fileZg,
      semble: fileSemble,
      delta: Object.fromEntries(
        ["hit_at_1", "hit_at_5", "hit_at_10", "mrr_at_10"].map((key) => [
          key,
          fileZg[key] === null ? null : fileSemble[key] - fileZg[key],
        ]),
      ),
    },
    measurements: {
      zg: summarizeMeasurements(
        before.flatMap((row) => row.measurement_observations),
      ),
      semble: summarizeMeasurements(
        after.flatMap((row) => row.measurement_observations),
      ),
    },
    semble_official: {
      zg: summarizeSembleOfficial(tasks.map((row) => row.zg)),
      semble: summarizeSembleOfficial(tasks.map((row) => row.semble)),
    },
    tasks,
    differences: {
      endpoint: {
        zg: "zg install generated public stdio MCP / zvec_grep_search",
        semble: "Semble native stdio MCP / search",
      },
      model: {
        zg: suite.protocol.model,
        semble: SEMBLE_PROTOCOL.model,
        qualification:
          "Same named model family does not establish identical artifacts, tokenization, embeddings, or runtime. Recorded model inventories remain separate.",
      },
      content: {
        zg:
          baseline.preview === "full"
            ? "Native complete available retrieved source content plus available outline; no whole-file expansion"
            : "Native bounded outline plus short source excerpt",
        semble:
          "Native complete chunk text, no outline; fifth-call results verified against the official SDK",
      },
      filtering: {
        zg: "zg native scan policy",
        semble:
          "content=code; Semble CODE extensions, native scanning and exclusions; zg uses the same fixed extension allowlist but native scanner exclusions may still differ",
      },
      retrieval: {
        zg: "Native zg hybrid",
        semble:
          "Native Semble BM25/vector hybrid and default reranking; no query rewrite or subquery",
      },
      freshness: {
        zg: "autoUpdate=false, freshness=eventual",
        semble:
          "No public auto-update disable switch; frozen corpus/index verified before and after",
      },
      environment: {
        zg: baseline.repositories.map((run) => ({
          repository: run.repository,
          environment: run.environment,
        })),
        semble: candidate.environment,
      },
      latency:
        "No cross-environment timing ratio or speed winner is computed. Index/MCP timings are observations only; first query includes engine-specific load costs.",
    },
    warnings: [
      "Same original queries, repository commits, frozen accepted-file targets and five quality metrics; endpoint, representation, filtering, model runtime and environment are not controlled identically.",
      "nDCG@10 uses the SWE-QA accepted-file projection, not Semble's original benchmark annotations. Both engines use the same first-target-rank algorithm and aggregation. Finding a target file does not establish sufficient answer evidence.",
      ...[baseline, candidate].flatMap((report, i) =>
        report.product_error_calls
          ? [
              `${i ? "Semble" : "zg"}: product-error zeros remain in the denominator; delta may include delivery failures.`,
            ]
          : [],
      ),
    ],
  };
}
