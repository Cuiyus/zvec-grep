import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { scoreSembleMetric } from "./semble-metrics.mjs";
import {
  summarizeSembleOfficial,
  markdownSembleOfficialTable,
} from "./report.mjs";

const MODES = ["hybrid", "fts", "vector"];
const HITS = ["hit_at_1", "hit_at_5", "hit_at_10"];
const METRICS = [...HITS, "rr_at_10", "ndcg_at_5", "ndcg_at_10"];
const CATEGORIES = ["what", "where", "how", "why"];
const hash = (value) => createHash("sha256").update(value).digest("hex");
const average = (values) =>
  values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
const difference = (before, after) => (before === null ? null : after - before);
const key = (row) => JSON.stringify([row.task_id, row.mode]);

function uniqueStrings(values, label) {
  assert.ok(
    Array.isArray(values) && values.length > 0,
    `${label}: missing values`,
  );
  assert.ok(
    values.every((value) => typeof value === "string" && value.length > 0),
    `${label}: invalid value`,
  );
  assert.equal(
    new Set(values).size,
    values.length,
    `${label}: duplicate value`,
  );
  return [...values].sort();
}

/** Select a presentation arm without treating repeated questions as new samples. */
export function selectPreviewReport(report, preview) {
  assert.ok(report.previews, "report has no preview arms");
  const names = uniqueStrings(Object.keys(report.previews), "preview arms");
  assert.deepEqual(names, ["full", "short"], "incomplete preview arm set");
  assert.equal(report.primary_preview, "short", "invalid primary preview");
  assert.deepEqual(
    uniqueStrings(Object.keys(report.previews.short.modes), "short modes"),
    uniqueStrings(Object.keys(report.previews.full.modes), "full modes"),
    "incompatible mode sets across preview arms",
  );
  assert.ok(names.includes(preview), "unknown preview arm");
  assert.ok(
    report.tasks.every((row) => names.includes(row.preview)),
    "unplanned or missing preview in quality rows",
  );
  // Cached arm tasks/summaries are not scoring inputs. The ordinary report
  // validator checks the selected public rows and recomputes their metrics.
  const selected = {
    ...report,
    preview,
    modes: report.previews[preview].modes,
    tasks: report.tasks.filter((row) => row.preview === preview),
  };
  delete selected.previews;
  return selected;
}

function validateReport(report, label) {
  assert.ok(
    [1, 2].includes(report.schema_version),
    `${label}: unsupported report schema`,
  );
  if (report.schema_version === 2)
    assert.ok(
      ["short", "full"].includes(report.preview),
      `${label}: missing selected preview`,
    );
  assert.equal(
    report.quality_repetition,
    5,
    `${label}: comparison requires quality repetition 5`,
  );
  assert.equal(
    report.quality_score_valid,
    true,
    `${label}: invalid quality report`,
  );
  assert.ok(
    Array.isArray(report.integrity_errors) &&
      report.integrity_errors.length === 0,
    `${label}: experimental integrity errors`,
  );
  assert.equal(
    typeof report.integrity_passed,
    "boolean",
    `${label}: missing integrity state`,
  );
  assert.ok(
    Number.isInteger(report.product_error_calls) &&
      report.product_error_calls >= 0,
    `${label}: invalid product error count`,
  );
  assert.equal(
    report.integrity_passed,
    report.product_error_calls === 0,
    `${label}: integrity flag and product errors disagree`,
  );
  for (const field of ["source", "gold", "semble_gold", "protocol"]) {
    assert.match(
      report.suite?.[field] ?? "",
      /^[a-f0-9]{64}$/,
      `${label}: missing suite ${field} identity`,
    );
  }
  const ids = uniqueStrings(
    report.expected_task_ids,
    `${label}: expected tasks`,
  );
  assert.ok(
    ["full-20-original-queries", "explicit-subset"].includes(report.scope),
    `${label}: unknown scope`,
  );
  assert.ok(
    report.scope === "full-20-original-queries"
      ? ids.length === 20
      : ids.length < 20,
    `${label}: scope/task-count mismatch`,
  );
  assert.ok(
    report.modes &&
      typeof report.modes === "object" &&
      !Array.isArray(report.modes),
    `${label}: missing modes`,
  );
  const modes = uniqueStrings(Object.keys(report.modes), `${label}: modes`);
  assert.ok(
    modes.includes("hybrid") && modes.every((mode) => MODES.includes(mode)),
    `${label}: unsupported mode selection`,
  );
  assert.ok(Array.isArray(report.tasks), `${label}: missing task rows`);
  const rows = new Map();
  for (const row of report.tasks) {
    const context = `${label}: ${row.task_id}/${row.mode}`;
    if (report.preview !== undefined)
      assert.equal(row.preview, report.preview, `${context}: preview mismatch`);
    assert.ok(
      ids.includes(row.task_id) && modes.includes(row.mode),
      `${context}: unexpected task/mode`,
    );
    assert.ok(!rows.has(key(row)), `${context}: duplicate task/mode`);
    assert.equal(
      row.repetition,
      5,
      `${context}: comparison requires quality repetition 5`,
    );
    assert.equal(
      row.quality_observation,
      true,
      `${context}: not a quality observation`,
    );
    assert.ok(
      CATEGORIES.includes(row.category),
      `${context}: invalid category`,
    );
    assert.ok(
      typeof row.repository === "string" && row.repository.length > 0,
      `${context}: missing repository`,
    );
    assert.ok(
      typeof row.language === "string" && row.language.length > 0,
      `${context}: missing benchmark language`,
    );
    const targets = row.semble_official?.targets;
    assert.ok(
      Array.isArray(targets) && targets.length > 0,
      `${context}: missing Semble file-target projection`,
    );
    assert.ok(
      targets.every(
        (target) =>
          Object.keys(target).length === 1 &&
          typeof target.path === "string" &&
          target.path.length > 0,
      ),
      `${context}: expected projected file-only targets`,
    );
    assert.equal(
      new Set(targets.map((target) => target.path)).size,
      targets.length,
      `${context}: duplicate projected file targets`,
    );
    assert.ok(
      Array.isArray(row.items),
      `${context}: missing public parsed items`,
    );
    if (row.execution_status === "product_error")
      assert.equal(
        row.items.length,
        0,
        `${context}: product errors cannot provide official retrieval credit`,
      );
    assert.deepEqual(
      row.semble_official,
      { targets, ...scoreSembleMetric(row.items, targets) },
      `${context}: Semble official score differs from public items`,
    );
    assert.ok(
      ["success", "product_error"].includes(row.execution_status),
      `${context}: invalid execution status`,
    );
    if (row.gold_status === "reviewed") {
      assert.equal(
        row.status,
        row.execution_status === "success" ? "scored" : "product_error",
        `${context}: inconsistent scored status`,
      );
      const rank = row.first_hit_rank;
      assert.ok(
        rank === "not_in_top10" ||
          (Number.isInteger(rank) && rank >= 1 && rank <= 10),
        `${context}: invalid first rank`,
      );
      for (const cutoff of [1, 5, 10]) {
        assert.equal(
          row[`hit_at_${cutoff}`],
          Number(Number.isInteger(rank) && rank <= cutoff),
          `${context}: hit/rank mismatch`,
        );
      }
      assert.equal(
        row.rr_at_10,
        Number.isInteger(rank) ? 1 / rank : 0,
        `${context}: RR/rank mismatch`,
      );
      assert.equal(
        row.ndcg_at_5 === null,
        row.ndcg_at_10 === null,
        `${context}: incomplete nDCG eligibility`,
      );
      for (const metric of ["ndcg_at_5", "ndcg_at_10"]) {
        assert.ok(
          row[metric] === null ||
            (Number.isFinite(row[metric]) &&
              row[metric] >= 0 &&
              row[metric] <= 1),
          `${context}: invalid ${metric}`,
        );
        if (rank === "not_in_top10" && row[metric] !== null)
          assert.equal(
            row[metric],
            0,
            `${context}: nDCG without an accepted hit`,
          );
      }
      if (row.execution_status === "product_error") {
        assert.equal(
          rank,
          "not_in_top10",
          `${context}: product error cannot have a hit`,
        );
      }
    } else {
      assert.ok(
        ["unknown", "disputed"].includes(row.gold_status),
        `${context}: invalid gold status`,
      );
      assert.equal(
        row.status,
        `gold_${row.gold_status}`,
        `${context}: inconsistent unscored status`,
      );
      assert.equal(
        row.first_hit_rank,
        null,
        `${context}: unreviewed gold has a rank`,
      );
      for (const metric of METRICS)
        assert.equal(
          row[metric],
          null,
          `${context}: unreviewed gold has a score`,
        );
    }
    rows.set(key(row), row);
  }
  assert.equal(
    rows.size,
    ids.length * modes.length,
    `${label}: missing task/mode coverage`,
  );
  assert.ok(
    report.product_error_calls >=
      report.tasks.filter((row) => row.execution_status === "product_error")
        .length,
    `${label}: product error count omits quality errors`,
  );
  return { ids, modes, rows };
}

function scoreView(row) {
  return Object.fromEntries(
    [
      "status",
      "execution_status",
      "gold_status",
      "repository",
      "language",
      "semble_official",
      "first_hit_rank",
      ...METRICS,
    ].map((field) => [field, row[field]]),
  );
}

function summarize(rows) {
  const scored = rows.filter((row) => row.hit_at_10 !== null);
  const ndcg = rows.filter((row) => row.ndcg_at_10 !== null);
  return {
    planned_tasks: rows.length,
    scored_tasks: scored.length,
    ndcg_tasks: ndcg.length,
    ...Object.fromEntries(
      HITS.map((metric) => [
        `${metric}_count`,
        scored.reduce((sum, row) => sum + row[metric], 0),
      ]),
    ),
    mrr_at_10: average(scored.map((row) => row.rr_at_10)),
    ndcg_at_5: average(ndcg.map((row) => row.ndcg_at_5)),
    ndcg_at_10: average(ndcg.map((row) => row.ndcg_at_10)),
    product_error_tasks: rows.filter(
      (row) => row.execution_status === "product_error",
    ).length,
  };
}

function summarizePairs(pairs) {
  const baseline = summarize(pairs.map((pair) => pair.baseline));
  const candidate = summarize(pairs.map((pair) => pair.candidate));
  return {
    baseline,
    candidate,
    delta: Object.fromEntries(
      Object.keys(baseline).map((field) => [
        field,
        difference(baseline[field], candidate[field]),
      ]),
    ),
    hit_flips: Object.fromEntries(
      HITS.map((metric) => [
        metric,
        {
          improvements: pairs.filter((pair) => pair.delta[metric] === 1).length,
          regressions: pairs.filter((pair) => pair.delta[metric] === -1).length,
        },
      ]),
    ),
    status_transitions: Object.fromEntries(
      [...new Set(pairs.map((pair) => pair.status_transition))]
        .sort()
        .map((transition) => [
          transition,
          pairs.filter((pair) => pair.status_transition === transition).length,
        ]),
    ),
  };
}

function officialPair(pairs) {
  const baseline = summarizeSembleOfficial(pairs.map((pair) => pair.baseline));
  const candidate = summarizeSembleOfficial(
    pairs.map((pair) => pair.candidate),
  );
  return {
    baseline,
    candidate,
    delta: Object.fromEntries(
      ["query_mean", "repository_macro", "language_macro"].map(
        (aggregation) => [
          aggregation,
          Object.fromEntries(
            ["ndcg_at_5", "ndcg_at_10"].map((metric) => [
              metric,
              difference(
                baseline[aggregation][metric],
                candidate[aggregation][metric],
              ),
            ]),
          ),
        ],
      ),
    ),
  };
}

function reportIdentity(report) {
  const unique = (getter) =>
    [
      ...new Set((report.repositories ?? []).map(getter).filter(Boolean)),
    ].sort();
  return {
    generated_at: report.generated_at ?? null,
    integrity_passed: report.integrity_passed,
    product_error_calls: report.product_error_calls,
    candidate_commits: unique((repo) => repo.candidate_commit),
    package_sha256: unique((repo) => repo.package?.tarball_sha256),
    model_files_sha256: unique((repo) => repo.model_files_sha256),
  };
}

/** Compare validated quality rows; task ordering and cached aggregate summaries are not score inputs. */
export function compareReports(baseline, candidate) {
  if (baseline.previews || candidate.previews) {
    assert.ok(
      baseline.previews && candidate.previews,
      "incompatible preview arm sets",
    );
    const previews = Object.fromEntries(
      ["short", "full"].map((preview) => [
        preview,
        compareReports(
          selectPreviewReport(baseline, preview),
          selectPreviewReport(candidate, preview),
        ),
      ]),
    );
    return { ...previews.short, primary_preview: "short", previews };
  }
  assert.equal(
    baseline.preview,
    candidate.preview,
    "incompatible preview arms",
  );
  const before = validateReport(baseline, "baseline");
  const after = validateReport(candidate, "candidate");
  for (const field of ["source", "gold", "semble_gold", "protocol"])
    assert.equal(
      baseline.suite[field],
      candidate.suite[field],
      `incompatible suite ${field}`,
    );
  assert.equal(baseline.scope, candidate.scope, "incompatible report scopes");
  assert.deepEqual(before.ids, after.ids, "incompatible expected task sets");
  assert.deepEqual(before.modes, after.modes, "incompatible mode sets");
  const modes = MODES.filter((mode) => before.modes.includes(mode));
  const pairs = [];
  for (const mode of modes)
    for (const task_id of baseline.expected_task_ids) {
      const oldRow = before.rows.get(key({ task_id, mode }));
      const newRow = after.rows.get(key({ task_id, mode }));
      for (const field of ["repository", "category", "gold_status", "language"])
        assert.equal(
          oldRow[field],
          newRow[field],
          `${task_id}/${mode}: incompatible ${field}`,
        );
      assert.deepEqual(
        oldRow.semble_official.targets,
        newRow.semble_official.targets,
        `${task_id}/${mode}: incompatible Semble target projection`,
      );
      for (const metric of METRICS)
        assert.equal(
          oldRow[metric] === null,
          newRow[metric] === null,
          `${task_id}/${mode}: changed scoring eligibility for ${metric}`,
        );
      const delta = Object.fromEntries(
        METRICS.map((metric) => [
          metric,
          difference(oldRow[metric], newRow[metric]),
        ]),
      );
      pairs.push({
        task_id,
        mode,
        repository: oldRow.repository,
        category: oldRow.category,
        baseline: scoreView(oldRow),
        candidate: scoreView(newRow),
        delta,
        semble_official_delta: Object.fromEntries(
          ["ndcg_at_5", "ndcg_at_10"].map((metric) => [
            metric,
            newRow.semble_official[metric] - oldRow.semble_official[metric],
          ]),
        ),
        rank_change:
          delta.rr_at_10 === null
            ? "unscored"
            : delta.rr_at_10 > 0
              ? "improved"
              : delta.rr_at_10 < 0
                ? "regressed"
                : "unchanged",
        hit_flips: Object.fromEntries(
          HITS.map((metric) => [
            metric,
            delta[metric] === null
              ? "unscored"
              : delta[metric] > 0
                ? "improved"
                : delta[metric] < 0
                  ? "regressed"
                  : "unchanged",
          ]),
        ),
        status_transition: `${oldRow.status} -> ${newRow.status}`,
        execution_transition: `${oldRow.execution_status} -> ${newRow.execution_status}`,
      });
    }
  return {
    schema_version: 1,
    ...(baseline.preview === undefined ? {} : { preview: baseline.preview }),
    quality_repetition: 5,
    suite: structuredClone(baseline.suite),
    scope: baseline.scope,
    expected_task_ids: [...baseline.expected_task_ids],
    task_order_policy:
      "Task IDs and modes match as sets; tables follow baseline task order and hybrid/fts/vector mode order.",
    quality_gate: "report-only; no quality threshold or causal attribution",
    aggregation:
      "Recomputed from matched repetition-5 public items. Semble official metric includes query, repository and language means on the SWE-QA accepted-file projection. Supplementary anchor nDCG eligibility matches per task/mode.",
    baseline: reportIdentity(baseline),
    candidate: reportIdentity(candidate),
    warnings: [baseline, candidate].flatMap((report, i) =>
      report.integrity_passed
        ? []
        : [
            `${i === 0 ? "baseline" : "candidate"}: operational integrity failed with ${report.product_error_calls} product-error calls; valid reviewed quality zeros are retained. Differences can include delivery failures and are not pure ranking effects.`,
          ],
    ),
    modes: Object.fromEntries(
      modes.map((mode) => {
        const rows = pairs.filter((pair) => pair.mode === mode);
        const group = (field, values) =>
          Object.fromEntries(
            values.map((value) => [
              value,
              summarizePairs(rows.filter((row) => row[field] === value)),
            ]),
          );
        return [
          mode,
          {
            summary: summarizePairs(rows),
            semble_official: officialPair(rows),
            by_category: group(
              "category",
              CATEGORIES.filter((category) =>
                rows.some((row) => row.category === category),
              ),
            ),
            by_repository: group(
              "repository",
              [...new Set(rows.map((row) => row.repository))].sort(),
            ),
          },
        ];
      }),
    ),
    tasks: pairs,
  };
}

const cell = (value) =>
  String(value ?? "N/A")
    .replaceAll("|", "\\|")
    .replace(/[\r\n]+/g, " ");
const signed = (value) =>
  value === null ? "N/A" : `${value > 0 ? "+" : ""}${value.toFixed(3)}`;
const change = (before, after) => `${cell(before)} → ${cell(after)}`;

export function markdownComparison(result) {
  if (result.previews)
    return ["short", "full"]
      .map((preview) =>
        markdownComparison(result.previews[preview]).replace(
          "# zg Retrieval-only version comparison",
          `# zg Retrieval-only version comparison — ${preview} source`,
        ),
      )
      .join("\n");
  const lines = [
    "# zg Retrieval-only version comparison",
    "",
    `Scope: **${result.scope}**; **${result.expected_task_ids.length} questions**. Delta is candidate minus baseline.`,
    "",
    "Source, anchor Gold, Semble file-target projection and protocol identities match. Task order may differ, but task/mode coverage and per-task scoring eligibility must match. Official quality is recomputed from the paired fifth public result lists; repeats are not independent quality samples.",
    "",
    "This is a report-only comparison, with no quality gate and no causal attribution. Partial source-entry positives do not measure complete answer evidence or E2E token savings.",
    ...markdownSembleOfficialTable(
      Object.fromEntries(
        Object.entries(result.modes).flatMap(([mode, entry]) =>
          ["baseline", "candidate"].map((side) => [
            `${mode} / ${side}`,
            { semble_official: entry.semble_official[side] },
          ]),
        ),
      ),
    ),
  ];
  if (result.warnings.length)
    lines.push(
      "",
      "## Operational integrity warnings",
      "",
      ...result.warnings.map((warning) => `- ${cell(warning)}`),
    );
  const header = [
    "| Group | Scored / planned | nDCG tasks | ΔHit@1 count | ΔHit@5 count | ΔHit@10 count | ΔMRR@10 | ΔnDCG@5 | ΔnDCG@10 |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
  ];
  const summaryLine = (label, group) =>
    `| ${cell(label)} | ${group.baseline.scored_tasks}/${group.baseline.planned_tasks} | ${group.baseline.ndcg_tasks} | ${signed(group.delta.hit_at_1_count)} | ${signed(group.delta.hit_at_5_count)} | ${signed(group.delta.hit_at_10_count)} | ${signed(group.delta.mrr_at_10)} | ${signed(group.delta.ndcg_at_5)} | ${signed(group.delta.ndcg_at_10)} |`;
  lines.push(
    "",
    "## Supplementary source-anchor metrics by mode",
    "",
    ...header,
  );
  for (const [mode, report] of Object.entries(result.modes))
    lines.push(summaryLine(mode, report.summary));
  for (const [field, title] of [
    ["by_category", "By category"],
    ["by_repository", "By repository"],
  ]) {
    lines.push("", `## ${title}`, "", ...header);
    for (const [mode, report] of Object.entries(result.modes))
      for (const [name, group] of Object.entries(report[field]))
        lines.push(summaryLine(`${mode} / ${name}`, group));
  }
  lines.push(
    "",
    "## Semble official metric per question",
    "",
    "| Task / mode | Baseline nDCG@5 / @10 | Candidate nDCG@5 / @10 | ΔnDCG@5 / @10 | Baseline target ranks | Candidate target ranks |",
    "| --- | --- | --- | --- | --- | --- |",
    ...result.tasks.map(
      (row) =>
        `| ${cell(row.task_id)} / ${cell(row.mode)} | ${signed(row.baseline.semble_official.ndcg_at_5)} / ${signed(row.baseline.semble_official.ndcg_at_10)} | ${signed(row.candidate.semble_official.ndcg_at_5)} / ${signed(row.candidate.semble_official.ndcg_at_10)} | ${signed(row.semble_official_delta.ndcg_at_5)} / ${signed(row.semble_official_delta.ndcg_at_10)} | ${row.baseline.semble_official.target_ranks.map((rank) => rank ?? "not found").join(", ")} | ${row.candidate.semble_official.target_ranks.map((rank) => rank ?? "not found").join(", ")} |`,
    ),
    "",
    "## Per question and mode",
    "",
    "| Task / mode | First rank | Rank change | Hit@1 / @5 / @10 | ΔRR@10 | ΔnDCG@5 / @10 | Scoring status | Execution status |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
  );
  for (const row of result.tasks)
    lines.push(
      `| ${cell(row.task_id)} / ${cell(row.mode)} | ${change(row.baseline.first_hit_rank, row.candidate.first_hit_rank)} | ${row.rank_change} | ${HITS.map((metric) => change(row.baseline[metric], row.candidate[metric])).join(" / ")} | ${signed(row.delta.rr_at_10)} | ${signed(row.delta.ndcg_at_5)} / ${signed(row.delta.ndcg_at_10)} | ${cell(row.status_transition)} | ${cell(row.execution_transition)} |`,
    );
  lines.push(
    "",
    "`not_in_top10` means no known accepted entry in the returned top ten; it does not reveal a rank beyond ten. Product-error zeros remain in the reviewed denominator. Unreviewed Gold remains N/A. JSON contains version identities, matched denominators, hit-flip counts and status transitions.",
  );
  return lines.join("\n") + "\n";
}

export async function writeComparison(baselinePath, candidatePath, outputPath) {
  const paths = [resolve(baselinePath), resolve(candidatePath)];
  const bytes = await Promise.all(paths.map((path) => readFile(path)));
  const result = compareReports(...bytes.map((raw) => JSON.parse(raw)));
  result.inputs = Object.fromEntries(
    paths.map((path, i) => [
      i === 0 ? "baseline" : "candidate",
      { path, sha256: hash(bytes[i]) },
    ]),
  );
  const output = resolve(outputPath);
  // Validate before creating artifacts; never overwrite a previous comparison.
  await mkdir(output, { recursive: false });
  await writeFile(
    join(output, "comparison.json"),
    `${JSON.stringify(result, null, 2)}\n`,
  );
  await writeFile(join(output, "comparison.md"), markdownComparison(result));
  return result;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const args = process.argv.slice(2);
  if (args.length !== 3) {
    console.error(
      "Usage: node compare.mjs BASELINE_REPORT.json CANDIDATE_REPORT.json NEW_OUTPUT_DIR",
    );
    process.exitCode = 1;
  } else {
    writeComparison(...args)
      .then(() =>
        console.log(`Comparison: ${join(resolve(args[2]), "comparison.md")}`),
      )
      .catch((error) => {
        console.error(error);
        process.exitCode = 1;
      });
  }
}
