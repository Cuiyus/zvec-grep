#!/usr/bin/env node
/** Exercise the released SDK's remote document and query path before downloads. */
import { writeFile } from "node:fs/promises";
import {
  assertFreshInfo, loadProduction, retrievalRequest,
  withBenchmarkRemoteEmbeddingAuthorization,
} from "./readonly-search.mjs";

const options = { root: "/app", embeddingModel: "qwen/qwen3.7-text-embedding" };
const started = performance.now();
const record = {
  phase: "setup_sdk_probe", status: "failed", embedding_model: options.embeddingModel,
  included_in_agent_tokens: false, included_in_qa_results: false,
};
let service;
try {
  const production = await loadProduction("/opt/qa/node_modules/@zvec/zvec-grep");
  record.package = production.packageIdentity;
  service = await production.createZvecGrep({
    root: options.root, embedding: options.embeddingModel, modelCacheDir: "/models",
  });
  await withBenchmarkRemoteEmbeddingAuthorization(options, production,
    () => service.index({ root: options.root }));
  const info = await service.info({ root: options.root, includeStatus: true });
  assertFreshInfo(info, options.root, options.embeddingModel);
  const result = await withBenchmarkRemoteEmbeddingAuthorization(options, production,
    () => service.context(retrievalRequest(options.root,
      "如何在仓库中查找代码？ repository code search", "vector", 1)));
  const formatted = production.formatAgentContextResult(result, { preview: "short" });
  if (!formatted.includes("probe.md")) throw new Error("Vector SDK probe did not retrieve the fixture");
  record.status = "completed";
  record.document_indexed = true;
  record.vector_query_retrieved_fixture = true;
} catch (error) {
  // Only diagnostic categories leave this setup process, never an API response/key.
  record.error_type = error.name ?? "Error";
  record.error_code = error.code;
  process.exitCode = 1;
} finally {
  if (service) {
    try { await service.close(); }
    catch (error) { record.status = "failed"; record.close_error_type = error.name; process.exitCode = 1; }
  }
  record.wall_seconds = (performance.now() - started) / 1000;
  await writeFile("/logs/result.json", JSON.stringify(record, null, 2) + "\n");
  process.stdout.write(JSON.stringify(record) + "\n");
}
