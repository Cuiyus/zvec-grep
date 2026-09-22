import assert from "node:assert/strict";
import { mkdir, lstat, realpath, readlink, readdir } from "node:fs/promises";
import { join, relative } from "node:path";
import { run, fileHash, objectHash } from "./io.mjs";

export const repositorySlug = (repository) => repository.replaceAll("/", "__");

export async function prepareCorpus(repo, corpusDirectory) {
  const root = join(corpusDirectory, repositorySlug(repo.repository));
  await mkdir(root, { recursive: true });
  try {
    await lstat(join(root, ".git"));
  } catch {
    await run("git", ["init", "--quiet", root]);
    await run("git", ["-C", root, "remote", "add", "origin", repo.url]);
    await run("git", [
      "-C",
      root,
      "-c",
      "core.hooksPath=/dev/null",
      "fetch",
      "--depth=1",
      "origin",
      repo.commit,
    ]);
    await run("git", [
      "-C",
      root,
      "-c",
      "core.hooksPath=/dev/null",
      "checkout",
      "--detach",
      "FETCH_HEAD",
    ]);
  }
  assert.equal(
    (await run("git", ["-C", root, "rev-parse", "HEAD"])).stdout.trim(),
    repo.commit,
    `corpus commit mismatch: ${root}`,
  );
  const dirty = await run("git", [
    "-C",
    root,
    "status",
    "--porcelain",
    "--untracked-files=all",
    "--",
    ".",
    ":(exclude).zvec-grep",
  ]);
  assert.equal(dirty.stdout, "", `corpus must be pristine: ${root}`);
  return await realpath(root);
}

export async function corpusManifest(root) {
  const tracked = (await run("git", ["-C", root, "ls-files", "-z"])).stdout
    .split("\0")
    .filter(Boolean)
    .sort();
  const entries = [];
  for (const path of tracked) {
    const full = join(root, path),
      info = await lstat(full);
    if (info.isSymbolicLink())
      entries.push({ path, kind: "symlink", target: await readlink(full) });
    else if (info.isFile())
      entries.push({
        path,
        kind: "file",
        size: info.size,
        sha256: await fileHash(full),
      });
    else entries.push({ path, kind: "unmaterialized_submodule" });
  }
  return { sha256: objectHash(entries), entries };
}

export async function directoryManifest(root) {
  return await filteredDirectoryManifest(root, () => true);
}

/** Exclude only the upstream downloader's timestamp-bearing cache completion files. */
export async function modelArtifactManifest(root) {
  // artifact-downloader.ts uses sha256.digest("hex").slice(0, 24).
  return await filteredDirectoryManifest(
    root,
    (name) => !/^\.zvec-grep-artifacts-[a-f0-9]{24}\.complete$/.test(name),
  );
}

async function filteredDirectoryManifest(root, includeFile) {
  const entries = [];
  async function visit(directory) {
    for (const entry of (
      await readdir(directory, { withFileTypes: true })
    ).sort((a, b) => a.name.localeCompare(b.name))) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) await visit(path);
      else if (entry.isFile()) {
        if (includeFile(entry.name))
          entries.push({
            path: relative(root, path).split("\\").join("/"),
            sha256: await fileHash(path),
          });
      } else throw new Error(`unsupported model artifact: ${path}`);
    }
  }
  await visit(root);
  return { sha256: objectHash(entries), entries };
}
