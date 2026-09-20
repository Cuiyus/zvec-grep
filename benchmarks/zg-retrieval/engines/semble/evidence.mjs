import assert from "node:assert/strict";
import { readFile, realpath } from "node:fs/promises";
import { join, isAbsolute } from "node:path";
import { inside, fileHash, objectHash, readJson } from "../../core/lib.mjs";
import { createSembleResponseParser } from "./parse.mjs";

const same = (a, b, message) =>
  assert.equal(objectHash(a), objectHash(b), message);
const sha = (value, label) =>
  assert.match(value ?? "", /^[a-f0-9]{64}$/, label);

export async function auditVisibleSource(items, root, chunks) {
  const errors = [];
  for (const item of items) {
    assert.ok(
      inside(await realpath(root), await realpath(join(root, item.path))),
      "visible source escapes corpus",
    );
    const visible = item.source_content;
    const carriers = chunks.filter(
      (chunk) =>
        chunk.file_path === item.path &&
        chunk.start_line === item.range.start_line &&
        chunk.end_line === item.range.end_line &&
        chunk.content === visible,
    );
    if (!carriers.length)
      errors.push(
        `rank ${item.rank}: full content not bound to a persisted native chunk`,
      );
    const source = (await readFile(join(root, item.path), "utf8"))
      .replace(/\r\n?/g, "\n")
      .split("\n");
    for (const [index, line] of item.source_lines.entries()) {
      const actual = source[line.line - 1];
      const matches =
        actual === line.text ||
        (index === 0 && actual?.endsWith(line.text)) ||
        (index === item.source_lines.length - 1 &&
          !visible.endsWith("\n") &&
          actual?.startsWith(line.text)) ||
        (item.source_lines.length === 1 &&
          !visible.endsWith("\n") &&
          carriers.length > 0 &&
          actual?.includes(line.text));
      if (!matches)
        errors.push(
          `rank ${item.rank} ${item.path}:${line.line}: visible line does not map to locked source`,
        );
    }
  }
  return errors;
}

/** Compare all native fields, including scores and final newlines, without Gold. */
export function compareSdkMcp(response, sdkQuery, expectedQuery) {
  try {
    assert.equal(
      sdkQuery.query,
      expectedQuery,
      "SDK query differs from original",
    );
    assert.ok(Array.isArray(sdkQuery.results), "SDK results missing");
    assert.notEqual(response?.isError, true, "MCP returned a product error");
    assert.equal(response?.content?.length, 1, "MCP text block count differs");
    assert.equal(response.content[0].type, "text", "MCP response is not text");
    const payload = JSON.parse(response.content[0].text);
    const expected = sdkQuery.results.length
      ? { query: expectedQuery, results: sdkQuery.results }
      : { error: "No results found." };
    assert.deepEqual(payload, expected, "MCP and SDK native results differ");
    return [];
  } catch (error) {
    return [`SDK/MCP parity: ${error.message}`];
  }
}

/** A product failure has no successful result to compare, but remains a quality zero. */
export function sdkParityOutcome(response, sdkQuery, expectedQuery) {
  try {
    assert.equal(
      sdkQuery.query,
      expectedQuery,
      "SDK query differs from original",
    );
    assert.ok(Array.isArray(sdkQuery.results), "SDK results missing");
    const productError =
      response?.isError === true ||
      createSembleResponseParser({ expectedQuery })(response)
        .execution_status === "product_error";
    if (productError)
      return { status: "product_error", matches: null, errors: [] };
  } catch (error) {
    return { matches: false, errors: [`SDK/MCP parity: ${error.message}`] };
  }
  const errors = compareSdkMcp(response, sdkQuery, expectedQuery);
  return { matches: errors.length === 0, errors };
}

export async function checkedArtifact(directory, reference, expectedPath) {
  assert.equal(
    reference?.path,
    expectedPath,
    `unexpected artifact path: ${expectedPath}`,
  );
  sha(reference.sha256, `${expectedPath}: missing file hash`);
  const path = join(directory, reference.path);
  assert.ok(
    inside(await realpath(directory), await realpath(path)),
    `${expectedPath}: artifact escapes run`,
  );
  assert.equal(
    await fileHash(path),
    reference.sha256,
    `${expectedPath}: artifact file hash mismatch`,
  );
  return path;
}

export async function auditFrozenRun(
  directory,
  run,
  suite,
  tasks,
  modelIdentity,
) {
  assert.equal(
    run.post_run_integrity,
    "verified",
    "post-run integrity not verified",
  );
  const inventories = {};
  for (const kind of ["corpus", "index", "model"]) {
    inventories[kind] = {};
    for (const phase of ["before", "after"]) {
      const name = `${kind}-${phase}.json`;
      const path = await checkedArtifact(
        directory,
        run.evidence?.[phase]?.[kind],
        name,
      );
      const inventory = await readJson(path);
      sha(inventory.sha256, `${name}: missing inventory hash`);
      assert.ok(
        Array.isArray(inventory.entries) && inventory.entries.length > 0,
        `${name}: empty inventory`,
      );
      assert.equal(
        objectHash(inventory.entries),
        inventory.sha256,
        `${name}: changed/truncated inventory`,
      );
      const paths = inventory.entries.map((entry) => entry.path);
      assert.ok(
        paths.every(
          (path) =>
            typeof path === "string" &&
            path.length &&
            !isAbsolute(path) &&
            !path.split(/[\\/]/).includes(".."),
        ),
        `${name}: unsafe inventory path`,
      );
      assert.equal(
        new Set(paths).size,
        paths.length,
        `${name}: duplicate inventory paths`,
      );
      inventories[kind][phase] = inventory;
    }
    same(
      inventories[kind].before,
      inventories[kind].after,
      `${kind} identity drift`,
    );
  }
  const files = new Map(
    inventories.corpus.before.entries.map((entry) => [entry.path, entry]),
  );
  for (const task of tasks)
    for (const target of suite.gold[task.task_id].targets)
      assert.equal(
        files.get(target.path)?.sha256,
        target.source_sha256,
        `${task.task_id}: stale/missing Gold source ${target.path}`,
      );
  assert.equal(
    inventories.model.before.sha256,
    modelIdentity,
    "shard model differs from experiment model identity",
  );
  const preparation = await readJson(
    await checkedArtifact(directory, run.preparation, "preparation.json"),
  );
  assert.equal(
    preparation.loaded_from_disk,
    false,
    "index preparation restored an index cache",
  );
  assert.equal(
    preparation.source_mapping_verified,
    true,
    "prepared source mapping not verified",
  );
  same(
    preparation.content,
    ["code"],
    "prepared index content is not code-only",
  );
  assert.ok(
    Number.isInteger(preparation.chunk_count) && preparation.chunk_count >= 0,
    "invalid prepared chunk count",
  );
  assert.ok(
    Array.isArray(preparation.indexed_files) &&
      preparation.indexed_files.every((path) => files.has(path)),
    "prepared index contains files outside frozen corpus",
  );
  assert.equal(
    new Set(preparation.indexed_files).size,
    preparation.indexed_files.length,
    "duplicate prepared indexed file",
  );
  assert.ok(
    preparation.chunk_count >= preparation.indexed_files.length,
    "prepared chunk/file count mismatch",
  );
  return {
    corpus_sha256: inventories.corpus.before.sha256,
    index_sha256: inventories.index.before.sha256,
    model_sha256: inventories.model.before.sha256,
  };
}

export async function auditSdkParity(
  directory,
  run,
  tasks,
  calls,
  modelDirectory,
) {
  const replay = await readJson(
    await checkedArtifact(directory, run.sdk_replay, "sdk-replay.json"),
  );
  const parity = await readJson(
    await checkedArtifact(directory, run.sdk_parity, "sdk-parity.json"),
  );
  assert.equal(replay.schema_version, 1, "unsupported SDK replay schema");
  assert.equal(replay.engine, "semble", "wrong SDK replay engine");
  assert.equal(
    replay.loaded_from_disk,
    true,
    "SDK replay did not use the frozen index",
  );
  assert.equal(replay.corpus_root, run.corpus_root, "SDK corpus root mismatch");
  assert.equal(replay.model_path, modelDirectory, "SDK model path mismatch");
  const preparation = await readJson(join(directory, "preparation.json"));
  assert.equal(
    replay.index_directory,
    preparation.index_directory,
    "SDK replay index differs from prepared index",
  );
  same(
    replay.content,
    ["code"],
    "SDK replay content differs from code-only protocol",
  );
  same(
    replay.parameters,
    {
      top_k: 10,
      alpha: null,
      rerank: null,
      filter_languages: null,
      filter_paths: null,
      max_snippet_lines: null,
    },
    "SDK replay parameters differ from official defaults",
  );
  assert.equal(parity.schema_version, 1, "unsupported SDK parity schema");
  assert.equal(
    parity.quality_repetition,
    5,
    "SDK parity must bind repetition 5",
  );
  assert.equal(
    parity.sdk_replay_sha256,
    run.sdk_replay.sha256,
    "SDK parity replay hash mismatch",
  );
  assert.ok(
    Array.isArray(replay.queries) && Array.isArray(parity.calls),
    "missing SDK replay/parity task records",
  );
  same(
    replay.queries.map((query) => query.task_id),
    tasks.map((task) => task.task_id),
    "SDK replay task coverage/order mismatch",
  );
  same(
    parity.calls.map((call) => call.task_id),
    tasks.map((task) => task.task_id),
    "SDK parity task coverage/order mismatch",
  );
  let allCompared = true;
  for (const task of tasks) {
    const sdk = replay.queries.find((query) => query.task_id === task.task_id);
    const record = parity.calls.find((call) => call.task_id === task.task_id);
    const call = calls.find(
      (call) => call.task_id === task.task_id && call.repetition === 5,
    );
    assert.ok(call, "SDK parity has no fifth MCP call");
    assert.equal(sdk.query, task.query, "SDK replay changed original query");
    assert.ok(Array.isArray(sdk.results), "SDK replay omitted result list");
    assert.equal(
      record.repetition,
      5,
      "SDK parity call is not fifth repetition",
    );
    assert.equal(
      record.raw_path,
      call.raw_path,
      "SDK parity raw call mapping mismatch",
    );
    assert.equal(
      record.raw_sha256,
      call.raw_sha256,
      "SDK parity raw hash mismatch",
    );
    assert.equal(
      record.sdk_result_sha256,
      objectHash(sdk),
      "SDK parity result hash mismatch",
    );
    const raw = await readJson(
      await checkedArtifact(
        directory,
        { path: call.raw_path, sha256: call.raw_sha256 },
        `raw/${task.task_slug}-hybrid-5.json`,
      ),
    );
    const outcome = sdkParityOutcome(raw, sdk, task.query);
    if (outcome.status === "product_error") {
      assert.equal(
        record.status,
        "product_error",
        "SDK parity product failure status missing",
      );
      assert.equal(
        record.matches,
        null,
        "SDK parity product failure cannot claim a comparison",
      );
      same(record.errors, [], "SDK parity product failure errors");
      allCompared = false;
      continue;
    }
    assert.equal(
      record.status ?? null,
      null,
      "SDK parity successful response has an invalid status",
    );
    assert.equal(record.matches, true, "SDK parity mismatch");
    same(record.errors, [], "SDK parity errors");
    assert.ok(
      !raw.isError &&
        raw.content?.length === 1 &&
        raw.content[0].type === "text",
      "SDK parity requires a successful native MCP response",
    );
    const payload = JSON.parse(raw.content[0].text);
    same(
      payload,
      sdk.results.length
        ? { query: sdk.query, results: sdk.results }
        : { error: "No results found." },
      "fifth MCP result differs from official SDK replay",
    );
  }
  return allCompared;
}
