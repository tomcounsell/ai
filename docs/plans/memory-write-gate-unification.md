---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-22
tracking: https://github.com/tomcounsell/ai/issues/2201
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-22T15:03:05Z
---

# Unify Memory Write-Path Quality Gates + Remove Line-Splitting Fallback

## Problem

Valor's subconscious memory system stores memories automatically during regular
use — there is no human curator, so write-time quality gating is the only
legitimate defense against noise. Today that defense guards exactly one of the
writer paths.

Production memory (2026-07-22 baseline: 1977 durable records) shows a
`junk_rate = 2.98%` (59 junk records). The junk takes several shapes: true
dangling syntax the classifier flags (`includes:`, bare markers like `1.`),
plus durable-but-worthless content the frozen `classify_content` does NOT flag —
below-floor shrapnel (`1. Concurrency`, 14 chars, classifies `durable`) and
multi-word line-split shrapnel (`runs on a schedule`, 18 chars, `durable`). These
last two are gated by the length floor and prevented by fallback-removal
respectively, not by the fragment classifier. Two distinct causes:

1. **Content gates guard only the hook-ingest path.** `hook_utils/memory_bridge.py::ingest()`
   (`:759`) enforces `MIN_PROMPT_LENGTH=50`, a `TRIVIAL_PATTERNS` ack frozenset,
   and bloom dedup — but the other four writer paths (post-session extraction,
   post-merge learning, Telegram bridge, intentional CLI save) call
   `Memory.safe_save()` / `Memory(...).save()` without any content inspection.
   `Memory.compute_filter_score()` (`models/memory.py:174-180`) — the model's
   `WriteFilterMixin` choke point that *every* path passes through — returns raw
   `importance` (a source-derived constant), so it filters on importance only and
   never looks at content.
2. **A line-splitting fallback parser is still live.** `agent/memory_extraction.py::_parse_categorized_observations()`
   splits unparseable LLM output on newlines and emits **one Memory per line**
   (`:945-978`). Issue #1212 added a JSON short-circuit, but the fallback still
   fires whenever `extract_json_payload()` returns `None`.

**Current behavior:** ack-only and fragment records are persisted at importance
6.0, compete equally in retrieval, get injected, get dismissed, and only then
begin to decay.

**Desired outcome:** noise never enters the store, regardless of writer path;
multi-line content is never shrapnel'd into per-line records; every gated/dropped
write is counted so Phase 1 telemetry can report gate effectiveness.

## Freshness Check

**Baseline commit:** `49e408f5b657cba699e49c1895ced119b51d8648`
**Issue filed at:** 2026-07-22T04:30:47Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `.claude/hooks/hook_utils/memory_bridge.py` — hook gates (`MIN_PROMPT_LENGTH=50` at `:123`, `TRIVIAL_PATTERNS` at `:96`, `ingest()` at `:759`, `safe_save` at `:813`) — still holds.
- `agent/memory_extraction.py:945-978` — line-splitting fallback (`_parse_categorized_observations` at `:848`; save sites at `:784`, `:1081`) — still holds.
- `models/memory.py:174-180` — `compute_filter_score` returns raw importance, content-blind; `safe_save` at `:182` — still holds.
- `agent/memory_quality.py` — Phase 1 shared heuristics module (`classify_content` → `durable`/`ack_only`/`fragment`) EXISTS and is imported by `tools/memory_eval/ingest_quality.py:34`. Confirmed the acceptance-criterion-3 reuse target is real.
- **DRIFT:** the issue's Definitions table names `mcp_servers/memory_server.py` as the fifth "MCP writer path." That file is **read-only** — it exposes only `memory_get` and `memory_search`. The actual fifth write path is the intentional CLI save at `tools/memory_search/__init__.py:249` (`Memory.safe_save`, the `python -m tools.memory_search save` command). The plan targets the correct five paths below.

**Cited sibling issues/PRs re-checked:**
- #2200 (Phase 1 prerequisite) — **CLOSED/merged** 2026-07-22T14:12:09Z. Its shared heuristics module (`agent/memory_quality.py`), corpus aggregation (`tools/memory_eval/ingest_quality.py`), metrics endpoint (`/memories/metrics.json`, `ui/app.py:299`), and committed baseline (`docs/baselines/memory-telemetry-baseline.json`) all landed. Prerequisite satisfied.
- #1212 — CLOSED. JSON short-circuit added; the non-JSON fallback this issue removes is the remaining half.
- #1217, #2016, #1822 (merged) — hardened the JSON *branch* against shrapnel/refusal/boilerplate. None touched the newline fallback.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since=2026-07-22T04:30:47Z` over the four referenced files returned empty).

**Active plans in `docs/plans/` overlapping this area:** none (no other `*mem*` plan present; the Phase 1 plan already migrated out).

**Notes:** All five real writer paths funnel through `Memory.save()` (via `safe_save` or a direct `.save()`), which confirms the single-choke-point thesis: gating inside the model catches every path with no per-path edits.

## Prior Art

- **#2200 (Phase 1, merged):** Built `agent/memory_quality.py` (shared junk heuristics), `tools/memory_eval/ingest_quality.py` (corpus metrics), the `/memories/metrics.json` endpoint, and froze `docs/baselines/memory-telemetry-baseline.json`. This is the measurement substrate Phase 2 enforces against and reports through.
- **#1212 / PR #1217 (merged):** Added the JSON short-circuit so successful JSON parses never reach the line fallback. Partial fix — the fallback still fires on non-JSON output. This issue removes it.
- **#2016 / PR #2023 (merged):** Type-guarded and refusal-filtered the JSON *branch* so shrapnel-shaped observation values don't get saved. Applied at the record level inside the JSON path; does not touch the newline fallback.
- **#1822 (merged):** `_is_scoping_boilerplate` filter for session-scoping echoes. Applied inside both parser branches.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR #1217 (#1212) | JSON short-circuit: successful JSON parse returns before the fallback | Only covers the case where `extract_json_payload` succeeds. When it returns `None` (no JSON-shaped substring), the newline fallback still explodes text into per-line records. |
| PR #2023 (#2016) | Per-record refusal/type guards in the JSON branch | Hardened the *sanctioned* path; the fallback is the *unsanctioned* path and was left live. |
| Hook `MIN_PROMPT_LENGTH`/`TRIVIAL_PATTERNS` | Content gate on user-prompt ingest | Guards one writer path. The junk in production is human-sourced via the four ungated paths. |

**Root cause pattern:** fixes were applied per-branch and per-path instead of at
the model choke point every writer shares. The line fallback and the four
ungated writer paths are the two remaining holes; both are closed by
consolidating at `Memory.save()` and deleting the fallback outright.

## Data Flow

**Writer paths (all five funnel through `Memory.save()`):**

1. **Entry point** — one of: hook ingest (`memory_bridge.py:813`), post-session extraction (`memory_extraction.py:784`), post-merge learning (`memory_extraction.py:1081`), Telegram bridge (`telegram_bridge.py:1335`), intentional CLI save (`tools/memory_search/__init__.py:249`).
2. **`Memory.safe_save()` / `Memory(...).save()`** — constructs the record and calls `save()`.
3. **`Memory.save()` → `WriteFilterMixin` → `compute_filter_score()`** — the universal choke point. **This is where the content gate lands.** Records failing the gate are dropped (`save()` returns `False`); `safe_save` already maps `False` to `None`.
4. **Persisted** — bloom fingerprint, BM25 index, embedding, decay-sorted relevance.

**Extraction fallback path (the shrapnel source):**

1. **Entry point** — `extract_observations_async` receives raw Haiku text (`memory_extraction.py:720`).
2. **`_parse_categorized_observations(raw_text)`** — tries tolerant JSON (`:876`). On success (`≥1` valid observation) short-circuits (#1212).
3. **Fallback (`:945-978`)** — fires in THREE cases, not one: (a) `extract_json_payload` returns `None` (no JSON-shaped substring); (b) `json.loads` raises and the `except (json.JSONDecodeError, TypeError): pass` falls through; (c) the payload parses but yields zero valid observations (`if results:` is false). In any of the three it splits `raw_text` on `\n` and emits one `(content, importance, {})` tuple per line → **one Memory per line**. This is removed: all three converge to a single unconditional `return []` (see Technical Approach), so nothing is saved.
4. **Output** — parsed tuples are saved via `Memory.safe_save` in the caller loop (`:784`).

## Architectural Impact

- **New dependencies:** `models/memory.py` gains an import of `agent.memory_quality`. That module is deliberately dependency-light (no popoto/redis/models imports) precisely so a hot write path can import it without circular imports — verified in its docstring. No new third-party deps.
- **Interface changes:** `Memory` gains an overridden `save()` (content gate + counter). `compute_filter_score()` stays as-is (importance filtering unchanged). `agent/memory_quality.py` gains a write-gate predicate (`gate_reason` / length-floor helper). `get_corpus_metrics()` gains gate-counter fields. No writer-path signatures change.
- **Coupling:** *decreases* net complexity — four ungated paths stop needing their own content logic; one choke point owns it. Measurement and enforcement share `agent/memory_quality`, so they cannot drift (acceptance criterion 3).
- **Data ownership:** unchanged. New Redis counter keys shaped `{project_key}:memory-gate:{reason}` (not Popoto-managed).
- **Reversibility:** high — revert is the `save()` override + fallback deletion + counter reads. No schema migration, no data rewrite.

## Appetite

**Size:** Medium

**Team:** Solo dev, plan critique, code reviewer

**Interactions:**
- PM check-ins: 1-2 (the length-floor value is provisional — measure `gate_rejected_short` before tightening, per Decision 1)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Phase 1 shared module present | `python -c "from agent.memory_quality import classify_content; assert classify_content('includes:')=='fragment'"` | Reuse target for the content gate (criterion 3) |
| Phase 1 corpus metrics present | `python -c "from tools.memory_eval.ingest_quality import compute_corpus_metrics; compute_corpus_metrics([])"` | Endpoint the counters attach to (criterion 4) |
| Committed baseline present | `test -f docs/baselines/memory-telemetry-baseline.json` | Reference for the before/after junk-rate comparison (criterion 5) |
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB as _R; _R.ping()"` | Gate counters use `INCR`/`GET` on this handle (there is no `tools.redis_client`) |

Run via `python scripts/check_prerequisites.py docs/plans/memory-write-gate-unification.md`.

## Solution

### Key Elements

- **Content gate at the model choke point** — a single predicate applied inside `Memory.save()` that rejects ack-only, fragment/dangling-syntax, and below-floor content before persistence. Because every writer path calls `save()`, all five inherit the gate with zero per-path edits.
- **Shared heuristics, one source of truth** — the gate imports its classification from `agent/memory_quality.py`, the same module Phase 1's junk-rate metric uses. A future refinement to "what is junk" lands once and both consumers move together.
- **Fallback deletion** — the newline-splitting branch in `_parse_categorized_observations` is removed. An unparseable payload is dropped and counted, never exploded into per-line records.
- **Readable dropped-write counters** — a `memory-gate:*` Redis counter namespace (per project_key), incremented at each rejection site and surfaced in `/memories/metrics.json`, so gate effectiveness is visible in the Phase 1 telemetry endpoint.

### Flow

Any writer constructs a `Memory` → `save()` → content gate reads `content` → if ack / fragment / short: increment `{project_key}:memory-gate:{reason}` counter, return `False` (dropped, silent) → else `WriteFilterMixin` importance gate → persisted.

Extraction produces unparseable Haiku output (no JSON substring, a `json.loads` error, or zero valid observations) → parser returns `[]` → caller (after resolving `project_key`) increments `{project_key}:memory-gate:fallback_dropped` → nothing saved.

Operator/dashboard reads `/memories/metrics.json` → sees `gate_rejected_ack`, `gate_rejected_fragment`, `gate_rejected_short`, `gate_fallback_dropped` alongside `junk_rate`.

### Technical Approach

- **Override `Memory.save()`, leave `compute_filter_score()` unchanged.** Keeping `compute_filter_score` returning `importance` preserves the existing importance-threshold filtering. The new content gate is a distinct concern, so it lives in an overridden `save()`. **The content gate runs on INSERT only — never on updates to an existing record.** The outcome/metadata update at `memory_extraction.py:1343` calls a bare `m.save()` on an already-persisted record; if that record's content is below-floor or `is_fragment` (legacy junk already in the store), gating the re-save would return `False` and silently lose the outcome/`dismissal_count`/`last_outcome` write. Guard with an existence check on the record's Redis key:
  ```python
  def save(self, *args, **kwargs):
      # Content gate applies to NEW records only. An existing key means this is
      # an update (e.g. the outcome re-save at memory_extraction.py:1343) — never
      # drop those, or we lose outcome/metadata on legacy-junk records.
      is_update = _key_exists(self.db_key)   # POPOTO_REDIS_DB.exists(str(self.db_key)); try/except → False
      if not is_update:
          reason = gate_reason(self.content)   # None | "ack" | "fragment" | "short"
          if reason:
              _increment_gate_counter(self.project_key, reason)   # try/except, never raises
              return False   # matches WriteFilterMixin's drop contract; safe_save maps False→None
      return super().save(*args, **kwargs)
  ```
  `self.db_key` computes the record's Redis key (`ClassName:keyfield…`); an existence check on it via `POPOTO_REDIS_DB.exists` is the insert-vs-update signal. Wrap the check in try/except: on error default `is_update=False` (run the gate) — an `exists()` failure while the write itself succeeds is nearly impossible since both use the same Redis handle, and preserving the gate's junk-blocking guarantee on the common insert path is the safer default; the only exposure is a rare junk-content update whose existence check errored, which Phase 4 removes anyway. The builder verifies the exact key stringification. This counts exactly once per rejected write (avoids the double-count hazard of counting inside `compute_filter_score`, which `WriteFilterMixin` may call more than once — the builder must verify call cardinality either way).
- **`content_gate_reason` lives in `agent/memory_quality.py`.** It composes the existing `classify_content` (returns `ack_only`/`fragment`/`durable`) with a new length-floor check. This keeps the single-source-of-truth invariant (criterion 3). Proposed helper:
  ```python
  MIN_CONTENT_LENGTH = 15   # provisional floor: admits 15+ char facts while the length gate catches
                            # durable-but-too-short junk like "1. Concurrency" (14 chars, classifies
                            # `durable` under the frozen classifier) — measure gate_rejected_short
                            # before tightening (Decision 1)
  def gate_reason(content: str | None) -> str | None:
      c = classify_content(content)
      if c == "ack_only": return "ack"
      if c == "fragment": return "fragment"   # None/empty/dangling-syntax → its own counter
      if len((content or "").strip()) < MIN_CONTENT_LENGTH: return "short"
      return None
  ```
  **Reason taxonomy = `{ack, fragment, short}` (exactly the three non-None returns).** `gate_reason` must NOT fold `fragment` into `short`: `classify_content`-detected fragments (dangling colon `includes:`, bare list marker `1.`, unbalanced brackets) must land in their own `gate_rejected_fragment` counter, separate from below-floor durable content (`gate_rejected_short`). Folding them would leave `gate_rejected_fragment` a permanent-zero dead counter and hide a real junk shape inside `gate_rejected_short`.
  **What the content gate does and does NOT catch (verified against the frozen `classify_content`):** `1. Concurrency` (14 chars) classifies as `durable` — the bare-marker regex `^([-*•]|\d+[.)])\s*$` requires NO body text, so `1. Concurrency` is NOT a fragment; it is gated only by the length floor → `short`. Only a bare `1.` (no body) is a `fragment`. Multi-word shrapnel like `runs on a schedule` (18 chars → `durable`, ≥ floor) is NOT caught by the content gate at all — the length floor admits it. That shrapnel is prevented by **fallback-removal (Task 2)**, not by the content gate: it only ever entered the store because the line-splitting fallback exploded a multi-line payload into per-line records. The two mechanisms are complementary — the content gate stops ack/fragment/below-floor writes; fallback-removal stops per-line shrapnel. Do NOT mutate `classify_content` to make `1. Concurrency` a fragment — that would break baseline integrity (Decision 1).
- **Measurement integrity — keep `classify_content`'s three buckets frozen.** The length floor is a *write-gate-only* dimension; do NOT fold it into `classify_content` (which drives the frozen baseline's `junk_rate`). Reclassifying below-floor durable records as junk would change the junk-rate *definition* and break the apples-to-apples baseline comparison (criterion 5). Length-floor rejections are visible via their own `gate_rejected_short` counter instead. (Decision 1: write-gate-only floor chosen; `classify_content` stays frozen.)
- **Remove the fallback (`memory_extraction.py:945-978`).** Delete the entire block — both the `categorized` line loop and the `uncategorized` per-line return — and end `_parse_categorized_observations` with a single **unconditional** `return []`. The fallback actually fires in THREE cases, not one: (1) `extract_json_payload` returns `None` (no JSON-shaped substring); (2) `json.loads` raises and the `except (json.JSONDecodeError, TypeError): pass` falls through; (3) the payload parses but yields zero valid observations (`if results:` is false). A single trailing `return []` converges all three to "nothing saved." Do NOT guard the drop with `if payload is None:` — that leaves cases (2)/(3) hitting an implicit `return None`, and the caller's `for` loop over `parsed` then raises `TypeError: 'NoneType' is not iterable`.
- **The parser stays `project_key`-free; increment `fallback_dropped` in the caller.** `_parse_categorized_observations(raw_text)` has NO `project_key` parameter, so its body must never reference `project_key` — doing so raises `NameError` on every unparseable path (an error raised while *evaluating the counter's argument*, which the counter's own try/except cannot catch). Instead, count in `extract_observations_async` after `project_key` is resolved. The current caller short-circuits with `if not parsed: return []` (line ~722) BEFORE the `resolve_project_key()` block (lines ~728-731), so **move project-key resolution above the not-parsed check**:
  ```python
  parsed = _parse_categorized_observations(raw_text)
  # resolve project_key first (keeps its own None early-return) so the counter has a key
  if not project_key:
      from config.project_key_resolver import resolve_project_key
      project_key = resolve_project_key()
      if project_key is None:
          return []
  if not parsed:
      _increment_gate_counter(project_key, "fallback_dropped")   # try/except, never raises
      return []
  ```
  Default: **no retry** before dropping — JSON is the sanctioned contract since #1212/#2016, and a retry adds an LLM call plus latency (Decision 2).
- **Counters use `INCR`/`GET` via `POPOTO_REDIS_DB`** on keys shaped `{project_key}:memory-gate:{reason}` — **project_key first**, matching `_sum_project_counter`'s `{project_key}:{suffix}` layout (`ui/app.py:434`) so `_sum_gate_counter` can reuse it by passing `suffix=f"memory-gate:{reason}"`. `_increment_gate_counter` imports the handle the rest of the repo uses — `from popoto.redis_db import POPOTO_REDIS_DB as _R` (the exact handle at `monitoring/worker_watchdog.py:409`); **there is no `tools.redis_client` module**. These are NOT Popoto-managed keys, so `INCR`/`GET` are allowed — the raw-Redis ban (`validate_no_raw_redis_delete.py`) targets `delete`/`srem`/`sadd`/`zrem` on model keys only. All increments wrapped in try/except so a Redis hiccup never crashes a write.
- **Surface counters in `get_corpus_metrics` (`ui/data/memories.py`).** Phase 1 explicitly skipped counter attachment because `analytics.record_metric` is write-only (`:297-303`). Phase 2 supplies a readable path: a `_sum_gate_counter(reason)` helper reads `{project_key}:memory-gate:{reason}` (best-effort `GET` via `POPOTO_REDIS_DB`) and adds `gate_rejected_ack`/`gate_rejected_fragment`/`gate_rejected_short`/`gate_fallback_dropped` to the metrics dict, so `/memories/metrics.json` reports them (criterion 4). **`_sum_gate_counter` must iterate the `pks` already resolved at the top of `get_corpus_metrics` (`pks = _resolve_project_keys(project_key)`, `ui/data/memories.py:276`) — NOT re-call `get_machine_project_keys()`.** The corpus metrics are scoped to `pks`; if the helper summed a different key set, an explicit-`project_key` call would report gate counters for the wrong scope. This diverges deliberately from `_sum_project_counter` at `ui/app.py:434` (which iterates `get_machine_project_keys()` because that endpoint has no per-call project scope): reuse its `{project_key}:{suffix}` key layout, but drive it with the local `pks`.
- **Keep path-specific gates in place.** The hook's `MIN_PROMPT_LENGTH=50`/`TRIVIAL_PATTERNS` pre-filter and bloom dedup stay — they short-circuit obvious junk before doing bloom/embedding work. The model gate is the backstop that catches the other four paths. Redundant on the hook path, authoritative everywhere else.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_increment_gate_counter` wraps its `INCR` in `try/except` — add a test that a raising Redis client does NOT propagate (the write still returns its normal result; the counter is best-effort). Assert observable behavior (no exception escapes `save()`).
- [ ] `_sum_gate_counter` in `get_corpus_metrics` — on Redis failure it returns 0 and the metrics dict is still well-formed (matches the loader's never-crash contract). Test with a failing client.
- [ ] `safe_save` already logs on `result is False` (`models/memory.py:196`) — confirm a gated write logs at debug and returns `None`, no raise.

### Empty/Invalid Input Handling
- [ ] `gate_reason(None)`, `gate_reason("")`, `gate_reason("   ")` — all return a rejection reason (`classify_content` maps these to `fragment` → gated). Add explicit tests.
- [ ] Below-floor durable content (`"deploy fri"`, 10 chars) → `"short"`; `"1. Concurrency"` (14 chars, `durable`) → `"short"`; at-floor durable content (`"Deploy on Fridays"`, 17 chars) → `None` (persists). Boundary tests around `MIN_CONTENT_LENGTH`.
- [ ] Update re-save is never gated: a record whose content is below-floor / `is_fragment` already persisted (or forced to exist), re-saved as an update, still calls `super().save()` (gate skipped because the key exists). Guards the outcome-loop re-save at `memory_extraction.py:1343`.
- [ ] Extraction fallback: non-JSON, multi-line `raw_text` → `_parse_categorized_observations` returns `[]` (no per-line records) and increments `fallback_dropped`.

### Error State Rendering
- [ ] `/memories/metrics.json` renders the new counter fields even when the corpus is empty and when Redis is down (zero-filled, HTTP 200). Extend `tests/integration/test_dashboard_memories.py`.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py::TestParseCategorizedObservations` (`:819+`) — UPDATE: cases asserting the newline/`CATEGORY:`-line fallback produced records now assert `[]`. The JSON-path cases stay unchanged. Any case that fed non-JSON text expecting per-line output is REPLACED with a drop+counter assertion.
- [ ] `tests/unit/test_memory_quality.py` — UPDATE: add `gate_reason` / `MIN_CONTENT_LENGTH` boundary cases. Existing `classify_content`/`is_ack_only`/`is_fragment` assertions stay unchanged (classification is frozen).
- [ ] `tests/unit/test_memory_eval.py` — no change expected (junk-rate definition unchanged by design, per Decision 1); confirm and state so.
- [ ] `tests/integration/test_dashboard_memories.py` — UPDATE: assert the new `gate_*` counter fields appear in the metrics payload.
- [ ] New: `tests/integration/test_memory_write_gate.py` — REPLACE/CREATE: one test per writer path proving junk is gated at the model layer (criterion 1: "test each of the five paths").

## Rabbit Holes

- **Retro-cleaning the existing 59 fragments.** Out of scope — that is Phase 4 (prune, #2203). Phase 2 *prevents* new junk; it does not delete existing records.
- **LLM-based content scoring at write time.** Explicitly dropped in recon; that is Phase 3 (distilled ingest, #2202). This phase is cheap string heuristics only.
- **Reworking `WriteFilterMixin` in popoto.** Do not modify the upstream mixin. The gate lives in the `Memory` subclass via `save()` override.
- **A general "readable analytics counter" abstraction.** Do not generalize `analytics.record_metric` into a read/write store. Use the existing `INCR`/`GET` + `_sum_project_counter` pattern for these four counters and stop.
- **Tuning the hook's `MIN_PROMPT_LENGTH=50` to match the model floor.** Different concerns (prompt-length heuristic vs. content-floor). Leave the hook value alone.

## Risks

### Risk 1: Length floor rejects legitimately short durable memories
**Impact:** A too-aggressive `MIN_CONTENT_LENGTH` silently drops valid short facts (e.g. an intentional CLI save of "Deploy on Fridays").
**Mitigation:** Ship `MIN_CONTENT_LENGTH=15` as a **conservative provisional floor** (below the hook's 50, aligned with extraction's existing `len < 10` observation drop) and wire `gate_rejected_short` first, so telemetry reveals how many real records the floor would reject before the value is finalized — measure, then tighten (Decision 1). If a sharper anchor is wanted before ship, derive the floor from the shortest-durable-record length distribution in the committed baseline. Boundary tests around the floor. The `gate_rejected_short` counter makes over-rejection observable in telemetry rather than invisible.

### Risk 2: The gate fires on update re-saves of existing records
**Impact:** `save()` is also called by metadata-update paths (`memory_extraction.py:1343` outcome update, title write-back). If an already-persisted record's content is junk (legacy fragments already in the store), gating the re-save would return `False` and lose the outcome/`dismissal_count`/`last_outcome` write.
**Mitigation:** The content gate runs on INSERT only — the `save()` override skips the gate when the record's Redis key already exists (`POPOTO_REDIS_DB.exists(str(self.db_key))`), so every update path (including the outcome re-save at `:1343`) bypasses content gating entirely and reaches `super().save()`. This is a structural guarantee, not a "durable stays durable" assumption — it holds even for legacy-junk records that predate the gate. Add a test that an outcome-update re-save of a below-floor / `is_fragment` record still persists the metadata change. The existence check is best-effort (try/except → treat as insert on error); an `exists()` failure while the write itself succeeds is nearly impossible since both use the same Redis handle.

### Risk 3: Post-deploy junk_rate does not immediately drop (criterion 5 timing)
**Impact:** Write gates prevent *new* junk but cannot remove the 59 existing fragments. `junk_rate = 59 / (1977 + new_durable)` only declines as new durable records accumulate — it will not visibly move the day of deploy.
**Mitigation:** Frame criterion 5 as a trend measured over a window, not an instant drop. The `gate_rejected_*` counters give immediate, direct evidence of gate effectiveness (junk *prevented*), which is the honest Phase-2 signal (Decision 3: counters + trend accepted; existing-fragment cleanup stays Phase 4).

### Risk 4: Counting inside a filter method double-counts
**Impact:** If the counter increment were placed in `compute_filter_score`, and `WriteFilterMixin` calls it more than once per `save()`, rejections over-count.
**Mitigation:** Count in the overridden `save()` (called once per persist attempt), not in `compute_filter_score`. Builder verifies call cardinality of both.

## Race Conditions

### Race 1: Concurrent gate-counter increments across worker/bridge/hook processes
**Location:** `_increment_gate_counter` (new, `models/memory.py` or a small `models/memory_gate.py`).
**Trigger:** The bridge, worker, and Claude Code hooks can all reject a write concurrently for the same `project_key`.
**Data prerequisite:** The `{project_key}:memory-gate:{reason}` key exists or is created by `INCR` (atomic, creates-on-missing).
**State prerequisite:** none — counters are monotonic, order-independent.
**Mitigation:** Redis `INCR` is atomic; concurrent increments are safe by construction. Reads (`GET`) are best-effort snapshots — no correctness dependency on read-time consistency.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2202] LLM-based / distilled content scoring at write time — Phase 3.
- [SEPARATE-SLUG #2203] Pruning the 59 existing fragment records and the outcome-loop — Phase 4.
- [SEPARATE-SLUG #2200] Changing the `junk_rate` metric definition or the committed baseline — Phase 1 owns the measurement contract; this plan reads it, it does not redefine it.

## Update System

No update-system changes required — this feature is purely internal (a model-layer
gate, an extraction change, and Redis counters). No new dependencies, config
files, secrets, or launchd services to propagate. The `agent/memory_quality.py`
and `tools/memory_eval/` modules already ship with the repo. No `/update` skill
or `scripts/remote-update.sh` change.

## Agent Integration

No new agent-facing surface is required.

- **MCP:** `mcp_servers/memory_server.py` is read-only (`memory_get`, `memory_search`); it is NOT a writer path (correcting the issue's Definitions table). No new MCP tool. Memory writes reach the model through the five internal paths, all of which already call `save()`, so the gate applies transparently.
- **Bridge:** `bridge/telegram_bridge.py` already calls `Memory.safe_save()` (`:1335`) — it inherits the gate with no code change. No new import.
- **Metrics surface:** the existing `/memories/metrics.json` endpoint (`ui/app.py:299`) gains the `gate_*` counter fields — no new route.
- **Integration tests:** `tests/integration/test_memory_write_gate.py` verifies each writer path (hook ingest, extraction, post-merge, bridge, CLI save) actually drops junk at the model layer, and `tests/integration/test_dashboard_memories.py` verifies the counters are visible via the endpoint the agent/dashboard reads.

**Popoto schema migration:** none required. The change overrides `Memory.save()`
behavior and adds no fields (no new `StringField`/`FloatField`/etc.). Per the
repo's Popoto migration rule, migrations are needed only for model *field*
changes; a method override is not one. State this explicitly in the PR.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — add a "Write-path quality gates" subsection: the model choke point, the shared-heuristics reuse, the removed fallback, and the `gate_*` counters. Cross-link the Phase 1 telemetry section.
- [ ] Verify `docs/features/README.md` index entry for subconscious memory still resolves (no new file, so likely no index change — confirm).

### Inline Documentation
- [ ] Docstring on `Memory.save()` override explaining the content-gate contract (returns `False` on rejection, mirrors `WriteFilterMixin`).
- [ ] Docstring on `gate_reason` / `MIN_CONTENT_LENGTH` in `agent/memory_quality.py`, including the "length floor is write-gate-only, not in `classify_content`" invariant and why (baseline integrity). Record the concrete 14-char anchor in the `MIN_CONTENT_LENGTH` docstring — `1. Concurrency` classifies as `durable` yet is production junk, so the floor's motivation is documented at the constant.
- [ ] Comment at the deleted-fallback site in `memory_extraction.py` recording that unparseable payloads are dropped+counted (issue #2201), so a future reader doesn't re-add a fallback.

## Success Criteria

- [ ] Ack-only and below-floor content is rejected at the model layer regardless of writer path — one passing test per path in `tests/integration/test_memory_write_gate.py` (criterion 1).
- [ ] The line-splitting fallback (`memory_extraction.py:945-978`) is deleted; unparseable payloads return `[]` and increment `gate_fallback_dropped` (criterion 2).
- [ ] The gate imports its classification from `agent/memory_quality.py` — the same module `tools/memory_eval/ingest_quality.py` uses (criterion 3; grep confirms the import).
- [ ] `gate_rejected_ack`, `gate_rejected_fragment`, `gate_rejected_short`, `gate_fallback_dropped` appear in `/memories/metrics.json` (criterion 4).
- [ ] Gating requires no configuration or manual step — it operates on every `save()` automatically (criterion 6).
- [ ] `gate_rejected_*` counters report non-zero after junk is written across the writer paths, giving immediate, executable evidence of junk *prevented* (criterion 5, pre-merge — this is the checkable half; see the `test_memory_write_gate.py` per-path tests and the counter-presence Verification row). The `junk_rate` *trend* vs the committed baseline is a **non-blocking post-deploy follow-up** (check `/memories/metrics.json` 1-2 weeks after merge), NOT a Step 5/7 Final-Validation gate — Risk 3 confirms `junk_rate` cannot move at deploy time, so Final Validation checks counter wiring + gate behavior, never the trend.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `models/memory.py` imports from `agent.memory_quality`

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (model-gate)**
  - Name: `gate-builder`
  - Role: `Memory.save()` override + `agent/memory_quality.gate_reason` + `_increment_gate_counter`
  - Agent Type: builder
  - Domain: data (Redis/Popoto)
  - Resume: true

- **Builder (extraction-fallback)**
  - Name: `fallback-builder`
  - Role: delete the newline fallback in `_parse_categorized_observations`, wire the `fallback_dropped` counter
  - Agent Type: builder
  - Resume: true

- **Builder (metrics-surface)**
  - Name: `metrics-builder`
  - Role: `_sum_gate_counter` + counter fields in `get_corpus_metrics` / `/memories/metrics.json`
  - Agent Type: builder
  - Resume: true

- **Validator (write-gate)**
  - Name: `gate-validator`
  - Role: verify each of the five writer paths gates junk; verify counters surface; verify no fallback records
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `memory-docs`
  - Role: `docs/features/subconscious-memory.md` write-gate subsection
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 core agents (builder, validator, code-reviewer, test-engineer,
documentarian). For the Redis/Popoto data work, assign a `builder` with a
`Domain: data` line and the matching `DOMAIN_FRAMING.md` rules.

## Step by Step Tasks

### 1. Content gate at the model choke point
- **Task ID**: build-model-gate
- **Depends On**: none
- **Validates**: tests/unit/test_memory_quality.py, tests/integration/test_memory_write_gate.py (create)
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `MIN_CONTENT_LENGTH` and `gate_reason(content)` to `agent/memory_quality.py`, importing/composing the existing `classify_content`. Do NOT alter `classify_content`'s three-bucket output (baseline integrity).
- Add `_increment_gate_counter(project_key, reason)` (try/except-wrapped `INCR` on `{project_key}:memory-gate:{reason}` via `from popoto.redis_db import POPOTO_REDIS_DB as _R`) — new `models/memory_gate.py` or inline in `models/memory.py`.
- Override `Memory.save()` to gate on INSERT only: skip the gate when the record already exists (`POPOTO_REDIS_DB.exists(str(self.db_key))`, try/except → treat as insert on error), else call `gate_reason`, increment on rejection, return `False`; on pass call `super().save()`. Leave `compute_filter_score` unchanged. Verify `save()`/`compute_filter_score` call cardinality to avoid double-count. Verify the outcome re-save at `memory_extraction.py:1343` still persists (update path is never gated).
- Unit + boundary tests: `gate_reason` returns each of `{ack, fragment, short}` distinctly. Assert (verified against the frozen `classify_content`): ack-only → `ack`; `includes:` (dangling colon) → `fragment`; `1.` (bare list marker, no body) → `fragment`; `1. Concurrency` (14 chars, `durable` under the classifier) → `short`; 10-char durable (`deploy fri`) → `short`; None/`""`/whitespace → `fragment`; 17-char durable (`Deploy on Fridays`) → `None`. Do NOT alter `classify_content` — `1. Concurrency` is `durable` and is gated only by the length floor; multi-word shrapnel like `runs on a schedule` (18 chars → durable) is NOT caught by the content gate at all (fallback-removal in Task 2 is what prevents it).

### 2. Remove the line-splitting fallback
- **Task ID**: build-fallback-removal
- **Depends On**: build-model-gate (reuses `_increment_gate_counter`)
- **Validates**: tests/unit/test_memory_extraction.py
- **Assigned To**: fallback-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `memory_extraction.py:945-978` (both the `categorized` and `uncategorized` line loops and returns) and end `_parse_categorized_observations` with a single unconditional `return []`. The parser takes no `project_key` and must not reference one.
- Increment `fallback_dropped` in `extract_observations_async`, not the parser: move `resolve_project_key()` above the `if not parsed:` short-circuit, then `if not parsed: _increment_gate_counter(project_key, "fallback_dropped"); return []`.
- Add the "do not re-add a fallback (#2201)" comment. Default: no retry (Decision 2).
- Update the affected `TestParseCategorizedObservations` cases to assert `[]` — cover all three fall-through inputs: no-JSON text, a `json.loads`-raising input, AND valid-JSON-with-zero-observations.

### 3. Surface gate counters in the metrics endpoint
- **Task ID**: build-metrics-surface
- **Depends On**: build-model-gate
- **Validates**: tests/integration/test_dashboard_memories.py
- **Assigned To**: metrics-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_sum_gate_counter(reason)` (read `{project_key}:memory-gate:{reason}` via `GET`, best-effort, iterating the `pks` already resolved in `get_corpus_metrics` — NOT `get_machine_project_keys()`; reuses `_sum_project_counter`'s `{project_key}:{suffix}` key layout but driven by the local resolved scope) and attach `gate_rejected_ack`/`gate_rejected_fragment`/`gate_rejected_short`/`gate_fallback_dropped` to `get_corpus_metrics`'s return in `ui/data/memories.py`.
- Extend the endpoint integration test for the new fields (empty corpus + Redis-down cases).

### 4. Per-path write-gate integration test
- **Task ID**: build-path-tests
- **Depends On**: build-model-gate, build-fallback-removal
- **Validates**: tests/integration/test_memory_write_gate.py
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: false
- One test per writer path (hook ingest, post-session extraction, post-merge learning, Telegram bridge, CLI save) proving junk is dropped at the model layer and a durable record persists. Clean up test records by `project_key` prefix (Popoto only).

### 5. Validation
- **Task ID**: validate-gate
- **Depends On**: build-metrics-surface, build-path-tests
- **Assigned To**: gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows. Confirm no fallback path can emit per-line records; confirm counters visible; confirm the five paths gate.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-gate
- **Assigned To**: memory-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Add the write-gate subsection to `docs/features/subconscious-memory.md`; verify the README index.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Full suite, all success criteria including docs and the import grep.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_memory_quality.py tests/unit/test_memory_extraction.py tests/integration/test_memory_write_gate.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Gate reuses shared module | `grep -c "from agent.memory_quality import" models/memory.py` | output > 0 |
| Fallback deleted (no per-line return) | `grep -n "for line in uncategorized" agent/memory_extraction.py` | exit code 1 |
| Fallback drop counter wired | `grep -c "fallback_dropped" agent/memory_extraction.py` | output > 0 |
| All four counters in endpoint | `python -c "from ui.data.memories import get_corpus_metrics as g; m=g(); print(all(k in m for k in ('gate_rejected_ack','gate_rejected_fragment','gate_rejected_short','gate_fallback_dropped')))"` | output contains True |
| `gate_reason` taxonomy is `{ack,fragment,short}` | `python -c "from agent.memory_quality import gate_reason as r; print(r('Yup'), r('includes:'), r('deploy fri'), r('Deploy on Fridays'))"` | `ack fragment short None` |
| `classify_content` unchanged (3 buckets) | `python -c "from agent.memory_quality import classify_content as c; print(c('Yup'), c('includes:'), c('Deploy on Fridays'))"` | output contains ack_only |
| No new Popoto field (no migration needed) | `grep -nE "= (String\|Float\|Key\|Dict)Field\(" models/memory.py \| wc -l` | output contains 10 (unchanged from pre-build baseline; a `save()` override adds no field) |

## Critique Results

<!-- Populated by /do-plan-critique (war room), FULL depth, 2026-07-22. Verdict: NEEDS REVISION (2 blockers). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency + Scope & Value | Reason taxonomy vs counter taxonomy contradict: `gate_reason` returns only `{ack, short, None}` (fragment folded into `short` at line 170), so the mandated `gate_rejected_fragment` counter is permanently 0 and `gate_rejected_short` conflates real dangling-syntax fragments (`includes:`, `1. Concurrency`) with below-floor content — the primary junk shape becomes invisible in its own counter. | | Make `gate_reason` return one of exactly `{ack, fragment, short}`: `if c == "fragment": return "fragment"` and reserve `"short"` for the `len(...) < MIN_CONTENT_LENGTH` branch only. Then `_increment_gate_counter(pk, "fragment")` fires for dangling syntax. Verification row must assert ALL FOUR counter keys, not just `gate_rejected_ack`/`gate_fallback_dropped`. |
| BLOCKER | Risk & Robustness | Fallback-removal snippet (Technical Approach lines 176-180, Task 2 line 359) calls `_increment_gate_counter(project_key, ...)` from inside `_parse_categorized_observations(raw_text)`, which has NO `project_key` param; the only caller resolves `project_key` at lines 728-731, AFTER the parse call at line 720 — a `NameError` on every unparseable-extraction path (the counter's try/except cannot catch an error raised while evaluating its own argument). | | Do NOT reference `project_key` inside the parser. Preferred: parser returns `[]` with no counter; in `extract_observations_async` after line 731 do `if not parsed: _increment_gate_counter(project_key, "fallback_dropped")`. Alternative: reorder `resolve_project_key` above line 720 and add the param to the signature. |
| CONCERN | Risk & Robustness | The fallback fires in THREE cases, not one: (1) `payload is None`; (2) `json.loads` raises → `except: pass` falls through; (3) payload parses to a list but yields zero valid results. Plan wording ("when `extract_json_payload` returns `None`", Task 2 "or empty JSON result") omits case (2). Guarding the drop with `if payload is None:` leaves cases (2)/(3) hitting an implicit `return None` → caller's `for` loop over `parsed` raises `TypeError: 'NoneType' is not iterable`. | | Delete the entire block 945-978 and end the function with a single unconditional `return []` so all three fall-through paths converge; do not condition the drop on `payload is None`. Verify with a `json.loads`-raising input AND a valid-JSON-but-zero-observation input, not only a no-JSON input. |
| CONCERN | Risk & Robustness + History & Consistency | Two of four Prerequisites check commands (lines 128, 131) are non-executable, so `python scripts/check_prerequisites.py` fails before work starts: (1) `assert classify_content('x')=='fragment'` is false — `'x'` classifies as `'durable'` (no length floor in `classify_content`); (2) `from tools.redis_client import get_redis_client` imports a module/function that does not exist in the repo. The second is load-bearing: copying that import into the counter code `ImportError`s on the hot write path. | | Prereq row 1 → `assert classify_content('includes:')=='fragment'` (aligns with the Verification row at line 417). Prereq row 4 + all counter code → `from popoto.redis_db import POPOTO_REDIS_DB as _R; _R.ping()` / `_R.incr(...)` / `_R.get(...)` (the handle `monitoring/worker_watchdog.py` uses); there is no `tools.redis_client`. |
| NIT | History & Consistency + Risk & Robustness | Plan claims the `memory-gate:*` counters "mirror the established readable-counter pattern (`_sum_project_counter`)" but proposes key order `memory-gate:{project_key}:{reason}` (namespace first) while `_sum_project_counter` reads `{project_key}:{suffix}` (project_key first). A builder reusing `_sum_project_counter` verbatim would read `{pk}:memory-gate:...` and always get zero. | | Either write keys as `{project_key}:memory-gate:{reason}` to truly reuse the helper, or keep namespace-first and have `_sum_gate_counter` construct keys in the writer's order — drop the "mirror" claim to avoid the trap. |
| NIT | Scope & Value | Near-final code snippets plus an unmotivated `MIN_CONTENT_LENGTH=15` (justified only as "below the hook's 50, aligned with extraction's `len < 10`"). Honestly flagged as Open Question 1, so not blocking, but the constant lacks a data anchor. | | If deferred, wire `gate_rejected_short` first with a conservative/low floor so telemetry reveals how many real records the floor would reject before the value is finalized — measure, then tighten. Or derive 15 from the shortest-durable-record distribution in the committed baseline. |

**Revision applied (2026-07-22):** All 6 findings + the 3 Open Questions resolved. BLOCKER 1 — `gate_reason` now returns exactly `{ack, fragment, short}` (fragment no longer folded into short), so `gate_rejected_fragment` counts real dangling-syntax fragments. BLOCKER 2 — parser stays `project_key`-free; `fallback_dropped` increments in `extract_observations_async` after resolution. CONCERN 3 — fallback block deleted, function ends with a single unconditional `return []` (all three fall-through cases converge). CONCERN 4 — prereq row 1 uses `classify_content('includes:')`, row 4 (and all counter code) uses `from popoto.redis_db import POPOTO_REDIS_DB as _R`. NIT 5 — counter key order is `{project_key}:memory-gate:{reason}` to match `_sum_project_counter`. NIT 6 — `MIN_CONTENT_LENGTH=15` framed as a conservative provisional floor, measure `gate_rejected_short` before tightening. Open Questions folded into `## Decisions`.

**Re-critique (2026-07-22, pass 3 — FULL depth). Verdict: READY TO BUILD (with concerns).** 0 blockers, 2 concerns; Risk & Robustness returned no findings (the twice-revised Risks/Race/Failure-Path sections cover every runtime angle). Both concerns were trivial factual defects in the plan's own executable checks and were embedded inline (no separate `/do-plan` pass — `revision_applied: true`):
- CONCERN (History & Consistency): the "No new Popoto field" Verification row expected a field count of `8`, but the live `models/memory.py` count matching `= (String|Float|Key|Dict)Field(` is `10` (a no-field-change build prints 10) — the row would spuriously fail. **Fixed:** expected value corrected to `10`.
- CONCERN (Scope & Value): Success Criterion 5 named a `junk_rate` *trend* as a pre-merge gate, but Risk 3 confirms `junk_rate` cannot move at deploy time and no Verification row checks a trend — unexecutable at Step 5/7. **Fixed:** criterion 5 split into a pre-merge counter-evidence check (executable) and a non-blocking post-deploy trend follow-up.

**Revision applied (2026-07-22, pass 2 — re-critique NEEDS REVISION):** Verified every claim against live `agent/memory_quality.py` / `agent/memory_extraction.py` before editing. BLOCKER (`1. Concurrency` boundary test unsatisfiable) — confirmed `classify_content("1. Concurrency") == "durable"` (14 chars; the bare-marker regex `^([-*•]|\d+[.)])\s*$` requires no body) and `classify_content("1.") == "fragment"`. Task 1 now asserts `gate_reason("1. Concurrency") == "short"` and adds `gate_reason("1.") == "fragment"`; Technical Approach + Problem + Task 1 state that fallback-removal (not the content gate) prevents multi-word shrapnel like `runs on a schedule` (18 chars → durable, ungated). `classify_content` is NOT altered. CONCERN 1 (gate fires on UPDATE re-saves) — `save()` override now gates on INSERT only via `POPOTO_REDIS_DB.exists(str(self.db_key))`; the outcome re-save at `memory_extraction.py:1343` bypasses the gate; Risk 2 upgraded from assumption to structural guarantee; update-never-gated test added. CONCERN 2 (gate-counter scope) — `_sum_gate_counter` iterates the `pks` resolved in `get_corpus_metrics` (`ui/data/memories.py:276`), not `get_machine_project_keys()`. CONCERN 3 (Data Flow / Technical Approach contradiction) — Data Flow now describes the same THREE fall-through cases as Technical Approach. NIT — 14-char `1. Concurrency` anchor recorded in the `MIN_CONTENT_LENGTH` comment/docstring.

---

## Decisions (resolved at revision)

The three questions the plan surfaced are resolved as follows. Each choice is the
conservative, ship-now option consistent with the critique guidance.

1. **Length floor — write-gate-only, provisional value, measure before tightening.**
   `MIN_CONTENT_LENGTH=15` is a *write-gate-only* dimension. `classify_content`'s
   three buckets stay **frozen** so the committed baseline's `junk_rate` remains an
   apples-to-apples reference (criterion 5). The alternative — folding a fourth
   `too_short` bucket into `classify_content` — is rejected: it would redefine
   `junk_rate` and force a baseline recompute. `15` ships as a conservative
   provisional floor; `gate_rejected_short` is wired first so telemetry reveals how
   many real records the floor would reject before the value is finalized. Tighten
   only after measuring (or anchor to the shortest-durable-record distribution in
   the committed baseline). No `classify_content` change.

2. **No retry before dropping an unparseable extraction payload.** JSON is the
   sanctioned contract since #1212/#2016; a stricter-format retry adds an LLM call
   plus latency for no proven gain. Unparseable output is dropped and counted
   (`fallback_dropped`). No retry.

3. **Criterion 5 read as counters + trend, not an instant drop.** Write gates
   prevent *new* junk; they cannot remove the 59 existing fragments, so `junk_rate`
   declines only as new durable records accumulate — it will not visibly move the
   day of deploy. The `gate_rejected_*` counters (junk *prevented*) plus a
   `junk_rate` trend over a window are the honest Phase-2 signal and satisfy
   criterion 5. Existing-fragment cleanup stays in Phase 4 (#2203); it is NOT pulled
   forward into this phase.
