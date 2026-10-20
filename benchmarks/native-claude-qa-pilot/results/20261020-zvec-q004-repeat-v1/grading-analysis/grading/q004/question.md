Use the implemented behavior in the supplied source checkout to answer this configuration question.

In a fresh Python process, before any collection or global worker pool is used, an application successfully calls `zvec.init(optimize_threads=4)`. Assume that `std::thread::hardware_concurrency()` reports at least four, worker creation succeeds, and there is no later reconfiguration. Compare two independent collection-optimization invocations:

- A: `collection.optimize(zvec.OptimizeOption(concurrency=0))`
- B: `collection.optimize(zvec.OptimizeOption(concurrency=2))`

For each invocation, assume that the collection has work requiring a vector-index rebuild using a target builder, and that its training/building stage is reached. These are independent scenarios, so B is not a no-op after A. There are no concurrent user operations. This is a source-reading scenario, not a claim about a measured bug or benchmark.

Explain:

1. For a core vector-index merge reached by each invocation, which worker pool and worker count are selected? Does B change the configured global optimization pool?
2. How is the selected pool passed to the target builder's training/building calls and to the optional conversion-training call immediately before them? Focus on that builder-side stage and the meaningful handoffs; listing every wrapper or analyzing earlier per-field preparation is unnecessary. Distinguish passing a pool to a call from proving that every possible implementation consumes it.
3. For DiskANN's product-quantization training specifically, is its outer pool that dispatches work across chunks reused in the same way? Explain how that pool's worker count is determined relative to the caller's pool and the builder's own setting.
4. What can these settings establish about worker-pool capacity, and what can they not establish about the whole process's thread count or actual CPU utilization?

Support the pool-selection, handoff, and DiskANN conclusions with repository-relative source paths and line numbers or ranges from this checkout. Equivalent source locations are acceptable when they establish the claim. Keep the answer within these paths and conditions; no historical patch analysis, other index families, performance measurements, or regression-test plan is required. Do not edit or execute the repository.
