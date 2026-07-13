---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2026
last_comment_id:
---

# SDLC Fork/Supervisor Hardening (umbrella)

## Problem

Every supervised `/do-sdlc` run in the 2026-07-13 batch (8 lanes) hit the same
family of fork-vs-supervisor failures. A local supervisor spawns each pipeline
stage as a subagent (a "fork"). Those forks repeatedly race the supervisor's
gates, self-lock against the supervisor's own issue lease, strand the pipeline
on notifications a fork can never receive, and expose router-routing gaps that
route unreviewed PRs toward `/do-merge`. Every lane eventually shipped correct
results on main — but only because a human supervisor reconciled ground truth
after each race. Unattended, any one of these deadlocks or ships-without-review.

This umbrella collects five separately-filed instances of the same 2026-07-13
forensics into ONE plan, ONE PR, and (mirroring the #1897 umbrella) closes the
four sibling issues as subsumed while the anchor issue **#2026** stays the
durable home for future fork-supervision instances.

**Current behavior (the five instances):**

- **#2026 (anchor) — lease churn + gate races.** The ~300s issue-lock TTL
  (`ISSUE_LOCK_TTL_SECONDS`, `models/session_lifecycle.py:791`) lapses during
  every long stage because nothing renews it mid-stage; forks re-mint fresh
  `run_id`s and self-contend the supervisor's live lock with `ISSUE_LOCKED`;
  MERGE can fire from an actor that never held the operative lease. Prose
  `--reuse-run-id` inheritance instructions are routinely ignored by the nested
  skill fork (three consecutive failed builds on the #2020 lane).
- **#2051 — phantom-wait.** `/do-build` and `/do-merge` stage forks background a
  builder child or a full `pytest` run, then end the turn "waiting for a
  completion event" that never arrives for a stopped fork. 4+ stalls across 3
  lanes in one batch.
- **#2062 — router verdict gates.** `REVIEW=completed` with no recorded verdict
  falls through router rows 8c/8d/9 and misroutes to row 10 `/do-merge`; plus
  two sibling gaps — `/do-pr-review` can post a GitHub APPROVED yet skip the
  substrate `verdict record`, and `_review_verdict_is_stale` disagrees with the
  merge predicate's head_sha check, creating a router↔predicate oscillation loop.
- **#2049 — NEEDS-REVISION deadlock.** The router keeps re-dispatching `/do-plan`
  on a stale `NEEDS REVISION` verdict after a plan revision, ignoring
  `revision_applied`. Recurred on two lanes despite the #1760 convergence latch
  that was supposed to fix exactly this.
- **#2022 — docs-stage tool-less wedge.** Docs children were spawned with a
  tool-less `documenter` agent type (no Bash), so a docs task that begins with
  git commands emits the command as plain text with zero tool calls. **Root
  cause is genuinely separate** from the fork/lease/router mechanisms (an
  agent-type misconfiguration); included here for batch-closure convenience and
  scoped down to a confirm-and-guard workstream — see WS5.

**Desired outcome:**

A supervised `/do-sdlc` run drives PLAN→…→MERGE with no manual lease revival, no
self-lock recovery cycles, no phantom-wait resumes, no router-repair of stage
markers, and no tool-less docs wedge. The issue lease is held continuously by
one owner across the whole run; stage forks inherit that identity by
construction rather than by prose; every stage that backgrounds work polls it to
completion in-turn; and the router refuses to advance REVIEW/MERGE without a
fresh recorded verdict.

## Freshness Check

**Baseline commit:** `fa7b93f1a470e5394c56fc47215187fe3213e04f`
**Issue filed at:** #2026 2026-07-11T08:28:33Z (siblings #2049/#2051 2026-07-13; #2062 2026-07-13)
**Disposition:** Minor drift

**File:line references re-verified (against baseline):**
- `models/session_lifecycle.py:791` — `ISSUE_LOCK_TTL_SECONDS = int(os.environ.get("ISSUE_LOCK_TTL_SECONDS", "300"))` — holds. `touch_issue_lock` (line 869) acquires/renews/peeks; only `stage-marker`, `dispatch record`, `session-ensure`, `verdict record` renew the lease.
- `tools/sdlc_next_skill.py:360-395` — `next-skill` peeks the lock only (`peek=True`), never renews — confirmed. So a long dispatched stage that makes zero `sdlc-tool` writes has nothing renewing its lease.
- `agent/sdlc_router.py:1207` — `_rule_ready_to_merge` (row 10) checks only `_stages_completed([...,"REVIEW","DOCS"])`, with **no** `REVIEW_APPROVED` verdict gate, unlike row 9 `_rule_review_approved_docs_not_done` (line 1191) which requires `REVIEW_APPROVED`. Confirmed.
- `agent/sdlc_router.py:1097` (row 8c) requires `REVIEW == in_progress`; `agent/sdlc_router.py:1133` (row 8d) requires `PATCH == completed` AND `last_dispatched_skill == /do-pr-review`. The observed #1897 state (`REVIEW=completed`, `DOCS=completed`, `PATCH=pending`, no verdict, `last=/do-build`) is owned by none of 8c/8d/9 → falls to row 10. Confirmed.
- `agent/sdlc_router.py:886` — `_review_verdict_is_stale` compares `recorded_at` vs the latest `/do-patch` dispatch timestamp (timing only). `tools/merge_predicate.py:553-564` checks the `REVIEW_CONTEXT head_sha=` trailer against the PR head commit. The two use different freshness definitions — confirmed disagreement.
- `.claude/agents/documentarian.md` — `tools: ['*']` (Bash-capable). `documenter` is **absent** from `.claude/agents/` (present only in stale `.claude/worktrees/*` copies); `docs/features/subagent-roster.md` records the stub agents (incl. `documenter`) were deleted as dead weight. Confirmed.

**Cited sibling issues/PRs re-checked:**
- #1760 — closed; its convergence latch (`revision_applied_at`) shipped in commit `30fbebb6` (PR #2029/#2033). #2049 shows it did not hold on #1925/#1968 — WS4 must diagnose why before adding code.
- #1932 — router verdict-gate fixes (rows 8d/9 APPROVED gate) already landed; WS3 extends the same pattern to row 10.
- #2028/#2042/#2043 — `AgentSession.is_ledger` landed (commit `51473b9f`) so the live worker skips `sdlc-local-{N}` anchors. This retires the "was it the worker or a fork?" ambiguity in #2026's early comments: the 2026-07-12/13 recurrences ran with workers DOWN, so every twin was a fork lineage. The lease-churn and gate-race mechanisms remain regardless of actor.

**Commits on main since issue was filed (touching referenced files):**
- `30fbebb6` fix(sdlc-router): dispatch precedence, convergence latch, outcome-verified advance (#2029/#2033) — landed the #1760 latch WS4 must re-examine.
- `51473b9f` Non-executable-ledger flag for CLI-created sdlc-local anchors (#2043) — removed worker-pickup as a co-driver; sharpens #2026 to a pure fork-vs-supervisor problem.

**Active plans in `docs/plans/` overlapping this area:** none open (the closed `docs/plans/completed/xdist-test-isolation-flakes.md` is the #1897 umbrella whose structure this plan mirrors; `docs/plans/sdlc-1111.md` is unrelated pipeline work).

**Notes:** All five issues are current; the only drift is that two enabling fixes (#2033 latch, #2043 is_ledger) landed since the anchor was filed, which narrows rather than invalidates the scope.

## Prior Art

- **#1915 (closed)**: "do-sdlc/do-build fork: background exits strand pipelines" — closed, but the phantom-wait (#2051) recurs. Its closure changed the parallel-task convention (foreground `run_in_background: false`, `do-build/WORKFLOW.md`) but did not bar a stage fork from ending its turn on a wait; WS2 closes that residual.
- **#1760 (closed)**: "PLAN↔CRITIQUE router never converges to BUILD" — shipped the `revision_applied_at` convergence latch (#2033). #2049 is its recurrence; WS4 diagnoses the gap.
- **#1932 (closed, PR #1941)**: router verdict gates — added the row 9 APPROVED gate and row 8d crash-recovery row. WS3 is the row-10 mirror of that pattern.
- **#1687 (closed)**: fixed the REVIEW `in_progress` empty-verdict analog (row 8c). WS3 extends coverage to the `completed`-marker no-verdict state.
- **#2003 / #1954 (closed)**: `run_id` minting via `session-ensure` and the issue-level ownership lock. WS1 builds the supervised-mode handoff on top of this lease.
- **#2028 / #2043 (closed)**: `is_ledger` worker-pickup fix — removes the worker as a pipeline co-driver, isolating #2026 to fork-vs-supervisor.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1915 (fork background-exit) | Made parallel build tasks run foreground; documented "a fork has one turn" | Documented the hazard in `do-build/WORKFLOW.md` but did not bake a synchronous-poll mandate into `/do-build` and `/do-merge` bodies; a stage fork can still end its turn on a background `pytest`/monitor wait (#2051). |
| #1760 / #2033 (convergence latch) | Added `revision_applied_at` event-scoped timestamp + `_critique_verdict_is_stale` latch | The latch is inert when `revision_applied_at` is absent/unparseable (fails safe to timestamp-only staleness), and the boolean-only `revision_applied` path is not itself treated as verdict-invalidating; #2049 recurred on lanes where the latch never engaged. WS4 must find which branch was taken before adding code. |
| #1932 (verdict gates) | Gated rows 8d/9 on a recorded APPROVED verdict | Left row 10 `_rule_ready_to_merge` ungated — it still trusts `REVIEW==completed` alone, so the no-verdict state that 8c/8d/9 now correctly step aside from falls straight through to `/do-merge` (#2062). |
| Prose `--reuse-run-id` instructions (SKILL.md Step 1.5) | Told forks to inherit the supervisor's `run_id` | An LLM fork routinely ignores the prose and runs a bare `session-ensure`, self-locking against the supervisor. Inheritance must be structural (a signal the fork can't skip), not advisory (#2026). |

**Root cause pattern:** each prior fix hardened one gate or one skill body but left the *fork's identity and turn model* implicit. The fork mints its own lease, has no continuous renewer, and has a single non-resumable turn — so any gate that assumes "one owner, live across the stage, resumable" is one race away from breaking. This umbrella makes fork identity and the turn contract explicit.

## Data Flow

1. **Entry point**: a local supervisor invokes `/do-sdlc {issue}`; the router (`.claude/skills/sdlc/SKILL.md` → `sdlc-tool next-skill` → `agent.sdlc_router.decide_next_dispatch`) reads pipeline state and returns one skill.
2. **Lease**: the supervisor's first `sdlc-tool session-ensure` mints a `run_id` and SET-NX-EX acquires the issue lock (`touch_issue_lock`, TTL `ISSUE_LOCK_TTL_SECONDS`). Today each stage fork re-runs `session-ensure` → contests the lock → self-blocks or re-mints.
3. **Stage dispatch**: the supervisor records the dispatch (`dispatch record`, which renews the lease) then spawns the stage fork. During the fork's 8–20 min of work, **no `sdlc-tool` write occurs**, so the lease is not renewed and lapses.
4. **Stage completion**: the fork writes markers/verdicts (`stage-marker`, `verdict record`) — but only if it did not phantom-wait first, and only if it holds a valid `run_id`.
5. **Gate**: `next-skill` peeks the lock (never renews) and evaluates rows/guards; `/do-merge` additionally consults `tools/merge_predicate`.
6. **Output**: a merged PR + migrated plan, or a deadlock/strand requiring supervisor reconciliation.

The fixes touch steps 2–5: continuous single-owner lease (WS1), in-turn synchronous work at step 3 (WS2), verdict-gated routing at step 5 (WS3), staleness-aware routing at steps 4–5 (WS4), and correct agent-type/tool availability for the docs stage at step 4 (WS5).

## Architectural Impact

- **New dependencies**: none (no new packages). All work is inside `agent/sdlc_router.py`, `models/session_lifecycle.py`, the `tools/sdlc_*` CLIs, and the `.claude/skills-global/` skill bodies.
- **Interface changes**: potentially one new `sdlc-tool` subcommand for the lease handoff (WS1) and/or one new `--supervised-run-id` flag; a possible refusal path on `stage-marker --stage REVIEW --status completed` when no verdict is readable (WS3c). Router rule set gains one row (WS3b) and one gate refinement (WS3a/d).
- **Coupling**: decreases fork↔supervisor coupling by making identity explicit rather than inferred through lock contention.
- **Data ownership**: the issue lease becomes single-owner (the supervisor) for the whole run instead of rotating per stage.
- **Reversibility**: each workstream is independently revertable; the router changes are guarded by the parity test and unit rows.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (workstream sequencing, the WS1 handoff-mechanism decision, the WS4 diagnosis gate)
- Review rounds: 2+ (router logic and lease semantics both warrant a careful review pass)

Large because it spans two subsystems (router + lease) and four skill bodies, and because WS1's handoff mechanism and WS4's latch diagnosis each carry a real design decision that critique should weigh.

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are to
in-repo Python and skill markdown; the SDLC tooling resolves via `sdlc-tool`
(`AI_REPO_ROOT` default `~/src/ai`).

## Solution

### Key Elements

- **WS1 — Single-owner lease + first-class fork handoff (#2026 core)**: the
  supervisor mints one `run_id`, holds the issue lock for the whole run, and
  renews it continuously; stage forks inherit that identity through an explicit
  signal instead of re-minting. MERGE becomes single-owner.
- **WS2 — In-turn synchronous stage work (#2051)**: `/do-build` and `/do-merge`
  bodies prescribe running backgrounded work (builder children, the full pytest
  suite) and polling it to completion within the same turn, recording the result
  before the turn ends.
- **WS3 — Verdict-gated routing + recording enforcement (#2062)**: row 10 gains
  an APPROVED-verdict gate; a new recovery row owns the `REVIEW=completed` +
  no-verdict state; the REVIEW completion marker becomes unwritable without a
  readable verdict; the router learns the head_sha staleness signal so it agrees
  with the merge predicate.
- **WS4 — Robust revision invalidation (#2049)**: the router treats a plan
  revision as invalidating the stale `NEEDS REVISION` verdict and routes to
  re-critique, covering both the timestamped and boolean-only paths, without
  re-staling a clean `READY TO BUILD` verdict.
- **WS5 — Docs-stage tool availability guard (#2022, scoped)**: confirm no code
  path selects a tool-less agent type for docs work (pin to `documentarian`),
  and add a guard that flags an agent whose final message is a bare shell
  command with zero tool calls as a tool-availability mismatch rather than a
  normal completion.

### Flow

Supervised run → `session-ensure` (one run_id, lock held) → per stage: `dispatch record` (renews lease) → **handoff signal set** → spawn fork (inherits run_id, skips `session-ensure`) → fork does work **in-turn** → writes verdict+marker (verdict-gated) → supervisor reclaims/renews → `next-skill` (verdict- and head_sha-aware) → … → MERGE (single-owner) → done, no manual reconciliation.

### Technical Approach

**WS1 — Single-owner lease + fork handoff.** Two candidate mechanisms; the plan
recommends (a) and treats (b) as the fallback the critique may prefer:

- **(a) Supervised-run signal (recommended).** The supervisor writes its
  verified `run_id` to a signal the stage fork reads at spawn — the strongest
  available channel that survives a fork boundary (a run-scoped file in the
  worktree, e.g. `.worktrees/{slug}/.sdlc-run`, and/or a `_meta` key on the PM
  session read via `sdlc-tool`). When the signal is present, the stage skill
  **skips `session-ensure` entirely** and passes that `run_id` to every
  `--run-id` write. The supervisor remains the sole lock owner and renewer; the
  fork never contests the lock. This makes inheritance structural — the fork has
  no code path that re-mints.
- **(b) First-class release-before-spawn handoff (fallback).** Codify the proven
  interim workaround as an `sdlc-tool session-handoff` subcommand: record the
  dispatch under the supervisor lock, release the lock (compare-and-delete), and
  return a handoff token; the spawned fork acquires the now-free lock and mints
  its own `run_id`; the supervisor reclaims the new `run_id` via the record
  mirror. This keeps per-stage minting but makes the release/reclaim atomic and
  scripted instead of prose.
- **Lease auto-renewal (both mechanisms).** So identity stops rotating under long
  stages: (1) raise the `ISSUE_LOCK_TTL_SECONDS` **default** to comfortably
  exceed observed p99 stage duration (review runs 8–12 min), keeping it
  env-overridable and marking it provisional/tunable; and (2) add a
  supervisor-driven renewal touch on a named interval
  (`SDLC_LEASE_RENEW_INTERVAL_SECONDS`, provisional/tunable) so a live supervisor
  keeps the lease warm between stages. A pure TTL bump alone risks a genuinely
  dead owner holding the lock longer; pairing the bump with an active renewer
  (owned by the live supervisor) keeps the dead-owner ceiling bounded by the TTL
  while eliminating mid-stage lapse for live runs. **Full cutover:** once the
  handoff is first-class, remove the SKILL.md Step 1.5 prose that instructs forks
  to juggle `--reuse-run-id`; describe only the new signal-based inheritance.
- **Single-owner MERGE.** `/do-merge` (and/or the merge predicate) refuses unless
  the merge actor holds the current issue lease AND its `run_id` matches the run
  that recorded the operative REVIEW verdict — so a fork that never held the
  lease can no longer merge past a blocked gate.

**WS2 — Phantom-wait elimination.** Edit `.claude/skills-global/do-build/` and
`.claude/skills-global/do-merge/` bodies to prescribe the proven synchronous
pattern in positive terms: *run the suite / builder work and poll it to
completion within this turn; verify a live producer exists before ever waiting on
one; record the result in-turn before ending the turn.* Propagate the same brief
into any child the stage skill spawns. Encourage what TO DO; do not enumerate the
space of bad waits. Changes propagate to every machine via the `/update`
hardlink sync.

**WS3 — Verdict-gated routing + recording enforcement.**
- (a) Add a recorded-APPROVED gate to `_rule_ready_to_merge` (row 10) mirroring
  row 9 — step aside when no `REVIEW_APPROVED` verdict is recorded.
- (b) Add a recovery row (ordered before row 9/10; the natural home is beside
  8d) owning `REVIEW==completed` + no recorded verdict + no `/do-pr-review` in
  dispatch history → dispatch `/do-pr-review`. This is the state currently owned
  by nobody (8d's `PATCH==completed` + `last==/do-pr-review` preconditions
  exclude the observed `PATCH=pending`, `last=/do-build` state). Loop-bound by G4.
- (c) Close the recording hole: make the REVIEW `completed` marker unwritable
  without a readable verdict — either `stage-marker --stage REVIEW --status
  completed` refuses (named error) when `verdict get --stage REVIEW` is empty, or
  the verdict record and completion marker are co-written as one atomic block in
  the tool. This makes "post GitHub APPROVED but skip the substrate write"
  impossible by construction, backing the existing do-pr-review Step 5 mandate.
- (d) Teach the router the head_sha staleness signal: extend the review-staleness
  check so a recorded verdict whose `head_sha` trailer ≠ the current PR head is
  treated as stale → route to `/do-pr-review` at the new head, instead of G6
  fast-pathing to `/do-merge`. Router and `tools/merge_predicate` (which already
  checks the `REVIEW_CONTEXT head_sha=` trailer, `merge_predicate.py:553-564`)
  then agree on "fresh," ending the post-approval-commit oscillation loop.

**WS4 — Robust revision invalidation.** First **diagnose** (a code-read spike,
not a blind edit) which branch #1925/#1968 took: was `revision_applied_at`
absent (latch inert → timestamp-only staleness) or present-but-not-consulted, and
did `_rule_critique_needs_revision`'s `_critique_verdict_is_stale` step-aside fire
or not? Then make revision invalidation robust: a `NEEDS REVISION` verdict is
consumed once the plan has been revised since it was recorded — via
`revision_applied_at` when present, falling back to the boolean `revision_applied`
(treated as "revised since" when the timestamp is missing) so the latch is never
silently inert. Preserve the inverse #1760 guarantee: a clean `READY TO BUILD`
verdict must not be re-staled by a no-op notes edit (keep the artifact-hash /
`revision_applied_at` guard that distinguishes the settle-and-build revision from
a later unrelated `/do-plan`).

**WS5 — Docs-stage tool availability guard (scoped, separate root cause).**
- (a) Grep the fork/docs spawn paths (`do-sdlc`, `do-build/WORKFLOW.md`,
  `do-docs`, `.claude/agents/`) for any residual selection of a tool-less agent
  type for docs work; ensure docs tasks that need a shell route to
  `documentarian` (`tools: ['*']`). The acute `documenter` type is already gone
  from the roster, so this is a confirm-and-pin, not a rewrite.
- (b) Add a guard (harness- or skill-level) that treats an agent whose final
  message parses as a bare shell command with **zero tool calls** as a
  tool-availability mismatch — surfaced/flagged, never reported as a normal
  completion. This is the durable defense that catches any future tool-less
  spawn regardless of agent name.
- The plan states explicitly that #2022 is **not** a fork/lease/router bug; it is
  bundled here only for one-PR batch closure.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `models/session_lifecycle.py` lock helpers already fail-open on Redis errors — add/keep a test asserting the renewal path logs and fails open (does not crash the supervisor) when Redis is unreachable.
- [ ] The WS3c `stage-marker` refusal path must assert an observable named error (not a silent swallow) when a REVIEW `completed` marker is attempted with no readable verdict.
- [ ] Router staleness helpers (`_review_verdict_is_stale`, `_critique_verdict_is_stale`) already fail-safe to "not stale" on parse errors — keep tests asserting that behavior after the WS3d/WS4 edits.

### Empty/Invalid Input Handling
- [ ] WS1: assert a stage fork with a missing/empty supervised-run signal fails safe (blocks with a clear reason) rather than silently re-minting a colliding run_id.
- [ ] WS3d: assert a verdict with an absent/malformed `head_sha` trailer is treated as stale (routes to re-review), never as fresh.
- [ ] WS4: assert absent `revision_applied` AND absent `revision_applied_at` leaves prior behavior unchanged (no free pass to BUILD).

### Error State Rendering
- [ ] WS5b: assert the zero-tool-call bare-command guard emits a visible mismatch signal that a supervisor/log can see, not a "completed" notice.
- [ ] WS1 single-owner MERGE: assert a merge attempt from a non-lease-holding run surfaces a clear refusal reason rather than merging.

## Test Impact

- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE: the parity test cross-checks the SKILL.md dispatch-row table against `agent.sdlc_router` rules. WS3b adds a row and WS3a/d change gates, so both SKILL.md and this test's expectations must move together.
- [ ] `tests/unit/test_sdlc_router.py` — UPDATE/ADD: new cases for row 10 APPROVED gate (WS3a), the new no-verdict recovery row (WS3b), head_sha staleness routing (WS3d), and the #2049 revision-invalidation cases (WS4: critique→NEEDS REVISION→revision→assert next dispatch is `/do-plan-critique`, twice; plus the inverse #1760 no-op-edit case).
- [ ] `tests/unit/test_architectural_constraints.py` — CHECK: `agent/sdlc_router.py` must stay import-free of `tools/`; ensure WS3d/WS4 edits don't introduce a `tools` import (head_sha comes from context assembly in `tools/sdlc_next_skill.py`, mirroring G8).
- [ ] session-lifecycle lock tests (locate under `tests/unit/` for `touch_issue_lock`/`release_issue_lock`) — UPDATE: TTL default bump + renewal-interval constant; assert continuous renewal keeps the lease live and that compare-and-delete release still frees immediately.
- [ ] `tools/sdlc_stage_marker.py` tests — ADD: WS3c refusal when REVIEW `completed` is attempted with no readable verdict.
- [ ] `tools/sdlc_session_ensure.py` tests — UPDATE: WS1 supervised-signal path (fork skips `session-ensure` when the signal is present) and/or the new `session-handoff` subcommand.
- [ ] do-build / do-merge / do-pr-review / do-docs skill-body tests (if any assert body content) — UPDATE for the WS2 synchronous mandate and WS5 agent-type pin.
- [ ] `tests/integration/` SDLC pipeline tests — CHECK: any end-to-end that asserts a merge path may need the single-owner-MERGE precondition satisfied.

If a listed test does not exist yet, it is created as part of the owning workstream (regression coverage is a completion requirement per the acceptance criteria).

## Rabbit Holes

- **Rebuilding fork identity as a full session-handoff protocol with tokens,
  leases, and renegotiation.** Scope WS1 to the single-owner + inherited-signal
  shape; do not design a general distributed-lock library.
- **Making the whole pipeline resumable so a fork *can* receive a notification
  (#2051 harness path).** Tempting, but the reliable fix is in-turn synchronous
  work in the two skill bodies. Do not re-architect the fork turn model.
- **Chasing the ~26k-token cost of the #2022 wedged spawn.** That is a
  consequence of a large injected context re-serialized on a tool-call failure,
  not a separate defect; the guard (WS5b) makes the wedge visible — cost analysis
  is out of scope.
- **Tuning the TTL / renewal constants to a "perfect" value.** Ship sane
  provisional defaults with a grain-of-salt comment and env overrides; do not
  benchmark stage-duration distributions in this plan.
- **Retrofitting every stage skill (not just build/merge) with the synchronous
  mandate.** #2051's evidence is build/merge; extend only if WS2 review surfaces
  a concrete third stage that phantom-waits.

## Risks

### Risk 1: TTL bump masks a genuinely dead supervisor holding the lock longer
**Impact:** A crashed owner now blocks the issue for the (longer) TTL before self-healing.
**Mitigation:** Pair the bump with an active renewer owned by the *live* supervisor, and keep the `orphaned_lock` self-heal on the TTL. The dead-owner ceiling is the TTL (bounded, env-tunable); only live runs benefit from the renewer. Do not set the TTL absurdly high — pick a value modestly above p99 stage duration.

### Risk 2: WS3c marker-refusal deadlocks a legitimate crash-recovery path
**Impact:** If REVIEW can never be marked completed without a verdict, a partial crash could strand REVIEW.
**Mitigation:** WS3b's recovery row owns exactly that state (no verdict → re-dispatch `/do-pr-review`), so the refusal redirects to re-review rather than deadlocking; G4 bounds the loop. Test the crash→recover path explicitly.

### Risk 3: WS1 supervised-signal is skipped by a fork the same way prose was ignored
**Impact:** If the signal is "advisory," we've reproduced the failure we're fixing.
**Mitigation:** The signal must be structural — the stage skill's `session-ensure` step is *conditional on the signal's absence*, so a fork that reads the signal has no code path that re-mints. Assert this with a test that spawns a stage flow with the signal set and confirms no fresh `run_id` is minted.

### Risk 4: Router parity test drift
**Impact:** Adding a row without updating SKILL.md breaks `test_sdlc_skill_md_parity.py`.
**Mitigation:** Treat SKILL.md row-table edits and `agent/sdlc_router.py` rule edits as one atomic change; run the parity test as a gate.

## Race Conditions

### Race 1: Lease lapse mid-stage (the #2026 core race)
**Location:** `models/session_lifecycle.py` (`touch_issue_lock`, TTL) × `tools/sdlc_next_skill.py:360-395` (peek-only).
**Trigger:** A stage fork runs longer than `ISSUE_LOCK_TTL_SECONDS` with no intervening `sdlc-tool` write.
**Data prerequisite:** The issue lock payload must carry the live owner `run_id` for the whole stage.
**State prerequisite:** The lock must remain owned by the supervisor's run for the run's duration.
**Mitigation:** Continuous supervisor-driven renewal (WS1) + TTL default that exceeds p99 stage duration; the lease no longer depends on stage-boundary writes.

### Race 2: Fork merges past a blocked gate
**Location:** `/do-merge` × `tools/merge_predicate` × the issue lease.
**Trigger:** A parallel fork/lineage merges the PR while the supervisor's gate is still blocked.
**Data prerequisite:** The operative REVIEW verdict's `run_id` and the current lease owner.
**State prerequisite:** Only the lease-holding run whose `run_id` recorded the fresh APPROVED verdict may merge.
**Mitigation:** Single-owner MERGE (WS1) — verify lease + run_id match before merging; head_sha-fresh verdict required (WS3d).

### Race 3: Verdict record vs completion marker
**Location:** `/do-pr-review` × `tools/sdlc_stage_marker.py` × `tools/sdlc_verdict.py`.
**Trigger:** A fork posts the GitHub review, then crashes/skips before `verdict record`, but the completion marker is already written.
**Data prerequisite:** A readable substrate verdict must exist before REVIEW is `completed`.
**State prerequisite:** Marker-completed ⇒ verdict-readable (invariant).
**Mitigation:** WS3c — co-write or refuse; the marker cannot precede the verdict.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2028] Worker-vs-fork pickup ambiguity is already resolved by the `is_ledger` fix (#2043); this umbrella assumes workers-down fork lineage and does not re-litigate worker co-driving.
- Nothing else deferred — the five instances proven in the 2026-07-13 forensics are all in scope for this plan. Future fork-supervision instances are logged under the anchor issue #2026 as they are observed (planning a fix for an un-observed instance is not possible), mirroring the #1897 umbrella's collection model.

## Update System

- **Skill-body changes propagate via the `/update` hardlink sync.** WS2 (do-build,
  do-merge) and WS5a (do-docs / agent-type pin) edit files under
  `.claude/skills-global/`, which `scripts/update/hardlinks.py` `sync_claude_dirs()`
  hardlinks into `~/.claude/skills/` on every machine. No registration step and
  no update-script code change is required — the sync already covers these
  directories.
- **New named constants** (`ISSUE_LOCK_TTL_SECONDS` default change,
  `SDLC_LEASE_RENEW_INTERVAL_SECONDS`, any WS3/WS1 interval) are env-overridable
  via existing `os.environ` reads / `config/settings.py` conventions; document
  them where the timeout catalog lives (`docs/features/config-timeout-catalog.md`)
  if promoted, otherwise name them locally with a provisional/tunable comment.
  No `.env` propagation is needed because defaults ship in code.
- **No `/update` skill (`.claude/skills/update/`) changes** beyond the automatic
  hardlink sync. `sdlc-tool` is already on PATH via the repo; a new subcommand
  (if WS1 fallback (b) is chosen) is picked up by the existing `sdlc-tool`
  wrapper `ALLOWED_SUBCOMMANDS` list edit — an in-repo change, not an update-skill
  change.

## Agent Integration

- **`sdlc-tool` surface**: if WS1 adds a `session-handoff` subcommand or a
  `--supervised-run-id` flag, the `sdlc-tool` bash wrapper's `ALLOWED_SUBCOMMANDS`
  and `tools/sdlc_session_ensure.py` must expose it; the SKILL.md Step 1.5 body
  must be rewritten to describe signal-based inheritance and the prose
  `--reuse-run-id` juggling removed (full cutover).
- **Router changes are internal**: WS3/WS4 edits live in `agent/sdlc_router.py`
  and are consumed by the agent exclusively through `sdlc-tool next-skill`
  (`tools/sdlc_next_skill.py`) — no new agent-facing surface. The head_sha signal
  (WS3d) is assembled in `tools/sdlc_next_skill.py` context (mirroring G8's
  live-verification seam) and read by a pure router rule, preserving the
  `agent/sdlc_router.py` import-free-of-`tools/` constraint.
- **No bridge (`bridge/telegram_bridge.py`) change**: this is entirely
  pipeline-internal; the agent reaches all of it through the existing `/do-sdlc`
  → `sdlc-tool` path.
- **Integration verification**: a supervised `/do-sdlc` dry-run on a scratch
  issue that exercises PLAN→…→MERGE without manual lease revival or synchronous
  re-dispatch is the end-to-end acceptance signal (see Success Criteria).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-pipeline.md` — document the single-owner lease + supervised-run signal, the row-10 verdict gate + new recovery row, the head_sha staleness signal, and the revision-invalidation fix. Cross-reference the anchor #2026.
- [ ] Update `docs/features/config-timeout-catalog.md` — add `ISSUE_LOCK_TTL_SECONDS` (new default) and `SDLC_LEASE_RENEW_INTERVAL_SECONDS` with their provisional/tunable notes.
- [ ] Update `.claude/skills/sdlc/SKILL.md` — Step 1.5 (signal-based inheritance, remove `--reuse-run-id` prose), the dispatch-row table (WS3b new row), and the guard/row notes for WS3a/d and WS4. Keep in lockstep with `test_sdlc_skill_md_parity.py`.
- [ ] Update `docs/sdlc/do-build.md`, `docs/sdlc/do-merge.md`, `docs/sdlc/do-pr-review.md` addenda if they carry repo-specific verdict/marker/synchronous guidance affected by WS2/WS3.

### Inline Documentation
- [ ] Grain-of-salt comments on every new named constant marking it provisional/tunable and env-overridable.
- [ ] Docstrings on the new router row and refined staleness helpers, kept in sync with the parity test's docstring-based row-state cross-check.

## Success Criteria

- [ ] A supervised `/do-sdlc` run on a scratch issue completes PLAN→…→MERGE with **zero** manual lease revivals, self-lock recoveries, or `--reuse-run-id` juggling.
- [ ] Stage forks make no bare `session-ensure` call when the supervised-run signal is present (grep/test confirms the conditional skip).
- [ ] `/do-build` and `/do-merge` complete build/merge without a manual synchronous re-dispatch; the shipped skill bodies carry the in-turn synchronous mandate.
- [ ] Replaying the #1897 observed state (`REVIEW=completed`, `DOCS=completed`, `PATCH=pending`, no verdict, `last=/do-build`) against `decide_next_dispatch` routes to `/do-pr-review`, not `/do-merge`.
- [ ] The REVIEW `completed` marker cannot be written without a readable verdict (WS3c test red-then-green).
- [ ] Router and merge predicate agree on head_sha freshness: a post-approval commit routes to re-review, not merge (WS3d test).
- [ ] critique→NEEDS REVISION→revision routes to `/do-plan-critique` on the next turn, twice in a row; a clean READY-TO-BUILD verdict is not re-staled by a no-op notes edit (WS4 tests).
- [ ] A merge attempt from a run that does not hold the lease / does not match the operative REVIEW `run_id` is refused (single-owner MERGE test).
- [ ] Docs work routes to a Bash-capable agent; a zero-tool-call bare-command final is flagged as a tool-availability mismatch (WS5 tests).
- [ ] Tests pass (`/do-test`), including updated `test_sdlc_skill_md_parity.py` and `test_sdlc_router.py`.
- [ ] Documentation updated (`/do-docs`).
- [ ] All five sibling issues referenced: PR body closes anchor #2026 and subsumes #2051, #2062, #2049, #2022 with pointers.

## Team Orchestration

The lead agent orchestrates; it never builds directly. Builder + validator pairs per workstream, plus a documentarian.

### Team Members

- **Builder (lease-handoff)** — Name: `lease-builder` — Role: WS1 single-owner lease, supervised-run signal, renewal, single-owner MERGE — Agent Type: builder — Domain: async/concurrency (paste DOMAIN_FRAMING async rules) — Resume: true
- **Builder (skill-bodies)** — Name: `skills-builder` — Role: WS2 do-build/do-merge synchronous mandate + WS5a agent-type pin — Agent Type: builder — Resume: true
- **Builder (router)** — Name: `router-builder` — Role: WS3 verdict gates + recovery row + head_sha signal, WS4 revision invalidation — Agent Type: builder — Domain: debugging (router state machine) — Resume: true
- **Builder (guard)** — Name: `guard-builder` — Role: WS3c stage-marker refusal + WS5b zero-tool-call guard — Agent Type: builder — Resume: true
- **Validator (pipeline)** — Name: `pipeline-validator` — Role: verify router/lease behavior against Success Criteria — Agent Type: validator — Resume: true
- **Documentarian** — Name: `sdlc-doc` — Role: WS docs above — Agent Type: documentarian — Resume: true

### Available Agent Types

Tier 1: `builder`, `validator`, `code-reviewer`, `documentarian`. Domain work
gets a `Domain:` tag + the matching `DOMAIN_FRAMING.md` rules pasted into the
task. No standing specialist pool.

## Step by Step Tasks

### 1. WS4 diagnosis spike (code-read, blocking gate for router work)
- **Task ID**: spike-revision-latch
- **Depends On**: none
- **Assigned To**: router-builder
- **Agent Type**: Explore
- **Parallel**: true
- Read `agent/sdlc_router.py` `_critique_verdict_is_stale` + `_rule_critique_needs_revision` and the #2033 latch; determine which branch #1925/#1968 took (timestamp absent vs boolean-only vs step-aside not firing). Return the finding — no code.

### 2. WS1 — Single-owner lease + fork handoff
- **Task ID**: build-lease
- **Depends On**: none
- **Validates**: `tools/sdlc_session_ensure.py` tests, session-lifecycle lock tests
- **Assigned To**: lease-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement the supervised-run signal (recommended) or `session-handoff` (fallback); make the stage `session-ensure` conditional on the signal; add lease renewal + provisional/tunable constants; implement single-owner MERGE; cut over SKILL.md Step 1.5 prose.

### 3. WS2 + WS5a — Skill-body mandates
- **Task ID**: build-skills
- **Depends On**: none
- **Validates**: skill-body tests (if present)
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- Bake the in-turn synchronous mandate into do-build/do-merge; pin docs work to `documentarian`.

### 4. WS3 + WS4 — Router gates, recovery row, head_sha, revision invalidation
- **Task ID**: build-router
- **Depends On**: spike-revision-latch
- **Validates**: `tests/unit/test_sdlc_router.py`, `tests/unit/test_sdlc_skill_md_parity.py`, `tests/unit/test_architectural_constraints.py`
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Row 10 APPROVED gate; new no-verdict recovery row (+ SKILL.md row-table edit); head_sha staleness in the `next-skill` context seam; robust revision invalidation preserving the inverse #1760 guarantee.

### 5. WS3c + WS5b — Guards
- **Task ID**: build-guards
- **Depends On**: none
- **Validates**: `tools/sdlc_stage_marker.py` tests
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Make REVIEW `completed` unwritable without a readable verdict; add the zero-tool-call bare-command mismatch guard.

### 6. Validate all workstreams
- **Task ID**: validate-pipeline
- **Depends On**: build-lease, build-skills, build-router, build-guards
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the router unit suite, parity test, lock tests, and a supervised scratch-issue dry-run; verify every Success Criterion.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-pipeline
- **Assigned To**: sdlc-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-pipeline.md`, the timeout catalog, SKILL.md, and the `docs/sdlc/` addenda.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Full Verification table + all Success Criteria including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Router unit tests pass | `pytest tests/unit/test_sdlc_router.py -q` | exit code 0 |
| Router↔SKILL.md parity | `pytest tests/unit/test_sdlc_skill_md_parity.py -q` | exit code 0 |
| Architectural constraint holds | `pytest tests/unit/test_architectural_constraints.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Row 10 has a verdict gate (no ungated ready-to-merge) | `grep -n "REVIEW_APPROVED" agent/sdlc_router.py` | output contains REVIEW_APPROVED |
| Lease renewal constant is named + provisional | `grep -n "SDLC_LEASE_RENEW_INTERVAL_SECONDS" models/session_lifecycle.py agent/ tools/ -r` | output contains SDLC_LEASE_RENEW_INTERVAL_SECONDS |
| No residual `--reuse-run-id` juggling prose in SKILL.md Step 1.5 | `grep -c "reuse-run-id" .claude/skills/sdlc/SKILL.md` | match count == 0 |
| Docs work not pinned to a tool-less agent | `grep -rn "documenter" .claude/skills-global/ .claude/agents/` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **WS1 mechanism**: supervised-run signal (recommended, structural inheritance, supervisor holds lock whole run) vs first-class `session-handoff` release-before-spawn subcommand (proven interim, per-stage minting). Which does the team prefer as the primary, and do we ship both (signal primary, handoff as the recovery path)?
2. **TTL default**: what value modestly above observed p99 stage duration (review 8–12 min) should `ISSUE_LOCK_TTL_SECONDS` default to — e.g. ~1200–1800s — given the dead-owner-hold tradeoff, and is the active renewer enough to keep the TTL conservative?
3. **WS3c enforcement site**: refuse in `stage-marker` (named error, redirect via WS3b recovery row) vs co-write verdict+marker atomically in `verdict record`. The recovery row makes refusal safe — is refusal the cleaner invariant?
4. **WS5 guard placement**: harness-level (catches every agent type everywhere) vs skill-level (scoped to the docs stage). Harness-level is more durable but broader blast radius — acceptable?
