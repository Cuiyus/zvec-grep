## Root cause

`SegmentImpl::flush()` uses "the WAL handle is null" as a proxy for "nothing to persist" (`src/db/index/segment/segment.cc:2220`), but the WAL-replay path never assigns that handle. Everything downstream of the guard is skipped, yet returns `Status::OK()`.

## 1. Divergent internal state

**Ordinary insert** — `SegmentImpl::Insert` (`src/db/index/segment/segment.cc:903-918`) calls `append_wal` (`:4324-4340`), which lazily calls `open_wal_file` (`:4303-4322`). That is the *only* assignment to the member `wal_file_` (declared `:401`; verified by exhaustive grep). Then `internal_insert` (`:837-881`).

**Recovery** — `SegmentImpl::Open` (`:461-517`) sees `writing_forward_block` present in the manifest and calls `recover()` (`:4193-4301`). Recovery opens a *local* `recover_wal_file` (`:4206-4215`), replays each record directly into `internal_insert/internal_update/internal_upsert/internal_delete` (`:4250-4272`) — bypassing `Insert`, hence bypassing `append_wal` — and closes it via `AILEGO_DEFER` (`:4215`). **`wal_file_` remains `nullptr`.**

**Why it is still readable:** replay populates the same live in-memory structures a normal insert would. `internal_insert` triggers `init_memory_components()` (`:4119-4190`, creating `memory_store_` and `memory_vector_indexers_`), bumps `mem_block.max_doc_id_/doc_count_` (`:874-876`) and `doc_ids_` (`:878`); `recover()` also corrects `mem_block.max_doc_id_` (`:4288`). `fetch_normal` explicitly serves rows from the memory store whenever `has_writing_forward_block()` (`:2483-2498`). Durability at this point is provided *incidentally* by the still-intact `<seg_id>/<block_id>.wal` file (`src/db/common/file_helper.h:74-78`), not by the segment.

## 2. Call sequence and exact condition

1. `CollectionImpl::Optimize` (`src/db/collection.cc:779-812`): `writing_segment_->doc_count() != 0` is **true**, because `doc_count()` is `doc_ids_.size()` (`segment.cc:589-600`) → calls `switch_to_new_segment_for_writing()`.
2. `src/db/collection.cc:1519` → `SegmentImpl::dump()` (`segment.cc:2197-2215`) → `flush()` (`:2217`).
3. **The condition:** `segment.cc:2220` — `if (wal_file_ == nullptr || !wal_file_->has_record()) return Status::OK();`. `wal_file_` is null → immediate OK. Skipped: `memory_store_->flush()`, scalar/FTS/vector indexer flushes, `id_map_->flush()`, delete-store snapshot, **`finish_memory_components()`** (`:2290`, defined `:4342-4412` — the sole caller of `segment_meta_->add_persisted_block()` for the memory block), new-writing-block allocation, `update_version()`, and `wal_file_->remove()` (`:2306`).
4. `dump()` sets `sealed_ = true` (`:2212`) and returns OK.
5. `switch_to_new_segment_for_writing` then commits the meta change: old segment added to `segment_manager_` (`collection.cc:1522`), new empty segment created (`:1526-1533`), **`writing_segment_meta->remove_writing_forward_block()`** (`:1538`; `src/db/index/common/meta.h:263-265`), `add_persisted_segment_meta` (`:1539`), `version_manager_->apply` + `flush()` (`:1546-1550`) which durably rewrites the manifest (`src/db/index/common/version_manager.cc:282-293`). The old segment is now a *persisted* segment with **no SCALAR block and no writing block**.
6. `get_all_persist_segments()` (`collection.cc:2084-2086`) returns it. For one small segment, `build_compact_task`'s tail (`collection.cc:990-1001`) emits a `CreateVectorIndexTask`, not a compact — so the in-memory rows are never re-materialized. `create_all_vector_index` (`segment.cc:1617-1640`) merges over `vector_indexers_[column]` (`:1642-1668`), which is empty because `finish_memory_components()` never migrated `memory_vector_indexers_`; it still returns OK and records a VECTOR_INDEX block sized from in-memory `doc_ids_` (`:1719-1727`). (For FLAT fields it short-circuits entirely at `:1685-1690`, since `Create` pre-marks them indexed at `:534-538`.) `Optimize` returns OK.
7. **Reopen:** `CollectionImpl::recovery()` (`collection.cc:1836-1892`) opens that meta read-only; `SegmentImpl::Open` finds `writing_block == nullopt` (`segment.cc:484-509`), takes the else branch, and **never calls `recover()`**. The `.wal` file is still on disk but orphaned. `load_persist_scalar_blocks` (`:3913-3958`) finds no SCALAR block → `doc_ids_` empty → documents gone.

**Control case:** `wal_file_` is non-null and `has_record()` is true (`src/db/index/storage/wal/local_wal_file.h:75-77`), so `flush()` runs the full path and the data is on disk *before* the meta switch.

The same no-op silently affects `Collection::Flush()` (`collection.cc:380-395`) and `close_unsafe()` (`:339-360`) after recovery, and every other `switch_to_new_segment_for_writing` caller — CreateIndex (`:502`), DropIndex (`:675`), AddColumn (`:1155`), DropColumn (`:1227`), AlterColumn (`:1311`), write-path rollover (`:1446`). Optimize is merely the one that also removes the WAL recovery anchor.

## 3. Why success and stats prove nothing

Every step returns `Status::OK()` because the early return at `segment.cc:2220` is OK, not an error; nothing cross-checks that the segment's in-memory doc count is reflected in `persisted_blocks()` before `remove_writing_forward_block()` runs. `Stats()` (`collection.cc:403-442`) sums `segment->doc_count(...)` over `get_all_segments()` (`:2076-2082`) — the *same live `SegmentImpl` objects* still held in `segment_manager_`, whose `doc_ids_` and `memory_store_` the no-op flush left untouched. Fetch/Query read the same RAM. Durability is a property of the manifest plus block files; the one artifact that made the recovered rows durable was de-referenced by the meta edit rather than deleted, so nothing in-process observes the loss.

## 4. Smallest repair principle and regression test

**Repair (describe only):** make the WAL handle lifecycle match the recovery path. In `SegmentImpl::recover()` (`segment.cc:4193-4301`), end recovery by calling `open_wal_file()` (`:4303-4322`) so the segment owns an open handle on the WAL it just replayed (it already selects `create_new = false` when the file exists) instead of using a throwaway local. `has_record()` is then true and `flush()` performs the real persist and `wal_file_->remove()`. Complement with an invariant guard — drive `:2220` off dirty state (`memory_store_ != nullptr` or `writing_forward_block()->doc_count_ != 0`) rather than the WAL pointer, and/or assert in `switch_to_new_segment_for_writing` (`collection.cc:1519-1539`) that after `dump()` the old meta has persisted SCALAR blocks covering `doc_count()` before `remove_writing_forward_block()`. Prefer the lifecycle fix; add the guard so any future regression fails loudly instead of losing data.

**Regression test (describe only):** in `tests/db/crash_recovery/`, modeled on `optimize_recovery_test.cc:144-261` and `write_recovery_test.cc:182-216`:
1. Process A: create collection, insert N docs (N below `max_doc_count_per_segment` and `max_buffer_size_` so no rollover), **no flush**, `SIGKILL`/`TerminateProcess` via the existing `RunGeneratorAndCrash` / `data_generator` harness.
2. Process B: `Collection::Open`; assert `Stats().doc_count == N` and Fetch-equality for all N pks (passes today).
3. Process B: `Optimize({})`, assert OK; then assert the on-disk state is self-sufficient — the block's `.wal` file is gone (consumed by a real flush) and/or the old segment meta carries a persisted SCALAR block.
4. Process B closes; Process C: `Collection::Open` again, assert `Stats().doc_count == N`, per-pk Fetch equality, and a Query returning expected hits. **Step 4 is the assertion that fails today (yields 0).**
5. Control arm: same N docs inserted and optimized in one process with no crash; assert identical results (passes today), isolating the recovery path.

Add a cheaper unit-level pin next to `SegmentTest.Recover` (`tests/db/index/segment/segment_test.cc:366`): create segment with docs, close without flush, reopen (recover), call `dump()`/`flush()` with no intervening insert, reopen a third time, assert `doc_count()` unchanged — no process kills needed.

## Established from source vs. requiring execution

I read only the frozen tree; **I did not build or run anything, and did not reproduce the failure.** The static chain is unconditional given the stated preconditions: null `wal_file_` after `recover()` → OK-returning `flush()` → `remove_writing_forward_block()` + manifest flush → `writing_block == nullopt` on reopen → `recover()` never invoked. What execution would be needed to confirm: the actual post-reopen `doc_count`; whether `create_all_vector_index` over an empty `vector_indexers_[column]` succeeds or errors for a given index type (HNSW vs FLAT differ, per `segment.cc:534-538` and `:1685-1690`); and whether the RocksDB-backed `id_map`/`delete_store` leave partial residue after the skipped flushes.