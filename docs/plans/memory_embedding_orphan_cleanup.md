---
status: Planning
type: bug
appetite: Medium
owner: Tom Counsell
created: 2026-04-30
tracking: https://github.com/tomcounsell/ai/issues/1214
last_comment_id:
---

# Memory Embedding Orphan Cleanup

## Problem

The Memory model's on-disk embedding store at `~/.popoto/content/.embeddings/Memory/` has accumulated 7,443 `.npy` files for only 173 live `Memory` records — a 43:1 orphan ratio. Four leaked `tmp*.npy` atomic-write tempfiles (some zero-byte, some partial) are also present, the oldest from Apr 19. The `_index.json` sidecar itself contains 7,428 entries (1.2 MB), so both disk and index are out of sync with Redis.

**Current behavior:**

- Every `retrieve_memories()` call walks the entire embedding directory and logs hundreds of `WARNING Skipping unrecognized embedding file ...` and `WARNING Failed to load embedding ... No data left in file` lines.
- Semantic-similarity ranking degrades silently — failed loads return empty arrays so the RRF fusion falls back to the other three signals (BM25, relevance, confidence) without telling anyone.
- Disk usage and index file size grow unbounded.
- `python -m tools.memory_search status --deep` reports `Orphan index keys: 0`, contradicting the actual disk state. The check only walks the Redis class set, never the disk.

**Desired outcome:**

- Memory deletions remove the `.npy` and the `_index.json` entry in one transaction.
- Worker startup sweeps stale `tmp*.npy` files older than 1 hour.
- A scheduled reflection performs disk-vs-Redis reconciliation periodically.
- `status --deep` reports both Redis-side and disk-side orphans.
- A one-shot reconciliation reduces the existing 7,000+ orphan files to zero.
- Recall queries produce zero per-file warnings on a clean corpus.
- The full embedding-file lifecycle is documented.

## Freshness Check

**Baseline commit:** `32bb1f5297d254c9203e828934422a9e6bcaafe5` (main)
**Issue filed at:** 2026-04-29T16:23:58Z (~22 hours before plan time)
**Disposition:** Unchanged

**File:line references re-verified:**

- `agent/memory_retrieval.py:282` — `embedding_results = get_embedding_ranked(...)` — still holds
- `models/memory.py:108` — `embedding = EmbeddingField(source="content")` — still holds
- `tools/memory_search/__init__.py:463-478` — `status --deep` orphan check call site — still holds
- `popoto/fields/embedding_field.py:339-367` — existing `on_delete` hook — still holds
- `popoto/fields/embedding_field.py:459-470` — `garbage_collect()` stub — still holds
- `scripts/popoto_index_cleanup.py:23-35` — `_has_embedding_field()` skip-list — still holds (Memory is excluded)
- `scripts/popoto_index_cleanup.py:63-89` — `_count_orphans()` Redis-only check — still holds

**Cited sibling issues/PRs re-checked:**

- #1212 — open, related (extraction-side parser bugs producing JSON shrapnel as memories) — distinct fix, out of scope for this plan
- #964, #970 — merged, introduced `memory_search status --deep` subcommand — this plan extends the orphan check rather than replacing it

**Commits on main since issue was filed (touching referenced files):**

- None. `git log --since=2026-04-29T16:23:58Z` against the referenced files returned empty.

**Active plans in `docs/plans/` overlapping this area:** None. `grep -l "embedding\|memory" docs/plans/` yields plans for unrelated memory-system work (extraction, consolidation), none touching the on-disk embedding lifecycle.

**Notes:** No drift. The recon evidence remains accurate.

## Prior Art

- **#964 / #970** (merged): Added `memory_search status` and the `--deep` subcommand. The Redis-side orphan scan introduced here is the asymmetric check that this plan must extend, not replace.
- **#967 / #748** (merged): Extracted `reflections/` package from the monolith — established the reflection scheduling pattern and `reflections/memory_management.py` is the closest neighbor for the new sweep reflection.
- **#1082** (merged), **#864** (merged), **#677** (merged): Unrelated embedding work (test cleanup, chunked retrieval, emoji reactions). No prior fixes targeted on-disk lifecycle, so the **Why Previous Fixes Failed** section is omitted (no prior failures exist).

## Research

**Queries used:**

- `numpy npy atomic write tempfile cleanup orphan files pattern 2026`
- `vector embedding store on-disk garbage collection orphan reconciliation pattern`

**Key findings:**

- **Atomic write requires explicit tempfile cleanup**. The `tempfile.mkstemp()` + `os.rename()` pattern leaks the tempfile if the process crashes between `mkstemp()` and `rename()`. The widely-recommended mitigation is a periodic sweep that removes `tmp*` files older than N seconds. Source: [docs.python.org/3/library/tempfile.html](https://docs.python.org/3/library/tempfile.html), [bswen.com on atomic file writing](https://docs.bswen.com/blog/2026-04-04-atomic-file-writing-python/). This validates the issue's "stale tempfile sweep" approach.
- **Async reconciliation is the canonical pattern for on-disk vector orphans**. WarpStream's GC writeup describes it as: "object storage does not GC itself; reconciliation loops are added in addition to delayed-queue deletes to clean up any orphaned files that were missed." Source: [warpstream.com — Taking out the Trash](https://www.warpstream.com/blog/taking-out-the-trash-garbage-collection-of-object-storage-at-massive-scale). This validates the "scheduled reflection that walks disk vs. live records" approach over a "delete on every shutdown" approach (which fails when processes crash).

Both findings inform the solution: combine **immediate cleanup** (already wired via `on_delete`) with **periodic reconciliation** (the missing piece).

## Spike Results

This is a Medium-appetite bug fix with verified file:line evidence; no spikes are required. The recon already validated:

- `EmbeddingField.on_delete` exists and is invoked by `Model.delete` (read both sources).
- `garbage_collect()` is a stub returning 0 (read source).
- `_count_orphans()` walks Redis only (read source).

Removing the spike scaffold per template guidance.

## Data Flow

This plan touches a non-trivial data flow across three sites (write, delete, sweep). Trace below:

**Write path (existing, unchanged):**

1. `Memory.save()` → Popoto field-iteration → `EmbeddingField.on_save()`
2. `on_save()` calls Ollama provider, normalizes vector, writes via atomic `tempfile.mkstemp()` + `os.rename()`
3. `_index.json` is read, mutated to add `{filename: redis_key}`, atomically rewritten
4. Redis hash is updated with the dimension count

**Delete path (existing, BROKEN somewhere):**

1. `Memory.delete()` → Popoto field-iteration → `EmbeddingField.on_delete()` (`base.py:1625-1638`)
2. `on_delete()` removes the `.npy` file (`os.unlink`)
3. `on_delete()` removes the entry from `_index.json` (`_read_index → del → _write_index`)

The hook **is wired**. But the disk and index are 43x oversized, which means deletions either bypassed `Memory.delete()` historically (e.g., raw Redis `DEL` from earlier code, or a `bulk_delete` path that skipped `on_delete`) or `on_delete` raised silently. Either way, the existing path is necessary but not sufficient — we need a **reconciliation sweep** to catch what the per-record path missed.

**New sweep path (this plan):**

1. Scheduled reflection (or worker startup) → `EmbeddingField.garbage_collect(Memory)`
2. List all `.npy` files in `~/.popoto/content/.embeddings/Memory/`
3. Read `Memory:_all` Redis class set → set of live keys
4. Read `_index.json` → mapping of filename → redis_key
5. For each disk file:
   - If filename not in `_index.json` → orphan; remove (or mark for removal in dry-run)
   - If filename in `_index.json` but its redis_key not in `Memory:_all` → orphan; remove and update `_index.json`
6. Separately, sweep `tmp*.npy` files with mtime older than 1 hour

**Status check path (new branch in this plan):**

1. `status --deep` already calls `_count_orphans()` (Redis-side check)
2. NEW: also calls `_count_disk_orphans()` (disk-side check) and reports both

## Architectural Impact

- **New dependencies**: None. Uses stdlib (`os`, `time`) and existing Popoto plumbing.
- **Interface changes**: `EmbeddingField.garbage_collect()` gains a real implementation (signature unchanged: `(model_class) -> int`). New helper `_count_disk_orphans()` exposed from `scripts/popoto_index_cleanup.py`. `status --deep` adds a `disk_orphans` field.
- **Coupling**: Slight increase — the cleanup logic must know that the embedding directory is laid out as `<embeddings_dir>/<ModelName>/{filename}.npy` and that `_index.json` is the source of truth. This coupling already exists in `EmbeddingField` itself; the sweep just reads the same paths.
- **Data ownership**: Unchanged. Popoto continues to own the on-disk store; this repo schedules cleanup but never writes to the directory directly outside the Popoto API.
- **Reversibility**: Easy. The reflection can be disabled in `config/reflections.yaml`. The `garbage_collect()` implementation can be reverted to a stub. Already-deleted orphan files cannot be restored, but they were already orphans (unrecoverable from Redis).

## Appetite

**Size:** Medium

**Team:** Solo dev with sibling-repo coordination (Popoto vendored at `~/src/popoto`, separate git repo).

**Interactions:**

- PM check-ins: 1-2 (decision on whether `garbage_collect` lives in Popoto or in this repo as an external helper; design of dry-run/apply gating)
- Review rounds: 1 (single review covers Popoto changes + ai/ wiring)

Solo dev coding time is small; the bottleneck is the Popoto side (changes to a vendored library that other models also use, so risk-of-regression matters) and the live-data reconciliation step (one-shot script run against 7,000+ files).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Popoto repo writable at `~/src/popoto` | `test -w ~/src/popoto/.git` | Need to commit `EmbeddingField.garbage_collect()` |
| Memory directory exists | `test -d ~/.popoto/content/.embeddings/Memory/` | Sweep target must be present |
| Embedding provider configured | `python -c "from popoto.fields.embedding_field import get_default_provider; assert get_default_provider() is not None"` | Required so reading `_index.json` matches the same path the writer uses |
| pytest available | `python -m pytest --version` | Needed for the integration test in success criteria |

Run all checks: `python scripts/check_prerequisites.py docs/plans/memory_embedding_orphan_cleanup.md`

## Solution

### Key Elements

- **`EmbeddingField.garbage_collect(Memory)`** in Popoto: real implementation that reconciles disk against Redis and `_index.json`, returns count removed. Replaces the existing stub.
- **`EmbeddingField.sweep_stale_tempfiles(Memory, max_age_seconds=3600)`** in Popoto: removes `tmp*.npy` files older than the cutoff. Returns count removed.
- **`reflections/memory_management.py::run_embedding_orphan_sweep`** in this repo: thin wrapper that calls both Popoto methods, with dry-run gating via `EMBEDDING_ORPHAN_SWEEP_APPLY` env var. Registered in `config/reflections.yaml` as `embedding-orphan-sweep` (daily).
- **`scripts/popoto_index_cleanup.py::_count_disk_orphans(Memory)`** in this repo: walks the embedding directory and counts files whose redis_key is not in `Memory:_all`. Pure read-only.
- **`tools/memory_search/__init__.py::status(deep=True)`**: extended to call `_count_disk_orphans()` and report `disk_orphans` alongside the existing `orphan_index_keys`.
- **`scripts/embedding_orphan_reconcile.py`** in this repo: one-shot CLI for the existing 7,000+ files. Dry-run by default; `--apply` actually deletes. Logs the count to stdout for the PR description.
- **Log noise reduction**: lower `Skipping unrecognized embedding file` from WARNING to DEBUG in Popoto's `load_embeddings`. After reconciliation it should never fire on a clean corpus, but defense-in-depth.

### Flow

**Operator runs cleanup once:**
`scripts/embedding_orphan_reconcile.py --dry-run` → see "would remove N files" → `--apply` → "removed N files" → check `python -m tools.memory_search status --deep` shows `disk_orphans: 0`

**Ongoing prevention:**
Memory created → on_save writes .npy + index entry → Memory deleted → on_delete removes .npy + index entry → daily `embedding-orphan-sweep` reflection runs garbage_collect + sweep_stale_tempfiles → if any orphans found (concurrent crash, raw Redis op), they are removed → `status --deep` shows `disk_orphans: 0`

### Technical Approach

- **Popoto changes are minimal and additive.** `garbage_collect` has the right signature already; we just write the body. `sweep_stale_tempfiles` is a new classmethod, also small. Both go in `popoto/fields/embedding_field.py` next to the existing `on_delete`.
- **Source of truth for "live" is the Redis class set `<ModelName>:_all`**, not the `_index.json`. The index is a derived cache that is also being reconciled. Walking against the Redis set means we tolerate index corruption.
- **Reading Redis from inside Popoto**: `EmbeddingField.garbage_collect` already takes `model_class`; we use `POPOTO_REDIS_DB.smembers(f"{model_class.__name__}:_all")` which is the same access pattern used by `_count_orphans` in this repo.
- **Concurrency safety**: a parallel write that creates a new `.npy` while the sweep is running is safe — the sweep snapshots the directory listing at the start, and any new file appearing after that snapshot is not visited. The only hazard is deleting a file just as another process re-creates it; `os.unlink` with `FileNotFoundError` swallow handles the inverse race.
- **Dry-run is the default for the reflection.** `EMBEDDING_ORPHAN_SWEEP_APPLY=true` env var (matching the existing `MEMORY_DECAY_PRUNE_APPLY` pattern in `memory_management.py`) gates actual deletion. This matches the established prevention-over-cleanup pattern in this codebase.
- **One-shot script also defaults to dry-run.** This is the operator's deliberate first run; `--apply` only after dry-run output is reviewed.
- **Tempfile sweep cutoff is 1 hour.** Atomic writes complete in milliseconds. A 1-hour cutoff is conservative — anything older is unambiguously a leak.
- **The `_has_embedding_field()` skip in `popoto_index_cleanup._get_all_models()` stays.** That skip exists because `rebuild_indexes()` would re-trigger Ollama embed calls. Our new sweep does NOT call `rebuild_indexes`; it only deletes orphans. So we add Memory back via a dedicated path, not by removing the skip.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `EmbeddingField.garbage_collect`: an `os.unlink` raising `OSError` (permissions, race) must be logged and skipped, not propagated. Test asserts a single failure leaves the rest of the sweep intact.
- [ ] `_count_disk_orphans`: a missing embedding directory must return 0, not raise. Test exercises the "fresh install" case.
- [ ] `run_embedding_orphan_sweep` reflection wrapper: any exception inside Popoto must be caught and reported in the reflection result dict, never crash the scheduler. Test injects a synthetic exception.
- [ ] `load_embeddings` log-level change: "Skipping unrecognized" downgraded to DEBUG. Test asserts the WARNING-level log no longer fires (use `caplog`).

### Empty/Invalid Input Handling

- [ ] Empty embedding directory: `garbage_collect` returns 0, no exceptions
- [ ] Empty `_index.json`: same
- [ ] No live Memory records: `garbage_collect` removes ALL files. Test asserts behavior is correct (nothing in Redis means nothing to keep).
- [ ] Missing `_index.json` (legacy install): `garbage_collect` falls back to hex-decoding filenames, same as `load_embeddings` does today. Test exercises this path.

### Error State Rendering

- [ ] One-shot CLI: when `--apply` runs and partially fails (some unlinks succeed, some fail), output reports both counts. Test mocks half the files to raise `OSError`.
- [ ] Reflection result: `status: "ok" | "error"`, `findings: [...]`, `summary: "..."` matching the existing `run_memory_decay_prune` shape.

## Test Impact

- [ ] `tests/unit/test_memory_search_status.py` (existing) — UPDATE: extend the `--deep` test to assert `disk_orphans` field is present in the output.
- [ ] `tests/unit/test_popoto_index_cleanup.py` (existing) — UPDATE: assert that adding a `_count_disk_orphans` helper does not break the existing `_count_orphans` Redis-side scan.
- [ ] `tests/integration/test_memory_lifecycle.py` (NEW) — REPLACE: integration test that creates a Memory, asserts the `.npy` exists; deletes the Memory, asserts the `.npy` is gone; manually drops a stray file, runs `garbage_collect`, asserts cleanup.
- [ ] Popoto: add `tests/test_embedding_field_gc.py` in `~/src/popoto` covering `garbage_collect` and `sweep_stale_tempfiles` against a temp directory.
- [ ] `tests/integration/test_memory_retrieval.py` (existing) — UPDATE: capture log output during `retrieve_memories` and assert no `WARNING Skipping unrecognized embedding file` lines after a clean fixture setup. (This is the user-visible signal — once the corpus is clean and Popoto's log level is lowered, this test should be silent.)

## Rabbit Holes

- **Adding a "soft delete" / tombstone column to Memory.** The recon already established Memory has no soft-delete and the orphans are pure garbage. Don't introduce one.
- **Rewriting `EmbeddingField` to use a single combined index file.** The existing `_index.json` is fine; the bug is missing reconciliation, not the data structure.
- **Migrating to Redis-native vector storage (RediSearch FT.SEARCH).** Out of scope per the issue, and would replace the entire `EmbeddingField` rather than fix it.
- **Adding a `--clean-orphans` interactive flag to `status --deep`.** The issue suggests this, but it conflates "report" with "act". Keep `status` read-only and put apply behavior in the dedicated `embedding_orphan_reconcile.py` script.
- **Investigating which historical code path bypassed `Memory.delete()`** to leave 7,400 orphans. Worth one paragraph of research, but not worth chasing — git archaeology on a Redis-only deletion path is unlikely to find a smoking gun, and the prevention (sweep reflection) catches it regardless of historical source.

## Risks

### Risk 1: Sweep deletes files that a concurrent process is mid-writing

**Impact:** A `Memory.save()` that has just landed an atomic `.npy` could (in theory) have its file removed if the sweep snapshot was taken between `os.rename` and the Redis hash write.
**Mitigation:** Source of truth for "live" is `Memory:_all` Redis class set, which is updated as part of save. We snapshot the file listing FIRST, then check Redis for each filename's mapped key. If the Redis key exists, we keep the file. The race window is the inverse: file exists on disk, Redis save hasn't completed → we'd treat it as orphan and delete. Mitigation: skip any disk file whose mtime is within the last 60 seconds. Recently-written files always survive.

### Risk 2: Popoto change breaks unrelated models that also use EmbeddingField

**Impact:** Other consumers of `EmbeddingField.garbage_collect` (currently zero, but could grow) would suddenly get real deletion behavior instead of a no-op.
**Mitigation:** The signature is unchanged and the docstring already says "Remove orphaned .npy files". Any caller of the stub today gets the documented behavior; no caller should be relying on the no-op result. Test in Popoto's own suite covers correct behavior. Communicate the Popoto bump in this repo's PR description.

### Risk 3: One-shot reconcile deletes 7,000 files in production embedding directory

**Impact:** If the script is buggy, we could remove embeddings for live records, silently downgrading recall quality.
**Mitigation:** Default is dry-run. Apply requires `--apply`. The script logs the live key set count and the removed file count; refuse to delete if removed-count is greater than (disk-files - live-records + 50) — sanity bound. Add an integration test that creates 5 live Memory records + 50 stray .npy files, runs `--apply`, asserts the 5 live files survive.

## Race Conditions

### Race 1: Save lands while sweep is iterating

**Location:** `popoto/fields/embedding_field.py::garbage_collect` reading `os.listdir` vs. `on_save` writing
**Trigger:** `Memory.save()` completes `os.rename` (file appears on disk), `_index.json` write happens, Redis hash write happens — sweep's `os.listdir` snapshot was taken before the rename.
**Data prerequisite:** The disk file exists, the Redis hash exists.
**State prerequisite:** `_index.json` contains the new filename.
**Mitigation:** Skip any file with `mtime` more recent than `time.time() - 60`. New files survive the sweep. Sweep iteration order is not significant.

### Race 2: Delete races with sweep

**Location:** `popoto/fields/embedding_field.py::garbage_collect::os.unlink` vs. `on_delete::os.unlink`
**Trigger:** Two processes both decide a file should be removed.
**Data prerequisite:** None — both want the file gone.
**State prerequisite:** None.
**Mitigation:** `os.unlink` wrapped in try/except that swallows `FileNotFoundError`. Both paths converge on the same end state.

### Race 3: Save creates new file as sweep deletes it

**Location:** `EmbeddingField.on_save` rename vs. concurrent `garbage_collect` unlink, when the same memory_id is re-saved
**Trigger:** Memory.save → atomic rename → sweep reads the file as "doesn't yet have index entry" (millisecond gap) → sweep deletes → save's index update lands → next read fails because file is gone.
**Data prerequisite:** Memory record save and sweep both targeting the same memory_id concurrently.
**State prerequisite:** Worker startup running sweep AT THE SAME TIME as a fresh Memory.save() landing.
**Mitigation:** Two layers. (a) The 60-second mtime guard from Race 1 covers normal save→sweep windows. (b) The sweep checks `_index.json` AND `Memory:_all` — only deletes if BOTH say the file is orphan. Save updates `_index.json` BEFORE the embedding is queryable, so this race window is narrower than the mtime guard already handles.

## No-Gos (Out of Scope)

- Changing the embedding model or vector format.
- Migrating to a different embedding store backend (Qdrant, Weaviate, RediSearch, etc.).
- Adding soft-delete / tombstone semantics to Memory.
- Adding the `--clean-orphans` flag to `status --deep` (split into the dedicated reconcile script).
- Investigating which historical code path leaked the 7,400 files (covered by the sweep reflection going forward).
- Changes to `tools/memory_search/cli.py` beyond what `status --deep` already shows.
- Ollama provider changes.

## Update System

- The `embedding-orphan-sweep` reflection must be added to `config/reflections.yaml` (committed in this repo). The `/update` skill will pick it up via the standard config sync — no update-script changes needed.
- The Popoto bump (`~/src/popoto` commit) needs to be reflected in this repo's `pyproject.toml` if Popoto is pinned to a specific commit. Check `grep popoto /Users/tomcounsell/src/ai/pyproject.toml` — if pinned to a commit hash or version, bump it in the same PR. If installed via `-e ~/src/popoto`, no version bump needed.
- No new env var defaults need propagating in `.env.example`; `EMBEDDING_ORPHAN_SWEEP_APPLY` defaults to false (dry-run). Operators opt in by setting it.
- Migration step for existing installations: documented in PR description — operators run `python scripts/embedding_orphan_reconcile.py --apply` once after pulling, then verify `python -m tools.memory_search status --deep` reports `disk_orphans: 0`.

## Agent Integration

No new agent-facing CLI is required. Both surfaces the agent already uses keep working unchanged:

- `python -m tools.memory_search status --deep` — exits with the new `disk_orphans` field included, no flag changes.
- The `embedding-orphan-sweep` reflection runs in the worker, no agent invocation involved.

The one-shot `scripts/embedding_orphan_reconcile.py` is an operator tool, NOT an agent tool. Do NOT add it to `pyproject.toml [project.scripts]` and do NOT register a slash command for it — it is intentionally manual. Documenting that decision here so future audits don't try to wire it.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/subconscious-memory.md` — add a new "Embedding-File Lifecycle" subsection under "Vector embeddings via Ollama". Document: write path (atomic tmp+rename), delete path (on_delete hook), sweep path (reflection + one-shot), tempfile cleanup behavior. Reference the new file paths.
- [ ] Update `docs/features/README.md` index entry for subconscious-memory if the description mentions lifecycle gaps.

### External Documentation Site

- [ ] No external docs site for this repo. Skip.

### Inline Documentation

- [ ] Docstring on `EmbeddingField.garbage_collect` in Popoto (replace the "Future enhancement" comment with a real description of behavior, mtime guard, return value).
- [ ] Docstring on `EmbeddingField.sweep_stale_tempfiles` in Popoto.
- [ ] Module-level docstring update in `reflections/memory_management.py` to mention `run_embedding_orphan_sweep` alongside the existing reflections.
- [ ] CLI `--help` text for `scripts/embedding_orphan_reconcile.py`.

## Success Criteria

- [ ] Deleting a Memory record removes its `.npy` file in the same operation (verified by `tests/integration/test_memory_lifecycle.py`)
- [ ] Worker startup (or daily reflection) sweeps stale `tmp*.npy` files older than 1 hour (verified by reflection unit test)
- [ ] `python -m tools.memory_search status --deep` reports `disk_orphans` count alongside `orphan_index_keys`
- [ ] One-shot `scripts/embedding_orphan_reconcile.py --apply` reduces existing 7,000+ orphans to ≤10 (count documented in PR description)
- [ ] `pytest tests/integration/test_memory_retrieval.py -k log_silence` shows zero `WARNING Skipping unrecognized embedding file` lines on a clean corpus
- [ ] `docs/features/subconscious-memory.md` has an "Embedding-File Lifecycle" subsection
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -rn "garbage_collect" ~/src/popoto/src/popoto/fields/embedding_field.py | grep -v "Future enhancement"` confirms the stub has been replaced

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (popoto-changes)**
  - Name: popoto-builder
  - Role: Implement `EmbeddingField.garbage_collect` and `sweep_stale_tempfiles` in the vendored Popoto repo at `~/src/popoto`. Add tests there.
  - Agent Type: builder
  - Resume: true

- **Builder (ai-wiring)**
  - Name: ai-builder
  - Role: Add `_count_disk_orphans` to `scripts/popoto_index_cleanup.py`, extend `tools/memory_search/__init__.py::status` to surface `disk_orphans`, add `run_embedding_orphan_sweep` to `reflections/memory_management.py`, register `embedding-orphan-sweep` in `config/reflections.yaml`, lower the log level for "Skipping unrecognized" in Popoto.
  - Agent Type: builder
  - Resume: true

- **Builder (one-shot-script)**
  - Name: script-builder
  - Role: Create `scripts/embedding_orphan_reconcile.py` with dry-run default and `--apply` flag. Include the safety bound described in Risk 3.
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: lifecycle-test-engineer
  - Role: Author the integration test `tests/integration/test_memory_lifecycle.py` and update existing status / retrieval tests per Test Impact.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: orphan-cleanup-validator
  - Role: Run the full verification suite, execute the one-shot reconcile against the actual ~7,000-file corpus on this dev machine in dry-run first, then `--apply`, capture before/after counts. Fail if any success criterion isn't met.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: memory-lifecycle-documentarian
  - Role: Update `docs/features/subconscious-memory.md` with the lifecycle subsection. Update Popoto docstrings inline as part of the build, not as a separate task.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement EmbeddingField garbage collection (Popoto side)

- **Task ID**: build-popoto-gc
- **Depends On**: none
- **Validates**: `~/src/popoto/tests/test_embedding_field_gc.py` (create)
- **Informed By**: Recon Summary (file:line pointers), Research findings on reconciliation pattern
- **Assigned To**: popoto-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `EmbeddingField.garbage_collect()` body in `~/src/popoto/src/popoto/fields/embedding_field.py`
- Add `EmbeddingField.sweep_stale_tempfiles(model_class, max_age_seconds=3600)` classmethod
- Use `POPOTO_REDIS_DB.smembers(f"{model_class.__name__}:_all")` as the live-keys source
- Apply the 60-second mtime guard against the most recent files
- Wrap each `os.unlink` in try/except for `FileNotFoundError` and `OSError` (log + skip)
- Update `_index.json` to remove orphan entries in the same pass
- Lower `Skipping unrecognized embedding file` log level from WARNING to DEBUG in `load_embeddings`
- Add `tests/test_embedding_field_gc.py` covering: orphan removal, mtime guard, missing directory, missing index, tempfile sweep
- Commit and push the Popoto change to its main branch
- Update Popoto pin in this repo's `pyproject.toml` if pinned (check first)

### 2. Wire the sweep into ai/ reflections + status

- **Task ID**: build-ai-wiring
- **Depends On**: build-popoto-gc
- **Validates**: `tests/unit/test_memory_search_status.py` (update), `tests/unit/test_reflections_memory_management.py` (update or create)
- **Assigned To**: ai-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_count_disk_orphans(model_class)` helper to `scripts/popoto_index_cleanup.py`
- Extend `tools/memory_search/__init__.py::status` to call `_count_disk_orphans` when `deep=True` and include `disk_orphans` in the result dict
- Add `run_embedding_orphan_sweep` async function to `reflections/memory_management.py` matching the dry-run/apply pattern of `run_memory_decay_prune`
- Register `embedding-orphan-sweep` in `config/reflections.yaml` with daily cadence and `enabled: true`
- Update unit tests for both `status --deep` output and the new reflection function

### 3. Build one-shot reconcile script

- **Task ID**: build-reconcile-script
- **Depends On**: build-popoto-gc
- **Validates**: `tests/unit/test_embedding_orphan_reconcile.py` (create)
- **Assigned To**: script-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/embedding_orphan_reconcile.py` with `argparse` for `--dry-run` (default), `--apply`, `--max-age-seconds` (default 60 for mtime guard)
- Print live record count, disk file count, would-remove count BEFORE any deletion
- Sanity bound: refuse to apply if `would_remove > (disk_count - live_count + 50)`
- Logs go to stdout in human-readable form for the PR description
- Test mocks 5 live + 50 stray files, asserts `--apply` removes 50 and keeps 5

### 4. Author the lifecycle integration test

- **Task ID**: build-integration-test
- **Depends On**: build-popoto-gc, build-ai-wiring
- **Assigned To**: lifecycle-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/integration/test_memory_lifecycle.py`
- Test: create Memory → assert .npy exists at expected path → delete Memory → assert .npy is gone
- Test: drop a stray .npy with a fake hex name → call `EmbeddingField.garbage_collect(Memory)` → assert it's removed
- Test: drop a `tmp123.npy` with mtime > 1 hour ago → call `sweep_stale_tempfiles` → assert removed
- Test: drop a fresh `.npy` with mtime < 60s → call `garbage_collect` → assert it survives
- Update existing tests per Test Impact section (status --deep test, retrieval log-silence test)

### 5. Validate end-to-end

- **Task ID**: validate-all
- **Depends On**: build-popoto-gc, build-ai-wiring, build-reconcile-script, build-integration-test
- **Assigned To**: orphan-cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q`
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Capture baseline: `ls ~/.popoto/content/.embeddings/Memory/ | wc -l` and `python -m tools.memory_search status --deep`
- Run `python scripts/embedding_orphan_reconcile.py --dry-run` — capture output
- Run `python scripts/embedding_orphan_reconcile.py --apply` — capture output
- Run `python -m tools.memory_search status --deep` again — verify `disk_orphans: 0`
- Run a few `retrieve_memories()` queries and tail logs — verify zero "Skipping unrecognized" warnings
- Generate the validation report with before/after numbers for the PR description

### 6. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: memory-lifecycle-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Add "Embedding-File Lifecycle" subsection to `docs/features/subconscious-memory.md` covering write/delete/sweep paths and the new tools
- Update Popoto docstrings inline (already done in build-popoto-gc, verify content quality)
- Update `docs/features/README.md` index if needed

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lifecycle integration test | `pytest tests/integration/test_memory_lifecycle.py -v` | exit code 0 |
| Disk orphans reconciled | `python -m tools.memory_search status --deep --json \| python -c "import sys,json; d=json.load(sys.stdin); assert d.get('disk_orphans', 999) <= 10"` | exit code 0 |
| garbage_collect implemented | `grep -A 1 "def garbage_collect" ~/src/popoto/src/popoto/fields/embedding_field.py \| grep -v "Future enhancement"` | exit code 0 |
| Reflection registered | `grep "embedding-orphan-sweep" config/reflections.yaml` | exit code 0 |
| One-shot script exists | `test -x scripts/embedding_orphan_reconcile.py` | exit code 0 |
| No "Skipping unrecognized" WARN | `python -c "import logging; logging.basicConfig(level=logging.WARNING); from agent.memory_retrieval import retrieve_memories; retrieve_memories('test', 'default', limit=5)" 2>&1 \| grep "Skipping unrecognized"` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Open Questions

1. **Should `garbage_collect` live in Popoto or as a helper in this repo?** Recommendation: Popoto (where the storage layout is owned). But that means a Popoto bump in the same PR cycle. Confirm direction.
2. **Sweep cadence: daily or hourly?** Recommendation: daily (low-frequency reconciliation matches WarpStream's pattern). Tempfile sweep alone could justify hourly, but the cost of one daily run is trivial.
3. **Should the `embedding-orphan-sweep` reflection default to dry-run (`EMBEDDING_ORPHAN_SWEEP_APPLY=false`) like `memory-decay-prune` does, or default to apply?** Recommendation: dry-run default for the first release, flip to apply default after one week of clean dry-run findings. Confirm.
4. **Mtime guard: 60 seconds or 5 minutes?** Recommendation: 60 seconds — atomic writes complete in milliseconds and 60s is generous. 5 minutes would be safer but slows the catch-up on real orphans during a busy hour.
5. **Should we add a metrics emit (`record_metric("memory.embedding_orphans_swept", N)`) so the dashboard can chart cleanup over time?** Recommendation: yes, low cost; it's the kind of signal that catches a regression in the on_delete hook before it festers into another 7,000-file backlog.
