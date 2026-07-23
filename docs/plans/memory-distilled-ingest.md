---
status: docs_complete
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2202
last_comment_id: 5053660188
revision_applied: true
revision_applied_at: 2026-07-23T03:17:40Z
---

# Distilled Human Ingest: Extraction-Based Memories + Content-Derived Importance

## Problem

The subconscious-memory hook path stores human prompts **verbatim** at a **flat
importance of 6.0**. Two structural defects follow:

1. **Wrong unit of storage.** `hook_utils/memory_bridge.py::ingest()` saves the
   raw utterance (`stripped[:500]`) as the memory content. A chat line
   ("Rewrite justfile in a way") retrieves poorly (pronouns, ellipsis, missing
   context) and ages worse than a distilled fact ("Tom wants the justfile
   rewritten").
2. **Content-blind importance.** Every human record is written at a hardcoded
   `importance=6.0` (`memory_bridge.py:817`). When everything is 6.0, a throwaway
   remark and a standing preference rank identically, and `relevance`
   (`DecayingSortedField(base_score_field="importance")`, `models/memory.py:173`)
   inherits the flatness, so decay ranking is content-blind too.

Distilled-quality memories exist today only via the *post-session* extraction
path (`agent/memory_extraction.py::extract_observations_async`), not the live
ingest path.

**Current behavior:** production shows ~all human records clustered at 6.0
(baseline: 28 human records, importance dominated by the 6.0 spike).

**Desired outcome:** live ingest produces distilled memories with
content-derived importance; the importance distribution shows spread rather than
a single 6.0 spike; the human>agent source prior survives as a *factor*, not the
entire signal; the 8s hook deadline is never violated.

## Freshness Check

**Baseline commit:** `3c0fc7ee103b955201f026af01852b41b57dc361`
**Issue filed at:** 2026-07-22T04:31:26Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `.claude/hooks/hook_utils/memory_bridge.py:813-819` — verbatim save at flat
  `importance=6.0`, `source=SOURCE_HUMAN` — **still holds** (confirmed literal
  `importance=6.0` at line 817).
- `agent/memory_extraction.py:502` — `extract_observations_async` post-session
  distillation machinery — **still holds** (signature at line 502; routes every
  Haiku call through `_llm_call` → `agent.llm.run_typed`, `MODEL_FAST` at
  line 657).
- `models/memory.py:152` (issue's pointer for the `relevance` DecayingSortedField)
  — **drifted to line 173**; the `base_score_field="importance"` binding is
  intact. Docstring importance note at lines 7-9, 129.
- `.claude/hooks/user_prompt_submit.py:48` — `MEMORY_HOOK_DEADLINE_SECONDS = 8`
  SIGALRM wall-clock guard wrapping ingest+prefetch — **still holds exactly**.

**Cited sibling issues/PRs re-checked:**
- #2200 (Phase 1 baseline) — CLOSED/merged. Baseline artifact committed at
  `docs/baselines/memory-telemetry-baseline.json` (+ `.md`). This is the
  denominator for act-rate lift.
- #2201 (Phase 2 gates) — CLOSED, shipped via PR #2215 (commit `e563efd19`, the
  one commit touching referenced files since the issue was filed). `Memory.save()`
  now gates all five writer paths on `agent/memory_quality.py::gate_reason()`
  (ack/fragment/short), INSERT-only (existence check skips gating on UPDATE). The
  newline-splitting extraction fallback is deleted. The issue's "Phase 2 should
  land first" framing is now a **satisfied prerequisite**, not a forward
  dependency.
- popoto #461/#489 — the substrate `ClaudeExtractionProvider` path. **Explicitly
  not adopted** this phase (see the engine-choice decision below).

**Commits on main since issue was filed (touching referenced files):**
- `e563efd19` "Unify memory write-path quality gates (#2215)" — **changed the
  save choke point** in our favor: the content gate is INSERT-only, which is
  exactly the shape our persist-provisional-then-update design needs (the
  distillation re-save is an UPDATE, so it is not re-gated).

**Active plans in `docs/plans/` overlapping this area:** none live.
`memory-telemetry-baseline.md` and `memory-write-gate-unification.md` are the
completed Phase 1/2 plans (now under `docs/plans/completed/`). This plan is
Phase 3 and builds forward on both.

**Notes:** No root-cause drift. The only substantive landscape change (Phase 2
merge) strengthens the plan's chosen shape rather than invalidating it.

## Prior Art

- **#2201 / PR #2215**: Unify memory write-path quality gates — shipped the
  INSERT-only content gate at `Memory.save()` and the `{project_key}:memory-gate:{reason}`
  counter pattern (`models/memory_gate.py`). This phase reuses both: junk is
  already rejected before storage, and the counter pattern is the template for
  distillation telemetry.
- **#2200**: Memory telemetry baseline — shipped `tools/memory_eval/ingest_quality.py`
  (corpus act-rate + importance histogram) and the committed baseline artifact.
  This phase reuses that aggregator verbatim for the lift report.
- **#1904**: Embedding timeout silently drops Memory records in `safe_save` —
  the originating issue for `reflections/memory/memory_embedding_backfill.py`, the
  persist-now/backfill-later reflection this plan copies in shape (dry-run scaffold,
  per-run cap, fail-open, `os.environ.get` runtime toggle). This plan reuses that
  structure but inverts the apply-default to `true` (distillation is steady state,
  not a one-off remediation — see Update System).
- **#524**: Intentional memory saves for project-scoped learnings — established
  the higher-importance manual-save band (7.0-8.0); informs the importance
  formula's upper range.
- **#1310 / #393**: Trajectory / episode memory — scoping explorations, not
  shipped; no reusable code, but confirm no competing live distillation path
  exists.

No prior attempt shipped live-ingest distillation, so there is no failed-fix
pattern to analyze (the "Why Previous Fixes Failed" section is omitted).

## Research

No external WebSearch performed — the engine choice was resolved by operator
decision (reuse Valor's own machinery, do **not** adopt popoto's
`ClaudeExtractionProvider`), and every dependency (Haiku via `_llm_call`, the
reflection scheduler, the eval aggregator) is internal. Proceeding on codebase
context and the recorded architectural decision.

## Spike Results

### spike-1: Can the async distillation run as an in-hook daemon thread?
- **Assumption**: "We can mirror `title_generator.generate_title_async` — spawn a
  daemon thread from `ingest()` that calls Haiku and re-saves the record."
- **Method**: code-read (`tools/memory_search/title_generator.py`,
  `.claude/hooks/user_prompt_submit.py`).
- **Finding**: **Invalidated for a cloud LLM call.** `user_prompt_submit.py` is
  an ephemeral hook process: `main()` emits its JSON and the process exits.
  `generate_title_async` uses a `daemon=True` thread, which the interpreter
  **kills on process exit**. This is tolerable for the title generator because it
  hits a localhost Ollama endpoint that returns in tens of ms, usually before the
  process exits. A cloud Haiku distillation call takes ~1-3s and would be killed
  mid-flight on most turns, silently dropping distillation. The in-hook daemon
  thread is **not** a reliable async cadence for a network LLM call.
- **Confidence**: high.
- **Impact on plan**: The async cadence must live in a **long-lived process**.
  Chosen shape: synchronous provisional insert in `ingest()` (cheap, within the
  8s deadline) + a **backfill reflection** that distills provisional records out
  of band, exactly mirroring `reflections/memory/memory_embedding_backfill.py`
  (the persist-now/backfill-later precedent the issue's GracefulEmbeddingField
  reference points at). The reflection scheduler is a standing subprocess
  (`com.valor.reflection-worker`), so the LLM call completes reliably.

### spike-2: Does the distillation re-save re-gate or corrupt indexes?
- **Assumption**: "Re-saving the provisional record to overwrite content +
  importance is safe."
- **Method**: code-read (`models/memory.py::save`, `memory_embedding_backfill.py`).
- **Finding**: The re-save is an **UPDATE** (key exists) → the INSERT-only content
  gate at `Memory.save()` is skipped (verified: `_key_exists(self.db_key)` guard,
  lines 247-252). BUT: a bare `save()` re-runs `on_save` for **every** field,
  re-stamping the `relevance` DecayingSortedField (`auto_now`) to "now". The
  embedding-backfill reflection deliberately uses a **partial**
  `save(update_fields=["embedding"])` to avoid that. For distillation the content
  itself changes (verbatim → fact), so BM25 + bloom + embedding **must** re-index
  on the new content — a partial save on `["content", "importance", "metadata"]`
  is required, and re-stamping `relevance` to "now" is **acceptable and arguably
  correct** here (the record only becomes meaningful once distilled). This is a
  deliberate divergence from the embedding-backfill partial-save, documented in
  the reflection.
- **Confidence**: high.
- **Impact on plan**: Distillation re-save uses
  `save(update_fields=["content", "importance", "metadata"])`. `title` re-fires
  via the existing async title generator on the distilled content. No new Popoto
  field is added (all state rides existing `metadata` DictField), so **no schema
  migration is required**.

### spike-2b: Does `WriteFilterMixin` drop a partial UPDATE below the importance floor? (added in revision)
- **Assumption**: "A partial `save(update_fields=[...])` skips the write filter,
  so the distilled importance value can be anything."
- **Method**: code-read (`popoto/models/base.py` `save()`,
  `popoto/fields/write_filter.py`, `models/memory.py:191-201`).
- **Finding**: **Invalidated — this is the revision blocker.** `Model.save()`
  runs `_check_write_filter()` **before** the `update_fields` branch
  (`popoto/models/base.py:1094-1096`), on **every** save, INSERT or partial
  UPDATE alike. `Memory` sets `_wf_min_threshold = 0.15` as a plain class
  attribute (`models/memory.py:192`, shadowing the mixin property) and
  `compute_filter_score()` returns `self.importance` unconditionally
  (`models/memory.py:195-201`). So if `compute_ingest_importance()` yields
  `< 0.15`, the partial distillation `save(update_fields=[...])` raises
  `SkipSaveException` → returns `False` → **the distillation write is silently
  lost, the record stays `distill_status=provisional`, and the backfill
  reflection re-attempts it forever** (the INSERT-only content gate at
  `Memory.save()` is skipped on UPDATE, but the write filter is NOT — it is
  upstream of both branches). Note: the CATEGORY_IMPORTANCE bands are 1.0–4.0
  (`agent/memory_extraction.py:471-477`), safely above 0.15, so a category-mapped
  distilled importance never trips this. The hazard is any formula output — or a
  provisional-insert constant — that dips below 0.15.
- **Confidence**: high (verified against the vendored popoto source).
- **Impact on plan**: three coupled requirements, all landing in this revision:
  1. **Floor by construction.** `compute_ingest_importance()` clamps its result
     to `max(computed, MEMORY_WF_MIN_THRESHOLD)` (0.15, the constant already in
     `config/memory_defaults.py:34`). The **provisional-insert importance is
     floored the same way** — this is load-bearing: every later partial save
     (distilled OR the terminal-abandon write below) re-runs the filter on the
     record's current importance, so the record must sit at ≥ 0.15 from birth or
     it can never be updated at all.
  2. **Inspect the `save()` return.** The distillation re-save captures the
     boolean result; a `False` means the write filter dropped it. The record is
     never left silently un-updated: the reflection increments the attempt
     counter and, on a drop or attempt-cap breach, transitions the record to the
     terminal `distill_abandoned` state (see below).
  3. **Terminal state for provisional records** (resolves the infinite-retry
     loop, and shared root with the attempt-cap and kill-switch concerns):
     `distill_status` gains a terminal `distill_abandoned` value alongside
     `distilled`. The backfill scan filters on `distill_status == "provisional"`
     only, so an abandoned record is never re-scanned. The abandon write is a
     metadata-only partial save (`save(update_fields=["metadata"])`) on a record
     whose floored importance already clears the filter, so the terminal write
     itself is guaranteed to persist.

## Data Flow

1. **Entry point**: Human prompt arrives at `UserPromptSubmit` hook
   (`.claude/hooks/user_prompt_submit.py`), wrapped in the 8s SIGALRM deadline.
2. **Synchronous provisional insert** (`memory_bridge.py::ingest`): existing
   length/trivial/bloom filters run, then `Memory.safe_save(...)` persists a
   record with `content=verbatim[:500]`,
   `importance=PROVISIONAL_INGEST_IMPORTANCE` (a named tunable **above** the bare
   `MEMORY_WF_MIN_THRESHOLD` floor so provisional records stay retrievable in the
   pre-distillation window — see the provisional-importance note in Technical
   Approach), `source=SOURCE_HUMAN`, and
   `metadata={"distill_status": "provisional", "distill_attempts": 0, "distill_last_attempt_at": 0}`.
   The `distill_last_attempt_at: 0` seed is **load-bearing**: the backfill scan
   sorts ascending by that key, and a missing key would sort as `None` — Python's
   `sorted()` raises `TypeError` comparing `None` against a stamped float
   timestamp, and that comparison happens during scan setup **outside** the
   per-record `try/except`, aborting the whole backfill run every cycle (fresh
   provisionals are the steady state). Seeding `0` (and the defensive
   `.get(..., 0)` in the scan sort, below) closes this two ways. Cheap, no LLM —
   well within the deadline. Nothing is lost. The importance floor is also
   load-bearing: it guarantees every later partial save on this record clears the
   write filter.
3. **Out-of-band distillation** (`reflections/memory/memory_distill_backfill.py`,
   scheduled at a fast cadence — see the cadence note in Technical Approach):
   queries non-superseded records with `metadata.distill_status == "provisional"`,
   **sorted ascending by `metadata.distill_last_attempt_at`, using a defensive
   `key=lambda r: r.metadata.get("distill_last_attempt_at", 0)`** so a record
   missing the key (or with an explicit `0` seed) never surfaces `None` into the
   Python-side `sorted()` — a `None`-vs-float comparison would raise `TypeError`
   at scan setup, outside the per-record `try/except`, and abort the entire run.
   Least-recently attempted first, so a poison-pill record that keeps failing
   sinks to the back and never crowds out fresh records; capped per run. For each,
   it first
   increments `metadata.distill_attempts` and stamps
   `metadata.distill_last_attempt_at`, then calls
   `agent/memory_extraction.py::distill_human_prompt_async` (new, thin wrapper
   over the existing `_llm_call` + `MODEL_FAST`) with the pinned distillation
   prompt. If `distill_attempts` already exceeds `MAX_DISTILL_ATTEMPTS`, the
   record is transitioned to terminal `distill_abandoned` instead of retried.
4. **Content-derived importance + rewrite**: the distillation returns
   `{fact, salience_or_category}`. Importance is recomputed via
   `compute_ingest_importance(source_weight, content_value)`, **clamped to
   `max(computed, MEMORY_WF_MIN_THRESHOLD)`**. **Immediately before the write, the
   record's `distill_status` is re-read from Redis; if it is no longer
   `"provisional"` (a concurrent run already handled it), the write is skipped —
   this is the primary Race-1 guard, see Race Conditions.** The record is updated
   with `content=fact`, `importance=<computed, floored>`,
   `metadata={distill_status:"distilled", distill_model, distill_prompt_version, distill_attempts, ...}`
   via a partial `save(update_fields=["content","importance","metadata"])`. **The
   boolean return is inspected**: a `False` (write-filter drop — should not happen
   after flooring, but caught defensively) increments the attempt counter and, on
   cap breach, transitions the record to terminal `distill_abandoned` via a
   metadata-only partial save. On success, BM25/bloom/embedding re-index on the
   fact; the async title generator re-fires.
5. **Output**: distilled records with spread importance feed the RRF recall path
   (`agent/memory_retrieval.py` / `memory_bridge.py::prefetch`) on subsequent
   turns. Aggregate + per-source act-rate and importance-histogram are measured by
   `tools/memory_eval/ingest_quality.py` against the Phase 1 baseline.

## Architectural Impact

- **New dependencies**: none external. New internal callables: a distillation
  wrapper in `agent/memory_extraction.py`, an importance helper, one new
  reflection module.
- **Interface changes**: `ingest()` gains a provisional-marker write (backward
  compatible — added metadata key). No public signature change.
- **Coupling**: adds a coupling from the reflection layer to
  `agent/memory_extraction` (already the extraction owner) — low, matches
  existing memory reflections.
- **Data ownership**: the reflection now co-owns human-record content (rewrites
  verbatim → fact). Marked and reversible via the `distill_status` metadata.
- **Reversibility**: high, and explicitly does not strand in-flight records.
  Disabling the reflection (registry `enabled: false`) or dropping the
  provisional-marker write would otherwise leave records stuck at
  `distill_status=provisional` with verbatim content. The module therefore ships a
  one-off idempotent sweep — `sweep_provisional_to_abandoned()` (runnable via
  `python -m reflections.memory.memory_distill_backfill --sweep-abandon`) — that
  transitions every remaining `provisional` record to terminal `distill_abandoned`
  (verbatim content retained, floored importance retained, so the metadata-only
  write clears the filter). Existing `distilled` records remain valid and
  untouched. This makes the feature cleanly disableable with no orphaned state.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (importance-formula shape, measurement window)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Anthropic API key (Haiku distillation) | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | Distillation LLM calls |
| Phase 1 baseline artifact present | `test -f docs/baselines/memory-telemetry-baseline.json` | Lift denominator |
| Reflection scheduler resolvable | `python -m reflections --dry-run` | Backfill reflection host |

Run via `python scripts/check_prerequisites.py docs/plans/memory-distilled-ingest.md`.

## Solution

### Key Elements

- **Provisional insert (synchronous)**: `ingest()` persists a marked provisional
  record with the verbatim content and `PROVISIONAL_INGEST_IMPORTANCE` (default
  3.0, above the 0.15 write-filter floor so the record stays retrievable in the
  pre-distillation window) — cheap, loss-proof, deadline-safe. It also seeds
  `distill_last_attempt_at: 0` so the backfill scan's ascending sort never trips a
  `None`-vs-float `TypeError`.
- **Distillation wrapper (reused machinery)**: a new thin function in
  `agent/memory_extraction.py` that distills a *single human prompt* into a fact
  via the existing `_llm_call`/`MODEL_FAST` plumbing and a pinned prompt.
- **Content-derived importance helper**: `importance = f(source_weight,
  content_value)`, **clamped to `max(result, MEMORY_WF_MIN_THRESHOLD)`** — source
  prior (human>agent) as a multiplier/additive term, content value from the
  distillation step. Constants live in `config/memory_defaults.py`, tunable. The
  floor is not cosmetic: a sub-0.15 result would be silently dropped by
  `WriteFilterMixin` on the partial UPDATE save (see spike-2b).
- **Terminal state + attempt ceiling**: `distill_status` has three states —
  `provisional` → `distilled` (success) or `distill_abandoned` (terminal
  failure). `metadata.distill_attempts` is incremented per attempt and capped at
  `MAX_DISTILL_ATTEMPTS`; a record that hits the cap, or whose re-save returns
  `False`, is transitioned to `distill_abandoned` and never re-scanned. This
  single mechanism resolves the infinite-retry loop (blocker), the poison-pill
  ceiling, and the kill-switch sweep together.
- **Backfill reflection (async cadence)**: `memory_distill_backfill` scans
  provisional records (ascending by last-attempt timestamp), distills them out of
  band, and inspects each `save()` return, mirroring `memory_embedding_backfill`'s
  shape (capped, fail-open, undocumented `os.environ.get` runtime toggle) — but
  **apply-on-by-default**: distillation is the feature's steady state, so the
  toggle `MEMORY_DISTILL_BACKFILL_APPLY` defaults to `true` and acts as an operator
  kill switch, not an opt-in gate (see Update System for the rationale). It also
  exposes the one-off `sweep_provisional_to_abandoned()` for clean teardown.
- **Lift report**: reuse `tools/memory_eval/ingest_quality.py`; commit a
  before/after report segmented by source with pinned prompt + model.

### Flow

Human prompt → `ingest()` persists marked provisional record (verbatim,
provisional importance) → `memory_distill_backfill` reflection picks it up
within ~5 min (300s cadence) → Haiku distills fact + salience → record updated in place (fact
content, computed importance, `distill_status=distilled`, model+prompt recorded)
→ distilled record ranks by content-derived importance on later recall.

### Technical Approach

- **Engine choice (resolved, operator decision, issue comment `5053660188`)**:
  reuse `agent/memory_extraction.py`'s existing extraction machinery. **Do NOT**
  adopt popoto's `ClaudeExtractionProvider`. Rationale: lowest integration risk,
  we own the prompts, no cross-repo dependency on popoto #481/#489.
- **Latency (spike-1)**: distillation runs in the standing reflection subprocess,
  never inline in the hook and never in an in-hook daemon thread (which the
  ephemeral hook process would kill). `ingest()` stays synchronous and cheap.
- **Cadence (reconciled)**: the backfill reflection runs at **300s** (5 min),
  registered as `every: 300s` in `config/reflections.yaml`. **300s matches an
  existing reflection cadence** — `session-liveness-check` already runs at
  `every: 300s`, so this is a supported, exercised interval, not a novel or
  divergent one. It is faster than the `86400s` daily cadence that
  `memory-embedding-backfill` uses (which this plan mirrors in *shape* — cap,
  fail-open, `os.environ.get` toggle — but not in cadence, and not in apply-default;
  see Update System), because ingest freshness is the whole point: a distilled
  memory is only useful once available for recall, so a "distill shortly after
  ingest" cadence is required. The scheduler accepts arbitrary second-granularity
  intervals (see the header legend in `config/reflections.yaml`); the per-run cap
  (`MAX_DISTILL_PER_RUN`) bounds Haiku load so a fast cadence stays cheap. Earlier
  drafts said "~180s / ~3 min"; the plan now commits to a single figure (300s)
  everywhere.
- **Re-save shape (spike-2)**: partial
  `save(update_fields=["content","importance","metadata"])` on UPDATE — skips the
  INSERT-only content gate, re-indexes BM25/bloom/embedding on the fact,
  intentionally re-stamps `relevance` to distillation time.
- **Write-filter floor (spike-2b, revision blocker)**: `WriteFilterMixin`'s
  `_check_write_filter()` runs on **every** save, including partial UPDATEs,
  before the `update_fields` branch (`popoto/models/base.py:1094-1096`), and drops
  any record whose `compute_filter_score()` (= `self.importance`) is
  `< _wf_min_threshold` (0.15). Therefore **both** the provisional-insert
  importance and every computed distilled importance are clamped to
  `max(value, MEMORY_WF_MIN_THRESHOLD)`. The distillation re-save **inspects its
  boolean return**; a `False` never leaves the record silently un-updated — it
  increments the attempt counter and, on drop or cap breach, marks the record
  terminal.
- **Terminal state + retry ceiling**: `distill_status ∈ {provisional, distilled,
  distill_abandoned}`. `metadata.distill_attempts` (int, incremented per attempt)
  is capped at `MAX_DISTILL_ATTEMPTS` (new constant in `config/memory_defaults.py`,
  default 5). The backfill scan filters on `provisional` only and orders ascending
  by `metadata.distill_last_attempt_at` via a defensive
  `key=lambda r: r.metadata.get("distill_last_attempt_at", 0)` (the provisional
  insert also seeds the key to `0`, so no `None` ever reaches `sorted()` — see the
  TypeError guard in Data Flow), so persistently-refusing records sink and
  never starve fresh ones. A record hitting the cap (or a `False` re-save) is
  transitioned to `distill_abandoned` via a metadata-only partial save (guaranteed
  above the floor). A one-off `sweep_provisional_to_abandoned()` drains all
  remaining provisional records to the terminal state when the feature is disabled.
- **Provisional importance**: `PROVISIONAL_INGEST_IMPORTANCE`, a named tunable in
  `config/memory_defaults.py` (default **3.0**) set **deliberately above** the bare
  `MEMORY_WF_MIN_THRESHOLD` 0.15 floor. Flooring the provisional at exactly 0.15
  would be a near-term recall regression: a just-ingested record would rank far
  below the current flat-6.0 during the immediate-follow-up access pattern (the
  human refers back to what they just said before the reflection has distilled it).
  3.0 keeps the record comfortably retrievable in the pre-distillation window while
  still sitting below the settled distilled top band, so it is not mistaken for a
  high-value settled memory. It is NOT the current flat 6.0 verbatim value, and it
  always carries `distill_status=provisional` so it is distinguishable from a
  settled record and excluded from the "no new flat-6.0 verbatim" measurement. A
  rank-band test asserts provisional records remain retrievable via
  `memory_search` during the window before distillation (see Failure Path Test
  Strategy).
- **Model/prompt pinning**: `DISTILL_MODEL = MODEL_FAST`,
  `DISTILL_PROMPT_VERSION = "v1"`, `DISTILL_PROMPT` constant. Recorded per record
  in `metadata` and in the committed report header.
- **Telemetry**: reuse the `{project_key}:memory-gate:{reason}` counter idiom for
  distillation outcomes (`distilled`, `distill_failed`, `distill_refused`) via a
  small counter helper alongside `models/memory_gate.py`.
- **No migration**: all new state rides the existing `metadata` DictField; no
  Popoto model field is added, so `scripts/update/migrations.py` is untouched.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `ingest()` retains its outer `except Exception: pass` (fail-silent by
  contract) — add a test asserting a provisional record is still written on the
  happy path and that a distillation-marker write failure does not crash ingest.
- [ ] The distillation wrapper mirrors `extract_observations_async` fail-open:
  test that a `TimeoutError` / LLM error leaves the record **provisional**
  (retried next reflection run), never crashes, and increments a
  `distill_failed` counter.
- [ ] The backfill reflection catches per-record `save()` failures, logs, and
  continues (mirror `memory_embedding_backfill`) — test one poisoned record does
  not abort the batch.
- [ ] **Write-filter floor (spike-2b):** assert `compute_ingest_importance(...)`
  never returns `< MEMORY_WF_MIN_THRESHOLD`, and that the provisional-insert
  importance is `>= MEMORY_WF_MIN_THRESHOLD`. Add a regression test that a partial
  `save(update_fields=[...])` on a record whose importance was forced below 0.15
  returns `False` (documents the popoto behavior the floor defends against).
- [ ] **Provisional recall rank-band (spike-2b concern):** assert the
  provisional-insert importance is `PROVISIONAL_INGEST_IMPORTANCE` (above the 0.15
  floor) and that a freshly-ingested provisional record is returned by
  `memory_search` during the pre-distillation window — i.e. provisional records
  remain retrievable and do not rank into oblivion before the reflection distills
  them.
- [ ] **Race-1 re-read guard (primary defense):** simulate a record whose
  `distill_status` flips to `distilled` (or `distill_abandoned`) between scan and
  save; assert the reflection re-reads status before the partial save and **skips
  the write** rather than clobbering the already-settled record. This test must
  pass without relying on scheduler single-instance behavior.
- [ ] **Scan-sort TypeError guard (blocker regression):** assert that a scan over
  a mix of fresh provisionals (some seeded `distill_last_attempt_at: 0`, some with
  the key absent to simulate legacy) does not raise `TypeError` — the defensive
  `.get(..., 0)` sort key handles the missing/`None` case, so the backfill run
  completes instead of aborting at setup.
- [ ] **Save-return inspection → terminal:** assert that when the distillation
  re-save returns `False`, the reflection does NOT leave the record silently
  `provisional` — it increments `distill_attempts` and, on cap breach, sets
  `distill_status=distill_abandoned`.
- [ ] **Attempt ceiling:** a record that fails distillation `MAX_DISTILL_ATTEMPTS`
  times is transitioned to `distill_abandoned` and is no longer returned by the
  provisional scan (no infinite retry).
- [ ] **Poison-pill ordering:** given a mix of fresh provisionals and one
  high-attempt provisional, assert the scan orders ascending by
  `distill_last_attempt_at` so fresh records are processed first within the cap.
- [ ] **Kill-switch sweep:** `sweep_provisional_to_abandoned()` transitions every
  remaining `provisional` record to `distill_abandoned` (verbatim content +
  floored importance retained), leaves `distilled` records untouched, and is
  idempotent (a second run is a no-op).

### Empty/Invalid Input Handling
- [ ] Empty / whitespace-only / sub-`MIN_PROMPT_LENGTH` prompts: assert no
  provisional record is written (existing filters unchanged).
- [ ] Haiku returns `NONE`/refusal/empty for a provisional record: assert the
  record's content is left unchanged, `distill_attempts` is incremented, and the
  record stays `provisional` for retry until the attempt cap, at which point it is
  transitioned to terminal `distill_abandoned` — never a silent infinite loop.

### Error State Rendering
- [ ] Distillation is not user-visible; assert failures surface only via
  `distill_failed` counter + DEBUG log, never to the user. Assert the
  `/memories/metrics.json` surface reflects distillation counters.

## Test Impact

- [ ] `tests/**/test_*memory_bridge*` (ingest tests) — UPDATE: assert new
  `metadata.distill_status == "provisional"` and provisional importance instead
  of the old flat `6.0` verbatim expectation. (Builder to grep exact file:
  `grep -rln "def ingest\|memory_bridge" tests/`.)
- [ ] Any test asserting human records are saved at `importance == 6.0` from the
  hook path — UPDATE to the provisional constant.
- [ ] `tests/**/test_*ingest_quality*` — no change to the aggregator; ADD a
  per-source segmentation assertion if not already present.
- [ ] New: `tests/unit/test_memory_distill.py` (distillation wrapper + importance
  helper, including the `MEMORY_WF_MIN_THRESHOLD` floor regression and the
  `PROVISIONAL_INGEST_IMPORTANCE` rank-band retrievability check),
  `tests/unit/test_memory_distill_backfill.py` (reflection: attempt cap → terminal
  `distill_abandoned`, `save()`-return inspection, last-attempt ordering, the
  scan-sort `TypeError` guard on a missing/`0` `distill_last_attempt_at`, the
  Race-1 re-read-before-save guard, `sweep_provisional_to_abandoned` idempotency),
  `tests/integration/` end-to-end provisional→distilled and provisional→abandoned
  transitions.

No existing test asserts the *content* of a verbatim human record beyond
importance/length, so content-rewrite breakage is limited to the importance
expectations above.

## Rabbit Holes

- **Retroactive re-distillation of the existing corpus.** Dropped in recon;
  forward-path only. The 1963 existing agent records and 28 human records stay as
  they are — the backfill reflection only touches records marked
  `distill_status=provisional`, which legacy records never have.
- **Low-latency worker-drained distillation queue.** Tempting (sub-second
  distillation) but adds a Redis queue + worker drain loop. The 5-min (300s)
  reflection cadence satisfies "shortly after" and reuses an existing standing
  subprocess. Do not build a queue this phase.
- **Adopting popoto's `ClaudeExtractionProvider`.** Explicitly out (operator
  decision). Do not partially wire it "to be ready."
- **Tuning the importance formula to chase act-rate.** The aggregate act-rate is
  already 0.990 and agent-dominated; do not over-fit constants to move a
  saturated metric. Measure the **importance-distribution spread** and
  **per-source human act-rate**, not the aggregate.
- **LLM-judged salience scoring.** Prefer mapping the distillation category
  (correction/decision/pattern/surprise, already in `CATEGORY_IMPORTANCE`) to a
  content value over inventing a fresh 0-1 salience score the LLM must calibrate.

## Risks

### Risk 1: Provisional records never get distilled (reflection down / API down)
**Impact:** verbatim flat-importance records accumulate, defeating the feature.
**Mitigation:** the reflection is idempotent and re-scans every run;
transient-failure records stay provisional and retry, but each retry increments
`distill_attempts` and a record hitting `MAX_DISTILL_ATTEMPTS` is transitioned to
terminal `distill_abandoned` so a permanently-refusing record cannot retry
forever or crowd the queue (the scan orders ascending by last-attempt). A
`provisional_count` **and** an `abandoned_count` are surfaced in
`/memories/metrics.json` so both a stuck backlog and a rising abandon rate are
observable. Cap per run to avoid re-saturating Haiku after an outage (mirror
`MAX_BACKFILL_PER_RUN`).

### Risk 1b: A below-floor importance silently voids the distillation write
**Impact:** if `compute_ingest_importance()` returned `< 0.15`, the partial
UPDATE save would be dropped by `WriteFilterMixin` (spike-2b), the record would
stay provisional, and the reflection would re-attempt it forever.
**Mitigation:** importance is floored at `MEMORY_WF_MIN_THRESHOLD` by construction
(both provisional insert and distilled recompute), AND the re-save's boolean
return is inspected so any residual `False` marks the record terminal instead of
looping. Belt and suspenders — the floor prevents the drop, the return-check
bounds it if the floor is ever mis-set.

### Risk 2: The "no new flat-6.0 verbatim" acceptance criterion vs the transient provisional record
**Impact:** a strict reading of AC#1 is violated by the provisional verbatim
record between insert and distillation.
**Mitigation:** provisional records use a **distinct** provisional importance
(not 6.0) and carry `distill_status=provisional`; the measurement of AC#1/AC#2 is
defined over **settled** (`distilled`) records. This interpretation is called out
as Open Question 1 for explicit sign-off.

### Risk 3: Act-rate lift is unmeasurable at merge time
**Impact:** an act-rate comparison cannot show lift immediately — act-rate needs
outcome accrual (≥2 acted/dismissed events per record) over a post-deploy window.
**Mitigation:** the merge-time deliverable is scoped to what is actually
observable at merge — an **importance-distribution snapshot** (histogram spread,
per-source counts, distillation coverage) — and the act-rate comparison is a
**separately-tracked N-day follow-up**, not a merge gate. Success Criteria bullet
5 is split accordingly (5a merge-time snapshot, 5b deferred act-rate follow-up)
so the committed deliverable no longer overstates what merge can prove. Open
Question 2 fixes the window N and whether 5b runs as a scheduled reflection or a
manual report.

### Risk 4: Distillation rewrites away a fact the human will reference verbatim
**Impact:** a distilled "Tom wants X" loses an exact string the user later
searches for.
**Mitigation:** distillation preserves salient tokens (prompt instructs "keep
concrete nouns/paths"); bloom/BM25 re-index on the fact; the provisional verbatim
is only overwritten once a valid distillation returns (refusal/empty leaves
content untouched).

## Race Conditions

### Race 1: Two reflection runs distill the same provisional record
**Location:** `reflections/memory/memory_distill_backfill.py` scan+save loop.
**Trigger:** an overlapping/late reflection run picks a record still marked
provisional while a prior run is mid-distillation.
**Data prerequisite:** the record's `distill_status` must be re-read at save
time.
**State prerequisite:** at most one distillation write wins.
**Mitigation (primary — re-read guard):** immediately before the distilled
partial `save(update_fields=[...])`, the reflection **re-reads the record's
`distill_status` from Redis and skips the write if it is no longer
`"provisional"`** (another run already distilled or abandoned it). This
compare-before-write is the load-bearing defense and does not depend on any
scheduler assumption. Distillation is also **idempotent** — a second distillation
of an already-`distilled` record is a no-op — so even a lost race degrades to a
redundant Haiku call, never a corrupt double-write.
**Secondary (defense in depth):** the reflection scheduler is observed to run a
single instance of a given reflection at a time, and the scan filters on
`distill_status == "provisional"`, which narrows the window further. This is a
belt, not the buckle — the plan does not rely on it, because it was not spike-
verified; the re-read guard holds regardless.

### Race 2: `ingest()` provisional insert vs. the same-content bloom dedup
**Location:** `memory_bridge.py::ingest` bloom check (lines 788-798).
**Trigger:** rapid duplicate prompts within one session.
**Data prerequisite:** bloom fingerprints on `content`; the provisional record's
content is still the verbatim utterance at insert time, so existing dedup
behavior is unchanged.
**Mitigation:** none needed — provisional insert keeps verbatim content, so the
existing bloom dedup semantics hold exactly. Distillation changes content only on
UPDATE, after the dedup decision is already made.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2203] Outcome-loop strengthening and existing-fragment pruning
  (subconscious-memory Phase 4) — the pruning of legacy verbatim/fragment records
  is Phase 4's job, tracked in #2203.
- Retroactive re-distillation of the existing corpus — forward-path only this
  phase (dropped in recon). If wanted later, it is a separate backfill reflection
  pass over unmarked legacy records; not filed as a distinct issue because it is
  explicitly a possible future follow-up, not committed work.
- Adopting popoto's `ClaudeExtractionProvider` at the substrate — resolved out by
  operator decision; remains a possible future arm of popoto #489.

## Update System

- **Reflection registry**: register `memory-distill-backfill` in
  `config/reflections.yaml` (new block modeled on `memory-embedding-backfill`,
  but `every: 300s` — the same cadence `session-liveness-check` already uses; see
  the cadence note in Technical Approach for why the faster interval is required)
  and add its `run` import to
  `reflections/memory_management.py`. The reflection scheduler subprocess
  (`com.valor.reflection-worker`) picks it up on reload.
- **Worktree gotcha**: `config/reflections.yaml` is a gitignored symlink in fresh
  worktrees — builders running the full suite in a worktree must ensure the
  symlink exists (known issue) before reflection tests pass.
- **No new dependencies, secrets, or documented config keys.** The Anthropic key
  is already present. No `/update` script changes and no migration — all new state
  uses the existing `metadata` DictField.
- **Apply mode defaults to `true` (resolves the "fully automatic" AC vs. apply-gate
  contradiction).** Unlike `memory-embedding-backfill` — a one-off remediation of a
  historical drop bug (#1904) that ships dry-run so an operator opts into the
  backfill — distillation is this feature's **steady-state operating mode**. Shipping
  it dry-run by default would make the feature inert, directly contradicting
  Success Criterion "zero manual steps / fully automatic." Therefore the reflection
  **applies by default**: the runtime toggle `MEMORY_DISTILL_BACKFILL_APPLY`
  (`os.environ.get(..., "true")`) is an operator **kill switch** (set to
  `false`/`0`/`no` to force dry-run), not an opt-in gate. It is still deliberately
  undocumented in `.env.example` / `config/settings.py`: it is an operator-only
  runtime override, not a deployed configuration value, so it needs no `.env` key,
  no `config/settings.py` field, and no completeness-check entry. This means the
  write path is fully automatic on merge with **no post-merge manual apply step**.
  (The repo-wide `data/catchup-disabled` and per-reflection registry `enabled:
  false` remain available as coarser off-switches.)

## Agent Integration

No new agent-facing tool/MCP surface is required for the write path — distillation
is entirely internal (hook ingest + reflection). The agent already reads distilled
memories through the existing recall path (`memory_bridge.py::prefetch`,
`mcp__memory__memory_search`/`memory_get`), which needs no change.

- Report generation reuses the existing `tools/memory_eval` module; if a CLI
  entry point is convenient, expose it under the existing eval tooling rather than
  a new `pyproject.toml` script.
- Integration test: assert an ingested human prompt becomes a provisional record,
  then a simulated reflection run distills it (content rewritten, importance
  spread, `distill_status=distilled`, model+prompt recorded).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — add a "Distilled human
  ingest (Phase 3)" subsection: provisional-insert + backfill-reflection shape,
  importance formula, pinned model/prompt, and the persist-now/distill-later
  precedent link to embedding backfill.
- [ ] Add/refresh the reflection entry so `docs/features/README.md` (or the
  reflections doc) lists `memory-distill-backfill`.

### Inline Documentation
- [ ] Docstring on the distillation wrapper (pinned model/prompt, fail-open
  contract) and the importance helper (formula + constant rationale).
- [ ] Module docstring on `memory_distill_backfill.py` matching the
  `memory_embedding_backfill.py` house style (cadence, failure modes, apply gate).

## Success Criteria

- [ ] Live-ingested human content is stored as a marked provisional record and
  distilled to a fact out of band; no new settled flat-6.0 verbatim records from
  the hook path.
- [ ] Importance on settled (`distilled`) records varies with content — the
  importance histogram shows spread, not a single 6.0 spike — while the
  human>agent source prior is preserved as a factor.
- [ ] The 8s hook deadline is never violated: `ingest()` performs no LLM call;
  distillation runs only in the reflection subprocess (asserted by test +
  code-path grep).
- [ ] Distillation model + prompt are pinned (`DISTILL_MODEL`,
  `DISTILL_PROMPT_VERSION`) and recorded per record and in the committed report.
- [ ] Provisional records reach a terminal state: every provisional record is
  eventually `distilled` or `distill_abandoned` (attempt-capped), and
  `distill_abandoned` records are never re-scanned — no infinite retry loop
  (asserted by test). A below-floor computed importance cannot silently void the
  distillation write (importance floored at `MEMORY_WF_MIN_THRESHOLD`, `save()`
  return inspected).
- [ ] **(5a — merge-time)** A committed report captures the post-change
  **importance distribution** (histogram spread, per-source counts, distillation
  coverage) versus the Phase 1 baseline, with the measurement window and pinned
  prompt/model in its header. This is the merge deliverable.
- [ ] **(5b — deferred follow-up)** A per-source **act-rate comparison** against
  the Phase 1 baseline is produced after an N-day accrual window (Open Question 2;
  scheduled reflection or manual report). This is explicitly NOT a merge gate —
  act-rate needs post-deploy outcome accrual.
- [ ] **Recall-quality spot-check:** for at least one concrete example prompt
  (e.g. "Rewrite justfile in a way"), the settled distilled record reads as a
  standalone fact (e.g. "Tom wants the justfile rewritten") and is retrievable via
  `memory_search` — verifying the distillation actually improves recall, not just
  the importance histogram.
- [ ] Zero manual steps in the write path (provisional insert + scheduled
  reflection are fully automatic). The reflection's apply mode defaults to `true`
  (kill-switch semantics, not opt-in), so no post-merge apply step is required —
  the feature is live on merge.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

The lead orchestrates; it never builds directly.

### Team Members

- **Builder (ingest+importance)**
  - Name: `distill-core-builder`
  - Role: provisional-insert marker in `ingest()`, importance helper + constants,
    distillation wrapper in `agent/memory_extraction.py`.
  - Agent Type: builder
  - Domain: async (network LLM call, fail-open) — paste async framing.
  - Resume: true

- **Builder (reflection+telemetry)**
  - Name: `distill-reflection-builder`
  - Role: `memory_distill_backfill.py`, registry wiring, `/memories/metrics.json`
    distillation counters + `provisional_count` surface.
  - Agent Type: builder
  - Domain: Redis/Popoto — paste data framing.
  - Resume: true

- **Builder (report)**
  - Name: `distill-report-builder`
  - Role: reuse `ingest_quality.py`; produce the committed before/after report
    with per-source segmentation and pinned header.
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: `distill-validator`
  - Role: verify all success criteria + Verification rows.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `distill-docs`
  - Role: feature + inline docs.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Distillation core (ingest marker + importance + wrapper)
- **Task ID**: build-core
- **Depends On**: none
- **Validates**: `tests/unit/test_memory_distill.py` (create), updated
  memory_bridge ingest tests
- **Informed By**: spike-1 (no in-hook daemon thread), spike-2 (partial UPDATE
  save)
- **Assigned To**: distill-core-builder
- **Agent Type**: builder
- **Parallel**: true
- Add provisional-marker write + provisional importance constant to
  `ingest()` (`memory_bridge.py:813-819`), writing
  `metadata={"distill_status":"provisional","distill_attempts":0,"distill_last_attempt_at":0}`
  (the `distill_last_attempt_at:0` seed is mandatory — it keeps the backfill scan's
  ascending sort from hitting a `None`-vs-float `TypeError` on fresh provisionals;
  see Data Flow), preserving all existing filters and fail-silent. Provisional
  importance is `PROVISIONAL_INGEST_IMPORTANCE` (new tunable in
  `config/memory_defaults.py`, default 3.0 — comfortably **above** the bare
  `MEMORY_WF_MIN_THRESHOLD` 0.15 floor so provisional records stay retrievable
  before distillation, and above `MIN_IMPORTANCE_FLOOR` 0.2).
- Add `distill_human_prompt_async` to `agent/memory_extraction.py` reusing
  `_llm_call`/`MODEL_FAST`, pinned `DISTILL_PROMPT` + `DISTILL_PROMPT_VERSION`,
  fail-open on timeout/refusal/empty.
- Add `compute_ingest_importance(source_weight, content_value)` helper that
  **clamps its result to `max(result, MEMORY_WF_MIN_THRESHOLD)`** +
  tunable constants (`MAX_DISTILL_ATTEMPTS` default 5,
  `PROVISIONAL_INGEST_IMPORTANCE` default 3.0) in `config/memory_defaults.py`. Add
  a unit test asserting the helper never returns below the floor (spike-2b
  regression guard) and a rank-band test asserting a provisional record at
  `PROVISIONAL_INGEST_IMPORTANCE` remains retrievable via `memory_search` before
  distillation.

### 2. Backfill reflection + telemetry
- **Task ID**: build-reflection
- **Depends On**: build-core
- **Validates**: `tests/unit/test_memory_distill_backfill.py` (create),
  `tests/integration/` provisional→distilled transition
- **Assigned To**: distill-reflection-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/memory/memory_distill_backfill.py`:
  - **Apply-on by default**: runtime toggle
    `os.environ.get("MEMORY_DISTILL_BACKFILL_APPLY", "true")` (kill switch, not
    opt-in gate — distillation is the steady state; see Update System),
    undocumented in `.env`/`config.settings`. Structurally mirrors
    `memory_embedding_backfill.py:63` but inverts the default.
  - Scan `distill_status == "provisional"` **ordered ascending by
    `metadata.distill_last_attempt_at` using
    `key=lambda r: r.metadata.get("distill_last_attempt_at", 0)`** (defensive
    against a missing/`None` key — a `None`-vs-float compare aborts the whole scan
    with `TypeError` before the per-record `try/except`), `MAX_DISTILL_PER_RUN`
    cap.
  - Per record: increment `distill_attempts` + stamp `distill_last_attempt_at`;
    if attempts exceed `MAX_DISTILL_ATTEMPTS`, transition to `distill_abandoned`
    (metadata-only save) instead of distilling.
  - On distill: **re-read `distill_status` from Redis immediately before the write
    and skip if it is no longer `"provisional"`** (primary Race-1 guard, not a
    scheduler assumption), then partial
    `save(update_fields=["content","importance","metadata"])` and **inspect the
    boolean return** — `False` → increment attempts / mark terminal on cap breach,
    never leave silently un-updated. Fail-open per record.
  - Add `sweep_provisional_to_abandoned()` + a `--sweep-abandon` CLI entry for
    clean teardown (idempotent).
- Register in `config/reflections.yaml` (`every: 300s`, see cadence note) and
  import `run` in `reflections/memory_management.py`.
- Add `distilled`/`distill_failed`/`distill_refused`/`distill_abandoned` counters
  and `provisional_count` + `abandoned_count` gauges to the
  `/memories/metrics.json` surface.

### 3. Lift report
- **Task ID**: build-report
- **Depends On**: build-core, build-reflection
- **Validates**: report artifact committed; `ingest_quality` per-source assertion
- **Assigned To**: distill-report-builder
- **Agent Type**: builder
- **Parallel**: false
- Generate `docs/baselines/memory-distilled-ingest-report.md` (+ `.json`) via
  `tools/memory_eval/ingest_quality.py`, segmented by source, with pinned
  model/prompt + measurement-window header, comparing importance distribution to
  the Phase 1 baseline.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-core, build-reflection, build-report
- **Assigned To**: distill-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md` (Phase 3 subsection) and the
  reflections index; add docstrings.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-core, build-reflection, build-report, document-feature
- **Assigned To**: distill-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm every Success Criterion; generate report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q -k "distill or memory_bridge or ingest_quality"` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No inline LLM call in ingest | `grep -n "_llm_call\|messages.create\|anthropic" .claude/hooks/hook_utils/memory_bridge.py` | match count == 0 |
| Provisional marker written | `grep -c "distill_status" .claude/hooks/hook_utils/memory_bridge.py` | output > 0 |
| Reflection registered | `grep -c "memory-distill-backfill" config/reflections.yaml` | output > 0 |
| Importance floored | `grep -c "MEMORY_WF_MIN_THRESHOLD" config/memory_defaults.py agent/memory_extraction.py` | output > 0 |
| Provisional importance above floor | `grep -c "PROVISIONAL_INGEST_IMPORTANCE" config/memory_defaults.py .claude/hooks/hook_utils/memory_bridge.py` | output > 0 |
| Last-attempt seed present | `grep -c "distill_last_attempt_at" .claude/hooks/hook_utils/memory_bridge.py` | output > 0 |
| Scan-sort defensive key | `grep -c "distill_last_attempt_at\", 0\|get(\"distill_last_attempt_at\"" reflections/memory/memory_distill_backfill.py` | output > 0 |
| Apply-on by default | `grep -c "MEMORY_DISTILL_BACKFILL_APPLY\", \"true\"" reflections/memory/memory_distill_backfill.py` | output > 0 |
| Race-1 re-read guard | `grep -c "distill_status" reflections/memory/memory_distill_backfill.py` | output > 0 |
| Terminal state + cap present | `grep -c "distill_abandoned\|MAX_DISTILL_ATTEMPTS" reflections/memory/memory_distill_backfill.py` | output > 0 |
| Kill-switch sweep present | `grep -c "sweep_provisional_to_abandoned" reflections/memory/memory_distill_backfill.py` | output > 0 |
| Reflection callable wired | `python -m reflections --dry-run` | exit code 0 |
| Model/prompt pinned | `grep -c "DISTILL_PROMPT_VERSION\|DISTILL_MODEL" agent/memory_extraction.py` | output > 0 |
| No popoto provider adopted | `grep -rn "ClaudeExtractionProvider" agent/ models/ reflections/ tools/` | exit code 1 |
| Report committed | `test -f docs/baselines/memory-distilled-ingest-report.md && echo ok` | output contains ok |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | critique r1 | Partial distillation save missing `WriteFilterMixin` importance floor (0.15) → below-floor write silently dropped, record stuck provisional, infinite retry | spike-2b, Technical Approach (write-filter floor), Risk 1b, tasks 1-2 | Floor importance ≥ `MEMORY_WF_MIN_THRESHOLD` on provisional insert AND distilled recompute; inspect `save()` return; terminal `distill_abandoned` state |
| CONCERN | critique r1 | No retry ceiling on permanently-refusing records; poison pills crowd the queue | Terminal state + `MAX_DISTILL_ATTEMPTS`; scan ordered ascending by `distill_last_attempt_at` | `metadata.distill_attempts` capped; `abandoned_count` gauge surfaced |
| CONCERN | critique r1 | Disable/kill-switch strands in-flight provisional records | `sweep_provisional_to_abandoned()` one-off drain; Reversibility note | `--sweep-abandon` CLI, idempotent, verbatim + floored importance retained |
| CONCERN | critique r1 | Success Criteria bullet 5 overstates merge-time act-rate deliverable | SC split into 5a (merge-time importance snapshot) + 5b (deferred N-day act-rate follow-up); Risk 3 rewritten | act-rate is not a merge gate |
| CONCERN | critique r1 | "No new .env keys" contradicts Task 2's apply-gated env var | Update System reworded | Apply gate is undocumented runtime `os.environ.get(MEMORY_DISTILL_BACKFILL_APPLY)`, not a config key |
| NIT | critique r1 | Add example-based recall-quality check; reconcile 180s vs 86400s cadence | SC recall-quality spot-check bullet; cadence committed to 300s with divergence note | — |
| BLOCKER | critique r2 | Provisional insert omits `distill_last_attempt_at`; scan sorts ascending on it → `None`-vs-float `TypeError` at scan setup (outside per-record try/except) aborts every backfill run | Data Flow steps 2-3, Technical Approach, tasks build-core/build-reflection; new scan-sort TypeError test | Seed `distill_last_attempt_at: 0` in provisional metadata AND defensive `key=lambda r: r.metadata.get("distill_last_attempt_at", 0)` in scan sort |
| CONCERN | critique r2 | Provisional importance at exactly 0.15 floor is a near-term recall regression | New `PROVISIONAL_INGEST_IMPORTANCE` tunable (default 3.0, above floor); rank-band retrievability test | Provisional records stay retrievable in the pre-distillation window |
| CONCERN | critique r2 | "Zero manual steps / fully automatic" AC contradicts dry-run apply gate → feature ships inert | Apply mode defaults to `true` (kill-switch, not opt-in); Update System + SC reworded | No post-merge apply step; `MEMORY_DISTILL_BACKFILL_APPLY` defaults on |
| CONCERN | critique r2 | Race 1 leans on unspiked scheduler single-instance assumption | Re-read `distill_status` immediately before the distilled save (skip if not provisional) is now the PRIMARY guard; scheduler note demoted to secondary; new Race-1 re-read test | Compare-before-write holds regardless of scheduler behavior |
| CONCERN | critique r2 | Prior Art omits #1904, the originating issue for the copied `memory_embedding_backfill.py` pattern | Prior Art #1904 entry added | Persist-now/backfill-later precedent credited |
| NIT | critique r2 | Cadence framing "divergence from the daily norm" inaccurate — `session-liveness-check` already runs at 300s | Cadence note + registry note reworded | 300s matches an existing exercised reflection cadence |

---

## Open Questions

1. **Provisional-record interpretation of AC#1/AC#2.** The persist-now design
   writes a transient verbatim provisional record (distinct provisional
   importance, marked `distill_status=provisional`) before distillation. Confirm
   the acceptance criteria ("no new flat-6.0 verbatim records", "importance shows
   spread") are judged over **settled (`distilled`) records**, not the transient
   provisional state. (Plan assumes yes.)
2. **Measurement window for the act-rate report.** Act-rate lift needs outcome
   accrual (≥2 events/record) over a post-deploy window; it is not observable at
   merge. Proposal: commit a methodology + interim importance-distribution snapshot
   at merge, then a follow-up act-rate comparison after N days (default 14, per the
   SDLC reflection lookback). Confirm N and whether the follow-up is a scheduled
   reflection or a manual report.
3. **Importance content-value source.** Map the distillation category
   (correction/decision/pattern/surprise → existing `CATEGORY_IMPORTANCE` bands)
   to content value, or have the LLM emit a 0-1 salience score? Plan prefers the
   category mapping (reuses a calibrated table, less LLM-calibration risk).
