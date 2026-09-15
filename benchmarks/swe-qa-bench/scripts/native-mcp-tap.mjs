#!/usr/bin/env node
/** Observe released zg stdio unchanged; optionally replace tools/list descriptions.
 * No tool filtering, argument changes, retries, routing, or result formatting.
 */
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  appendFileSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";

const args = process.argv.slice(2);
const delimiter = args.indexOf("--");
if (
  delimiter < 0 ||
  args[0] !== "--log-dir" ||
  !args[1] ||
  !args[delimiter + 1]
) {
  throw new Error(
    "Usage: native-mcp-tap.mjs --log-dir DIR [--descriptions FILE] -- COMMAND [ARGS]",
  );
}
const root = args[1];
mkdirSync(root, { recursive: true });
const descriptionFlag = args.indexOf("--descriptions");
const overrides =
  descriptionFlag < 0
    ? {}
    : JSON.parse(readFileSync(args[descriptionFlag + 1], "utf8"));
if (
  Object.entries(overrides).some(
    ([key, value]) =>
      key !== "zvec_grep_search" || typeof value !== "string" || !value.trim(),
  )
) {
  throw new Error("Only nonempty native tool descriptions may be overridden");
}
const secrets = [
  "OPENAI_API_KEY",
  "GLM_API_KEY",
  "QWEN_API_KEY",
  "QODER_PERSONAL_ACCESS_TOKEN",
]
  .map((key) => process.env[key])
  .filter(Boolean);
const scrub = (value) =>
  secrets.reduce(
    (text, secret) => text.split(secret).join("[REDACTED]"),
    value,
  );
const hash = (value) => createHash("sha256").update(value).digest("hex");
const pending = new Map();
const idKey = (id) => `${typeof id}:${String(id)}`;
const child = spawn(args[delimiter + 1], args.slice(delimiter + 2), {
  stdio: ["pipe", "pipe", "pipe"],
});

function observe(line, direction) {
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    appendFileSync(
      join(root, "native-mcp.jsonl"),
      JSON.stringify({
        direction,
        timestamp: Date.now() / 1000,
        raw_sha256: hash(line),
        invalid_json: true,
        raw: scrub(line.toString("utf8")),
      }) + "\n",
    );
    return line;
  }
  if (
    direction === "agent_to_zg" &&
    message.method &&
    Object.hasOwn(message, "id")
  ) {
    pending.set(idKey(message.id), message.method);
  }
  let effective = line;
  let effectiveMessage;
  if (
    direction === "zg_to_agent" &&
    Object.hasOwn(message, "id") &&
    !message.method
  ) {
    const method = pending.get(idKey(message.id));
    pending.delete(idKey(message.id));
    if (method === "tools/list" && Array.isArray(message.result?.tools)) {
      writeFileSync(
        join(root, "native-mcp-catalog.json"),
        scrub(JSON.stringify(message.result, null, 2)) + "\n",
      );
      const found = new Set(message.result.tools.map((tool) => tool.name));
      if (Object.keys(overrides).some((name) => !found.has(name))) {
        throw new Error(
          "Description override refers to a tool absent from released native catalog",
        );
      }
      if (Object.keys(overrides).length) {
        effectiveMessage = structuredClone(message);
        for (const tool of effectiveMessage.result.tools) {
          if (Object.hasOwn(overrides, tool.name))
            tool.description = overrides[tool.name];
        }
        effective = Buffer.from(JSON.stringify(effectiveMessage) + "\n");
      }
      writeFileSync(
        join(root, "effective-mcp-catalog.json"),
        scrub(JSON.stringify((effectiveMessage ?? message).result, null, 2)) +
          "\n",
      );
    }
  }
  const record = {
    direction,
    timestamp: Date.now() / 1000,
    raw_sha256: hash(line),
    message,
    ...(effectiveMessage
      ? {
          effective_message: effectiveMessage,
          effective_sha256: hash(effective),
        }
      : {}),
  };
  appendFileSync(
    join(root, "native-mcp.jsonl"),
    scrub(JSON.stringify(record)) + "\n",
  );
  return effective;
}

function relay(stream, destination, direction) {
  let pendingBytes = Buffer.alloc(0);
  stream.on("data", (chunk) => {
    pendingBytes = Buffer.concat([pendingBytes, chunk]);
    let newline;
    while ((newline = pendingBytes.indexOf(10)) >= 0) {
      const line = pendingBytes.subarray(0, newline + 1);
      pendingBytes = pendingBytes.subarray(newline + 1);
      destination.write(observe(line, direction));
    }
  });
  stream.on("end", () => {
    if (pendingBytes.length)
      destination.write(observe(pendingBytes, direction));
    if (destination !== process.stdout) destination.end();
  });
}
relay(process.stdin, child.stdin, "agent_to_zg");
relay(child.stdout, process.stdout, "zg_to_agent");
child.stderr.on("data", (chunk) => {
  appendFileSync(
    join(root, "native-mcp.stderr.txt"),
    scrub(chunk.toString("utf8")),
  );
  process.stderr.write(chunk);
});
child.on("error", (error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 127;
});
child.on("close", (code) => {
  process.exitCode = code ?? 1;
  process.stdin.destroy();
});
for (const name of ["SIGTERM", "SIGINT"])
  process.on(name, () => child.kill(name));
