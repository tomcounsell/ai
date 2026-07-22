---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-22
tracking: https://github.com/tomcounsell/ai/issues/2201
last_comment_id:
---

# Unify Memory Write-Path Quality Gates + Remove Line-Splitting Fallback

## Problem

Valor's subconscious memory system stores memories automatically during regular
use — there is no human curator, so write-time quality gating is the only
legitimate defense against noise. Today that defense guards exactly one of the
writer paths.

Production memory (2026-07-22 baseline: 1977 durable records) contains 59
fragment records (`junk_rate = 2.98%`): dangling syntax like `includes:`,
`1. Concurrency`, `runs on a schedule`. Two distinct causes:

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
3. **Fallback (`:945-978`)** — when `extract_json_payload` returns `None`: splits `raw_text` on `\n`, emits one `(content, importance, {})` tuple per line → **one Memory per line**. This is removed.
4. **Output** — parsed tuples are saved via `Memory.safe_save` in the caller loop (`:784`).

## Architectural Impact

- **New dependencies:** `models/memory.py` gains an import of `agent.memory_quality`. That module is deliberately dependency-light (no popoto/redis/models imports) precisely so a hot write path can import it without circular imports — verified in its docstring. No new third-party deps.
- **Interface changes:** `Memory` gains an overridden `save()` (content gate + counter). `compute_filter_score()` stays as-is (importance filtering unchanged). `agent/memory_quality.py` gains a write-gate predicate (`gate_reason` / length-floor helper). `get_corpus_metrics()` gains gate-counter fields. No writer-path signatures change.
- **Coupling:** *decreases* net complexity — four ungated paths stop needing their own content logic; one choke point owns it. Measurement and enforcement share `agent/memory_quality`, so they cannot drift (acceptance criterion 3).
- **Data ownership:** unchanged. New Redis counter keys under a bespoke `memory-gate:*` namespace (not Popoto-managed).
- **Reversibility:** high — revert is the `save()` override + fallback deletion + counter reads. No schema migration, no data rewrite.

## Appetite

**Size:** Medium

**Team:** Solo dev, plan critique, code reviewer

**Interactions:**
- PM check-ins: 1-2 (the length-floor-vs-baseline measurement question below is the main alignment point)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Phase 1 shared module present | `python -c "from agent.memory_quality import classify_content; assert classify_content('x')=='fragment'"` | Reuse target for the content gate (criterion 3) |
| Phase 1 corpus metrics present | `python -c "from tools.memory_eval.ingest_quality import compute_corpus_metrics; compute_corpus_metrics([])"` | Endpoint the counters attach to (criterion 4) |
| Committed baseline present | `test -f docs/baselines/memory-telemetry-baseline.json` | Reference for the before/after junk-rate comparison (criterion 5) |
| Redis reachable | `python -c "from tools.redis_client import get_redis_client; get_redis_client().ping()"` | Gate counters use `INCR`/`GET` |

Run via `python scripts/check_prerequisites.py docs/plans/memory-write-gate-unification.md`.

## Solution

### Key Elements

- **Content gate at the model choke point** — a single predicate applied inside `Memory.save()` that rejects ack-only, fragment/dangling-syntax, and below-floor content before persistence. Because every writer path calls `save()`, all five inherit the gate with zero per-path edits.
- **Shared heuristics, one source of truth** — the gate imports its classification from `agent/memory_quality.py`, the same module Phase 1's junk-rate metric uses. A future refinement to "what is junk" lands once and both consumers move together.
- **Fallback deletion** — the newline-splitting branch in `_parse_categorized_observations` is removed. An unparseable payload is dropped and counted, never exploded into per-line records.
- **Readable dropped-write counters** — a `memory-gate:*` Redis counter namespace (per project_key), incremented at each rejection site and surfaced in `/memories/metrics.json`, so gate effectiveness is visible in the Phase 1 telemetry endpoint.

### Flow

Any writer constructs a `Memory` → `save()` → content gate reads `content` → if ack-only / fragment / below-floor: increment `memory-gate:{reason}` counter, return `False` (dropped, silent) → else `WriteFilterMixin` importance gate → persisted.

Extraction receives non-JSON Haiku output → JSON extraction returns `None` → increment `memory-gate:fallback_dropped` → return `[]` (nothing saved).

Operator/dashboard reads `/memories/metrics.json` → sees `gate_rejected_ack`, `gate_rejected_fragment`, `gate_rejected_short`, `gate_fallback_dropped` alongside `junk_rate`.

### Technical Approach

- **Override `Memory.save()`, leave `compute_filter_score()` unchanged.** Keeping `compute_filter_score` returning `importance` preserves the existing importance-threshold filtering. The new content gate is a distinct concern, so it lives in an overridden `save()`:
  ```python
  def save(self, *args, **kwargs):
      reason = content_gate_reason(self.content)   # None | "ack" | "fragment" | "short"
      if reason:
          _increment_gate_counter(self.project_key, reason)   # try/except, never raises
          return False   # matches WriteFilterMixin's drop contract; safe_save maps False→None
      return super().save(*args, **kwargs)
  ```
  This counts exactly once per rejected write (avoids the double-count hazard of counting inside `compute_filter_score`, which `WriteFilterMixin` may call more than once — the builder must verify call cardinality either way).
- **`content_gate_reason` lives in `agent/memory_quality.py`.** It composes the existing `classify_content` (returns `ack_only`/`fragment`/`durable`) with a new length-floor check. This keeps the single-source-of-truth invariant (criterion 3). Proposed helper:
  ```python
  MIN_CONTENT_LENGTH = 15   # see Open Question 1
  def gate_reason(content: str | None) -> str | None:
      c = classify_content(content)
      if c == "ack_only": return "ack"
      if c == "fragment": return "short"  # None/empty already classify as fragment
      if len((content or "").strip()) < MIN_CONTENT_LENGTH: return "short"
      return None
  ```
- **Measurement integrity — keep `classify_content`'s three buckets frozen.** The length floor is a *write-gate-only* dimension; do NOT fold it into `classify_content` (which drives the frozen baseline's `junk_rate`). Reclassifying below-floor durable records as junk would change the junk-rate *definition* and break the apples-to-apples baseline comparison (criterion 5). Length-floor rejections are visible via their own `gate_rejected_short` counter instead. (See Open Question 1 for the alternative.)
- **Remove the fallback (`memory_extraction.py:945-978`).** Replace both the `categorized` and `uncategorized` returns with a drop + counter:
  ```python
  # payload is None (no JSON-shaped substring) → unparseable
  _increment_gate_counter(project_key, "fallback_dropped")
  return []
  ```
  Default: **no retry** before dropping — JSON is the sanctioned contract since #1212/#2016, and a retry adds an LLM call plus latency. (See Open Question 2.)
- **Counters use `INCR`/`GET` on a bespoke `memory-gate:*` namespace**, mirroring the established readable-counter pattern (`ui/app.py:434 _sum_project_counter`; `monitoring/worker_watchdog.py:409 _R.incr`). These are NOT Popoto-managed keys, so `INCR`/`GET` are allowed — the raw-Redis ban (`validate_no_raw_redis_delete.py`) targets `delete`/`srem`/`sadd`/`zrem` on model keys only. All increments wrapped in try/except so a Redis hiccup never crashes a write.
- **Surface counters in `get_corpus_metrics` (`ui/data/memories.py`).** Phase 1 explicitly skipped counter attachment because `analytics.record_metric` is write-only (`:297-303`). Phase 2 supplies a readable path: a `_sum_gate_counter(project_key, reason)` helper reads the `memory-gate:*` keys and adds `gate_rejected_ack`/`gate_rejected_fragment`/`gate_rejected_short`/`gate_fallback_dropped` to the metrics dict, so `/memories/metrics.json` reports them (criterion 4).
- **Keep path-specific gates in place.** The hook's `MIN_PROMPT_LENGTH=50`/`TRIVIAL_PATTERNS` pre-filter and bloom dedup stay — they short-circuit obvious junk before doing bloom/embedding work. The model gate is the backstop that catches the other four paths. Redundant on the hook path, authoritative everywhere else.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_increment_gate_counter` wraps its `INCR` in `try/except` — add a test that a raising Redis client does NOT propagate (the write still returns its normal result; the counter is best-effort). Assert observable behavior (no exception escapes `save()`).
- [ ] `_sum_gate_counter` in `get_corpus_metrics` — on Redis failure it returns 0 and the metrics dict is still well-formed (matches the loader's never-crash contract). Test with a failing client.
- [ ] `safe_save` already logs on `result is False` (`models/memory.py:196`) — confirm a gated write logs at debug and returns `None`, no raise.

### Empty/Invalid Input Handling
- [ ] `gate_reason(None)`, `gate_reason("")`, `gate_reason("   ")` — all return a rejection reason (`classify_content` maps these to `fragment` → gated). Add explicit tests.
- [ ] Below-floor durable content (`"deploy fri"`, 10 chars) → `"short"`; at-floor durable content (`"Deploy on Fridays"`, 17 chars) → `None` (persists). Boundary tests around `MIN_CONTENT_LENGTH`.
- [ ] Extraction fallback: non-JSON, multi-line `raw_text` → `_parse_categorized_observations` returns `[]` (no per-line records) and increments `fallback_dropped`.

### Error State Rendering
- [ ] `/memories/metrics.json` renders the new counter fields even when the corpus is empty and when Redis is down (zero-filled, HTTP 200). Extend `tests/integration/test_dashboard_memories.py`.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py::TestParseCategorizedObservations` (`:819+`) — UPDATE: cases asserting the newline/`CATEGORY:`-line fallback produced records now assert `[]`. The JSON-path cases stay unchanged. Any case that fed non-JSON text expecting per-line output is REPLACED with a drop+counter assertion.
- [ ] `tests/unit/test_memory_quality.py` — UPDATE: add `gate_reason` / `MIN_CONTENT_LENGTH` boundary cases. Existing `classify_content`/`is_ack_only`/`is_fragment` assertions stay unchanged (classification is frozen).
- [ ] `tests/unit/test_memory_eval.py` — no change expected (junk-rate definition unchanged by design); confirm and state so. If Open Question 1 flips to fold length into `classify_content`, this file's fixtures REPLACE.
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
**Mitigation:** Set the floor conservatively at 15 chars (below the hook's 50, aligned with extraction's existing `len < 10` observation drop). Boundary tests. The `gate_rejected_short` counter makes over-rejection observable in telemetry rather than invisible.

### Risk 2: The gate fires on update re-saves of existing records
**Impact:** `save()` is also called by metadata-update paths (`memory_extraction.py:1343` outcome update, title write-back). If an already-persisted record's content is junk, the update would be dropped (returns `False`) and the outcome/title lost.
**Mitigation:** Durable content stays durable — records that were persisted and injected have durable content by definition, so the gate passes on re-save. Add a test that an outcome-update re-save of a durable record succeeds. Low probability; the only exposure is legacy junk already in the store, which Phase 4 removes anyway.

### Risk 3: Post-deploy junk_rate does not immediately drop (criterion 5 timing)
**Impact:** Write gates prevent *new* junk but cannot remove the 59 existing fragments. `junk_rate = 59 / (1977 + new_durable)` only declines as new durable records accumulate — it will not visibly move the day of deploy.
**Mitigation:** Frame criterion 5 as a trend measured over a window, not an instant drop. The `gate_rejected_*` counters give immediate, direct evidence of gate effectiveness (junk *prevented*), which is the honest Phase-2 signal. Flag to supervisor (Open Question 3).

### Risk 4: Counting inside a filter method double-counts
**Impact:** If the counter increment were placed in `compute_filter_score`, and `WriteFilterMixin` calls it more than once per `save()`, rejections over-count.
**Mitigation:** Count in the overridden `save()` (called once per persist attempt), not in `compute_filter_score`. Builder verifies call cardinality of both.

## Race Conditions

### Race 1: Concurrent gate-counter increments across worker/bridge/hook processes
**Location:** `_increment_gate_counter` (new, `models/memory.py` or a small `models/memory_gate.py`).
**Trigger:** The bridge, worker, and Claude Code hooks can all reject a write concurrently for the same `project_key`.
**Data prerequisite:** The `memory-gate:{project_key}:{reason}` key exists or is created by `INCR` (atomic, creates-on-missing).
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
- [ ] Docstring on `gate_reason` / `MIN_CONTENT_LENGTH` in `agent/memory_quality.py`, including the "length floor is write-gate-only, not in `classify_content`" invariant and why (baseline integrity).
- [ ] Comment at the deleted-fallback site in `memory_extraction.py` recording that unparseable payloads are dropped+counted (issue #2201), so a future reader doesn't re-add a fallback.

## Success Criteria

- [ ] Ack-only and below-floor content is rejected at the model layer regardless of writer path — one passing test per path in `tests/integration/test_memory_write_gate.py` (criterion 1).
- [ ] The line-splitting fallback (`memory_extraction.py:945-978`) is deleted; unparseable payloads return `[]` and increment `gate_fallback_dropped` (criterion 2).
- [ ] The gate imports its classification from `agent/memory_quality.py` — the same module `tools/memory_eval/ingest_quality.py` uses (criterion 3; grep confirms the import).
- [ ] `gate_rejected_ack`, `gate_rejected_fragment`, `gate_rejected_short`, `gate_fallback_dropped` appear in `/memories/metrics.json` (criterion 4).
- [ ] Gating requires no configuration or manual step — it operates on every `save()` automatically (criterion 6).
- [ ] `junk_rate` trend + `gate_rejected_*` counters demonstrate junk prevention vs the committed baseline (criterion 5; see Risk 3 on timing).
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
- Add `_increment_gate_counter(project_key, reason)` (try/except-wrapped `INCR` on `memory-gate:{project_key}:{reason}`) — new `models/memory_gate.py` or inline in `models/memory.py`.
- Override `Memory.save()` to call `gate_reason`, increment on rejection, return `False`; else `super().save()`. Leave `compute_filter_score` unchanged. Verify `save()`/`compute_filter_score` call cardinality to avoid double-count.
- Unit + boundary tests (None/""/whitespace, 10-char vs 17-char, ack-only).

### 2. Remove the line-splitting fallback
- **Task ID**: build-fallback-removal
- **Depends On**: build-model-gate (reuses `_increment_gate_counter`)
- **Validates**: tests/unit/test_memory_extraction.py
- **Assigned To**: fallback-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `memory_extraction.py:945-978` (both `categorized` and `uncategorized` returns). On `payload is None` (or empty JSON result), increment `fallback_dropped` and `return []`.
- Add the "do not re-add a fallback (#2201)" comment. Default: no retry (Open Question 2 may change this).
- Update the affected `TestParseCategorizedObservations` cases to assert `[]`.

### 3. Surface gate counters in the metrics endpoint
- **Task ID**: build-metrics-surface
- **Depends On**: build-model-gate
- **Validates**: tests/integration/test_dashboard_memories.py
- **Assigned To**: metrics-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_sum_gate_counter` (read `memory-gate:*` via `GET`, best-effort, per machine project key) and attach `gate_rejected_ack`/`gate_rejected_fragment`/`gate_rejected_short`/`gate_fallback_dropped` to `get_corpus_metrics`'s return in `ui/data/memories.py`.
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
| Counters in endpoint | `python -c "from ui.data.memories import get_corpus_metrics as g; m=g(); print('gate_rejected_ack' in m and 'gate_fallback_dropped' in m)"` | output contains True |
| `classify_content` unchanged (3 buckets) | `python -c "from agent.memory_quality import classify_content as c; print(c('Yup'), c('includes:'), c('Deploy on Fridays'))"` | output contains ack_only |
| No new Popoto field (no migration needed) | `grep -nE "= (String\|Float\|Key\|Dict)Field\(" models/memory.py \| wc -l` | output contains 8 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Length floor: value, and does it feed the junk-rate definition?** The plan
   defaults to `MIN_CONTENT_LENGTH=15` as a *write-gate-only* dimension, keeping
   `classify_content`'s three buckets frozen so the committed baseline's
   `junk_rate` stays an apples-to-apples reference (criterion 5). The alternative
   is folding a fourth `too_short` bucket into `classify_content`, which unifies
   the junk definition fully (criterion 3 in its strongest reading) but changes
   what `junk_rate` counts and would require recomputing the "baseline" against
   the new definition. Which do you want: write-gate-only floor (default,
   preserves baseline), or unified `classify_content` (stronger single-source,
   redefines the metric)?

2. **Retry before dropping an unparseable extraction payload?** The issue leaves
   this open. Default: no retry — drop + count (JSON is the sanctioned contract
   since #1212/#2016; a retry adds an LLM call and latency). Accept the default,
   or should the fallback do one stricter-format retry before dropping?

3. **Criterion 5 framing.** Write gates prevent new junk but cannot remove the 59
   existing fragments, so `junk_rate` will not visibly drop the day of deploy (it
   declines as new durable records accumulate). Is the `gate_rejected_*` counter
   evidence (junk *prevented*) plus a junk-rate *trend* an acceptable read of
   "measurably drops vs baseline," or do you want existing-fragment cleanup
   pulled forward from Phase 4 into this phase?
