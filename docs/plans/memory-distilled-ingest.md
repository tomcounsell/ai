---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2202
last_comment_id: 5053660188
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

## Data Flow

1. **Entry point**: Human prompt arrives at `UserPromptSubmit` hook
   (`.claude/hooks/user_prompt_submit.py`), wrapped in the 8s SIGALRM deadline.
2. **Synchronous provisional insert** (`memory_bridge.py::ingest`): existing
   length/trivial/bloom filters run, then `Memory.safe_save(...)` persists a
   record with `content=verbatim[:500]`, `importance=<provisional>`,
   `source=SOURCE_HUMAN`, and `metadata={"distill_status": "provisional"}`.
   Cheap, no LLM — well within the deadline. Nothing is lost.
3. **Out-of-band distillation** (`reflections/memory/memory_distill_backfill.py`,
   scheduled every ~180s): queries non-superseded records with
   `metadata.distill_status == "provisional"`, capped per run. For each, calls
   `agent/memory_extraction.py::distill_human_prompt_async` (new, thin wrapper
   over the existing `_llm_call` + `MODEL_FAST`) with the pinned distillation
   prompt.
4. **Content-derived importance + rewrite**: the distillation returns
   `{fact, salience_or_category}`. Importance is recomputed via
   `f(source_weight, content_value)` (new helper). The record is updated with
   `content=fact`, `importance=<computed>`,
   `metadata={distill_status:"distilled", distill_model, distill_prompt_version, ...}`
   via a partial `save(update_fields=[...])`. BM25/bloom/embedding re-index on the
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
- **Reversibility**: high. Disable the reflection (registry `enabled: false`) and
  drop the provisional-marker write; existing distilled records remain valid.

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
  record with the verbatim content and a defined provisional importance — cheap,
  loss-proof, deadline-safe.
- **Distillation wrapper (reused machinery)**: a new thin function in
  `agent/memory_extraction.py` that distills a *single human prompt* into a fact
  via the existing `_llm_call`/`MODEL_FAST` plumbing and a pinned prompt.
- **Content-derived importance helper**: `importance = f(source_weight,
  content_value)` — source prior (human>agent) as a multiplier/additive term,
  content value from the distillation step. Constants live in
  `config/memory_defaults.py`, tunable.
- **Backfill reflection (async cadence)**: `memory_distill_backfill` scans
  provisional records and distills them out of band, mirroring
  `memory_embedding_backfill` (dry-run default, apply-gated, capped, fail-open).
- **Lift report**: reuse `tools/memory_eval/ingest_quality.py`; commit a
  before/after report segmented by source with pinned prompt + model.

### Flow

Human prompt → `ingest()` persists marked provisional record (verbatim,
provisional importance) → `memory_distill_backfill` reflection picks it up
within ~3 min → Haiku distills fact + salience → record updated in place (fact
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
- **Re-save shape (spike-2)**: partial
  `save(update_fields=["content","importance","metadata"])` on UPDATE — skips the
  INSERT-only content gate, re-indexes BM25/bloom/embedding on the fact,
  intentionally re-stamps `relevance` to distillation time.
- **Provisional importance**: a defined constant (e.g. the human source prior
  alone), NOT the current flat 6.0 verbatim value, and always carrying
  `distill_status=provisional` so it is distinguishable from a settled record and
  excluded from the "no new flat-6.0 verbatim" measurement.
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

### Empty/Invalid Input Handling
- [ ] Empty / whitespace-only / sub-`MIN_PROMPT_LENGTH` prompts: assert no
  provisional record is written (existing filters unchanged).
- [ ] Haiku returns `NONE`/refusal/empty for a provisional record: assert the
  record stays provisional (or is marked `distill_skipped`), content unchanged,
  no silent loop.

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
  helper), `tests/unit/test_memory_distill_backfill.py` (reflection),
  `tests/integration/` end-to-end provisional→distilled transition.

No existing test asserts the *content* of a verbatim human record beyond
importance/length, so content-rewrite breakage is limited to the importance
expectations above.

## Rabbit Holes

- **Retroactive re-distillation of the existing corpus.** Dropped in recon;
  forward-path only. The 1963 existing agent records and 28 human records stay as
  they are — the backfill reflection only touches records marked
  `distill_status=provisional`, which legacy records never have.
- **Low-latency worker-drained distillation queue.** Tempting (sub-second
  distillation) but adds a Redis queue + worker drain loop. The ~3-min reflection
  cadence satisfies "shortly after" and reuses an existing standing subprocess.
  Do not build a queue this phase.
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
`distill_failed` records stay provisional and retry. A `provisional_count` is
surfaced in `/memories/metrics.json` so a stuck backlog is observable. Cap per
run to avoid re-saturating Haiku after an outage (mirror `MAX_BACKFILL_PER_RUN`).

### Risk 2: The "no new flat-6.0 verbatim" acceptance criterion vs the transient provisional record
**Impact:** a strict reading of AC#1 is violated by the provisional verbatim
record between insert and distillation.
**Mitigation:** provisional records use a **distinct** provisional importance
(not 6.0) and carry `distill_status=provisional`; the measurement of AC#1/AC#2 is
defined over **settled** (`distilled`) records. This interpretation is called out
as Open Question 1 for explicit sign-off.

### Risk 3: Act-rate lift is unmeasurable at merge time
**Impact:** AC#5 ("aggregate act rate compared against Phase 1 baseline in a
committed report") cannot show lift immediately — act-rate needs outcome accrual
(≥2 acted/dismissed events per record) over a post-deploy window.
**Mitigation:** commit at merge a **methodology + interim distribution snapshot**
(importance histogram spread, per-source counts, distillation coverage) plus a
scheduled follow-up act-rate comparison after a defined accrual window. Open
Question 2 fixes the window.

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
**Mitigation:** the reflection scheduler runs a single reflection instance at a
time (no concurrent runs of the same reflection), and distillation is
**idempotent** — a second distillation of an already-`distilled` record is a
no-op (the scan filters on `distill_status == "provisional"`). Cap + single-run
scheduler eliminate the practical race; re-reading status before the partial save
is the belt-and-suspenders guard.

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
  `config/reflections.yaml` (new block modeled on `memory-embedding-backfill`) and
  add its `run` import to `reflections/memory_management.py`. The reflection
  scheduler subprocess (`com.valor.reflection-worker`) picks it up on reload.
- **Worktree gotcha**: `config/reflections.yaml` is a gitignored symlink in fresh
  worktrees — builders running the full suite in a worktree must ensure the
  symlink exists (known issue) before reflection tests pass.
- **No new dependencies, secrets, or `.env` keys.** The Anthropic key is already
  present. No `/update` script changes and no migration — all new state uses the
  existing `metadata` DictField.

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
- [ ] A committed report compares post-change importance distribution and
  per-source act-rate against the Phase 1 baseline, with the measurement window
  and pinned prompt/model in its header.
- [ ] Zero manual steps in the write path (provisional insert + scheduled
  reflection are fully automatic).
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
- Add provisional-marker write + provisional importance constant to `ingest()`
  (`memory_bridge.py:813-819`), preserving all existing filters and fail-silent.
- Add `distill_human_prompt_async` to `agent/memory_extraction.py` reusing
  `_llm_call`/`MODEL_FAST`, pinned `DISTILL_PROMPT` + `DISTILL_PROMPT_VERSION`,
  fail-open on timeout/refusal/empty.
- Add `compute_ingest_importance(source_weight, content_value)` helper +
  tunable constants in `config/memory_defaults.py`.

### 2. Backfill reflection + telemetry
- **Task ID**: build-reflection
- **Depends On**: build-core
- **Validates**: `tests/unit/test_memory_distill_backfill.py` (create),
  `tests/integration/` provisional→distilled transition
- **Assigned To**: distill-reflection-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/memory/memory_distill_backfill.py` (dry-run default,
  apply-gated env var, `MAX_DISTILL_PER_RUN` cap, fail-open per record, partial
  `save(update_fields=["content","importance","metadata"])`).
- Register in `config/reflections.yaml` (~180s cadence) and import `run` in
  `reflections/memory_management.py`.
- Add `distilled`/`distill_failed`/`distill_refused` counters and a
  `provisional_count` gauge to the `/memories/metrics.json` surface.

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
| Reflection callable wired | `python -m reflections --dry-run` | exit code 0 |
| Model/prompt pinned | `grep -c "DISTILL_PROMPT_VERSION\|DISTILL_MODEL" agent/memory_extraction.py` | output > 0 |
| No popoto provider adopted | `grep -rn "ClaudeExtractionProvider" agent/ models/ reflections/ tools/` | exit code 1 |
| Report committed | `test -f docs/baselines/memory-distilled-ingest-report.md && echo ok` | output contains ok |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

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
