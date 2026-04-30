---
status: Ready
type: bug
appetite: Medium
owner: Tom Counsell
created: 2026-04-30
revised: 2026-04-30
revision_applied: true
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
- `python -m tools.memory_search status --deep` reports `Orphan index keys: 0` (human label) / `orphan_index_count: 0` (JSON key, set in `tools/memory_search/__init__.py:495`), contradicting the actual disk state. The check only walks the Redis class set, never the disk.

**Desired outcome:**

- Memory deletions remove the `.npy` and the `_index.json` entry in one transaction.
- The daily `embedding-orphan-sweep` reflection sweeps stale `tmp*.npy` files older than 1 hour AND reconciles disk against Redis. (Worker-startup hook is **out of scope** — see N3 resolution; the reflection is the single sweep surface.)
- `status --deep` reports both Redis-side (`orphan_index_count`, existing) and disk-side (`disk_orphan_count`, new) orphans as parallel JSON fields.
- A one-shot reconciliation reduces the existing 7,000+ orphan files to ≤10.
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

- issue 1212 — open, related (extraction-side parser bugs producing JSON shrapnel as memories) — distinct fix, out of scope for this plan
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
- **Interface changes**: `EmbeddingField.garbage_collect()` gains a real implementation (signature unchanged: `(model_class) -> int`). New helper `_count_disk_orphans()` exposed from `scripts/popoto_index_cleanup.py`. `status --deep` adds a `disk_orphan_count` field (parallel to existing `orphan_index_count`).
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
| Embedding provider configured | `python -c "from models.memory import Memory; from popoto.fields.embedding_field import get_default_provider; assert get_default_provider() is not None"` | Required so reading `_index.json` matches the same path the writer uses. Importing `Memory` first triggers `apply_defaults()` which calls `configure_embedding_provider()`; without that import, `get_default_provider()` returns None and the check fails as a false negative (verified locally). |
| Popoto version >= 1.6.0 | `python -c "from popoto.fields.embedding_field import EmbeddingField; assert 'Future enhancement' not in (EmbeddingField.garbage_collect.__doc__ or '')"` | Verifies the installed Popoto has the real `garbage_collect` body (not the stub). The reflection wrapper applies the same check at runtime as a defensive guard. |
| pytest available | `python -m pytest --version` | Needed for the integration test in success criteria |

Run all checks: `python scripts/check_prerequisites.py docs/plans/memory_embedding_orphan_cleanup.md`

## Solution

### Filename Scheme (CRITICAL — read first)

Embedding filenames are **SHA-256 hashes of the Redis key**, not hex-encoded keys. From `popoto/fields/embedding_field.py:189`:

```python
hash_key = hashlib.sha256(redis_key.encode("utf-8")).hexdigest()
# {hash_key}.npy
```

SHA-256 is one-way — you **cannot** decode a filename back to a Redis key. Reconciliation must therefore work in the forward direction: enumerate live keys, compute their expected hashed filenames, treat anything else as orphan. A `_legacy_embedding_path` (line 193) exists from a pre-SHA-256 hex era and is migrated on save; some of the 7,400+ orphans on disk may be unmigrated legacy hex files (see N1).

### Key Elements

- **`EmbeddingField.garbage_collect(Memory)`** in Popoto: real implementation. Computes `expected_keep = {sha256(k).hexdigest() + ".npy" for k in POPOTO_REDIS_DB.smembers("Memory:_all")}`, walks `~/.popoto/content/.embeddings/Memory/`, deletes any file (a) not in `expected_keep`, (b) not a current `tmp*.npy` (handled by separate sweep), and (c) older than the mtime guard. Returns count removed.
- **`EmbeddingField.sweep_stale_tempfiles(Memory, max_age_seconds=3600)`** in Popoto: removes `tmp*.npy` files older than the cutoff. Returns count removed.
- **`reflections/memory_management.py::run_embedding_orphan_sweep`** in this repo: thin wrapper that calls both Popoto methods, with dry-run gating via `EMBEDDING_ORPHAN_SWEEP_APPLY` env var. Registered in `config/reflections.yaml` as `embedding-orphan-sweep` (daily). Includes a runtime guard that detects the Popoto stub via docstring inspection and skips the sweep with a "popoto<1.6 — gc not implemented" warning if encountered (C4).
- **`scripts/popoto_index_cleanup.py::_count_disk_orphans(Memory)`** in this repo: walks the embedding directory, computes `expected_keep` from `Memory:_all`, returns `len(disk_files - expected_keep - tmp_files)`. Pure read-only.
- **`tools/memory_search/__init__.py::status(deep=True)`**: extended to call `_count_disk_orphans()` and report **`disk_orphan_count`** (and optionally `disk_orphan_paths` capped at 5 examples) alongside the existing **`orphan_index_count`**. Field name parallel to existing convention; existing `orphan_index_count` is preserved verbatim (B3).
- **`scripts/embedding_orphan_reconcile.py`** in this repo: one-shot CLI for the existing 7,000+ files. Dry-run by default; `--apply` actually deletes. Includes a positive-assertion safety check: before any deletion, asserts that the to-delete set has empty intersection with `expected_keep` (live-record filenames). Refuses to apply if any live file appears in the to-delete set (C5). Logs counts to stdout for the PR description.
- **Log noise reduction**: lower `Skipping unrecognized embedding file` from WARNING to DEBUG in Popoto's `load_embeddings`. After reconciliation it should never fire on a clean corpus, but defense-in-depth.

### Flow

**Operator runs cleanup once:**
`scripts/embedding_orphan_reconcile.py --dry-run` → see "would remove N files" → `--apply` → "removed N files" → check `python -m tools.memory_search status --deep` shows `disk_orphan_count: 0`

**Ongoing prevention:**
Memory created → on_save writes .npy + index entry → Memory deleted → on_delete removes .npy + index entry → daily `embedding-orphan-sweep` reflection runs garbage_collect + sweep_stale_tempfiles → if any orphans found (concurrent crash, raw Redis op), they are removed → `status --deep` shows `disk_orphan_count: 0`

### Technical Approach

- **Popoto changes are minimal and additive.** `garbage_collect` has the right signature already; we just write the body. `sweep_stale_tempfiles` is a new classmethod, also small. Both go in `popoto/fields/embedding_field.py` next to the existing `on_delete`.
- **Source of truth for "live" is the Redis class set `<ModelName>:_all`**, not `_index.json`. The index is a derived cache. We compute `expected_keep` directly from the Redis set via SHA-256 hashing — see Filename Scheme above. `_index.json` is reconciled separately by removing entries whose filename is not in `expected_keep`, but it is never trusted as a source of truth.
- **Reading Redis from inside Popoto**: `EmbeddingField.garbage_collect` already takes `model_class`; we use `POPOTO_REDIS_DB.smembers(f"{model_class.__name__}:_all")` which is the same access pattern used by `_count_orphans` in this repo.
- **Concurrency safety**: a parallel write that creates a new `.npy` while the sweep is running is safe — the sweep snapshots the directory listing at the start, and any new file appearing after that snapshot is not visited. The only hazard is deleting a file just as another process re-creates it; `os.unlink` with `FileNotFoundError` swallow handles the inverse race.
- **Mtime guard is 5 minutes (300 seconds), not 60 seconds.** The save path order in `embedding_field.py::on_save` is: atomic `os.rename` of the `.npy` (line 291) → `_read_index → mutate → _write_index` (line 300-302) → Redis `hset` of dimension count (line 327-331). Between rename and the Redis class-set update, the file exists on disk but is in NEITHER `_index.json` (briefly) NOR `Memory:_all`. Each save also calls Ollama (network round-trip; typically <5s but pathologically up to ~30s on retry). 60s is too tight for retried saves; 5 minutes covers timeout/retry pathologies. The mtime guard is the **only** real race protection; checking BOTH `_index.json` AND `Memory:_all` reduces false positives but cannot eliminate the rename-first race window (C1).
- **Dry-run is the default for the reflection.** `EMBEDDING_ORPHAN_SWEEP_APPLY=true` env var (matching the existing `MEMORY_DECAY_PRUNE_APPLY` pattern in `memory_management.py`) gates actual deletion. This matches the established prevention-over-cleanup pattern in this codebase.
- **One-shot script also defaults to dry-run.** This is the operator's deliberate first run; `--apply` only after dry-run output is reviewed.
- **Tempfile sweep cutoff is 1 hour.** Atomic writes complete in milliseconds. A 1-hour cutoff is conservative — anything older is unambiguously a leak.
- **The `_has_embedding_field()` skip in `popoto_index_cleanup._get_all_models()` stays.** That skip exists because `rebuild_indexes()` would re-trigger Ollama embed calls. Our new sweep does NOT call `rebuild_indexes`; it only deletes orphans. So we add Memory back via a dedicated path, not by removing the skip.
- **Popoto version coordination (C4).** This repo pins `popoto>=1.5.0` (`pyproject.toml:17`) and the live install is from PyPI (`/Users/tomcounsell/src/ai/.venv/lib/python3.14/site-packages/popoto/`), NOT an editable install of `~/src/popoto`. The new `garbage_collect` body must therefore (a) be cut into a Popoto release (bump to 1.6.0), (b) bump this repo's pin to `popoto>=1.6.0`, and (c) the new reflection must defensively detect the stub via `"Future enhancement" in (EmbeddingField.garbage_collect.__doc__ or "")` and short-circuit with a clear warning. This guard means staged rollout (Popoto release first, ai/ pin bump second) doesn't crash machines that haven't pulled yet.

### Reflection Registry Entry

The full YAML block to add to `config/reflections.yaml` (matching the schema documented at the top of that file):

```yaml
- name: embedding-orphan-sweep
  description: "Reconcile on-disk Memory embeddings against Redis class set; sweep stale tempfiles"
  interval: 86400  # daily
  priority: low
  execution_type: function
  callable: "reflections.memory_management.run_embedding_orphan_sweep"
  enabled: true
```

All six required fields (`name`, `description`, `interval`, `priority`, `execution_type`, `callable`) plus `enabled` are specified, matching the convention of `agent-session-cleanup` and `redis-index-cleanup` (C2).

### Metrics Emission (resolves Open Question #5)

`run_embedding_orphan_sweep` emits two metrics per run via the existing `record_metric` helper:
- `memory.embedding_orphans_swept` — count of orphan files removed (or would-remove count in dry-run)
- `memory.embedding_tempfiles_swept` — count of stale `tmp*.npy` files removed

These let the dashboard chart cleanup activity over time, catching regressions in the `on_delete` hook before they accumulate into another 7,000-file backlog (N2).

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
- [ ] Missing `_index.json` (fresh install): `garbage_collect` ignores the index entirely and reconciles disk against `expected_keep` derived from Redis. Test exercises this path. (Note: filenames are SHA-256 hashes of Redis keys — no hex-decoding fallback exists. Pre-migration legacy hex files on disk are treated as orphans and removed.)

### Error State Rendering

- [ ] One-shot CLI: when `--apply` runs and partially fails (some unlinks succeed, some fail), output reports both counts. Test mocks half the files to raise `OSError`.
- [ ] Reflection result: `status: "ok" | "error"`, `findings: [...]`, `summary: "..."` matching the existing `run_memory_decay_prune` shape.

## Test Impact

Verified test-file paths via `find tests -name "*memory*" -o -name "*popoto*"`. Updated dispositions to match files that actually exist on disk.

- [ ] `tests/unit/test_memory_search_cli.py` (existing) — UPDATE: extend the `--deep` test to assert `disk_orphan_count` (parallel to the existing `orphan_index_count`) is present in the JSON output.
- [ ] `tests/unit/test_popoto_cleanup_reflection.py` (existing) — UPDATE: assert that adding a `_count_disk_orphans` helper does not break the existing `_count_orphans` Redis-side scan; assert the new helper handles a missing embeddings directory by returning 0 (not raising).
- [ ] `tests/integration/test_memory_lifecycle.py` (NEW) — CREATE: integration test that creates a Memory, asserts the SHA-256-hashed `.npy` exists at `_embedding_path`; deletes the Memory, asserts the `.npy` is gone; manually drops a stray file with a non-live SHA-256 name, runs `garbage_collect`, asserts cleanup; drops a `tmp*.npy` with mtime > 1 hour ago, runs `sweep_stale_tempfiles`, asserts removal.
- [ ] `tests/unit/test_memory_retrieval.py` (existing — note: `unit/`, not `integration/`) — UPDATE: capture log output via `caplog` during `retrieve_memories`, assert no WARNING-level `Skipping unrecognized embedding file` lines after a clean fixture setup. The Popoto log-level downgrade to DEBUG is the mechanism; this test verifies the user-visible signal.
- [ ] `tests/unit/test_embedding_orphan_reconcile.py` (NEW) — CREATE: covers the one-shot script's positive-assertion safety check (refuses to apply if any `expected_keep` filename appears in to-delete set), dry-run vs `--apply` behavior, and the live=5 / stray=50 fixture scenario from Risk 3.
- [ ] Popoto: add `tests/test_embedding_field_gc.py` in `~/src/popoto` covering `garbage_collect` (orphan removal, mtime guard, `expected_keep` correctness, missing directory) and `sweep_stale_tempfiles` (age threshold, missing directory) against a temp directory. Required for the Popoto 1.6.0 release.

## Rabbit Holes

- **Adding a "soft delete" / tombstone column to Memory.** The recon already established Memory has no soft-delete and the orphans are pure garbage. Don't introduce one.
- **Rewriting `EmbeddingField` to use a single combined index file.** The existing `_index.json` is fine; the bug is missing reconciliation, not the data structure.
- **Migrating to Redis-native vector storage (RediSearch FT.SEARCH).** Out of scope per the issue, and would replace the entire `EmbeddingField` rather than fix it.
- **Adding a `--clean-orphans` interactive flag to `status --deep`.** The issue suggests this, but it conflates "report" with "act". Keep `status` read-only and put apply behavior in the dedicated `embedding_orphan_reconcile.py` script.
- **Investigating which historical code path bypassed `Memory.delete()`** to leave 7,400 orphans. Worth one paragraph of research, but not worth chasing — git archaeology on a Redis-only deletion path is unlikely to find a smoking gun, and the prevention (sweep reflection) catches it regardless of historical source.

## Risks

### Risk 1: Sweep deletes files that a concurrent process is mid-writing

**Impact:** A `Memory.save()` that has just landed an atomic `.npy` could (in theory) have its file removed if the sweep snapshot was taken between `os.rename` and the Redis hash write.
**Mitigation:** Skip any disk file whose mtime is within the last **5 minutes (300 seconds)**. Save path order in `embedding_field.py::on_save` is rename (line 291) → `_index.json` mutation (line 300-302) → Ollama embed call (typically <5s, pathologically up to ~30s on retry) → Redis `hset` of dimension count (line 327-331). The mtime guard is the **only** real race protection — checking `_index.json` and `Memory:_all` after the fact reduces false positives but cannot eliminate the rename-first window. 5 minutes covers Ollama timeout/retry pathologies; 60 seconds was too tight.

### Risk 2: Popoto change breaks unrelated models that also use EmbeddingField

**Impact:** Other consumers of `EmbeddingField.garbage_collect` (currently zero, but could grow) would suddenly get real deletion behavior instead of a no-op.
**Mitigation:** The signature is unchanged and the docstring already says "Remove orphaned .npy files". Any caller of the stub today gets the documented behavior; no caller should be relying on the no-op result. Test in Popoto's own suite covers correct behavior. Communicate the Popoto bump in this repo's PR description.

### Risk 4: Popoto release lag — machines run reflection before Popoto 1.6.0 is installed

**Impact:** The new reflection in this repo lands before Popoto 1.6.0 is published / pulled on every machine. The reflection calls `EmbeddingField.garbage_collect`, which still returns 0 (stub) — no harm done, but operators see "swept 0 orphans" daily and may believe the reconciliation is working when it isn't.
**Mitigation:** Defensive runtime check inside `run_embedding_orphan_sweep`:

```python
if "Future enhancement" in (EmbeddingField.garbage_collect.__doc__ or ""):
    logger.warning("popoto-embedding-gc-stub-detected — install popoto>=1.6.0")
    return {"status": "ok", "findings": ["popoto<1.6 — gc not implemented yet"], "summary": "skipped"}
```

This ensures the reflection emits a clear "skipped" status when the Popoto stub is still active, rather than silently appearing to succeed. Pair with the Verification table check that asserts `popoto>=1.6.0` is installed.

### Risk 3: One-shot reconcile deletes 7,000 files in production embedding directory

**Impact:** If the script is buggy (e.g., inverted-logic computing "orphan" as the wrong set), we could remove embeddings for live records, silently downgrading recall quality.
**Mitigation:** Default is dry-run. Apply requires `--apply`. **Positive-assertion safety check** (replacing the previous heuristic numeric bound, which only caught over-deletion beyond orphan-count and could not catch inverted logic):

```python
# Compute filenames that MUST survive
expected_keep = {
    hashlib.sha256(k.decode() if isinstance(k, bytes) else k).hexdigest() + ".npy"
    for k in POPOTO_REDIS_DB.smembers("Memory:_all")
}
to_delete = set(orphan_filenames)
collision = expected_keep & to_delete
if collision:
    sys.exit(f"REFUSE: would delete {len(collision)} live-record files (sample: {list(collision)[:5]})")
```

This catches inverted-orphan-set bugs deterministically, not heuristically. Add an integration test that creates 5 live Memory records + 50 stray .npy files, runs `--apply`, asserts the 5 live files survive.

## Race Conditions

### Race 1: Save lands while sweep is iterating

**Location:** `popoto/fields/embedding_field.py::garbage_collect` reading `os.listdir` vs. `on_save` writing
**Trigger:** `Memory.save()` completes `os.rename` (file appears on disk), `_index.json` write happens, Redis hash write happens — sweep's `os.listdir` snapshot was taken before the rename.
**Data prerequisite:** The disk file exists, the Redis hash exists.
**State prerequisite:** `_index.json` contains the new filename.
**Mitigation:** Skip any file with `mtime` more recent than `time.time() - MIN_AGE` where `MIN_AGE = 300` (5 minutes). New files survive the sweep. Sweep iteration order is not significant. Implementation: `if (time.time() - os.stat(path).st_mtime) < MIN_AGE: continue`.

### Race 2: Delete races with sweep

**Location:** `popoto/fields/embedding_field.py::garbage_collect::os.unlink` vs. `on_delete::os.unlink`
**Trigger:** Two processes both decide a file should be removed.
**Data prerequisite:** None — both want the file gone.
**State prerequisite:** None.
**Mitigation:** `os.unlink` wrapped in try/except that swallows `FileNotFoundError`. Both paths converge on the same end state.

### Race 3: Save creates new file as sweep deletes it

**Location:** `EmbeddingField.on_save` rename vs. concurrent `garbage_collect` unlink, when the same memory_id is re-saved
**Trigger:** Memory.save → atomic rename → Ollama embed call (network, possibly retried) → `_index.json` write → Redis class-set update. During the rename→Redis-update window, the file is on disk but in NEITHER `_index.json` nor `Memory:_all`. A sweep iterating concurrently could classify the new file as orphan.
**Data prerequisite:** Memory record save and sweep both targeting the same memory_id concurrently.
**State prerequisite:** Sweep running AT THE SAME TIME as a fresh `Memory.save()` landing.
**Mitigation:** The 5-minute mtime guard from Race 1 is the **only** real protection — checking `_index.json` AND `Memory:_all` reduces false positives but cannot eliminate the rename-first window because both checks return "missing" during the gap. The previous version of this section incorrectly claimed "save updates `_index.json` before the embedding is queryable, so the window is narrower than the mtime guard"; that ordering is wrong (rename happens first). Drop that reasoning. The mtime guard alone bounds the race; index/class-set checks are belt-and-suspenders.

## No-Gos (Out of Scope)

- Changing the embedding model or vector format.
- Migrating to a different embedding store backend (Qdrant, Weaviate, RediSearch, etc.).
- Adding soft-delete / tombstone semantics to Memory.
- Adding the `--clean-orphans` flag to `status --deep` (split into the dedicated reconcile script).
- Investigating which historical code path leaked the 7,400 files (covered by the sweep reflection going forward).
- Changes to `tools/memory_search/cli.py` beyond what `status --deep` already shows.
- Ollama provider changes.

## Update System

This plan requires **staged release coordination** across two repos. Order matters.

**Stage 1 — Popoto release (must ship first):**

1. Implement `EmbeddingField.garbage_collect` and `sweep_stale_tempfiles` in `~/src/popoto/src/popoto/fields/embedding_field.py`
2. Add tests in `~/src/popoto/tests/test_embedding_field_gc.py`
3. Bump Popoto version: edit `~/src/popoto/pyproject.toml` from `version = "1.5.0"` to `version = "1.6.0"`
4. Cut release: tag, push, publish to PyPI (or whatever distribution channel is active for this Popoto)
5. Verify on this machine: `pip install -U popoto` then `python -c "import popoto.fields.embedding_field as e; assert 'Future enhancement' not in (e.EmbeddingField.garbage_collect.__doc__ or '')"`

**Stage 2 — ai/ repo PR (depends on Stage 1):**

1. Bump pin: edit `pyproject.toml:17` from `popoto>=1.5.0` to `popoto>=1.6.0`
2. Add `embedding-orphan-sweep` to `config/reflections.yaml` (full block in Solution / Reflection Registry Entry)
3. Add the new files (`scripts/embedding_orphan_reconcile.py`, etc.)
4. The `/update` skill will pick up the reflections.yaml change via the standard config sync
5. Per-machine `pip install` of the new Popoto version is required — the `/update` skill currently runs `pip install -U -r requirements.txt` (or equivalent); confirm that picks up `popoto>=1.6.0` from the bumped pin. If `/update` does not currently re-install dependencies, add a step to do so for this release.

**Defense-in-depth:** The reflection wrapper detects the Popoto stub via docstring inspection and skips with a clear warning if encountered (Risk 4). Machines that haven't pulled Popoto 1.6.0 yet will see "popoto<1.6 — gc not implemented yet" in reflection output rather than silent no-ops.

**Env vars:** No new defaults propagate in `.env.example`; `EMBEDDING_ORPHAN_SWEEP_APPLY` defaults to false (dry-run). Operators opt in by setting it.

**Migration step for existing installations:** documented in PR description — after both stages are deployed, operators run `python scripts/embedding_orphan_reconcile.py --apply` once, then verify `python -m tools.memory_search status --deep` reports `disk_orphan_count: 0` (or near-zero — the 5-minute mtime guard means very recently created orphans will linger one cycle).

## Agent Integration

No new agent-facing CLI is required. Both surfaces the agent already uses keep working unchanged:

- `python -m tools.memory_search status --deep` — exits with the new `disk_orphan_count` field included (parallel to existing `orphan_index_count`), no flag changes.
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

- [ ] Deleting a Memory record removes its SHA-256-hashed `.npy` file in the same operation (verified by `tests/integration/test_memory_lifecycle.py`)
- [ ] The daily `embedding-orphan-sweep` reflection sweeps stale `tmp*.npy` files older than 1 hour (verified by `run_embedding_orphan_sweep` unit test)
- [ ] `python -m tools.memory_search status --deep --json` reports `disk_orphan_count` (new) alongside the existing `orphan_index_count`
- [ ] One-shot `scripts/embedding_orphan_reconcile.py --apply` reduces existing 7,000+ orphans to ≤10 (count documented in PR description)
- [ ] `pytest tests/unit/test_memory_retrieval.py -k log_silence` shows zero WARNING-level `Skipping unrecognized embedding file` lines on a clean corpus
- [ ] `docs/features/subconscious-memory.md` has an "Embedding-File Lifecycle" subsection
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] Popoto 1.6.0 released and `pyproject.toml` pin bumped to `popoto>=1.6.0`
- [ ] `python -c "from popoto.fields.embedding_field import EmbeddingField; assert 'Future enhancement' not in (EmbeddingField.garbage_collect.__doc__ or '')"` exits 0 (stub replaced)
- [ ] `run_embedding_orphan_sweep` short-circuits with a clear "skipped" status when the Popoto stub is detected (defensive guard, verified by unit test)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (popoto-changes)**
  - Name: popoto-builder
  - Role: Implement `EmbeddingField.garbage_collect` (using SHA-256 forward-hashing, NOT hex-decoding) and `sweep_stale_tempfiles` in the Popoto repo at `~/src/popoto`. Apply 5-minute mtime guard. Update the `garbage_collect` docstring to remove "Future enhancement" (the runtime stub-detection guard depends on this). Add `tests/test_embedding_field_gc.py`. Bump version to 1.6.0 in `~/src/popoto/pyproject.toml` and cut the release.
  - Agent Type: builder
  - Resume: true

- **Builder (ai-wiring)**
  - Name: ai-builder
  - Role: Add `_count_disk_orphans` to `scripts/popoto_index_cleanup.py`, extend `tools/memory_search/__init__.py::status` to surface `disk_orphan_count` (parallel to `orphan_index_count`), add `run_embedding_orphan_sweep` to `reflections/memory_management.py` with Popoto-stub-detection guard, register `embedding-orphan-sweep` in `config/reflections.yaml` (full YAML block), bump `pyproject.toml` Popoto pin to >=1.6.0.
  - Agent Type: builder
  - Resume: true

- **Builder (one-shot-script)**
  - Name: script-builder
  - Role: Create `scripts/embedding_orphan_reconcile.py` with dry-run default and `--apply` flag. Include the **positive-assertion safety check** described in Risk 3 (assert `expected_keep & to_delete == set()`); do NOT use the previous heuristic numeric bound.
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

### 1. Implement EmbeddingField garbage collection (Popoto side, release as 1.6.0)

- **Task ID**: build-popoto-gc
- **Depends On**: none
- **Validates**: `~/src/popoto/tests/test_embedding_field_gc.py` (create)
- **Informed By**: Recon Summary (file:line pointers), Research findings on reconciliation pattern
- **Assigned To**: popoto-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `EmbeddingField.garbage_collect()` body in `~/src/popoto/src/popoto/fields/embedding_field.py`
- Compute `expected_keep = {sha256(k.decode() if isinstance(k,bytes) else k).hexdigest() + ".npy" for k in POPOTO_REDIS_DB.smembers(f"{model_class.__name__}:_all")}` — DO NOT attempt to hex-decode filenames (SHA-256 is one-way) (B1)
- For each `f in os.listdir(emb_dir)` ending in `.npy`: orphan iff `f not in expected_keep` AND not a `tmp*.npy` (handled by separate sweep)
- Apply the **5-minute (300s) mtime guard** before unlink: `if (time.time() - os.stat(path).st_mtime) < 300: continue` (C1)
- Add `EmbeddingField.sweep_stale_tempfiles(model_class, max_age_seconds=3600)` classmethod for `tmp*.npy` cleanup
- Wrap each `os.unlink` in try/except for `FileNotFoundError` and `OSError` (log + skip)
- Update `_index.json` to remove entries whose filename is not in `expected_keep`, in the same pass — `_index.json` is reconciled by intersection, never used as source of truth
- Lower `Skipping unrecognized embedding file` log level from WARNING to DEBUG in `load_embeddings`
- Update the `garbage_collect` docstring — replace "Future enhancement" with a real description of behavior, mtime guard, and return value (the runtime stub-detection guard relies on this docstring change)
- Add `tests/test_embedding_field_gc.py` covering: orphan removal, mtime guard (5min), `expected_keep` correctness, missing directory, missing index, tempfile sweep (1hr)
- Bump Popoto version: `~/src/popoto/pyproject.toml` from `1.5.0` to `1.6.0`
- Commit, push, and cut the Popoto 1.6.0 release (tag + publish)

### 2. Wire the sweep into ai/ reflections + status

- **Task ID**: build-ai-wiring
- **Depends On**: build-popoto-gc
- **Validates**: `tests/unit/test_memory_search_cli.py` (update), `tests/unit/test_popoto_cleanup_reflection.py` (update)
- **Assigned To**: ai-builder
- **Agent Type**: builder
- **Parallel**: false
- Bump `pyproject.toml:17` from `popoto>=1.5.0` to `popoto>=1.6.0`
- Add `_count_disk_orphans(model_class)` helper to `scripts/popoto_index_cleanup.py` — computes `expected_keep` via SHA-256 from `Memory:_all`, returns `len(disk_files - expected_keep - tmp_files)`; returns 0 if directory is missing
- Extend `tools/memory_search/__init__.py::status(deep=True)` to call `_count_disk_orphans` and include **`disk_orphan_count`** (parallel to existing `orphan_index_count` — DO NOT rename the existing key) and optionally `disk_orphan_paths` (capped at 5 examples) in the result dict (B3)
- Add `run_embedding_orphan_sweep` async function to `reflections/memory_management.py` matching the dry-run/apply pattern of `run_memory_decay_prune`. Include the Popoto stub-detection guard at the top: `if "Future enhancement" in (EmbeddingField.garbage_collect.__doc__ or ""): logger.warning(...); return {"status": "ok", "findings": ["popoto<1.6 — gc not implemented yet"], "summary": "skipped"}` (C4 / Risk 4)
- Emit metrics via `record_metric("memory.embedding_orphans_swept", N)` and `record_metric("memory.embedding_tempfiles_swept", M)` (resolves N2)
- Register `embedding-orphan-sweep` in `config/reflections.yaml` using the full YAML block from Solution / Reflection Registry Entry (all six required fields plus `enabled: true`) (C2)
- Update `tests/unit/test_memory_search_cli.py` to assert `disk_orphan_count` field is present in `--deep --json` output
- Update `tests/unit/test_popoto_cleanup_reflection.py` to assert `_count_disk_orphans` exists and handles a missing embeddings directory by returning 0
- Add a unit test for `run_embedding_orphan_sweep` covering the stub-detection short-circuit and the dry-run/apply switch

### 3. Build one-shot reconcile script

- **Task ID**: build-reconcile-script
- **Depends On**: build-popoto-gc
- **Validates**: `tests/unit/test_embedding_orphan_reconcile.py` (create)
- **Assigned To**: script-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/embedding_orphan_reconcile.py` with `argparse` for `--dry-run` (default), `--apply`, `--min-age-seconds` (default 300 for mtime guard) (C1)
- Print live record count, disk file count, would-remove count BEFORE any deletion
- **Positive-assertion safety check** (C5): compute `expected_keep` from `Memory:_all`, assert `expected_keep & to_delete == set()`. If the intersection is non-empty, exit non-zero with a sample of misclassified filenames. This catches inverted-logic bugs deterministically; do NOT use the previous heuristic numeric bound.
- Logs go to stdout in human-readable form for the PR description
- Test mocks 5 live + 50 stray files, asserts `--apply` removes the 50 and keeps the 5
- Test asserts that if a stray-set list is mutated to include a live filename, `--apply` refuses to run

### 4. Author the lifecycle integration test

- **Task ID**: build-integration-test
- **Depends On**: build-popoto-gc, build-ai-wiring
- **Assigned To**: lifecycle-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/integration/test_memory_lifecycle.py`
- Test: create Memory → assert SHA-256-hashed `.npy` exists at `EmbeddingField._embedding_path(...)` → delete Memory → assert the `.npy` is gone
- Test: drop a stray `.npy` with a non-live SHA-256 name → call `EmbeddingField.garbage_collect(Memory)` → assert it's removed
- Test: drop a `tmp123.npy` with mtime > 1 hour ago → call `sweep_stale_tempfiles` → assert removed
- Test: drop a fresh `.npy` with mtime < 300s (5 min mtime guard) → call `garbage_collect` → assert it survives
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
- Run `python -m tools.memory_search status --deep --json` again — verify `disk_orphan_count` is 0 (or near-zero — files within the 5-minute mtime guard window will linger one cycle)
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
| Disk orphans reconciled | `python -m tools.memory_search status --deep --json \| python -c "import sys,json; d=json.load(sys.stdin); assert d.get('disk_orphan_count', 999) <= 10"` | exit code 0 |
| Popoto >= 1.6.0 installed | `python -c "from popoto.fields.embedding_field import EmbeddingField; assert 'Future enhancement' not in (EmbeddingField.garbage_collect.__doc__ or '')"` | exit code 0 |
| Reflection registered | `grep "embedding-orphan-sweep" config/reflections.yaml` | exit code 0 |
| One-shot script exists | `test -f scripts/embedding_orphan_reconcile.py` | exit code 0 |
| No "Skipping unrecognized" WARN | `pytest tests/unit/test_memory_retrieval.py -k log_silence -q` | exit code 0 |

## Critique Results

Round 1 critique (NEEDS REVISION — 3 blockers, 5 concerns, 3 nits) addressed by this revision pass:

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER (B1) | Skeptic, Adversary, Consistency Auditor | Plan misnames file scheme as hex; actual is SHA-256 (one-way) | Solution / Filename Scheme + Technical Approach + Step 1 + Test 4 | Compute `expected_keep = {sha256(k).hexdigest() + ".npy" for k in Memory:_all}`; never hex-decode a filename (one-way function) |
| BLOCKER (B2) | Skeptic, Operator, Consistency Auditor | Test Impact references nonexistent test files | Test Impact rewritten with verified paths | Replacements: `test_memory_search_cli.py`, `test_popoto_cleanup_reflection.py`, `test_memory_retrieval.py` (unit, not integration); plus NEW `test_memory_lifecycle.py` and `test_embedding_orphan_reconcile.py` |
| BLOCKER (B3) | Adversary, Consistency Auditor | Field name mismatch: plan says `disk_orphans`/`orphan_index_keys`; live key is `orphan_index_count` | Solution Key Elements + Test Impact + Verification + Success Criteria + Step 2 | Add `disk_orphan_count` parallel to existing `orphan_index_count`; do NOT rename existing key (would break #964/#970 consumers) |
| CONCERN (C1) | Adversary, Skeptic | Race-3 mitigation reasoning was wrong; rename happens BEFORE index update | Technical Approach + Race Conditions + Risk 1 | Mtime guard raised from 60s to 300s; mtime guard is the ONLY race protection — `_index.json`/`Memory:_all` checks reduce false positives but cannot eliminate the rename-first window |
| CONCERN (C2) | Operator, Consistency Auditor | Reflection YAML schema fields under-specified | Solution / Reflection Registry Entry | Full YAML block specified with all six required fields plus `enabled`, matching `agent-session-cleanup` convention |
| CONCERN (C3) | Skeptic, Operator | Prerequisite #3 fails as written — `get_default_provider()` returns None without `models.memory` import | Prerequisites table | Updated check command imports `Memory` first to trigger `apply_defaults()` → `configure_embedding_provider()`; added Popoto>=1.6.0 prereq |
| CONCERN (C4) | Operator, Archaeologist | Popoto release/version coordination hand-waved; live install is PyPI 1.4.4, not editable `~/src/popoto` | Update System (staged release) + Risk 4 + Solution Technical Approach | Popoto cuts 1.6.0 release first; ai/ pin bumps to `popoto>=1.6.0`; reflection includes runtime stub-detection guard via docstring inspection |
| CONCERN (C5) | Adversary, Operator | One-shot script's heuristic numeric bound permits inverted-logic deletion of every live file | Solution Key Elements + Risk 3 + Step 3 | Replaced with positive-assertion safety check: `assert (expected_keep & to_delete) == set()`; refuses to apply on any collision with deterministic error message |
| NIT (N1) | Archaeologist | Pre-SHA-256 hex migration history worth one sentence | Solution / Filename Scheme paragraph | Notes the existence of `_legacy_embedding_path` and that some orphans may be pre-migration hex files |
| NIT (N2) | Operator | Open Question #5 (metrics emit) should be resolved before build | Solution / Metrics Emission section | Resolved YES; emits `memory.embedding_orphans_swept` and `memory.embedding_tempfiles_swept` in `run_embedding_orphan_sweep` |
| NIT (N3) | User | "Worker startup sweeps" claim diluted to "or daily reflection"; no startup hook in tasks | Desired outcome rewritten + Open Questions resolved | Worker-startup hook is OUT OF SCOPE; the reflection is the single sweep surface. Honest narrative throughout. |

---

## Open Questions (resolved)

All five original open questions have been resolved by this revision pass and the critique:

1. **Should `garbage_collect` live in Popoto or as a helper in this repo?** **Resolved: Popoto.** The critique implicitly accepted the planner's recommendation. C4 makes the staged release explicit (Popoto 1.6.0 first, ai/ pin bump second).
2. **Sweep cadence: daily or hourly?** **Resolved: daily.** Matches WarpStream's reconciliation pattern. Reflected in the YAML block (`interval: 86400`).
3. **Reflection default: dry-run or apply?** **Resolved: dry-run for first release** (`EMBEDDING_ORPHAN_SWEEP_APPLY=false`), flip to apply default after one week of clean dry-run findings.
4. **Mtime guard: 60s or 5 minutes?** **Resolved: 5 minutes (300s).** Critique C1 surfaced that 60s is too tight for retried Ollama saves; bumped to 300s.
5. **Metrics emit?** **Resolved: yes.** N2 flagged this as needing resolution before build. `record_metric` calls now specified in Step 2 and Solution / Metrics Emission.
