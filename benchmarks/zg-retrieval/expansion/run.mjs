import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { arch, cpus, platform } from "node:os";
import { delimiter, join, resolve } from "node:path";
import { performance } from "node:perf_hooks";
import { parseArgs } from "node:util";
import { fileURLToPath, pathToFileURL } from "node:url";
import { fileHash, run, writeJson } from "../core/io.mjs";
import { scoreFileRetrieval } from "../metrics/files.mjs";
import { scoreNdcg } from "../metrics/ndcg.mjs";
import {
  parseVisibleResponse,
  validateSearchRoute,
} from "../engines/zg/parse.mjs";
import { freePort, packageCandidate } from "../engines/zg/run.mjs";
import { snapshotIndex } from "../engines/zg/snapshot.mjs";
import {
  loadPilot,
  prepareBeir,
  prepareQuarryTask,
  verifyQuarrySource,
} from "./datasets.mjs";

const REPETITIONS = 5;
const LIMIT = 10;

function searchArguments(task, root, mode) {
  return {
    root,
    ...(mode === "hybrid" ? { query: task.query } : { [mode]: [task.query] }),
    limit: LIMIT,
    autoUpdate: false,
    freshness: "eventual",
    preferSymbol: false,
  };
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const center = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[center]
    : (sorted[center - 1] + sorted[center]) / 2;
}

export function summarizePilotRows(rows, modes = ["hybrid", "fts", "vector"]) {
  return modes.map((mode) => {
    const selected = rows.filter((row) => row.mode === mode);
    const valid = selected.filter((row) => row.status === "success");
    const average = (select) =>
      valid.length
        ? valid.reduce((sum, row) => sum + select(row), 0) / valid.length
        : null;
    const latencies = selected.flatMap((row) =>
      row.calls
        .filter((call) => call.status === "success")
        .map((call) => call.latency_ms),
    );
    return {
      mode,
      completed: valid.length,
      planned: 10,
      metrics: {
        file_hit_at_1: average((row) => row.file.hit_at_1),
        file_hit_at_5: average((row) => row.file.hit_at_5),
        file_hit_at_10: average((row) => row.file.hit_at_10),
        file_mrr_at_10: average((row) => row.file.rr_at_10),
        ndcg_at_10: average((row) => row.ndcg.ndcg_at_10),
      },
      measurements: {
        output_bytes_mean: average((row) => row.output_bytes),
        output_sample_count: valid.length,
        latency_ms_p50: median(latencies),
        latency_sample_count: latencies.length,
      },
    };
  });
}

function formatNumber(value, places = 4) {
  return Number.isFinite(value) ? value.toFixed(places) : "—";
}

export function markdownPilotReport(report) {
  const lines = [
    `## ${report.label} · ${report.status === "success" ? "✅ Complete" : "❌ Incomplete"}`,
    "",
    `10 original queries · model \`${report.model}\` · Rust MCP default presentation · five calls/query`,
    "",
    "| Mode | Completed | File Hit@1 | File Hit@5 | File Hit@10 | File MRR@10 | nDCG@10 | Mean output (KiB) | Latency P50 (ms) |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
  ];
  for (const row of report.summary) {
    const hit = (cutoff) => {
      const value = row.metrics[`file_hit_at_${cutoff}`];
      return Number.isFinite(value)
        ? `${(value * 100).toFixed(1)}% (${Math.round(value * row.completed)}/${row.completed})`
        : "—";
    };
    lines.push(
      `| zg-${row.mode} | ${row.completed}/${row.planned} | ${hit(1)} | ${hit(5)} | ${hit(10)} | ${formatNumber(row.metrics.file_mrr_at_10)} | ${formatNumber(row.metrics.ndcg_at_10)} | ${formatNumber(row.measurements.output_bytes_mean == null ? null : row.measurements.output_bytes_mean / 1024, 2)} | ${formatNumber(row.measurements.latency_ms_p50, 2)} |`,
    );
  }
  lines.push(
    "",
    report.suite === "quarry10"
      ? "Quarry's function-level positives are projected to unique files. These are pilot file metrics, not the official Quarry function recall."
      : "SciFact uses the original BEIR test queries and qrels with the complete corpus; each document is one Markdown file.",
    "",
    "A score is shown only for completed queries. Incomplete queries are listed below and are never silently scored as zero. Output is public MCP text bytes from the fifth successful quality call; latency covers successful calls and excludes indexing.",
  );
  if (report.failures.length) {
    lines.push("", "### Failed tasks or calls", "");
    for (const failure of report.failures)
      lines.push(
        `- \`${String(failure.task_id).replaceAll("`", "")}\`: ${String(failure.reason).replaceAll(/\r?\n/g, " ")}`,
      );
  }
  return `${lines.join("\n")}\n`;
}

async function writeReport(output, report) {
  report.summary = summarizePilotRows(report.rows);
  report.status =
    report.failures.length || report.summary.some((row) => row.completed !== 10)
      ? "failed"
      : "success";
  report.finished_at = new Date().toISOString();
  await writeJson(join(output, "report.json"), report);
  await writeFile(join(output, "report.md"), markdownPilotReport(report));
}

async function runGroup(pilot, group, candidate, options, report, mcp) {
  const evidence = join(options.output, "evidence", group.id);
  await mkdir(evidence, { recursive: true });
  const root = resolve(group.root);
  const home = join(evidence, "runtime-home");
  const opencode = join(evidence, "opencode.json");
  await mkdir(home, { recursive: true });
  await writeJson(join(home, ".zvec-grep", "config.json"), {
    version: 1,
    server: { host: "127.0.0.1", port: await freePort() },
    defaults: {
      embedding: pilot.lock.model,
      modelCacheDir: options.modelCache,
    },
    models: { [pilot.lock.model]: { device: "cpu" } },
  });
  const env = {
    ...process.env,
    HOME: home,
    USERPROFILE: home,
    OPENCODE_CONFIG: opencode,
    ZVEC_GREP_HOME: join(home, ".zvec-grep"),
    ZVEC_GREP_MODEL_CACHE: options.modelCache,
    ZVEC_GREP_DEVICE: "cpu",
    NO_COLOR: "1",
    FORCE_COLOR: "0",
    PATH: `${join(candidate.consumer, "node_modules/.bin")}${delimiter}${process.env.PATH ?? ""}`,
  };
  let client;
  try {
    const install = await run(
      candidate.cli,
      ["install", "--target", "opencode", "--yes", "--mcp-transport", "stdio"],
      { cwd: root, env },
    );
    await writeJson(join(evidence, "install.json"), install);
    const config = JSON.parse(await readFile(opencode, "utf8"));
    assert.equal(config.mcp?.zvec_grep?.type, "local");
    const command = config.mcp.zvec_grep.command;
    assert.ok(Array.isArray(command) && command.length >= 2);
    await run(candidate.cli, ["server", "off"], { cwd: root, env });
    const indexStart = performance.now();
    try {
      const indexed = await run(
        candidate.cli,
        [
          "index",
          root,
          "--mode",
          "direct",
          "--embedding",
          pilot.lock.model,
          "--model-cache",
          options.modelCache,
          "--device",
          "cpu",
          "--max-filesize",
          "1000000",
          "--iglob",
          group.indexGlob,
          "--debug",
        ],
        { cwd: root, env, timeout: 2_400_000 },
      );
      await writeJson(join(evidence, "index.json"), indexed);
    } catch (error) {
      await writeJson(
        join(evidence, "index.json"),
        error.result ?? { error: error.message },
      );
      throw error;
    } finally {
      report.index_seconds[group.id] = (performance.now() - indexStart) / 1000;
    }
    const before = await snapshotIndex({
      cli: candidate.cli,
      root,
      output: join(evidence, "status-before"),
      env,
    });
    assert.equal(before.failed_files.length, 0);
    assert.ok(before.files > 0);
    if (group.expectedIndexedFiles)
      assert.equal(
        before.files,
        group.expectedIndexedFiles,
        "BEIR corpus was not fully indexed",
      );
    const transport = new mcp.StdioClientTransport({
      command: command[0],
      args: command.slice(1),
      cwd: root,
      env,
      stderr: "pipe",
    });
    client = new mcp.Client({ name: "zg-retrieval-pilots", version: "1.0.0" });
    await client.connect(transport, { timeout: 120_000 });
    const listed = await client.listTools();
    assert.ok(listed.tools.some((tool) => tool.name === "zvec_grep_search"));
    for (const mode of pilot.modes) {
      for (const task of group.tasks) {
        const row = {
          task_id: task.id,
          mode,
          query: task.query,
          targets: task.targets,
          corpus_id: group.id,
          status: "pending",
          calls: [],
        };
        report.rows.push(row);
        for (let repetition = 1; repetition <= REPETITIONS; repetition++) {
          const args = searchArguments(task, root, mode);
          const started = performance.now();
          let response;
          let error = null;
          try {
            response = await client.callTool(
              { name: "zvec_grep_search", arguments: args },
              undefined,
              { timeout: 120_000 },
            );
            assert.notEqual(response.isError, true, "MCP product error");
            const parsed = parseVisibleResponse(response, {
              allowTrailingBlankOutsideRange: true,
            });
            validateSearchRoute(parsed.items, mode);
            if (repetition === REPETITIONS) {
              row.file = scoreFileRetrieval(parsed.items, task.targets);
              row.ndcg = scoreNdcg(parsed.items, task.targets);
              row.output_bytes = Buffer.byteLength(parsed.text, "utf8");
              row.items = parsed.items.map(
                ({ rank, path, range, matched_by }) => ({
                  rank,
                  path,
                  range,
                  matched_by,
                }),
              );
            }
          } catch (failure) {
            error = failure.message;
            response ??= {
              isError: true,
              content: [{ type: "text", text: error }],
            };
          }
          const latency = performance.now() - started;
          const raw = join(
            evidence,
            "raw",
            `${encodeURIComponent(task.id)}-${mode}-${repetition}.json`,
          );
          await writeJson(raw, {
            request: { name: "zvec_grep_search", arguments: args },
            response,
          });
          row.calls.push({
            repetition,
            status: error ? "failed" : "success",
            latency_ms: latency,
            raw_sha256: await fileHash(raw),
            error,
          });
          if (error)
            report.failures.push({
              task_id: task.id,
              mode,
              repetition,
              reason: error,
            });
        }
        row.status =
          row.calls[REPETITIONS - 1].status === "success" &&
          row.file &&
          row.ndcg
            ? "success"
            : "failed";
      }
    }
    const after = await snapshotIndex({
      cli: candidate.cli,
      root,
      output: join(evidence, "status-after"),
      env,
    });
    assert.equal(
      after.logical_content_sha256,
      before.logical_content_sha256,
      "index changed during retrieval",
    );
  } finally {
    if (client) await client.close().catch(() => undefined);
    await run(candidate.cli, ["server", "off"], { cwd: root, env }).catch(
      () => undefined,
    );
  }
}

export async function main(args = process.argv.slice(2)) {
  const { values } = parseArgs({
    args,
    options: {
      suite: { type: "string" },
      package: { type: "string" },
      output: { type: "string" },
      "model-cache": { type: "string" },
      "candidate-commit": { type: "string", default: "unrecorded" },
    },
  });
  assert.ok(
    values.suite && values.package && values.output,
    "requires --suite, --package and --output",
  );
  const pilot = await loadPilot(values.suite);
  const output = resolve(values.output);
  await mkdir(output, { recursive: false });
  const options = {
    output,
    modelCache: resolve(values["model-cache"] ?? join(output, "model-cache")),
  };
  await mkdir(options.modelCache, { recursive: true });
  const report = {
    schema_version: 1,
    suite: pilot.lock.suite,
    label:
      pilot.name === "beir"
        ? "BEIR / SciFact (test)"
        : "Quarry / quic-go (preimage)",
    model: pilot.lock.model,
    candidate_commit: values["candidate-commit"],
    environment: {
      platform: platform(),
      architecture: arch(),
      cpus: cpus().length,
    },
    started_at: new Date().toISOString(),
    rows: [],
    failures: [],
    index_seconds: {},
  };
  try {
    const candidate = await packageCandidate(values.package, output);
    report.package = candidate.identity;
    const require = createRequire(join(candidate.consumer, "package.json"));
    const mcp = {
      Client: (
        await import(
          pathToFileURL(require.resolve("@modelcontextprotocol/client")).href
        )
      ).Client,
      StdioClientTransport: (
        await import(
          pathToFileURL(require.resolve("@modelcontextprotocol/client/stdio"))
            .href
        )
      ).StdioClientTransport,
    };
    if (pilot.name === "beir") {
      const groups = await prepareBeir(pilot, output);
      await runGroup(pilot, groups[0], candidate, options, report, mcp);
    } else {
      await verifyQuarrySource(pilot, output);
      for (const task of pilot.lock.tasks) {
        try {
          const group = await prepareQuarryTask(pilot, task, output);
          await runGroup(pilot, group, candidate, options, report, mcp);
        } catch (error) {
          report.failures.push({ task_id: task.id, reason: error.message });
          console.error(`${task.id}: ${error.message}`);
        }
      }
    }
  } catch (error) {
    report.failures.push({ task_id: "suite", reason: error.message });
    console.error(`${pilot.name}: ${error.stack}`);
  }
  await writeReport(output, report);
  console.log(`Report: ${join(output, "report.md")}`);
  if (report.status !== "success") process.exitCode = 1;
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
)
  await main();
