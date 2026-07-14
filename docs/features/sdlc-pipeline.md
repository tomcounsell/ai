# SDLC Pipeline

Overview of the SDLC pipeline routing, guards, and key metadata fields used by the SDLC router (`agent/sdlc_router.py`).

## Pipeline Flow

```
ISSUE → PLAN → CRITIQUE → BUILD → TEST → PATCH → REVIEW → DOCS → MERGE
```

Each stage is tracked as a JSON dict with stage-status keys (e.g. `{"ISSUE": "completed", "PLAN": "completed", ...}`). Since issue #2012 the durable primary store is the issue-keyed `PipelineLedger` (`(target_repo, issue_number)`), with the PM session's `AgentSession.stage_states` retained as a fallback for callers with no live per-issue lease — see [SDLC Issue-Keyed Stage Ledger](sdlc-issue-keyed-stage-ledger.md). The SDLC router reads this state (via `sdlc-tool stage-query`) and dispatches one sub-skill per invocation.

## Legal Dispatch Guards (G1–G8)

Guards are evaluated **in `GUARDS` list order** before the dispatch table; the
first guard to return a non-`None` decision wins and overrides the table. The
pinned order is `[G1, G2, G3, G4, G8, G7, G5, G6]` — guard IDs are historical
(assigned in the order each guard was introduced), not the evaluation order.
The table below is listed in evaluation order:

| Guard | Condition | Forced Dispatch |
|-------|-----------|-----------------|
| G1: Critique loop | Verdict NEEDS REVISION or MAJOR REWORK AND last dispatch was critique | `/do-plan` |
| G2: Critique cycle cap | `critique_cycle_count >= MAX_CRITIQUE_CYCLES` AND CRITIQUE not completed | `blocked` |
| G3: PR lock | PR open AND last/proposed dispatch is plan-stage skill | Redirect to appropriate PR-stage skill |
| G4: Oscillation | Same skill dispatched `MAX_SAME_STAGE_DISPATCHES` times without state change | `blocked` |
| G8: Stage-advance verification | `context["stage_artifacts_verified"] is False` (a claimed stage artifact failed live verification) | Re-dispatch the unverified stage's skill |
| G7: Plan-revising lock | `plan_revising=True` AND `revision_applied!=True` AND no open PR | `/do-plan` or `blocked` |
| G5: Unchanged plan hash | Critique verdict exists with matching `artifact_hash` | Reuse cached verdict |
| G6: Terminal merge | PR open, CI green, DOCS done, review APPROVED (head_sha-fresh, #2062) | `/do-merge` |

**Why G4 precedes G8.** G8 re-dispatches the same stage's skill on a false
artifact claim, with nothing upstream to stop it from doing so forever on a
persistently false claim. G4 is the loop-bound backstop: because G4 is
evaluated first, it fires and blocks once `same_stage_dispatch_count >=
MAX_SAME_STAGE_DISPATCHES` before G8 gets another chance to re-dispatch. The
phase-1 false-claim policy is "silent re-dispatch, then escalate via the
existing G4 cap" — not an immediate block on the first mismatch.

**Why G7 precedes G5 and G6 (issue #1871).** G5's cached READY-TO-BUILD fast
path does not itself read `plan_revising`, so G7 must run first to intercept
a stale-hash cache hit while a revision is pending. The "an already-mergeable
PR is never blocked by a stale `plan_revising` flag" guarantee does **not**
come from G7's position relative to G6 — it comes from G7's own Gate 1: G7
returns `None` immediately whenever `pr_number` is set. G6 only ever fires
when `pr_number` is set, so in every state where G6 could dispatch
`/do-merge`, G7 has already deferred at Gate 1. G6 always wins regardless of
list position relative to G7.

## Stage-Advance Verification Gate (G8, issue #1267)

The router advances on stage-completion markers that the executing agent
**self-attests** (the `<!-- OUTCOME {...} -->` contract). Nothing upstream of
G8 independently confirms the claimed load-bearing artifact — a PR actually
opened, a branch actually pushed, a plan actually committed — exists in the
world.

- **Where verification runs:** live verification happens in the next-skill
  **context-assembly** path (`tools/sdlc_next_skill.py`, reusing #2003's
  live-ref helpers) — deterministic, no LLM, and outside the router so
  `agent/sdlc_router.py` stays import-free of `tools/` (see
  `tests/unit/test_architectural_constraints.py`). G8 itself (
  `agent.sdlc_router.guard_g8_artifact_verification`) makes no live calls; it
  only consumes the context flags that path sets:
  `context["stage_artifacts_verified"]` / `context["unverified_stage"]`.
- **Positioning:** G8 is inserted into `GUARDS` immediately after G4, not
  before it — see "Why G4 precedes G8" above.
- **Firing condition:** G8 fires only when
  `context["stage_artifacts_verified"] is False` (an explicit, verified
  mismatch). Absent/unset/`True` is a no-op — a stage with no claimed
  artifact (or one that verified clean) never sets the flag to `False`.
- **Contract:** on fire, G8 maps `context["unverified_stage"]` to its owning
  skill (`STAGE_TO_SKILL`) and re-dispatches it. A mismatch that can't be
  mapped to a known stage fails open (returns `None`) rather than guessing a
  re-dispatch target.

## Plan-Revising Lock (G7)

G7 prevents `/do-build` from being dispatched while a critique-driven revision is still in flight.

### Problem it solves

Without G7, the pipeline had a race condition: a second critique round could revise the plan _after_ build had already consumed it and opened a PR. The cuttlefish #350 incident captured this failure: round-2 critique flagged a defect and revised the plan _after_ `/do-build` had shipped — the defect was rediscovered downstream as a review blocker, requiring a `/do-patch` round to apply the fix the critique had prescribed hours earlier.

### How it works

1. **Critique sets the lock**: `/do-plan-critique` Step 5.6 calls `sdlc-tool meta-set --key plan_revising --value true` when the verdict is NEEDS REVISION, MAJOR REWORK, or READY TO BUILD (with concerns) _and_ `revision_applied` is not already true in the plan frontmatter.

2. **Router blocks build**: G7 in `agent/sdlc_router.py::guard_g7_plan_revising` fires when `_meta.plan_revising` is truthy and `revision_applied` is falsy. If critique just ran (last dispatch was `/do-plan-critique`), G7 returns `Dispatch(/do-plan)`. If the lock has been set for more than `MAX_PLAN_REVISING_DISPATCHES + 1` router turns with no `/do-plan` in the recent dispatch history, G7 escalates to `Blocked`.

3. **Plan clears the lock**: `/do-plan` Phase 4 calls `sdlc-tool meta-set --key plan_revising --value false` in the same step that writes `revision_applied: true` to the plan frontmatter. The two signals always move together.

4. **Build proceeds normally**: Once the lock is cleared and `revision_applied: true` is set, the dispatch table routes to `/do-build` via Row 4c.

### Self-healing

If the lock-clear step is skipped (e.g. the plan skill crashes after writing `revision_applied: true` but before calling `meta-set`), G7 self-heals:

```python
# G7 gate 3: self-heal
if meta.get("revision_applied"):
    return None  # Lock informational only; revision_applied is the source of truth
```

### Deadlock backstop

If the lock is set but no `/do-plan` dispatch occurs within `MAX_PLAN_REVISING_DISPATCHES` (default 2) turns, G7 escalates to `Blocked`. The operator can manually clear the lock:

```bash
sdlc-tool meta-set --key plan_revising --value false --issue-number {N}
```

### Storage

The lock is stored as `stage_states["_plan_revising"]` (bool) — in the issue-keyed `PipelineLedger` when a live lease is held, on the PM session's `AgentSession.stage_states` otherwise (see [SDLC Issue-Keyed Stage Ledger](sdlc-issue-keyed-stage-ledger.md)). It is surfaced in `_meta.plan_revising` by `tools/sdlc_stage_query.py::_compute_meta()`.

A second metadata field, `_plan_hash_at_build_start` (str|None), is written by `/do-build` Step 7 and verified at Step 21 as a defense-in-depth check. If the plan's git commit hash changes mid-build, the build aborts.

## Convergence Latch (`revision_applied_at`, issue #1760)

`revision_applied` is a **sticky** boolean — `/do-plan` sets it `true` on every
revision pass and it stays `true` forever after. That's insufficient for one
consumer: `agent/sdlc_router.py::_critique_verdict_is_stale()` (which feeds
dispatch row 2b/3, deciding whether a NEEDS REVISION critique verdict should
re-route to `/do-plan-critique` or is already settled). A bare sticky boolean
can't distinguish "this is the settle-and-build revision the verdict judged"
from "this is some later, unrelated `/do-plan` dispatch on the same issue" —
either reading of `revision_applied: true` looks identical.

### The fix

`/do-plan` Phase 4 Step 2a now writes an **event-scoped** `revision_applied_at`
timestamp (ISO-8601 UTC, `date -u +"%Y-%m-%dT%H:%M:%SZ"`) into the plan
frontmatter in the *same* step that sets `revision_applied: true` — never as a
follow-up edit. `tools/sdlc_stage_query.py::_parse_revision_applied_at()`
reads it into `_meta.revision_applied_at`.

`_critique_verdict_is_stale()` uses it as a latch:

- If the latest `/do-plan` dispatch timestamp is **not later than**
  `revision_applied_at` → the verdict is judged converged (not stale) — this
  is the dispatch that produced the revision, not a later one.
- If a subsequent `/do-plan` dispatch postdates `revision_applied_at` → the
  latch does not apply and normal timestamp-based staleness resumes, so a
  later unrelated revision never gets a free pass straight to BUILD.
- If `revision_applied_at` is absent or unparseable → the latch is inert and
  the function falls back to the original timestamp-only staleness check
  (fail-safe to pre-#1760 behavior).
- **Verdict-kind gate (#2049, WS4):** the latch engages only for verdicts
  that do not require a revision (the settle-and-build READY TO BUILD path).
  For NEEDS REVISION / MAJOR REWORK the requested revision is exactly what
  invalidates the verdict, so the latch never suppresses staleness there —
  a settled revision routes to `/do-plan-critique` (row 2b) for re-critique.
  Previously the latch engaged for every verdict kind, which made row 2b step
  aside and row 3 re-dispatch `/do-plan` forever (`/do-plan` re-writes
  `revision_applied_at` on every pass, re-arming the suppression each round —
  the #1925/#1968 recurrence). Timestamp-only on every path: the sticky
  boolean is never consulted.

### `/do-plan` writer convention

Any skill or script that sets `revision_applied: true` in a plan's
frontmatter must write `revision_applied_at: <ISO-8601 UTC timestamp>`
alongside it, in the same commit. See `docs/sdlc/do-plan.md` for the
canonical Phase 4 Step 2a invocation.

## Fork/Supervisor Hardening (umbrella #2026)

The 2026-07-13 forensics batch surfaced a family of fork-vs-supervisor
failures (lease churn, self-locks, phantom waits, verdict-gate gaps). The
fixes below make fork identity and verdict freshness structural. Anchor issue
**#2026** stays open as the durable home for future instances.

### Single-owner lease + supervised-run signal (WS1, #2026)

One `run_id`, minted once by the supervisor's first `sdlc-tool
session-ensure`, owns the per-issue lock for the WHOLE run:

- **Supervised-run signal.** After winning (or renewing) the issue lock,
  `session-ensure` publishes the verified `run_id` to
  `session:supervisedrun:{issue}` (Redis, lock-TTL'd) and, when the session
  has a slug worktree, `.worktrees/{slug}/.sdlc-run` (`agent/supervised_run.py`).
  The signal is LIVE iff the issue lock is currently held by the signal's
  `run_id` — the lock is the single liveness source; there is no second TTL.
- **`SUPERVISED_RUN_ACTIVE` refusal.** A bare `session-ensure` under a live
  signal never contests the lock and never mints: it returns
  `{"blocked": true, "reason": "SUPERVISED_RUN_ACTIVE", "run_id": <supervisor's>}`.
  The stage fork inherits that `run_id` — enforcement lives in the tool
  (`tools/sdlc_session_ensure.py`), not prose, so a fork that ignores every
  instruction still cannot re-mint. A stale/expired signal falls back to
  normal standalone semantics.
- **TTL sized to stage wall time.** `ISSUE_LOCK_TTL_SECONDS` default is
  **1800s** (provisional/tunable; observed stages ran 6–25 min). A blocked
  `claude -p` supervisor has no executor for a mid-stage renewal heartbeat,
  so the lease must survive a stage without one; every `sdlc-tool` write
  still renews it at stage boundaries.
- **Explicit release.** `finalize_session` (`models/session_lifecycle.py`)
  releases the lease (`release_issue_lock`, compare-and-delete) and clears
  the signal on EVERY terminal transition — completion and graceful failure —
  so the happy path frees immediately and the TTL is only the crash backstop
  (the existing `orphaned_lock` self-heal covers a hard crash).
- **Single-owner MERGE.** `tools/merge_predicate.py` gains check group (d):
  when `--run-id` is supplied (the `/do-merge` skill always passes it), the
  merge actor's `run_id` must hold the current issue lease. A fork that never
  held the lease cannot merge past a blocked gate (Race 2). The merge-guard
  hook, which carries no run identity, skips only this group.

### Verdict-gated routing (WS3, #2062)

- **Row 10 verdict gate:** `_rule_ready_to_merge` requires a recorded
  `APPROVED` REVIEW verdict (mirroring row 9's #1932 gate) and head_sha
  freshness — `REVIEW == completed` alone is no longer merge-ready.
- **Row 8e no-verdict recovery:** owns every `REVIEW == completed` +
  no-recorded-verdict state that 8c/8d exclude (the #1897 misroute state) and
  re-dispatches `/do-pr-review`. G4-bounded.
- **Marker refusal (`REVIEW_VERDICT_MISSING`):** `sdlc-tool stage-marker
  --stage REVIEW --status completed` refuses with a named error when no
  substrate verdict is readable (`tools/sdlc_stage_marker.py`), making
  "post GitHub APPROVED but skip `verdict record`" impossible by
  construction. The refused state is exactly what row 8e recovers.
- **Head_sha staleness (row 8f + G6):** `sdlc-tool next-skill` context
  assembly live-fetches the PR head (`_fetch_pr_head_sha`,
  `context["pr_head_sha"]`); the router compares it to the verdict's
  `REVIEW_CONTEXT head_sha=` trailer — the same freshness definition
  `tools/merge_predicate` enforces. A mismatch, a missing trailer, or a
  failed lookup (fail-closed: `pr_head_sha=""` +
  `pr_head_sha_lookup_failed=true`, never omitted) routes to `/do-pr-review`
  at the new head instead of merging.

## `_meta` Fields

The enriched stage query output (`sdlc-tool stage-query`) includes a `_meta` dict alongside `stages`:

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `patch_cycle_count` | int | `_patch_cycle_count` | Number of patch cycles run |
| `critique_cycle_count` | int | `_critique_cycle_count` | Number of critique cycles run |
| `latest_critique_verdict` | str\|None | `_verdicts["CRITIQUE"]` | Most recent critique verdict text |
| `latest_review_verdict` | str\|None | `_verdicts["REVIEW"]` | Most recent review verdict text |
| `revision_applied` | bool | Plan frontmatter | Whether `revision_applied: true` is in the plan doc (sticky) |
| `revision_applied_at` | str\|None | Plan frontmatter | ISO-8601 UTC timestamp written alongside `revision_applied: true`; event-scoped convergence latch for `_critique_verdict_is_stale()` (#1760) |
| `pr_number` | int\|None | Session attr or `gh pr list` | Open PR number for this issue |
| `pr_merge_state` | str\|None | GitHub API | `mergeStateStatus` from `gh pr view` |
| `ci_all_passing` | bool\|None | GitHub API | Whether all status checks pass |
| `same_stage_dispatch_count` | int | `_sdlc_dispatches` | Consecutive same-skill same-state dispatches |
| `last_dispatched_skill` | str\|None | `_sdlc_dispatches` | Most recent skill dispatched |
| `plan_revising` | bool | `_plan_revising` | Plan-revising lock state (G7) |
| `plan_hash_at_build_start` | str\|None | `_plan_hash_at_build_start` | Git commit hash of plan doc at build start |
| `plan_exists` | bool | `_compute_meta()` / `find_plan_path()` | `True` if a plan file is present on disk for the issue. Used by `_rule_plan_not_critiqued` to require real evidence before routing to CRITIQUE (added #1640). |
| `issue_number` | int\|None | `_compute_meta()` | Resolved issue number. Enables `_rule_no_plan` to distinguish a genuine bootstrap from a stale status string (added #1640). |

## CLI Tools

| Tool | Purpose |
|------|---------|
| `sdlc-tool stage-query --issue-number N` | Query current pipeline state |
| `sdlc-tool stage-marker --stage S --status X` | Mark stage progress |
| `sdlc-tool verdict record --stage S --verdict V` | Record critique/review verdict |
| `sdlc-tool dispatch record --skill /do-X` | Record a dispatch event |
| `sdlc-tool next-skill --issue-number N` | Get next dispatch decision |
| `sdlc-tool meta-set --key K --value V` | Set a whitelisted _meta key |
| `sdlc-tool session-ensure --issue-number N` | Ensure a PM session exists |

## See Also

- `agent/sdlc_router.py` — canonical dispatch algorithm
- `agent/pipeline_graph.py` — pipeline stage edges
- `agent/pipeline_state.py` — stage state machine
- `tools/sdlc_stage_query.py` — enriched query and `_compute_meta()`
- `tools/sdlc_meta_set.py` — whitelisted metadata writes
- `docs/sdlc/` — per-stage skill addenda
- `.claude/skills/sdlc/SKILL.md` — runtime routing instructions
