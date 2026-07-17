---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-17
tracking: https://github.com/tomcounsell/ai/issues/2112
last_comment_id:
---

# Decode ContentField values for query-loaded rows (DocumentChunk.search and friends)

## Problem

`DocumentChunk.search()` is the fine-grained retrieval surface for the knowledge
base. For every result it reads `chunk.content` on a row loaded via
`cls.query.get(chunk_id=...)`. Popoto's lazy-field path
(`Model.__getattribute__` for `.get()`/`.filter()`/`.all()` rows) decodes the
raw msgpack value from Redis and returns it directly — for a `ContentField`
that stored value IS the `$CF:{hash}:{relpath}` reference string, so the
descriptor decode (`ContentField.__get__` → `store.load()`) never runs.

**Current behavior:**
Search results carry `"chunk_text": "$CF:303a3746..."` instead of the chunk
text — a retrieval-quality hole for any consumer reading `chunk_text`. Two
sibling consumers have the same defect: `index_file` can pass a raw `$CF:`
reference into companion memories when `safe_upsert` takes its unchanged-skip
path, and the doctor zero-chunk guard treats a dangling reference (content file
missing) as "has content."

**Desired outcome:**
Every query-loaded `.content` consumer receives decoded text. One shared decode
helper at this repo's seam (mirroring the proven pattern in
`rechunk_zero_chunk_documents`), used everywhere — including refactoring the
rechunk helper's inline workaround onto it so the decode logic exists exactly
once.

## Freshness Check

**Baseline commit:** `84f3c12d5968efdb8438773a93bd8e9613b71817`
**Issue filed at:** 2026-07-16T03:54:23Z
**Disposition:** Unchanged (root cause re-verified against installed popoto and current main)

**File:line references re-verified:**
- `models/document_chunk.py:127` — `"chunk_text": chunk.content or ""` on a `query.get()` row — still holds.
- `tools/knowledge/indexer.py` `rechunk_zero_chunk_documents` (decode workaround at ~line 595) — still holds; it detects `$CF:` and loads via `KnowledgeDocument._meta.fields["content"].store`.
- `tools/knowledge/indexer.py:329` — `_create_companion_memories(abs_path, project_key, scope, doc.content or "")` — still holds; `doc` is query-loaded on the `safe_upsert` unchanged-skip path (`models/knowledge_document.py:99-101`).
- `tools/doctor.py` `_check_knowledge_zero_chunk_documents` (~line 556) — `if not (doc.content and doc.content.strip())` truthiness check on the raw reference — still holds.
- Popoto lazy path re-verified in site-packages: `popoto/models/base.py::Model.__getattribute__` returns `decode_lazy_field(lazy_fields[name])` (msgpack decode only); no `ContentField` store load is involved for lazy rows.

**Cited sibling issues/PRs re-checked:**
- #2085 — closed; fixed filename-length overflow (store seam), explicitly scoped this read-path bug out.
- PR #2111 — merged 2026-07-16; added the length-safe store and the rechunk helper containing the proven decode pattern. Did not touch the read path.

**Commits on main since issue was filed (touching referenced files):**
- `3bdc0b027` (PR #2111) — the origin PR itself; irrelevant to the read-path defect.
- `b8b512e3d` (PR #2116) — keychain/TLS diagnostics; irrelevant.

**Active plans in `docs/plans/` overlapping this area:** none (`hybrid-retrieval-eval.md` targets Memory recall, not DocumentChunk/KnowledgeDocument).

**Notes:** Bug reproduction is structural: the lazy `__getattribute__` path visibly cannot invoke the store (code read of installed popoto 1.8.x), and #2085 empirically observed `d.content == '$CF:...'` on a `query.get()` row. No drift.

## Prior Art

- **#2085 / PR #2111**: Length-safe content-store filenames — added `LengthSafeFilesystemStore` and `rechunk_zero_chunk_documents`, whose inline `$CF:` decode is the proven pattern this plan generalizes. Succeeded; deliberately did not fix the read path.
- No other closed issues/merged PRs match (`gh` search for "ContentField decode" / "$CF reference" → empty beyond the above).

## Research

No relevant external findings — internal fix against a first-party dependency
(`popoto`, source readable in `.venv`). Upstreaming a popoto fix
(`Model.__getattribute__` routing ContentField reads through the store) is a
separate conversation; this repo needs the seam-level fix now and must not fork
popoto (see Rabbit Holes).

## Data Flow

1. **Write path:** `index_file` → `KnowledgeDocument.safe_upsert` / `_sync_chunks` → `ContentField.on_save` → `store.save(bytes)` → Redis hash stores the `$CF:{hash}:{relpath}` reference; text lives on the filesystem.
2. **Query-loaded read path (broken):** `Model.query.get/filter/all` → `decode_popoto_model_hashmap(lazy=True)` → attribute access hits `Model.__getattribute__` → `decode_lazy_field` (msgpack) → returns the reference string verbatim.
3. **Consumers of that read path:** `DocumentChunk.search()` (chunk_text), `index_file` → `_create_companion_memories` (unchanged-skip path only), doctor `_check_knowledge_zero_chunk_documents` (truthiness), `rechunk_zero_chunk_documents` (already works around it inline).
4. **Fix point:** a shared `decoded_content(instance)` helper that detects a `$CF:` reference and loads it through the field's own store (`type(instance)._meta.fields["content"].store.load(ref).decode("utf-8")`), used by all four consumers.

## Architectural Impact

- **New dependencies:** none. One new small module `models/content_decode.py`.
- **Interface changes:** none public. `DocumentChunk.search()` return shape unchanged; `chunk_text` now carries text instead of a reference (the documented contract).
- **Coupling:** decode logic moves from one inline workaround to one named helper; coupling to popoto internals is unchanged (same `_meta.fields[...].store` access the rechunk helper already uses).
- **Data ownership:** unchanged; no writes, no schema, no Redis key changes.
- **Reversibility:** high — pure read-path change, trivially revertible.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; r.ping()"` | Model save/query in tests |

## Solution

### Key Elements

- **`models/content_decode.py::decoded_content(instance) -> str`**: the single decode seam. Reads `instance.content`; if it is a `str` starting with `$CF:`, loads real bytes via the model's own content-field store and decodes UTF-8; on `FileNotFoundError` logs a warning and returns `""`; otherwise returns the value (or `""` for None). Pure read, ORM-only, never raw Redis.
- **`DocumentChunk.search()`**: `"chunk_text": decoded_content(chunk)` replaces `chunk.content or ""`.
- **`index_file` (indexer.py:329)**: `_create_companion_memories(abs_path, project_key, scope, decoded_content(doc))` — fixes the unchanged-skip path; fresh-save path is byte-identical (value is not a reference, helper passes it through).
- **Doctor `_check_knowledge_zero_chunk_documents`**: non-trivial-content gate becomes `decoded_content(doc).strip()`. Semantics improve: a doc whose content file is missing decodes to `""` and is skipped — consistent with `rechunk_zero_chunk_documents`, which cannot repair such a doc anyway (it skips on `FileNotFoundError`).
- **`rechunk_zero_chunk_documents`**: inline `$CF:` decode block replaced by the shared helper (NO LEGACY duplication). Behavior preserved: missing content file → helper returns `""` → existing non-empty guard `continue`s the doc.

### Flow

Search call → `query.get()` row (lazy) → `decoded_content(chunk)` → `$CF:` detected → `store.load(ref).decode("utf-8")` → real chunk text in `chunk_text` → caller gets prose, never a reference.

### Technical Approach

- Helper signature: `decoded_content(instance) -> str`. Store resolution: `type(instance)._meta.fields["content"].store` — identical to the pattern already shipped in `rechunk_zero_chunk_documents` and pinned by #2085's tests. Works for any model with a `content` ContentField (both `DocumentChunk` and `KnowledgeDocument` share the `length_safe_content_store` singleton).
- Passthrough contract: non-reference values (fresh instances, plain strings) are returned unchanged (`raw or ""`), so the helper is safe to call unconditionally at every consumer — no "is this row lazy?" branching anywhere.
- Deliberately NOT a model mixin or `__getattribute__` override: popoto's metaclass and lazy machinery make attribute-interception fragile (see Rabbit Holes); a plain function at the consumer seam is deterministic and testable.
- Missing-file handling: `FileNotFoundError` → `logger.warning` + `""`. Callers already treat empty content as "skip"; no new exception surface. Any other store error propagates to each caller's existing try/except (search's per-chunk except, indexer/doctor guards) — unchanged failure envelopes.
- The unit test that pins popoto's bypass doubles as a canary: it asserts a `query.get()` row's raw `.content` starts with `$CF:` AND `decoded_content()` returns the text. If a future popoto release fixes the lazy path, the first assertion fails loudly and we can retire the helper deliberately.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `DocumentChunk.search()`'s per-chunk `except Exception` (models/document_chunk.py:134): add a test asserting a chunk whose content file is missing yields `chunk_text == ""` (helper returns `""`; row still scored) rather than a raised error or a `$CF:` string.
- [ ] `decoded_content` on `FileNotFoundError`: assert `""` return and a `logger.warning` record (caplog).

### Empty/Invalid Input Handling
- [ ] `decoded_content` with `content=None` → `""`; with plain non-reference string → returned unchanged; with empty string → `""`.
- [ ] Doctor check: doc with dangling `$CF:` reference and zero chunks → skipped (not flagged), check still returns a `CheckResult` and never raises.

### Error State Rendering
- [ ] No user-visible UI in scope; the observable contract is the `search()` result dict — regression test asserts `chunk_text` never starts with `$CF:`.

## Test Impact

No existing tests affected — `tests/unit/test_document_chunk.py` asserts only import/field/empty-search shapes, `tests/unit/test_knowledge_document.py` exercises `safe_upsert` on fresh instances (descriptor path, not lazy rows), and #2085's store tests pin the write path, which this plan does not touch. New tests are additive.

- [ ] `tests/unit/test_document_chunk.py` — UPDATE (additive only): new test class for decoded search results; no existing case modified.

## Rabbit Holes

- Do NOT fork or monkeypatch popoto (`Model.__getattribute__`, `ContentField.__get__`) — attribute interception on lazy instances is exactly the machinery `AgentSession`'s descriptor-pollution saga showed to be fragile. Seam-level helper only. An upstream popoto fix is a separate, optional follow-up conversation, not this plan.
- Do NOT add a `decoded_content` property to the models via mixin — popoto's metaclass treats class attributes as potential fields; a plain function avoids that entire risk class.
- Do NOT sweep every `.content` in the repo — only ContentField-backed models (`DocumentChunk`, `KnowledgeDocument`) are in scope; `Memory.content` etc. are ordinary fields.
- Do NOT re-embed or re-index anything; this is a read-path fix.

## Risks

### Risk 1: Doctor semantics change for dangling references
**Impact:** A doc whose content file is missing (and has zero chunks) is no longer flagged by the zero-chunk check.
**Mitigation:** Deliberate: rechunk cannot repair such docs (it skips them), so flagging them pointed operators at a fix that can't work. The helper's `logger.warning` still surfaces the dangling reference. Documented in the feature doc.

### Risk 2: Per-result file I/O in search
**Impact:** `search()` now reads up to top-K candidate content files from disk per query (previously it returned references without I/O — but was wrong).
**Mitigation:** Decode happens for every scored row pre-sort (same loop that already does a per-row `query.get`); chunk files are small text files and result sets are bounded by the chunk corpus. Correctness over micro-cost; no caching layer (Small appetite).

## Race Conditions

No race conditions identified — the change is a synchronous read path; content
files are written atomically by popoto's `_atomic_write` (temp + `os.rename`),
so a concurrent reindex yields either the old or new complete file, never a
partial read.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. (An upstream
popoto patch is a possibility deliberately rejected, not deferred work: the
repo-seam fix is complete on its own and the canary test tells us when upstream
changes.)

## Update System

No update system changes required — no new dependencies, config, env vars, or
Popoto schema/key/reference changes (read path only), so no
`scripts/update/migrations.py` entry is warranted per the schema-migration
convention (it targets schema/key/reference changes).

## Agent Integration

No agent integration required — no new CLI entry point or MCP surface. The fix
lands inside existing surfaces (`DocumentChunk.search`, the indexer, and
`python -m tools.doctor`), all already reachable by the agent.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/length-safe-content-store.md` with a "Read path: query-loaded rows" section documenting the popoto lazy bypass, the `decoded_content` seam, the canary test, and the doctor-semantics note.
- [ ] Verify `docs/features/README.md` index entry still describes the doc accurately (update the one-liner if needed).

### Inline Documentation
- [ ] Docstring on `decoded_content` explaining the lazy bypass, passthrough contract, and FileNotFoundError policy.
- [ ] Trim `rechunk_zero_chunk_documents`'s long inline decode commentary to a pointer at the helper.

## Success Criteria

- [ ] A `DocumentChunk` saved and re-loaded via `query.get()` returns decoded text from `decoded_content()` while raw `.content` is a `$CF:` reference (canary assertion).
- [ ] `DocumentChunk.search()` results carry decoded `chunk_text` (regression test asserts no `$CF:` prefix) with the embedding provider stubbed.
- [ ] `index_file` on an unchanged document passes decoded text (not a reference) to `_create_companion_memories` (test via the unchanged-skip path).
- [ ] `rechunk_zero_chunk_documents` no longer contains an inline `$CF:` decode block (uses the shared helper) and its integration behavior is unchanged.
- [ ] Doctor zero-chunk check uses decoded content; dangling-reference doc is skipped, check never raises.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

Solo dev given Small appetite: one builder implements the helper, the four
consumer edits, tests, and docs in the session worktree (disjoint from any other
lane); one validator pass confirms success criteria.

### Team Members

- **Builder (content-decode)**
  - Name: decode-builder
  - Role: Implement helper, wire four consumers, write tests, update docs
  - Agent Type: builder
  - Resume: true

- **Validator (content-decode)**
  - Name: decode-validator
  - Role: Verify success criteria, run narrow tests, lint
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Tier 1 `builder` and `validator` suffice. Domain: Redis/Popoto data — ORM only,
never raw Redis on Popoto-managed keys.

## Step by Step Tasks

### 1. Implement decode helper + wire consumers + tests
- **Task ID**: build-content-decode
- **Depends On**: none
- **Validates**: tests/unit/test_content_decode.py (create), tests/unit/test_document_chunk.py (additive)
- **Assigned To**: decode-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `models/content_decode.py` with `decoded_content(instance) -> str` per Technical Approach (store via `type(instance)._meta.fields["content"].store`; `$CF:` detection; `FileNotFoundError` → warn + `""`; passthrough otherwise).
- Wire `models/document_chunk.py::search` (`chunk_text`), `tools/knowledge/indexer.py::index_file` (companion memories arg), `tools/doctor.py::_check_knowledge_zero_chunk_documents` (non-trivial gate), and refactor `tools/knowledge/indexer.py::rechunk_zero_chunk_documents` onto the helper (delete the inline decode block).
- Add `tests/unit/test_content_decode.py`: canary round-trip on a real saved+query-loaded row (Redis), passthrough cases (None/plain/empty), FileNotFoundError → `""` + warning.
- Add additive search test in `tests/unit/test_document_chunk.py`: stub `OpenAIProvider.embed` and `EmbeddingField.load_embeddings` to surface a saved chunk; assert `chunk_text` is decoded text and never `$CF:`-prefixed; missing-content-file case yields `""`.
- Clean up any test rows created (recognizable `test-` project_key, ORM deletes only).

### 2. Validation
- **Task ID**: validate-content-decode
- **Depends On**: build-content-decode
- **Assigned To**: decode-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `scripts/pytest-clean.sh tests/unit/test_content_decode.py tests/unit/test_document_chunk.py -q`.
- Run `python -m ruff check` / `ruff format --check` on changed files.
- Grep-confirm the inline decode block is gone from the rechunk helper.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-content-decode
- **Assigned To**: decode-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/length-safe-content-store.md` (read-path section) and the README index one-liner if needed.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Helper unit tests pass | `scripts/pytest-clean.sh tests/unit/test_content_decode.py -q` | exit code 0 |
| Chunk model tests pass | `scripts/pytest-clean.sh tests/unit/test_document_chunk.py -q` | exit code 0 |
| Lint clean | `python -m ruff check models/content_decode.py models/document_chunk.py tools/knowledge/indexer.py tools/doctor.py` | exit code 0 |
| search uses helper | `grep -c "decoded_content" models/document_chunk.py` | output > 0 |
| Rechunk inline decode gone | `grep -c 'startswith("\$CF:")' tools/knowledge/indexer.py` | match count == 0 |
| No popoto site-packages edits | `git diff --name-only \| grep -c "site-packages"` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
