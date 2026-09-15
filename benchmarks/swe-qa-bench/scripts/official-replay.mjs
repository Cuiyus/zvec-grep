#!/usr/bin/env node
/** Replay observed arguments through released native MCP. No model or repairs. */
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFile, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs, promisify } from "node:util";

const exec = promisify(execFile);
const LOCAL_MODEL = "local/potion-code-16m-v2";
export const stableJson = (value) =>
  JSON.stringify(value, (_key, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(
          Object.entries(item).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
        )
      : item,
  );
const hash = (value) => createHash("sha256").update(value).digest("hex");
const safeError = (error) => ({
  name: error?.name ?? "Error",
  message: String(error?.message ?? error),
  ...(error?.code !== undefined ? { code: error.code } : {}),
});

export function validatePlan(plan) {
  if (!plan || !Array.isArray(plan.requests) || plan.repetitions !== 5) {
    throw new Error("Replay requires requests[] and exactly five repetitions");
  }
  const ids = new Set();
  for (const row of plan.requests) {
    if (
      !row ||
      typeof row.request_id !== "string" ||
      !row.request_id ||
      ids.has(row.request_id)
    ) {
      throw new Error(
        "Each planned observation requires a unique nonempty request_id",
      );
    }
    ids.add(row.request_id);
    if (!Object.hasOwn(row, "request"))
      throw new Error(`Missing original request for ${row.request_id}`);
    if (Object.hasOwn(row, "mcp_request")) {
      const params = row.mcp_request;
      if (
        !params ||
        typeof params !== "object" ||
        Array.isArray(params) ||
        params.name !== row.tool_name ||
        stableJson(params.arguments) !== stableJson(row.request)
      ) {
        throw new Error(
          `Full MCP params disagree with tool_name/request for ${row.request_id}; refusing to repair`,
        );
      }
    }
  }
  return plan;
}

export function fullParams(row) {
  return Object.hasOwn(row, "mcp_request")
    ? structuredClone(row.mcp_request)
    : { name: row.tool_name ?? null, arguments: structuredClone(row.request) };
}

export function uniqueRequests(plan) {
  const groups = new Map();
  for (const row of validatePlan(plan).requests) {
    const identity = stableJson(fullParams(row));
    if (!groups.has(identity))
      groups.set(identity, { ...structuredClone(row), source_request_ids: [] });
    groups.get(identity).source_request_ids.push(row.request_id);
  }
  return [...groups.values()];
}

export async function connectNative({
  packageDir,
  command,
  cwd,
  env,
  logDir,
  timeoutMs,
}) {
  // Resolve the SDK supplied with the installed release, not a global version.
  const require = createRequire(join(resolve(packageDir), "package.json"));
  const importFromPackage = (name) =>
    import(pathToFileURL(require.resolve(name)).href);
  const [{ Client }, { StdioClientTransport }, { ResultSchema }] =
    await Promise.all([
      importFromPackage("@modelcontextprotocol/client"),
      importFromPackage("@modelcontextprotocol/client/stdio"),
      importFromPackage("@modelcontextprotocol/core"),
    ]);
  const client = new Client(
    { name: "zg-native-retrieval-replay", version: "1" },
    {
      // zg's installed stdio bridge serves the ordinary agent initialize
      // handshake; its private HTTP hop owns modern protocol negotiation.
      capabilities: {},
      versionNegotiation: { mode: "legacy" },
    },
  );
  const transport = new StdioClientTransport({
    command: command[0],
    args: command.slice(1),
    cwd,
    env,
    stderr: "pipe",
  });
  let stderr = "";
  transport.stderr?.on("data", (chunk) => {
    stderr += chunk.toString("utf8");
  });
  client.onerror = (error) => {
    stderr += `${error.message}\n`;
  };
  try {
    await client.connect(transport, { timeout: timeoutMs });
    const catalog = await client.listTools();
    return {
      catalog,
      server: client.getServerVersion(),
      instructions: client.getInstructions(),
      request: (toolName, args, params) =>
        client.request(
          {
            method: "tools/call",
            params: params ?? { name: toolName, arguments: args },
          },
          ResultSchema,
          { timeout: timeoutMs, resetTimeoutOnProgress: false },
        ),
      close: async () => {
        await client.close();
        await writeFile(join(logDir, "official-replay.stderr.txt"), stderr);
      },
    };
  } catch (error) {
    await client.close().catch(() => {});
    await writeFile(join(logDir, "official-replay.stderr.txt"), stderr);
    throw error;
  }
}

async function readOptionalJson(path) {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

async function statusSnapshot({ cwd, env }) {
  try {
    const result = await exec("zg", ["status", cwd, "--check-ready"], {
      cwd,
      env,
      timeout: 120000,
    });
    return {
      command: ["zg", "status", cwd, "--check-ready"],
      returncode: 0,
      stdout: result.stdout,
      stderr: result.stderr,
    };
  } catch (error) {
    return {
      command: ["zg", "status", cwd, "--check-ready"],
      returncode: error.code ?? null,
      stdout: error.stdout ?? "",
      stderr: error.stderr ?? "",
      error: safeError(error),
    };
  }
}

export async function runReplay(options) {
  const plan = validatePlan(options.plan);
  const rows = uniqueRequests(plan);
  const logDir = resolve(options.logDir);
  const output = join(logDir, "official-replay.jsonl");
  await mkdir(logDir, { recursive: true });
  try {
    if ((await stat(output)).size > 0)
      throw new Error("Refusing to append to an existing replay experiment");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const install = await readOptionalJson(join(logDir, "install-manifest.json"));
  if (
    !install ||
    install.integration !== "official_zg_install" ||
    install.installation_verified !== true
  )
    throw new Error("Replay requires a verified official zg install manifest");
  const command = install.native_mcp_command;
  if (options.command && stableJson(options.command) !== stableJson(command))
    throw new Error("Cannot replace the installer-produced MCP command");
  if (
    !Array.isArray(command) ||
    !command.length ||
    command.some((value) => typeof value !== "string" || !value)
  ) {
    throw new Error("Invalid native MCP launch command");
  }
  const context = {
    packageDir: options.packageDir ?? "/opt/qa/node_modules/@zvec/zvec-grep",
    command,
    cwd: options.cwd ?? process.cwd(),
    env: options.env ?? process.env,
    logDir,
    timeoutMs: options.timeoutMs ?? 600000,
  };
  const getStatus = options.statusReader ?? statusSnapshot;
  const metadata = {
    schema_version: 1,
    protocol: "official-install-retrieval-replay-v1",
    mode: "retrieval-only-after-e2e",
    not_an_e2e_sample: true,
    paid_model_calls: 0,
    plan_sha256: hash(stableJson(plan)),
    repetitions: 5,
    planned_request_ids: plan.requests.length,
    unique_requests: rows.length,
    planned_observations: rows.length * 5,
    completed_observations: 0,
    status: "starting",
    observation_status_counts: {},
    native_protocol:
      "ordinary agent stdio initialize; SDK negotiation mode legacy",
    native_command: command,
    index_policy:
      plan.index_policy ??
      install?.index_policy ??
      "normal independently prepared index; no cross-run identity requirement",
    index_build: install?.index_build ?? null,
    configured_embedding: install?.embedding ?? null,
    embedding_expected: LOCAL_MODEL,
    install_manifest: install,
    source_requests: rows,
    public_sha256_definition:
      "SHA-256 of canonical JSON for native result.content, preserving content block order; no text stripping",
    full_result_sha256_definition:
      "SHA-256 of canonical JSON for the complete native result",
    index_status_before: await getStatus(context),
    started_at: new Date().toISOString(),
  };
  const metadataPath = join(logDir, "official-replay-metadata.json");
  metadata.embedding_observation = {
    configured: install?.embedding ?? null,
    source: "native zg status stdout before replay",
    expected_model_mentioned:
      metadata.index_status_before.stdout?.includes(LOCAL_MODEL) ?? false,
    status_returncode: metadata.index_status_before.returncode ?? null,
  };
  const saveMetadata = () =>
    writeFile(metadataPath, JSON.stringify(metadata, null, 2) + "\n");
  await saveMetadata();
  let connection;
  try {
    connection = await (options.connect ?? connectNative)(context);
    metadata.native_server = connection.server;
    metadata.native_instructions = connection.instructions;
    metadata.native_catalog = connection.catalog;
    metadata.native_catalog_sha256 = hash(stableJson(connection.catalog));
    const names = connection.catalog.tools.map((tool) => tool.name).sort();
    if (stableJson(names) !== stableJson(["zvec_grep_search"]))
      throw new Error("Expected released native agent search-only catalog");
    if (connection.server?.version !== "0.2.2")
      throw new Error("Native replay must use released zg 0.2.2");
    metadata.status = "running";
    await saveMetadata();
    for (const row of rows) {
      for (let repetition = 1; repetition <= 5; repetition++) {
        const started = performance.now();
        const record = {
          request_id: row.request_id,
          source_request_ids: row.source_request_ids,
          repetition,
          tool_name: row.tool_name ?? null,
          request: structuredClone(row.request),
          mcp_request: fullParams(row),
          request_sha256: hash(stableJson(fullParams(row))),
          status: "unknown",
          result: null,
          text: null,
          public_sha256: null,
          duration_ms: 0,
        };
        if (row.tool_name !== "zvec_grep_search") {
          record.status = "unsupported_tool";
          record.error = {
            message:
              "Original tool is not in the installed agent catalog; retained without substitution",
          };
        } else if (
          !row.request ||
          typeof row.request !== "object" ||
          Array.isArray(row.request)
        ) {
          record.status = "invalid_request";
          record.error = {
            message:
              "Original arguments are not an object; retained without parsing or repair",
          };
        } else {
          try {
            // Do not default root/limit/mode, rename routes, coerce types,
            // remove unknown arguments, or retry protocol/tool errors.
            const result = await connection.request(
              row.tool_name,
              structuredClone(row.request),
              fullParams(row),
            );
            record.result = result;
            record.status = result.isError ? "tool_error" : "completed";
            record.text = Array.isArray(result.content)
              ? result.content
                  .filter((item) => item.type === "text")
                  .map((item) => item.text)
                  .join("\n")
              : null;
            record.public_sha256 = hash(stableJson(result.content ?? null));
            record.full_result_sha256 = hash(stableJson(result));
          } catch (error) {
            record.status = "request_error";
            record.error = safeError(error);
          }
        }
        record.duration_ms = performance.now() - started;
        await appendFile(output, JSON.stringify(record) + "\n");
        metadata.completed_observations++;
        metadata.observation_status_counts[record.status] =
          (metadata.observation_status_counts[record.status] ?? 0) + 1;
        await saveMetadata();
      }
    }
    metadata.status = "completed";
  } catch (error) {
    metadata.status = "failed";
    metadata.error = safeError(error);
    throw error;
  } finally {
    try {
      await connection?.close();
    } catch (error) {
      metadata.teardown_error = safeError(error);
      metadata.status = "failed";
    }
    metadata.index_status_after = await getStatus(context);
    metadata.finished_at = new Date().toISOString();
    await saveMetadata();
  }
  return metadata;
}

async function main() {
  const { values } = parseArgs({
    options: {
      plan: { type: "string" },
      "log-dir": { type: "string", default: "/logs" },
      "package-dir": {
        type: "string",
        default: "/opt/qa/node_modules/@zvec/zvec-grep",
      },
    },
    strict: true,
  });
  if (!values.plan) throw new Error("--plan is required");
  const metadata = await runReplay({
    plan: JSON.parse(await readFile(values.plan, "utf8")),
    logDir: values["log-dir"],
    packageDir: values["package-dir"],
  });
  console.log(
    JSON.stringify({
      status: metadata.status,
      observations: metadata.completed_observations,
      paid_model_calls: 0,
    }),
  );
}
if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
