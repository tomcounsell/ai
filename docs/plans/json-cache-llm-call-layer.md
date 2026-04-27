---
status: docs_complete
type: feature
appetite: Small
owner: Valor Engels
created: 2026-04-27
revised: 2026-04-27
revision_applied: true
tracking: https://github.com/tomcounsell/ai/issues/1182
last_comment_id:
---

# JSON Sidecar Cache for Deterministic Haiku Call Sites

## Problem

Two production code paths re-burn Haiku tokens on identical inputs every invocation, because there is no general-purpose persistent LLM-response cache in this repo.

1. **Intent classification — on the critical path.** `agent/intent_classifier.py:162` runs `classify_intent()` on every incoming Telegram message before the bridge can route or spawn a session. Each call is a ~200ms Haiku roundtrip. Inputs are `(message, context.recent_messages[-3:])` — fully deterministic. Status-check questions ("what are you working on", "any updates") repeat several times a day.
2. **Knowledge indexer summarization.** `tools/knowledge/indexer.py:69` `_summarize_content()` summarizes vault docs via Haiku during indexing. Documents rarely change between re-indexes, so the same Haiku call is repeated on every re-run.

**Current behavior:**
- Every Telegram message: a fresh Haiku call before routing, ~200ms blocking
- Every knowledge re-index: re-summarizes all unchanged docs, paying full Haiku cost each time

**Desired outcome:**
- Repeated identical inputs return cached results from a local on-disk file (~10ms read)
- Stale cache or cache miss falls through to the existing live API call (graceful degradation already built into both call sites)
- Cache hit/miss observable via analytics for measuring real-world hit rate

## Freshness Check

**Baseline commit:** `4a7029e0` (`Delete migrated plan: pm-session-liveness (#1172 / PR #1177)`)
**Issue filed at:** 2026-04-27T11:27:06Z (~10 hours before plan time)
**Disposition:** **Minor drift** — line numbers cited in the issue still hold, but two paths in the issue's pseudocode are wrong and one classifier reference conflates two distinct call sites.

**File:line references re-verified:**
- `agent/intent_classifier.py:162` `classify_intent()` — still holds.
- `agent/intent_classifier.py:226-230` graceful-degradation `except Exception` block — still holds (returns work intent on any failure).
- `tools/knowledge/indexer.py:69` `_summarize_content()` — still holds.
- `tools/knowledge/indexer.py:99-106` truncation fallback — still holds.
- `tools/classifier.py:365` `classify_message_intent_async` — still holds **but this is NOT the async variant of `classify_intent`**. It is a separate three-way intake classifier (#320), with a different prompt and different inputs. The issue's wording is misleading. Plan scope is `classify_intent` only; `classify_message_intent_async` is explicitly out of scope.
- `tools/analytics/collector.py` (cited in issue) — **does not exist**. The actual collector is `analytics/collector.py`. The CLI is `tools/analytics.py`. Public API is `record_metric(name, value, dimensions)`, not `emit()`.

**Cited sibling issues/PRs re-checked:**
- #677 — MERGED 2026-04-03 ("Replace Ollama emoji reactions with embedding-based lookup"). In-memory only.
- #992 — MERGED 2026-04-15 ("feat: terminal reactions via find_best_emoji (EmojiResult, lazy cache)"). In-memory only.
- Neither prior PR introduced persistent-disk LLM-response caching, confirming this is the first.

**Commits on main since issue was filed (touching referenced files):**
- None. `agent/intent_classifier.py`, `tools/knowledge/indexer.py`, `tools/classifier.py`, `analytics/collector.py`, and `utils/` are all untouched on main since 2026-04-27T11:27:06Z.

**Active plans in `docs/plans/` overlapping this area:** None. Spot-check of all `docs/plans/*.md` for `cache`, `json_cache`, `intent_classifier`, or `summarize` produced no overlap.

**Notes:** The issue's pseudocode comment `analytics.collector.emit(...)` and module path `tools/analytics/collector.py` are both wrong. Plan uses `from analytics.collector import record_metric` and emits with `record_metric("cache.hit", 1.0, {"namespace": ...})`. This is a documentation drift in the issue, not a behavioral drift in the codebase.

## Prior Art

Search of closed issues and merged PRs for "cache" returned no prior persistent-disk LLM-response cache work. Two existing in-memory caching PRs operate on a different problem (emoji embeddings).

- **PR #677**: "Replace Ollama emoji reactions with embedding-based lookup" — Built embedding-based emoji selection. In-memory cache only. Different problem (embeddings, not LLM responses); different shape (small fixed corpus, not unbounded user input).
- **PR #992**: "feat: terminal reactions via find_best_emoji (EmojiResult, lazy cache)" — Lazy in-memory cache for embedding lookups. In-memory only. Same shape conclusion as #677.

**Existing on-disk JSON precedents** (not LLM-response caches, but proof the pattern works in this repo):
- `data/doc_embeddings.json` (`tools/doc_impact_finder.py`) — JSON dump of embedding vectors, gitignored.
- `data/emoji_embeddings.json` (`tools/emoji_embedding.py:75`) — same pattern.
- `data/custom_emoji_embeddings.json` (`tools/emoji_embedding.py:78`) — same pattern.

No prior fix has been attempted for this exact problem. **No `## Why Previous Fixes Failed` section needed.**

## Research

No external research performed. The work is purely internal — stdlib only (`json`, `hashlib`, `pathlib`, `collections.OrderedDict`, `os`, `time`), no new libraries, no external APIs to evaluate. `os.replace` atomicity on POSIX is well-documented stdlib semantics. Skipping Phase 0.7 per `/do-plan` guidance ("Skip if: The work is purely internal").

## Data Flow

**Intent classification path (bridge critical path):**

1. **Entry point:** Telegram message arrives at `bridge/telegram_bridge.py`; bridge constructs context dict including `recent_messages[-3:]`.
2. **Intent classifier call:** `classify_intent(message, context)` is invoked at `agent/intent_classifier.py:162`.
3. **Cache lookup (NEW):** Helper computes `sha256("v1:" + message + "|" + recent_window)`; reads `data/cache/intent_classifier.json` from in-memory `OrderedDict`. If hit and not TTL-expired (TTL=2h), return cached `IntentResult`. Emit `cache.hit{namespace="intent_classifier"}`.
4. **Cache miss path:** Existing `_call_api()` runs against Haiku. Result wrapped in `IntentResult` and stored in cache. Atomic snapshot to disk (`os.replace` of `.tmp`). Emit `cache.miss{namespace="intent_classifier"}`.
5. **Failure path:** Existing `except Exception` at line 226 returns `IntentResult(intent="work", confidence=0.0, ...)` — **unchanged**. Cache failures (corrupt JSON, disk full) silently fall through to live API call. Cache itself never raises.
6. **Output:** Bridge receives `IntentResult` and routes accordingly.

**Knowledge indexer path (background indexing):**

1. **Entry point:** `tools/knowledge/indexer.py::full_scan` walks the work-vault; per-file or per-section `_summarize_content(content, file_path)` is called at line 69.
2. **Cache lookup (NEW):** Helper computes `sha256("v1:" + content[:4000] + "|" + filename)`; reads `data/cache/knowledge_summaries.json`. No TTL — knowledge content rarely changes and the version key handles invalidation. On hit, return cached summary string. Emit `cache.hit{namespace="knowledge_summaries"}`.
3. **Cache miss path:** Existing `client.messages.create(...)` runs against Haiku. Stripped summary stored in cache. Atomic snapshot. Emit `cache.miss{namespace="knowledge_summaries"}`.
4. **Failure path:** Existing `except Exception` at line 99 falls back to first-N-chars truncation — **unchanged**.
5. **Output:** `_summarize_content` returns the summary string to `_create_companion_memories`.

## Architectural Impact

- **New dependencies:** None. Stdlib only.
- **Interface changes:** None. The cache layer is internal to `classify_intent()` and `_summarize_content()`. Their public signatures and return types are unchanged.
- **Coupling:** Slightly increased — both call sites now depend on `utils/json_cache.py`. The helper has no reverse dependencies (it imports nothing except stdlib + optional `analytics.collector.record_metric` for hit/miss metrics).
- **Data ownership:** New filesystem state in `data/cache/`. Both files are gitignored (covered by existing `data/` rule at `.gitignore:181`). One writer per file: bridge process writes `intent_classifier.json`; indexer process writes `knowledge_summaries.json`. **Single-writer-per-file invariant must be documented and explicit.**
- **Reversibility:** Trivial. Delete `utils/json_cache.py`, revert two call-site changes, delete `data/cache/`. Both call sites already have try/except fallbacks, so removing the cache cannot regress behavior — only performance.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (issue is unusually complete and the recon revealed no scope ambiguity)
- Review rounds: 1 (formal `/do-pr-review` after build, plus standard `/do-plan-critique` before build)

This is a self-contained ~80-line helper plus two ~5-line call-site changes plus tests plus one docs file. No infrastructure, no migrations, no external dependencies, no agent integration, no MCP changes. Estimate ~half a day from build start to PR open.

## Prerequisites

No prerequisites — this work has no external dependencies. The `data/cache/` directory is created at runtime via `path.parent.mkdir(parents=True, exist_ok=True)` on first write.

## Solution

### Key Elements

- **`utils/json_cache.py`**: A small `JsonCache` class wrapping an `OrderedDict` with LRU eviction, plus a `get_or_compute()` helper that hashes inputs with a version prefix and emits hit/miss analytics.
- **`agent/intent_classifier.py` wire-up**: Module-level singleton `JsonCache(Path("data/cache/intent_classifier.json"), max_entries=2000)`. The existing `_call_api()` body is wrapped in `get_or_compute(...)` with `ttl=7200` and `version="v1"`. The existing `except Exception` at line 226 is preserved verbatim.
- **`tools/knowledge/indexer.py` wire-up**: Same pattern. `JsonCache(Path("data/cache/knowledge_summaries.json"), max_entries=5000)` with `ttl=None` (no TTL — content hash is implicit in the key) and `version="v1"`. Existing truncation fallback preserved.
- **Analytics integration**: `get_or_compute()` calls `analytics.collector.record_metric("cache.hit"|"cache.miss", 1.0, {"namespace": <stem>})`. The `record_metric` import is wrapped in try/except so analytics being unavailable does not break cache behavior.

### Flow

**Identical input arrives twice:**
First call → Cache miss → Haiku API → result stored → returned (slow, ~200ms). Second call → Cache hit → result returned directly (~10ms read from in-memory `OrderedDict`, no disk I/O on read).

**Prompt template changes during a deploy:**
Code change bumps `version="v1"` to `version="v2"` → all old keys are unreachable on the next read → they LRU-evict naturally as new entries land → no manual flush needed.

**Disk full / readonly filesystem:**
`os.replace` raises → silently caught in `_save()` → in-memory cache continues serving reads correctly → persistence resumes when disk frees.

**Corrupt JSON file on startup:**
`json.loads()` raises → silently caught in `_load()` → `_data` starts as empty `OrderedDict` → first write produces a fresh valid file.

**Process crash mid-write:**
`os.replace` is atomic on POSIX. Either the old `cache.json` or the new one is visible — never a partial. The `.tmp` file may be left behind; not a correctness issue, and it is overwritten on the next save.

### Technical Approach

- **Stdlib only.** `json`, `hashlib`, `pathlib`, `collections.OrderedDict`, `os`, `time`. No new dependencies.
- **Filesystem-only.** No Redis, no Popoto, no SQLite. The repo's documented invariant is Redis = ephemeral coordination + Popoto-managed ORM state. SQLite was deliberately removed because writer locks froze agent sessions. JSON-on-disk has neither failure class.
- **Two cache files, two namespaces:** `data/cache/intent_classifier.json` (TTL 2h, max 2000 entries) and `data/cache/knowledge_summaries.json` (no TTL, max 5000 entries). Each file has exactly one writer process by construction:
  - `intent_classifier.json` is written only by the bridge process.
  - `knowledge_summaries.json` is written only by the indexer process.
- **Atomic snapshot via `os.replace`.** Writes go to `cache.tmp`, then `os.replace(tmp, final)`. POSIX guarantees the rename is atomic — readers see either the old or new file, never a partial.
- **Version-prefixed sha256 keying.** Each entry's key is `sha256(f"{version}:{key_input}").hexdigest()`. Bumping `version` on prompt template changes makes old keys unreachable; they LRU-evict naturally. No tag bookkeeping, no separate eviction job.
- **LRU eviction.** Backed by `collections.OrderedDict`. On `set`, after insert, while `len(_data) > max_entries`, popleft (`popitem(last=False)`). On `get`, `move_to_end` to mark recency.
- **Analytics emits per namespace.** `record_metric("cache.hit", 1.0, {"namespace": cache.path.stem})` and `record_metric("cache.miss", 1.0, ...)` so `python -m tools.analytics summary` shows hit-rate per cache file. The analytics import is wrapped in try/except inside `get_or_compute` — if `analytics.collector` raises, caching still works.
- **Failsafe by construction.** Both call sites already have `except Exception` fallbacks to the live API. Cache failure → cache miss → existing fallback behavior. The cache itself raises nothing.

**Concrete API signatures:**
```python
class JsonCache:
    def __init__(self, path: Path, max_entries: int = 2000) -> None: ...
    def get(self, key: str, ttl: int | None = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...

def get_or_compute(
    cache: JsonCache,
    key_input: str,
    compute_fn: Callable[[], T],
    *,
    ttl: int | None = None,
    version: str = "v1",
) -> T: ...
```

The helper stays at ~80 lines as the issue estimates. No type magic, no async layer (both call sites have their own threading concerns and the cache is synchronous — `intent_classifier.py` already wraps the API call in `asyncio.to_thread`, so the cache lookup runs in the thread alongside the API call when needed).

### Serialization Contract

`JsonCache.set/get` operate on **JSON-serializable values only** — `dict`, `list`, `str`, `int`, `float`, `bool`, `None`, or compositions thereof. The helper does NOT attempt to pickle/unpickle dataclasses or custom objects. This keeps the helper trivial and forces call sites to be explicit about wire format.

**Implications for each call site:**

- **`agent/intent_classifier.py` — `IntentResult` is a frozen dataclass.** The wire-up MUST convert in both directions:
  - **Write path (cache miss):** after `_call_api()` returns the parsed `IntentResult`, convert to dict via `dataclasses.asdict(result)` and store the dict. The `compute_fn` passed to `get_or_compute` therefore returns a `dict`, not an `IntentResult`.
  - **Read path (cache hit + cache miss return):** rehydrate by constructing `IntentResult(**cached_dict)`. The wire-up wraps `get_or_compute` and returns `IntentResult(**dict_from_cache)` regardless of hit/miss path.
  - Frozen-dataclass `__init__` accepts only the dataclass fields (`intent`, `confidence`, `reasoning`); JSON round-trip naturally preserves these three primitive types. Computed `@property` accessors are recomputed on each access — they do not need to be persisted.
- **`tools/knowledge/indexer.py` — `_summarize_content` returns `str`.** No conversion needed; `compute_fn` returns the summary string directly. Cache stores strings as-is.

This contract is documented in the helper's module docstring with a one-line rule: "Cache values must be JSON-serializable. For dataclasses, store `dataclasses.asdict(...)` and rehydrate at the call site."

## Failure Path Test Strategy

### Exception Handling Coverage

- [x] `JsonCache._load()` swallows JSON decode errors silently — test asserts that calling `_load()` on a corrupt file results in `_data == OrderedDict()` (empty, not crashed). Logger.warning optional but not required.
- [x] `JsonCache._save()` swallows IOError / OSError silently — test asserts that a `JsonCache` whose path points to a readonly directory still serves reads correctly after `set()`. (Mock `Path.write_text` to raise IOError.)
- [x] `get_or_compute()` swallows analytics emission errors — test asserts that when `analytics.collector.record_metric` is patched to raise, the cached value is still returned correctly. Importing `analytics.collector` is itself wrapped in try/except inside `get_or_compute`.
- [x] **Existing fallbacks preserved.** `agent/intent_classifier.py:226-230` and `tools/knowledge/indexer.py:99-106` are not modified by this work. Existing tests cover those fallbacks; we add tests asserting the cache's presence does not change their behavior.

### Empty/Invalid Input Handling

- [x] Empty `message` to `classify_intent()` — already handled upstream; the cache key still hashes deterministically (`sha256("v1:|")` is a valid cache key). Test asserts the cache layer does not crash on empty/whitespace input.
- [x] Empty `content` to `_summarize_content()` — current behavior is to call Haiku with an empty body; cache layer preserves this and stores the empty-input result.
- [x] No agent-output processing in scope — neither call site produces output that loops back to the agent.

### Error State Rendering

- [x] No user-visible output is added by this work. Both call sites are internal; their outputs feed downstream code paths that already render errors elsewhere.

## Test Impact

- [x] `tests/unit/test_intent_classifier.py::TestClassifyIntent` (entire class, 7 tests) — UPDATE: add an `autouse=True` `isolated_cache` fixture (see Step 5 for the full snippet) so every test runs against a `tmp_path`-rooted cold cache. Existing assertion logic is unchanged. **Affected tests:** `test_teammate_classification`, `test_work_classification`, `test_collaboration_classification`, `test_other_classification`, `test_no_api_key_defaults_to_work`, `test_api_error_defaults_to_work`, `test_context_passed_to_api`. The two tests that do NOT call the mocked Haiku client (`test_no_api_key_defaults_to_work` returns early before cache lookup; `test_api_error_defaults_to_work` raises before cache `set`) still benefit from the fixture for module-state hygiene.
- [x] `tests/unit/test_knowledge_indexer.py::TestSummarizeContent` (2 tests) — UPDATE: same autouse fixture pattern. **Affected tests:** `test_summarize_uses_haiku_constant`, `test_summarize_fallback_on_api_failure`. The fallback test still passes because the falsy/exception path bypasses cache write per the empty-result skip rule.
- [x] `tests/tools/test_classifier.py` — UNCHANGED. This file exercises `classify_request` (work-request classifier), which is out of scope per the No-Gos section.
- [x] **New test file** `tests/unit/test_json_cache.py` (CREATE): covers the helper's own behavior — hit, miss, fallback-on-corrupt-file, TTL expiry, LRU eviction at `max_entries`, atomic write semantics (no partial file visible after a simulated crash mid-save), version-key invalidation (different `version=` parameters produce different keys for identical input), **falsy-result-not-cached** (compute_fn returns `""`, `None`, or `{}` → cache stays empty; subsequent calls re-invoke `compute_fn`).

No DELETE or REPLACE dispositions. All existing impacts are UPDATE: autouse pytest fixture for cache isolation.

## Rabbit Holes

- **Generalizing the helper into a "cache framework" before we have a third namespace.** The issue's scaling-path table explicitly defers this. The helper is ~80 lines; resist the urge to add tag-based eviction, key prefixes, multi-backend strategies, decorators, or LRU/LFU/ARC strategy plug-ins until a third call site shows up.
- **Caching `classify_message_intent_async` (`tools/classifier.py:365`) opportunistically.** It looks similar but its prompt and inputs are different and the recon explicitly identifies it as out of scope. Defer until measured.
- **Adding `fcntl.flock` "just in case" two writers ever land.** Single-writer-per-file is an invariant we guard via documentation and review, not via locks. If we add `flock` now we own its deadlock failure mode forever.
- **Migrating `data/doc_embeddings.json` or `data/emoji_embeddings.json` to use this helper.** Different role (vector store, not response cache), different access pattern (bulk load + sparse update, not granular hit/miss), different lifecycle. Leave them alone.
- **Embedding-based deduplication of cache keys** ("classify near-identical messages to the same key"). This is a different feature and breaks the determinism invariant of the cache. Reject on sight.
- **Atomic per-key writes** instead of full-snapshot atomic replace. Per-key writes need a real database (SQLite, BDB, etc.). Snapshot-replace works because cache files are small and writes are infrequent relative to reads. Sticking with snapshot is the explicit design.

## Risks

### Risk 1: Cache file grows unboundedly between LRU evictions

**Impact:** Disk usage creeps up over time if the eviction logic has a bug, or if `max_entries` is set too high for the disk available.
**Mitigation:** `max_entries` is fixed at 2000 (intent) and 5000 (summaries). Each entry stores at most a small dict (intent: ~200 bytes; summary: ~500 bytes). Worst case: ~2.5 MB per cache file. Test asserts `len(cache._data) <= max_entries` after stress-loading 2× the limit. The scaling-path docs flag the "≥10 MB or ≥100ms load time" trigger for switching to `shelve` or partitioning.

### Risk 2: Test pollution across runs from a shared on-disk cache

**Impact:** Tests that mock Haiku could see stale cached results from prior test runs and never invoke the mocked client, producing false-negative assertion failures.
**Mitigation:** Tests must use a `tmp_path` fixture and monkeypatch the module-level `_cache` singleton at each call site. This is enforced by Test Impact section above. The new `test_json_cache.py` also uses `tmp_path` exclusively.

### Risk 3: A second writer process is added later without anyone noticing the invariant

**Impact:** Two writers race on `os.replace` and one wins, silently losing the other's writes. No data corruption (atomicity holds), but a fraction of cache writes vanish.
**Mitigation:** Single-writer-per-file is documented in `docs/features/json-cache-layer.md` as an explicit invariant. The scaling-path table calls out `fcntl.flock` as the upgrade trigger ("a second writer needs to write the same file"). A code-reviewer checking PRs that touch `utils/json_cache.py` or its consumers will see the invariant in the doc.

### Risk 4: `record_metric` is unavailable or raises during emit

**Impact:** Hit/miss observability disappears; could mask a regression in cache behavior.
**Mitigation:** `get_or_compute` wraps the analytics import and call in try/except. Cache itself continues to work. Test `test_json_cache.py::test_get_or_compute_when_analytics_unavailable` asserts the wrapped behavior.

## Race Conditions

### Race 1: Concurrent reads of the in-memory cache during async classify_intent

**Location:** `agent/intent_classifier.py:162` (`classify_intent` is async) accessing the module-level singleton `_cache`.
**Trigger:** Two concurrent `classify_intent` invocations (both in the bridge event loop) compute keys, both miss, both call Haiku, both `set()` results.
**Data prerequisite:** None — `_cache._data` is an `OrderedDict`, which is thread-safe for individual operations in CPython but not atomic for compound read-modify-write across the GIL boundary in async code.
**State prerequisite:** The cache must remain internally consistent (no torn writes, no key with `None` value).
**Mitigation:** This is benign. Both calls hit the API independently and store the same result. The second `set()` overwrites the first with an identical value. LRU `move_to_end` is idempotent. No correctness violation; just a missed optimization. We do NOT add an `asyncio.Lock` because the lock contention itself would be a worse failure than the duplicate Haiku call. Documented in the helper docstring.

### Race 2: Reader visible to a write-in-progress on disk

**Location:** `JsonCache._save()` (`os.replace` of `.tmp` to final).
**Trigger:** Reader process starts up while writer process is in the middle of `_save()`.
**Data prerequisite:** Final cache file must always be parseable JSON or absent.
**State prerequisite:** Atomic rename on POSIX guarantees readers see either the old `cache.json` or the new one.
**Mitigation:** `os.replace` is atomic on POSIX. The `.tmp` file is never opened by the reader (only the final path). If `_load()` somehow does see a malformed file (e.g., on a non-POSIX filesystem), the corrupt-file path silently starts with empty `_data`. **Single-writer-per-file invariant guarantees Race 2 only matters cross-process between writer and a future read-only sibling — there is no such sibling today, but the invariant holds the line.**

## No-Gos (Out of Scope)

- **Caching `classify_message_intent_async` (`tools/classifier.py:365`).** Different classifier (3-way intake intent vs 4-way intent), different prompt, different inputs. Add later if measured hit-rate justifies a third namespace.
- **Caching `classify_request` (`tools/classifier.py:57`).** Work-request classifier; lower frequency than `classify_intent`.
- **Caching the routing fallback at `bridge/routing.py:846`.** Already deferred by the issue.
- **Migrating `data/doc_embeddings.json`, `data/emoji_embeddings.json`, or `data/analytics.db` to this helper.** Different roles.
- **Multi-process write coordination via `fcntl.flock` or filesystem locks.** Single-writer-per-file invariant is the design. Scaling path documents the upgrade trigger.
- **Cross-machine cache sharing.** Scaling-path documents `Popoto CacheEntry` model as the future direction; not built now.
- **Embedding-based cache key deduplication.** Different feature; breaks the determinism invariant.
- **A general-purpose `@cached` decorator.** Two call sites do not justify a framework. Helper functions only.
- **Building the "follow-up after 7 days in production" hit-rate report mechanism.** This is a post-deploy tracking task, not build work. We will record a reminder in the PR description and run `python -m tools.analytics summary` manually after 7 days.

## Update System

**No update system changes required.** The cache lives entirely in `data/cache/` which is gitignored (covered by `.gitignore:181`'s `data/` rule). New machines will start with a cold cache; first request per identical input pays the live API cost, subsequent requests are warm. No migration needed for existing installations — `data/cache/` is created on first write via `mkdir(parents=True, exist_ok=True)`.

The `/update` skill (`scripts/remote-update.sh`) does not need to do anything: there are no new dependencies, no new config files, no new environment variables. The `record_metric` integration uses the existing `analytics/` module which is already deployed everywhere.

## Agent Integration

**No agent integration required — this is bridge-internal and indexer-internal.** Specifically:

- The intent classification path runs in the bridge process (`bridge/telegram_bridge.py` calls `classify_intent` directly). The cache layer is invisible to the agent; the agent never invokes `classify_intent` itself.
- The knowledge indexer runs in its own process (`tools/knowledge/indexer.py`, called from the indexer service). The agent never invokes `_summarize_content`.
- No new tools, no MCP server changes, no `.mcp.json` updates, no `mcp_servers/` directory changes.
- The agent will indirectly benefit from faster classification (cached intent → faster bridge routing → faster session pickup), but no agent-facing API changes.

## Documentation

### Feature Documentation
- [x] Create `docs/features/json-cache-layer.md` documenting:
  - When to add a new call site (deterministic inputs, repeated identical calls, fallback already in place)
  - Version-bumping for prompt changes (single string change at one call site)
  - What NOT to cache (Popoto-managed data, non-deterministic inputs, side-effecting calls)
  - The single-writer-per-file invariant — explicit and prominent
  - The full scaling path (table from the issue) as documented future-state design — NOT built work
  - Hit-rate observability via `python -m tools.analytics summary`
- [x] Add entry to `docs/features/README.md` index table (alphabetical insertion enforced by hook).

### External Documentation Site
None — this repo does not use Sphinx / MkDocs / Read the Docs.

### Inline Documentation
- [x] Module docstring on `utils/json_cache.py` explaining the helper's contract and the single-writer invariant.
- [x] Inline comment at each cache singleton (`_cache = JsonCache(...)`) explaining why that namespace, what TTL was chosen, and what version is in use.

## Success Criteria

- [x] `utils/json_cache.py` exposes `JsonCache` and `get_or_compute()` with version-prefix sha256 keying, TTL, and atomic snapshot via `os.replace`.
- [x] `classify_intent()` (`agent/intent_classifier.py:162`) routes through the helper with `ttl=7200` and `data/cache/intent_classifier.json`. The existing graceful-degradation `except Exception` block at line 226 is preserved verbatim.
- [x] `_summarize_content()` (`tools/knowledge/indexer.py:69`) routes through the helper with `ttl=None` and `data/cache/knowledge_summaries.json`. The existing truncation fallback at line 99 is preserved.
- [x] `analytics.collector.record_metric` emits `cache.hit` and `cache.miss` events with `{"namespace": ...}` dimension; visible via `python -m tools.analytics summary`.
- [x] Tests cover: hit, miss, fallback-on-corrupt-file, TTL expiry, LRU eviction at `max_entries`, atomic write (no partial file on simulated crash mid-save), version-key invalidation, analytics-unavailable graceful degradation.
- [x] Affected existing tests (`tests/unit/test_intent_classifier.py`, `tests/unit/test_knowledge_indexer.py`) updated to use per-test cache isolation via `tmp_path` and module-singleton monkeypatch.
- [x] `docs/features/json-cache-layer.md` created with all the content listed under Documentation, including the scaling path as future-state design.
- [x] `docs/features/README.md` index updated in alphabetical position.
- [x] Tests pass (`/do-test`).
- [x] Documentation updated (`/do-docs`).
- [x] **Post-deploy reminder (NOT build work):** PR description includes a note to revisit hit rates after 7 days via `python -m tools.analytics summary`.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly.

### Team Members

- **Builder (cache helper)**
  - Name: `cache-helper-builder`
  - Role: Implement `utils/json_cache.py` with `JsonCache` class and `get_or_compute()` helper. Stdlib only. Wrapper for analytics emission with try/except.
  - Agent Type: builder
  - Resume: true

- **Builder (intent classifier wire-up)**
  - Name: `intent-wire-builder`
  - Role: Wire `agent/intent_classifier.py:162` to the cache helper. Preserve graceful-degradation block verbatim. Add module-level singleton with documenting comment.
  - Agent Type: builder
  - Resume: true

- **Builder (indexer wire-up)**
  - Name: `indexer-wire-builder`
  - Role: Wire `tools/knowledge/indexer.py:69` to the cache helper. Preserve fallback verbatim. Add module-level singleton with documenting comment.
  - Agent Type: builder
  - Resume: true

- **Test engineer (helper tests)**
  - Name: `cache-helper-tester`
  - Role: Author `tests/unit/test_json_cache.py` covering hit, miss, fallback-on-corrupt, TTL expiry, LRU eviction, atomic write, version invalidation, analytics-unavailable.
  - Agent Type: test-engineer
  - Resume: true

- **Test engineer (existing test updates)**
  - Name: `existing-test-updater`
  - Role: Update `tests/unit/test_intent_classifier.py` and `tests/unit/test_knowledge_indexer.py` for per-test cache isolation. Verify they still assert the original mocked-Haiku behavior.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian (feature doc)**
  - Name: `json-cache-documentarian`
  - Role: Author `docs/features/json-cache-layer.md` with the contract, the single-writer invariant, the version-bumping process, the do-not-cache list, and the scaling-path future-state design. Add the alphabetical entry to `docs/features/README.md`.
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: `final-validator`
  - Role: Run all tests, verify the success criteria checkboxes, run `python -m tools.analytics summary` against a hot cache to verify metric emission, confirm `docs/features/README.md` is alphabetized.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

(Standard tier 1 plus documentarian.)

## Build Sequencing

The build order is non-trivial because adding the cache layer to existing call sites WILL break their tests until the autouse fixture is in place. To avoid a window where `pytest tests/unit/` is broken on `main` or on a feature branch:

```
1. build-cache-helper          (creates utils/json_cache.py — no impact on existing tests)
2. build-cache-tests           (validates the helper in isolation)
3. update-existing-tests       (adds autouse isolated_cache fixture to test files)
                               ↳ At this point fixtures reference _cache singletons that
                                  do not yet exist. Use a guarded import + skip:
                                  `pytest.importorskip("utils.json_cache")` at the top
                                  of each fixture so the fixture is a no-op until wire-up
                                  lands. After wire-up the fixture activates automatically.
4. build-intent-wire           (adds _cache singleton to agent/intent_classifier.py;
                                fixture activates; tests stay green)
5. build-indexer-wire          (same for tools/knowledge/indexer.py)
6. document-feature            (writes docs/features/json-cache-layer.md)
7. validate-all                (full pytest + lint + smoke)
```

**Why this order matters:** if Steps 4-5 land before Step 3, every commit between them shows a red `pytest tests/unit/test_intent_classifier.py` because the second test in the class runs against a hot cache and the mock_client is bypassed. CI would block the PR until Step 3 lands. The order above keeps the tree green at every commit boundary.

**Single-PR caveat:** since this entire feature ships in one PR, individual commits in the PR don't gate CI — only the final tip does. The order above is still recommended because (a) `git bisect` stays useful, and (b) if the PR is split or any commit is reverted, no commit leaves the tree red.

## Step by Step Tasks

### 1. Implement `utils/json_cache.py`
- **Task ID**: build-cache-helper
- **Depends On**: none
- **Validates**: tests/unit/test_json_cache.py (create — covered by build-cache-tests)
- **Assigned To**: cache-helper-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `utils/json_cache.py` implementing `JsonCache` (init/load/save/get/set with `OrderedDict` LRU) and `get_or_compute` (hashing + analytics emission).
- Wrap the `analytics.collector` import and `record_metric` call in try/except so analytics being unavailable never breaks caching.
- **Empty-result skip:** Inside `get_or_compute`, after calling `compute_fn()` and before `cache.set()`, add a guard: if the result is falsy (`None`, empty string, empty dict, empty list, `False`, `0`), return it WITHOUT caching. This prevents permanent caching of transient API flakes that returned empty content. Document this contract in the docstring: "Falsy results are not cached." Cache hits still return cached values regardless — only the write path skips falsy values.
- Stdlib imports only: `hashlib`, `json`, `os`, `time`, `collections.OrderedDict`, `pathlib.Path`, `typing.Any | Callable | TypeVar`.
- Module docstring: state the contract and the single-writer-per-file invariant. Specifically:
  - "Cache values must be JSON-serializable. For dataclasses, store `dataclasses.asdict(...)` and rehydrate at the call site."
  - "Single-writer-per-file invariant: each `JsonCache` instance must be written to by exactly one process. Multi-writer scenarios will silently lose writes (atomic `os.replace` guarantees no corruption, but last-write-wins). See `docs/features/json-cache-layer.md` for the upgrade path (`fcntl.flock`)."
  - "Falsy results from `compute_fn` are not cached."
- Confirm code style: `python -m ruff format utils/json_cache.py && python -m ruff check utils/json_cache.py`.

### 2. Author `tests/unit/test_json_cache.py`
- **Task ID**: build-cache-tests
- **Depends On**: build-cache-helper
- **Assigned To**: cache-helper-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Use `tmp_path` for every test — never write to `data/cache/`.
- Cover: hit, miss, fallback-on-corrupt-file (write garbage to the path, then construct `JsonCache`), TTL expiry (use a small TTL and `time.sleep(...)` or monkeypatch `time.time`), LRU eviction (set `max_entries=3`, push 5 entries, assert oldest 2 are gone, assert recency is preserved by `get`), atomic-write (mock `Path.write_text` to raise after writing, assert no partial file is visible), version invalidation (same input, different `version=` produces different cache slot), analytics-unavailable (patch `analytics.collector.record_metric` to raise; assert helper still returns the cached or freshly-computed value).
- Run `pytest tests/unit/test_json_cache.py -v` and confirm all pass.

### 3. Wire `agent/intent_classifier.py`
- **Task ID**: build-intent-wire
- **Depends On**: build-cache-helper, update-existing-tests
- **Validates**: tests/unit/test_intent_classifier.py (the autouse fixture activates as soon as the `_cache` singleton lands)
- **Assigned To**: intent-wire-builder
- **Agent Type**: builder
- **Parallel**: true (with build-indexer-wire)
- Add module-level singleton: `_cache = JsonCache(Path("data/cache/intent_classifier.json"), max_entries=2000)` near the top of the module (under imports, with a documenting comment specifying TTL choice and version).
- Inside `classify_intent()`, route through `get_or_compute` AFTER `user_content` is built (line ~202) and BEFORE `_call_api()` is invoked (line ~214). The cache layer is inside the existing `try` block at line 183 so the existing `except Exception` at line 226 still catches any cache-helper raise (defense-in-depth — `get_or_compute` itself never raises, but the `try` boundary is preserved).
- **Key input (canonical):** `f"{message}\n---\n{recent_window}"` where `recent_window` is the formatted multi-line `"Recent conversation:\n- msg1\n- msg2\n- msg3\n"` block (or the empty string if `context` is `None` or `recent_messages` is missing/empty). Using the same exact formatted block that gets sent to the API guarantees deterministic key derivation — any change to the prompt-building logic upstream is automatically reflected in the cache key. The `\n---\n` separator avoids ambiguity when message text itself contains newlines.
- **Serialization (CRITICAL):** `IntentResult` is a frozen dataclass. The `compute_fn` passed to `get_or_compute` MUST return `dataclasses.asdict(result)` (a dict), NOT the dataclass itself. The wire-up rehydrates with `IntentResult(**cached_dict)` after `get_or_compute` returns. Without this, JSON serialization fails with `TypeError: Object of type IntentResult is not JSON serializable`.
- Concrete sketch:
  ```python
  recent_window = ""  # or build the formatted block
  # ... existing user_content assembly ...
  cache_input = f"{message}\n---\n{recent_window}"
  def _call_and_serialize() -> dict:
      response = client.messages.create(...)  # existing _call_api body
      raw_text = response.content[0].text.strip()
      result = _parse_classifier_response(raw_text)
      return dataclasses.asdict(result)
  cached_dict = await asyncio.to_thread(
      get_or_compute, _cache, cache_input, _call_and_serialize, ttl=7200, version="v1"
  )
  result = IntentResult(**cached_dict)
  ```
- TTL=7200 (2 hours), version="v1".
- Preserve the existing `except Exception` graceful-degradation block at line 226 verbatim. Do not modify the existing logger calls. The elapsed-ms log at line 219 still runs whether the result came from cache or live API; this is intentional (a hot cache will show low ms; cold will show ~200ms).
- Confirm `python -m ruff format agent/intent_classifier.py && python -m ruff check agent/intent_classifier.py`.

### 4. Wire `tools/knowledge/indexer.py`
- **Task ID**: build-indexer-wire
- **Depends On**: build-cache-helper, update-existing-tests
- **Validates**: tests/unit/test_knowledge_indexer.py (the autouse fixture activates as soon as the `_cache` singleton lands)
- **Assigned To**: indexer-wire-builder
- **Agent Type**: builder
- **Parallel**: true (with build-intent-wire)
- Add module-level singleton: `_cache = JsonCache(Path("data/cache/knowledge_summaries.json"), max_entries=5000)`.
- Inside `_summarize_content()`, the cache layer wraps the Haiku call but stays INSIDE the existing `try` block at line 75 so the existing truncation fallback at line 99 still catches any helper raise.
- **Key input (canonical):** `f"{content[:4000]}\n---\n{filename}"`. Using `\n---\n` as separator (not `|`) for the same reason as intent: avoid ambiguity when content/filename contains pipes.
- **Serialization:** `_summarize_content` returns a `str`. `compute_fn` returns the stripped summary string directly. No dataclass conversion needed.
- Concrete sketch:
  ```python
  def _summarize_via_haiku() -> str:
      response = client.messages.create(...)  # existing body
      return response.content[0].text.strip()
  cache_input = f"{content[:4000]}\n---\n{filename}"
  summary = get_or_compute(_cache, cache_input, _summarize_via_haiku, ttl=None, version="v1")
  if summary:
      return summary
  ```
- The empty-string check after `get_or_compute` is preserved: if Haiku returned an empty string, it should NOT be cached (the cache would prevent retry). Action: skip caching when `compute_fn` returns falsy. **Implementation note:** add an `if not result: return result` guard inside `get_or_compute` BEFORE `cache.set()` so empty/None/False values bypass storage entirely. This avoids permanent caching of one-off Haiku flakes that returned empty content.
- `ttl=None` (no TTL — content hash is the key, version handles invalidation), version="v1".
- Preserve the existing truncation fallback at line 99 verbatim.
- Confirm format and lint.

### 5. Update existing tests for cache isolation
- **Task ID**: update-existing-tests
- **Depends On**: build-cache-helper
- **Required BEFORE**: build-intent-wire, build-indexer-wire (NOT after — see "Build Sequencing" note below)
- **Assigned To**: existing-test-updater
- **Agent Type**: test-engineer
- **Parallel**: false
- **Use an `autouse=True` pytest fixture, NOT per-test monkeypatch.** Per-test monkeypatch is fragile: a developer adding a new test forgets the monkeypatch, the cache hot-loads from a previous test's data, the `mock_client.messages.create` mock is bypassed, and the test silently passes for the wrong reason. Autouse closes that hole structurally.
- For `tests/unit/test_intent_classifier.py::TestClassifyIntent`, add at the top of the test class (or as a module-level fixture):
  ```python
  @pytest.fixture(autouse=True)
  def isolated_cache(monkeypatch, tmp_path):
      """Replace the module-level cache singleton with a tmp_path-rooted instance.
      Runs before every test to guarantee a cold cache. Required because the
      cache layer would otherwise short-circuit the mocked Haiku client.

      Guarded with hasattr so this fixture is a no-op if the wire-up hasn't
      landed yet (Build Sequencing step 4/5). Once the singleton exists, the
      fixture activates automatically with no code change.
      """
      from agent import intent_classifier
      if not hasattr(intent_classifier, "_cache"):
          return  # wire-up hasn't landed; nothing to isolate
      from utils.json_cache import JsonCache
      monkeypatch.setattr(
          intent_classifier,
          "_cache",
          JsonCache(tmp_path / "intent_cache.json", max_entries=10),
      )
  ```
- Same pattern for `tests/unit/test_knowledge_indexer.py::TestSummarizeContent` (replacing `_cache` in the indexer module). The `monkeypatch` fixture's automatic teardown restores the original `_cache` after each test, so module-level state never leaks.
- Verify the fix: temporarily comment out the autouse fixture and run `pytest tests/unit/test_intent_classifier.py::TestClassifyIntent -v`. Without the fixture, tests should pass on first run but the second run should show some tests failing because the cached results are returned instead of the mocked client being invoked. Re-enable the fixture; all tests should pass on every run.
- Run `pytest tests/unit/test_intent_classifier.py tests/unit/test_knowledge_indexer.py -v`. Confirm all pass.
- Confirm `mock_client.messages.create.assert_called_once()` (or `.assert_called()`) still passes for tests that assert it — the autouse fixture guarantees a cold cache, so the mock IS invoked.

### 6. Author feature doc
- **Task ID**: document-feature
- **Depends On**: build-intent-wire, build-indexer-wire, update-existing-tests
- **Assigned To**: json-cache-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/json-cache-layer.md` covering: the contract, single-writer invariant, version-bumping for prompt changes, do-not-cache list (Popoto-managed, non-deterministic, side-effecting), how to read hit/miss metrics via `python -m tools.analytics summary`, and the full scaling-path table from the issue body labeled clearly as future-state design (not built work).
- Add entry to `docs/features/README.md` in alphabetical position. The PostToolUse hook auto-fixes alphabetization, but place it correctly to avoid noisy diffs.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-cache-tests, update-existing-tests, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` and confirm exit 0.
- Run `python -m ruff check . && python -m ruff format --check .` and confirm clean.
- Run `python -c "from utils.json_cache import JsonCache, get_or_compute; print('ok')"` and confirm import works.
- Run a smoke test that exercises both call sites once to populate caches, then verify `data/cache/intent_classifier.json` and `data/cache/knowledge_summaries.json` exist and parse as valid JSON.
- Run `python -m tools.analytics summary` and verify `cache.hit` and `cache.miss` metrics are listed.
- Confirm `docs/features/README.md` is alphabetically sorted at the new entry.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_json_cache.py tests/unit/test_intent_classifier.py tests/unit/test_knowledge_indexer.py -x -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -x -q` | exit code 0 |
| **Repeated test runs (pollution check)** | `pytest tests/unit/test_intent_classifier.py::TestClassifyIntent -v --count 3` (or run 3x consecutively) | exit code 0 every run; mock asserts always pass |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Helper importable | `python -c "from utils.json_cache import JsonCache, get_or_compute"` | exit code 0 |
| Intent cache file format | `python -c "import json; json.load(open('data/cache/intent_classifier.json'))"` after one classify_intent call | exit code 0 |
| **Intent cache round-trip** | python smoke: call `classify_intent("test")` twice → second call returns identical `IntentResult` and elapsed_ms < 50ms | second-call ms < 50ms |
| Summary cache file format | `python -c "import json; json.load(open('data/cache/knowledge_summaries.json'))"` after one indexer run | exit code 0 |
| Analytics emits namespace | `python -m tools.analytics summary` | output contains `cache.hit` |
| Feature doc exists | `test -f docs/features/json-cache-layer.md` | exit code 0 |
| Docs README has entry | `grep -c "json-cache-layer.md" docs/features/README.md` | output > 0 |

## Critique Results

The /do-plan-critique pass on 2026-04-27 returned the verdict **NEEDS REVISION** without populating per-finding details (the war-room session ran but the findings table was not written back to the plan). This revision pass synthesizes the most likely concerns based on a self-critique re-read of the plan, addresses them, and documents the resolutions below. If a fresh /do-plan-critique surfaces additional findings, they will be appended to this table.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | self (Adversary) | `IntentResult` is a frozen dataclass; the original plan stored it via `cache.set(value)` which would fail with `TypeError: Object of type IntentResult is not JSON serializable`. Build would crash on the first cache miss. | Solution → Serialization Contract; Step 3 wire-up | `compute_fn` returns `dataclasses.asdict(result)` (a dict). Wire-up rehydrates with `IntentResult(**cached_dict)` on every return. The helper itself is dataclass-agnostic — call sites own the conversion. |
| BLOCKER | self (Operator) | Existing `tests/unit/test_intent_classifier.py` tests share process-level state. After wire-up, the second test in `TestClassifyIntent` runs against a hot cache; `mock_client.messages.create.assert_called_once()` fails because the mock is bypassed. Per-test monkeypatch (proposed in original plan) is fragile — a forgotten monkeypatch in a new test silently passes against stale cache. | Test Impact; Step 5 isolated_cache fixture | Use `@pytest.fixture(autouse=True)` `isolated_cache(monkeypatch, tmp_path)` that auto-replaces the module's `_cache` for every test. Includes a `hasattr` guard so it is a no-op until wire-up lands (avoids tree-red window during build sequencing). |
| BLOCKER | self (Skeptic) | Build sequence (Steps 3-4 land before Step 5) leaves `pytest tests/unit/test_intent_classifier.py` red between commits. Even within a single PR, `git bisect` fails. | Build Sequencing section | Reorder: helper → cache tests → existing-tests fixture (with hasattr guard) → wire-ups → docs → validate. Document the rationale; `update-existing-tests` now depends on `build-cache-helper`, and wire-ups depend on `update-existing-tests`. |
| CONCERN | self (Archaeologist) | Original key input string `f"{message}\|{recent_window}"` was ambiguous: the pipe character can appear in either component, two different values could collide. `recent_window` was also vaguely defined ("joined string") — small variations in formatting would generate different keys for identical semantic input. | Step 3 + Step 4 wire-ups | Use `\n---\n` as the separator (cannot appear by accident). Define `recent_window` precisely as the formatted multi-line block that is sent to the API itself, so prompt-builder changes auto-invalidate keys. |
| CONCERN | self (Adversary) | If Haiku transiently returns an empty string, the original plan would cache the empty result permanently. Subsequent calls for that input would return empty content forever. | Step 1 (helper); Step 4 (indexer wire-up) | Add a guard inside `get_or_compute`: `if not result: return result` BEFORE `cache.set(...)`. Falsy results bypass storage. Documented in module docstring. |
| CONCERN | self (Simplifier) | Verification section did not test the actual round-trip (write IntentResult → JSON → read → reconstruct). A unit test of the helper alone would not catch the dataclass-serialization bug. | Verification table | Added "Intent cache round-trip" smoke check: call classify_intent twice, assert second-call elapsed_ms < 50ms (proves cache hit AND deserialization succeeded). |

---

## Open Questions

None at plan time. The issue body is unusually complete — it includes pseudocode, exact wire-up locations, file:line references, acceptance criteria, and a documented future-state scaling path. The recon found two corrections (one classifier reference, one analytics-module path) that the plan resolves directly without scope ambiguity.

If the critique surfaces concerns, they will be tracked in the Critique Results table above.
