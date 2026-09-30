---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-05
tracking: https://github.com/tomcounsell/ai/issues/1904
last_comment_id:
---

# Graceful embedding degradation: persist Memory records when the embedding provider fails

## Problem

Human Telegram messages are ingested as `Memory` records at importance 6.0. When the embedding provider (local Ollama, 5s read timeout) is slow or unreachable — the exact condition observed under concurrent load during #1900 baseline triage — the memory silently vanishes.

**Current behavior:**
- `OllamaEmbeddingProvider.embed()` raises `RuntimeError` on a timeout (`agent/embedding_provider.py:99-100`).
- popoto's `EmbeddingField.on_save` re-raises it as `RuntimeError` (`.venv/.../popoto/fields/embedding_field.py:301-309`).
- That exception propagates inside popoto's `Model.save()` field loop, which runs **before** `internal_pipeline.execute()` (`base.py:1389-1418`). The queued main `hset` never commits, so the entire record — content, BM25 index, relevance — is lost.
- `Memory.safe_save`'s blanket `except Exception` swallows the error and returns `None` (`models/memory.py:206`), logging only `Memory save failed (non-fatal)`. No alert, no record.

A memory with no vector is degraded (loses one of four recall signals). A memory that never persisted is data loss.

**Desired outcome:**
- Embedding-provider timeout/failure during `Memory.save()`/`safe_save()` still persists the record; it is saved without an embedding.
- Recall paths tolerate records with missing embeddings (already true by design — verified below; this plan adds a regression test).
- A backfill reflection re-embeds records that were saved without a vector once the provider is healthy again.

## Freshness Check

**Baseline commit:** `a87b11da0c43c9050cd181a946bbd8e30e0f5e1a`
**Issue filed at:** 2026-07-05T12:59:58Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.venv/.../popoto/fields/embedding_field.py:301-309` — provider error re-raised as `RuntimeError` — still holds.
- `models/memory.py:206` — `safe_save` blanket `except Exception` returns `None` — still holds (read directly).
- `agent/memory_retrieval.py:199-216` — recall guards for missing provider / empty embedding matrix — still holds.

**Cited sibling issues/PRs re-checked:** Issue references #1900 / PR #1903 (baseline triage where this was found) and #1876 (KnowledgeDocument re-embed precedent). All landed; none touched the Memory write path.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since` over `models/memory.py`, `agent/embedding_provider.py`, `agent/memory_retrieval.py`, `reflections/memory/` returned empty). Issue filed and planned same day.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Bug confirmed present against current main by code-path reading (reproducing the live Ollama timeout is infeasible on demand; the code path is unambiguous).

## Prior Art

- **PR #1013 (issue #965)**: Added vector-similarity as the fourth RRF signal on Memory. This is *why* recall already degrades gracefully when a record has no vector — the fusion unions signals rather than requiring all four. Directly relevant: confirms AC2 needs only a regression test.
- **PR #1224 (issue #1214)**: Added the embedding orphan cleanup (`reflections/memory/embedding_orphan_sweep.py` + `scripts/embedding_orphan_reconcile.py`). This is the template for the new backfill reflection's registry wiring and dry-run/apply gating, but it only *deletes* orphan `.npy` files — it never re-embeds a missing vector.
- **Issue #1876 (KnowledgeDocument)**: Established the convention that a `.embedding` field holds the dimension count (positive int once embedded, `None`/`0` otherwise) and that a matching content-hash with a falsy embedding must force a re-embed (`models/knowledge_document.py:87-96`). The backfill query and re-embed mechanism reuse this exact semantics.
- **Issue #859**: OpenAI embedding token-limit truncation — unrelated write path, no overlap.

No prior attempt fixed the Memory write-side drop. This is the first fix for it.

## Research

No relevant external findings — `popoto` is an in-house library (`github.com/tomcounsell/popoto`) and the fix is entirely internal to this repo. Proceeding with codebase context.

## Spike Results

### spike-1: GracefulEmbeddingField subclass is a valid, concurrency-safe seam
- **Assumption**: "A `GracefulEmbeddingField(EmbeddingField)` whose `on_save` catches the provider error and returns the pipeline will let the main record persist, without patching pinned popoto and without mutating shared class state."
- **Method**: code-read (`popoto/models/base.py:1350-1418`, `popoto/fields/embedding_field.py:239-376`)
- **Finding**: Confirmed. popoto's save loop calls `field.on_save(...)` per field (`base.py:1389-1398`) and only calls `internal_pipeline.execute()` afterward (`:1418`). `field` is the model's field instance, so `field.on_save` dispatches to the subclass override. The parent `on_save` raises at `embedding_field.py:307` (RuntimeError) / `:313` (ValueError) **before** writing the `.npy` file or queuing the dimension `hset` — so catching the exception leaves no partial embedding artifact, and the already-queued main `hset` executes normally. `isinstance(field_instance, EmbeddingField)` inside the parent stays true for a subclass instance. On the caught path `m.embedding` is never set, so it stays `None` (the field's `default`) — the queryable "no vector" marker.
- **Confidence**: high
- **Impact on plan**: Adopt the subclass approach. Rejected the alternative of catch-and-retry-with-provider-swapped-to-None because that mutates class-level `set_default_provider` state under exactly the concurrent-saturation condition that triggers the bug, causing healthy concurrent saves to lose their embeddings.

### spike-2: Recall already tolerates missing embeddings; backfill wiring is pure YAML + importlib
- **Assumption**: "Recall paths need no changes, and adding a backfill reflection is low-boilerplate."
- **Method**: code-read (`agent/memory_retrieval.py`, `reflections/memory/embedding_orphan_sweep.py`, `config/reflections.yaml`, `reflections/memory_management.py`, `agent/reflection_scheduler.py`)
- **Finding**: Recall is already tolerant — `get_embedding_ranked` guards empty provider (`:199-200`) and empty matrix (`:215-216`); `load_embeddings` only surfaces keys that have a `.npy`, and `rrf_fuse` (`:47-79`) unions signals so an absent record scores 0 on the embedding signal and stays retrievable via BM25/relevance/confidence/bloom. Adding a reflection = one `async def run() -> dict` module under `reflections/memory/`, one re-export line in `reflections/memory_management.py`, one YAML block in `config/reflections.yaml` (`callable: reflections.memory_management.run_<name>`), gated by an `*_APPLY` env var (dry-run default). The scheduler resolves it via `importlib` and `await`s coroutine callables natively (`agent/reflection_scheduler.py:318-336`, `:424-455`).
- **Confidence**: high
- **Impact on plan**: AC2 becomes a regression test, not new code. Backfill (AC3) is in scope — it is genuinely finishable here using the established reflection pattern, so it is not deferred.

## Data Flow

1. **Entry point**: A human Telegram message → subconscious ingestion calls `Memory.safe_save(...)` (or an agent observation path).
2. **`Memory.safe_save`** (`models/memory.py:178-208`): instantiates `Memory(**kwargs)` and calls `m.save()`.
3. **popoto `Model.save`** (`base.py:1350-1418`): queues the main `hset`, then loops `field.on_save` for `relevance`, `confidence`, `bm25`, **`embedding`**, `bloom`; executes the pipeline last.
4. **`EmbeddingField.on_save`** → `OllamaEmbeddingProvider.embed` → HTTP POST to Ollama. On timeout, raises `RuntimeError` → today aborts the whole save.
5. **Output (today)**: exception → `safe_save` returns `None` → record lost. **Output (fixed)**: `GracefulEmbeddingField.on_save` catches, logs, returns the pipeline → record persists with `embedding = None`; recall serves it via the other three signals; the backfill reflection later re-embeds it.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

The fix is a focused field subclass plus a well-trodden reflection. The bottleneck is review, not coding.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable (Popoto backend for tests) | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Memory save/recall tests write real records |
| numpy present (EmbeddingField requirement) | `python -c "import numpy"` | EmbeddingField subclass load |

No Ollama prerequisite — the tests stub the provider to raise; production degrades when Ollama is absent (that is the feature).

## Solution

### Key Elements

- **`GracefulEmbeddingField`**: a `popoto.EmbeddingField` subclass whose `on_save` wraps the parent call in `try/except (RuntimeError, ValueError)`; on failure it logs a warning and returns the pipeline unchanged, so the record persists without a vector (`embedding` stays `None`).
- **Memory model swap**: `Memory.embedding` uses `GracefulEmbeddingField(source="content")` instead of the raw `EmbeddingField`. Storage-identical — no data migration.
- **`memory_embedding_backfill` reflection**: a daily, dry-run-default reflection that finds active `Memory` records with a falsy `embedding` and re-saves them when the provider `is_available()`, healing degraded records into full semantic recall.

### Flow

Human message → `Memory.safe_save` → `save()` → (Ollama times out) → `GracefulEmbeddingField.on_save` catches → record persists without vector, logged once → recall serves it via BM25/relevance/confidence → nightly `memory_embedding_backfill` re-embeds it when Ollama is healthy → full four-signal recall restored.

### Technical Approach

- **New module `models/graceful_embedding_field.py`**:
  ```python
  class GracefulEmbeddingField(EmbeddingField):
      @classmethod
      def on_save(cls, model_instance, field_name, field_value, pipeline=None, **kwargs):
          try:
              return super().on_save(model_instance, field_name, field_value, pipeline=pipeline, **kwargs)
          except (RuntimeError, ValueError) as e:
              logger.warning("Embedding degraded — persisting %s without vector: %s",
                             model_instance.__class__.__name__, e)
              return pipeline if pipeline else None
  ```
  Catching before `execute()` is what preserves the record (spike-1). The parent raises before any `.npy` write, so no orphan artifact is created.
- **`models/memory.py:154`**: `embedding = GracefulEmbeddingField(source="content")`. Import the new class. No other model changes; `__embedding_garbage_collect__` and the field's Redis semantics are unchanged.
- **`safe_save` stays as the outer backstop** for non-embedding failures; its `except Exception` no longer fires on embedding timeouts because `on_save` no longer raises for them.
- **Backfill reflection** `reflections/memory/memory_embedding_backfill.py`: `async def run() -> dict` returning `{"status", "findings", "summary"}`. Read `MEMORY_EMBEDDING_BACKFILL_APPLY` (dry-run default, mirroring `embedding_orphan_sweep.py:96-102`). Iterate `Memory.query.all()`, skip `memory.superseded_by`, collect records where `not memory.embedding`; in apply mode and when `OllamaEmbeddingProvider().is_available()`, call `memory.save()` to trigger re-embed (the `KnowledgeDocument` #1876 precedent: re-embed == `.save()`). Wrap in try/except returning `{"status": "error", ...}`.
- **Registry**: add `from reflections.memory.memory_embedding_backfill import run as run_memory_embedding_backfill` to `reflections/memory_management.py` (append to `__all__`), and a YAML block in `config/reflections.yaml` (`every: 86400s`, `priority: low`, `execution_type: function`, `callable: reflections.memory_management.run_memory_embedding_backfill`, `enabled: true`).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `GracefulEmbeddingField.on_save` `except (RuntimeError, ValueError)` block is directly tested: a provider stub raising `RuntimeError` must produce a persisted record AND a `logger.warning` (assert both — persistence via `Memory.query`, log via `caplog`).
- [ ] `safe_save`'s existing `except Exception` (`models/memory.py:206`) remains covered by `tests/unit/test_memory_model.py`; assert it still returns `None` for a genuinely un-persistable record (e.g. a non-embedding error) so the backstop is intact.

### Empty/Invalid Input Handling
- [ ] Backfill `run()` handles an empty corpus (`Memory.query.all()` returns `[]`) → `{"status": "success", "findings": 0}`, no crash.
- [ ] Backfill skips records already having an embedding and records with `superseded_by` set; assert neither is re-saved.
- [ ] Confirm a degraded record's `content` is non-empty so BM25 recall still functions (empty content already returns early in the parent `on_save`).

### Error State Rendering
- [ ] Backfill in apply mode with an unavailable provider does not re-save (assert `.save()` not called) and reports the skip in its summary rather than raising.
- [ ] The degradation `logger.warning` is emitted once per failed save (not silent), so operators can see the degradation in logs.

## Test Impact

- [ ] `tests/unit/test_memory_model.py::test_memory_create_and_save` — UPDATE if needed: verify it still passes with `GracefulEmbeddingField` (storage-identical; expected to pass unchanged). If the environment has no provider it already exercises the no-embedding path.
- [ ] `tests/unit/test_memory_model.py::test_memory_safe_save_success` / `::test_memory_safe_save_filtered` — UPDATE if needed: same field-swap verification; assert `safe_save` still returns the instance / `None` respectively.
- [ ] `tests/unit/test_reflections_memory.py` — UPDATE: add `class TestMemoryEmbeddingBackfill` mirroring `TestEmbeddingOrphanSweep` (patch `models.memory.Memory` + `MEMORY_EMBEDDING_BACKFILL_APPLY` env gate); no existing case in this file changes behavior.
- [ ] `tests/unit/test_graceful_embedding_field.py` — CREATE: provider-stub-raises → record persists with `embedding is None` and is retrievable via BM25; warning logged.

These are the only tests touching the Memory save path or the embedding field; the field swap is storage-compatible, so no existing assertion about stored bytes changes.

## Rabbit Holes

- **Patching popoto in `.venv`**: it is a pinned PyPI dependency; edits are non-durable. Any popoto-level fix belongs in a separate `tomcounsell/popoto` release + version bump — out of scope here.
- **Making recall re-embed on read** (the `KnowledgeDocument.safe_upsert` lazy pattern): recall is a hot path; embedding synchronously on read would reintroduce the timeout stall into queries. Keep healing in the async backfill reflection.
- **A synchronous in-process retry queue for failed embeddings**: adds concurrency and state for a case the nightly backfill already covers. Skip.
- **Reworking `safe_save`'s blanket `except Exception`** into typed handlers: tempting but orthogonal; the fix is at the field layer so the exception never reaches `safe_save` for embedding timeouts. Leave the backstop as-is.

## Risks

### Risk 1: Subclass override drifts if popoto changes `on_save`'s internal ordering
**Impact:** A future popoto version could write the `.npy` before raising, so a caught error leaves an orphan artifact.
**Mitigation:** The catch path creates no artifacts today (spike-1). The existing `embedding_orphan_sweep` reflection already reaps orphan `.npy` files, so even a future drift is self-healing. Pin note stays in the module docstring referencing `popoto>=1.7.1`.

### Risk 2: Backfill re-save storms when Ollama recovers after a long outage
**Impact:** Many degraded records re-embed at once, re-saturating Ollama.
**Mitigation:** Reflection runs daily at low priority; add a per-run cap (e.g. process at most N records per invocation) and dry-run-default gating so apply is opt-in. Provider `is_available()` check short-circuits when Ollama is still down.

## Race Conditions

### Race 1: Concurrent degraded saves
**Location:** `GracefulEmbeddingField.on_save`, `models/memory.py` save path.
**Trigger:** Multiple workers save memories while Ollama is saturated.
**Data prerequisite:** None shared — each save owns its own instance and pipeline.
**State prerequisite:** No mutation of the class-level default provider.
**Mitigation:** The subclass approach touches no shared state (unlike the rejected provider-swap alternative), so concurrent degradations are independent and correct. This is the primary reason the subclass seam was chosen.

### Race 2: Backfill re-save vs. embedding orphan GC
**Location:** `memory_embedding_backfill.run()` re-save vs. `EmbeddingField.garbage_collect` in `embedding_orphan_sweep`.
**Trigger:** Backfill writes a fresh `.npy` while the orphan sweep scans.
**Data prerequisite:** The re-saved record is a live member of `$Class:Memory`, so it is in `garbage_collect`'s expected-keep set.
**State prerequisite:** GC's 300s mtime guard protects freshly written files.
**Mitigation:** Both jobs are daily and key on live records; the mtime guard already covers the write window (documented in `embedding_field.py:505-549`). No new mitigation needed.

## No-Gos (Out of Scope)

- [ORDERED] A `popoto` upstream change to make `EmbeddingField.on_save` degrade natively — belongs in a `tomcounsell/popoto` release, gated on that repo's release cadence, then a version bump here. This plan fixes the behavior repo-locally without waiting on it.

Everything else in the issue's acceptance criteria (persist-on-failure, recall tolerance regression test, re-embed backfill) is in scope for this plan.

## Update System

The new reflection adds a block to `config/reflections.yaml`, which is read by the reflection scheduler on every machine. No new Python dependencies, no `scripts/update/run.py` changes, no `migrations.py` changes. The `GracefulEmbeddingField` swap is storage-identical (same dimension-count int / `None`, same `.npy` layout), so **no Popoto schema migration is required** and existing embedded records are unaffected. Confirm the reflection scheduler picks up the new YAML entry after deploy (`python -m reflections --dry-run`).

## Agent Integration

No agent integration required — this is an internal memory-layer fix. The `GracefulEmbeddingField` change is transparent to callers of `Memory.safe_save`. The backfill runs via the reflection scheduler (`agent/reflection_scheduler.py`), not through an MCP tool or the Telegram bridge; no `.mcp.json` or `bridge/telegram_bridge.py` changes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — add an "Embedding degradation" subsection: on provider failure the record persists without a vector (`GracefulEmbeddingField`), recall serves it via the other RRF signals, and the `memory-embedding-backfill` reflection heals it later.
- [ ] Add the `memory-embedding-backfill` reflection to any reflection index/table in the memory docs (mirror how `embedding-orphan-sweep` is listed).

### Inline Documentation
- [ ] Docstring on `GracefulEmbeddingField` explaining why it exists (issue #1904), the pinned-popoto constraint, and the "persist without vector" contract.
- [ ] Module docstring on `memory_embedding_backfill.py` documenting the `MEMORY_EMBEDDING_BACKFILL_APPLY` gate and dry-run default.

No external documentation site in this repo.

## Success Criteria

- [ ] With the embedding provider stubbed to raise `RuntimeError`, `Memory.safe_save(...)` returns a persisted record (found via `Memory.query`) whose `embedding` is `None` — asserted by a new test.
- [ ] A Memory record with no embedding is retrievable via BM25/keyword recall — asserted by a regression test (AC2).
- [ ] `memory_embedding_backfill` reflection exists, is registered in `config/reflections.yaml`, defaults to dry-run, and in apply mode re-embeds only active records with a falsy embedding — asserted by a new test class (AC3).
- [ ] The degradation path logs a `logger.warning` (observable, not silent).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

The lead agent orchestrates; it deploys team members and coordinates.

### Team Members

- **Builder (field + model)**
  - Name: `field-builder`
  - Role: Implement `GracefulEmbeddingField` and swap the Memory field; add the field-level test.
  - Agent Type: builder
  - Domain: redis-popoto
  - Resume: true

- **Builder (backfill reflection)**
  - Name: `backfill-builder`
  - Role: Implement the `memory_embedding_backfill` reflection, registry wiring, YAML, and its test class.
  - Agent Type: builder
  - Domain: redis-popoto
  - Resume: true

- **Validator**
  - Name: `memory-validator`
  - Role: Verify all success criteria and run the suite.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `memory-doc`
  - Role: Update `docs/features/subconscious-memory.md`.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement GracefulEmbeddingField and swap the Memory field
- **Task ID**: build-field
- **Depends On**: none
- **Validates**: tests/unit/test_graceful_embedding_field.py (create), tests/unit/test_memory_model.py
- **Informed By**: spike-1 (subclass catch before `execute()` preserves the record; no orphan artifact)
- **Assigned To**: field-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `models/graceful_embedding_field.py` with the subclass override catching `(RuntimeError, ValueError)`, logging a warning, returning the pipeline.
- Change `models/memory.py:154` to `GracefulEmbeddingField(source="content")` and import it.
- Add `tests/unit/test_graceful_embedding_field.py`: stub the provider to raise; assert the record persists with `embedding is None`, is retrievable via BM25, and a warning is logged.

### 2. Implement the backfill reflection + wiring
- **Task ID**: build-backfill
- **Depends On**: none
- **Validates**: tests/unit/test_reflections_memory.py (TestMemoryEmbeddingBackfill, create)
- **Informed By**: spike-2 (reflection wiring is YAML + importlib; re-embed == `.save()`, #1876)
- **Assigned To**: backfill-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `reflections/memory/memory_embedding_backfill.py` (`async def run() -> dict`, `MEMORY_EMBEDDING_BACKFILL_APPLY` gate, per-run cap, provider `is_available()` guard, skip `superseded_by`, re-save where `not memory.embedding`).
- Add the re-export to `reflections/memory_management.py` and append to `__all__`.
- Add the YAML block to `config/reflections.yaml` (mirror `embedding-orphan-sweep`).
- Add `class TestMemoryEmbeddingBackfill` to `tests/unit/test_reflections_memory.py` mirroring `TestEmbeddingOrphanSweep`.

### 3. Validate
- **Task ID**: validate-all
- **Depends On**: build-field, build-backfill, document-feature
- **Assigned To**: memory-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_graceful_embedding_field.py tests/unit/test_memory_model.py tests/unit/test_reflections_memory.py -q`.
- Run `python -m reflections --dry-run` and confirm `memory-embedding-backfill` loads.
- Verify each Success Criterion and report pass/fail.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-field, build-backfill
- **Assigned To**: memory-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md` with the degradation + backfill subsection and reflection listing.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Field + model tests pass | `pytest tests/unit/test_graceful_embedding_field.py tests/unit/test_memory_model.py -q` | exit code 0 |
| Backfill reflection tests pass | `pytest tests/unit/test_reflections_memory.py -q` | exit code 0 |
| GracefulEmbeddingField wired into Memory | `grep -c "GracefulEmbeddingField(source=\"content\")" models/memory.py` | output contains 1 |
| Backfill reflection registered | `grep -c "run_memory_embedding_backfill" config/reflections.yaml reflections/memory_management.py` | output > 1 |
| Reflection registry loads | `python -m reflections --dry-run` | exit code 0 |
| Lint clean | `python -m ruff check models/graceful_embedding_field.py models/memory.py reflections/memory/memory_embedding_backfill.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/ reflections/memory/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

## Open Questions

1. Backfill scope: acceptable to cap each nightly run at, say, 500 re-embeds to avoid re-saturating Ollama after a long outage, or should it drain the full backlog each run?
2. Should the degradation `logger.warning` also increment a metric / emit to the analytics surface so silent-embedding-loss is observable on the dashboard, or is a log line sufficient for now?
