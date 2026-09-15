import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import {
  connectNative,
  runReplay,
  uniqueRequests,
  validatePlan,
} from "./official-replay.mjs";

async function temporary(t) {
  const path = await mkdtemp(join(tmpdir(), "zg-official-replay-test-"));
  t.after(() => rm(path, { recursive: true, force: true }));
  await writeFile(
    join(path, "install-manifest.json"),
    JSON.stringify({
      integration: "official_zg_install",
      installation_verified: true,
      native_mcp_command: ["zg", "server", "--stdio"],
      embedding: "local/potion-code-16m-v2",
    }),
  );
  return path;
}
const catalog = {
  tools: [
    {
      name: "zvec_grep_search",
      description: "native description",
      inputSchema: { type: "object" },
    },
  ],
};
const statusReader = async () => ({
  returncode: 0,
  stdout: "embedding: local/potion-code-16m-v2",
});
const planFor = (requests) => ({ repetitions: 5, requests });
const rowsAt = async (path) =>
  (await readFile(join(path, "official-replay.jsonl"), "utf8"))
    .trim()
    .split("\n")
    .map(JSON.parse);

test("deduplication retains all source IDs and distinguishes complete parameters", () => {
  const rows = uniqueRequests(
    planFor([
      {
        request_id: "a",
        tool_name: "zvec_grep_search",
        request: { root: "/app", query: "concept" },
      },
      {
        request_id: "b",
        tool_name: "zvec_grep_search",
        request: { query: "concept", root: "/app" },
      },
      {
        request_id: "c",
        tool_name: "zvec_grep_search",
        request: { root: "/app", query: "concept", limit: 10 },
      },
    ]),
  );
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0].source_request_ids, ["a", "b"]);
  assert.equal(rows[1].request.limit, 10);
  assert.throws(
    () => validatePlan({ requests: [], repetitions: 3 }),
    /exactly five/,
  );
  assert.throws(
    () =>
      validatePlan(
        planFor([
          { request_id: "a", request: {} },
          { request_id: "a", request: {} },
        ]),
      ),
    /unique/,
  );
  assert.throws(
    () =>
      validatePlan(
        planFor([
          {
            request_id: "mismatch",
            tool_name: "zvec_grep_search",
            request: { query: "a" },
            mcp_request: {
              name: "zvec_grep_search",
              arguments: { query: "b" },
            },
          },
        ]),
      ),
    /disagree/,
  );
  const common = { tool_name: "zvec_grep_search", request: { query: "a" } };
  assert.equal(
    uniqueRequests(
      planFor([
        {
          ...common,
          request_id: "a",
          mcp_request: {
            name: common.tool_name,
            arguments: common.request,
            _meta: { token: "one" },
          },
        },
        {
          ...common,
          request_id: "b",
          mcp_request: {
            name: common.tool_name,
            arguments: common.request,
            _meta: { token: "two" },
          },
        },
      ]),
    ).length,
    2,
  );
});

test("replays unchanged arguments five times and hashes native public content", async (t) => {
  const path = await temporary(t);
  const request = {
    root: "/app",
    query: "concept",
    experimental_unknown: 7,
    queries: ["literal"],
    autoUpdate: true,
  };
  const before = structuredClone(request);
  const received = [];
  let closed = false;
  const metadata = await runReplay({
    logDir: path,
    plan: planFor([
      { request_id: "a", tool_name: "zvec_grep_search", request },
    ]),
    statusReader,
    connect: async () => ({
      catalog,
      server: { name: "zvec-grep", version: "0.2.2" },
      instructions: "native rules",
      close: async () => {
        closed = true;
      },
      request: async (name, args) => {
        received.push({ name, args });
        return {
          content: [{ type: "text", text: "A\nB\n" }],
          _meta: { observation: received.length },
        };
      },
    }),
  });
  assert.equal(metadata.completed_observations, 5);
  assert.equal(metadata.paid_model_calls, 0);
  assert.equal(metadata.native_instructions, "native rules");
  assert.deepEqual(request, before);
  assert.ok(closed);
  assert.equal(received.length, 5);
  for (const row of received)
    assert.deepEqual(row, { name: "zvec_grep_search", args: before });
  const rows = await rowsAt(path);
  assert.ok(
    rows.every((row) => row.status === "completed" && row.text === "A\nB\n"),
  );
  assert.equal(new Set(rows.map((row) => row.public_sha256)).size, 1);
  assert.equal(new Set(rows.map((row) => row.full_result_sha256)).size, 5);
  await assert.rejects(
    () => runReplay({ logDir: path, plan: planFor([]), statusReader }),
    /existing replay/,
  );
});

test("invalid arguments and unsupported tools are retained, never repaired or substituted", async (t) => {
  const path = await temporary(t);
  let called = 0;
  const metadata = await runReplay({
    logDir: path,
    statusReader,
    plan: planFor([
      {
        request_id: "bad-json-string",
        tool_name: "zvec_grep_search",
        request: '{"query":"x"}',
      },
      {
        request_id: "not-native",
        tool_name: "zvec_grep_rg",
        request: { command: "rg x" },
      },
      {
        request_id: "invalid-native-args",
        tool_name: "zvec_grep_search",
        request: { query: 7 },
      },
      {
        request_id: "native-tool-error",
        tool_name: "zvec_grep_search",
        request: {},
      },
    ]),
    connect: async () => ({
      catalog,
      server: { version: "0.2.2" },
      close: async () => {},
      request: async (_name, args) => {
        called++;
        if (args.query === 7)
          throw Object.assign(new Error("Native InvalidParams"), {
            code: -32602,
          });
        return {
          isError: true,
          content: [{ type: "text", text: "Native missing root" }],
        };
      },
    }),
  });
  assert.equal(metadata.completed_observations, 20);
  assert.equal(called, 10);
  const rows = await rowsAt(path);
  assert.equal(
    rows.filter((row) => row.status === "invalid_request").length,
    5,
  );
  assert.equal(
    rows.filter((row) => row.status === "unsupported_tool").length,
    5,
  );
  assert.equal(
    rows.filter(
      (row) => row.status === "request_error" && row.error.code === -32602,
    ).length,
    5,
  );
  assert.equal(rows.filter((row) => row.status === "tool_error").length, 5);
  assert.equal(rows[0].request, '{"query":"x"}');
});

test("wrong server version fails before any replay and saves failure metadata", async (t) => {
  const path = await temporary(t);
  await assert.rejects(
    () =>
      runReplay({
        logDir: path,
        statusReader,
        plan: planFor([
          { request_id: "a", tool_name: "zvec_grep_search", request: {} },
        ]),
        connect: async () => ({
          catalog,
          server: { version: "0.3.0" },
          close: async () => {},
          request: async () => assert.fail("must not call"),
        }),
      }),
    /0.2.2/,
  );
  const metadata = JSON.parse(
    await readFile(join(path, "official-replay-metadata.json"), "utf8"),
  );
  assert.equal(metadata.status, "failed");
  assert.equal(metadata.completed_observations, 0);
});

test("installed SDK sends unchanged tools/call arguments through real stdio", async (t) => {
  let packageDir = process.env.ZG_TEST_SDK_ROOT;
  if (!packageDir) {
    try {
      const require = createRequire(import.meta.url);
      packageDir = dirname(
        dirname(require.resolve("@modelcontextprotocol/client")),
      );
    } catch {
      t.skip("Install runtime SDK dependencies or set ZG_TEST_SDK_ROOT");
      return;
    }
  }
  const path = await temporary(t);
  const serverPath = join(path, "fake-native.mjs");
  await writeFile(
    serverPath,
    `import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const require=createRequire(${JSON.stringify(join(packageDir, "package.json"))});
const load=name=>import(pathToFileURL(require.resolve(name)).href);
const {Server}=await load('@modelcontextprotocol/server');
const {StdioServerTransport}=await load('@modelcontextprotocol/server/stdio');
const server=new Server({name:'test-native',version:'0.2.2'},{capabilities:{tools:{}},instructions:'fixed native instructions'});
server.setRequestHandler('tools/list',async()=>(${JSON.stringify(catalog)}));
server.setRequestHandler('tools/call',async request=>({content:[{type:'text',text:JSON.stringify(request.params)}]}));
await server.connect(new StdioServerTransport());`,
  );
  const connection = await connectNative({
    packageDir,
    command: [process.execPath, serverPath],
    cwd: path,
    env: process.env,
    logDir: path,
    timeoutMs: 5000,
  }).catch(async (error) => {
    error.message +=
      "\nNative test server stderr: " +
      (await readFile(join(path, "official-replay.stderr.txt"), "utf8"));
    throw error;
  });
  try {
    const args = {
      root: "/app",
      queries: ["a", "b"],
      extra: { nested: true },
      limit: "wrong-type",
    };
    const params = {
      name: "zvec_grep_search",
      arguments: args,
      _meta: { custom_trace: "retain-me" },
      future_extra: { yes: true },
    };
    const result = await connection.request("zvec_grep_search", args, params);
    assert.deepEqual(JSON.parse(result.content[0].text), params);
    assert.equal(connection.instructions, "fixed native instructions");
  } finally {
    await connection.close();
  }
});

test("unverified installation and replacement commands cannot be replayed", async (t) => {
  const path = await temporary(t);
  const args = {
    logDir: path,
    plan: planFor([]),
    statusReader,
    connect: async () => {
      throw new Error("must not connect");
    },
  };
  await assert.rejects(
    () => runReplay({ ...args, command: ["node", "custom-bridge.mjs"] }),
    /Cannot replace/,
  );
  await rm(join(path, "install-manifest.json"));
  await assert.rejects(() => runReplay(args), /verified official zg install/);
});
