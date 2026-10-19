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
async function probeChild(args, env, label) {
  return await new Promise((resolveResult) => {
    const childProcess = spawn(process.execPath, args, {
      cwd: consumer,
      env,
      windowsHide: true,
    });
    let stdout = "",
      stderr = "",
      failure;
    childProcess.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    childProcess.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    const timer = setTimeout(() => {
      failure = {
        code: "ETIMEDOUT",
        message: "Native process exceeded 60 seconds",
      };
      const debuggerPath = join(
        process.env["ProgramFiles(x86)"],
        "Windows Kits",
        "10",
        "Debuggers",
        "x64",
        "cdb.exe",
      );
      const debug = spawnSync(
        debuggerPath,
        ["-p", String(childProcess.pid), "-c", "~* kv; lm; .detach; q"],
        {
          encoding: "utf8",
          timeout: 20000,
          maxBuffer: 8 * 1024 * 1024,
          windowsHide: true,
        },
      );
      writeFileSync(
        join(output, label + ".native-stacks.log"),
        (debug.stdout ?? "") + (debug.stderr ?? ""),
      );
      childProcess.kill();
    }, 60000);
    childProcess.on("error", (error) => {
      failure = error;
    });
    childProcess.on("close", (status, signal) => {
      clearTimeout(timer);
      resolveResult({ stdout, stderr, status, signal, error: failure });
    });
  });
}
const gflags = join(
  process.env["ProgramFiles(x86)"],
  "Windows Kits",
  "10",
  "Debuggers",
  "x64",
  "gflags.exe",
);
if (process.env.ZG_DIAG_PAGEHEAP === "1") {
  const enabled = spawnSync(gflags, ["/p", "/enable", "node.exe", "/full"], {
    encoding: "utf8",
  });
  writeFileSync(
    join(output, "pageheap-enable.log"),
    (enabled.stdout ?? "") + (enabled.stderr ?? ""),
  );
  if (enabled.status !== 0) throw new Error("Cannot enable page heap");
}
const rows = [];
const modes = (process.env.ZG_DIAG_MODES ?? "plain,traced,await-runtime").split(
  ",",
);
if (
  modes.some(
    (m) => !["plain", "traced", "await-runtime", "gc-stress"].includes(m),
  )
) {
  throw new Error("Unknown probe mode");
}
for (let repetition = 1; repetition <= repeats; repetition++) {
  for (const mode of modes) {
    const started = Date.now();
    const result = await probeChild(
      mode === "gc-stress" ? ["--expose-gc", child] : [child],
      { ...process.env, ZG_DIAG_MODE: mode, NO_COLOR: "1" },
      `${mode}-${repetition}`,
    );
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
if (process.env.ZG_DIAG_PAGEHEAP === "1") {
  spawnSync(gflags, ["/p", "/disable", "node.exe"], { encoding: "utf8" });
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
