## Root cause

`SegmentImpl::recover()` replays the WAL through a **local** file handle and never installs it as the segment's live `wal_file_`. `SegmentImpl::flush()` uses `wal_file_` as its "is there anything dirty?" proxy, so after a recovery the flush is a silent no-op — while `dump()`/`switch_to_new_segment_for_writing()` still commit a version that declares the segment fully persisted.

---

### 1. State that differs, and why the data is initially readable

**Ordinary insert path** — `SegmentImpl::Insert` (`src/db/index/segment/segment.cc:903-918`) calls `append_wal(doc)` (line 914) *before* `internal_insert`. `append_wal` (`segment.cc:4324-4340`) lazily calls `open_wal_file()` (line 4327-4330), which is the **only** place the member `wal_file_` (declared `segment.cc:401`, initialized `nullptr`) is ever assigned (`segment.cc:4314`). So a written-to segment always has `wal_file_ != nullptr`.

**Recovery path** — `CollectionImpl::recovery()` (`src/db/collection.cc:1836-1892`) opens the writing segment at `collection.cc:1880-1887`; `SegmentImpl::Open` calls `recover()` only because `writing_forward_block()` still has a value (`segment.cc:484-501`). `recover()` (`segment.cc:4193-4301`) opens a **local** `recover_wal_file` (`4206-4214`), registers `AILEGO_DEFER([&]{ recover_wal_file->close(); })` (`4215`), and replays each record via `internal_insert/internal_update/internal_upsert/internal_delete` (`4250-4272`). It returns without ever touching `wal_file_`. **Post-recovery invariant is broken: the writing block has `doc_count_ > 0` but `wal_file_ == nullptr`.**

**Why it reads fine anyway** — `internal_insert` (`segment.cc:837-881`) populates everything in-process: `id_map_->upsert` (853), `memory_store_->insert` (857, store lazily built at 847-850 → `init_memory_components`, `segment.cc:4119-4191`), scalar/FTS/vector indexers (861-872), the writing-block counters (874-876), and `doc_ids_.push_back` (878). `MemForwardStore::insert` only appends to an in-memory cache (`src/db/index/storage/memory_forward_store.cc:117-145`) — nothing reaches the block file until `flush()`/`close()` (`memory_forward_store.cc:212`, `305-314`). Reads are served from that heap state: `doc_count()` is `doc_ids_.size()` (`segment.cc:589-600`), fetch falls back to `memory_store_` when `block_index == -1` (`segment.cc:2531-2532`, `2755`), and `get_all_segments()` includes the writing segment whenever `doc_count() > 0` (`collection.cc:2076-2082`).

### 2. Call sequence and the exact condition

`CollectionImpl::Optimize` (`collection.cc:779-915`):

1. `collection.cc:794-800` — `writing_segment_->doc_count() != 0` is true (recovered rows are in `doc_ids_`), so `switch_to_new_segment_for_writing()` runs.
2. `collection.cc:1519` → `SegmentImpl::dump()` (`segment.cc:2197-2215`) → `flush()` at `segment.cc:2201`.
3. **The exact condition — `segment.cc:2220-2222`:**
   ```cpp
   if (wal_file_ == nullptr || !wal_file_->has_record()) {
     return Status::OK();
   }
   ```
   `wal_file_` is `nullptr`, so `flush()` returns OK having done **nothing**. It skips `memory_store_->flush()` (2233-2236), `finish_memory_components()` (2288-2298 → `segment.cc:4342-4412`, the only caller of `segment_meta_->add_persisted_block(...)` at 4368/4383/4401), `update_version()` (2301 → `segment.cc:4414-4426`), and the WAL removal (2304-2315).
4. `dump()` then seals the scalar indexer (2204-2207), dumps FTS (2209), and **unconditionally sets `sealed_ = true`** (2212), returning OK.
5. `collection.cc:1522` adds the segment to `segment_manager_` as a *persisted* segment; `collection.cc:1538` calls `writing_segment_meta->remove_writing_forward_block()`; `1539` adds it to the version; `1546-1548` `apply()` + `flush()` commit it. Because `VersionManager::get_current_version()` returns a shallow copy sharing `SegmentMeta::Ptr` (`version_manager.cc:255-258`, `version_manager.h:110-116`), this mutation is global. **The manifest now says: segment N is persisted, has no writing block, and has no `BlockType::SCALAR` block covering the recovered rows.**
6. Optimize proceeds: `collection.cc:802-803` picks up the segment; for one small segment with no deletes, `build_compact_task` yields a `CreateVectorIndexTask` (`collection.cc:991-1002`). `create_all_vector_index` (`segment.cc:1617-1640`) clones the meta and calls `remove_writing_forward_block()` again (1625); `merge_vector_indexer` merges only `vector_indexers_[column]` (`segment.cc:1652-1653`) — the *persisted* indexers, which never received the memory indexers because `finish_memory_components()` didn't run. `VectorColumnIndexer::Merge` returns OK on empty input (`src/db/index/column/vector_column/vector_column_indexer.cc:111-113`), and the output meta still records a `VECTOR_INDEX` block sized from the in-memory `doc_ids_` (`segment.cc:1720-1727`). Optimize commits it (`collection.cc:863-868`), applies + flushes the version (`872-877`), and returns `Status::OK()` (`914`).
7. **Reopen:** `collection.cc:1861-1874` opens persisted segments with `read_only_ = true` (`1864`). In `SegmentImpl::Open`, `writing_forward_block()` is now empty → the `else` branch at `segment.cc:502-510` → **`recover()` is never called**, so the WAL file (still on disk) is never replayed. `load_persist_scalar_blocks()` (`segment.cc:3913-3960`) rebuilds `doc_ids_` only from persisted `BlockType::SCALAR` blocks — there are none → `doc_count() == 0`.

Aggravating variants, all reachable from source: if Optimize instead picks a `CompactTask` (several segments, or delete ratio past `COMPACT_DELETE_RATIO_THRESHOLD`, `collection.cc:931-935`, `src/db/common/constants.h:41`), then `collection.cc:898-901` → `destroy_segment` → `SegmentImpl::destroy()` (`segment.cc:2326-2332`) → `~SegmentImpl` (`segment.cc:112-117`) → `cleanup()` (`segment.cc:2334-2338`) `RemoveDirectory(seg_path)`, which **deletes the orphaned WAL too**. And `init_memory_components` deletes any residual forward-block file as "crash residue" on the next open (`segment.cc:4126-4132`).

**Control case:** with no crash, `Insert` set `wal_file_` (`create_new=true`, `segment.cc:4308-4312`), so step 3's guard passes, the memory block becomes a persisted `SCALAR` block, the version is updated, and the WAL is removed (`segment.cc:2304-2315`). Reopen finds the persisted block. No loss.

### 3. Why OK + healthy stats prove nothing here

- `Status::OK()` from `flush()` is returned by the *early-out* (`segment.cc:2220-2222`) and is byte-identical to "everything persisted". `dump()` sets `sealed_ = true` regardless (`segment.cc:2212`).
- Nothing cross-checks the committed meta against the live data: `switch_to_new_segment_for_writing` never verifies that `persisted_blocks` covers `doc_ids_` before `remove_writing_forward_block()` + `apply()` + `flush()` (`collection.cc:1538-1549`).
- The old segment object stays alive in `segment_manager_` (`collection.cc:1522`) with `memory_store_`, `doc_ids_` and indexers intact, so `Stats()` (`collection.cc:403-442`) and `Fetch` keep answering from heap.
- `index_completeness` is derived from `meta()->vector_indexed(...)` (`collection.cc:433`), which `create_all_vector_index` set (`segment.cc:1636`, `1691`) even though the index file it produced is empty. So the stats can even report completeness 1.0 for a zero-row index.
- The one durable copy — the WAL — is no longer referenced by the committed version, so it is dead weight (or already deleted).

### 4. Smallest repair principle, and a regression test

**Repair principle (describe only):** *WAL replay must leave the segment in the same lifecycle state as an equivalent sequence of successful `Insert()` calls.* Concretely, at the end of `SegmentImpl::recover()` (`segment.cc:4193-4301`), adopt the replayed WAL as the live write handle instead of only closing the local one (`4215`) — either assign it to `wal_file_` and re-seek to end, or call `open_wal_file()` (`segment.cc:4303-4322`) after the replay loop. This is sufficient and requires no change to `flush()`: `LocalWalFile::open` with `create_new=false` already seeks to `End` (`src/db/index/storage/wal/local_wal_file.cc:99-106`), so later `append_wal` is safe, and `has_record()` (`local_wal_file.h:75-77`, `file_.size() > sizeof(header_)`) is true because the replayed records are still on disk — so the existing flush body runs and removes the WAL at `segment.cc:2304-2315`.

State it as an invariant and assert it: **if `writing_forward_block()` has `doc_count_ > 0` (equivalently `memory_store_ != nullptr`), then `wal_file_ != nullptr && wal_file_->has_record()`.**

Defense-in-depth, still small: make `flush()`'s guard reflect dirty *in-memory* state rather than handle presence (`segment.cc:2220`), and make `dump()` refuse to set `sealed_` (`segment.cc:2212`) while the writing block still has undumped rows. Also worth fixing nearby: `recover()` swallows a WAL-open failure by returning `Status::OK()` (`segment.cc:4209-4214`).

**Regression test sequence** (belongs beside `tests/db/crash_recovery/optimize_recovery_test.cc`, reusing `utility.h` and the existing fork/SIGKILL harness):

- **Phase 1 — writer child, hard exit.** Create with `CreateTestSchema`, but keep `max_doc_count_per_segment` and `max_buffer_size_` large enough that no rollover occurs; insert N docs (N well under both). Terminate **without** `Flush()` and without running the `Collection` destructor — `_exit()` after releasing the handle, or parent-side `SIGKILL` as in `write_recovery_test.cc:124-128`. Assert the writing segment's WAL file exists on disk.
- **Phase 2 — recovering process.** `Collection::Open`; assert `Stats().doc_count == N`, field-exact `Fetch` for all N pks, expected vector top-k (this establishes the replay precondition). Then call `Optimize()` **with zero intervening writes**; assert OK and assert `Stats().doc_count == N` on the same handle. Close cleanly.
- **Phase 3 — verifying process.** `Collection::Open` again; assert `Stats().doc_count == N`, field-exact `Fetch` for all N, same vector top-k. **This is the assertion that fails today** (expected N, actual 0).
- **Optional white-box asserts** right after Phase 2's `Optimize()`, which localize the fault instead of just reporting a count: (a) the writing segment's WAL file no longer exists (it must have been consumed by a real flush, `segment.cc:2304-2315`); (b) the committed manifest contains a persisted `BlockType::SCALAR` block whose doc-id range/count covers the recovered rows (produced only by `finish_memory_components`, `segment.cc:4366-4368`).
- **Control arm** in the same test: a second collection populated and `Optimize()`d in one process with no crash, then reopened — must still show N docs.

**Why the existing test misses it:** `OptimizeRecoveryTest.CrashDuringOptimize` inserts 500 more batches after reopening (`optimize_recovery_test.cc:207-218`) before invoking the optimizer (`:223`). Those inserts go `Insert` → `append_wal` → `open_wal_file` (`segment.cc:914`, `4327-4330`), which re-arms `wal_file_` and masks the bug. Add a variant that runs the optimizer immediately after recovery with no intervening write.

---

**Established from source vs. requiring execution.** Everything in §1–§3 and the repair reasoning is static: the null `wal_file_` after replay, the guard at `segment.cc:2220`, the version commit at `collection.cc:1538-1549`, the reopen path skipping `recover()` at `segment.cc:502-510`, and `doc_ids_` being rebuilt only from persisted `SCALAR` blocks at `segment.cc:3913-3960`. What would need a real run: the actual numbers (N vs 0), whether the orphan WAL survives or is removed by `cleanup()` (depends on whether Optimize selects `CreateVectorIndexTask` vs `CompactTask`, i.e. segment count and delete ratio), whether the empty rebuilt vector index causes anomalous in-process query results after Optimize, and whether the RocksDB-backed id_map/inverted index leave partial on-disk state. I did not build or run anything; no reproduction was executed.