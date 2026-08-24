---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-04-18
revised: 2026-04-18 (second pass)
revision_applied: true
tracking: https://github.com/tomcounsell/ai/issues/1040
last_comment_id:
---

# SDLC Router Oscillation Guard

## Problem

On a continuous run of `/sdlc` for issue #1036 / PR #1039, the router failed to
monotonically advance through the pipeline. It dispatched `/do-plan-critique`
three times in a row when the verdict was `NEEDS REVISION` (the correct next
dispatch was `/do-plan`). It produced three different verdicts on three
consecutive `/do-pr-review` runs against the unchanged PR #1039 (APPROVED, then
2 tech-debt findings, then 1 blocker). And after PR #1039 was open, it
regressed and dispatched `/do-plan-critique` on a plan that was no longer the
active artifact. The user had to break the cycle manually with `/do-patch`.

**Current behavior:**

The dispatch table in `.claude/skills/sdlc/SKILL.md` is a natural-language
decision tree driven by conversation context. Three concrete gaps:

1. **No verdict -> dispatch mapping**: The dispatch table does not consume the
   most recent critique/review verdict as structured input. It relies on the
   orchestrator LLM to re-read conversation tail and guess which row applies.
   When the critique verdict is `NEEDS REVISION`, there is no hard rule
   forbidding another `/do-plan-critique` — the LLM chose it anyway.
2. **No artifact-hash guard**: `/do-plan-critique` and `/do-pr-review` can run
   against an unchanged artifact (plan content, PR diff) and return a different
   verdict every time. The router has no signal for "critique already ran on
   this exact plan hash — skip the re-run or use the cached verdict."
3. **No PR-existence guard**: Once PR #N is open and attached to the issue,
   dispatching `/do-plan-critique` or `/do-plan` is structurally illegal
   (the plan is frozen, the artifact is the PR). The dispatch table has no
   explicit row that says "PR exists -> critique/plan are not legal targets."

The cycle counter in `agent/pipeline_state.py` (`_critique_cycle_count`,
`MAX_CRITIQUE_CYCLES = 2`) exists but is **not surfaced to the SDLC router**.
`sdlc_stage_query` does not return it. So the router cannot know "we've hit the
critique-cycle ceiling, escalate to human."

**Desired outcome:**

- `/sdlc` invoked after a `NEEDS REVISION` or `MAJOR REWORK` critique verdict
  dispatches `/do-plan` next, never `/do-plan-critique`.
- `/sdlc` invoked while an open PR exists for the current issue dispatches
  `/do-pr-review`, `/do-patch`, `/do-docs`, or `/do-merge` — never
  `/do-plan` or `/do-plan-critique`.
- Non-deterministic verdicts on unchanged artifacts are either
  (a) cached against an artifact hash and reused on the next invocation,
  or (b) capped via a same-stage dispatch counter that escalates to human
  after N consecutive dispatches without state change.
- A regression test replays the 12-step dispatch sequence from #1036 and
  asserts the router terminates in `merged` or a legitimate terminal
  `blocked` state.

## Freshness Check

**Baseline commit:** 350df702d0648a4036913ba60b6cb551bc6ef7c0
**Issue filed at:** 2026-04-18T06:34:18Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/skills/sdlc/SKILL.md` — dispatch table lines 141-162 — still holds,
  no commits on this file since the issue was filed.
- `.claude/skills/do-plan-critique/SKILL.md` — verdict strings on lines 233-236
  and 245-248 — still holds, verdict shape `READY TO BUILD (no concerns)` /
  `READY TO BUILD (with concerns)` / `NEEDS REVISION` / `MAJOR REWORK`.
- `.claude/skills/do-pr-review/SKILL.md` — structured `<!-- OUTCOME {...} -->`
  blocks already emitted on lines 425-435 (success / partial / fail) — still
  holds.
- `tools/sdlc_stage_marker.py` — still holds, resolves session via
  `--issue-number` / env vars / `--session-id`.
- `tools/sdlc_stage_query.py` — still holds, returns stage statuses but
  drops the `_patch_cycle_count` and `_critique_cycle_count` metadata keys
  (see `_get_stage_states` filtering at line 87 — filters to `ALL_STAGES` only).
- `agent/pipeline_graph.py` and `agent/pipeline_state.py` — still hold.
  `MAX_CRITIQUE_CYCLES = 2` at line 35, `classify_outcome()` parses verdicts
  from output tail at line 492.

**Cited sibling issues/PRs re-checked:**
- #704 — closed 2026-04-05, established stage_states as source of truth.
- #729 — closed 2026-04-06, killed artifact inference. Constraint for this plan.
- #941 — closed 2026-04-14, fixed local SDLC stage tracking. `sdlc_stage_marker`
  and `sdlc_session_ensure` are the outputs.
- #1005 — closed 2026-04-16, fixed PM exiting before merge gate.
- #1007 — closed 2026-04-16, PM persona hardening.
- PR #815 — merged 2026-04-07, introduced the concern-triggered revision pass
  and `revision_applied` frontmatter flag.
- PR #722 — merged 2026-04-05, wired SDLC router to read stage_states as primary
  signal.
- PR #951 — merged 2026-04-14, local SDLC pipeline state tracking.

**Commits on main since issue was filed (touching referenced files):** None.

**Active plans in `docs/plans/` overlapping this area:**
- `sdlc-redesign.md`, `sdlc-plan-critique-revision.md`,
  `sdlc-stage-skip-prevention.md`, `pm-skips-critique-and-review.md`,
  `observer_sdlc_template_bypass.md` — all exist under `docs/plans/` (not
  `completed/`). Only `sdlc-plan-critique-revision.md` touches the same
  dispatch-table area; it introduced `revision_applied` and the Row 4b/4c
  logic. This plan builds on that mechanism without conflict. The others
  address different failure modes (stage skipping, template bypass,
  missing critique/review) — complementary, not overlapping.

**Notes:** No drift. All file:line references in the issue still hold.

## Prior Art

- **#704** (closed 2026-04-05) — `SDLC router must use PipelineStateMachine for
  stage tracking instead of artifact inference`. Migrated router to read
  `stage_states` as the source of truth. This plan preserves that constraint
  and hardens the dispatch table on top of it. PR #722.
- **#729** (closed 2026-04-06) — `SDLC router skips stages by inferring
  completion from artifacts`. Addressed the inverse failure mode (skipping).
  This issue is the complement (repeating).
- **#941** (closed 2026-04-14) — `Local /sdlc sessions have no pipeline state
  tracking`. Fixed by `sdlc_session_ensure` and `sdlc_stage_marker`. Relevant
  because verdicts from critique/review must land in `stage_states` and
  persist across local invocations. PR #951.
- **#779** (closed 2026-04-07) — `SDLC skill gaps: missing propagation check,
  shallow critique findings, no revision pass before build`. PR #815
  introduced the concern-triggered revision pass. This plan extends the
  verdict-reading logic further.
- **#1005** (closed 2026-04-16) — `PM session completes before merge gate — PR
  left open and unmerged`. Similar class of routing failure; solved by PM
  persona guards. This plan adds structural router guards.
- **#1007** (closed 2026-04-16) — `PM persona needs self-monitoring and
  pipeline completion guards`. Addressed PM-level drift. This plan addresses
  the adjacent router-level drift.

## Research

No relevant external findings — proceeding with codebase context. The fix is
purely internal: hardening a dispatch table and a CLI tool already owned by
this repo.

## Why Previous Fixes Failed

The prior fixes were not wrong — they were **partial**. Each one moved the
right direction but left gaps that this plan closes:

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|------------------------|
| PR #722 (issue #704) | Wired router to read `stage_states` | Router reads stage statuses, but does NOT read the most recent verdict or the cycle counters. So it can see "CRITIQUE = failed" but not "critique has already run twice — escalate." |
| PR #815 (issue #779) | Introduced `revision_applied` flag and Row 4b/4c | Added one specific transition (concerns -> revision -> build), but did not generalize the pattern. Other critique verdicts (`NEEDS REVISION`, `MAJOR REWORK`) still rely on natural-language inference by the router LLM. |
| PR #951 (issue #941) | Local session `stage_states` tracking | State now persists across local `/sdlc` invocations, but the router still doesn't use the OUTCOME contract from dev-session output. Verdicts float in conversation context, not in state. |

**Root cause pattern:** The router infers what to do next from conversation
context (the LLM re-reads the tail and guesses), rather than consuming a
structured signal (verdict + cycle count + PR existence flag) that makes
the dispatch decision deterministic. When the LLM's guess is wrong, the
pipeline loops. This plan makes the signal structured.

## Architectural Impact

- **New dependencies**: None. All new logic is in existing tools (`tools/sdlc_stage_query.py`,
  `.claude/skills/sdlc/SKILL.md`) or a new narrow CLI tool (`tools/sdlc_verdict.py`).
- **Interface changes**:
  - `sdlc_stage_query` returns a richer JSON payload (stages + cycle counters
    + latest verdict + PR number). Backward compatible: old callers can
    ignore the new keys.
  - New `sdlc_verdict` CLI: records/reads the most recent critique or review
    verdict on the PM session.
  - SDLC SKILL.md dispatch table gains a "Legal Dispatch Guards" section that
    the router MUST consult before picking a row.
- **Coupling**: Decreases coupling between router and conversation context;
  increases coupling between router and `stage_states` payload (already the
  established source of truth).
- **Data ownership**: `stage_states` already owns stage statuses. It now also
  carries a `_verdicts` subkey (latest critique and review verdicts keyed by
  stage).
- **Reversibility**: High. All changes are additive — rolling back means
  the router falls back to existing natural-language inference. No data
  migration.

## Appetite

**Size:** Medium

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 1 (after plan critique)
- Review rounds: 1 (standard PR review)

The fix is narrow (one SKILL.md file, one CLI tool, one test file) but the
correctness requirements are strict — a regression test replaying the
12-step sequence from #1036 is mandatory.

## Prerequisites

No prerequisites — this work has no external dependencies. Redis,
Popoto ORM, and the existing `PipelineStateMachine` are already present.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | `stage_states` persistence |
| Popoto ORM | `python -c "from models.agent_session import AgentSession"` | Session model access |

## Solution

### Key Elements

- **Verdict recorder**: A new `tools/sdlc_verdict.py` CLI that writes the
  latest critique/review verdict onto the PM session's `stage_states` JSON
  under a `_verdicts` metadata key (mirrors the existing `_patch_cycle_count`
  and `_critique_cycle_count` convention). Invoked by `/do-plan-critique` and
  `/do-pr-review` after they emit their verdict.
- **Enriched stage query**: `tools/sdlc_stage_query.py` is extended to return
  cycle counters and latest verdicts alongside stage statuses. The JSON
  payload gains `_meta` section.
- **Legal Dispatch Guards**: A new section at the top of
  `.claude/skills/sdlc/SKILL.md` Step 4 that lists hard preconditions
  evaluated against the enriched query output BEFORE the dispatch table is
  consulted. Any guard violation forces a specific dispatch or escalation.
- **Oscillation counter**: A same-stage dispatch counter persisted on the
  session under `_sdlc_dispatches` (incremented every time `/sdlc` dispatches
  the same sub-skill twice in a row without an intervening state change).
  When it exceeds 3, the router escalates to `blocked` and surfaces the
  reason.

### Flow

Incoming `/sdlc` invocation -> Query enriched `stage_states` -> Evaluate
Legal Dispatch Guards -> If any guard fires, emit its forced dispatch or
escalation and return -> Otherwise, consult the dispatch table rows using
stage_states + latest verdicts -> Dispatch exactly one sub-skill -> Return.

**Starting point:** `/sdlc issue N` -> **Enriched query**: stages, cycle
counts, verdicts, PR number -> **Guards evaluated** -> **Dispatch
decision** -> **Sub-skill launched** -> **End state** (return to caller).

### Technical Approach

**1. Extend `sdlc_stage_query` to return an enriched payload.**

Current output: `{"ISSUE": "completed", "PLAN": "completed", ...}`

New output:
```json
{
  "stages": {"ISSUE": "completed", "PLAN": "completed", ...},
  "_meta": {
    "patch_cycle_count": 0,
    "critique_cycle_count": 1,
    "latest_critique_verdict": "NEEDS REVISION",
    "latest_review_verdict": null,
    "revision_applied": false,
    "pr_number": null,
    "same_stage_dispatch_count": 2,
    "last_dispatched_skill": "/do-plan-critique"
  }
}
```

Backward compatibility: if a caller passes `--format legacy`, return the old
flat shape. All existing callers (the SDLC skill dispatch logic) are updated
to read the new shape. The legacy flag is a transitional safety net, not a
long-lived API.

**2. New CLI `tools/sdlc_verdict.py` for recording verdicts.**

```
python -m tools.sdlc_verdict record --stage CRITIQUE --verdict "NEEDS REVISION" --issue-number N
python -m tools.sdlc_verdict record --stage REVIEW --verdict "CHANGES REQUESTED" --blockers 2 --issue-number N
python -m tools.sdlc_verdict get --stage CRITIQUE --issue-number N
```

Writes to `stage_states._verdicts[stage] = {"verdict": ..., "recorded_at":
ISO8601, "artifact_hash": sha256}` where `artifact_hash` is computed from
the plan file content (for CRITIQUE) or PR diff head-hash (for REVIEW).

`/do-plan-critique` invokes `sdlc_verdict record` after posting its verdict.
`/do-pr-review` invokes it after emitting its `<!-- OUTCOME {...} -->`
block.

**3. Legal Dispatch Guards in SDLC SKILL.md.**

Add a new "Step 3.5: Legal Dispatch Guards" section evaluated before
Step 4's dispatch table. Each guard is a boolean precondition with a
forced dispatch if it fires:

| Guard | Condition | Forced Dispatch |
|-------|-----------|-----------------|
| G1: Critique loop | Latest critique verdict is `NEEDS REVISION` or `MAJOR REWORK` AND last dispatched skill was `/do-plan-critique` | `/do-plan` |
| G2: Critique cycle cap | `critique_cycle_count >= MAX_CRITIQUE_CYCLES` (2) AND CRITIQUE is still failing | Escalate: emit `blocked` state with reason `critique cycle cap reached` |
| G3: PR lock | Open PR exists for this issue AND proposed dispatch is `/do-plan` or `/do-plan-critique` | Redirect to `/do-pr-review` / `/do-patch` / `/do-merge` based on stage_states |
| G4: Oscillation (universal) | `same_stage_dispatch_count >= 3` | Escalate: emit `blocked` state with reason `stage oscillation — {skill} dispatched {N} times without state change` |
| G5: Unchanged critique artifact | Previous CRITIQUE verdict exists AND current plan file hash matches recorded hash | Use cached verdict — do not re-dispatch `/do-plan-critique` |

**G5 applies to CRITIQUE only, not REVIEW.** Plan files are pure text that only
change when the plan file changes. Review verdicts on a PR can legitimately
change without the diff changing — CI status flips green/red, new linked
issues, new sibling PRs merging, human review comments arriving. Caching
review verdicts on a diff hash would mask legitimate signal changes. The
non-determinism of `/do-pr-review` is handled by G4 (oscillation cap) instead
— after 3 same-skill dispatches without state change, the router escalates
to `blocked` and surfaces the non-determinism for human judgment.

**Implementation Note (G5 hash stability):** The `artifact_hash` for CRITIQUE
MUST be computed over the full UTF-8-encoded plan file bytes including
frontmatter, after normalizing line endings to `\n`. Do NOT strip the YAML
frontmatter before hashing — frontmatter edits (e.g. `revision_applied: true`)
are meaningful plan changes that SHOULD bust the cache. Do NOT normalize
whitespace within prose — a reviewer who reflows a paragraph is editing the
plan and the critique should re-run. Only line-ending normalization is
required (cross-platform safety for collaborators on Windows checkouts).

G5 on CRITIQUE converts "same input, different output" into "same input, one
consistent output." Cache invalidation: any edit to the plan file changes
its sha256 hash; no TTL is needed because the hash IS the cache key. A plan
whose hash matches a prior verdict cannot be re-critiqued until it is
re-edited.

**Coverage across all stages:** G1 targets CRITIQUE specifically, G2 caps
critique cycles, G3 locks plan-stage dispatch once a PR exists, G5 caches
CRITIQUE verdicts. G4 is **universal** — it applies to EVERY stage,
including the less-scrutinized DOCS and MERGE stages. If the router
dispatches `/do-docs` three times without stage_states changing, G4
escalates. If `/do-merge` is dispatched repeatedly on a PR that keeps
failing its merge gate, G4 escalates. No stage is exempt.

**4. Same-stage dispatch counter (precise state machine).**

The oscillation counter tracks how many times in a row `/sdlc` has picked the
same sub-skill WITHOUT the pipeline state changing between picks.

- **Storage:** `stage_states._sdlc_dispatches` is a **bounded list** (max 10
  entries, FIFO-evicted) of `{skill: str, at: ISO8601, stage_snapshot: dict}`
  records. Bounded list prevents unbounded growth over long-running sessions.
- **When written:** At the END of `/sdlc` Step 4 — after the dispatch decision
  is finalized and BEFORE the Skill tool is invoked. The router writes the
  decision + current `stage_snapshot` BEFORE launching the sub-skill. This
  ensures the record exists even if the sub-skill crashes mid-execution.
- **When read:** At the START of `/sdlc` Step 3.5 (guard evaluation), for the
  current invocation to decide whether to trip G4.
- **`stage_snapshot` projection (explicit):** Only these fields:
  - `stages` (the stage_state dict keyed by stage name → status)
  - `_verdicts` (the most recent verdict per stage)
  - `_patch_cycle_count` and `_critique_cycle_count`
  - `pr_number` (if any)

  **Excluded from snapshot** (to prevent spurious churn):
  - Timestamps, ISO8601 strings, wall-clock ticks
  - `recorded_at` inside verdict records
  - PR check counts, CI status, human review comments
  - The `_sdlc_dispatches` list itself (otherwise the counter would never match)

- **Counter logic:** `same_stage_dispatch_count` is computed on read as "how
  many of the most recent entries have the SAME skill AND IDENTICAL
  stage_snapshot, counted backward from the last entry." A dict-equality
  compare on the snapshot projection — cheap and deterministic.
- **What resets the counter:** Any dispatch where the skill differs from the
  prior dispatch, OR any dispatch where the `stage_snapshot` differs from the
  prior snapshot. Human intervention (running `/do-patch` or `/do-test`
  directly outside of `/sdlc`) changes `stage_states`, which changes the
  snapshot, which implicitly resets the counter on the next `/sdlc` read —
  no explicit hook needed (resolves former Open Question #2).
- **Sub-skill crash handling:** If a sub-skill crashes before it updates
  `stage_states`, the next `/sdlc` reads the same snapshot and the counter
  increments. This is the intended behavior — repeated dispatches of a
  crashing skill ARE oscillation. G4 catches it.

**Implementation Note (snapshot determinism):** When computing the
`stage_snapshot` for equality comparison, the dict MUST be serialized with
`json.dumps(snapshot, sort_keys=True, separators=(",", ":"))` before
comparing. Python dict equality is insertion-order-insensitive, but mixing
raw-dict compares with JSON-roundtripped dicts (which can happen when a
snapshot is loaded from Redis) creates subtle bugs where equal-looking
snapshots compare unequal. Canonicalize through `json.dumps(..., sort_keys=True)`
once, compare string equality. Document this in the `sdlc_router.py`
snapshot helper.

G4 uses this counter. Threshold: `same_stage_dispatch_count >= 3` → escalate
to `blocked`.

**5. Regression test.**

Create `tests/unit/test_sdlc_router_oscillation.py` that replays the
12-step dispatch sequence from #1036. Uses a fake `AgentSession` with
seeded `stage_states` and drives the router's decision logic
directly (the dispatch table is extracted into a pure function for this).
Asserts each turn produces the expected next skill, and asserts terminal
states match the expected outcome (merged or explicit blocked).

The dispatch logic lives in SKILL.md today as natural language. To make it
testable, extract a Python-level reference implementation at
`agent/sdlc_router.py` that encodes the dispatch table and guards as code.
The SKILL.md continues to be the human-readable router runbook, but it
cites `agent.sdlc_router.decide_next_dispatch()` as the canonical
algorithm.

**Parity contract (SKILL.md markdown vs. Python table):**

The SKILL.md dispatch table rows are parsed deterministically by the parity
test via a fixed format:
- Row format: `| <number> | <state string> | <skill string> | <reason> |`
- State string and skill string MUST be extractable by a fixed regex
- Each row has a stable row number (1, 2, 3, 4a, 4b, 4c, 5, 6, 7, 8, 8b, 9, 10, 10b)
- The Python `DISPATCH_RULES` list has one entry per row number with fields
  `{row_id: str, state_predicate: Callable, skill: str, reason: str}`

The parity test asserts every markdown row number appears in `DISPATCH_RULES`
with a matching `skill` string and a `state_predicate` whose natural-language
`__doc__` matches the markdown state string (normalized whitespace, case
insensitive). If the markdown evolves but the Python doesn't (or vice versa),
the test fails with a precise diff of the mismatched row. This avoids brittle
full-table comparison and localizes drift to single rows.

The "Reason" column in the markdown is descriptive only and NOT checked by the
parity test — it can evolve freely for human readability without breaking CI.

**Implementation Note (parity parser robustness):** The markdown row parser
MUST tolerate trailing whitespace, escaped pipe characters inside cells
(`\|`), and multi-line cells that use `<br>` for line breaks. Use a tested
markdown-table parser (e.g. `mistune` if already a dependency, otherwise a
small local regex parser with explicit test coverage) rather than naive
string splitting on `|`. Add a negative test that mutates a row in SKILL.md
and asserts the parity test fails with a readable diff pointing at the
specific row number — this proves the test catches drift rather than silently
passing when the parser crashes.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `tools/sdlc_verdict.py` catches all exceptions and writes `{}` on
      failure (same pattern as `sdlc_stage_marker`). Test: corrupt the session's
      `stage_states` JSON and call `sdlc_verdict record` — must not crash.
      Covered by `test_corrupt_stage_states_does_not_crash`.
- [x] `tools/sdlc_stage_query.py` enriched output gracefully handles missing
      `_verdicts` / missing cycle counters — returns sane defaults. Test: session
      with no metadata returns `{"stages": {...}, "_meta": {"patch_cycle_count":
      0, ...}}`. Covered by `test_defaults_when_session_missing`.
- [x] No exception handlers added in `agent/sdlc_router.py` — the dispatch
      function is pure and deterministic given a state dict. Failure modes are
      caller's responsibility.

### Empty/Invalid Input Handling
- [x] `sdlc_verdict record` with an unknown stage returns `{}` and does not
      write to session. Test: `--stage BOGUS` returns `{}`. Covered by
      `test_rejects_unknown_stage`.
- [x] Enriched query with empty `stage_states` returns
      `{"stages": {}, "_meta": {...defaults}}`. Test: new session with no prior
      dispatches. Covered by `test_defaults_when_session_missing`.
- [x] Router guards fail closed: if the enriched query returns `{}`, Guard G3
      (PR lock) cannot fire (no pr_number available) — router falls through to
      natural-language dispatch (current behavior preserved for edge cases).

### Error State Rendering
- [x] Guard G2 (critique cycle cap) escalation produces a human-readable
      `blocked` message in the router output. The orchestrator LLM surfaces this
      to the user rather than silently looping. Test: seed a session with
      `critique_cycle_count=2` and verify the router emits `blocked` with
      the cycle-cap reason. Covered by `test_g2_critique_cycle_cap`.
- [x] Guard G4 (oscillation) emits a structured `blocked` state that the PM
      persona can read and report. Covered by `test_g4_oscillation_cap`.

## Test Impact

- [x] `tests/unit/test_sdlc_stage_query.py` — UPDATE: add cases for the
      enriched `--format json` output (new `_meta` section with cycle counters
      and verdicts). Existing flat-shape tests run under `--format legacy` and
      still pass.
- [x] `tests/unit/test_pipeline_state_machine.py` — UPDATE: add verdict
      read/write tests on the `_verdicts` metadata subkey. Add test that
      `classify_outcome()` routes verdict writes through
      `sdlc_verdict.record_verdict()` and produces the same `_verdicts`
      shape as the CLI path. Existing tests unaffected.
- [x] `tests/unit/test_sdlc_mode.py` — UPDATE: if any test asserts the exact
      shape of the SDLC skill's dispatch output, update to assert on the new
      enriched shape.
- [x] `tests/unit/test_sdlc_stubs.py` — UPDATE: stubs for `sdlc_verdict` may
      need to be added (if the test file stubs SDLC tools for isolation).

Greenfield tests (new files, no existing tests to modify):
- [x] `tests/unit/test_sdlc_verdict.py` — NEW: round-trip recording and
      retrieval, artifact-hash stability, graceful failure on bad inputs.
- [x] `tests/unit/test_sdlc_router_oscillation.py` — NEW: 12-step replay
      regression test for #1036, plus synthetic cases for each of G1-G5.
- [x] `tests/unit/test_sdlc_router_decision.py` — NEW: pure-function tests
      for `agent.sdlc_router.decide_next_dispatch()`.
- [x] `tests/unit/test_sdlc_skill_md_parity.py` — NEW: markdown-to-Python
      parity test that fails when SKILL.md dispatch rows diverge from
      `agent.sdlc_router.DISPATCH_RULES`.
- [x] `tests/unit/test_stage_states_helpers.py` — NEW: optimistic-retry
      helper tests — retry-on-conflict, retry exhaustion, success path.

## Rabbit Holes

- **Full LLM-verdict determinism**: Do not try to make `/do-plan-critique`
  or `/do-pr-review` deterministic by pinning temperature or seeding the
  LLM. This is out of scope and would require changing how we invoke
  Anthropic's API. The artifact-hash cache (G5) is the correct level to
  solve this at.
- **Rewriting the router as a pure state machine**: Do not rewrite
  `/sdlc` SKILL.md into a rigid state machine with no LLM judgment. The
  LLM-orchestrated flexibility is intentional — only the hard guards and
  cycle limits need to be structural. Keep the dispatch table as the
  primary decision surface; the guards are preconditions, not a
  replacement.
- **Verdict parsing via LLM**: Do not add another LLM call to parse the
  critique/review output. The skills already produce structured verdicts
  (explicit strings in `/do-plan-critique`, `<!-- OUTCOME {...} -->` blocks
  in `/do-pr-review`). Parse these with plain string matching; do not
  introduce an LLM-based parser.
- **Rewriting `classify_outcome()` in `agent/pipeline_state.py`**: That function
  parses verdicts from dev-session output tail for bridge-initiated sessions.
  It stays as-is for its existing callers. This plan does NOT rewrite it.

  **Deduplication: there is ONE source of truth.** To avoid two writers of the
  `_verdicts` key, the new `sdlc_verdict` CLI is the SOLE writer. When
  `classify_outcome()` parses a verdict from the output tail, it invokes
  `sdlc_verdict record` (via subprocess or direct Python call — see task 3.5
  below) instead of writing to `stage_states` itself. The existing
  `_critique_cycle_count` increment in `classify_outcome()` remains unchanged —
  that is a separate metadata field with a clear existing owner. Only verdict
  RECORDING is unified. This is in scope for this plan, not a follow-up,
  because leaving two writers risks exactly the dual-source drift that caused
  the original bug.

  **Implementation Note (import direction):** The `classify_outcome()` caller
  lives in `agent/pipeline_state.py`; the verdict recorder lives in
  `tools/sdlc_verdict.py`. Today `tools/` does not import `agent/`, so the
  new call direction (`agent/pipeline_state.py` importing
  `tools.sdlc_verdict.record_verdict`) is acceptable. Do NOT introduce a
  reverse import (tools importing agent) — that would create a cycle with
  `agent/sdlc_router.py` which imports helpers from `tools/`. A regression
  test in `test_architectural_constraints.py` (or similar) should assert
  this one-way boundary.

## Risks

### Risk 1: Guard logic false-positives block legitimate flows
**Impact:** A guard fires incorrectly and forces a wrong dispatch, or
escalates a healthy pipeline to `blocked`.
**Mitigation:** Guards are deliberately narrow (verdict + state +
counter). Every guard has a regression test covering both the positive
case (guard fires correctly) and the negative case (guard does not fire
on healthy input). G5's artifact-hash is computed from file content only;
whitespace-only changes to the plan will produce a different hash and
force re-critique (conservative: prefer re-running over using a stale
cached verdict).

### Risk 2: `_verdicts` metadata key collides with future additions
**Impact:** Adding new metadata keys to `stage_states` conflicts with
this one.
**Mitigation:** Reserve the namespace — all new metadata keys are
underscore-prefixed (`_verdicts`, `_sdlc_dispatches`, already
`_patch_cycle_count`, `_critique_cycle_count`). The StageStates Pydantic
model already drops all underscore-prefixed keys from the stages dict
(see `pipeline_state.py:77-78`), so no stage-name collision is possible.

### Risk 3: SKILL.md drift from the Python reference implementation
**Impact:** The documentation (SKILL.md) says one thing but the code
(sdlc_router.py) does another. Router behavior diverges from its own
runbook.
**Mitigation:** CI check (simple pytest) asserts that the SKILL.md
dispatch-table markdown parses into the same transitions as the Python
reference. If they drift, CI fails. The Python module is the ground
truth.

### Risk 4: Oscillation counter resets too aggressively and misses loops
**Impact:** A subtle cycle where state changes slightly each turn (e.g.,
timestamp updates) never hits G4 because `stage_snapshot` looks
"different" each time.
**Mitigation:** `stage_snapshot` is explicitly a narrow projection —
stage statuses, verdicts, and cycle counts only. Timestamps, PR check
counts, and other churn fields are excluded. The snapshot is stable
across benign churn.

## Race Conditions

### Race 1: Concurrent writes to `stage_states._verdicts` and `_sdlc_dispatches`
**Location:** `tools/sdlc_verdict.py` and `agent/sdlc_router.py`
read-modify-write on `AgentSession.stage_states` (a JSON field).
**Trigger:** A skill finishes and invokes `sdlc_verdict record` at the same
moment that `classify_outcome()` (bridge path) records its own verdict for a
different stage, OR a PM and a hook both drive `/sdlc` for the same session.
Both read the same `stage_states` dict, both modify different keys, one save
wins and drops the other's change.
**Data prerequisite:** `stage_states` reflects all completed writes.
**State prerequisite:** Concurrent writers observe each other's writes.
**Mitigation (v1, in-scope):** All writers use a **read-modify-write with
optimistic retry** pattern, implemented as a small helper `update_stage_states`
in `tools/stage_states_helpers.py`:
1. Load session.
2. Snapshot `stage_states` JSON.
3. Apply the update function locally.
4. Save; if Popoto raises a conflict or if the saved value differs from
   the pre-save snapshot when re-read, retry up to 3 times.
5. If all 3 retries fail, log a warning and continue (the write is a
   metadata update, not a correctness-critical state change).

This pattern eliminates the lost-write window without requiring a Redis lock.
It is used by `sdlc_verdict record`, the oscillation-counter write in
`sdlc_router`, and (for consistency) the verdict write that
`classify_outcome()` now routes through `sdlc_verdict record` (see task 3.5).

**Out of scope (v2, deferred):** True Redis WATCH/MULTI locking keyed by
session_id. Defer until optimistic retry proves insufficient in production.

**Implementation Note (retry exhaustion observability):** When the optimistic
retry in `update_stage_states` exhausts its 3 attempts, the helper MUST emit
a WARNING log with `session_id`, `stage`, and `update_fn.__name__` so the
failure is traceable. Silently returning `False` without a log makes missed
writes invisible during bridge-path debugging. Also increment a Prometheus
counter `sdlc_stage_states_retry_exhausted_total` (or log a structured JSON
record if Prometheus is not wired yet) so the rate of lost writes is
observable in the dashboard. This is metadata lost, not correctness lost —
but sustained exhaustion indicates real contention worth escalating to v2.

### Race 2: Verdict recorded after the next `/sdlc` already read
**Location:** `tools/sdlc_verdict.py` write races with `/sdlc` read.
**Trigger:** `/do-plan-critique` emits the verdict in its output, the
orchestrator immediately calls `/sdlc`, but `sdlc_verdict record` hasn't
flushed yet.
**Data prerequisite:** Verdict is recorded before the router reads
`stage_states`.
**State prerequisite:** Writer wins before reader starts.
**Mitigation:** The verdict recorder is invoked as the LAST step of
`/do-plan-critique` and `/do-pr-review` — it must complete before the
skill returns. The harness guarantees sequential execution within a skill
invocation; the only way a stale read can happen is if a concurrent
`/sdlc` is running (see Race 1).

## No-Gos (Out of Scope)

- LLM temperature tuning or determinism pinning (mentioned in Rabbit Holes).
- Rewriting the router as a rigid state machine.
- Extending `classify_outcome()` in `agent/pipeline_state.py` to write to
  `_verdicts` — that is a separate unification task and belongs in a
  follow-up issue.
- Adding new dispatch rows for stages that weren't previously in the table.
- Changing the verdict strings emitted by `/do-plan-critique` or
  `/do-pr-review` — they stay as-is; the recorder parses them as-is.
- Dashboard changes to surface cycle counters or oscillation state (nice
  to have, not blocking).

## Update System

No update system changes required — this feature is purely internal to the
skills and tools directory. No new deploy step, no new config file, no new
external dependency. After the PR merges, `scripts/remote-update.sh` pulls
the changes normally and the new tool is available.

## Agent Integration

No agent integration required — this is a skills-and-tools internal change.
The new `tools/sdlc_verdict.py` is invoked by `/do-plan-critique` and
`/do-pr-review` via the bash tool inside those skills (same pattern as
`tools/sdlc_stage_marker`). It is not surfaced through MCP or the bridge.

## Documentation

### Feature Documentation
- [x] Create `docs/features/sdlc-router-oscillation-guard.md` describing
      the guards (G1-G5), the enriched stage query, and the verdict recorder.
      Include the G1-G5 table and the regression-test reference.
- [x] Add entry to `docs/features/README.md` index table pointing at the
      new feature doc.

### Inline Documentation
- [x] Docstring on `agent/sdlc_router.decide_next_dispatch()` describing
      the algorithm and guard order.
- [x] Docstring on `tools/sdlc_verdict.record_verdict()` and
      `read_verdict()` describing the `_verdicts` key shape.
- [x] Comments in `.claude/skills/sdlc/SKILL.md` new Legal Dispatch
      Guards section pointing at `agent.sdlc_router.decide_next_dispatch`
      as the canonical algorithm.

### External Documentation Site
Not applicable — this repo does not use Sphinx / MkDocs / Read the Docs for
the SDLC skills.

## Success Criteria

- [x] `/sdlc` on a session with latest CRITIQUE verdict `NEEDS REVISION`
      and last_dispatched_skill `/do-plan-critique` dispatches `/do-plan`
      (not `/do-plan-critique`). Covered by
      `tests/unit/test_sdlc_router_oscillation.py::test_g1_critique_loop_blocked`.
- [x] `/sdlc` with `critique_cycle_count >= 2` and CRITIQUE still failing
      emits `blocked`. Covered by `test_g2_critique_cycle_cap`.
- [x] `/sdlc` with an open PR for the current issue never dispatches
      `/do-plan` or `/do-plan-critique`. Covered by `test_g3_pr_lock`.
- [x] `/sdlc` that has dispatched the same skill 3 times with unchanged
      state emits `blocked`. Covered by `test_g4_oscillation_cap`.
- [x] `/sdlc` re-invoked on an unchanged plan hash uses the cached
      critique verdict rather than re-dispatching `/do-plan-critique`.
      Covered by `test_g5_artifact_hash_cache`.
- [x] The 12-step #1036 dispatch sequence replay terminates in `merged`
      or a legitimate `blocked` state, never in a loop. Covered by
      `test_1036_replay_terminates`.
- [x] `tools/sdlc_stage_query` returns the enriched payload; legacy
      shape available via `--format legacy`.
- [x] `tools/sdlc_verdict record` and `get` round-trip a verdict with
      artifact hash.
- [x] `classify_outcome()` in `agent/pipeline_state.py` routes verdict
      writes through `sdlc_verdict.record_verdict()`. There is ONE writer
      for `_verdicts`. Covered by a new test in
      `tests/unit/test_pipeline_state_machine.py`.
- [x] `update_stage_states` helper retries on write conflict and succeeds
      on the second attempt. Covered by
      `tests/unit/test_stage_states_helpers.py::test_retry_on_conflict`.
- [x] Tests pass (`/do-test`).
- [x] Documentation updated (`/do-docs`): feature doc created, README
      index updated.
- [x] Lint and format clean (`python -m ruff check . && python -m ruff
      format --check .`).

## PR Review Blocker Fixes (Post-Review)

The following blockers were identified in the PR #1044 review and resolved:

- [x] **Blocker 1**: `agent/pipeline_state.py _save()` clobbers `_verdicts` and `_sdlc_dispatches` — fixed by `_load_preserved_metadata()` which reloads all `_*` keys before writing. Regression tests added in `TestSaveMetadataPreservation` in `tests/unit/test_pipeline_state_machine.py` (4 new tests).
- [x] **Blocker 2**: `agent/sdlc_router.record_dispatch()` was only callable via Python API, not at runtime — added `tools/sdlc_dispatch.py` CLI module (`python -m tools.sdlc_dispatch record`) and updated SKILL.md with concrete bash examples.


## Team Orchestration

### Team Members

- **Builder (router + tools)**
  - Name: `router-builder`
  - Role: Implement `agent/sdlc_router.py`, extend `sdlc_stage_query.py`,
    create `sdlc_verdict.py`, update SKILL.md dispatch-guards section.
  - Agent Type: `builder`
  - Resume: true

- **Test Engineer (regression suite)**
  - Name: `test-engineer`
  - Role: Implement the five guard tests, the 12-step replay, and the
    pure-function decision tests. Update existing tests to the new
    enriched query shape.
  - Agent Type: `test-engineer`
  - Resume: true

- **Validator (final pass)**
  - Name: `router-validator`
  - Role: Verify success criteria, run the full test suite, verify
    SKILL.md/Python parity check.
  - Agent Type: `validator`
  - Resume: true

- **Documentarian**
  - Name: `router-documentarian`
  - Role: Create `docs/features/sdlc-router-oscillation-guard.md`,
    update `docs/features/README.md` index.
  - Agent Type: `documentarian`
  - Resume: true

## Step by Step Tasks

### 1. Extract the reference dispatch algorithm
- **Task ID**: build-reference-algorithm
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_router_decision.py` (create)
- **Informed By**: existing SKILL.md dispatch table
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/sdlc_router.py` with `decide_next_dispatch(stage_states, meta, context) -> Dispatch | Blocked`.
- Encode the existing dispatch table Rows 1-10b as pure Python.
- Expose the table as a structured object (list of rules) so a CI test can
  cross-check SKILL.md markdown against it.
- No guards yet — those come in task 3.

### 2. Extend `sdlc_stage_query` with enriched payload
- **Task ID**: build-enriched-query
- **Depends On**: build-reference-algorithm
- **Validates**: `tests/unit/test_sdlc_stage_query.py` (update)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- Return `{"stages": {...}, "_meta": {...}}` by default.
- Preserve flat shape under `--format legacy`.
- `_meta` includes: `patch_cycle_count`, `critique_cycle_count`,
  `latest_critique_verdict`, `latest_review_verdict`, `revision_applied`,
  `pr_number`, `same_stage_dispatch_count`, `last_dispatched_skill`.
- `pr_number` is read from existing session field (or looked up via
  `gh pr list --search "#{issue_number}" --state open`).
- Compute `revision_applied` by parsing the plan file frontmatter — read
  the plan path from session metadata or from `grep -rl "#{issue_number}"
  docs/plans/`.

### 3. Create `sdlc_verdict` CLI
- **Task ID**: build-verdict-recorder
- **Depends On**: build-reference-algorithm, build-stage-states-helpers
- **Validates**: `tests/unit/test_sdlc_verdict.py` (create)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- Expose `record_verdict(session, stage, verdict_str, blockers=None,
  tech_debt=None)` and `get_verdict(session, stage)` as Python functions
  (called from CLI AND from `classify_outcome()` — see task 3.5).
- `record` subcommand writes `stage_states._verdicts[stage] = {"verdict":
  str, "recorded_at": ISO8601, "artifact_hash": sha256}` via the
  `update_stage_states` helper (optimistic retry).
- `get` subcommand reads the latest verdict for a stage.
- Artifact hash computation: plan file content for CRITIQUE only (REVIEW
  does not cache by diff hash — see G5 rationale in Technical Approach).
  Plan file path discovered by `grep -rl "#{issue_number}" docs/plans/`.
- Graceful failure: returns `{}` on any error (same as sdlc_stage_marker).
- Invoke from `/do-plan-critique` SKILL.md Step 5 and
  `/do-pr-review` SKILL.md after the OUTCOME block.

### 3.5. Route `classify_outcome()` through the verdict recorder
- **Task ID**: build-classify-outcome-unification
- **Depends On**: build-verdict-recorder
- **Validates**: `tests/unit/test_pipeline_state_machine.py` (update)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/pipeline_state.py::classify_outcome()`, when a critique or review
  verdict is extracted from the dev-session output tail, invoke
  `tools.sdlc_verdict.record_verdict(session, stage, verdict_str)` directly
  (same process — not subprocess) instead of writing to `stage_states` inline.
- Preserve existing `_critique_cycle_count` increment logic — unchanged.
- The goal: ONE writer for `_verdicts`. Bridge-initiated sessions and
  locally-initiated sessions both funnel through the same code path.
- Add a regression test: `classify_outcome()` with a critique verdict
  populates `_verdicts["CRITIQUE"]` with the same shape the CLI produces.

### 3.6. Add optimistic-retry helper for stage_states writes
- **Task ID**: build-stage-states-helpers
- **Depends On**: none
- **Validates**: `tests/unit/test_stage_states_helpers.py` (create)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/stage_states_helpers.py` with `update_stage_states(session,
  update_fn, max_retries=3) -> bool`.
- Loads session, snapshots `stage_states` JSON, applies `update_fn(dict)`,
  saves, verifies.
- On conflict or mismatch, reloads and retries up to `max_retries`.
- Returns `True` on success, `False` on exhaustion (logs a warning).
- Used by `sdlc_verdict record`, oscillation-counter write in `sdlc_router`,
  and the `classify_outcome()` write path.

### 4. Add Legal Dispatch Guards to the router
- **Task ID**: build-guards
- **Depends On**: build-enriched-query, build-verdict-recorder, build-stage-states-helpers
- **Validates**: `tests/unit/test_sdlc_router_oscillation.py` (create)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement G1-G5 in `agent/sdlc_router.py` as a `_evaluate_guards()`
  function that runs BEFORE the dispatch table.
- Each guard returns either `None` (pass) or a `GuardTripped(skill=...,
  reason=...)` / `Blocked(reason=...)` object.
- The router collects the first tripped guard and returns it.
- Update `.claude/skills/sdlc/SKILL.md` Step 3.5 "Legal Dispatch Guards"
  subsection.
- Reserve the `_sdlc_dispatches` metadata key on `stage_states` for the
  oscillation counter.

### 5. Write regression tests
- **Task ID**: test-regression-suite
- **Depends On**: build-guards
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- `test_sdlc_router_decision.py`: pure-function tests of the table
  (one test per row).
- `test_sdlc_router_oscillation.py`: one test per guard (G1-G5) plus
  the `test_1036_replay_terminates` test that drives 12 turns.
- `test_sdlc_verdict.py`: round-trip, hash stability, bad-input failure.
- `test_sdlc_stage_query.py`: enriched payload shape, legacy flag.
- Fixtures: seeded `AgentSession` objects with varying `stage_states`.

### 6. Parity check: SKILL.md markdown vs. Python table
- **Task ID**: test-skill-md-parity
- **Depends On**: build-guards
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- Add `tests/unit/test_sdlc_skill_md_parity.py` that parses the dispatch
  table from SKILL.md and asserts it matches the Python rules.
- Parser: read the markdown table, extract rows, cross-reference with
  `sdlc_router.DISPATCH_RULES`.
- Prevents future drift.

### 7. Wire verdict recording into do-plan-critique and do-pr-review
- **Task ID**: build-skill-integrations
- **Depends On**: build-verdict-recorder
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- `.claude/skills/do-plan-critique/SKILL.md` Step 5: after posting the
  verdict, invoke `python -m tools.sdlc_verdict record --stage CRITIQUE
  --verdict "$VERDICT" --issue-number $N`.
- `.claude/skills/do-pr-review/SKILL.md` Step 6 (after OUTCOME block):
  invoke `python -m tools.sdlc_verdict record --stage REVIEW --verdict
  "$VERDICT_STRING" --blockers $BLOCKERS --tech-debt $TECH_DEBT
  --issue-number $N`.

### 8. Validate
- **Task ID**: validate-all
- **Depends On**: test-regression-suite, test-skill-md-parity,
  build-skill-integrations, build-classify-outcome-unification
- **Assigned To**: router-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full test suite (`pytest tests/unit/ -n auto`).
- Verify every Success Criterion has a corresponding passing test.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Smoke test: run `python -m tools.sdlc_stage_query --issue-number 1040`
  and verify the enriched payload shape.
- Smoke test: run `python -m tools.sdlc_verdict record --stage CRITIQUE
  --verdict "READY TO BUILD" --issue-number 1040 &&
  python -m tools.sdlc_verdict get --stage CRITIQUE --issue-number 1040`
  and verify round-trip.
- Smoke test: invoke `classify_outcome()` with a simulated critique-verdict
  output tail and verify `_verdicts["CRITIQUE"]` is populated identically
  to the CLI path.

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: router-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-router-oscillation-guard.md`.
- Include the G1-G5 table, the enriched query payload schema, and
  references to `agent/sdlc_router.py` and `tools/sdlc_verdict.py`.
- Add to `docs/features/README.md` index.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_sdlc_*.py -x -q` | exit code 0 |
| Regression replay | `pytest tests/unit/test_sdlc_router_oscillation.py::test_1036_replay_terminates -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| SKILL.md parity | `pytest tests/unit/test_sdlc_skill_md_parity.py -v` | exit code 0 |
| Enriched query shape | `python -m tools.sdlc_stage_query --issue-number 1040 \| python -c "import json,sys; d=json.load(sys.stdin); assert 'stages' in d and '_meta' in d"` | exit code 0 |
| Verdict round-trip | `python -m tools.sdlc_verdict record --stage CRITIQUE --verdict "READY TO BUILD" --issue-number 1040 && python -m tools.sdlc_verdict get --stage CRITIQUE --issue-number 1040` | output contains `READY TO BUILD` |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

## Revision Summary (2026-04-18)

Revision pass addressed these concerns from the initial critique:

| Concern | Resolution |
|---------|------------|
| G5 cache semantics ambiguous — does it apply to REVIEW? | **Scoped to CRITIQUE only.** REVIEW is covered by G4's universal oscillation cap. The rationale is explicit in Technical Approach. |
| Dual writers to `_verdicts` (CLI + `classify_outcome()`) create drift risk | **Unified.** New task 3.5 routes `classify_outcome()` through `sdlc_verdict.record_verdict()`. ONE writer. |
| SKILL.md vs. Python parity check was hand-wave | **Concrete contract** — row-number-keyed comparison, state-predicate `__doc__` matched to markdown state cell, Reason column intentionally excluded. Localized failure diffs. |
| Race 1 dismissed with "single-PM invariant" despite new write paths | **Optimistic-retry helper** (task 3.6) wraps all `stage_states` writes. Retries on conflict up to 3 times, logs on exhaustion. |
| G4 oscillation counter state machine underspecified | **Fully specified** — bounded list (max 10), written BEFORE skill launch, snapshot projection explicitly enumerated (excluded: timestamps, CI churn, the list itself), counter reset on any state-snapshot diff. |
| DOCS/MERGE stage oscillation uncovered | G4 is **universal**, applies to every stage. Explicit coverage note added in Technical Approach. |
| Open questions left unresolved | All three resolved in-plan (see Open Questions section). |

## Revision Summary (2026-04-18, second pass)

Second revision pass embeds Implementation Notes for concerns that the prior
revision addressed in prose but did not flag as actionable for the builder.
These are NOT new blockers — the concerns were already acknowledged. They are
surfaced now as explicit Implementation Note callouts inside the relevant
sections so the build agent cannot miss them during execution.

| Concern | Implementation Note Location |
|---------|-------------------------------|
| G5 artifact-hash stability across platforms and whitespace edits | **Technical Approach — G5 table footnote.** Hash full UTF-8 bytes including frontmatter; normalize line endings only; do NOT strip frontmatter or normalize internal whitespace. |
| Snapshot-equality false negatives from dict ordering / JSON roundtrip mismatch | **Technical Approach — Step 4 (same-stage counter).** Canonicalize snapshots via `json.dumps(..., sort_keys=True)` before comparison. |
| Silent write loss when `update_stage_states` retries exhaust | **Race Conditions — Mitigation (v1).** WARNING log with session_id + stage + update_fn.__name__, plus a counter metric to surface sustained contention. |
| Markdown parity parser fragility on escaped pipes / multi-line cells | **Technical Approach — Step 5 (parity contract).** Use a tested markdown-table parser; add a negative mutation test asserting parity failure produces a readable row-level diff. |
| Import-direction cycle risk when `classify_outcome()` calls `sdlc_verdict` | **Rabbit Holes — `classify_outcome()` dedup.** Agent imports tools; tools MUST NOT import agent. Boundary enforced by a regression test. |

Frontmatter changes: `revised: 2026-04-18 (second pass)`; `revision_applied`
remains `true`. No task list additions — every Implementation Note is a
surgical instruction the builder applies inside the task it is nested under.
No new Success Criteria are introduced; the notes sharpen existing criteria
rather than adding work.

---

## Open Questions

All prior open questions have been resolved during the revision pass:

- **G5 applies to CRITIQUE only** (see Technical Approach, G5 row of the
  guards table). Review verdicts are not cached on diff hash because CI
  status and linked-issue state legitimately vary without the diff changing.
  G4 (universal oscillation cap) handles REVIEW non-determinism instead.
- **G4 counter resets implicitly on state change** (see "What resets the
  counter" in Technical Approach step 4). Human intervention that modifies
  `stage_states` changes the snapshot and resets the counter on the next
  `/sdlc` read — no explicit hook needed.
- **Dispatch table natural-language inference stays as the fallback after
  guards.** Guards are preconditions, not a replacement for the table. A
  future plan may propose full structural routing; this plan does not.

No remaining open questions — the plan is ready for build.
