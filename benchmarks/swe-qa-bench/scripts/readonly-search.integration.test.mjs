import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import {
  createRuntime,
  loadProduction,
  retrievalRequest,
  serve,
} from "./readonly-search.mjs";

const packageDir = process.env.ZG_READONLY_PACKAGE_DIR;

test(
  "published 0.2.2 MCP registration exposes only search and returns production text",
  {
    skip: packageDir
      ? false
      : "Set ZG_READONLY_PACKAGE_DIR to an installed @zvec/zvec-grep@0.2.2 package",
    timeout: 15_000,
  },
  async () => {
    const require = createRequire(join(packageDir, "package.json"));
    const pkg = require("./package.json");
    assert.equal(pkg.version, "0.2.2");
    const [
      { createZvecGrepMcpServer },
      { formatAgentContextResult },
      { InMemoryTransport },
      { Client },
    ] = await Promise.all([
      import(pathToFileURL(join(packageDir, "dist/mcp/tools.js")).href),
      import(
        pathToFileURL(join(packageDir, "dist/cli/format/context.js")).href
      ),
      import(
        pathToFileURL(require.resolve("@modelcontextprotocol/server")).href
      ),
      import(
        pathToFileURL(require.resolve("@modelcontextprotocol/client")).href
      ),
    ]);
    const [serverTransport, clientTransport] =
      InMemoryTransport.createLinkedPair();
    const calls = [];
    const result = {
      root: "/app",
      query: "find evidence",
      source: "index",
      coverage: "ranked_sample",
      items: [],
      diagnostics: { emptyReason: "no_matches" },
    };
    const serving = serve(
      {
        search: async (input, metadata) => {
          calls.push({ input, metadata });
          return { response: { root: "/app", freshness: "fresh", result } };
        },
      },
      {
        packageIdentity: { version: pkg.version },
        createZvecGrepMcpServer,
        StdioServerTransport: class {
          constructor() {
            return serverTransport;
          }
        },
      },
    );
    const client = new Client({
      name: "readonly-integration-test",
      version: "1",
    });
    try {
      await client.connect(clientTransport);
      const listed = await client.listTools();
      assert.deepEqual(
        listed.tools.map((tool) => tool.name),
        ["zvec_grep_search"],
      );
      const searched = await client.callTool({
        name: "zvec_grep_search",
        arguments: { root: "/app", query: "find evidence", autoUpdate: true },
      });
      assert.equal(searched.isError, undefined);
      assert.deepEqual(searched.content, [
        {
          type: "text",
          text: `freshness: fresh\n${formatAgentContextResult(result, { preview: "short" })}`,
        },
      ]);
      assert.equal(searched.structuredContent, undefined);
      assert.equal(calls.length, 1);
      assert.equal(calls[0].metadata.origin, "agent-mcp");
      assert.deepEqual(calls[0].input.queries, ["find evidence"]);
      await assert.rejects(
        client.callTool({
          name: "zvec_grep_index",
          arguments: { root: "/app" },
        }),
      );
    } finally {
      await client.close();
      await serving;
    }
  },
);

test(
  "published 0.2.2 reuses a fixture working copy for five FTS queries with unchanged documents and vectors",
  {
    skip: packageDir
      ? false
      : "Set ZG_READONLY_PACKAGE_DIR to an installed @zvec/zvec-grep@0.2.2 package",
    timeout: 60_000,
  },
  async (t) => {
    const directory = await realpath(
      await mkdtemp(join(tmpdir(), "zg-readonly-native-test-")),
    );
    t.after(() =>
      process.env.ZG_READONLY_KEEP_FIXTURE
        ? t.diagnostic(`Fixture retained: ${directory}`)
        : rm(directory, { recursive: true, force: true }),
    );
    const root = join(directory, "source");
    execFileSync("git", ["init", "--quiet", root]);
    await writeFile(
      join(root, "README.md"),
      "# QA fixture\n\nThe frobnicator cancels all pending requests before closing its transport.\n",
    );
    for (const args of [
      ["add", "README.md"],
      [
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
      ],
    ]) {
      execFileSync("git", ["-C", root, ...args]);
    }
    const production = await loadProduction(packageDir);
    // Fixture construction belongs to this integration test only. The runtime
    // under test receives an existing index and never receives an index method.
    const embeddingModel = {
      info: {
        reference: "test/deterministic",
        provider: "test",
        name: "deterministic",
        dimension: 16,
        metric: "cosine",
        inputKinds: ["text"],
        limits: { maxBatchSize: 128 },
      },
      embed: async (contents) => ({
        vectors: contents.map(() => [1, ...new Array(15).fill(0)]),
        truncated: [],
      }),
      dispose: async () => {},
    };
    const builder = await production.createZvecGrep({ root, embeddingModel });
    try {
      await builder.index({ root });
    } finally {
      await builder.close();
    }
    const options = {
      command: "preflight",
      root,
      snapshot: join(directory, "snapshot.json"),
      log: join(directory, "trace.jsonl"),
      embeddingModel: "test/deterministic",
      workingCopy: true,
    };
    const preflight = await createRuntime(options, production);
    await preflight.close();
    const runtime = await createRuntime(
      { ...options, command: "retrieve" },
      production,
    );
    const visible = [];
    try {
      for (let repetition = 1; repetition <= 5; repetition++) {
        const { event } = await runtime.search(
          retrievalRequest(root, "frobnicator", "fts", 10),
          { origin: "retrieval-only", mode: "fts", repetition, repetitions: 5 },
        );
        assert.equal(event.status, "success");
        assert.ok(event.result.items.length > 0);
        assert.match(event.text, /frobnicator/);
        visible.push(event.text_sha256);
      }
    } finally {
      await runtime.close();
    }
    assert.equal(new Set(visible).size, 1);
    const events = (await readFile(options.log, "utf8"))
      .trim()
      .split("\n")
      .map(JSON.parse);
    const checks = events.filter((event) => event.event === "integrity");
    assert.ok(
      checks.every((event) => event.unchanged && event.semantic_unchanged),
    );
    assert.ok(
      checks.every(
        (event) => typeof event.physical_storage_unchanged === "boolean",
      ),
    );
    assert.equal(events.filter((event) => event.event === "search").length, 5);
  },
);
