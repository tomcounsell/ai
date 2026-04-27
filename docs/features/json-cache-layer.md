# JSON Cache Layer

A small persistent JSON-on-disk cache for deterministic LLM call sites that
replay identical inputs. First request pays the live API cost; subsequent
identical requests return the cached value (~10ms read, no network).

Status: **Shipped**. Tracking: [#1182](https://github.com/tomcounsell/ai/issues/1182).

## What it caches today

Two call sites currently route through the helper:

| Call site | File | Cache file | TTL | Max entries |
|-----------|------|------------|-----|-------------|
| Intent classifier | `agent/intent_classifier.py::classify_intent` | `data/cache/intent_classifier.json` | 7200s (2h) | 2000 |
| Knowledge summarizer | `tools/knowledge/indexer.py::_summarize_content` | `data/cache/knowledge_summaries.json` | None | 5000 |

Both files live under `data/cache/`, which is gitignored.

## Contract

The helper is intentionally tiny. Two pieces:

```python
from utils.json_cache import JsonCache, get_or_compute

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

`get_or_compute` hashes `f"{version}:{key_input}"` with sha256, looks up
the cache, calls `compute_fn` on miss, stores the result, and emits
`cache.hit` / `cache.miss` analytics keyed by the cache file's stem.

### JSON-only values

Cache values must be JSON-serializable: `dict`, `list`, `str`, `int`,
`float`, `bool`, `None`, or compositions thereof. The helper does NOT
pickle dataclasses or custom objects.

For dataclasses, store `dataclasses.asdict(...)` and rehydrate at the call
site. The intent classifier wire-up is the canonical example:

```python
def _call_and_serialize() -> dict:
    response = client.messages.create(...)
    parsed = _parse_classifier_response(response.content[0].text.strip())
    return dataclasses.asdict(parsed)  # <-- dict, not dataclass

cached_dict = get_or_compute(_cache, key, _call_and_serialize, ttl=7200)
result = IntentResult(**cached_dict)  # rehydrate at the call site
```

### Falsy results bypass storage

If `compute_fn()` returns `""`, `None`, `[]`, `{}`, `False`, or `0`,
`get_or_compute` returns the falsy value but does NOT call `cache.set()`.
A transient API flake that returned empty content is not cached
permanently — the next call retries.

### Single-writer-per-file invariant

**Each `JsonCache` instance must be written to by exactly one process.**

- `data/cache/intent_classifier.json` is written only by the bridge process.
- `data/cache/knowledge_summaries.json` is written only by the indexer process.

`os.replace` is atomic on POSIX, so two writers cannot corrupt each other —
but the loser's writes are silently lost (last-write-wins). If we ever
need a second writer for the same file, the upgrade path is `fcntl.flock`
or a real database (see "Scaling path" below).

A code reviewer touching `utils/json_cache.py` or its consumers should
verify the invariant holds.

## Adding a new call site

1. **Confirm the inputs are deterministic.** If two calls with identical
   inputs can legitimately produce different outputs (RAG retrieval,
   timestamp-dependent prompts, sampling-temp > 0), the cache is wrong.
2. **Confirm there's an existing fallback** at the call site. Both current
   call sites have `except Exception:` blocks that fall back to safe
   default behavior. The cache should never be the only thing standing
   between the user and a working system.
3. **Pick a stable namespace.** The cache file's stem (e.g.,
   `intent_classifier`) becomes the analytics dimension. Keep it short
   and grep-friendly.
4. **Add a module-level singleton:**
   ```python
   _cache = JsonCache(Path("data/cache/<namespace>.json"), max_entries=N)
   _CACHE_VERSION = "v1"
   ```
5. **Wire `get_or_compute` inside the existing `try` block** so the
   existing `except` still catches any helper raise (defense-in-depth —
   the helper itself never raises, but the boundary is preserved).
6. **Update tests** with an `autouse=True` `isolated_cache` fixture that
   monkeypatches the `_cache` singleton to `tmp_path` and includes a
   `hasattr` guard so it's a no-op until the wire-up lands.

## Version-bumping for prompt changes

Keys are `sha256(f"{version}:{key_input}").hexdigest()`. Bumping `version`
from `"v1"` to `"v2"` makes every old key unreachable; they LRU-evict
naturally as new entries land. **No manual flush, no script, no migration.**

When to bump:

- Prompt template changes (system message, user content format, examples).
- Model change (e.g., Haiku 3.5 → Haiku 4) if it materially changes outputs.
- `IntentResult` shape change (new field, removed field).

When NOT to bump:

- Refactor that doesn't change outputs.
- Logging change.
- Comment edits.

## What NOT to cache

- **Popoto-managed data.** Reads and writes go through the ORM. Use the
  ORM's own caching primitives if you need them.
- **Non-deterministic inputs.** RAG context, conversation memory,
  timestamps in the prompt — every cache lookup misses, so caching has
  negative value.
- **Side-effecting calls.** The cache short-circuits the call entirely. If
  the side effect (write to DB, send Slack message, append to file) is
  the point, do not cache it.
- **Calls that return large blobs.** The cache file is loaded into memory
  on every process start. Anything over ~10MB or with ~100k entries
  belongs in SQLite or `shelve`.

## Hit-rate observability

Hit/miss is emitted via `analytics.collector.record_metric` with the
namespace as a dimension:

```bash
python -m tools.analytics summary
# look for `cache.hit` and `cache.miss` rows with `namespace` = intent_classifier
# or knowledge_summaries
```

If `analytics.collector` is unavailable or raises, caching continues
unchanged — the analytics emission is wrapped in try/except.

## Race-condition notes

- **Concurrent reads of the in-memory cache during async classify_intent.**
  Two concurrent `classify_intent` calls can both miss, both call Haiku,
  both `set()`. This is benign: the second `set()` overwrites the first
  with an identical value, and `move_to_end` is idempotent. We do NOT add
  an `asyncio.Lock` because lock contention would be a worse failure
  than a duplicate Haiku call.
- **Reader visible to a write-in-progress on disk.** `os.replace` is
  atomic on POSIX; readers see either the old `cache.json` or the new
  one, never a partial. The single-writer invariant means there's no
  cross-process reader/writer race today.

## Scaling path

The current design is deliberately simple. Trigger an upgrade when one
of the following becomes true:

| Trigger | Upgrade path |
|---------|--------------|
| Cache file ≥ 10MB or load time ≥ 100ms | Switch to `shelve` or partition by key prefix. |
| A second writer needs to write the same file | Add `fcntl.flock` around `_save()` (or split into per-writer namespaces). |
| Need cross-machine cache sharing | Move to a `Popoto CacheEntry` model so all bridge machines see the same hits. |
| Need atomic per-key writes | Move to SQLite. (Note: SQLite was deliberately removed from the agent-session path because writer locks froze sessions; only adopt for caches that don't sit on the critical path.) |

These are documented future-state designs, **not built work**. Add them
only when the trigger fires, not preemptively.

## Test strategy

The helper has its own test suite at `tests/unit/test_json_cache.py`
covering hit, miss, corrupt-file fallback, TTL expiry, LRU eviction,
recency-bump preservation, atomic write semantics, version-key
invalidation, falsy-result-not-cached, and analytics-unavailable
graceful degradation.

Existing call-site tests use an `autouse=True` `isolated_cache` fixture
that monkeypatches the module's `_cache` singleton to a `tmp_path`-rooted
instance for every test. Without this fixture, the second test in a
class would silently bypass the mocked Haiku client and assert
incorrectly. The fixture's `hasattr` guard makes it a safe no-op when
the wire-up has not landed yet.

## Files

- `utils/json_cache.py` — the helper.
- `agent/intent_classifier.py` — call site 1 (TTL 7200s).
- `tools/knowledge/indexer.py` — call site 2 (no TTL).
- `tests/unit/test_json_cache.py` — helper tests.
- `tests/unit/test_intent_classifier.py` — call-site tests with `isolated_cache` fixture.
- `tests/unit/test_knowledge_indexer.py` — call-site tests with `isolated_cache` fixture.

## Post-deploy follow-up

After 7 days in production, run `python -m tools.analytics summary` and
verify the hit rate. If the intent-classifier hit rate is below ~30%,
the TTL or key derivation may need tuning. If the knowledge-summarizer
hit rate is below ~80%, content-changed detection may have a bug.

This is a tracking task, not build work — capture the result in the
issue, do not gate the PR on it.
