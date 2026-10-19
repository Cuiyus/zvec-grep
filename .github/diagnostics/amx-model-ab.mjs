import { spawn, spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { cpus, platform, arch, totalmem } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const output = resolve(process.env.ZG_DIAG_OUTPUT);
const repeats = Number(process.env.ZG_DIAG_REPEATS ?? 20);
if (!Number.isInteger(repeats) || repeats < 1 || repeats > 100) {
  throw new Error("repeats must be 1..100");
}
mkdirSync(output, { recursive: true });
const consumer = join(output, "consumer");
const pack = join(output, "pack");
mkdirSync(consumer, { recursive: true });
mkdirSync(pack, { recursive: true });
writeFileSync(
  join(consumer, "package.json"),
  JSON.stringify({ private: true, type: "module" }),
);
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
function install(args, cwd) {
  const result = spawnSync(npm, args, {
    cwd,
    encoding: "utf8",
    shell: process.platform === "win32",
    timeout: 600000,
    maxBuffer: 16 * 1024 * 1024,
    env: { ...process.env, npm_config_registry: "https://registry.npmjs.org/" },
  });
  writeFileSync(
    join(output, args[0] + ".log"),
    (result.stdout ?? "") + (result.stderr ?? ""),
  );
  if (result.status !== 0)
    throw new Error(`npm ${args[0]} failed: ${result.stderr}`);
  return result.stdout;
}
const packed = install(
  ["pack", "--ignore-scripts", "--json", "--pack-destination", pack],
  resolve("."),
);
const [{ filename }] = JSON.parse(packed);
install(["install", "--no-audit", "--no-fund", join(pack, filename)], consumer);
const child = join(consumer, "embedding-lifecycle-child.mjs");
copyFileSync(
  join(
    dirname(fileURLToPath(import.meta.url)),
    "embedding-lifecycle-child.mjs",
  ),
  child,
);
const runtime = JSON.parse(
  readFileSync(join(consumer, "node_modules/node-llama-cpp/package.json")),
);
writeFileSync(
  join(output, "environment.json"),
  JSON.stringify(
    {
      node: process.version,
      platform: platform(),
      arch: arch(),
      cpus: cpus(),
      memory: totalmem(),
      nativeRuntime: runtime.version,
      source: process.env.GITHUB_SHA,
    },
    null,
    2,
  ),
);
if (
  process.env.ZG_DIAG_EXPECTED_RUNTIME &&
  runtime.version !== process.env.ZG_DIAG_EXPECTED_RUNTIME
) {
  throw new Error("Unexpected native runtime: " + runtime.version);
}

const { readdirSync } = await import("node:fs");
const runtimeBins = join(
  consumer,
  "node_modules/@node-llama-cpp/win-x64/bins/win-x64",
);
const dll = join(runtimeBins, "ggml-cpu-sapphirerapids.dll");
const originalDll = join(output, "prebuilt-original-sapphirerapids.dll");
copyFileSync(dll, originalDll);
const childArgs = [child];
const rows = [];
const debug = join(
  process.env["ProgramFiles(x86)"],
  "Windows Kits/10/Debuggers/x64/cdb.exe",
);
function probe(phase, repetition, mode = "traced", debuggerLaunch = false) {
  const name = `${phase}-${mode}-${repetition}`;
  const started = Date.now();
  const command = debuggerLaunch ? debug : process.execPath;
  const args = debuggerLaunch
    ? [
        "-c",
        'sxe -c ".exr -1; .ecxr; kv; lm; q" 0xc0000374; g',
        process.execPath,
        child,
      ]
    : mode === "gc-stress"
      ? ["--expose-gc", child]
      : childArgs;
  const result = spawnSync(command, args, {
    cwd: consumer,
    encoding: "utf8",
    timeout: 120000,
    maxBuffer: 16 * 1024 * 1024,
    windowsHide: true,
    env: { ...process.env, ZG_DIAG_MODE: mode, NO_COLOR: "1" },
  });
  writeFileSync(join(output, name + ".stdout.log"), result.stdout ?? "");
  writeFileSync(join(output, name + ".stderr.log"), result.stderr ?? "");
  const row = {
    phase,
    mode,
    repetition,
    debuggerLaunch,
    status: result.status,
    signal: result.signal,
    error: result.error?.message,
    elapsedMs: Date.now() - started,
    vectorReported: (result.stdout ?? "").includes("ZVEC_GREP_SMOKE_RESULT="),
  };
  rows.push(row);
  writeFileSync(join(output, "ab-results.json"), JSON.stringify(rows, null, 2));
  console.log(JSON.stringify(row));
}
// Control A: the exact published 3.18.1 native binary.
for (let i = 1; i <= 5; i++) probe("prebuilt-original", i);
// Capture one native crash under the debugger, without changing the published DLL.
probe("prebuilt-original", 1, "traced", true);
// Control B: same llama.cpp b8390 rebuilt, retaining the faulty callback.
copyFileSync(
  join(process.env.AMX_DLL_ARTIFACT, "original/ggml-cpu-sapphirerapids.dll"),
  dll,
);
for (let i = 1; i <= 5; i++) probe("rebuilt-original", i);
probe("rebuilt-original", 1, "traced", true);
// Treatment: replace only the same CPU backend with the allocator-pair correction.
copyFileSync(
  join(process.env.AMX_DLL_ARTIFACT, "patched/ggml-cpu-sapphirerapids.dll"),
  dll,
);
for (let i = 1; i <= 10; i++) {
  probe("patched", i);
  probe("patched", i, "gc-stress");
}
const summary = {};
for (const phase of ["prebuilt-original", "rebuilt-original", "patched"]) {
  const samples = rows.filter((r) => r.phase === phase && !r.debuggerLaunch);
  summary[phase] = {
    count: samples.length,
    failed: samples.filter((r) => r.status !== 0 || !r.vectorReported).length,
  };
}
writeFileSync(
  join(output, "ab-summary.json"),
  JSON.stringify(summary, null, 2),
);
console.log(JSON.stringify(summary));
if (
  summary["prebuilt-original"].failed !== 5 ||
  summary["rebuilt-original"].failed !== 5 ||
  summary.patched.failed !== 0
) {
  throw new Error(
    "AMX model negative/positive controls did not match the expected result",
  );
}

// Verify the published zg CLI against the same patched native backend.
const workspace = join(output, "cli-workspace");
const isolatedHome = join(output, "cli-home");
mkdirSync(workspace, { recursive: true });
mkdirSync(isolatedHome, { recursive: true });
writeFileSync(
  join(workspace, "fixture.ts"),
  'export function CrossPlatformPotionNeedle(token: string): boolean { return token.startsWith("potion-"); }\n',
);
const cli = join(consumer, "node_modules/@zvec/zvec-grep/dist/cli/index.js");
const cliEnv = {
  ...process.env,
  HOME: isolatedHome,
  USERPROFILE: isolatedHome,
  NO_COLOR: "1",
  ZVEC_GREP_HOME: isolatedHome,
  ZVEC_GREP_EMBEDDING: "local/embeddinggemma-300m",
  ZVEC_GREP_DEVICE: "cpu",
};
const cliChecks = [];
for (const [name, args, expected] of [
  [
    "vector-query",
    [
      "--mode",
      "direct",
      "--vector",
      "CrossPlatformPotionNeedle",
      "--limit",
      "5",
      "-g",
      "fixture.ts",
      "-t",
      "ts",
      ".",
    ],
    /CrossPlatformPotionNeedle/,
  ],
  [
    "status",
    ["--status", "--mode", "direct", "."],
    /local\/embeddinggemma-300m/,
  ],
]) {
  const result = spawnSync(process.execPath, [cli, ...args], {
    cwd: workspace,
    env: cliEnv,
    encoding: "utf8",
    timeout: 180000,
    maxBuffer: 16 * 1024 * 1024,
  });
  writeFileSync(join(output, `cli-${name}.stdout.log`), result.stdout ?? "");
  writeFileSync(join(output, `cli-${name}.stderr.log`), result.stderr ?? "");
  const ok = result.status === 0 && expected.test(result.stdout ?? "");
  cliChecks.push({ name, status: result.status, passed: ok });
}
writeFileSync(
  join(output, "cli-results.json"),
  JSON.stringify(cliChecks, null, 2),
);
if (cliChecks.some((r) => !r.passed))
  throw new Error("Patched native backend CLI check failed");
