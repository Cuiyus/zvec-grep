import { spawnSync } from "node:child_process";
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
const rows = [];
const modes = ["plain", "traced", "await-runtime"];
for (let repetition = 1; repetition <= repeats; repetition++) {
  for (const mode of modes) {
    const started = Date.now();
    const result = spawnSync(process.execPath, [child], {
      cwd: consumer,
      encoding: "utf8",
      timeout: 120000,
      maxBuffer: 4 * 1024 * 1024,
      env: { ...process.env, ZG_DIAG_MODE: mode, NO_COLOR: "1" },
      windowsHide: true,
    });
    const name = `${mode}-${String(repetition).padStart(2, "0")}`;
    const stdout = result.stdout ?? "";
    const stderr = result.stderr ?? "";
    writeFileSync(join(output, name + ".stdout.log"), stdout);
    writeFileSync(join(output, name + ".stderr.log"), stderr);
    const row = {
      mode,
      repetition,
      status: result.status,
      signal: result.signal,
      error: result.error
        ? { code: result.error.code, message: result.error.message }
        : null,
      elapsedMs: Date.now() - started,
      vectorReported: stdout.includes("ZVEC_GREP_SMOKE_RESULT="),
      lastStage: stderr.trim().split(/\r?\n/).at(-1),
    };
    rows.push(row);
    writeFileSync(join(output, "results.json"), JSON.stringify(rows, null, 2));
    console.log(JSON.stringify(row));
  }
}
const summary = Object.fromEntries(
  modes.map((mode) => {
    const values = rows.filter((r) => r.mode === mode);
    return [
      mode,
      {
        count: values.length,
        failed: values.filter((r) => r.status !== 0 || !r.vectorReported)
          .length,
      },
    ];
  }),
);
writeFileSync(join(output, "summary.json"), JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary));
if (rows.some((r) => r.status !== 0 || !r.vectorReported)) process.exitCode = 1;
