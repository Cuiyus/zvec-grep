import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import {
  copyFile,
  mkdir,
  readFile,
  readdir,
  stat,
  writeFile,
} from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

const COMMIT = /^[a-f0-9]{40}$/;
const MANIFEST = "manifest.json";
const TARBALL = "candidate.tgz";

async function sha256(path) {
  const contents = await readFile(path);
  return createHash("sha256").update(contents).digest("hex");
}

async function regularFile(path, label) {
  assert.ok((await stat(path)).isFile(), `${label} must be a regular file`);
}

export async function createPackageCache({ source, packagePath, output }) {
  source = resolve(source);
  packagePath = resolve(packagePath);
  output = resolve(output);
  await regularFile(packagePath, "candidate package");
  const { stdout } = await new Promise((resolvePromise, reject) =>
    execFile(
      "git",
      ["-C", source, "rev-parse", "HEAD"],
      { encoding: "utf8" },
      (error, stdout, stderr) =>
        error
          ? reject(Object.assign(error, { stderr }))
          : resolvePromise({ stdout }),
    ),
  );
  const commit = stdout.trim();
  assert.match(commit, COMMIT, "candidate source did not resolve to a commit");
  await mkdir(output, { recursive: true });
  const destination = join(output, TARBALL);
  await copyFile(packagePath, destination);
  const manifest = {
    schema_version: 1,
    candidate_commit: commit,
    source_package: basename(packagePath),
    tarball: TARBALL,
    tarball_sha256: await sha256(destination),
  };
  await writeFile(
    join(output, MANIFEST),
    `${JSON.stringify(manifest, null, 2)}\n`,
  );
  return manifest;
}

export async function verifyPackageCache({ directory, commit }) {
  directory = resolve(directory);
  assert.match(commit, COMMIT, "expected candidate commit must be a full SHA");
  assert.deepEqual((await readdir(directory)).sort(), [TARBALL, MANIFEST]);
  const manifest = JSON.parse(
    await readFile(join(directory, MANIFEST), "utf8"),
  );
  assert.equal(manifest.schema_version, 1);
  assert.equal(
    manifest.candidate_commit,
    commit,
    "candidate_commit does not match the selected source",
  );
  assert.equal(manifest.tarball, TARBALL);
  assert.match(manifest.tarball_sha256, /^[a-f0-9]{64}$/);
  await regularFile(join(directory, TARBALL), "cached candidate package");
  assert.equal(
    await sha256(join(directory, TARBALL)),
    manifest.tarball_sha256,
    "cached package hash does not match its manifest",
  );
  return manifest;
}

async function main(args) {
  const command = args.shift();
  const { values } = parseArgs({
    args,
    options: {
      source: { type: "string" },
      package: { type: "string" },
      output: { type: "string" },
      directory: { type: "string" },
      commit: { type: "string" },
    },
  });
  const result =
    command === "create"
      ? await createPackageCache({
          source: values.source,
          packagePath: values.package,
          output: values.output,
        })
      : command === "verify"
        ? await verifyPackageCache({
            directory: values.directory,
            commit: values.commit,
          })
        : assert.fail(
            "usage: rust-package-cache.mjs <create|verify> [options]",
          );
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  await main(process.argv.slice(2));
}
