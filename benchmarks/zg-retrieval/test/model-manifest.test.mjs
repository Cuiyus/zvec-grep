import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { directoryManifest, modelArtifactManifest } from "../core/corpus.mjs";

const marker = `.zvec-grep-artifacts-${"a".repeat(24)}.complete`;

async function cache(t) {
  const root = await mkdtemp(join(tmpdir(), "zg-model-manifest-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

test("model identity ignores completion-marker timestamps while the full directory identity retains them", async (t) => {
  const root = await cache(t);
  const snapshot = join(root, "model2vec", "model", "revision");
  await mkdir(snapshot, { recursive: true });
  await writeFile(join(snapshot, "model.safetensors"), "identical weights");
  const before = await modelArtifactManifest(root);
  await writeFile(
    join(snapshot, marker),
    JSON.stringify({ mtimeMs: 1, ctimeMs: 2 }),
  );
  assert.deepEqual(await modelArtifactManifest(root), before);
  const fullBefore = await directoryManifest(root);
  assert.equal(fullBefore.entries.length, 2);
  assert.ok(fullBefore.entries.some((entry) => entry.path.endsWith(marker)));

  await writeFile(
    join(snapshot, marker),
    JSON.stringify({ mtimeMs: 3, ctimeMs: 4 }),
  );
  await utimes(join(snapshot, marker), 100, 200);
  assert.deepEqual(await modelArtifactManifest(root), before);
  assert.notEqual((await directoryManifest(root)).sha256, fullBefore.sha256);
  await rm(join(snapshot, marker));
  assert.deepEqual(await modelArtifactManifest(root), before);
});

test("model weights, tokenizer, configuration and unrelated hidden files remain part of model identity", async (t) => {
  const root = await cache(t);
  const names = [
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    ".model-settings",
  ];
  for (const name of names) await writeFile(join(root, name), "original");
  const before = await modelArtifactManifest(root);
  assert.equal(before.entries.length, names.length);
  for (const name of names) {
    await writeFile(join(root, name), "modified");
    assert.notEqual(
      (await modelArtifactManifest(root)).sha256,
      before.sha256,
      name,
    );
    await writeFile(join(root, name), "original");
    assert.deepEqual(await modelArtifactManifest(root), before);
  }
});

test("only exact lowercase 24-hex completion filenames are excluded, never lookalikes or directories", async (t) => {
  const root = await cache(t);
  const names = [
    `.zvec-grep-artifacts-${"a".repeat(23)}.complete`,
    `.zvec-grep-artifacts-${"a".repeat(25)}.complete`,
    `.zvec-grep-artifacts-${"A".repeat(24)}.complete`,
    `.zvec-grep-artifacts-${"g".repeat(24)}.complete`,
    `${marker}.bak`,
    `.zvec-grep-artifacts-${"a".repeat(24)}.lock`,
  ];
  for (const name of names) await writeFile(join(root, name), "retained");
  const nested = join(root, `.zvec-grep-artifacts-${"b".repeat(24)}.complete`);
  await mkdir(nested);
  await writeFile(join(nested, "weights"), "retained directory contents");
  const model = await modelArtifactManifest(root);
  assert.equal(model.entries.length, names.length + 1);
  assert.deepEqual(model, await directoryManifest(root));
  for (const name of names) {
    await writeFile(join(root, name), "changed");
    assert.notEqual(
      (await modelArtifactManifest(root)).sha256,
      model.sha256,
      name,
    );
    await writeFile(join(root, name), "retained");
  }
});
