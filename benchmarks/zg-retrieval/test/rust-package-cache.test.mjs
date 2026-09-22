import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import {
  createPackageCache,
  verifyPackageCache,
} from "../../shared/rust-package-cache.mjs";

const exec = promisify(execFile);

test("the Rust package cache is bound to the exact source commit and tarball bytes", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "zg-rust-package-cache-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await exec("git", ["init", "--quiet", root]);
  await exec("git", ["-C", root, "config", "user.email", "cache@test.invalid"]);
  await exec("git", ["-C", root, "config", "user.name", "cache test"]);
  await writeFile(join(root, "source"), "source\n");
  await exec("git", ["-C", root, "add", "source"]);
  await exec("git", ["-C", root, "commit", "--quiet", "-m", "source"]);
  const { stdout } = await exec("git", ["-C", root, "rev-parse", "HEAD"]);
  const commit = stdout.trim();
  const packagePath = join(root, "candidate-input.tgz");
  const output = join(root, "cache");
  await writeFile(packagePath, "native package bytes");
  const manifest = await createPackageCache({
    source: root,
    packagePath,
    output,
  });
  assert.equal(manifest.candidate_commit, commit);
  assert.deepEqual(
    await verifyPackageCache({ directory: output, commit }),
    manifest,
  );
  assert.equal(
    await readFile(join(output, "candidate.tgz"), "utf8"),
    "native package bytes",
  );
  await assert.rejects(
    verifyPackageCache({ directory: output, commit: "0".repeat(40) }),
    /candidate_commit/,
  );
  await writeFile(join(output, "candidate.tgz"), "changed");
  await assert.rejects(
    verifyPackageCache({ directory: output, commit }),
    /cached package hash/,
  );
});
