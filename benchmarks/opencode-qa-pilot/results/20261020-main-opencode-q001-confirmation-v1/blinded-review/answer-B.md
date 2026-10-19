## Root cause (one line)

`SegmentImpl::recover()` replays the WAL through a **local** handle and never assigns the member `wal_file_`; `SegmentImpl::flush()` uses `wal_file_ == nullptr` as its "nothing to persist" predicate (`src/db/index/segment/segment.cc:2220-2222`). So the first flush after a pure recovery is a silent no-op, while `switch_to_new_segment_for_writing()` still strips the writing-block meta and commits a new manifest.

---

## 1. State that differs, and why the data is initially readable

**Ordinary insert path** — `SegmentImpl::Insert` (`segment.cc:903-918`) → `append_wal` (`4324-4340`) → `open_wal_file` (`4303-4322`), which is the *only* place `wal_file_` is assigned (`4314`) → `internal_insert` (`837-881`) fills `memory_store_`, `memory_vector_indexers_`, `doc_ids_`, and bumps the mem-block meta (`874-878`). The segment ends up with in-memory data **and** a live WAL handle whose `has_record()` is true (`src/db/index/storage/wal/local_wal_file.h:75-77`).

**Recovery path** — `CollectionImpl::recovery()` (`src/db/collection.cc:1836-1892`) opens persisted segments read-only (`1863-1874`) and the writing segment (`1880-1887`). `SegmentImpl::Open` (`segment.cc:461-517`) loads persisted blocks, then calls `recover()` only if the meta has a writing forward block (`484-501`). `recover()` (`4193-4301`):

- opens the WAL into a **local** `recover_wal_file` and closes it on scope exit (`4206-4215`);
- replays each record through the same `internal_insert/update/upsert/delete` (`4250-4272`);
- bumps `mem_block.max_doc_id_` (`4285-4288`);
- **never touches `wal_file_`** — `open_wal_file()` is reachable only from `append_wal()`, i.e. only from a real write.

So the *visible* state is identical (memory store, memory vector indexers, `doc_ids_`, idmap, mem-block meta) and the only difference is `wal_file_ == nullptr`. Reads work because they consult exactly those in-memory structures: `doc_count()` = `doc_ids_.size()` (`589-600`); `fetch_normal` falls back to `memory_store_` while the meta still has a writing block (`2483-2498`, `2531-2532`); vector fetch uses `memory_vector_indexers_` under the same guard (`1468-1499`); `scan()` appends memory rows (`2825-2841`). The WAL file also still exists at `<coll>/<seg_id>/<block_id>.wal` (`src/db/common/file_helper.h:74-78`) — the data is present twice, but only in RAM plus an unreferenced file.

---

## 2. Call sequence and the exact condition

`CollectionImpl::Optimize` (`collection.cc:779-915`):

1. `writing_segment_->doc_count() != 0` is true after recovery → `switch_to_new_segment_for_writing()` (`794-800`).
2. `switch_to_new_segment_for_writing` (`1517-1552`) calls `writing_segment_->dump()` (`1519`) → `flush()` (`segment.cc:2197-2215`).
3. **Exact condition:** `if (wal_file_ == nullptr || !wal_file_->has_record()) return Status::OK();` (`segment.cc:2220-2222`). It returns OK *before* any of: `memory_store_->flush()` (`2233-2236`), `finish_memory_components()` (`2288-2291` → `4342-4412`, the step that converts the mem block into a persisted `BlockMeta` and moves memory indexers into `vector_indexers_`), new-writing-block allocation (`2293-2298`), `update_version()` (`2300-2302` → `4414-4426`), `id_map_->flush()` (`2266-2269`), and WAL removal (`2304-2315`).
4. `dump()` still seals scalar indexers, dumps FTS, sets `sealed_ = true`, returns OK (`2204-2214`).
5. Back in `switch_to_new_segment_for_writing`: old segment registered as persist segment (`1522`), new empty writing segment created (`1527-1534`), then **`writing_segment_meta->remove_writing_forward_block()`** (`1538`) erases the only meta record of the recovered rows, and `add_persisted_segment_meta` + `apply` + `flush` (`1539-1549`) commit it. `VersionManager::flush()` writes `manifest.<n+1>` and **deletes `manifest.<n>`** (`src/db/index/common/version_manager.cc:282-301`) — the manifest that still described the writing block (and thus enabled WAL replay) is gone. This is the point of no return, and it happens *before* any compaction.
6. Optimize continues: `get_all_persist_segments()` (`802-803`) → `build_compact_task` (`917-1005`), which plans using the **in-memory** `doc_count()` (`928-929`, `944-945`), so the segment looks non-empty although its persisted meta now has zero blocks.
   - *Small-collection case (one persist segment, no rebuild):* tail branch builds a `CreateVectorIndexTask` (`991-1001`) → `create_all_vector_index` (`segment.cc:1617-1640`) → `create_vector_index` (`1670-1730`) → `merge_vector_indexer` over `vector_indexers_[column]` (`1652-1653`), which is **empty** because `finish_memory_components()` never ran; `VectorColumnIndexer::Merge` returns OK on empty input (`src/db/index/column/vector_column/vector_column_indexer.cc:111-113`). A VECTOR_INDEX block meta is then added claiming `doc_ids_.front()/back()/size()` docs (`1720-1727`) — N docs "indexed" by an empty index. Optimize updates the meta (`865-867`), applies + flushes the version (`872-877`), calls `reload_vector_index` (`905-909` → `segment.cc:1882-1940`, which swaps `segment_meta_`), and returns OK (`914`).
   - *≥2 persist segments or `rebuild` (`931-935`):* `CompactTask` → `ReduceScalar` → `segment->scan()` (`src/db/index/segment/segment_helper.cc:232-236`), and `scan()` dereferences `writing_forward_block().value()` unguarded (`segment.cc:2837`) → `std::bad_optional_access`. If it produced 0 rows it deletes the output and leaves `output_segment_meta_` null (`segment_helper.cc:138-141`), yet Optimize still removes all input metas (`collection.cc:859-862`) and destroys the input segments (`898-901`) → `destroy_segment` (`segment_manager.cc:45-56`) → `~SegmentImpl` → `cleanup()` → `RemoveDirectory(seg_path)` (`segment.cc:112-117`, `2334-2338`), deleting the orphaned WAL as well. *(Source-read prediction; not executed.)*

**On the next open:** the segment is loaded read-only (`collection.cc:1863-1874`), `SegmentImpl::Open` takes the `else` branch (`segment.cc:502-510`) because there is no writing block, so `recover()` is never called and the WAL is never replayed; `load_persist_scalar_blocks` (`3913-3958`) finds no SCALAR block, so `doc_ids_` stays empty. The WAL bytes may still sit in `<seg_id>/<block_id>.wal`, unreachable.

**Control case:** without the crash, `Insert` had set `wal_file_`, so the same `dump()`→`flush()` executes the full body — forward data written, blocks registered, version flushed, and only then the WAL removed (`2304-2315`). Optimize merges genuinely persisted blocks; nothing is lost.

---

## 3. Why "OK" and healthy stats prove nothing

- The early return is `Status::OK()` (`2220-2222`), indistinguishable from "everything persisted"; `dump()` and `Optimize()` propagate it. Nothing asserts that the writing block was actually consumed before `remove_writing_forward_block()` + manifest commit.
- `Stats()` (`collection.cc:403-442`) sums `segment->doc_count(filter)` = `doc_ids_.size()` (`segment.cc:589-600`), a pure RAM counter untouched by the meta strip, and computes `index_completeness` from `meta()->vector_indexed(field)` (`433`) — a flag step 6 just set on the strength of an empty merged index. Both numbers come from structures the durability path never consulted.
- In the same handle the data is *already* unreachable while stats look fine: scalar fetch falls back to `memory_store_` only if `has_writing_forward_block()` (`2484`), vector fetch likewise (`1469`). After `collection.cc:1538` / `segment.cc:1889` those are false, so fetch fails with "Segment doc ID … not found" (`2500-2502`) while `doc_count` still reports N.
- Durability is a property of manifest + block files (what `recovery()` reads), not of the handle; only a fresh process observes it. Additionally, `id_map_` is rocksdb with `write_opts_.disableWAL = true` (`src/db/common/rocksdb_context.cc:93`) and its `flush()` is skipped by the same early return (`segment.cc:2266-2269`); it reaches disk only via `~IDMap` → `close()` → `flush_unlocked()` (`src/db/index/common/id_map.h:35-37`, `rocksdb_context.cc:318`, `339-360`). A graceful exit can therefore leave pk→doc_id entries durable while the rows are not: reopen yields `AlreadyExists` on re-insert (`segment.cc:906-909`) and `Segment not found` on fetch (`collection.cc:1388-1392`, `2054-2074`).

---

## 4. Smallest repair principle and regression test (described, not implemented)

**Repair principle:** recovery must leave the segment in the same state as insertion — a replayed-but-unpersisted writing block must be owned by a live WAL handle, so `flush()`'s "nothing to do" predicate is true only when nothing is actually pending.

- (a) Minimal: in `recover()`, adopt the WAL as the member handle after a non-empty replay (assign `wal_file_` instead of a local + `AILEGO_DEFER` close at `4206-4215`, or call `open_wal_file()`, which already handles the existing-file case at `4308-4312`). The first `flush()`/`dump()` then runs the full body and removes the WAL only after the blocks and version are durable.
- (b) Belt-and-braces: make the predicate state-based rather than handle-based — dirty means "writing block `doc_count_ > 0` / `memory_store_` has rows" (`segment.cc:2220`), so no future caller can no-op a flush with pending rows.
- (c) Lifecycle invariant: `switch_to_new_segment_for_writing()` must not strip the writing block and commit a manifest unless `dump()` reports the block was persisted (e.g. return a "consumed" flag, or assert `memory_store_ == nullptr` / writing-block `doc_count_ == 0` after dump, `collection.cc:1519-1549`). This turns silent loss into a loud error.
- Adjacent, same root cause: guard `scan()`'s `writing_forward_block().value()` (`segment.cc:2837`); and `mem_block.max_doc_id_ += added_docs` (`4285-4288`) appears to double-count, since `internal_insert` already set `max_doc_id_` (`874-876`) — it inflates the next segment's `min_doc_id` (`collection.cc:1527-1531`).

**Regression test** — new case in `tests/db/crash_recovery/`, reusing `RunGeneratorAndCrash` (`write_recovery_test.cc:144-148`) and `RunOptimizer` (`optimize_recovery_test.cc:121-127`); the existing `CrashDuringOptimize` misses this because it closes the collection gracefully (flushing the WAL) before optimizing (`optimize_recovery_test.cc:207-223`):

1. Create with `CreateTestSchema` (`utility.h:38-65`), `max_doc_count_per_segment` big enough to avoid rollover; close cleanly.
2. `RunGeneratorAndCrash("0", N, "insert", "0", secs)` — SIGKILL mid-insert so records exist only in the WAL.
3. Reopen, assert `Stats().doc_count == K > 0` and every `pk_0..pk_{K-1}` fetches equal to `CreateTestDoc`; close cleanly (baseline: recovered and readable).
4. `RunOptimizer(dir_path_)` in a separate process; assert exit 0 (the bug only manifests when optimize *succeeds*).
5. Reopen in a fresh process; assert `doc_count == K`, all K docs fetch and compare equal, and a `Query` on `dense_fp32_field` returns expected top-k (as at `optimize_recovery_test.cc:195-205`). Today this fails with 0 docs while step 4 passed — the regression signal.
6. Mechanism-level invariants so the test can't be satisfied by accident:
   - after step 4, in the *same* handle, fetch must still work (catches the `has_writing_forward_block()` strip at `2484`/`1469`);
   - after a successful optimize+close, no `*.wal` may remain for any segment the newest manifest lists as persisted (every WAL is either replayed-then-removed or still referenced by a writing block);
   - per persisted segment meta: SCALAR-block `doc_count` equals the segment's reported `doc_count`, and every VECTOR_INDEX block's `doc_count` is backed by SCALAR rows (catches `segment.cc:1720-1727`);
   - every pk in the idmap resolves via `local_segment_by_doc_id` (catches the idmap-outlives-data case);
   - control arm without the crash in step 2, plus a variant with ≥2 persisted segments to cover the `CompactTask`/`scan()` branch;
   - a segment-level unit test: open a segment whose WAL has records, call `flush()` with no `Insert`, assert the writing block became a persisted block and the WAL file is gone.

---

**Established from source vs. requiring execution.** I read the frozen tree only; I did not build or run anything, and there is no reproduction behind these claims (the tree isn't even a git checkout). Static reading establishes: `wal_file_` is null after recovery, the flush early-return and everything it skips, the meta strip plus manifest commit/previous-manifest deletion, the empty-merge returning OK, the reopen path skipping `recover()`, and the stats/fetch divergence. A run would be needed to confirm: the exact post-reopen `doc_count`, whether the orphaned WAL survives on disk in the single-segment case, whether the idmap truly outlives the forward data given `disableWAL`, whether `MemForwardStore`/`ChunkedFileWriter` left a partial `scalar.<block>.ipc`, the `bad_optional_access` prediction for the multi-segment/rebuild branch, and how many docs a given SIGKILL timing recovers.