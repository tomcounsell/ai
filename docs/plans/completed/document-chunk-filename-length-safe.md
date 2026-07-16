---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-16
tracking: https://github.com/tomcounsell/ai/issues/2085
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-16T03:21:46Z
---

# DocumentChunk content filenames length-safe

## Problem

`DocumentChunk.content` and `KnowledgeDocument.content` use Popoto's
`ContentField(store="filesystem")`. Popoto derives the on-disk content filename
from the model's sorted key fields joined by `:` and sanitized into a single
filename component. For `DocumentChunk` that key is
`chunk_id:document_doc_id:file_path:project_key`. When `file_path` is a deeply
nested, long vault path, the resulting single filename exceeds the macOS/most-Unix
255-byte limit and every `chunk.save()` fails with `[Errno 63] File name too long`.

**Current behavior:**
Two PsyOptimal data-room PDFs (long paths) silently lose all fine-grained chunk
coverage — their `KnowledgeDocument` records exist with **zero** `DocumentChunk`
records because `_sync_chunks`'s per-chunk try/except swallows the `Errno 63`.
The document is still findable at doc granularity but not chunk granularity.

**Desired outcome:**
Chunk (and document) content filenames are length-safe regardless of source path
length. Every indexed document gets its chunks. A regression guard surfaces any
`KnowledgeDocument` with zero chunks. Already-saved (short-path) content still
resolves; failed docs are re-derivable by re-indexing.

## Freshness Check

**Baseline commit:** `bc1a311b4c1625aca8f1322a9fdc726738c6bff5`
**Issue filed at:** 2026-07-14T06:12:01Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `models/document_chunk.py:37` — `content = ContentField(store="filesystem")` — still holds.
- `models/knowledge_document.py:47` — same field, same latent risk — still holds.
- `tools/knowledge/indexer.py:157 _sync_chunks` — per-chunk try/except swallows the error — still holds.
- Popoto `FilesystemStore._live_path` / `save` derive the filename from the sanitized key; `load` / `_parse_reference` read the relative path FROM the `$CF:` reference (not re-derived) — verified in `.venv/.../popoto/stores/filesystem.py`.
- Runtime check: `DocumentChunk._meta.key_field_names` sorted = `['chunk_id', 'document_doc_id', 'file_path', 'project_key']`; `KnowledgeDocument` = `['doc_id', 'file_path', 'project_key']`.

**Cited sibling issues/PRs re-checked:** none cited.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since=createdAt` over the three files returned empty).

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** `popoto` is a pip dependency (v1.7.1, `github.com/tomcounsell/popoto`), NOT vendored — the fix must live in this repo, not site-packages.

## Prior Art

No prior issues or merged PRs found related to this work
(`gh pr list --state merged --search "filename too long DocumentChunk content"` → empty).
This is the first fix for content-filename length safety.

## Research

No relevant external findings needed — this is an internal fix against a
first-party dependency (`popoto`) whose source is readable in `.venv`. The
255-byte per-component filename limit is a well-known POSIX/HFS+/APFS constraint;
no external documentation changes the approach.

## Data Flow

1. **Entry point:** `tools/knowledge/indexer.py::index_file` → `KnowledgeDocument.safe_upsert` → `_sync_chunks(doc, content, project_key)`.
2. **Chunk creation:** `_sync_chunks` builds `DocumentChunk(document_doc_id=..., content=chunk_text, file_path=doc.file_path, project_key=...)` and calls `chunk.save()`.
3. **ContentField.on_save (popoto):** joins sorted key-field values into `key_value`, calls `store.save(content_bytes, key=key_value, model_class_name="DocumentChunk")`.
4. **FilesystemStore.save (popoto):** `_sanitize_filename(key) + ".txt"` → single filename component. **Overflow point** when `key_value` is long. Returns a `$CF:{hash}:{model}/{safe_key}.txt` reference stored in Redis.
5. **Read path:** `ContentField.__get__` → `store.load(reference)` → `_parse_reference` splits the relative path out of the reference string and reads `base_path/relative_path` (falls back to `.versions/{hash}`). The key is NOT re-derived on read → a length-safe subclass is read-compatible with old references.

## Architectural Impact

- **New dependencies:** none. A new module `models/length_safe_content_store.py` subclassing `popoto.stores.filesystem.FilesystemStore`.
- **Interface changes:** `DocumentChunk.content` and `KnowledgeDocument.content` gain an explicit `store=` instance (same base_path resolution as the default store). No field names, keys, or `$CF` reference formats change.
- **Coupling:** slightly increases coupling to popoto's `FilesystemStore` internals (overrides `_sanitize_filename`). Mitigated by a unit test that pins the parent's method contract.
- **Data ownership:** unchanged — same content directory, same reference format.
- **Reversibility:** high. Reverting to `store="filesystem"` restores old behavior; old length-safe files remain loadable because their references embed their relative paths.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No external prerequisites — the fix is internal. Unit tests need no network; the
optional heal helper and doctor check use only Redis + the local content dir.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; r.ping()"` | Model save/query in integration test |

## Solution

### Key Elements

- **`LengthSafeFilesystemStore`**: a `FilesystemStore` subclass that overrides the single filename-derivation seam (`_sanitize_filename`) so that when the sanitized name would exceed a safe byte budget, it is replaced with a truncated readable prefix plus a stable content-key digest suffix. Because both `save` (reference building + live path) and `_live_path` route through `self._sanitize_filename`, one override makes both write paths length-safe and self-consistent.
- **Model wiring**: `DocumentChunk.content` and `KnowledgeDocument.content` use a shared module-level `LengthSafeFilesystemStore` singleton (same base_path resolution as the default → old files stay reachable). Both models are wired because both keys can overflow — see the worst-case byte math below.
- **Regression guard (doctor)**: `_check_knowledge_zero_chunk_documents` reports any `KnowledgeDocument` whose content is non-trivial but which has zero `DocumentChunk` records — the exact symptom of this bug. The check is **internally bounded** (see Technical Approach) because `get_checks` only exposes `quick`/`quality` kwargs and this check runs in the unconditional list on `--quick`.
- **Re-derivation helper**: `rechunk_zero_chunk_documents()` in the indexer re-runs `_sync_chunks` from already-stored `doc.content` for zero-chunk documents. Idempotent, on-demand, referenced by the doctor `fix` message.

### Flow

Long-path vault file → `index_file` → `_sync_chunks` → `chunk.save()` → **length-safe store caps the filename** → chunk persists → chunk searchable. Doctor run → zero-chunk guard passes. If a legacy zero-chunk doc remains → doctor fix points to `rechunk_zero_chunk_documents()`.

- Byte budget: `MAX_CONTENT_FILENAME_BYTES` (default 200, sourced from a new `config/settings.py` field, env-overridable `POPOTO_MAX_CONTENT_FILENAME_BYTES`), a provisional/tunable value leaving headroom under 255 for the `.txt` extension and any tempfile suffix. Marked with a grain-of-salt comment.
- Override `_sanitize_filename(self, name)`: call the parent explicitly as `FilesystemStore._sanitize_filename(name)` (the parent is a `@staticmethod`; this is the pinning-test contract) to get the sanitized name; if `len(sanitized.encode("utf-8")) <= budget`, return it unchanged (old short names are byte-identical → no churn). Otherwise return `f"{prefix}_{digest}"` where `digest = sha256(name.encode("utf-8")).hexdigest()[:16]` (stable, unique — `name` includes the unique `chunk_id`) and `prefix = sanitized.encode("utf-8")[:budget - len(digest) - 1].decode("utf-8", errors="ignore")`.
- **UTF-8-safe truncation (CONCERN, Risk & Robustness):** `str.isalnum()` is Unicode-aware, so a sanitized name can contain multi-byte chars; a bare `.decode("utf-8")` after a byte-slice raises `UnicodeDecodeError` when the cut lands mid-character. Always truncate with `errors="ignore"`. A non-ASCII long-path unit test must assert no exception and a byte-length ≤ budget.
- **Doctor check internal bound (CONCERN, Risk & Robustness):** `get_checks(*, quick=False, quality=False)` gates only on `quality`, and the plan places the check in the always-run "Services" group, so it runs on every `--quick` invocation. Since there is no `--deep` kwarg to thread, `_check_knowledge_zero_chunk_documents` must short-circuit internally: cap the KnowledgeDocument scan to a bounded sample (e.g. first N via `AgentSession`-style bounded iteration) and report "sampled N, M zero-chunk" rather than walking an unbounded vault.
- Determinism: the same `key` always yields the same filename (pure function of `name`), so re-saving a chunk overwrites its own live file rather than orphaning.
- Uniqueness: distinct chunks differ in `chunk_id`, so their full `key` differs, so their digest differs — no cross-chunk collisions even with identical long path prefixes.
- Read compatibility: unchanged — `load()` uses the reference's embedded relative path; already-saved references (short or long) resolve as before.

### Worst-case byte math (why both models are wired)

- **DocumentChunk** key = `chunk_id(32) : document_doc_id(32) : file_path : project_key`. The issue's example sanitized filename is ~290 bytes — clearly over 255.
- **KnowledgeDocument** key = `doc_id(32) : file_path : project_key`. For the reported `file_path` (~230 bytes sanitized) and `project_key="psyoptimal"` (10): 32 + 1 + 230 + 1 + 10 = **274 bytes** → also over 255. The issue observed KD *marginally* saving only because its specific two reported paths land just under; a slightly deeper path or longer project key overflows it too. Wiring both models closes the latent overflow rather than waiting for it to bite. A KnowledgeDocument-specific long-path unit case proves the override does not silently no-op there.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_sync_chunks` has a per-chunk `except Exception` that logs `logger.warning`. Add a test asserting that after the fix a long-path chunk save does NOT hit that warning path (chunk count > 0). The existing swallow stays (correct crash isolation) but must no longer fire for length overflow.
- [ ] The doctor check wraps its body in try/except returning a failed `CheckResult` — assert it returns a `CheckResult` (never raises) when Redis is empty.

### Empty/Invalid Input Handling
- [ ] `_sanitize_filename` with empty string / whitespace-only key → returns a valid (possibly empty→parent-behavior) name without raising. Test short keys are returned byte-identical.
- [ ] `rechunk_zero_chunk_documents()` on a doc with empty content → no chunks created, no raise.

### Error State Rendering
- [ ] Doctor check surfaces the zero-chunk count in its message and a `fix` string pointing to the rechunk helper (user-visible failure path), not a swallowed silent pass.

## Test Impact

- [ ] `tests/unit/test_document_chunk.py` (if present) — UPDATE: add a long-`file_path` save case; verify no existing case asserts the old (overflowing) filename shape.
- [ ] No existing test asserts Popoto content filenames directly (grep of `tests/` for `_sanitize_filename` / `FilesystemStore` is expected empty) — new tests are additive.

If the grep for existing content-store assertions is empty: No existing tests
affected — the change is additive (new store subclass, new doctor check, new
helper) and preserves the read path and reference format, so no prior behavior or
interface that existing tests assert is modified.

## Rabbit Holes

- Do NOT patch popoto in `.venv` — it is a pip dependency; the fix lives in this repo via subclassing.
- Do NOT change `DocumentChunk`/`KnowledgeDocument` key fields (e.g. drop `file_path` from the key). That would alter Redis keys and query semantics and require a real data migration — out of proportion to the bug.
- Do NOT set a global popoto default store at startup as the primary mechanism — startup-ordering-dependent and non-deterministic across worker/bridge/CLI entry points. Explicit per-field store is deterministic.
- Do NOT force a re-embed of the whole vault inside a `/update` migration — embeddings cost money and require an API key not present on every machine.

## Risks

### Risk 1: Two-level directory / path length vs. single-component length
**Impact:** If some filesystem also enforced a total path limit we could still overflow.
**Mitigation:** The observed failure (`Errno 63`) is the 255-byte single-component `NAME_MAX`, which the digest cap fixes. The content root path is short and fixed; total path length is not the constraint here.

### Risk 2: Digest truncation collision
**Impact:** Two distinct keys hashing to the same 16-hex prefix would overwrite each other.
**Mitigation:** 16 hex = 64 bits; collision probability across realistic chunk counts is negligible. `chunk_id` uniqueness already guarantees distinct full keys; the digest is over the full key.

### Risk 3: popoto internal method (`_sanitize_filename`) changes upstream
**Impact:** A future popoto release could rename/remove the override seam.
**Mitigation:** Unit test pins that `save()` + `load()` round-trip through our store for a long key; if upstream changes the seam, the test fails loudly rather than silently regressing.

## Race Conditions

No race conditions identified — chunk saves are synchronous within `_sync_chunks`
(single-threaded per document), and the filename function is a pure deterministic
function of the key. Concurrent saves of the *same* chunk id would target the same
live file, and popoto's `_atomic_write` (temp + `os.rename`) already makes each
write atomic.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2085] Re-embedding the entire vault is not in scope; the fix plus on-demand `rechunk_zero_chunk_documents()` (invoked where an embedding provider is configured) is sufficient. (This bug's own tracking issue; healing of the two reported docs happens via the helper / next reindex.)

Nothing else deferred — the store fix, both model wirings, the doctor guard, the
rechunk helper, tests, and docs are all in scope for this plan.

## Update System

- **Popoto schema:** no field is added, removed, or renamed; Redis keys and the `$CF` reference format are unchanged. Only the ContentField's `store` backend (filename derivation) changes. **No Redis-data migration is required.**
- **No confirmation migration (revised — CONCERN + NIT):** the earlier draft proposed a `confirm_content_references_load` migration. It is dropped for two reasons the war room surfaced: (1) `run_pending_migrations` records any run name in `completed` permanently, so running it during rollout on an empty/near-empty Redis would mark it complete forever and never re-verify once real long-path docs exist — false assurance (Risk & Robustness + History & Consistency). (2) Read-compatibility is *structurally* guaranteed by the parent `FilesystemStore`: `load()` / `_parse_reference` read the relative path embedded in the `$CF` reference and never re-derive it from key fields, so the override cannot break existing reads — nothing to confirm (Scope & Value nit). The repo's "Popoto model change → migrations.py entry" convention targets *schema/key/reference* changes; this change is none of those, so no `migrations.py` entry is warranted. Re-derivation of the currently-failing (zero-chunk) docs happens via `rechunk_zero_chunk_documents()` and the natural reindex path, not a migration.
- **New tunable through the settings convention (CONCERN, History & Consistency):** add a `POPOTO_MAX_CONTENT_FILENAME_BYTES` field to `config/settings.py` (default 200) and a placeholder + comment line in `.env.example`, per the repo's tunable-propagation convention, so `/update`'s env-completeness check knows the var exists. The store reads the value from settings (falling back to the default), not a bare `os.environ.get`.
- No new runtime dependencies to propagate; `/update` needs no other changes.

## Agent Integration

No agent integration required — this is an internal indexing/storage fix. No new
CLI entry point or MCP surface is needed for the core fix. The doctor guard is
reachable via the existing `python -m tools.doctor` entry point (already wired).
The `rechunk_zero_chunk_documents()` helper is invoked from the doctor `fix`
guidance / an existing indexer entry point, not a new agent tool.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/length-safe-content-store.md` describing the overflow bug, the `LengthSafeFilesystemStore` seam, the byte budget knob, read compatibility, the doctor guard, and the rechunk helper.
- [ ] Add an entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] Docstring on `LengthSafeFilesystemStore._sanitize_filename` explaining the cap, digest stability, and read compatibility.
- [ ] Grain-of-salt comment on `MAX_CONTENT_FILENAME_BYTES`.

## Success Criteria

- [ ] A `DocumentChunk` with a >255-byte derived key (including a **non-ASCII** long path) saves successfully and its `content` round-trips via `load()` with no `UnicodeDecodeError`.
- [ ] A `KnowledgeDocument` with a >255-byte derived key saves and round-trips (proves the override does not silently no-op on the 3-field model).
- [ ] Already-saved (short-key) content references still resolve unchanged (byte-identical filenames for keys under budget).
- [ ] `_check_knowledge_zero_chunk_documents` returns a `CheckResult` (never raises), is internally bounded, and flags zero-chunk documents (fails when present, passes when none).
- [ ] `rechunk_zero_chunk_documents()` regenerates chunks for a zero-chunk doc from stored content (idempotent).
- [ ] **Affected-doc validation (operator, bridge machine — CONCERN, Scope & Value):** running `rechunk_zero_chunk_documents(project_key="psyoptimal")` against the two reported RISCPoint Threat Summary KnowledgeDocuments yields `DocumentChunk.query.filter(document_doc_id=doc.doc_id)` non-empty for each. This runs where the NDA-partitioned vault + embedding provider exist (not CI); recorded as a post-merge deploy validation step.
- [ ] `POPOTO_MAX_CONTENT_FILENAME_BYTES` present in `config/settings.py` and `.env.example`.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

Solo dev builds this directly given Small appetite. No multi-agent orchestration
needed; a single builder in the session worktree implements all files with a
disjoint file set, then a validator pass confirms success criteria.

### Team Members

- **Builder (length-safe-store)**
  - Name: store-builder
  - Role: Implement the store, model wiring, doctor guard, rechunk helper, migration, tests, docs
  - Agent Type: builder
  - Resume: true

- **Validator (length-safe-store)**
  - Name: store-validator
  - Role: Verify success criteria and run narrow tests
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Tier 1 `builder` and `validator` suffice. Domain: Redis/Popoto data — the builder
must never use raw Redis on Popoto-managed keys (use the ORM).

## Step by Step Tasks

### 1. Implement length-safe store + wiring + guard + helper + migration
- **Task ID**: build-length-safe-store
- **Depends On**: none
- **Validates**: tests/unit/test_length_safe_content_store.py (create), tests/integration/test_document_chunk_long_path.py (create)
- **Assigned To**: store-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a `POPOTO_MAX_CONTENT_FILENAME_BYTES` field (default 200) to `config/settings.py` and a placeholder + comment in `.env.example`.
- Create `models/length_safe_content_store.py` with `LengthSafeFilesystemStore` (override `_sanitize_filename`, calling `FilesystemStore._sanitize_filename(name)` explicitly; truncate with `errors="ignore"`), `MAX_CONTENT_FILENAME_BYTES` (from settings, grain-of-salt comment), and a module-level singleton `length_safe_content_store`.
- Wire `models/document_chunk.py` and `models/knowledge_document.py` `content = ContentField(store=length_safe_content_store)`.
- Add `_check_knowledge_zero_chunk_documents` to `tools/doctor.py` (internally bounded sample) and register it in `get_checks` (Services category).
- Add `rechunk_zero_chunk_documents(project_key: str | None = None)` to `tools/knowledge/indexer.py` (re-runs `_sync_chunks(doc, doc.content, doc.project_key)` for zero-chunk docs; idempotent).
- Add unit tests (store: short key byte-identical; long ASCII key capped/deterministic/unique/round-trip; long **non-ASCII** key no `UnicodeDecodeError`; empty key; a KnowledgeDocument-shaped long key) and an integration test (DocumentChunk + KnowledgeDocument with a long file_path save and content loads).

### 2. Validation
- **Task ID**: validate-length-safe-store
- **Depends On**: build-length-safe-store
- **Assigned To**: store-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the new unit + integration tests via `scripts/pytest-clean.sh`.
- Confirm `python -m ruff check .` and `python -m ruff format --check .` on changed files.
- Confirm the doctor check appears in `python -m tools.doctor --json`.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-length-safe-store
- **Assigned To**: store-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/length-safe-content-store.md` and add the README index entry.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Store unit tests pass | `scripts/pytest-clean.sh tests/unit/test_length_safe_content_store.py -q` | exit code 0 |
| Long-path chunk integration test passes | `scripts/pytest-clean.sh tests/integration/test_document_chunk_long_path.py -q` | exit code 0 |
| Lint clean | `python -m ruff check models/length_safe_content_store.py models/document_chunk.py models/knowledge_document.py tools/doctor.py tools/knowledge/indexer.py config/settings.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/length_safe_content_store.py` | exit code 0 |
| Doctor guard registered | `python -m tools.doctor --json --quick` | output contains knowledge-zero-chunk |
| Settings tunable present | `grep -c "POPOTO_MAX_CONTENT_FILENAME_BYTES" config/settings.py .env.example` | output > 1 |
| No popoto site-packages edits | `git diff --name-only | grep -c "site-packages"` | match count == 0 |

## Critique Results

Verdict: **READY TO BUILD (with concerns)** — 0 blockers, 6 concerns, 2 nits. Revision pass applied 2026-07-16.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | UTF-8-safe truncation unspecified; bare `.decode()` raises on non-ASCII paths | Technical Approach now mandates `errors="ignore"` + a non-ASCII long-path unit test | Any UTF-8 byte-slice must decode with `errors="ignore"`; a bare `.decode("utf-8")` is the failure mode to test |
| CONCERN | Risk & Robustness | Zero-chunk doctor check runs unbounded on `--quick` | Check must short-circuit internally with a bounded sample | Only `quick`/`quality` kwargs exist in `get_checks`; the check function caps its own scan |
| CONCERN | Risk & Robustness + History & Consistency | Confirmation migration gives false assurance on empty machines | Migration dropped entirely; read-compat is structurally guaranteed by parent `load()` | `run_pending_migrations` marks any returned-None run complete forever; no migration avoids the trap |
| CONCERN | Scope & Value | No success criterion validates the two reported PsyOptimal docs regain chunks | Added operator/bridge validation running `rechunk_zero_chunk_documents(project_key="psyoptimal")` and asserting chunks > 0 | `_sync_chunks(doc, doc.content, doc.project_key)`; NDA-partitioned, runs on bridge not CI |
| CONCERN | Scope & Value | Wiring both models not justified | Added worst-case byte math (KnowledgeDocument key ≈ 274 bytes > 255) + KD long-path unit case | The 2 extra components push DocumentChunk over; KD overflows on slightly deeper paths |
| CONCERN | History & Consistency | New env var not threaded through settings/`.env.example` | Added `POPOTO_MAX_CONTENT_FILENAME_BYTES` to `config/settings.py` + `.env.example` | Store reads from settings, not a bare `os.environ.get` |
| NIT | Scope & Value | Confirmation migration redundant ceremony | Resolved by dropping the migration | — |
| NIT | History & Consistency | Parent-staticmethod call form underspecified | Technical Approach pins `FilesystemStore._sanitize_filename(name)` | Parent is a `@staticmethod`; explicit call is the pinning-test contract |
