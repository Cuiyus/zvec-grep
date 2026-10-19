## Root cause in one sentence

`SegmentImpl::flush()` uses the *WAL handle* as its dirty-flag (`src/db/index/segment/segment.cc:2220-2222`), but WAL **replay** never assigns that handle — only a new write does — so a segment whose entire content came from recovery flushes as a silent no-op, and `Optimize`'s segment switch then rewrites the manifest so it no longer points at the WAL.

---

## 1. Which internal state differs, and why the data is still readable

**Ordinary insertion path**

- `SegmentImpl::Insert` → `append_wal(doc)` → `internal_insert(doc)` — `segment.cc:903-918` (same shape for `Update` 920-936, `Upsert` 938-948, `Delete` 950-989).
- `append_wal` lazily calls `open_wal_file()` — `segment.cc:4324-4340`, specifically 4327-4330.
- `open_wal_file()` is the **only** place the member `wal_file_` is ever assigned — `segment.cc:4303-4322`, assignment at 4314; member declared at `segment.cc:401`.
- So after any real write: `wal_file_ != nullptr` and `has_record()` is true (`file_.size() > sizeof(WalHeader)`, i.e. > 64 bytes — `src/db/index/storage/wal/local_wal_file.h:75-77`, header at 31-37).

**Recovery path**

- `CollectionImpl::Open` → `recovery()` — `src/db/collection.cc:308-329` and `1836-1892`; the writing segment is opened from the manifest's `writing_segment_meta` at `collection.cc:1879-1887`.
- `SegmentImpl::Open` sees a `writing_forward_block` and calls `recover()` — `segment.cc:461-517`, gate at 484, call at 500.
- `recover()` — `segment.cc:4193-4301` — opens a **local** `recover_wal_file` (4206-4214), replays each record through `internal_insert` / `internal_update` / `internal_upsert` / `internal_delete` (4250-4272), and closes it via `AILEGO_DEFER` (4215). **It never assigns the member `wal_file_`.**
- `internal_insert` does *not* append to the WAL (contrast `Insert` at 914). It only populates RAM: `memory_store_->insert` (857), scalar/FTS/vector indexers (861-872), `doc_ids_.push_back` (878), and the in-memory block counters `mem_block.max_doc_id_` / `doc_count_` (874-876). `recover()` additionally advances `mem_block.max_doc_id_ += added_docs` at 4288.

**The delta is therefore exactly one field:** after pure WAL recovery `wal_file_ == nullptr`, and the memory components are populated with **no corresponding persisted `BlockMeta`**.

**Why it reads fine:** every read path is served from those RAM structures and never consults disk —
- `doc_count()` is literally `doc_ids_.size()` — `segment.cc:589-599`;
- forward fetch falls back to `memory_store_` — `segment.cc:2531-2532` and `2744-2756`;
- `Stats()` sums `segment->doc_count(...)` over `get_all_segments()` — `collection.cc:403-442`, `2076-2086`.

The only on-disk evidence of the data is still `<collection>/<seg_id>/<block_id>.wal` (`src/db/common/file_helper.h:74-78`), reachable solely through the manifest's `writing_segment_meta.writing_forward_block`.

---

## 2. Call sequence and the exact condition

`CollectionImpl::Optimize` — `collection.cc:779-915`:

| Step | Location | What happens |
|---|---|---|
| 1 | `collection.cc:794` | `writing_segment_->doc_count() != 0` → **true** (recovered docs are in `doc_ids_`) |
| 2 | `collection.cc:796` → `1517-1552` | `switch_to_new_segment_for_writing()` |
| 2a | `collection.cc:1519` → `segment.cc:2197-2215` → `2201` | `dump()` calls `flush()` |
| **2b** | **`segment.cc:2220-2222`** | **`if (wal_file_ == nullptr \|\| !wal_file_->has_record()) return Status::OK();`** ← the exact condition. `wal_file_` is null, so `flush()` returns OK having done **no I/O at all** |
| 2c | skipped: `segment.cc:2233-2236`, `2288-2298`, `2301`, `2305-2315` | `memory_store_->flush()`, `finish_memory_components()`, `update_version()`, WAL removal — all bypassed |
| 2d | `segment.cc:4342-4412` (never runs) | `finish_memory_components()` is the *only* thing that calls `segment_meta_->add_persisted_block(...)` for the SCALAR forward block (4368) and the vector blocks (4383, 4401), and the only thing that moves `memory_vector_indexers_` → `vector_indexers_` (4371-4406) |
| 2e | `collection.cc:1522` | the never-persisted segment is registered as a **persist** segment in `segment_manager_` |
| 2f | `collection.cc:1537-1538` | `writing_segment_->meta()` returns the *same* `shared_ptr` as `segment_meta_` (`segment.cc:585-587`), then `remove_writing_forward_block()` (`src/db/index/common/meta.h:263-265`) deletes the only pointer to the WAL |
| **2g** | **`collection.cc:1539, 1546-1549`** | `add_persisted_segment_meta` → `apply` → **`version_manager_->flush()`** writes `manifest.<n>` and deletes `manifest.<n-1>` (`src/db/index/common/version_manager.cc:282-301`, serialization `77-116`). **Point of no return:** the manifest now describes segment 0 as persisted with *zero* SCALAR blocks and no writing block |
| 3 | `collection.cc:802-810` | `persist_segments` = [seg0], non-empty, so no early return |
| 4 | `collection.cc:813-819` → `991-1002` | one segment + `rebuild == false` (931-935, no deletes) ⇒ a `CreateVectorIndexTask` |
| 5 | `src/db/index/segment/segment_helper.cc:1113-1125` → `segment.cc:1617-1640` | `create_all_vector_index` copies `*segment_meta_` and again strips the writing block (1624-1625) |
| 6 | `collection.cc:863-867, 872-877` | `update_persisted_segment_meta(output_meta)` → `apply` → `flush()` ⇒ `manifest.<n+1>`, still with no forward block |
| 7 | `collection.cc:902-909` → `segment.cc:1882-1940` | `reload_vector_index` swaps `segment_meta_` but leaves `doc_ids_`, `memory_store_`, `memory_vector_indexers_` intact |
| 8 | `collection.cc:914` | `return Status::OK()` |

**Why step 5/6 succeed rather than error** (established from source, both index flavours):
- FLAT / `DefaultVectorIndexParams` (`src/include/zvec/db/index_params.h:437`; marked indexed at `segment.cc:534-539`) ⇒ `create_vector_index` short-circuits at `segment.cc:1685-1690`.
- HNSW (what `tests/db/crash_recovery/utility.h:57-62` uses) ⇒ `merge_vector_indexer` over `vector_indexers_[column]`, which is **empty** because 2d never ran; `VectorColumnIndexer::Merge` returns `Status::OK()` on empty input — `src/db/index/column/vector_column/vector_column_indexer.cc:111-113`. It then records a VECTOR_INDEX `BlockMeta` claiming `doc_ids_.size()` docs (`segment.cc:1720-1727`) with no SCALAR block behind it.

**Next reopen:** `recovery()` opens seg0 read-only (`collection.cc:1858-1874`); `SegmentImpl::Open` finds no `writing_forward_block` (`segment.cc:484-490`) so `recover()` is never called; `load_persist_scalar_blocks` finds no SCALAR block, so `doc_ids_` stays empty (`segment.cc:3913-3957`) ⇒ `doc_count() == 0`. The `.wal` file is usually still physically present — it is only deleted by `cleanup()`, which runs solely when `destroy()` was called (`segment.cc:112-117`, `2326-2337`), and `Optimize` only destroys `CompactTask` inputs (`collection.cc:898-901`). It is simply orphaned.

**Control case:** identical `Optimize`, but `wal_file_ != nullptr && has_record()`, so `flush()` executes its whole body — `finish_memory_components()` creates the SCALAR/VECTOR `BlockMeta`s and `update_version()` (`segment.cc:4414-4426`) persists them *before* `switch_to_new_segment_for_writing` strips the writing block. No loss.

**Blast radius beyond `Optimize`:** the same no-op makes `Collection::Flush()` (`collection.cc:380-395`) and the `Close()` flush (`340-349`) silently ineffective on a recovery-only handle. That is *harmless by itself*, because the manifest still references the WAL and a later reopen replays it again. It becomes loss only when a manifest rewrite drops that reference — i.e. `switch_to_new_segment_for_writing`, which is also reached from `CreateIndex` (`collection.cc:502`), `DropIndex` (675), `AddColumn` (1155), `DropColumn` (1227), `AlterColumn` (1311), and the pre-write rollover in `write_impl` (1445-1448, excluded by your "no rollover" assumption).

---

## 3. Why `OK` + healthy stats prove nothing here

- **`Status::OK()` is overloaded.** The early-out at `segment.cc:2220-2222` returns OK without touching the filesystem. `dump()` propagates it (2201-2214). No caller anywhere distinguishes "nothing to persist" from "bytes are durable".
- **`Stats()` reads RAM only.** `collection.cc:421-423` sums `SegmentImpl::doc_count()` = `doc_ids_.size()` (`segment.cc:589-599`), a vector that nothing in the Optimize path clears. `index_completeness` (425-439) reads `meta()->vector_indexed(...)`, which `create_all_vector_index` *sets* on the output meta (`segment.cc:1636`). Both are satisfied entirely by memory state that the manifest no longer describes.
- **The divergence is even observable in-process**, which shows stats are the wrong instrument: after the switch, `SegmentMeta::max_doc_id()` returns 0 (`meta.h:293-305` — no writing block, no SCALAR block), so `SegmentImpl::Fetch` rejects every `g_doc_id > 0` at `segment.cc:1102`, while `Stats()` still reports N documents.
- Durability here is a property of *manifest + block files*. The only sound checks are (a) reopen in a fresh process, or (b) an invariant that every in-memory doc range is covered by a persisted `BlockMeta`.

---

## 4. Smallest repair principle, and a regression test

### Repair (described, not implemented)

The principle: **stop using the WAL handle as the proxy for "this segment has un-persisted state".** Recovered-but-unflushed is a real dirty state and must be treated as such.

- **(b) Minimal and complete — fix the guard.** At `segment.cc:2220-2222`, gate on actual dirtiness instead of `wal_file_`: e.g. `memory_store_ != nullptr && memory_store_->num_rows() > 0`, or `writing_forward_block()->doc_count_ != 0`, or `!doc_ids_.empty()` not covered by persisted SCALAR blocks. The WAL-specific steps already null-check `wal_file_` individually (`2224-2229`, `2305-2315`), so only the entry condition changes. This one edit repairs `Flush()`, `Close()`, `Optimize()` and all five schema operations at once.
- **(a) Alternative one-liner — converge the paths.** In `recover()` (`segment.cc:4193-4301`), when ≥1 record was replayed, leave the WAL open for append in the member `wal_file_` rather than closing the local `recover_wal_file` (4215). Then the existing `flush()` body runs unchanged. Implementation caveat: `LocalWalFile::open(create_new=false)` seeks to End (`local_wal_file.cc:99-106`) while `prepare_for_read()` seeks to Begin (`143-158`), so either read to EOF then append on one handle, or open the member handle after replay finishes.
- **(c) Defence in depth at the caller.** `switch_to_new_segment_for_writing` (`collection.cc:1517-1552`) should refuse to `remove_writing_forward_block()` + `add_persisted_segment_meta()` + `version_manager_->flush()` unless the dump actually produced a persisted SCALAR `BlockMeta` covering the segment's `doc_ids_` — i.e. assert `meta()->doc_count() == doc_count()` before promoting a writing segment to persisted. This converts any future variant of silent loss into a loud error and protects all six call sites.

Recommend (b) as the fix plus (c) as the invariant guard.

### Regression test (described)

Put it in `tests/db/crash_recovery/`, which already has every fixture needed: SIGKILL-able `data_generator` (`data_generator.cc`, whose clean path flushes at 245-248), `collection_optimizer` (`collection_optimizer.cc:107`), `CreateTestSchema`/`CreateTestDoc` (`utility.h:38-156`), and the process helpers `ExecuteProcess` (`write_recovery_test.cc:56-148`) / `ExecuteOptimizer` (`optimize_recovery_test.cc:53-128`). Keep N well under `set_max_doc_count_per_segment(10000)` (`utility.h:41`) so no rollover occurs, matching the report.

`TEST_F(CrashRecoveryTest, OptimizeAfterWalRecoveryWithoutNewWrites)`:

1. `Collection::CreateAndOpen(dir_path_, schema, options_)`, then `collection.reset()` — clean empty collection (pattern: `write_recovery_test.cc:183-189`).
2. `RunGeneratorAndCrash("0", "500", "insert", "0", secs)` — SIGKILL before the generator's `Flush()`, so the docs exist **only** in `<path>/0/0.wal`.
3. `Collection::Open(dir_path_, options_)`; capture `d1 = Stats().doc_count`; `ASSERT_GT(d1, 0)` (use `GT`, not `EQ` — the kill is wall-clock based, cf. `write_recovery_test.cc:198`); `Fetch` a sample of pks and compare against `CreateTestDoc(id, 0)`; run a `Query` and record the top-k. This pins the "recovered and readable" state.
4. **Insert nothing.** Call `collection->Optimize(OptimizeOptions{2})`; `ASSERT_TRUE(s.ok())`. Then capture `d2 = Stats().doc_count` and `ASSERT_EQ(d2, d1)` — this documents the "same-handle stats still look healthy" half of the bug.
5. `collection.reset()` (clean close).
6. `Collection::Open(dir_path_, options_)` again; `ASSERT_EQ(Stats().doc_count, d1)`; re-`Fetch` every pk in `[0, d1)` and `ASSERT_EQ(*actual, CreateTestDoc(id, 0))`; re-run the `Query` and compare the top-k. **Step 6 is the assertion that fails on the current tree** (it will observe 0 docs).
7. Variants worth adding: call `Optimize` twice in a row (cf. `Feature_Optimize_Repeated`, `tests/db/collection_test.cc:2920`); substitute `CreateIndex` / `AddColumn` for `Optimize` in step 4, since they share `switch_to_new_segment_for_writing` (`collection.cc:502`, `1155`); and a control that inserts one doc after recovery before optimizing — that one passes today and locks in the non-crash behaviour.
8. **Timing-free white-box companion** (no fork/kill, so not flaky): a unit test in `tests/db/` that, after WAL recovery, asserts the writing segment satisfies `meta()->doc_count() == doc_count()`, and that after `Flush()`/`Optimize()` its meta contains a persisted SCALAR `BlockMeta` whose `doc_count_` covers `doc_ids_`. That invariant is violated by the current code and is exactly guard (c).

---

## Source-established vs. needs-execution

**Established by reading the tree:** the single assignment site of `wal_file_` (`segment.cc:4314`) and its only caller chain (`append_wal` ← `Insert/Update/Upsert/Delete`); `recover()`'s use of a separate local handle that it closes (`4206-4215`); the `flush()` guard (`2220-2222`) and everything it skips; the ordering in `switch_to_new_segment_for_writing` (`collection.cc:1519 → 1538 → 1548`); the manifest rewrite/rotate semantics (`version_manager.cc:282-301`); the reopen path that skips `recover()` when there is no writing block (`segment.cc:484-490`) and rebuilds `doc_ids_` only from SCALAR blocks (`3913-3957`); and that `Optimize` returns OK for both FLAT (`segment.cc:1685-1690`) and HNSW (`vector_column_indexer.cc:111-113`) index types. The causal chain is fully determined by these.

**Would require building and running:** the end-to-end confirmation that doc count goes to 0 on the third open, the exact `d1` produced by a timed SIGKILL, and whether any FTS/scalar-index path adds a second failure mode (`dump_fts_indexers` / `invert_indexers_->seal()` at `segment.cc:2204-2210` run *after* the no-op flush, and I did not trace whether sealing an indexer whose records were never flushed is itself lossy).

I did not build, run, or reproduce anything; this is a static reading of the frozen tree. No files were modified.