import { writeSync } from "node:fs";
import { createEmbeddingModel } from "@zvec/zvec-grep";

const mode = process.env.ZG_DIAG_MODE;
const traced = mode !== "plain";
function trace(stage, data = {}) {
  if (traced) {
    writeSync(
      2,
      JSON.stringify({ stage, at: new Date().toISOString(), ...data }) + "\n",
    );
  }
}
if (traced) {
  process.on("beforeExit", (code) => trace("beforeExit", { code }));
  process.on("exit", (code) => trace("exit", { code }));
}

const model = createEmbeddingModel("local/embeddinggemma-300m", {
  modelCacheDir: process.env.ZVEC_GREP_MODEL_CACHE,
  device: "cpu",
});
let runtimeDisposal;
function traceDispose(resource, name) {
  if (!resource?.dispose) return;
  const dispose = resource.dispose;
  resource.dispose = function (...args) {
    trace(name + ":start");
    const result = Promise.resolve(dispose.apply(this, args)).then((value) => {
      trace(name + ":end");
      return value;
    });
    if (name === "llama.dispose") runtimeDisposal = result;
    return result;
  };
}

try {
  trace("embed:start");
  const result = await model.embed(
    [
      {
        kind: "text",
        text: "CrossPlatformPotionNeedle validates package-local embedding.",
      },
    ],
    { purpose: "query" },
  );
  const vector = result.vectors[0];
  if (
    !vector ||
    vector.length !== model.info.dimension ||
    vector.some((v) => !Number.isFinite(v))
  ) {
    throw new Error("invalid embedding vector");
  }
  const norm = Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0));
  if (!(norm > 0 && Number.isFinite(norm)))
    throw new Error("invalid embedding norm");
  console.log(
    "ZVEC_GREP_SMOKE_RESULT=" +
      JSON.stringify({ dimension: vector.length, norm }),
  );
  trace("embed:end", {
    modelPath: model.resolvedModelPath,
    gpu: model.llama?.gpu,
    mathCores: model.llama?.cpuMathCores,
    contexts: model.contexts?.length,
    contextThreads: model.contexts?.[0]?._llamaContext?.threads,
  });
} finally {
  if (traced) {
    for (const [i, context] of (model.contexts ?? []).entries()) {
      traceDispose(context, `context[${i}].dispose`);
    }
    traceDispose(model.model, "model.dispose");
    traceDispose(model.llama, "llama.dispose");
  }
  let gcTimer;
  if (mode === "gc-stress") {
    if (typeof globalThis.gc !== "function")
      throw new Error("GC stress requires --expose-gc");
    gcTimer = setInterval(() => globalThis.gc(), 1);
  }
  trace("zg.dispose:start");
  try {
    const disposing = model.dispose();
    if (mode === "gc-stress") globalThis.gc();
    await disposing;
    trace("zg.dispose:end");
  } finally {
    clearInterval(gcTimer);
  }
  if (mode === "await-runtime" && runtimeDisposal) {
    await runtimeDisposal;
    trace("runtime:awaited");
  }
}
