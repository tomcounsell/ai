---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2735
last_comment_id:
revision_applied: true
revision_applied_at: 2026-08-13T09:33:20Z
---

# SDLC Lane Identity: One Recorded Slug, Minted Once

Closes #2735 and #2718. Both are the same defect seen from two ends: **no component
records the lane's identity, so every consumer guesses it, and each one guesses
differently.**

## Problem

An SDLC lane has exactly one identity — the thing that names its branch, its worktree,
its task list, and (usually) its plan doc. Nothing in this system writes that identity
down. Five places invent it independently:

| Inventor | Shape it invents | File |
|---|---|---|
| Reflection lane pickup | `sdlc-{N}` | `reflections/sdlc_upvote_lanes.py:489` |
| Stalled-lane respawn | `sdlc-{N}` | `reflections/sdlc_progress.py:707` |
| CLI session create | `sdlc-{N}` | `tools/valor_session.py:107` |
| G8 artifact verifier | plan-filename stem | `tools/sdlc_next_skill.py:205` |
| `branch_exists` context | plan-filename stem | `tools/sdlc_next_skill.py:357` |

The first three mint `sdlc-{N}` and go on to create real branches. The last two derive
a *different* slug from a plan document, then probe for a branch under that name. When
the two disagree, the probe fails against a branch that never existed.

Worse, the plan document the last two consult is itself resolved by a guess.
`tools/_sdlc_utils.py::find_plan_path` treats **any** textual `#N` mention in **any**
plan file as evidence of ownership — including mentions inside a "Not building" No-Gos
line, which is exactly where a plan names an issue it explicitly does not own.

**Current behavior:**

Issue #2663 has no plan document. `docs/plans/session-liveness-tick-counter.md` tracks
#2716 and mentions #2663 twice, once to say it is *not* building it. Both issues
resolve to that plan:

```
$ SDLC_TARGET_REPO=~/src/ai .venv/bin/python -c \
    "from tools._sdlc_utils import find_plan_path; print(find_plan_path(2663)); print(find_plan_path(2716))"
/Users/valorengels/src/ai/docs/plans/session-liveness-tick-counter.md
/Users/valorengels/src/ai/docs/plans/session-liveness-tick-counter.md
```

Two consumers make that load-bearing:

1. **G8 wedges the lane.** `_verify_stage_artifacts_live` derives
   `slug = "session-liveness-tick-counter"` and probes
   `git ls-remote --heads origin session/session-liveness-tick-counter`. The real branch
   is `session/sdlc-2663`. The probe returns nothing, G8 declares the PATCH artifact
   unverified, and the router force-dispatches `/do-patch` — against a clean worktree
   with nothing to patch. At three dispatches the G4 oscillation cap hard-blocks the
   lane, and the recorded block names *oscillation*, not the real cause, which
   misleads whoever picks it up.

2. **`_meta` is contaminated across issues.** `sdlc-tool stage-query --issue-number 2663`
   reports `revision_applied: true` and `revision_applied_at: 2026-08-11T03:27:10Z` —
   read verbatim out of #2716's plan frontmatter. #2663 has never had a plan. Those
   flags feed router rows 4b/4c and guard G7.

A scan of `docs/plans/` (33 plans, 29 with `tracking:` frontmatter as of cycle 4) finds **309 issue
numbers with no owning plan that nonetheless resolve to one.**

The failure shape is a confident wrong answer derived from a signal that looks
authoritative and is not.

**Desired outcome:**

The lane's slug is minted exactly once, at lane start, by `ensure_session` — the one
component that runs on **every** lane-start path before any stage exists — and recorded
on the `PipelineLedger` beside the stages. Every consumer reads it.
Nothing derives it. When no slug can be resolved, checks no-op rather than probing a
guessed name.

## Freshness Check

**Baseline commit:** `0dd8e70f2`
**Issue #2735 filed at:** 2026-08-13T02:44:46Z
**Issue #2718 filed at:** 2026-08-10T14:47:23Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `tools/_sdlc_utils.py:840` — `_is_ai_repo_fallback = False` initialization — still holds.
- `tools/_sdlc_utils.py:842/846/852` — the three-step plans-dir resolution; only step 3 flips the guard flag — still holds.
- `tools/_sdlc_utils.py:879/888` — the unguarded `fallback` assignment and the never-reached suppression — still holds.
- `tools/sdlc_next_skill.py:205` — `slug = Path(plan_path).stem` — still holds.
- `tools/sdlc_next_skill.py:236` — `if pr_state != "MERGED" and not _check_branch_pushed(slug)` — still holds.
- `tools/sdlc_next_skill.py:151-163` — `_check_branch_pushed` → `git ls-remote --heads origin session/{slug}` — still holds.
- `tools/sdlc_next_skill.py:239` — warning string naming the wrong branch — still holds.
- `tools/sdlc_next_skill.py:357/367` — the second, identical derivation feeding `branch_exists` — still holds (this is the "worth checking" site #2718 flagged; confirmed as the same defect).
- `tools/sdlc_stage_query.py:487` — `getattr(session, "slug", None)` — still holds, and is dead: verified in Redis that `PipelineLedger` has no `slug` attribute for `tomcounsell/ai:{2663,2716,2735}`.
- `tools/sdlc_stage_query.py:516` — `_find_plan_path(issue_number)` feeding `plan_exists` / `revision_applied` — still holds.
- `reflections/sdlc_upvote_lanes.py:489` — `slug = f"sdlc-{issue_number}"` — still holds.
- `tools/sdlc_next_skill.py:346-349` and `tools/sdlc_stage_query.py:347-350` — the false "this repo never creates `session/sdlc-{N}`" belief — still holds, still false.
- `tests/unit/test_sdlc_next_skill.py:117-176` — `TestBranchExistsCanonicalShape` pins the false belief in three cases — still holds.

**Cited sibling issues/PRs re-checked:**

- **#2668** — still `OPEN`, `mergeable: MERGEABLE`, `mergeStateStatus: CLEAN`, `headRefName: session/sdlc-2663`. Still wedged in exactly the shape #2718 describes.
- **#1915** — closed 2026-07-08. This is the origin of the "slug identity always wins" doctrine that the false belief comments cite. Its own suggested direction #3 offered a choice: *either* add a seam for supervisor-assigned lanes *or* document that slug identity always wins. The code took the second option as a comment, while the reflections kept creating issue-derived lanes. That unreconciled split is the root of both issues here.
- **#2663** — still open; the wedged lane. Not modified by this plan; the fix unwedges it.
- **#2716** — still open; owns `docs/plans/session-liveness-tick-counter.md`.

**Commits on main since the issues were filed (touching referenced files):** none touching
`tools/_sdlc_utils.py`, `tools/sdlc_next_skill.py`, `tools/sdlc_stage_query.py`,
`agent/pipeline_ledger.py`, or `tools/sdlc_session_ensure.py`.

**Active plans in `docs/plans/` overlapping this area:**

- **`agent-session-updated-at-restamp.md` (#2660, Ready)** — its Task 3 edits
  `tools/sdlc_session_ensure.py::_acquire_run_lock_and_bind` (`:486-493`) to add
  `save(update_fields=[...])`. This plan also touches `sdlc_session_ensure.py` but
  deliberately places its write in `ensure_session`'s own body, **not** inside
  `_acquire_run_lock_and_bind`, so the two do not collide on the same lines.
- **`plan-doc-single-writer-lease.md` (#2650, Ready)** — keys a Redis lease on the slug
  derived from `docs/plans/{slug}.md`. That is the *plan-doc* slug, not the lane slug;
  the two remain distinct concepts under this plan (see Technical Approach). Read-only
  reference to `tools/sdlc_stage_query.py:526`; no line collision.
- **`hook-validator-target-resolution.md` (#2738, Ready)** — consolidates four validators'
  duplicated `find_newest_plan_file` into one shared plan-doc resolver. Adjacent
  ("which plan doc is this") but a different symbol in a different layer; no file
  collision. Worth sequencing awareness only.

**Notes:**

**This plan is its own test case.** At plan time, `find_plan_path(2718)` resolves to
*this* document — via the bare-`#N` fallback, because the plan mentions `#2718` in prose
and no plan carries a `tracking:` line for it. That is the right answer for the wrong
reason, and it will stop being true the moment the fallback is deleted. After the fix,
`find_plan_path(2718)` correctly returns `None`: #2718 is a co-closed issue, not a lane.
Its `_meta` will report `plan_exists: False`, and since it will have no PR of its own,
router row 1 could dispatch `/do-plan` for it. **The builder must confirm #2718 is closed
by this PR's `Closes #2718` before the router gets a chance to open a lane on it** — or,
if the merge lags, accept that a `/do-plan` dispatch on #2718 is cosmetic noise against
an issue that is about to close. The lane is #2735; there is exactly one plan doc and one
branch.

Similarly, this lane's own branch will be `session/sdlc-2735` (that is what
`tools/valor_session.py:107` mints) while its plan doc is
`docs/plans/sdlc-lane-recorded-slug.md` — the exact slug/branch divergence #2718
describes. This lane is a live instance of the bug it fixes.

Two drifts, neither changing a premise:

1. `session/sdlc-2663` has advanced from `69039fae5` (cited in #2718) to `eeb89b4ee`.
   The branch still exists and the divergence still reproduces; only the head moved.
2. The origin now carries **451** heads under `session/`, of which 99 match
   `session/sdlc-*`. The issue cited 111 local. Both counts confirm the same point: the
   "never created" shape is the *majority* shape on this remote.

## Prior Art

- **#1915** — *"do-sdlc/do-build fork: background exits strand pipelines; parallel
  builders share one slug worktree"* (closed 2026-07-08, plan
  `sdlc-fork-sync-workers-worktree-isolation.md`). Its Defect 2 is the direct ancestor
  of both issues here: "The fork derives worktree+branch from the plan slug
  (`.worktrees/{slug}`, `session/{slug}`) and ignores externally designated lanes
  (supervisors' `.worktrees/sdlc-{N}` instructions never propagate)." Its suggested
  direction #3 was explicitly a fork in the road — add a seam, **or** document that slug
  identity always wins. The resolution recorded only the doctrine, as a comment, and
  never removed the competing minters. Ten months of `session/sdlc-{N}` branches later,
  that comment is a false statement the gates depend on.
- **#2026** — *"forked executions merge past blocked gates… lease TTL churn deadlocks the
  router"* (closed 2026-07-20). Established the run-lock / supervised-run-signal design
  and the lease-based liveness model. Relevant as a **constraint**, not a template: the
  #2446/#2451 class of bug came from spreading liveness inference across extra fields.
  This plan therefore adds **no** holder/pid/liveness field — liveness stays with the
  lease, and the new field is pure identity.
- **#2012** — introduced `PipelineLedger` as the durable, issue-keyed home for stage
  state, moving it off the ephemeral `AgentSession`. This plan follows that decision to
  its conclusion: the lane identity belongs with the stages it names, in the same
  record.
- **#2042 / `_migrate_confirm_is_ledger_field_readable`** (`scripts/update/migrations.py:335`,
  registered `:1032`) — the established pattern for landing a purely additive, defaulted
  Popoto field: a read-only healing probe, no backfill. This plan's migration mirrors it.

No merged PR has previously attempted to fix `find_plan_path`'s fallback or G8's branch
derivation.

## Research

No relevant external findings — proceeding with codebase context. This work is entirely
internal: one nullable field on an in-repo Popoto model, following the in-repo migration
precedent at `scripts/update/migrations.py:335`, plus refactoring of in-repo SDLC
tooling. No external library, API, or ecosystem pattern is involved.

## Spike Results

### spike-1: Are all `find_plan_path` callers safe under a much more frequent `None`?

- **Assumption**: "Every caller already handles `None`, so removing the bare-`#N`
  fallback cannot crash or mis-route anything."
- **Method**: code-read across all 8 call sites plus `agent/sdlc_router.py`.
- **Finding**: **TRUE-WITH-CAVEATS.** No site can raise — all 8 guard `None`, and
  `sdlc_stage_marker.py:341` additionally guards exceptions. But three sites have
  *behavioral* consequences that must be handled rather than accepted:
  1. `tools/sdlc_verdict.py:772` — a `None` plan makes the fail-closed CRITIQUE
     findings gate (#2447) not fire, so a `NEEDS REVISION` verdict can be recorded with
     no findings behind it.
  2. `tools/sdlc_stage_marker.py:341` — `None` lifts the `PLAN_EXISTS_NOT_SKIPPABLE`
     refusal, making a stage recordable as skipped.
  3. `tools/sdlc_next_skill.py:203` — `None` skips both the PLAN-committed-on-main check
     and the whole PATCH check, so G8 stops catching fabricated completion claims.

  On the router side, `_rule_no_plan` (row 1, `agent/sdlc_router.py:759-775`) returns
  `False` immediately when `meta["pr_number"]` is truthy, and guard G3 (`:358-393`)
  independently locks plan-stage dispatch when an open PR exists. **The specific danger
  of re-planning an issue that already has a PR is structurally absent.** The residual
  risk is a *pre-PR* re-planning loop when `plan_exists` is spuriously `False`.
- **Confidence**: high.
- **Impact on plan**: The fallback removal makes the `tracking:` backfill task
  **non-optional and load-bearing**. A cycle-2 draft claimed the removal was "safe only
  because the resolver replaces it — `find_plan_path` gains a `docs/plans/{recorded_slug}.md`
  rung"; that rung is dropped in cycle 3 (see Technical Approach), so nothing replaces the
  fallback. `tracking:` frontmatter plus Task 5's backfill and test are the whole
  mitigation. It also and it drives the split of the PLAN artifact check (which needs the
  *plan-doc* path) from the PATCH artifact check (which needs the *lane branch*) — today
  both wrongly share one derived slug.

### spike-2: Can an existing pushed branch be adopted without inventing a competing identity?

- **Assumption**: "For a live lane with no recorded slug, the pushed branch can be
  identified from local git plus already-recorded ledger state."
- **Method**: code-read plus live queries against this checkout and Redis.
- **Finding**: **TRUE-WITH-CAVEATS.**
  - `git ls-remote --heads origin session/sdlc-2663` → `eeb89b4ee…`. The same probe for
    2716, 2735, 2718 returns nothing. Absence is the common case and must be a normal
    "nothing to adopt" outcome, not an error.
  - `PipelineLedger.pr_number` is populated on only **3 of 16** ledgers (2696→2710,
    2717→2721, 2711→2728). A real but sparse (~19%) carrier.
  - `tools/pr_head_resolver.py` is git-first (`git ls-remote origin refs/pull/{N}/head`,
    `gh` only as fallback) but exposes **no** `headRefName` path — SHA only. A branch
    *name* is still recoverable offline by matching that SHA against a single
    `git ls-remote --heads origin` listing.
  - `AgentSession` contributes nothing today: 28 sessions, 11 with `issue_number`, 4
    with a non-`None` slug, **intersection zero**.
  - **The `sdlc-{N}` shape is not universal.** `.worktrees/sdlc-1920` and
    `.worktrees/sdlc-1997` sit on `session/dev-81976da0` and `session/dev-7bd4cf82`. A
    pure `session/sdlc-{N}` probe silently misses those and would invent a competing
    identity for a lane that already has a branch — precisely the failure the healer
    exists to prevent. The PR-mediated rung is what recovers them.
- **Confidence**: high.
- **Impact on plan**: Fixes the adoption ladder's ordering and content (see Technical
  Approach). Specifically: the PR→SHA→ref-name rung must sit *above* the `sdlc-{N}`
  probe, because it is shape-agnostic. The local `git worktree list` rung the spike
  recommended is **deliberately excluded** — it is machine-local, so two hosts would
  reach different answers for the same lane, and a per-host identity is not an identity.

## Data Flow

**Today (broken), for one `sdlc-tool next-skill --issue-number N` tick:**

1. **Entry**: router asks for context for issue N.
2. `find_plan_path(N)` scans `docs/plans/`, prefers a `tracking:` match, else returns the
   first file whose text contains `#N` **anywhere** — including a No-Gos line.
3. `Path(plan_path).stem` becomes "the slug" in two independent places.
4. `git ls-remote --heads origin session/{that-stem}` probes a branch that may never
   have existed.
5. `_compute_meta` separately reads `getattr(ledger, "slug")` → always `None` (no such
   field) → falls through to a `--head session/{slug}` PR lookup that is inert for the
   same reason.
6. **Output**: `branch_exists`, `stage_artifacts_verified`, `plan_exists`,
   `revision_applied` — four router inputs, all potentially sourced from a foreign
   issue's plan.

**After:**

1. **Entry (lane start)**: `tools/sdlc_session_ensure.py::ensure_session` runs on both
   lane-start paths — the upvote reflection and local `/do-sdlc` — before any plan exists.
2. It calls `resolve_lane_slug(N, allow_heal=True)`, which `get_or_create`s the
   `PipelineLedger` (creating it if this is the lane's first touch), walks the adoption
   ladder and, on a miss, mints `sdlc-{N}`. The result is written to
   `PipelineLedger.slug` **conditional-on-empty**, beside `stage_states_json` and
   `pr_number`.
3. **Every consumer** calls `resolve_lane_slug(N)` — with the default
   `allow_heal=False` — and gets that recorded value, or `None`. Consumers never mint.
   Only the three lane-start callers listed in Technical Approach pass `allow_heal=True`,
   and their write is conditional-on-empty: it can never overwrite and never needs the
   lease.
4. `find_plan_path(N)` resolves on one rung: a `tracking:` frontmatter line naming N.
   No textual fallback, and no slug-named-file rung (dropped, revision cycle 3).
5. G8's PATCH check probes `session/{recorded_slug}`; `branch_exists` checks the same
   name. With no resolvable slug, both **no-op** — they never probe a guess.
6. **Output**: the same four router inputs, each now sourced from the lane's own
   recorded identity or explicitly absent.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|---|---|---|
| #1915 resolution (2026-07-08) | Chose "slug identity always wins" and recorded it as a code comment: `tools/sdlc_next_skill.py:346-349`, `tools/sdlc_stage_query.py:347-350`, plus three test cases at `tests/unit/test_sdlc_next_skill.py:117-176`. | Documented a rule without removing the code that breaks it. `reflections/sdlc_upvote_lanes.py:489`, `reflections/sdlc_progress.py:707`, and `tools/valor_session.py:107` kept minting `sdlc-{N}` slugs, and `agent/worktree_manager.py:1273` kept turning them into real branches — 99 of them on origin today. A comment cannot enforce a convention against live minters. |
| `find_plan_path`'s bare-`#N` suppression | Added a guard so a bare textual `#N` match returns `None` instead of a foreign plan. | Gated on `_is_ai_repo_fallback`, which is set only when plans-dir resolution reaches step 3 (`SDLC_TARGET_REPO` unset **and** not inside a git repo). Steps 1 and 2 cover every production call, so the guard is dead code. The docstring correctly names the hazard; the implementation never fires. |
| `_compute_meta`'s `slug` read (`tools/sdlc_stage_query.py:487`) | Intended to prefer a recorded slug over derivation for PR lookup. | Reads `getattr(session, "slug", None)` against a `PipelineLedger`, which has no `slug` field — so it is unconditionally `None`. A recorded-value read was written against a record that never recorded the value. This is the closest prior attempt to the right answer, and it failed for want of the field this plan adds. |

**Root cause pattern:** every prior fix addressed *one consumer's* guess. None asked who
*writes* the identity. The system has three writers of lane identity and zero readers of
a recorded one, so each fix relocated the guess instead of removing it. This plan
inverts that: one writer, one record, every consumer a reader.

## Architectural Impact

- **New dependencies**: none external. One new internal module, `tools/lane_identity.py`,
  importing `agent.pipeline_ledger` and `tools.pr_head_resolver`.
- **Interface changes**:
  - `PipelineLedger` gains one nullable field, `slug`.
  - `find_plan_path` moves from `tools/_sdlc_utils.py` to `tools/lane_identity.py`. All
    8 call sites plus the `docs/sdlc/do-plan-critique.md` snippet are updated; no
    re-export shim is left behind (no-legacy rule).
  - `_check_branch_pushed(slug)` becomes `_check_branch_pushed(branch_name)` — the
    `session/` prefix stops being hardcoded inside the probe.
- **Coupling**: **decreases.** Today five modules each carry their own slug-derivation
  logic. After, they all depend on one resolver.
  **Import direction: `lane_identity → _sdlc_utils`, one symbol, no cycle.** An earlier
  draft of this plan asserted the opposite direction and claimed the import "would
  cycle". That claim was verified **false** and is retracted. Verified at plan time:
  `tools/_sdlc_utils.py:24-25` imports only `agent.sdlc_router` and
  `models.agent_session`; `tools/pr_head_resolver.py` is stdlib-only;
  `agent/pipeline_ledger.py` imports only `popoto`. After Task 4 deletes `find_plan_path`
  from `_sdlc_utils` with no shim, nothing gives `_sdlc_utils` any reason to import
  `lane_identity`, so the reverse edge does not exist and cannot close a cycle.
  `tools/lane_identity.py` therefore imports exactly two helpers from `_sdlc_utils`:
  `_git_toplevel` (for the plans-dir ladder) and `_resolve_target_repo` (to build the
  ledger key). Both **stay where they are** — `_resolve_target_repo`
  (`tools/_sdlc_utils.py:111`) calls it and 14 tests monkeypatch the literal path
  `tools._sdlc_utils._git_toplevel`; moving it would churn those tests for no
  correctness gain, which the minimum-solution constraint forbids. The plans-dir
  resolution ladder is retained verbatim *including* its `_git_toplevel()` step; the
  ladder is what moves, the helper is not.
- **Data ownership**: lane identity moves from "nobody, re-derived per call" to
  `PipelineLedger`, which already owns the stages the slug names. `AgentSession.slug`
  remains for non-SDLC sessions (nightly triage, dev lanes) and is stamped from the
  ledger for SDLC lanes — the ledger is authoritative.
- **Reversibility**: high. The field is additive and nullable; reverting the code leaves
  a harmless unread field. No data is destroyed at any point.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (the scope boundary against a general lane-record subsystem is the thing to hold)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|---|---|---|
| Redis reachable | `.venv/bin/python -c "from agent.pipeline_ledger import PipelineLedger; list(PipelineLedger.query.filter())"` | The new field is read/written on a live Popoto model |
| Git remote `origin` reachable | `git ls-remote --heads origin HEAD` | The adoption ladder probes remote refs |
| On-pin venv | `.venv/bin/python -c "import sys; print(sys.version)"` matches `.python-version` | `scripts/pytest-clean.sh` aborts on an off-pin venv |

## Solution

### Key Elements

- **`PipelineLedger.slug`** — one nullable field, beside `stage_states_json` and
  `pr_number`, holding the lane's single identity. Pure identity: no holder, no pid, no
  liveness, no timestamp.
- **`tools/lane_identity.py`** — the single home for **both** slug resolvers, deliberately
  colocated so the distinction between them is stated once and cannot drift. Exposes
  `resolve_lane_slug(issue_number, *, allow_heal=False)`, `mint_lane_slug(issue_number)`,
  `lane_branch_name(slug)`, and the relocated `find_plan_path(issue_number)`.
  `mint_lane_slug` is the pure, write-free `f"sdlc-{N}"` constructor — the **one** home of
  that literal, used both by `resolve_lane_slug`'s mint rung and by `cmd_create`'s
  non-recording fallback (Task 3). Its module docstring **opens** with the
  two-slug distinction (lane slug vs. plan-doc slug, bridged by `tracking:`), so a reader
  who arrives expecting one concept is corrected in the first sentence rather than
  invited to re-unify them.
- **Adoption ladder** — how `resolve_lane_slug` answers when the field is empty **and the
  caller is a lane-start path that opted into `allow_heal=True`**: adopt an identity that
  already exists in the world before inventing one. Read paths never walk the ladder.
- **Conditional-on-empty write** — the healing write re-reads immediately before
  writing, writes only if still empty, uses `save(update_fields=["slug"])`, and takes
  no lease.
- **`ensure_session` mints** — the one component that runs on **every** lane-start path
  (upvote reflection, local `/do-sdlc`) before any plan or any
  stage exists. It calls the resolver with `allow_heal=True` at lane start.
  **`ensure_session` does not create a `PipelineLedger` today** — no `get_or_create` call
  exists in `tools/sdlc_session_ensure.py`; the ledger is first created by whichever of
  `tools/sdlc_dispatch.py:227`, `tools/sdlc_review_finalize.py:485`,
  `tools/sdlc_verdict.py:790`, `tools/sdlc_meta_set.py:190`, `agent/pipeline_state.py:388`,
  `agent/session_runner/runner.py:1415`, or `scripts/update/migrations.py:448` runs first.
  This plan therefore makes the **healing path** of `resolve_lane_slug` the ledger's
  creator: `allow_heal=True` calls `get_or_create`, so the mint at lane start has a write
  target. Read paths (`allow_heal=False`) still use `load()` and can never bring a ledger
  into existence. See "Rung 1 branches on `allow_heal`" under Technical Approach.
- **No-op on unresolvable** — G8's PATCH check and `branch_exists` skip entirely rather
  than probe a guessed name.

### Flow

**Lane start** → `ensure_session(N)` calls `resolve_lane_slug(N, allow_heal=True)` →
`get_or_create` brings the `PipelineLedger` into existence (or finds it) → ladder finds
nothing → mints `sdlc-{N}` → writes `PipelineLedger.slug` → **lane has an identity**

**Every later tick** → any consumer calls `resolve_lane_slug(N)` (heal off) → reads the
recorded value → **same answer everywhere**

**Non-lane issue** (`stage-query --issue-number 2718`) → `resolve_lane_slug(2718)` →
heal off, so `load()` → no ledger (or ledger with empty slug) → **`None`, no ledger
created, nothing minted, nothing written, no git call**

**Migration lane (slug empty, branch already pushed)** → the next **lane-start** call for
that issue (`ensure_session` or a reflection pickup) walks the
ladder → rung 2 or 3 finds `session/sdlc-2663` → adopts `sdlc-2663` →
conditional-on-empty write → **existing identity captured, not replaced**. Read paths in
the meantime return `None` and no-op, which is the correct behavior for an unresolved
lane and strictly better than today's guess.

### Technical Approach

**Two slugs, kept distinct.** The *lane slug* names the branch, worktree, and task list.
The *plan-doc slug* is the plan filename stem. They are usually equal and sometimes
not — a human-named plan (`session-liveness-tick-counter`) can legitimately track a lane
minted as `sdlc-2716`. `tracking:` frontmatter is the bridge between them and stays
authoritative. This plan does **not** try to force them to be the same string; forcing
that is what produced the wedge.

**`resolve_lane_slug(issue_number, *, allow_heal: bool = False) -> str | None`** walks:

1. Fetch the ledger for the issue's key → `.slug` if non-empty → return it.
   **Never re-derive over a recorded value.** This rung is why the function is safe to
   call from anywhere.
2. `ledger.pr_number` → `resolve_pr_head_sha(pr, cross_check=False, repo=target_repo,
   repo_root=_target_repo_cwd())` (git-first; thread BOTH — `tools/pr_head_resolver.py:136-142`
   otherwise resolves against the process cwd, which is wrong for a cross-repo lane, and
   `tools/sdlc_next_skill.py:148` already passes `repo_root` for exactly this reason) → match
   that SHA against one `git ls-remote --heads origin` listing → recover the exact
   `session/<name>` ref → slug is that name minus the `session/` prefix. Shape-agnostic;
   this is the rung that recovers `dev-<hash>` lanes. **The match must be unique**
   (see "Rung 2 uniqueness" below).
3. `git ls-remote --heads origin refs/heads/session/sdlc-{N}` exact probe → adopt
   `sdlc-{N}`.
4. `docs/plans/` scan for a `tracking:` frontmatter match on issue N → adopt that plan's
   filename stem.
5. Mint `sdlc-{N}`.

**Rung 1 branches on `allow_heal` — this is the whole write-target question.** The two
modes fetch the ledger differently, and that difference is the only reason the minter has
somewhere to write:

```
def resolve_lane_slug(issue_number, *, allow_heal=False, target_repo=None):
    if target_repo is None:
        target_repo = resolve_target_repo_for_read(issue_number)  # tools/_sdlc_utils.py:134
    if not target_repo:
        return None            # NEVER assemble a `None:{issue}` key -- both arms.
    if allow_heal:
        ledger = PipelineLedger.get_or_create(target_repo, issue_number)  # :178
    else:
        ledger = PipelineLedger.get(target_repo, issue_number)            # :293, may be None
```

**ONE repo resolver on both arms, plus a mandatory passthrough (revision cycle 5).**
Cycles 3 and 4 both got this wrong, in opposite directions, and the second attempt is
what forced the collapse to a single rule:

- Cycle 3 used `_resolve_target_repo()` (the env ladder) for both arms. That shells out to
  `gh repo view` whenever `GH_REPO` is unset (`tools/_sdlc_utils.py:94-131`), and it is not
  the convention readers use.
- Cycle 4 split them — `resolve_target_repo_for_read` for the read arm,
  `_resolve_target_repo` for the heal arm — which created a **divergence class**: the two
  ladders can answer differently, so the heal arm could write under one key and the read
  arm look under another, and the recorded slug would read back as `None` with no error
  anywhere.
- Cycle 5 collapses it. `resolve_target_repo_for_read` peeks the issue lease first and
  **falls back to `_resolve_target_repo()`'s env ladder** when no live lease exists
  (`tools/_sdlc_utils.py:134-166`), so it is a strict **superset** of the writer ladder,
  not a competing one. Using it on both arms makes divergence impossible by construction
  rather than something a test has to chase. Its env fallback is memoized within a
  `cached_target_repo_resolution()` scope (#2122), so the read path does not pay for a
  `gh` call per tick.

**A caller holding an authoritative repo slug MUST pass `target_repo=` — this is not
optional (cycle 5, BLOCKER).** Both reflection callers iterate *projects*:
`reflections/sdlc_upvote_lanes.py:440` binds `repo = _project_repo(project)` and
`sdlc_progress.py` does the same. Only *subprocesses* get the project `cwd`; the Python
process's own cwd and env never change. So an unpassed `target_repo` resolves through the
reflections process's own git root — `tomcounsell/ai` — and a non-`ai` project's lane
would be recorded under the wrong repo while its own gates read the right one. Gate 3
(`:511`) has already excluded live leases by the time these run, so the lease peek cannot
rescue it either. Both sites pass `target_repo=repo`.

**Never build a key from a `None` repo.** Both resolvers can return `None`
(`tools/_sdlc_utils.py:112-113, 126-131, 147-149`), and `_build_key`
(`agent/pipeline_ledger.py:122-134`) explicitly pushes that check to callers — a `None`
would mint a phantom `None:{issue}` key, and on the heal arm it would **create** that
phantom record. The guard is `if not target_repo: return None` above the branch, matching
the established reader convention at `tools/sdlc_stage_query.py:612-614`.

**`_compute_meta` does NOT pass `target_repo` through.** A cycle-4 draft had it do so,
citing `tools/sdlc_stage_query.py:468` — but `:468` is `resolved_repo = _resolve_target_repo()`,
the env ladder, so passing it would hand the read path exactly the resolver this fix
exists to keep out of it. `_compute_meta` calls `resolve_lane_slug(N)` with no
`target_repo` and lets it resolve lease-first; the memoized fallback keeps the cost at one
resolution.

The Success Criterion is restated as "the read path issues **no subprocess beyond
target-repo resolution**, which is memoized per scope, and none at all when the caller
passes `target_repo` or `GH_REPO` is set".

- **Read path (`allow_heal=False`)**: `load()` is a direct-key `HGETALL` and **never
  creates a ledger**. When no ledger exists the function returns `None` immediately, so a
  read path can never bring a `PipelineLedger` into existence for a non-lane issue. This
  is cycle-1 blocker 1's fix and must not regress.
- **Heal path (`allow_heal=True`)**: `get_or_create` is explicitly permitted to create the
  ledger, because the three sanctioned callers are lane-start paths and **a lane by
  definition has stages**. Creating the record that will hold them is not a side effect;
  it is the point. `get_or_create` is already race-safe (SETNX-serialized create,
  `agent/pipeline_ledger.py:177-235`) and never clobbers a populated
  `stage_states_json`.

An earlier draft applied "never create a ledger" to the *whole* function. That removed the
mint's write target entirely: at lane start no ledger exists, rung 1 returned `None`, and
the resolver was forbidden from creating one — so the mint never persisted and every
consumer read `None` forever. The `allow_heal` branch is what keeps read paths inert
without disarming the minter.

**`allow_heal` defaults to `False`, and that default is load-bearing.** With
`allow_heal=False` the function stops after rung 1 and returns `None` — no git call, no
write, no ledger creation. Rungs 2-5 run **only** under `allow_heal=True`; they produce a
*candidate*, which is then written conditional-on-empty and returned.

The reason the default inverts (an earlier draft defaulted to `True`) is that
`stage-query` runs for **any** issue number the router, the dashboard, or an operator
asks about — not just lanes. A healing default would make a pure read path mint and
persist `sdlc-{N}` on issues that are not lanes, contradicting this plan's own thesis
that the slug is minted exactly once by `ensure_session`. This plan's own example proves
it: a single `stage-query --issue-number 2718` would mint `sdlc-2718` for an issue this
plan requires to resolve to `None`.

**Who may pass `allow_heal=True` — the complete list, enforced by a grep anti-criterion:**

| Caller | Why healing is correct there |
|---|---|
| `tools/sdlc_session_ensure.py::ensure_session` | The minter. Runs at lane start; this is where identity is created. |
| `reflections/sdlc_upvote_lanes.py` lane pickup | Lane start on the reflection path; goes on to create a real branch. |
| `reflections/sdlc_progress.py` stalled-lane respawn | Lane restart for an issue already known to be a lane. |

Three callers, not four. **`tools/valor_session.py::cmd_create` was removed from this list
in revision cycle 3.** It looked like a lane-start path, but its auto-derive branch fires
on any eng session whose `--message` merely mentions an issue in prose, so "lane start"
was never something it actually knew. It reads with heal off and falls back to
`mint_lane_slug()` without recording. See Task 3.

Every other caller — `_compute_meta`, G8, `branch_exists`, `cmd_create`, the reflections'
*discovery* probes — takes the default `False`. (`find_plan_path` no longer calls the
resolver at all; its slug-named-file rung is dropped in cycle 3.) A consumer that finds no
recorded slug gets `None` and no-ops. Self-healing is a **lane-start** behavior, not a
read-path behavior; that is the reconciliation between "a consumer finding no slug MAY
create one" and "minted exactly once".

**Rung 2 uniqueness.** Matching a PR head SHA against a listing of 451 `session/` heads
is not guaranteed unique: a re-created lane branch, a fork, or a `session/dev-*` left at
the same tip all produce duplicate matches, and a listing-order-dependent answer is a
per-invocation identity — the same failure class this plan rejects when it excludes the
machine-local `git worktree list` rung. So: collect **all** matches before deciding.
Adopt only when exactly one `refs/heads/session/*` ref matches; on zero or two-plus
matches, emit a `logger.warning` naming every candidate and fall through to rung 3.
Merged-and-deleted is the common zero case — `resolve_pr_head_sha` can return a SHA whose
branch no longer exists on origin — and must be a clean fall-through, never an error.

**Conditional-on-empty write.** Popoto has no compare-and-set. The write reuses the
short-lived SETNX pattern `PipelineLedger.get_or_create` already uses for its create
race (`agent/pipeline_ledger.py:248` (`_acquire_create_lock`)): take a SETNX on a dedicated non-Popoto key, then
`load()` the ledger fresh (direct-key `HGETALL`, index-independent — the #1720 hazard
does not apply), and write only if `slug` is still empty. `save(update_fields=["slug"])`
so `stage_states_json` is never touched. A lost race is not an error: the loser re-reads
and returns the winner's value. **No lease is taken** — this is an identity write, not a
stage transition, and gating it on the lease would reintroduce the deadlock class #2026
closed.

**`find_plan_path` becomes resolution, not derivation.** Moved into
`tools/lane_identity.py` and reduced to **one rung**:

1. A `tracking:` frontmatter line naming issue N → authoritative, return it.
2. Otherwise `None`.

**The `docs/plans/{resolve_lane_slug(N)}.md` rung is dropped (revision cycle 3.)** An
earlier draft added it as rung 2. It was near-dead code introduced by the same PR that
deletes near-dead code for the same reason. It runs with `allow_heal=False`, so it
requires both an already-recorded slug **and** a plan filename equal to the *lane* slug —
which "Two slugs, kept distinct" (above) explicitly refuses to force. This very lane is
the counterexample: recorded slug `sdlc-2735`, plan doc `sdlc-lane-recorded-slug.md`, rung
2 misses. Keeping it would have meant shipping a rung whose only honest test is a
contrived ledger built to hit it.

Dropping it has a real cost, stated plainly rather than papered over: `tracking:` becomes
a hard single point of failure for plan resolution, with no authoring-time enforcement.
`tests/unit/test_plan_docs.py` (Task 5) is the sole guard, and it catches a missing
`tracking:` on the next suite run, not at authoring time. Risk 2 records this. An
authoring-time hook validator is the proper closure and is a No-Go for this lane.

**The one-rung resolution interacts with the new fail-closed CRITIQUE raise (cycle 4).**
With rung 2 gone, a plan doc that `/do-plan` has just written without a `tracking:` line
makes `find_plan_path` return `None`, and `tools/sdlc_verdict.py:771` now *raises*
`CRITIQUE_PLAN_UNRESOLVABLE` rather than skipping — a hard lane block. Task 5's test
audits **committed** plan docs on the next suite run and cannot help the doc just
authored. Task 8 therefore adds `tracking:` to the `/do-plan` frontmatter template in
**this** PR, so the authoring path emits the line it is now required to carry. That is the
proportionate mitigation; a PreToolUse validator remains a No-Go.

This also means `find_plan_path` no longer calls `resolve_lane_slug` at all, so the
"moved into `tools/lane_identity.py` to avoid an import cycle" rationale weakens to plain
colocation: the two live together because the module docstring's lane-slug-vs-plan-slug
distinction is the thing a reader must not re-unify, not because either needs the other.

The bare-`#N` fallback and the entire `_is_ai_repo_fallback` mechanism are **deleted** —
not narrowed, not made section-aware. A heuristic that parses prose to decide which
"#N" counts is the same class of confident-wrong-answer the issue is about. The
plans-dir resolution ladder (env var → git toplevel → `__file__`) is retained verbatim;
only the ownership test changes.

**The two fail-open sites spike-1 named get an owner in this plan.** Deleting the
bare-`#N` fallback makes `find_plan_path` return `None` far more often, and at two sites
`None` currently means *permit*, not *refuse*:

- `tools/sdlc_verdict.py:772-777` — the #2447 CRITIQUE findings gate skips itself when the
  plan is unresolvable, so a `NEEDS REVISION` verdict can be recorded with no findings
  behind it. **Fix:** invert that branch to refuse. When
  `args.stage.upper() == "CRITIQUE"` **and** `normalize_verdict(args.verdict) == "NEEDS
  REVISION"` **and** `plan_path is None`, raise `CritiqueFindingsMissingError` with a
  distinct code `CRITIQUE_PLAN_UNRESOLVABLE`, because once the textual fallback is gone
  "unresolvable" and "no findings" stop being distinguishable and the gate must fail
  closed. The scope stays exactly as narrow as the existing comment at `:758-763`
  declares: never on any `READY TO BUILD` variant, never on `MAJOR REWORK` (incl.
  `MAJOR REWORK (CRITIQUE INCOMPLETE)`). The raise still precedes
  `PipelineLedger.get_or_create` at `:790`, so no partial write occurs.
- `tools/sdlc_stage_marker.py:349-362` — a `None` plan lifts the
  `PLAN_EXISTS_NOT_SKIPPABLE` refusal, making a stage recordable as skipped. **NOT FIXED
  HERE (revision cycle 4).** A cycle-3 draft proposed extending the `except Exception`
  arm's refusal to the resolvable-but-absent case. That is unbuildable: precondition 1
  refuses whenever `plan_path is not None`, so `plan_path is None` is the only branch that
  permits a skip at all, and refusing there means no stage is ever skippable. The site
  gets its import retargeted to `tools.lane_identity` and no behavior change. Skip-gating
  is a separate concern from lane identity; see Task 4 and No-Gos.

The third site spike-1 named, `tools/sdlc_next_skill.py:203`, is fully handled by the
PLAN/PATCH split below and needs no separate treatment.

**G8 and `branch_exists` read the recorded slug.** In `_verify_stage_artifacts_live`:

- The **PLAN** artifact check needs the *plan-doc* path — it takes it from
  `find_plan_path(N)` directly, not from a slug. Today it checks
  `docs/plans/{derived-slug}.md`, which for a lane slug like `sdlc-2663` names a file
  that does not exist. This split is a correctness fix in the same family.
- The **PATCH** artifact check needs the *lane branch* — it takes
  `lane_branch_name(resolve_lane_slug(N))`. When the slug is `None`, `patch_claimed`
  stays `False` and the check no-ops, exactly as the existing
  `patch_claimed = ... and bool(slug)` guard already allows.
- `_check_branch_pushed` takes a full branch name; the `session/` prefix is applied by
  `lane_branch_name`, in one place.
- The warning strings name the branch actually probed.

`_build_context`'s `branch_exists` (`:355-367`) does the same: recorded slug or `False`.

**`_compute_meta`'s dead read is repaired.** `tools/sdlc_stage_query.py:487` replaces
`getattr(session, "slug", None)` with `resolve_lane_slug(issue_number)` — **default
`allow_heal=False`** — which makes the `--head session/{slug}` PR-recovery rung at `:369`
functional for the first time for lanes that have a recorded slug, and correctly inert
(no probe at all) for issues that are not lanes. `_compute_meta` is the canonical example
of a read path that must never mint: it runs for any issue the router, the dashboard, or
an operator names.

**Mid-pipeline lanes: what actually happens to them.** Healing is a lane-**start**
behavior, so a lane already at BUILD/REVIEW when this merges never heals on its own:
`ensure_session` is not called again mid-pipeline, and the reflections' respawn fires only
for *stalled* lanes. Such a lane keeps an empty slug and every read path returns `None`
and no-ops. Stated plainly rather than waved through as "strictly better":

- **Blast radius, measured.** `PipelineLedger` today carries only `ledger_key`,
  `target_repo`, `issue_number`, `stage_states_json`, `pr_number` — 16 ledgers exist, none
  with a slug, so **every** currently-live lane is in this state on merge day. `sdlc-2663`
  and `sdlc-2716` are exactly these cases.
- **What changes for them.** `plan_exists` / `revision_applied` go from *contaminated*
  (sourced from a foreign plan) to *absent*, which flips router rows 4b/4c and G7 inputs.
  For a lane whose plan carries `tracking:` — which every active lane's does, verified
  under Risk 2 — `find_plan_path` rung 1 answers without any slug at all, so `plan_exists`
  and `revision_applied` are **unaffected**. The `None` slug only disables the *branch*
  probes: G8's PATCH check and `branch_exists` no-op. For #2663 specifically that is the
  fix, not a regression — the no-op replaces the wrong-branch probe that wedges it.
- **When they heal.** On their next lane-start call: a reflection respawn, a
  or any `ensure_session` for that issue. `sdlc-2663`'s next
  respawn adopts `sdlc-2663` via ladder rung 3. Until then they read `None`, which is
  correct-and-explicit rather than confident-and-wrong.
- **The operator signal.** This is why `_meta` gains `slug_source` alongside `slug`:
  `recorded` when rung 1 answered, `unresolved` when it did not. A wedge investigation can
  then distinguish "skipped because unresolvable" from "verified clean" at the CLI, not
  only in a `logger.debug`. `sdlc-tool stage-query --issue-number N` is the surface this
  plan already extends, so this costs one key, not a new tool. A one-liner over
  `PipelineLedger.query.filter()` counting empty slugs is the fleet-wide view; no
  dashboard work is in scope.
- **No backfill.** The no-eager-backfill rule under No-Gos stands: a backfill would have to
  guess the shape for lanes with no branch and no PR, writing exactly the wrong-identity
  records this plan exists to prevent.

**The third minter is removed too.** `tools/valor_session.py:107`
(`_derive_slug_from_message`) is the surviving `sdlc-{N}` minter the Problem table names.
Leaving it would reproduce the exact #1915 failure this plan diagnoses — "a comment
cannot enforce a convention against live minters" — at one-third scale, and because the
ledger write is conditional-on-empty, whichever writer ran first would win
non-deterministically.

**`_derive_slug_from_message` is deleted, not retained as a fallback.** Keeping it was
self-contradictory in two ways. First, its body *is* the minter — `return
f"sdlc-{match.group(1)}"` at `tools/valor_session.py:107`, plus an `sdlc-{N}` docstring
example at `:92` — so `AC-MINT` could never reach one hit while it survived, and the only
escapes were failing the gate or weakening the grep (the vacuous-anti-criterion failure
this plan exists to prevent). Second, the retained fallback arm was **provably dead**: the
function returns `None` whenever `_ISSUE_REF_RE` misses, which is exactly the
`issue_number is None` branch it was meant to serve, so `slug or
_derive_slug_from_message(message)` could only ever produce `None` there.

The function is replaced by a pure **issue-number extractor** in the same module, and the
slug decision moves to its caller `cmd_create` (`tools/valor_session.py:588`):

```python
def _issue_number_from_message(message: str) -> int | None:
    """Extract the first ``issue #N`` reference from ``message``.

    Replaces the former ``_derive_slug_from_message``: slug identity is now
    resolved from the ledger, never derived from message text.
    """
    match = _ISSUE_REF_RE.search(message or "")
    return int(match.group(1)) if match else None
```

```python
issue_number = _issue_number_from_message(message)
slug = mint_lane_slug(issue_number) if issue_number else None
```

**No resolver call, no healing** — see Task 3 for why (`cmd_create` fires on any prose
issue mention, and adopting a live lane's recorded slug would drop a conversational
session into that lane's worktree, #1915 Defect 2). `AC-HEAL` requires **zero**
`allow_heal=True` hits in `tools/valor_session.py`.

Behavior is preserved exactly at both ends: `"handle issue #1109"` still yields a slug
(now `sdlc-1109` from the resolver's mint rung rather than from a format string, and the
lane's *recorded* slug when one already exists — the case that proves the seam changed
semantics rather than reproducing a string), and `"do something generic"` still yields
`None`, which the `eng`/`dev` refusal path and the `teammate` allow path already pin.
`_derive_sdlc_metadata`'s docstring (`tools/valor_session.py:110-133`) references
`_derive_slug_from_message` by name and must be updated to name
`_issue_number_from_message` instead.

Two `sdlc-{N}` string literals in `reflections/sdlc_upvote_lanes.py` — a comment at `:133`
and a docstring at `:329` — must be scrubbed in the same pass, or `AC-MINT` still returns
3+ hits after an otherwise correct build.

**The reflections stop guessing when *finding* a lane, not just when naming one.**
`reflections/sdlc_upvote_lanes.py::_has_pr_on_branch` (`:328-344`) probes
`gh pr list --head session/sdlc-{issue_number}`. After this plan a lane whose recorded
slug is human-named would not be found by that probe — the #2718 wedge shape from the
opposite direction, and now *silent*, because the recorded slug exists and looks
authoritative. The site already receives `issue_number`, so it becomes a **resolve-then-
fall-back** (never a skip — see Task 4's BLOCKER note; skipping the leg disables upvote
gate 4 for its entire target population):

```
# allow_heal=False -- a discovery probe must never record identity.
head = lane_branch_name(resolve_lane_slug(issue_number) or mint_lane_slug(issue_number))
```

There is no `None` branch: `mint_lane_slug` always returns a string, so the leg always
runs and the literal `--head session/None` is unreachable by construction rather than by
a guard (Empty/Invalid Input Handling bans it either way). When a slug IS recorded this
probe now finds human-named lanes it used to miss, which is the #2718 fix at this site;
when none is recorded it probes `session/sdlc-{N}` exactly as it does today, which is the
behavior gate 4 depends on. `reflections/sdlc_progress.py`'s
discovery is a *different* shape — a regex filter (`_SDLC_BRANCH_RE`, `:116`) over all
open PRs plus `_issue_number_from_slug` (`:262`) to map back — and converting it to
ledger-driven discovery is a restructuring, not a one-liner. It is deferred to a **filed
issue**, not a promise; see No-Gos.

**The false belief is deleted, not preserved.** The comments at
`tools/sdlc_next_skill.py:346-349` and `tools/sdlc_stage_query.py:347-350` asserting
this repo never creates `session/sdlc-{N}` are removed and replaced with the true
statement (the lane slug is recorded on the ledger; both `sdlc-{N}` and human-named
shapes occur). `TestBranchExistsCanonicalShape`
(`tests/unit/test_sdlc_next_skill.py:117-176`) is rewritten: its
`test_false_when_only_fabricated_shape_present` case currently asserts the exact
behavior that causes the #2718 wedge and must invert.

**Minting moves out of the reflections.** `reflections/sdlc_upvote_lanes.py:489` and
`reflections/sdlc_progress.py:707` stop constructing `f"sdlc-{issue_number}"` and call
`resolve_lane_slug(issue_number, allow_heal=True)` instead — these two are lane-start
paths, so they are on the sanctioned `allow_heal=True` list above. They receive the slug
from the resolver; they do not construct it.

**Closing on a sweep, not a list.** Per the repo's replicated-defect rule, this closes
out on a clean grep sweep for the derivation patterns, not on the enumerated site list
above. The Verification table encodes the sweeps.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `tools/lane_identity.py` — every git subprocess rung is wrapped and must, on
  failure, fall through to the next rung with a `logger.debug`, never raise and never
  return a wrong answer. Test: each rung's subprocess forced to `returncode != 0`, assert
  the next rung is attempted and a debug record is emitted.
- [ ] The conditional-on-empty write must swallow Redis errors and return the resolved
  candidate anyway (resolution is useful even when persistence fails). Test: `save`
  raising → resolver still returns the candidate, `logger.warning` observed.
- [ ] `tools/_sdlc_utils.py`'s existing `except Exception as e: logger.debug(...)` around
  the plans-dir walk is preserved in the relocated function; test asserts an unreadable
  plans dir yields `None` plus the debug record.
- [ ] `tools/sdlc_next_skill.py:329`'s bare `except Exception: pass` around the G5 hash
  block is in scope of edits. It must gain an observable `logger.debug` — a swallowed
  exception here silently disables a loop bound.

### Empty/Invalid Input Handling

- [ ] `resolve_lane_slug(0)`, `resolve_lane_slug(None)` → `None`, no Redis call, no git call.
- [ ] A ledger whose `slug` is `""` or whitespace must be treated as **empty**, not as a
  recorded value (Popoto stores blanks as strings; a naive truthiness check on `""` is
  correct but a whitespace-only value is not). Test both.
- [ ] `lane_branch_name(None)` → `None`, and no caller ever builds `"session/None"`. Test
  asserts the literal string `session/None` never appears in any probe command.
- [ ] `find_plan_path` with an empty `docs/plans/` directory → `None`.
- [ ] A `git ls-remote` returning empty stdout with `returncode == 0` (branch absent) must
  be a clean "not found", not an adoption.

### Error State Rendering

- [ ] G8's warning strings must name the branch **actually probed**. Test asserts the
  logged message contains the resolved branch name and never a name derived from a plan
  stem.
- [ ] When no slug resolves and the PATCH check no-ops, that no-op must be visible:
  a `logger.debug` naming the issue and the reason, so a wedge investigation can tell
  "skipped because unresolvable" from "verified clean".

## Test Impact

- [ ] `tests/unit/test_sdlc_next_skill.py::TestBranchExistsCanonicalShape` (`:117-176`) —
  **REPLACE**: the class docstring and all three cases encode the false "repo never
  creates `session/sdlc-{N}`" belief. Rewrite against the recorded slug:
  `test_true_when_recorded_slug_branch_exists`, `test_true_when_recorded_slug_is_issue_derived`
  (the inversion of `test_false_when_only_fabricated_shape_present` — this is the #2718
  regression test), `test_false_when_no_slug_resolvable`.
- [ ] `tests/unit/test_sdlc_next_skill.py:40,53,65,80,101,107,142` — **UPDATE**: these
  monkeypatch `tools._sdlc_utils.find_plan_path`, which no longer exists at that path.
  Retarget to `tools.lane_identity.find_plan_path`.
- [ ] `tests/unit/test_sdlc_next_skill.py` `_verify_stage_artifacts` cases (incl.
  `test_no_claimed_artifact_is_a_noop`) — **UPDATE**: the PLAN and PATCH checks now take
  their inputs from two different resolvers; fixtures must supply both.
- [ ] `tests/unit/test_sdlc_stage_query.py` — **UPDATE**: any case asserting
  `_compute_meta`'s `slug` comes from `getattr(session, "slug")` must move to
  `resolve_lane_slug`. Also add a case pinning that `_meta` for an issue with no plan
  reports `plan_exists: False` and `revision_applied: False` even when another plan
  mentions `#N` (the #2735 `_meta`-contamination AC).
- [ ] `tests/unit/test_sdlc_session_ensure.py` — **UPDATE**: `ensure_session` now writes
  a slug on the create path and the adopt paths; existing assertions on the returned dict
  are unaffected but the ledger write must be asserted. Coordinate with #2660, which
  also edits this file.
- [ ] Any test importing `find_plan_path` from `tools._sdlc_utils` — **UPDATE** to the new
  module. Sweep, do not enumerate.
- [ ] `tests/unit/test_pm_session_auto_slug.py` — **UPDATE**. This is where the
  slug-from-message behavior actually lives; `tests/unit/test_valor_session.py` (named in
  the prior draft) does not exist, and `grep -rln '_derive_slug_from_message' tests/`
  returns nothing — the *symbol* has zero direct coverage, but the *behavior* is pinned
  through `cmd_create`. Existing cases
  `test_pm_role_no_slug_with_hash_issue_reference_derives_slug` (`:69`, asserts
  `sdlc-1109`), `test_pm_role_no_slug_with_plain_issue_reference_derives_slug` (`:109`,
  asserts `sdlc-735`), and `test_dev_role_no_slug_auto_derives_from_issue_reference`
  (`:174`) must retarget to the resolver path: monkeypatch `resolve_lane_slug` and assert
  it is called with `(N, allow_heal=True)`. `test_pm_role_explicit_slug_wins_over_issue_parse`
  (`:141`) is unaffected and must stay green — explicit `--slug` still short-circuits
  before any resolution.
  **ADD** the case that proves the seam changed behavior rather than reproducing a
  string: an issue whose *recorded* slug is human-named (e.g. `session-liveness-tick-counter`)
  yields that slug, not `sdlc-{N}`. Under the old code this was unreachable.
  *(Deviation from the critique's suggestion, named per convention: the critique proposed
  `tests/unit/test_valor_session_create_core.py`. That file exercises `cmd_create` but not
  the slug-derivation branch; the auto-slug behavior is covered in
  `test_pm_session_auto_slug.py`, which is where the retarget belongs.)*
- [ ] `tests/unit/test_pm_session_refuse_no_issue.py` — **NO CHANGE expected, but must be
  run.** `test_pm_role_no_slug_no_issue_reference_exits_nonzero` (`:52`),
  `test_dev_role_no_slug_no_issue_refused` (`:118`), and
  `test_teammate_role_no_slug_no_issue_allowed` (`:145`) are what pin "generic message →
  `None` slug". They must stay green **and** `resolve_lane_slug` must not be called at
  all on that path — add that assertion so the invariant has an enforcing artifact rather
  than a stated one.
- [ ] `tests/unit/test_sdlc_stage_marker.py` (several `patch("tools._sdlc_utils.find_plan_path", …)`
  sites — **re-derive by sweep, do not work from a list**; the enumeration here was stale
  in both directions at cycle 3 and the count moves with `main`),
  `tests/unit/test_sdlc_verdict.py`,
  `tests/unit/test_sdlc_utils.py`, `tests/unit/test_sdlc_env_vars.py`,
  `tests/integration/test_off_pipeline_merge_path.py`,
  `tests/integration/test_sdlc_cross_repo_resolution.py` — **UPDATE**: patch targets and
  imports move to `tools.lane_identity`. `test_sdlc_verdict.py` additionally needs cases
  for the newly fail-**closed** CRITIQUE branch (Concern-3 fix). `test_sdlc_stage_marker.py`
  needs **no** fail-closed cases — that half was dropped in cycle 4; its change is an
  import retarget only. These six are why the Verification test row became
  sweep-driven (`AC-TESTSCOPE`) — a fixed file list would have certified a red suite.
- [ ] `reflections/` tests covering `_has_pr_on_branch` — **UPDATE**: the `--head` argument
  is now derived from the recorded slug when there is one. The no-recorded-slug case must
  assert the leg is still passed as `session/sdlc-{N}` (the `mint_lane_slug` fallback) —
  **not** omitted. Cycle 4: an earlier draft asserted omission, which pins the exact
  behavior that disables upvote gate 4. Add a case with a recorded human-named slug
  asserting `--head session/<that-name>`.
- [ ] `tests/unit/test_sdlc_utils.py`, `tests/unit/test_sdlc_env_vars.py`,
  `tests/unit/test_sdlc_stage_query.py` — **NO CHANGE** to the 14 sites that monkeypatch
  `tools._sdlc_utils._git_toplevel`. The helper deliberately stays in `_sdlc_utils`
  (see Architectural Impact); only the plans-dir *ladder* moves, and it calls the helper
  across the module boundary. Called out explicitly because an earlier draft implied the
  helper would move, which would have churned all 14. **This NO-CHANGE claim holds only if
  `lane_identity` accesses the helper as a module attribute** (`_sdlc_utils._git_toplevel()`),
  not via `from ... import _git_toplevel` — see Task 2 (cycle 4).
- [ ] `tests/unit/test_sdlc_utils.py` bare-`#N` suppression cases — **DELETE, not
  retarget**: `test_git_toplevel_bare_fallback_not_suppressed` (`:629`) and the
  suppression cases at `:555` and `:597` pin the behavior of the fallback this plan
  removes. A builder must not try to preserve them.
- [ ] **ADD** a test (or hook validator) enforcing `AC-TRACKING` repo-wide, so the
  "every plan resolvable" invariant survives past this lane's merge — see Task 5.
- [ ] **ADD** `tests/unit/test_lane_identity.py` — new coverage for the ladder, the
  conditional-on-empty write, the no-overwrite property, and the two ACs from #2735
  (`find_plan_path(2663) is None` while a plan tracking #2716 mentions `#2663`;
  `find_plan_path(2716)` still resolves; a no-go-section mention never confers ownership).

No expected-failure (`xfail`) markers were found relating to either bug, so there are no
xfail conversions in scope.

## Rabbit Holes

- **Building a general lane-record subsystem.** The temptation is a `LaneRecord` model
  with holder, pid, host, heartbeat, and state. Do not. The scope is one nullable field
  on an existing record. A holder/liveness field on this record is exactly how the
  #2446/#2451 class of bug was created — liveness stays with the lease.
- **Making the lane slug and the plan-doc slug the same string.** They are not the same
  concept, and unifying them means either renaming 99 branches or renaming plan docs.
  `tracking:` frontmatter already bridges them.
- **Renaming or deleting the 99 `session/sdlc-*` branches on origin.** The point of this
  plan is that the code adopts the world as it is. Branch hygiene is a separate concern.
- **A prose-aware `find_plan_path` that skips No-Gos sections.** Parsing markdown
  sections to decide which `#N` mention "counts" is a heuristic dressed as a rule. Delete
  the fallback instead.
- **Backfilling `PipelineLedger.slug` for all 16 ledgers in the migration.** The
  self-healing read covers them lazily and correctly, including the ones whose branch
  shape the migration would have to guess at. The migration is a read-only healing probe
  only.
- **Fixing the `agent/sdk_client.py:474` copy-paste bug**
  (`getattr(session,"slug",None) or getattr(session,"slug",None)` — the fallback arm is
  dead). Real, adjacent, and genuinely trivial — but it is an `AgentSession.slug` read in
  the executor, not SDLC lane identity. See No-Gos.
- **Unwedging lane #2663 by hand.** The fix unwedges it. Do not run manual dispatch
  resets as part of this work.

## Risks

### Risk 1: The healing write races another process and two lanes disagree mid-flight

**Impact:** Two consumers on different processes resolve different slugs in the window
before either write lands; one probes the wrong branch for one tick.
**Mitigation:** The SETNX-serialized, re-read-before-write, conditional-on-empty write
means at most one value is ever persisted, and every reader after that window agrees.
The loser of the race re-reads and adopts the winner's value rather than writing its
own. Worst case is one tick of the *existing* behavior, which is what the plan already
tolerates. Explicitly tested: two concurrent resolvers, assert exactly one write and
identical return values.

### Risk 2: Removing the bare-`#N` fallback strands a real plan that lacks `tracking:`

**Impact:** An active lane whose plan has no `tracking:` frontmatter suddenly resolves to
`None`, which per spike-1 disables the CRITIQUE findings gate, lifts a stage-skip
refusal, and fails G8 open.
**Mitigation:** Verified concretely: five plans in `docs/plans/` lack a `tracking:` line —
`keyfield-migration-fix.md` (tracks a *popoto* repo issue), `sdlc-1111.md` (closed,
shipped), `session-recovery-observation-audit.md` (an audit doc, not a lane),
`session-type-pm-rename.md` (#648, closed), `resilience-simplification-three-tier.md`
(`tracking: none yet` placeholder). **None is an active lane.** The plan additionally
backfills `tracking:` where an issue genuinely exists and adds a Verification row that
fails if any plan in `docs/plans/` lacks a **resolvable** `tracking:` line — resolvable
meaning it carries a real `#N` or `issues/N` token, not merely a non-empty value. The
regex stays tight — `^tracking:\s*\S` would accept the literal placeholder
`tracking: none yet`, green-lighting the exact case the row exists to catch. The two files
whose disposition needs a human call (`resilience-simplification-three-tier.md`'s
placeholder, and `session-recovery-observation-audit.md`, an audit document rather than a
lane plan) are excluded **by name** in the explicit `NON_LANE_PLANS` frozenset owned by
`tools/plan_doc_scope.py`, not by loosening the pattern (see Task 5).

**`tests/unit/test_plan_docs.py` is the SOLE guard (revision cycle 3).** An earlier draft
also claimed "the new `docs/plans/{recorded_slug}.md` rung covers slug-named plans without
frontmatter". That rung has been **dropped** (see Task 2), so this mitigation now rests
entirely on the backfill plus the test. That is the honest position: after this lane
`find_plan_path` is a one-rung `tracking:` lookup and `tracking:` is a hard single point
of failure with **no authoring-time enforcement** — nothing stops a human from creating a
plan doc without it. The test catches it on the next suite run rather than at authoring
time. Closing that gap properly means a hook validator, which is a new control surface and
belongs in its own lane (see No-Gos).

### Risk 3: Adopting the wrong branch for a lane whose `sdlc-{N}` branch is stale

**Impact:** Ladder rung 3 finds an old `session/sdlc-{N}` from a prior, abandoned attempt
and adopts it, binding the lane to a dead branch.
**Mitigation:** Rung 2 (PR head → exact ref name) sits **above** rung 3 precisely for
this: whenever a PR exists, its head ref is authoritative and shape-agnostic. Rung 3 only
fires when no PR is recorded, i.e. pre-build, when a pushed `session/sdlc-{N}` is by
construction *this* lane's. The write is also conditional-on-empty and never overwrites,
so once `ensure_session` has minted at lane start, rung 3 is unreachable for that lane.

### Risk 4: File collision on `tools/sdlc_session_ensure.py` — RETIRED (revision cycle 3)

**This risk no longer exists.** #2660 merged as `971ff1caf` before this lane started
building, so `tools/sdlc_session_ensure.py` is no longer contested. Its
`_acquire_run_lock_and_bind` now saves with `update_fields=["active_run_id",
"owned_run_ids"]` at `:498` — which this lane must simply not disturb.

Recorded because it changes a decision's justification, not the decision: the round-3
critique forbade placing the mint inside `_acquire_run_lock_and_bind` **on file-collision
grounds**, and those grounds are now gone. The mint still does not go there, for the three
standing reasons recorded in Task 3 (wrong layer, the lock contest has losing branches, no
shared state). A reviewer who notices the collision is moot should not conclude the
placement is therefore open.

**Live constraint that replaces it:** any write this lane adds via `session.save()` in
that file must appear in an explicit `update_fields` list, or it silently never persists —
the partial-save posture #2660 established is now the file's convention.

### Risk 5: `git ls-remote --heads origin` (full listing) is slow enough to matter in the router's poll loop

**Impact:** The router polls `next-skill` constantly; a full remote-ref listing on every
tick would add hundreds of milliseconds each time.
**Mitigation:** Rung 1 (recorded value) short-circuits every steady-state call with a
single direct-key `HGETALL` — **no git beyond target-repo resolution**, and none at all
when the caller passes `target_repo` or `GH_REPO` is set (revision cycle 4: the read path
resolves the repo via `resolve_target_repo_for_read`, which peeks the lease before
falling through to the env ladder). The full listing only runs in the ladder,
which only runs when the field is empty, which happens at most once per lane. The
existing per-request memo (`cached_target_repo_resolution`) pattern is available if the
dashboard fan-out ever needs it. A test asserts that a resolve against a ledger with a
recorded slug issues **zero** subprocess calls.

## Race Conditions

### Race 1: Concurrent first-resolve on an empty slug

**Location:** `tools/lane_identity.py::resolve_lane_slug` healing-write block.
**Trigger:** Two processes (e.g. the router poll and `ensure_session`, or two SDLC lanes'
tooling) both read an empty `PipelineLedger.slug` for the same issue and both walk the
ladder.
**Data prerequisite:** The `PipelineLedger` record must exist (`get_or_create` already
serializes its own create race via SETNX at `agent/pipeline_ledger.py:248` (`_acquire_create_lock`)).
**State prerequisite:** `slug` empty at read time.
**Mitigation:** SETNX on a dedicated non-Popoto key serializes the write. The winner
re-`load()`s (direct-key, index-independent) and writes only if still empty, with
`save(update_fields=["slug"])`. The loser waits briefly, re-`load()`s, and returns the
winner's value. Both callers return the same string. Directly modeled on the
`get_or_create` precedent in the same module.

### Race 2: `ensure_session` mints while a branch is being pushed under a different name

**Location:** `tools/sdlc_session_ensure.py::ensure_session` → `resolve_lane_slug`.
**Trigger:** A lane is created (slug minted as `sdlc-{N}`) at the same moment a builder
pushes `session/some-other-name`.
**Data prerequisite:** none.
**State prerequisite:** The ladder's remote probes ran before the push landed.
**Mitigation:** Accepted and bounded, not prevented. `ensure_session` runs at lane
**start**, before any build, so there is no builder to race. If it somehow occurs, the
recorded slug is authoritative and the builder's branch is the anomaly — which is
exactly the divergence this plan makes visible instead of silently wedging on. The
no-overwrite rule guarantees the recorded identity is stable once written.

### Race 3: `stage_states_json` clobbered by the slug write

**Location:** `PipelineLedger.save()`.
**Trigger:** The slug write lands between another process's read and write of
`stage_states_json`.
**Data prerequisite:** A populated `stage_states_json`.
**State prerequisite:** Concurrent stage-marker activity.
**Mitigation:** `save(update_fields=["slug"])` writes only that field, so the stage blob
is never part of the write. A test asserts that a slug write against a ledger with
populated `stage_states_json` leaves the blob byte-identical.

## No-Gos (Out of Scope)

- `[SEPARATE-SLUG #2718]` — nothing, actually: #2718 is **in** scope and closes with this
  PR. Listed here only to be explicit that it is not deferred.
- `[SEPARATE-SLUG #2663]` — manually unwedging lane sdlc-2663 / PR #2668. The fix
  unwedges it mechanically; the lane's own routing is its business.
- `[DESTRUCTIVE]` — renaming or deleting any of the 99 `session/sdlc-*` branches on
  origin. Irreversible, touches other agents' live lanes, and unnecessary: the code
  adopts existing names.
- `[DESTRUCTIVE]` — eager backfill of `PipelineLedger.slug` across all existing ledgers
  in the migration. A backfill would have to *guess* the shape for lanes with no branch
  and no PR, writing exactly the wrong-identity records this plan exists to prevent. The
  self-healing read covers them lazily and correctly.
- `[SEPARATE-SLUG #2738]` — consolidating the four hook validators' plan-doc resolution.
  Adjacent ("which plan doc is this") but a different symbol in a different layer, and
  already owned by an in-flight lane.
- `[SEPARATE-SLUG #2756]` — the `agent/sdk_client.py:474` dead-`or` copy-paste bug
  (`getattr(session,"slug",None) or getattr(session,"slug",None)`). Genuinely trivial,
  but it is an `AgentSession.slug` executor read, not SDLC lane identity, and fixing it
  here would widen the PR's blast radius into the session executor for no gain. **Issue
  #2756 filed at plan-revision time** — this is a tracked deferral, not a promise.
- `[SEPARATE-SLUG #2755]` — converting `reflections/sdlc_progress.py`'s lane *discovery*
  from branch-shape matching (`_SDLC_BRANCH_RE` at `:116`, `_list_open_sdlc_prs` at
  `:216`, `_issue_number_from_slug` at `:262`) to ledger-driven enumeration. This is the
  one remaining place where the system guesses at lane identity after this plan lands,
  and it is a real gap: a stalled lane on a human-named branch becomes invisible to the
  stall detector, silently. It is deferred because it is a **restructuring** of the
  discovery corpus, not a substitution — unlike its sibling
  `reflections/sdlc_upvote_lanes.py::_has_pr_on_branch`, which *is* a one-line
  substitution and is therefore **in scope** for this plan (see Technical Approach).
  **Issue #2755 filed at plan-revision time**, blocked on this PR merging.

## Update System

The `/update` skill needs one change: a Popoto schema migration, per the repo's
"Popoto Schema Migration Requirement".

- Add `_migrate_confirm_pipeline_ledger_slug_readable(project_dir)` to
  `scripts/update/migrations.py`, mirroring `_migrate_confirm_is_ledger_field_readable`
  (`:335`) exactly: load a small sample of `PipelineLedger` records and read `.slug` on
  each, proving Popoto's lazy-load descriptor healing resolves cleanly on rows written
  before the field existed. **Writes nothing.** Returns `None` on success (including
  "no ledgers to check"), an error string otherwise.
- Register it in the `MIGRATIONS` dict (`:999`) — required, `run_pending_migrations()`
  iterates that dict. It is recorded once in `data/migrations_completed.json`.
- No new dependency, config file, or secret is propagated. No `.env` change.
- No `scripts/remote-update.sh` change — the migration runs through the existing
  `run_pending_migrations` step.
- Existing installations need no manual step: the field is nullable with no default
  semantics beyond "empty means unresolved", and the self-healing read fills it on first
  use.

## Agent Integration

No new agent integration required.

- No new CLI entry point in `pyproject.toml [project.scripts]`. `tools/lane_identity.py`
  is a library consumed by existing `sdlc-tool` subcommands
  (`stage-query`, `next-skill`, `verdict`, `stage-marker`) and by
  `tools/sdlc_session_ensure.py`. All of those are already agent-reachable.
- The bridge (`bridge/telegram_bridge.py`) does not import this code and does not need to.
- The resolved slug becomes visible to the agent through the existing surface:
  `sdlc-tool stage-query --issue-number N` already emits `_meta`, and the recorded slug
  is added to that payload so an operator can see the lane's identity without a Python
  one-liner. That is a field addition to an existing response, not a new surface.
- Integration coverage: an end-to-end test that runs `sdlc-tool stage-query
  --issue-number N` against a ledger with a recorded slug and asserts `_meta.slug`
  carries it.

## Documentation

### Feature Documentation

- [ ] Create `docs/features/sdlc-lane-identity.md` — what a lane slug is, who mints it
  (`ensure_session`, once, at lane start), where it lives (`PipelineLedger.slug`), the
  adoption ladder in order, the conditional-on-empty / no-overwrite / no-lease rules, and
  the explicit statement that **both** `sdlc-{N}` and human-named slug shapes are real.
  Must include a "why there is no holder/liveness field here" note pointing at
  #2446/#2451, so a future reader does not add one.
- [ ] Add an entry to the `docs/features/README.md` index table.
- [ ] Update `docs/features/gh-stale-state-verdict-gate.md` if it references
  slug-derived branch probing (check; update or note no change needed).

### Repo Instruction Docs

- [ ] `CLAUDE.md` — the Session Management line currently says the slug "ties together
  the task list, branch, worktree, plan doc, and GitHub issue". Correct it: the lane slug
  ties together task list, branch, and worktree; the plan doc is linked by `tracking:`
  frontmatter and may carry a different name.
- [ ] `docs/sdlc/do-plan.md` "Slug Conventions" (`:145-149`) — currently states slugs are
  "derived from the plan filename". Correct to: the lane slug is recorded on the
  `PipelineLedger` and read, never derived; the plan filename is independent and linked
  by `tracking:`.
- [ ] **Moved-symbol sweep, not a list.** Run `AC-DOCSWEEP` and `AC-PROSE` (see
  Verification) and update every file they surface. Do **not** work from an enumeration
  in this plan — the repo's replicated-value rule says a moved-symbol cleanup closes out
  on a clean grep, and an enumeration here is the same defect as the one the plan is
  fixing. The prior draft of this section named exactly one file (`do-plan-critique.md`)
  while the sweep surfaces eight under `docs/features/` and `docs/sdlc/` alone, three of
  which reference the old home in *prose* that no import-shaped grep catches.
  Exit condition: `AC-PROSE` returns 0 and every `AC-DOCSWEEP` file names
  `tools/lane_identity`.
  - Known-load-bearing within that sweep, called out so it is not skimmed:
    `docs/sdlc/do-plan-critique.md:156` is an executable bash snippet importing
    `from tools._sdlc_utils import find_plan_path`. It is the shared-resolver contract
    with `_cli_record`, so a stale import there silently breaks the critique write path.
    `docs/features/sdlc-pipeline-portability.md` references the symbol twice and also
    documents the `_git_toplevel` helper the plans-dir ladder move touches — reconcile it
    against the retained-in-place decision recorded under Architectural Impact.
  - `docs/plans/completed/` is **out of scope** for the sweep. Those are historical
    records of shipped work and are correct as written; rewriting them would inject
    present-tense claims into a historical artifact.
- [ ] **Slug-derivation instruction sweep, not a list.** Run
  `grep -rn 'slug' .claude/skills-global/ docs/sdlc/` and route every instruction that
  tells an agent to *derive* a slug from a plan filename to the recorded lane slug
  instead. Known members at revision time, re-derive rather than trust:
  `.claude/skills-global/do-build/SKILL.md:110` ("derive `{slug}` from the plan
  filename"), `.claude/skills-global/do-pr-review/SKILL.md:83`, `docs/sdlc/do-merge.md:39`
  and `:137`, `docs/sdlc/do-build.md`, `docs/sdlc/do-patch.md`.
  Note: `.claude/skills-global/` is hardlinked to `~/.claude/skills/` by `/update`, so
  edits there propagate to every machine — use `Edit`, never write-and-rename, or the
  hardlink breaks and the live skill silently stays on pre-edit text.

### Inline Documentation

- [ ] `tools/lane_identity.py` module docstring stating the single-identity contract and
  the ladder order with the *reason* for each rung's position (particularly why rung 2
  precedes rung 3 — shape-agnosticism, per spike-2).
- [ ] Docstring on `PipelineLedger.slug` in the model's Fields block, stating: pure
  identity, single writer at lane start, self-healing conditional-on-empty, never
  overwritten, no lease required, and explicitly **not** a liveness signal.
- [ ] Delete the false-belief comments at `tools/sdlc_next_skill.py:346-349` and
  `tools/sdlc_stage_query.py:347-350` and replace with the true statement.

## Success Criteria

- [ ] `find_plan_path(2663)` returns `None` while `docs/plans/session-liveness-tick-counter.md`
  (tracking #2716) is present and mentions `#2663`. *(#2735 AC 1)*
- [ ] `find_plan_path(2716)` still returns that plan. *(#2735 AC 2)*
- [ ] A test pins the no-go-section case: a plan tracking issue A that names issue B in a
  "Not building" line never resolves as B's plan. *(#2735 AC 3)*
- [ ] `stage-query --issue-number N` no longer reports `plan_exists` / `revision_applied`
  sourced from another issue's plan. *(#2735 AC 4)*
- [ ] G8 no longer re-dispatches `/do-patch` for an issue whose branch is pushed under
  the correct slug. *(#2735 AC 5, #2718 primary)*
- [ ] A regression test covers a lane whose branch name diverges from its plan slug
  (`session/sdlc-{N}` with a differently-named plan doc) and asserts G8 does **not** fire
  when the branch is pushed. Per #2658's demonstrated-red posture, shown failing against
  current `main` before the fix lands. *(#2718 AC)*
- [ ] `PipelineLedger` carries a recorded `slug`, written once by `ensure_session`, and
  no consumer derives a slug from a plan filename.
- [ ] The self-heal adopts `sdlc-2663` for issue #2663 rather than inventing a competing
  identity, and never overwrites a recorded value.
- [ ] `stage-query --issue-number N` for an issue that is **not** a lane creates no
  `PipelineLedger` and writes no slug, and adds **no subprocess beyond the target-repo
  resolution `_compute_meta` already performs** (it passes its resolved `target_repo`
  through). Read paths never mint.
- [ ] `AC-MINT` returns exactly one hit — the mint rung in `tools/lane_identity.py`. All
  three prior minters (`reflections/sdlc_upvote_lanes.py`, `reflections/sdlc_progress.py`,
  `tools/valor_session.py`) are gone, verified by sweep rather than by checklist.
- [ ] `AC-HEAL` shows `allow_heal=True` at exactly the three sanctioned lane-start callers
  and nowhere in `tools/sdlc_stage_query.py`, `tools/sdlc_next_skill.py`,
  `tools/sdlc_verdict.py`, or `tools/sdlc_stage_marker.py`.
- [ ] `AC-TRACKING` exits 0, and the invariant has a durable owner —
  `tests/unit/test_plan_docs.py` — not just a one-shot Verification row.
- [ ] `ensure_session(N)` on a clean Redis leaves a `PipelineLedger` for N with a
  non-empty `slug`. The mint has a write target. *(cycle-2 blocker 1)*
- [ ] `resolve_lane_slug(N)` (heal off) for an issue with no ledger creates no ledger, and
  issues no subprocess when given `target_repo` or with `GH_REPO` set. Read paths stay
  inert. *(cycle-1 blocker 1, must not regress)*
- [ ] The read path and the heal path resolve the **same** ledger key by construction —
  both call `resolve_target_repo_for_read`. A test still pins it, and to be non-vacuous it
  must pin a lease whose `target_repo` **differs** from the env-ladder answer, then write
  via heal and read back via read. *(cycle-4 concern; made structural in cycle 5)*
- [ ] `resolve_lane_slug` returns `None` and writes nothing when `target_repo` resolves to
  `None`. No `None:{issue}` key is ever created on either arm. *(cycle-5 concern)*
- [ ] Both reflection callers pass `target_repo=repo`, so a non-`ai` project's lane is
  recorded under its own repo. A test with a non-`ai` project config asserts the key.
  *(cycle-5 blocker)*
- [ ] `_derive_slug_from_message` no longer exists anywhere in `tools/`; the "generic
  message → `None` slug" behavior is still pinned by
  `tests/unit/test_pm_session_refuse_no_issue.py`, and `resolve_lane_slug` is not called
  on that path. *(cycle-2 blocker 2)*
- [ ] A `NEEDS REVISION` CRITIQUE verdict with an unresolvable plan **raises**
  `CRITIQUE_PLAN_UNRESOLVABLE`; the same write at `READY TO BUILD` / `MAJOR REWORK` does
  not. *(concern 3)* — the stage-skip half of this criterion was **removed in cycle 4**;
  see Task 4 for why refusing there deletes stage skipping entirely.
- [ ] `_meta` carries `slug` and `slug_source` (`recorded` / `unresolved`), so an operator
  can tell "skipped because unresolvable" from "verified clean". *(concern 4)*
- [ ] `AC-TESTSCOPE` exits 0 — the sweep-driven test scope, including
  `tests/integration/`. *(concern 5)*
- [ ] Clean grep sweep: the `.stem` sweep shows no plan-path stem becoming a slug or
  branch name, no hardcoded `session/` prefix outside `lane_branch_name`, no
  `find_plan_path` import or prose reference to `tools._sdlc_utils` in live docs, no
  "never creates one" belief comment. Every zero-expecting anti-criterion was
  demonstrated red on `main` first.
- [ ] Tests pass (`scripts/pytest-clean.sh`)
- [ ] Documentation updated (`/do-docs`)
- [ ] PR body carries `Closes #2735` and `Closes #2718`.

## Team Orchestration

### Team Members

- **Builder (lane-identity core)**
  - Name: `identity-builder`
  - Role: The new field, the new module, the resolver and its ladder, the migration.
  - Agent Type: builder
  - Resume: true

- **Builder (consumer sweep)**
  - Name: `consumer-builder`
  - Role: Route every derivation site through the resolver; delete the false-belief
    comments; move `find_plan_path`.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `identity-tester`
  - Role: The regression tests, including the demonstrated-red #2718 case and the
    concurrency cases.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `identity-validator`
  - Role: Verify the ACs from both issues and run the grep sweeps.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `identity-documentarian`
  - Role: The feature doc plus the instruction-doc sweep.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Demonstrated-red regression tests

- **Task ID**: `test-red`
- **Depends On**: none
- **Validates**: `tests/unit/test_lane_identity.py` (create), `tests/unit/test_sdlc_next_skill.py`
- **Informed By**: spike-1 (all callers None-safe but three fail open), spike-2 (adoption ladder)
- **Assigned To**: `identity-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- Write the #2718 regression test: a lane with plan doc `some-other-name.md` tracking
  issue N, branch `session/sdlc-N` pushed, PATCH claimed completed — assert G8 does not
  fire. Run it against current `main` and **capture the FAIL output** for the PR body
  (#2658 demonstrated-red posture).
- Write the #2735 tests: `find_plan_path(B) is None` when only plan A (tracking A)
  mentions `#B`; `find_plan_path(A)` still resolves; the no-go-section case explicitly.
  Capture red output.
- Write the `_meta`-contamination test: `stage-query` for an issue with no plan reports
  `plan_exists: False` / `revision_applied: False` despite a foreign plan mentioning it.
  Capture red output.
- Do not write any production code in this task.

### 2. Field, module, resolver, migration

- **Task ID**: `build-core`
- **Depends On**: `test-red`
- **Validates**: `tests/unit/test_lane_identity.py`, `tests/unit/test_pipeline_ledger.py`
- **Informed By**: spike-2 (ladder order; worktree rung excluded as machine-local)
- **Assigned To**: `identity-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Add `slug = Field(null=True)` to `PipelineLedger` (`agent/pipeline_ledger.py`) with the
  full Fields-block docstring described under Documentation.
- Create `tools/lane_identity.py` with `resolve_lane_slug`, `lane_branch_name`, and the
  relocated `find_plan_path` (ONE rung -- `tracking:` only; the slug-named-file rung is
  dropped in cycle 3; `_is_ai_repo_fallback` and the bare-`#N`
  fallback deleted entirely). Preserve the plans-dir resolution ladder verbatim.
- Implement the conditional-on-empty healing write, modeled on the SETNX pattern at
  `agent/pipeline_ledger.py:248` (`_acquire_create_lock`). Use `save(update_fields=["slug"])`. Take no lease.
  Treat `""` and whitespace-only as empty.
- **Import `_git_toplevel` and `_resolve_target_repo` as module attributes, not as bound
  names (cycle 4).** Fourteen existing tests monkeypatch the literal
  `tools._sdlc_utils._git_toplevel` (`tests/unit/test_sdlc_utils.py:420,435,447,464,495,508,521,555,597,641`,
  `test_sdlc_env_vars.py:158,173`, `test_sdlc_stage_query.py:1067,1101`). If
  `tools/lane_identity.py` does `from tools._sdlc_utils import _git_toplevel` at module
  scope, the name binds at import time and every one of those patches goes inert — the
  relocated `find_plan_path` ladder tests would pass while testing nothing. Use
  `from tools import _sdlc_utils` then `_sdlc_utils._git_toplevel()`, or a function-local
  import. The Test Impact claim that these 14 sites need NO CHANGE is true **only** under
  that form.
- Add `_migrate_confirm_pipeline_ledger_slug_readable` to `scripts/update/migrations.py`
  mirroring `_migrate_confirm_is_ledger_field_readable` (`:335`) and register it in
  `MIGRATIONS` (`:1033`). Read-only probe; no backfill.

### 3. Mint at lane start

- **Task ID**: `build-mint`
- **Depends On**: `build-core`
- **Validates**: `tests/unit/test_sdlc_session_ensure.py`
- **Assigned To**: `identity-builder`
- **Agent Type**: builder
- **Parallel**: false
- In `tools/sdlc_session_ensure.py::ensure_session`, make the mint **one unconditional
  call** placed immediately after the validity guard (`if not issue_number or
  issue_number < 1: return {}`, `:645`) and **before** the `VALOR_SESSION_ID` env
  short-circuit at `:651`:

  ```python
  try:
      resolve_lane_slug(issue_number, allow_heal=True)
  except Exception as e:  # identity resolution must never fail an ensure
      logger.debug(
          "sdlc_session_ensure: lane slug resolution failed for #%s (%s: %s)",
          issue_number, type(e).__name__, e,
      )
  ```

  The call is idempotent and conditional-on-empty, so a single early invocation covers
  **all six** success return points (`:680, :735, :756, :779, :795, :874`) by
  construction and cannot double-write. It **must** swallow: `ensure_session`'s contract
  is to return a session dict, and a Redis or git failure inside identity resolution must
  not convert a successful ensure into `return {}`.
- **Mint-site decision — recorded (revision cycle 3).** The alternative was hooking
  `_acquire_run_lock_and_bind`, which is already documented (`:309-311`) as running
  before every return point (#1954/#2003). It is **rejected**, and not for the reason the
  round-3 critique gave. That reason (file collision with the in-flight #2660 lane on
  `:486-493`) is now **moot** — #2660 merged as `971ff1caf` and the fence is lifted; that
  helper now saves with `update_fields=["active_run_id", "owned_run_ids"]` at `:498`. The
  standing reasons are:
  1. **Wrong layer.** `_acquire_run_lock_and_bind` owns the *lease* — a transient,
     contested, expiring claim. The slug is *permanent identity*. Owner direction #4 for
     this lane is explicit that liveness belongs to the lease and the identity record
     must not re-create the `#2446`/`#2451` shape; minting inside the lock contest is
     exactly that conflation.
  2. **The lock contest has losing branches.** The helper returns `ISSUE_LOCKED` at
     `:475` and `RUN_BIND_FAILED` at `:531` *before* reaching the bind. A mint placed
     after the bind is skipped for every blocked caller; a mint placed before it fires
     even when the caller loses the contest. Neither is correct. A lane's identity does
     not depend on who currently holds its lease.
  3. **No shared state.** The slug write targets `PipelineLedger`, keyed by issue number
     alone. It needs no `session` object, so it has no reason to live in the helper that
     exists to bind one.
  The "two sites to keep in sync" objection does not apply: the `:645` call is not a
  second every-return-point mechanism. It is a single call on the function's only entry
  path, above every branch, which is a strictly weaker and more obviously correct
  invariant than "before every return".
- **`ensure_session` does not resolve `target_repo` itself and must not start.** The
  ledger key is built inside `resolve_lane_slug`, which calls
  `_resolve_target_repo()` from `tools/_sdlc_utils` — the same helper
  `sdlc_session_ensure.py` already imports at `:223` and `:359`. `ensure_session` passes
  only `issue_number`. Keeping repo resolution inside the resolver is what lets all three
  lane-start callers share one code path; pushing it to the callers would replicate it
  three times, which is the defect class this plan is closing. (The resolver's *read*
  path uses a different helper — `resolve_target_repo_for_read` — for the reason recorded
  under "Rung 1 branches on `allow_heal`".)
- Under `allow_heal=True` the resolver calls `PipelineLedger.get_or_create(target_repo,
  issue_number)`, so a lane-start call **creates the ledger if it does not yet exist**.
  This is the mint's write target (see Technical Approach, "Rung 1 branches on
  `allow_heal`"). Assert it in `tests/unit/test_sdlc_session_ensure.py`: after
  `ensure_session(N)` on a clean Redis, a `PipelineLedger` exists for N and its `slug` is
  non-empty.
- Stamp the resolved slug onto the created/adopted `AgentSession.slug` so the executor's
  existing slug readers see the same identity. The ledger remains authoritative; the
  `AgentSession` copy is a documented convenience mirror (see Open Questions, resolved).
- Remove the mint from `reflections/sdlc_upvote_lanes.py:489` and
  `reflections/sdlc_progress.py:707`. **`sdlc_progress.py:707`** substitutes in place to
  `resolve_lane_slug(issue_number, allow_heal=True, target_repo=repo)` — it sits at the
  respawn site for an issue already known to be a lane. **Assign the result to the
  surrounding `slug` variable**, not just to the `create_session` argument: `slug` is
  already in scope from the PR branch (`:788`) and is charged against `_bump_attempts`
  (`:728`) and the escalation/cooldown keys (`:824`, `:861`). Substituting only at the
  create rung would create the session under one name while attempts and cooldowns accrue
  under another.
- **`sdlc_upvote_lanes.py:489` is NOT an in-place substitution — the heal call moves
  (revision cycle 4, BLOCKER).** Line `:489` is the top of the candidate loop, *above*
  gate 1 (`:493`), gate 1.5 (`:502`), gate 1.6 (`:505`), gate 2 (`:508`), gate 3 (`:511`),
  gate 4 (`:515`), the budget check, and the announce/anchor block. Healing there would
  run `PipelineLedger.get_or_create` and permanently record a slug for **every scanned
  upvote candidate**, including every one the reflection declines seconds later — and
  because the write is no-overwrite, it could permanently fix an identity for an issue no
  lane ever starts. That contradicts three of this plan's own commitments: "minted exactly
  once at lane start", "non-lane issue → no ledger created", and "read paths never mint".
  Required shape:
  - At `:489`, resolve **once**, heal off, and let every gate below use that value:
    `slug = resolve_lane_slug(issue_number, target_repo=repo) or mint_lane_slug(issue_number)`.
    The gates need a name to look sessions up by; none of them may record one.
  - Move the healing call **past every gate**, after the Race-1 re-check at `:553-566`
    and immediately before `create_session(... slug=slug ...)` at `:577` — the first
    point at which the reflection has actually committed to starting a lane:
    `resolve_lane_slug(issue_number, allow_heal=True, target_repo=repo)`.
  - **The healed value must equal the probed value, so the heal call does not reassign
    `slug` (cycle 5).** Rung 1 returns the recorded slug and the probe already read it, so
    they agree; when nothing was recorded, the heal mints the same `sdlc-{N}` the probe
    fell back to. Reassigning would risk gates 1 (`:493`), 1.6 (`:505`) and the Race-1
    re-check (`:554`) having tested `_non_terminal_session_for` under one name while the
    session is created under another — silently losing duplicate-lane protection for a
    candidate that has a recorded human-named slug but no recorded stages (gate 2 only
    tests `stage_states_json`). Resolving once at `:489` is what makes them agree.
  - Both calls pass `target_repo=repo` (`:440`). Without it the reflection records a
    non-`ai` project's lane under `tomcounsell/ai` — see "ONE repo resolver" above.
  The distinction is the same one Task 4 draws: scanning is a search and may guess;
  starting a lane is a write and must record.
  - **Accepted exception:** healing at `:577` runs before `create_session`, which can
    itself fail (`:592-602`), leaving a ledger and a recorded identity for a lane that
    never started. This is a real but narrow departure from "non-lane issue → no ledger
    created". It is acceptable because by `:577` the issue is a *committed pickup target*
    that passed every gate, and the next tick retries it under the same recorded slug —
    which is the identity-stability property this whole lane exists to create. Recorded
    rather than hidden so a reviewer does not read it as an oversight.
- **Remove the third minter — by deletion, not by demotion.** `tools/valor_session.py:107`'s
  `_derive_slug_from_message` returns `f"sdlc-{N}"` and never calls `ensure_session`, so
  `valor-session create "handle issue #N"` mints a competing identity today. **Delete the
  function** (body *and* its `sdlc-{N}` docstring examples at `:92`) and replace it with
  `_issue_number_from_message(message) -> int | None`. Retaining it as a fallback is not
  an option — its body is an `f"sdlc-` literal that keeps `AC-MINT` red forever, and its
  fallback arm is dead by construction (it returns `None` on exactly the branch that
  would call it).
- **`cmd_create` reads; it does not heal (revision cycle 3).** The auto-derive branch at
  `tools/valor_session.py:588` fires for **any** eng session whose `--message` merely
  matches `_ISSUE_REF_RE`. Under an earlier draft of this task it called the resolver with
  `allow_heal=True`, so asking `valor-session create --role eng "what's going on with
  issue #2663?"` would run `PipelineLedger.get_or_create` and write a permanent lane
  identity for an issue that is not a lane — contradicting this plan's own Flow example
  ("Non-lane issue → no ledger created, nothing minted") and the Success Criterion "Read
  paths never mint". Because the write is no-overwrite, a conversational session could
  permanently fix a wrong identity before the real lane ever started. The shape is:

  ```python
  issue_number = _issue_number_from_message(message)
  if issue_number:
      # No resolver call at all: this is a conversational seam, not a lane start.
      slug = mint_lane_slug(issue_number)
  ```

  The session gets exactly the `sdlc-{N}` slug it needs for a worktree (eng sessions are
  rejected without one, `#1272`), **no ledger is created, and nothing is recorded**. This
  is a pure no-behavior-change refactor: the same string by the same rule as today, just
  sourced from the one function that owns the literal so `AC-MINT` stays at one hit.
- **`cmd_create` does NOT adopt a recorded slug (revision cycle 4).** A cycle-3 draft had
  it call `resolve_lane_slug(issue_number) or mint_lane_slug(issue_number)` and described
  adopting a recorded identity as "a pure alignment win". It is not. `tools/valor_session.py:639-644`
  feeds the slug straight into `get_or_create_worktree(repo_root, slug)`, so adopting a
  live lane's human-named slug would drop a conversational session
  (`"what's going on with issue #2663?"`) into **that lane's worktree and branch** —
  precisely the shared-slug worktree contention #1915 Defect 2 fixed. Today the session
  gets its own `.worktrees/sdlc-2663` and stays out of the way; that property is worth
  more than CLI-side slug alignment, and #2718's actual defect is in G8's branch probe,
  not here. If a real lane later starts for that issue, `ensure_session`'s ladder reaches
  the same `sdlc-{N}` string via rung 3 or rung 5 anyway.
- **Do not "fix" this by having `cmd_create` call `ensure_session`.** That was the
  round-3 critique's preferred option and it is worse: `ensure_session` acquires the
  **issue lease**, so a conversational `valor-session create` mentioning `#2663` would
  contest and hold a lock on someone else's lane. Trading a stray ledger row for a stray
  lease is a strictly worse bargain.
- `mint_lane_slug(issue_number) -> str` is the pure, write-free `f"sdlc-{N}"` constructor
  exported by `tools/lane_identity.py`, and is the same function `resolve_lane_slug` uses
  for its own rung 5. It keeps `AC-MINT` at exactly one literal while giving `cmd_create`
  a non-recording fallback. This is **not** a fifth inventor: one function, one home, one
  literal — which is the fix's whole shape.
- Update `_derive_sdlc_metadata`'s docstring (`:110-133`), which names
  `_derive_slug_from_message`.
- Scrub the two `sdlc-{N}` literals in `reflections/sdlc_upvote_lanes.py` — the comment at
  `:133` and the docstring at `:329` — or `AC-MINT` returns 3+ hits after a correct build.
- **Grep-verifiable exit condition for this task**, not the three bullets above: `AC-MINT`
  returns exactly one hit, in `tools/lane_identity.py`. Re-run the sweep; a fourth minter
  the plan did not find is caught by the grep, not by this list.

### 4. Consumer sweep

- **Task ID**: `build-consumers`
- **Depends On**: `build-core`
- **Validates**: `tests/unit/test_sdlc_next_skill.py`, `tests/unit/test_sdlc_stage_query.py`
- **Informed By**: spike-1 (the PLAN/PATCH split; the three fail-open sites)
- **Assigned To**: `consumer-builder`
- **Agent Type**: builder
- **Parallel**: true
- `tools/sdlc_next_skill.py`: split the PLAN artifact check (plan path from
  `find_plan_path`) from the PATCH artifact check (branch from
  `lane_branch_name(resolve_lane_slug(N))`); change `_check_branch_pushed` to take a full
  branch name; make `branch_exists` (`:355-367`) read the recorded slug; fix the warning
  strings to name the branch actually probed; add the missing `logger.debug` to the bare
  `except Exception: pass` at `:329`; add the no-op debug record.
- `tools/sdlc_stage_query.py`: replace the dead `getattr(session, "slug", None)` at `:487`
  with `resolve_lane_slug(issue_number)`; surface the recorded slug in the `_meta` payload
  as `slug`, alongside `slug_source` (`recorded` when rung 1 answered, `unresolved` when
  it did not) — see "Mid-pipeline lanes" below.
- **Close the ONE fail-open site** (spike-1, Technical Approach): `tools/sdlc_verdict.py`
  raises `CritiqueFindingsMissingError` with code `CRITIQUE_PLAN_UNRESOLVABLE` on
  CRITIQUE + exact `NEEDS REVISION` + `plan_path is None`. It is in this task because it
  imports the moved `find_plan_path` and is therefore already edited here. Tests: a
  `NEEDS REVISION` write with an unresolvable plan must raise, and a `READY TO BUILD` /
  `MAJOR REWORK` write with the same unresolvable plan must **not**.
- **`tools/sdlc_stage_marker.py` gets its import retargeted and NOTHING ELSE (revision
  cycle 4, BLOCKER).** A cycle-3 draft told the builder to "extend the existing
  lookup-failure refusal to the resolvable-but-absent case". Read against the actual
  function (`:345-362`), that instruction deletes stage skipping outright: precondition 1
  already refuses whenever `plan_path is not None`, so `plan_path is None` is the **only**
  branch that lets a skip proceed. Refusing there too means every skip refuses, always.
  The qualifier "for the stages this matters for" named no stages and no test pinned it,
  so the instruction read as settled while being unbuildable.
- Skip-gating is a **different concern from lane identity** and this lane has no standing
  to change it. The skip path already carries affirmative evidence checks (recorded
  verdict or dispatch, `:364+`); tightening it further belongs in its own lane against its
  own issue. If a future lane takes it up, the discriminating signal is "the issue has a
  recorded lane slug but no resolvable plan" — not "no plan resolved", which is also what
  a genuinely plan-less issue looks like.
- Update all `find_plan_path` importers to `tools.lane_identity` and **delete** the
  symbol from `tools/_sdlc_utils.py` — no re-export shim.
- **`_check_plan_committed_on_main` is in the blast radius (cycle 4).**
  `tools/sdlc_next_skill.py:166-174` currently takes a *slug* and builds
  `git show main:docs/plans/{slug}.md`. The PLAN/PATCH split hands it a plan path from
  `find_plan_path`, which returns an **absolute** `Path` — `git show` needs a
  repo-relative one. Change the signature to take a repo-relative path and do the
  conversion at the call site with `Path(plan_path).relative_to(_target_repo_cwd())` —
  spell the `Path()` cast: `find_plan_path`'s return is consumed as
  `Path(plan_path).stem` at `tools/sdlc_next_skill.py:203-205`, so callers do not assume
  it is already a `Path`. Guard the `ValueError` for a plan resolved outside the target
  repo (skip the check rather than raise). Without this the PLAN artifact check breaks in a new way while fixing the old
  one.
- Delete the false-belief comments at `tools/sdlc_next_skill.py:346-349` and
  `tools/sdlc_stage_query.py:347-350`; replace with the true statement.
- Fix the reflections' *discovery* guess:
  `reflections/sdlc_upvote_lanes.py::_has_pr_on_branch` (`:328-344`) takes
  `head = lane_branch_name(resolve_lane_slug(issue_number) or mint_lane_slug(issue_number))`
  (heal off). Update the function's docstring, which currently asserts the branch is
  `session/sdlc-{N}`. `reflections/sdlc_progress.py`'s corpus-filter discovery is
  deferred to #2755 (see No-Gos).
- **This site FALLS BACK; it does not skip (revision cycle 4, BLOCKER).** A cycle-3 draft
  had it skip the `--head` leg when no slug is recorded. That silently disables upvote
  **gate 4** for exactly the population the gate screens: `_has_pr_on_branch` is called at
  `:515`, *after* gate 2 (`_ledger_has_recorded_stage`, `:508`) has already `continue`d
  every candidate that HAS recorded stages. So every candidate reaching the call has no
  recorded stage and therefore no recorded slug — heal-off resolution returns `None` for
  all of them, the leg is skipped, and the gate never fires. The consequences are
  duplicate lane pickup on an issue that already has an open PR, and the loss of the
  "merged PR but still open, likely missing `Closes #N`" finding.
- The general rule this establishes: **a site that SEARCHES may guess; a site that WRITES
  identity may not.** Probing `session/sdlc-{N}` costs one `gh` call and records nothing,
  so a wrong guess is free. `mint_lane_slug` is the right fallback wherever a miss is
  merely a miss. Reserve no-op-on-unresolvable for sites where acting on a guessed name is
  itself harmful — G8's PATCH artifact check and `branch_exists`, where a failed probe
  force-dispatches `/do-patch` and wedges the lane, which is #2718's actual defect.
- Every consumer edit in this task passes `resolve_lane_slug` with the **default
  `allow_heal=False`**. A consumer that heals is the Blocker-1 defect.
- **Grep-verifiable exit conditions**, not this list: the `.stem` sweep, `AC-BELIEF`,
  `AC-HEAL`, and both stale-import rows all pass (see Verification).

### 5. Backfill `tracking:` frontmatter

- **Task ID**: `build-backfill`
- **Depends On**: none
- **Validates**: the "every plan resolvable" Verification row
- **Assigned To**: `identity-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- **Parallel: false as of cycle 4.** `identity-tester` also owns `test-red`, which is also
  startable at t=0, so leaving this `Parallel: true` re-creates the one-agent-two-tasks
  shape cycle 3 fixed, one notch smaller. `Depends On: none` still holds, so this runs
  whenever `identity-tester` is free; it is not serialized behind `build-consumers` and
  does not lengthen Task 7's critical path.
- **Reassigned in revision cycle 3.** This task was co-assigned to `consumer-builder`
  alongside Task 4, both marked `Parallel: true`. One agent cannot run two tasks in
  parallel, so a scheduler honoring the flag either silently serializes them or dispatches
  the same agent twice. It is worse than cosmetic here: Task 5 commits on `main` while
  Task 4 works on the lane branch, so co-assignment forces a mid-task checkout switch in a
  worktree on a machine running several other live SDLC lanes. `identity-tester` already
  owns new test files, and Task 5's deliverable is three one-line frontmatter edits plus
  one new test. Deliberately **not** resolved by serializing Task 5 behind Task 4: Task 7
  depends on `build-backfill`, so serializing would lengthen the critical path for nothing.
**Scope, deliberately bounded.** Removing the bare-`#N` fallback makes `tracking:` the
only general way a plan resolves, so *some* backfill is load-bearing for this lane. The
prior draft then grew past that into repo-wide docs governance — relocating an audit doc
and fixing its inbound links, **filing a new GitHub issue** for a plan whose purpose only
a human can decide, and standing up a new hook-validator control surface. None of that is
required by any AC in #2735 or #2718, and the "or file one" branch made a pending human
decision an exit criterion for this lane. Cut to three one-line backfills plus one test:

- **Three one-line `tracking:` backfills**, all mechanical, no decision required:
  - `sdlc-1111.md` — #1111, closed. Backfill `tracking:`.
  - `session-type-pm-rename.md` — #648, closed. Backfill `tracking:`.
  - `keyfield-migration-fix.md` — tracks a *popoto* repo issue. Record the full cross-repo
    issue URL (the pattern accepts `issues/N`, so a cross-repo URL passes on its own terms).
- **Two files are explicitly NOT resolved here.**
  `resilience-simplification-three-tier.md` (literal placeholder `tracking: none yet`) and
  `session-recovery-observation-audit.md` (an audit document, not a lane plan) both need a
  human call this lane has no standing to make. They go into the
  `NON_LANE_PLANS` frozenset described below, with a comment naming each and why.
  **This reverses Decision 2** (which had said "move the audit doc out of `docs/plans/`"):
  a file move plus inbound-link fixes is docs governance, and an exclusion set is
  enforceable today without importing a pending decision. Named as a deviation so the
  decider can rule; the invariant is unchanged either way.
- **The exclusion set gets exactly one home (revision cycle 3).** Create
  `tools/plan_doc_scope.py` exporting `NON_LANE_PLANS: frozenset[str]`, holding the two
  filenames with a comment naming each and why. Both consumers **import** it: the test
  below, and the `AC-TRACKING` anti-criterion. An earlier draft defined the frozenset in
  the test and *restated* it as an inline literal in `AC-TRACKING` — while commenting that
  the test owned it. That is this plan's own diagnosed root cause (several places
  inventing the same value) reproduced inside the fix's verification: the next excluded
  plan doc would be added to one copy and not the other, and the Verification row would
  then disagree with the test it exists to prove. A non-test module rather than the test
  file itself, so the AC does not import from `tests/` off-suite.
- **Durable owner: a test, not a hook validator.** Add
  `tests/unit/test_plan_docs.py::test_every_plan_doc_carries_resolvable_tracking`
  asserting every `docs/plans/*.md` minus `NON_LANE_PLANS` (imported, not restated)
  carries a `#N` or `issues/N` token. A hook validator is a new control surface with its
  own PreToolUse wiring and belongs in its own lane. A test is the minimum acceptable
  outcome — a bare Verification row runs once at build time and nothing re-checks it when
  the next plan doc lands.
- **Exit condition**: that test passes, and `AC-TRACKING` (which imports the same
  exclusion set) exits 0. Re-derive by running it; the three-file list above is a
  revision-time snapshot for orientation.
- **Split commit, then sync — the ordering is load-bearing (revision cycle 3).** The three
  `tracking:` frontmatter edits are plan-doc edits and commit **directly on main**, per the
  repo convention. But `tools/plan_doc_scope.py` and `tests/unit/test_plan_docs.py` are
  code and commit on the lane branch. That split creates a trap the prior draft did not
  state: `AC-TRACKING` globs `docs/plans/*.md` in the *working tree*, and
  `tests/unit/test_plan_docs.py` is a new branch-side file asserting over frontmatter
  backfilled on `main` — so main-side backfills are **invisible** to the branch-side
  validator and the lane's own suite run fails on a correct build. Required sequence:
  1. Commit the three `tracking:` backfills on `main`; push.
  2. `git -C <worktree> merge --ff-only origin/main` to carry them onto `session/sdlc-2735`.
  3. Only then do Tasks 7 and 9 run their validation.
  Task 7's preamble states this ordering as a precondition. The alternative — having the
  test read plan docs from the `main` ref rather than the working tree — is a larger
  change and diverges from how every other main-committed plan edit reaches a lane branch
  here.

### 6. Test rewrite

- **Task ID**: `test-rewrite`
- **Depends On**: `build-consumers`, `build-mint`
- **Assigned To**: `identity-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- Rewrite `TestBranchExistsCanonicalShape` (`tests/unit/test_sdlc_next_skill.py:117-176`)
  against the recorded slug. `test_false_when_only_fabricated_shape_present` **inverts**.
- Retarget every `monkeypatch.setattr("tools._sdlc_utils.find_plan_path", ...)` to
  `tools.lane_identity.find_plan_path`.
- Add the concurrency cases: two concurrent resolvers → exactly one write, identical
  returns; a slug write leaves `stage_states_json` byte-identical; a resolve against a
  recorded slug issues zero subprocess calls.
- Add the Failure Path Test Strategy cases.
- Confirm every test from task 1 now passes.

### 7. Validation

- **Task ID**: `validate-all-acs`
- **Depends On**: `test-rewrite`, `build-backfill`
- **Assigned To**: `identity-validator`
- **Agent Type**: validator
- **Parallel**: false
- **Precondition (revision cycle 3): the main-side backfills must already be merged into
  the lane worktree.** Task 5 commits three `tracking:` frontmatter edits on `main`, and
  this task's validators (`AC-TRACKING`, `tests/unit/test_plan_docs.py`) read the
  *working tree* on `session/sdlc-2735`. Before running anything below, confirm
  `git -C <worktree> merge --ff-only origin/main` has landed Task 5's main commit. Without
  it, both checks fail on a correct build and the failure looks like a code defect.
- Verify each AC from #2735 and #2718 individually against the built code.
- Run every Verification-table command and every `AC-*` block command, and report actual
  output. Report **both** the pre-fix (`main`) and post-fix output for each zero-expecting
  anti-criterion. Any zero-expecting row that was already green on `main` is a **failed**
  anti-criterion — report it as a blocker rather than counting it as a pass.
- Confirm the demonstrated-red output from task 1 is captured for the PR body.
- Confirm no live lane's worktree, branch, or ledger was touched (three other SDLC lanes
  run concurrently on this machine).

### 8. Documentation

- **Task ID**: `document-feature`
- **Depends On**: `validate-all-acs`
- **Assigned To**: `identity-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the whole `## Documentation` section. Both doc tasks close on a **sweep**
  (`AC-DOCSWEEP` / `AC-PROSE` and the slug-instruction grep), not on the orientation
  enumerations written there.
- Pay particular attention to `docs/sdlc/do-plan-critique.md:156` — a stale import there
  silently breaks the critique write path — and to
  `docs/features/sdlc-pipeline-portability.md`, which references the moved symbol twice
  and also documents `_git_toplevel`.
- `.claude/skills-global/` files are hardlinked to `~/.claude/skills/`: use `Edit`, never
  write-and-rename, or the live skill silently stays on pre-edit text.

### 9. Final validation

- **Task ID**: `validate-final`
- **Depends On**: `document-feature`
- **Assigned To**: `identity-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run all Verification commands.
- Verify every Success Criterion including the documentation ones.
- **Observe the live reproduction.** Run `sdlc-tool next-skill --issue-number 2663` and
  confirm it no longer returns `/do-patch`, and that `sdlc-tool stage-query --issue-number
  2663` no longer reports `revision_applied` sourced from #2716's plan. This is the
  outcome that motivated both issues, and every other criterion is a grep or a unit test.
  **Observation only** — the plan forbids manual unwedging (see Rabbit Holes), so do not
  reset dispatch counters, touch the worktree, or modify the lane's ledger. If it still
  returns `/do-patch`, report it as a blocker rather than intervening.
- Confirm the PR body carries both `Closes #2735` and `Closes #2718`.

## Verification

| Check | Command | Expected |
|---|---|---|
| Tests pass | see `AC-TESTSCOPE` below | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| #2735 AC 1+2 | `SDLC_TARGET_REPO=$PWD .venv/bin/python -c "from tools.lane_identity import find_plan_path; assert find_plan_path(2663) is None, find_plan_path(2663); assert find_plan_path(2716) is not None"` | exit code 0 |
| Ledger carries the field | `.venv/bin/python -c "from agent.pipeline_ledger import PipelineLedger; assert 'slug' in PipelineLedger._meta.fields"` | exit code 0 |
| Migration registered | `grep -c 'confirm_pipeline_ledger_slug_readable' scripts/update/migrations.py` | output > 1 |
| Anti-criterion: no slug derived from a plan filename | `grep -rnE '\.stem\b' tools/ agent/ reflections/` | every hit reviewed by hand; **zero** hits where the `.stem` of a plan path becomes a slug or a branch name. Broadened from the old literal `Path(plan_path).stem`, which a rename to `plan_path.stem` defeated. |
| Anti-criterion: no stale import path | see `AC-STALEIMPORT` below | match count == 0 |
| Anti-criterion: no stale prose reference to the moved symbol | see `AC-PROSE` below | match count == 0 |
| Anti-criterion: docs sweep complete | see `AC-DOCSWEEP` below | every listed file names `tools/lane_identity`, none names `tools/_sdlc_utils` |
| Anti-criterion: false belief deleted | see `AC-BELIEF` below | match count == 0 |
| Anti-criterion: fallback mechanism deleted | see `AC-FALLBACK` below | match count == 0 |
| Anti-criterion: no hardcoded prefix in probes | see `AC-PREFIX` below | **hand-review row, not a count row** (21 hits on `main`, mostly test fixtures and prose). Every hit in `tools/` and `reflections/` that constructs a branch name to *act on* must route through `lane_branch_name`; `tools/lane_identity.py` is the only place the literal prefix may appear in constructing code. |
| Anti-criterion: no holder/pid/liveness field added | see `AC-LIVENESS` below | match count == 0 |
| Anti-criterion: no eager slug backfill in migration | see `AC-BACKFILL` below | match count == 0 |
| Anti-criterion: no read path heals | see `AC-HEAL` below | hits are **exactly** the three sanctioned lane-start callers; zero hits in `tools/valor_session.py`, `tools/sdlc_stage_query.py`, `tools/sdlc_next_skill.py`, `tools/sdlc_verdict.py`, `tools/sdlc_stage_marker.py` |
| Anti-criterion: no `sdlc-{N}` construction outside the resolver | see `AC-MINT` below | the only hit is the mint rung inside `tools/lane_identity.py` |
| Every plan resolvable | see `AC-TRACKING` below | exit code 0 |

**Pipe-escaping warning.** The table cells above cannot contain a literal `|` — markdown
would split the cell. Every command whose regex needs alternation therefore lives in the
fenced block below, **unescaped and runnable as written**. Do not copy a `\|` out of a
table cell into a `grep -E`: inside an ERE, `\|` is a *literal pipe character*, not
alternation, which is precisely how the prior draft's liveness-field guard came to be
vacuous.

```bash
# AC-TESTSCOPE — the test row is sweep-driven so it cannot drift from the
# Test Impact sweep it is supposed to prove. A fixed four-file list certified
# a red suite: the module move breaks monkeypatch targets in six more files
# the list never ran (test_sdlc_stage_marker.py carries SEVERAL patch sites --
# re-derive by sweep, never from an enumeration: the count has drifted every
# revision as main moved, 5 -> 7 -> 6 as of cycle 3 -- plus test_sdlc_verdict.py,
# test_sdlc_utils.py, test_sdlc_env_vars.py, and TWO integration files).
# tests/integration/ MUST stay in scope: the module move is an import-path
# change and the integration tests import the real symbol, so a unit-only
# scope cannot prove it.
scripts/pytest-clean.sh \
  $(grep -rl 'find_plan_path\|lane_identity\|resolve_lane_slug' tests/ | tr '\n' ' ') \
  tests/unit/test_lane_identity.py \
  tests/unit/test_pm_session_auto_slug.py \
  tests/unit/test_pm_session_refuse_no_issue.py -q     # expect exit 0
# The three explicit files are appended because they may not contain the swept
# tokens: test_lane_identity.py is new, and the two valor-session files exercise
# cmd_create's slug seam through the CLI rather than by importing the resolver.

# AC-PROSE / AC-DOCSWEEP — live docs must not name the symbol's old home.
# Scope EXCLUDES docs/plans/completed/: those are historical records of shipped
# work and are correct as written. Rewriting them would be exactly the
# "historical artifacts in docs" the repo forbids, in reverse.
grep -rn '_sdlc_utils.*find_plan_path' docs/features/ docs/sdlc/ | wc -l   # AC-PROSE, expect 0
grep -rln 'find_plan_path' docs/features/ docs/sdlc/                      # AC-DOCSWEEP
# Every file AC-DOCSWEEP lists must be read and updated. Do not work from an
# enumerated list in this plan — re-run the grep at build time; the set will
# have moved. Verified at cycle-2 revision time: 8 files under docs/features/
# and docs/sdlc/. Two are called out under ## Documentation because they are
# load-bearing rather than descriptive; the rest are found by the sweep.

# AC-STALEIMPORT — no live importer still names the symbol's old home.
# Lives here, not in a table cell: the cell form needed `\|` for the shell
# pipes, and in a shell `\|` is an escaped LITERAL pipe passed as an argument,
# so the command breaks. Both --include globs are single-quoted (zsh would
# otherwise glob-expand them, abort the pipeline, and print 0 -- a vacuous PASS).
grep -rn 'from tools._sdlc_utils import find_plan_path' \
  --include='*.py' --include='*.md' . | grep -v '^./.worktrees/' | wc -l   # expect 0
# Under-counts by construction: a `from tools import _sdlc_utils` + attribute
# form would not match. Pair with AC-TESTSCOPE; never treat as exhaustive.

# AC-FALLBACK — the _is_ai_repo_fallback mechanism is gone
grep -rn '_is_ai_repo_fallback' tools/ tests/ | wc -l                      # expect 0

# AC-PREFIX — the `session/` literal appears in constructing code exactly once.
# HAND-REVIEW row: 21 hits on main, most of them test fixtures and prose that
# legitimately name a branch. The question each hit must answer is "does this
# BUILD a branch name that something then acts on?" -- if yes it routes through
# lane_branch_name(). The cycle-4 form of this row was itself vacuous: it lived
# in a table cell as `'"session/\|f"session/\|session/\{'`, and inside an ERE a
# `\|` is a literal pipe, not alternation, so it searched for one impossible
# string and returned 0 on any tree.
grep -rnE '"session/|f"session/|session/\{' tools/ reflections/
# Two sites the cycle-3 row (`ls-remote.*session/`) missed, both kept and made
# newly functional by this plan, both of which MUST route through lane_branch_name:
#   tools/sdlc_next_skill.py:367  branch_exists  f"session/{slug}" in branch_names
#                                                (a `git branch -a` probe, not ls-remote)
#   tools/sdlc_stage_query.py:369 _lookup_pr     "--head", f"session/{slug}"

# AC-BELIEF — the false "repo never creates session/sdlc-{N}" comments are gone
grep -rnE 'never creates one|this repo never creates' tools/ tests/ | wc -l   # expect 0

# AC-LIVENESS — no holder/pid/host/heartbeat/last_seen field on the ledger
grep -nE '^[[:space:]]+(holder|pid|host|heartbeat|last_seen)[[:space:]]*=' \
  agent/pipeline_ledger.py | wc -l                                            # expect 0

# AC-BACKFILL — the migration is a read-only probe, it writes nothing
grep -n 'slug' scripts/update/migrations.py | grep -cE 'save|create|update_fields'  # expect 0
# This row guards against ADDING something, so like AC-LIVENESS it is green on main
# (today `grep 'slug' scripts/update/migrations.py` matches nothing at all) and must
# STAY green -- the demonstrated-red rule does not apply to it. Note for Task 7:
# `grep -c` EXITS 1 when the count is zero, so a passing run reports a nonzero exit
# code. Check the printed count, not the exit status.

# AC-HEAL — only lane-start paths opt into healing
grep -rn 'allow_heal=True' tools/ agent/ reflections/
# expect exactly THREE sites: tools/sdlc_session_ensure.py,
#                 reflections/sdlc_upvote_lanes.py, reflections/sdlc_progress.py
# tools/valor_session.py is deliberately NOT on this list (revision cycle 3):
# cmd_create's auto-derive fires on any prose issue mention, so it reads with
# heal OFF and falls back to mint_lane_slug() without recording. See Task 3.

# AC-MINT — the lane-slug literal is constructed in exactly one place.
# The trailing filter excludes OTHER `sdlc-` namespaces by shape (not by site):
# sdlc-local-{N} session ids, the sdlc-cold-issue-marker-{N} synthetic id, and
# the sdlc-{stage} tag form are unrelated identifiers and stay where they are.
grep -rnE 'sdlc-\{|sdlc-%s|"sdlc-" *\+|f"sdlc-' tools/ agent/ reflections/ \
  | grep -vE 'sdlc-local-|sdlc-cold-issue-marker-|sdlc-\{stage\}|sdlc-progress-check'
# expect exactly one hit, the mint rung in tools/lane_identity.py (inside
# mint_lane_slug, which is the sole home of the literal).
#
# CYCLE-4 TRAP, read before writing any prose in tools/ agent/ reflections/:
# this ERE matches PROSE, not just code. The plan requires replacement comments
# in sdlc_next_skill.py / sdlc_stage_query.py, a PipelineLedger.slug field
# docstring, and a lane_identity.py module docstring -- all inside this AC's
# scope. Writing "both sdlc-{N} and human-named shapes occur" in any of them
# fails the AC on a CORRECT build, and the tempting escape (widen the trailing
# filter) is exactly the vacuous-anti-criterion failure this plan devotes a
# section to. Spell it "sdlc-<issue>" or "issue-derived (sdlc-N)" in all prose;
# neither form matches. Tasks 3, 4 and the Inline Documentation checklist say so.
# Verified red at cycle-2 revision time: 8 hits —
#   tools/valor_session.py:92 (docstring), :107 (the minter body)  -> deleted, Task 3
#   tools/sdlc_stage_query.py:349 (false-belief comment)           -> deleted, Task 4
#   reflections/sdlc_progress.py:707 (mint)                        -> resolver, Task 3
#   reflections/sdlc_upvote_lanes.py:489 (mint)                    -> resolver, Task 3
#   reflections/sdlc_upvote_lanes.py:133 (comment), :329 (docstring), :344
#     (the _has_pr_on_branch --head literal)                       -> Task 3/4 scrub
# Every one is removed or rewritten by this plan; none may be excused by
# widening the trailing filter.

# AC-TRACKING — every LANE plan doc carries a resolvable tracking issue.
# The exclusion set has exactly ONE home: NON_LANE_PLANS in tools/plan_doc_scope.py.
# Both this anti-criterion and tests/unit/test_plan_docs.py IMPORT it (revision
# cycle 3). An earlier draft restated the frozenset as an inline literal here
# while commenting that the test owned it -- reproducing, inside this fix's own
# verification, the replicated-value defect the fix exists to close.
.venv/bin/python -c "
import pathlib, re, sys
from tools.plan_doc_scope import NON_LANE_PLANS as NON_LANE
pat = re.compile(r'^tracking:.*(?:#\d+|issues/\d+)', re.M)
bad = [p.name for p in pathlib.Path('docs/plans').glob('*.md')
       if p.name not in NON_LANE and not pat.search(p.read_text())]
print(len(bad), bad)
sys.exit(1 if bad else 0)"
# Requires a real #N or issues/N token. The literal 'tracking: none yet' would
# FAIL if it were in scope — the prior draft's '^tracking:\s*\S' accepted it,
# green-lighting the exact case Task 5 exists to fix; the regex stays tight and
# the file is excluded by name, not by a loose pattern.
# Verified red at revision time: 3 files (sdlc-1111.md, session-type-pm-rename.md,
# keyfield-migration-fix.md) — the three Task 5 backfills.
```

**Every zero-expecting anti-criterion must be demonstrated RED against current `main`
before the fix lands.** A grep that never matches is indistinguishable from one that
passes; two rows in the prior draft of this table were vacuous for exactly that reason
(`AC-LIVENESS`'s escaped pipes, and a `Path(plan_path).stem` literal that a variable
rename defeated). Task 7 captures the pre-fix output of each row alongside the post-fix
output. **A zero-expecting row that is already green on `main` is a FAILED
anti-criterion** and must be rewritten before build proceeds — it is proving nothing.

Two rows are *positive* criteria and are green-on-main by construction, so the red rule
does not apply to them: `AC-HEAL` (0 hits on `main` because the parameter does not exist
yet; after the fix it must name exactly the three sanctioned callers) and the docs-sweep
row. `AC-LIVENESS` **and `AC-BACKFILL`** are the deliberate exceptions among zero-expecting
rows — it guards
against *adding* something, so it is green on `main` and must stay green; its value is
that the fixed ERE can now actually fire, which the fixed spelling is verified to do
against a synthetic `holder = ...` line. **Every count below was re-derived in cycle 5 using the exact command forms now in the
fenced block**, after three recorded counts were traced to commands that differed from the
ones in the table. Re-derive again at build time: `main` moves, and two of these have
already drifted twice.

| Row | Count on `main` (cycle 5) | Note |
|---|---|---|
| `AC-STALEIMPORT` | **30** | was 28 (cycle 2), 29 (cycle 4) — drifts with `main` |
| `AC-PREFIX` | **21** | hand-review row, not a zero row; the cycle-3 "2" came from the narrower `ls-remote` form and the cycle-4 form was vacuous |
| `AC-FALLBACK` (`_is_ai_repo_fallback`) | **4** | stable |
| `AC-BELIEF` | **2** | stable |
| `AC-MINT` | **8** | stable |
| `AC-PROSE` | **3** | stable |
| `AC-TRACKING` | **3 files** | `sdlc-1111.md`, `session-type-pm-rename.md`, `keyfield-migration-fix.md` |
| `.stem` sweep | **2 offending of 6 total** | `tools/sdlc_next_skill.py:205,357`; the other 4 are unrelated `.stem` uses a hand review must pass |
| `AC-DOCSWEEP` | **8 files** | positive criterion |

`AC-TESTSCOPE` is a positive criterion, red on `main` for a different reason
(`tests/unit/test_lane_identity.py` does not exist yet). Every count re-verified against
`main` at cycle-2 revision time — quote the counts, not just "red", so a later drift is
visible.

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The mint is placed where the dominant lane-start path never goes. `ensure_session` has SIX success return points, verified in `tools/sdlc_session_ensure.py`: `:674` (env session already OWNS the issue — the "legitimate bridge case, true no-op"), `:729` (adopt ownerless env session), `:750` (adopt owned-run session), `:773` (reuse existing session found by issue), `:789` (reuse by local session id), `:868` (create). Task 3 says to write the slug "at the create and adopt return points", covering only `:729`, `:750`, `:868`. The three reuse/no-op paths — including `:674`, which short-circuits on the FIRST branch of the function for any bridge- or worker-started eng session — never reach the mint. A lane started conversationally keeps an empty slug forever and every consumer no-ops. This is cycle-2 blocker 1 recurring in a narrower shape: the write target now exists, but the write is placed where the dominant path never goes. | **addressed** (cycle 3, Task 3 + Risk 4) | Make the mint one unconditional call near the top of `ensure_session`, not a per-return-point write. Place it immediately after the existing validity guard (`if not issue_number or issue_number < 1: return {}`, `:639`) and before the `VALOR_SESSION_ID` env short-circuit: `try: resolve_lane_slug(issue_number, allow_heal=True)` / `except Exception: logger.debug(...)`. The call is idempotent and conditional-on-empty, so one early invocation covers every exit path by construction and cannot double-write. It MUST swallow: `ensure_session`'s contract is to return a session dict, and a Redis/git failure inside identity resolution must not convert a successful ensure into `return {}`. Do NOT satisfy this by moving the write into `_acquire_run_lock_and_bind` (the one helper all six paths share) — Risk 4 correctly forbids that line range because #2660 is editing `:486-493` concurrently. |
| CONCERN | Risk & Robustness | `tools/valor_session.py::cmd_create` is on the sanctioned `allow_heal=True` list but is not gated on lane intent. Verified at `tools/valor_session.py:588`: the auto-derive branch fires for ANY non-teammate (i.e. `eng`) session whose `--message` merely matches `_ISSUE_REF_RE`. Today the consequence is local (a slug and a worktree). After this plan the same casual mention ("what's going on with issue #2663?") calls `resolve_lane_slug(N, allow_heal=True)`, whose heal path runs `PipelineLedger.get_or_create` and mints `sdlc-N` — creating a durable Redis ledger and a permanent recorded identity for an issue that is not a lane. That contradicts the plan's own Flow example ("Non-lane issue → no ledger created, nothing minted") and the Success Criterion "Read paths never mint", and because the write is no-overwrite a conversational session can permanently fix a wrong identity before the real lane starts. | **addressed** (cycle 3, Task 3 `cmd_create` seam + AC-HEAL) | Preferred fix: drop `cmd_create` from the sanctioned list and have it call `ensure_session(issue_number)` — the plan's designated single minter — instead of the resolver directly. That keeps "minted exactly once by `ensure_session`" literally true and shrinks the `allow_heal=True` grep to three sites. If `cmd_create` must heal directly, gate it on an explicit lane opt-in rather than on an issue reference appearing in prose (`_derive_sdlc_metadata` is NOT a usable discriminator — it fires on the same `_ISSUE_REF_RE` signal). Either way, `AC-HEAL`'s expected four-caller list must be updated in the same edit or the anti-criterion fails on a correct build. |
| CONCERN | Risk & Robustness | Task 5 commits the `tracking:` backfills "directly on main, never on the feature branch", but its exit condition (`AC-TRACKING` exits 0 and `tests/unit/test_plan_docs.py` passes) is checked by Tasks 7 and 9, which run in the lane worktree on `session/sdlc-2735`. `AC-TRACKING` globs `docs/plans/*.md` in the working tree, so main-side backfills are invisible to the validator. Worse, `tests/unit/test_plan_docs.py` is a NEW file created on the branch that asserts over plan docs backfilled on main, so the branch's own suite run fails. No task states the sync step. | **addressed** (cycle 3, Task 5 split-commit sequence + Task 7 precondition) | Add to Task 5: commit the three `tracking:` backfills on `main`, push, then `git -C <worktree> merge --ff-only origin/main` (or rebase the lane branch) BEFORE Task 7 runs, and state that ordering in Task 7's preamble. Alternative (larger): have `tests/unit/test_plan_docs.py` read plan docs from the repo's `main` ref rather than the working tree. The merge step is the smaller change and matches how every other main-committed plan edit reaches a lane branch here. |
| CONCERN | Scope & Value | `_NON_LANE_PLANS` is defined twice — as a frozenset in `tests/unit/test_plan_docs.py` (Task 5's "durable owner") and again as an inline `NON_LANE = {...}` literal in the `AC-TRACKING` bash block, which even comments "the same frozenset `tests/unit/test_plan_docs.py` owns" while restating it. That is the repo's replicated-value defect, and it is this plan's own diagnosed root-cause pattern (five places inventing the same value) reproduced inside the fix's verification. The next excluded plan doc will be added to one copy and not the other, and the Verification row will then disagree with the test it exists to prove. | **addressed** (cycle 3, `tools/plan_doc_scope.py` single home) | Give the set exactly one home and have the anti-criterion import it: define `NON_LANE_PLANS: frozenset[str]` at module scope in `tests/unit/test_plan_docs.py` and rewrite `AC-TRACKING` as `.venv/bin/python -c "from tests.unit.test_plan_docs import NON_LANE_PLANS; ..."`. If importing from `tests/` off-suite is undesirable, put the frozenset in a small non-test module (e.g. `tools/plan_doc_scope.py`) that both the test and the AC import. Do not leave two literals. The regex stays the tight `^tracking:.*(?:#\d+\|issues/\d+)`; only the exclusion set is deduplicated. |
| CONCERN | History & Consistency | The "no stale import path" Verification row is not runnable as written and therefore passes vacuously — the exact failure class the plan devotes a paragraph to (the `AC-LIVENESS` escaped-pipe precedent). Under zsh, the unquoted `--include=*.py` is glob-expanded, matches no file, and zsh ABORTS the pipeline with `no matches found: --include=*.py`; `wc -l` then prints `0`, so the row reports PASS on unmodified `main`. Verified both ways at critique time: unquoted → aborts, reports 0; quoted → **28**. The plan's claim "Verified red at revision time … the stale-import row (28)" is only reachable with the quoted form, i.e. the recorded number came from a different command than the one the builder will run. | **addressed** (cycle 3, both globs quoted; count re-verified at 29) | Change the cell to `grep -rn 'from tools._sdlc_utils import find_plan_path' --include='*.py' --include='*.md' . \| grep -v '^./.worktrees/' \| wc -l` (single quotes around BOTH glob values). Note the row still under-counts the real blast radius: the symbol is also imported as `from tools._sdlc_utils import find_plan_path as _find_plan_path` (`tools/sdlc_stage_query.py:51`, `tools/sdlc_verdict.py:104`), which this exact-string row happens to match, but a `from tools import _sdlc_utils` + attribute form would not. Pair the row with `AC-TESTSCOPE` rather than treating it as exhaustive. |
| CONCERN | History & Consistency | `find_plan_path`'s replacement rung 2 (`docs/plans/{resolve_lane_slug(N)}.md`) is near-dead code added in the same PR that deletes near-dead code for the same reason. It runs with `allow_heal=False`, so it needs an already-recorded slug AND needs the plan filename to equal the LANE slug — which "Two slugs, kept distinct" explicitly refuses to force. This lane is the demonstration: recorded slug `sdlc-2735`, plan doc `sdlc-lane-recorded-slug.md`, rung 2 misses. After the fix `find_plan_path` is effectively a one-rung `tracking:` lookup, making `tracking:` a hard single point of failure with no authoring-time enforcement. | **addressed** (cycle 3, rung 2 DROPPED; Risk 2 rewritten to name the test as sole guard) | If rung 2 is kept, add a Test Impact case that actually exercises it — a ledger whose recorded slug equals an existing `docs/plans/{slug}.md` stem (the `sdlc-1111.md` shape, which is real in this repo) — so it is not merged untested; no such case is currently listed. If rung 2 is dropped, `tests/unit/test_plan_docs.py` becomes the sole guard and Risk 2 must say so, replacing the sentence "The new `docs/plans/{recorded_slug}.md` rung also covers slug-named plans without frontmatter", which is currently the only stated mitigation for a `tracking:`-less plan and is unreachable for any plan whose filename is not the lane slug. |
| CONCERN | Scope & Value | Task 4 (`build-consumers`) and Task 5 (`build-backfill`) are both marked `Parallel: true` and both `Assigned To: consumer-builder`. One agent cannot execute two tasks in parallel, so a scheduler honoring the flag either serializes them silently or dispatches the same agent twice. Task 5 additionally commits on `main` while Task 4 works on the lane branch, so co-assigning them forces a mid-task checkout switch in a worktree on a machine running three other SDLC lanes. | **addressed** (cycle 3, Task 5 reassigned to `identity-tester`) | Reassign Task 5 (three one-line frontmatter edits plus one new test file, `Depends On: none`) to `identity-tester` (already owns new test files) or `identity-documentarian` (already owns main-committed doc edits), keeping `Parallel: true` meaningful. Do NOT resolve this by serializing Task 5 behind Task 4 — Task 7 depends on `build-backfill`, so serializing lengthens the critical path for no benefit. |
| NIT | History & Consistency | The `tests/unit/test_sdlc_stage_marker.py` patch-site inventory is stale in both directions. Verified at critique time there are SEVEN `patch("tools._sdlc_utils.find_plan_path", …)` sites — `:1375, :1386, :1394, :1407, :1419, :1428, :1444`. The plan names five and misses `:1428` and `:1444`. Harmless in practice (`AC-TESTSCOPE` is sweep-driven and the file carries the swept token), but the count is quoted as verified evidence in the Critique Results table. | **addressed** (cycle 3, both enumerations replaced with a sweep instruction) | N/A (NIT) — if corrected, replace the enumerated line numbers with "seven patch sites (re-derive by sweep)", consistent with the plan's own rule that a moved-symbol cleanup closes out on a grep, not a list. |
| NIT | Scope & Value | 1643 lines, nine tasks, five named agents to add one nullable Popoto field, one module, and route five call sites. The prior round raised this and the plan's reply ("the blockers required more specificity") was honest, but the trend is one-directional across three cycles, and this round's blocker again lives in a dense passage (Task 3's return-point instruction) that reads as settled because it is detailed, not because it was checked against the function it describes. | **partially addressed** (cycle 3) | N/A (NIT) — if trimmed, cut the `Decisions` `<details>` block holding the superseded original wording of the three open questions, and the cycle-1/cycle-2 narration in Technical Approach ("An earlier draft applied…", "The reason the default inverts…"). Those are revision history, not build instructions, and the repo's no-historical-artifacts rule already argues for their removal. |
---

### Cycle 4 (post-revision re-critique)

| Severity | Finding | Addressed By | Note |
|----------|---------|--------------|------|
| BLOCKER | `_has_pr_on_branch` skipping the `--head` leg on an unresolved slug disables upvote **gate 4** for its entire target population: the call at `:515` runs only for candidates gate 2 (`:508`) already proved have no recorded stage, hence no recorded slug. Duplicate lane pickup, and the "merged PR but still open" finding disappears. | **addressed** (Task 4) | Site falls back to `mint_lane_slug` instead of skipping. Established the general rule: a site that SEARCHES may guess, a site that WRITES identity may not. |
| BLOCKER | "Extend `sdlc_stage_marker`'s lookup-failure refusal to the resolvable-but-absent case" is unbuildable: precondition 1 (`:349-362`) already refuses whenever `plan_path is not None`, so `plan_path is None` is the ONLY branch permitting a skip. Refusing there deletes stage skipping entirely. | **addressed** (Task 4) | Change dropped. The file gets its import retargeted and nothing else; skip-gating is a separate concern with no standing in an identity lane. |
| BLOCKER | The upvote mint at `sdlc_upvote_lanes.py:489` sits ABOVE gates 1/1.5/1.6/2/3/4, so healing in place would record a permanent identity for every scanned candidate the reflection then declines. | **addressed** (Task 3) | Probe-only local (heal off) stays at `:489`; the healing call moves past the Race-1 re-check to just before `create_session` at `:577`. |
| CONCERN | Read path used `_resolve_target_repo()` (env ladder, shells out to `gh`) while every other reader uses `resolve_target_repo_for_read` (lease-first) — a different `ledger_key`, so the recorded slug could read back as `None` and the feature would silently no-op. Also falsified the no-subprocess criterion. | **addressed** (Technical Approach, Success Criteria) | Read path uses `resolve_target_repo_for_read` + `PipelineLedger.get`; heal path keeps `_resolve_target_repo`. Optional `target_repo=` passthrough. New success criterion: a test writes via heal and reads back via the read path. |
| CONCERN | `AC-MINT`'s ERE matches PROSE, so the replacement comments and docstrings this plan *requires* would fail it on a correct build. | **addressed** (AC-MINT) | Prose must spell it `sdlc-<issue>`; widening the filter is explicitly banned. |
| CONCERN | The 14 `_git_toplevel` monkeypatch sites are inert if `lane_identity` binds the name at import. | **addressed** (Task 2, Test Impact) | Module-attribute access mandated. |
| CONCERN | `cmd_create` adopting a recorded human-named slug would drop a conversational session into a live lane's worktree (#1915 Defect 2). | **addressed** (Task 3) | `cmd_create` calls `mint_lane_slug` unconditionally; no resolver call at all. |
| CONCERN | `_check_plan_committed_on_main` (`sdlc_next_skill.py:166-174`) takes a slug and builds `git show main:docs/plans/{slug}.md`; the PLAN/PATCH split hands it an absolute path. | **addressed** (Task 4) | Signature takes a repo-relative path; relativization + `ValueError` guard specified. |
| CONCERN | One-rung resolution plus the new fail-closed CRITIQUE raise turns a just-authored plan doc without `tracking:` into a hard lane block; Task 5's test only audits committed docs. | **addressed** (Risk 2, Task 8) | `/do-plan`'s frontmatter template gains `tracking:` in this PR. |
| CONCERN | Three narrative sites still described `valor-session create` as an `ensure_session` lane-start path, and Task 3 still said "four callers". | **addressed** | Struck; three everywhere. |
| CONCERN | The `session/`-prefix anti-criterion (`ls-remote.*session/`) missed `branch_exists` (`sdlc_next_skill.py:367`, a `git branch -a` probe) and `_lookup_pr` (`sdlc_stage_query.py:369`) — both kept and made newly functional. | **addressed** | Row broadened; both sites routed through `lane_branch_name`. |
| NIT | `AC-BACKFILL` is zero-expecting and already green on `main`; `grep -c` also exits 1 on zero. | **addressed** | Added to the AC-LIVENESS exception list; exit-code inversion noted for Task 7. |
| NIT | Rung 2's `resolve_pr_head_sha` call passed no `repo`/`repo_root`, resolving against process cwd for a cross-repo lane. | **addressed** | Both threaded. |
| NIT | Line-ref drift (`migrations.py:999`→`:1033`; 29 plans→33) and `test_sdlc_utils.py` suppression cases marked UPDATE when they need DELETE. | **addressed** | Refreshed; DELETE stated. |
| NIT | `build-backfill` marked `Parallel: true` under `identity-tester`, which also owns `test-red`. | **addressed** | Set to `Parallel: false`; `Depends On: none` retained. |
| NIT | Rung 1 pseudocode was a `NameError` (`target_repo` assigned only in the `if` arm). | **addressed** | Resolved by the two-resolver rewrite. |

### Cycle 5 (focused re-critique of the cycle-4 fixes)

| Severity | Finding | Addressed By |
|----------|---------|--------------|
| BLOCKER | The `cmd_create` snippet in Technical Approach still read `resolve_lane_slug(issue_number, allow_heal=True)`, contradicting Task 3, the three-caller table and `AC-HEAL`. Cycle 4 claimed "struck; three everywhere"; it was not. | **addressed** — snippet now `mint_lane_slug`, with the reason inline |
| BLOCKER | Both reflection callers would build the wrong ledger key for non-`ai` projects: they iterate projects and pass the project `cwd` only to *subprocesses*, so an unpassed `target_repo` resolves through the reflections process's own git root (`tomcounsell/ai`). Gate 3 has already excluded live leases, so the lease peek cannot rescue it. | **addressed** — `target_repo=repo` is mandatory at all three reflection sites; new success criterion with a non-`ai` project test |
| BLOCKER | The cycle-4-broadened `session/`-prefix row was vacuous by the plan's own rule: it lived in a table cell using `\|`, which inside an ERE is a *literal pipe*, so it searched for one impossible string. | **addressed** — moved to the fenced block as `AC-PREFIX`, converted to a hand-review row (21 hits on `main`, not 2) |
| HIGH | The cycle-4 drop of the `sdlc_stage_marker` change was not propagated: Success Criteria still required "a stage skip with an unresolvable plan is refused" and Test Impact still demanded fail-closed cases for that file. Task 7 validates every AC, so a correct build would fail validation. | **addressed** — both struck |
| HIGH | The `target_repo=` passthrough was incoherent: it named `_compute_meta` as the caller, but `tools/sdlc_stage_query.py:468` is `_resolve_target_repo()` — the env ladder — so passing it through would hand the read path the resolver the fix banned. | **addressed** — `_compute_meta` does not pass through; resolves lease-first, memoized |
| HIGH | No `None`-repo guard on either arm; `_build_key` explicitly pushes that check to callers, and the heal arm would *create* a phantom `None:{issue}` record. | **addressed** — `if not target_repo: return None` above the branch, plus a success criterion |
| MEDIUM | The upvote probe slug and the healed slug could diverge, so gates 1/1.6 and the Race-1 re-check would test one name while the session is created under another — losing duplicate-lane protection for a candidate with a recorded human-named slug but no recorded stages. | **addressed** — resolve once at `:489`; the heal call does not reassign `slug` |
| MEDIUM | Two more Verification rows were unrunnable (`\|` as a shell pipe inside table cells). | **addressed** — moved to the fenced block as `AC-STALEIMPORT` / `AC-FALLBACK` |
| MEDIUM | The cycle-4 key-agreement criterion was vacuous: it passes trivially whenever the env ladder and the lease agree, which is the default in any test that sets `SDLC_TARGET_REPO`. | **addressed** — divergence removed structurally (one resolver); the test must now pin a lease whose `target_repo` differs from the env answer |
| LOW | `sdlc_progress.py:707` substituted in place would desync from the surrounding `slug`, which is charged against `_bump_attempts` and the escalation/cooldown keys. | **addressed** — assign to `slug`, not just the argument |
| LOW | Healing at `:577` precedes `create_session`, which can fail, recording an identity for a lane that never started. | **addressed** — recorded as an accepted, reasoned exception rather than hidden |
| NIT | Citation drift (`pipeline_ledger.py:211` is docstring prose, not the SETNX call) and an unspecified `Path()` cast in the `_check_plan_committed_on_main` relativization. | **addressed** |

**Root cause of the cycle-4 → cycle-5 churn, recorded so it is not repeated:** cycles 3
and 4 both tried to specify *which* repo resolver each arm uses, and each attempt fixed one
failure while opening another. The resolution was not a better split but the removal of the
split — `resolve_target_repo_for_read` is a strict superset of the writer ladder
(lease-first, env-fallback), so using it everywhere makes the divergence class unreachable
instead of something a test has to chase. When two mechanisms must be kept in sync, prefer
deleting one.

## Decisions (formerly Open Questions)

All three open questions were ruled on in the war-room critique pass. Recorded here as
settled decisions; the plan body above already reflects them. No open questions remain.

1. **Slug stamping onto `AgentSession` — DECIDED: stamp it.** Unchallenged in critique, so
   the plan's declared default stands. `PipelineLedger.slug` is authoritative;
   `AgentSession.slug` is a documented convenience mirror for the executor's existing
   readers (`session_executor.py` task-list id, calendar slug, `sdk_client.py`'s
   `SDLC_SLUG` env). The mirror is written at lane start by Task 3 and never read back as
   authority. The feature doc must say so explicitly, so a future reader does not treat
   the mirror as a second source of truth.

2. **`session-recovery-observation-audit.md` — DECIDED (revised cycle 2): exclude by
   name, do not move.** Escalated to a blocker in cycle 1 because a lane cannot ship
   against a Verification row whose pass condition is a pending human decision. Cycle 1
   resolved it by moving the file out of `docs/plans/`; cycle 2 **reverses that** — a file
   move plus inbound-link fixes is repo-wide docs governance riding inside a one-field bug
   fix, and it does not make the invariant any more enforceable than an explicit exclusion
   does. The file (and `resilience-simplification-three-tier.md`'s `tracking: none yet`
   placeholder) now sit in a documented `NON_LANE_PLANS` frozenset, whose single home is
   `tools/plan_doc_scope.py` (cycle 3; both the test and `AC-TRACKING` import it). The row's regex stays
   tight — requiring a real `#N` or `issues/N` token, since the old `^tracking:\s*\S`
   accepted the placeholder — and the invariant gains a durable owner in
   `tests/unit/test_plan_docs.py`, a test rather than a hook validator (a validator is a
   new control surface and belongs in its own lane). Nothing about the pending human
   decision blocks this lane either way.

3. **Reflections' lane-*discovery* guess — DECIDED: split.** The per-issue site
   (`reflections/sdlc_upvote_lanes.py::_has_pr_on_branch`) is a one-line substitution and
   is **pulled into increment 1** (Task 4). The corpus-filter site
   (`reflections/sdlc_progress.py`'s `_SDLC_BRANCH_RE` / `_list_open_sdlc_prs` /
   `_issue_number_from_slug`) is a restructuring, not a substitution, and is deferred to
   **#2755, filed at plan-revision time** and referenced in No-Gos. Neither half is left
   as a bare promise.

