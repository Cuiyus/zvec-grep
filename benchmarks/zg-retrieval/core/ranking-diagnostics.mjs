import { join } from "node:path";
import { run, writeJson } from "./io.mjs";

// Run only after every quality/latency observation for this index is complete.
// These direct CLI calls expose stored per-hit traces omitted by MCP rendering.
// They never contribute to benchmark scores or latency measurements.
export async function captureRankingDiagnostics(candidate, {
  tasks, modes, root, env, output,
}) {
  if (process.env.RETRIEVAL_RANK_DIAGNOSTICS !== "1") return;
  for (const task of tasks) {
    const id = task.task_id ?? task.id;
    for (const mode of modes) {
      const args = [
        ...(mode === "hybrid" ? [task.query] : [`--${mode}`, task.query]),
        "--limit", "10", "--mode", "direct", "--refresh", "off",
        "--preview", "none", "--trace", "--debug",
      ];
      let result, error = null;
      try {
        result = await run(candidate.cli, args, { env, cwd: root, timeout: 120_000 });
      } catch (failure) {
        result = failure.result ?? null;
        error = failure.message;
      }
      await writeJson(join(output, "ranking-diagnostics", `${encodeURIComponent(id)}-${mode}.json`), {
        kind: "post-quality-direct-cli-trace",
        contributes_to_quality_or_latency: false,
        task_id: id, mode,
        command: candidate.cli, args, cwd: root,
        result, error,
      });
    }
  }
}
