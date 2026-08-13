---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2735
last_comment_id:
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

A scan of `docs/plans/` (29 plans, 24 with `tracking:` frontmatter) finds **309 issue
numbers with no owning plan that nonetheless resolve to one.**

The failure shape is a confident wrong answer derived from a signal that looks
authoritative and is not.

**Desired outcome:**

The lane's slug is minted exactly once, by the component that creates the Redis object
owning the SDLC stages, and recorded beside those stages. Every consumer reads it.
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
- **Impact on plan**: The fallback removal is safe *only because* the resolver replaces
  it — `find_plan_path` gains a `docs/plans/{recorded_slug}.md` rung, so a lane with a
  recorded slug still resolves its plan. It also makes the `tracking:` backfill task
  non-optional, and it drives the split of the PLAN artifact check (which needs the
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
2. It calls `resolve_lane_slug(N)`, which walks the adoption ladder and, on a miss,
   mints `sdlc-{N}`. The result is written to `PipelineLedger.slug`
   **conditional-on-empty**, beside `stage_states_json` and `pr_number`.
3. **Every consumer** calls `resolve_lane_slug(N)` — with the default
   `allow_heal=False` — and gets that recorded value, or `None`. Consumers never mint.
   Only the four lane-start callers listed in Technical Approach pass `allow_heal=True`,
   and their write is conditional-on-empty: it can never overwrite and never needs the
   lease.
4. `find_plan_path(N)` resolves in two rungs: `tracking:` frontmatter (authoritative),
   then `docs/plans/{recorded_slug}.md`. No textual fallback.
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
  `tools/lane_identity.py` therefore imports exactly one helper from `_sdlc_utils`:
  `_git_toplevel`. That helper **stays where it is** — `_resolve_target_repo`
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
  `resolve_lane_slug(issue_number, *, allow_heal=False)`, `lane_branch_name(slug)`, and
  the relocated `find_plan_path(issue_number)`. Its module docstring **opens** with the
  two-slug distinction (lane slug vs. plan-doc slug, bridged by `tracking:`), so a reader
  who arrives expecting one concept is corrected in the first sentence rather than
  invited to re-unify them.
- **Adoption ladder** — how `resolve_lane_slug` answers when the field is empty **and the
  caller is a lane-start path that opted into `allow_heal=True`**: adopt an identity that
  already exists in the world before inventing one. Read paths never walk the ladder.
- **Conditional-on-empty write** — the healing write re-reads immediately before
  writing, writes only if still empty, uses `save(update_fields=["slug"])`, and takes
  no lease.
- **`ensure_session` mints** — the one component that creates the Redis object owning
  the stages calls the resolver at lane start, on both paths, before any plan exists.
- **No-op on unresolvable** — G8's PATCH check and `branch_exists` skip entirely rather
  than probe a guessed name.

### Flow

**Lane start** → `ensure_session(N)` calls `resolve_lane_slug(N, allow_heal=True)` →
ladder finds nothing → mints `sdlc-{N}` → writes `PipelineLedger.slug` → **lane has an
identity**

**Every later tick** → any consumer calls `resolve_lane_slug(N)` (heal off) → reads the
recorded value → **same answer everywhere**

**Non-lane issue** (`stage-query --issue-number 2718`) → `resolve_lane_slug(2718)` → no
ledger, or ledger with empty slug, heal off → **`None`, nothing minted, nothing written,
no git call**

**Migration lane (slug empty, branch already pushed)** → the next **lane-start** call for
that issue (`ensure_session`, a reflection pickup, or `valor-session create`) walks the
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

1. `PipelineLedger.load(...)` for the issue's key → `.slug` if non-empty → return it.
   **Never re-derive over a recorded value.** This rung is why the function is safe to
   call from anywhere. `load()` is a direct-key `HGETALL` and **never creates a ledger**;
   when no ledger exists the function returns `None` immediately, so a read path can
   never bring a `PipelineLedger` into existence for a non-lane issue.
2. `ledger.pr_number` → `resolve_pr_head_sha(pr, cross_check=False)` (git-first) → match
   that SHA against one `git ls-remote --heads origin` listing → recover the exact
   `session/<name>` ref → slug is that name minus the `session/` prefix. Shape-agnostic;
   this is the rung that recovers `dev-<hash>` lanes. **The match must be unique**
   (see "Rung 2 uniqueness" below).
3. `git ls-remote --heads origin refs/heads/session/sdlc-{N}` exact probe → adopt
   `sdlc-{N}`.
4. `docs/plans/` scan for a `tracking:` frontmatter match on issue N → adopt that plan's
   filename stem.
5. Mint `sdlc-{N}`.

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
| `tools/valor_session.py::cmd_create` (issue-scoped) | Lane start on the CLI path (see Blocker-4 seam below). |

Every other caller — `_compute_meta`, `find_plan_path` rung 2, G8, `branch_exists`, the
reflections' *discovery* probes — takes the default `False`. A consumer that finds no
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
race (`agent/pipeline_ledger.py:211`): take a SETNX on a dedicated non-Popoto key, then
`load()` the ledger fresh (direct-key `HGETALL`, index-independent — the #1720 hazard
does not apply), and write only if `slug` is still empty. `save(update_fields=["slug"])`
so `stage_states_json` is never touched. A lost race is not an error: the loser re-reads
and returns the winner's value. **No lease is taken** — this is an identity write, not a
stage transition, and gating it on the lease would reintroduce the deadlock class #2026
closed.

**`find_plan_path` becomes resolution, not derivation.** Moved into
`tools/lane_identity.py` (so it can call `resolve_lane_slug` without a cycle) and reduced
to two rungs:

1. A `tracking:` frontmatter line naming issue N → authoritative, return immediately.
2. `docs/plans/{resolve_lane_slug(N)}.md` if that file exists → return it. This rung
   takes the **default `allow_heal=False`** — `find_plan_path` is a read path and must
   never mint. When no slug is recorded, rung 2 is simply skipped.
3. Otherwise `None`.

The bare-`#N` fallback and the entire `_is_ai_repo_fallback` mechanism are **deleted** —
not narrowed, not made section-aware. A heuristic that parses prose to decide which
"#N" counts is the same class of confident-wrong-answer the issue is about. The
plans-dir resolution ladder (env var → git toplevel → `__file__`) is retained verbatim;
only the ownership test changes.

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

**The third minter is removed too.** `tools/valor_session.py:107`
(`_derive_slug_from_message`) is the surviving `sdlc-{N}` minter the Problem table names.
Leaving it would reproduce the exact #1915 failure this plan diagnoses — "a comment
cannot enforce a convention against live minters" — at one-third scale, and because the
ledger write is conditional-on-empty, whichever writer ran first would win
non-deterministically. `_derive_slug_from_message` is a pure string function with no
issue-lifecycle context, so it cannot call the resolver in place. **The seam is its
caller**, `cmd_create`: resolve the issue number from the message first, then

```
slug = resolve_lane_slug(issue_number, allow_heal=True) if issue_number else None
slug = slug or _derive_slug_from_message(message)
```

The `issue_number is None` branch preserves the documented "generic message → `None`
slug" behavior verbatim; `_derive_slug_from_message` survives only as the no-issue /
resolver-unavailable fallback, and its docstring says so.

**The reflections stop guessing when *finding* a lane, not just when naming one.**
`reflections/sdlc_upvote_lanes.py::_has_pr_on_branch` (`:328-344`) probes
`gh pr list --head session/sdlc-{issue_number}`. After this plan a lane whose recorded
slug is human-named would not be found by that probe — the #2718 wedge shape from the
opposite direction, and now *silent*, because the recorded slug exists and looks
authoritative. The site already receives `issue_number`, so it becomes:

```
head = lane_branch_name(resolve_lane_slug(issue_number))   # allow_heal=False
if head is None:
    ...skip the --head leg entirely...
```

The `None` case **skips the leg**; it never emits the literal `--head session/None`,
which Empty/Invalid Input Handling already bans. `reflections/sdlc_progress.py`'s
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
placeholder `tracking: none yet` is exactly the unresolved case Task 5 names and the row
must **fail** on it, not wave it through. `session-recovery-observation-audit.md` is an
audit document, not a lane plan; Task 5 moves it out of `docs/plans/` so the invariant is
true by construction rather than by exception (see Task 5). The new
`docs/plans/{recorded_slug}.md` rung also covers slug-named plans without frontmatter.

### Risk 3: Adopting the wrong branch for a lane whose `sdlc-{N}` branch is stale

**Impact:** Ladder rung 3 finds an old `session/sdlc-{N}` from a prior, abandoned attempt
and adopts it, binding the lane to a dead branch.
**Mitigation:** Rung 2 (PR head → exact ref name) sits **above** rung 3 precisely for
this: whenever a PR exists, its head ref is authoritative and shape-agnostic. Rung 3 only
fires when no PR is recorded, i.e. pre-build, when a pushed `session/sdlc-{N}` is by
construction *this* lane's. The write is also conditional-on-empty and never overwrites,
so once `ensure_session` has minted at lane start, rung 3 is unreachable for that lane.

### Risk 4: File collision with the in-flight #2660 lane on `tools/sdlc_session_ensure.py`

**Impact:** Merge conflict, or one lane's edit silently reverting the other's.
**Mitigation:** #2660 Task 3 edits `_acquire_run_lock_and_bind` at `:486-493`. This plan
places its ledger write in `ensure_session`'s own body, at the create and adopt return
points, deliberately not inside `_acquire_run_lock_and_bind`. The two edits touch
disjoint line ranges in the same file, which git resolves cleanly. Noted here so a
reviewer does not "helpfully" consolidate them.

### Risk 5: `git ls-remote --heads origin` (full listing) is slow enough to matter in the router's poll loop

**Impact:** The router polls `next-skill` constantly; a full remote-ref listing on every
tick would add hundreds of milliseconds each time.
**Mitigation:** Rung 1 (recorded value) short-circuits every steady-state call with a
single direct-key `HGETALL` — no git at all. The full listing only runs in the ladder,
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
serializes its own create race via SETNX at `agent/pipeline_ledger.py:211`).
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
- The `agent/sdk_client.py:474` dead-`or` copy-paste bug. Genuinely trivial, but it is an
  `AgentSession.slug` executor read, not SDLC lane identity, and fixing it here would
  widen the PR's blast radius into the session executor for no gain. **Filed as part of
  this stage's output** — if no issue exists at build time, file one rather than leaving
  a bare promise.

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
- [ ] `docs/sdlc/do-plan-critique.md:156` — the bash snippet imports
  `from tools._sdlc_utils import find_plan_path`. Update to `tools.lane_identity`. This
  snippet is load-bearing (it is the shared-resolver contract with `_cli_record`), so a
  stale import here silently breaks the critique write path.
- [ ] `.claude/skills-global/do-build/SKILL.md:110` — "derive `{slug}` from the plan
  filename". Update to read the recorded lane slug.
- [ ] `.claude/skills-global/do-pr-review/SKILL.md:83`, `docs/sdlc/do-merge.md:39,137`,
  `docs/sdlc/do-build.md`, `docs/sdlc/do-patch.md` — sweep for slug-derivation
  instructions and route them to the recorded slug.

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
- [ ] Clean grep sweep: no `Path(plan_path).stem`, no hardcoded `session/{slug}` inside a
  probe, no `find_plan_path` import from `tools._sdlc_utils`, no "never creates one"
  belief comment.
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
  relocated `find_plan_path` (two rungs; `_is_ai_repo_fallback` and the bare-`#N`
  fallback deleted entirely). Preserve the plans-dir resolution ladder verbatim.
- Implement the conditional-on-empty healing write, modeled on the SETNX pattern at
  `agent/pipeline_ledger.py:211`. Use `save(update_fields=["slug"])`. Take no lease.
  Treat `""` and whitespace-only as empty.
- Add `_migrate_confirm_pipeline_ledger_slug_readable` to `scripts/update/migrations.py`
  mirroring `_migrate_confirm_is_ledger_field_readable` (`:335`) and register it in
  `MIGRATIONS` (`:999`). Read-only probe; no backfill.

### 3. Mint at lane start

- **Task ID**: `build-mint`
- **Depends On**: `build-core`
- **Validates**: `tests/unit/test_sdlc_session_ensure.py`
- **Assigned To**: `identity-builder`
- **Agent Type**: builder
- **Parallel**: false
- In `tools/sdlc_session_ensure.py::ensure_session`, call `resolve_lane_slug(issue_number)`
  and persist the result, at the create and adopt return points in `ensure_session`'s own
  body. **Do not** put this inside `_acquire_run_lock_and_bind` — #2660 is editing
  `:486-493` concurrently (see Risk 4).
- Stamp the resolved slug onto the created/adopted `AgentSession.slug` so the executor's
  existing slug readers see the same identity. The ledger remains authoritative.
- Remove the mint from `reflections/sdlc_upvote_lanes.py:489` and
  `reflections/sdlc_progress.py:707`; both call the resolver instead.

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
  with `resolve_lane_slug(issue_number)`; surface the recorded slug in the `_meta` payload.
- Update all `find_plan_path` importers to `tools.lane_identity` and **delete** the
  symbol from `tools/_sdlc_utils.py` — no re-export shim.
- Delete the false-belief comments at `tools/sdlc_next_skill.py:346-349` and
  `tools/sdlc_stage_query.py:347-350`; replace with the true statement.
- Close out on a **grep sweep**, not this list.

### 5. Backfill `tracking:` frontmatter

- **Task ID**: `build-backfill`
- **Depends On**: none
- **Validates**: the "every plan resolvable" Verification row
- **Assigned To**: `consumer-builder`
- **Agent Type**: builder
- **Parallel**: true
- For each plan in `docs/plans/` with no `tracking:` line, add one where a real tracking
  issue exists. The five known cases: `keyfield-migration-fix.md` (popoto repo — record
  the cross-repo URL explicitly), `sdlc-1111.md` (#1111, closed), `session-type-pm-rename.md`
  (#648, closed), `session-recovery-observation-audit.md` (an audit doc, not a lane —
  move to `docs/plans/completed/` or mark explicitly non-tracking),
  `resilience-simplification-three-tier.md` (`tracking: none yet` placeholder — resolve
  or mark non-tracking). Re-derive the list by sweep; do not trust this enumeration.
- Plan-doc edits commit **directly on main**, per the repo convention — never on the
  feature branch.

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
- Verify each AC from #2735 and #2718 individually against the built code.
- Run every Verification-table command and report actual output.
- Confirm the demonstrated-red output from task 1 is captured for the PR body.
- Confirm no live lane's worktree, branch, or ledger was touched (three other SDLC lanes
  run concurrently on this machine).

### 8. Documentation

- **Task ID**: `document-feature`
- **Depends On**: `validate-all-acs`
- **Assigned To**: `identity-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the whole `## Documentation` section, including the instruction-doc sweep.
- Pay particular attention to `docs/sdlc/do-plan-critique.md:156` — a stale import there
  silently breaks the critique write path.

### 9. Final validation

- **Task ID**: `validate-final`
- **Depends On**: `document-feature`
- **Assigned To**: `identity-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run all Verification commands.
- Verify every Success Criterion including the documentation ones.
- Confirm the PR body carries both `Closes #2735` and `Closes #2718`.

## Verification

| Check | Command | Expected |
|---|---|---|
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_lane_identity.py tests/unit/test_sdlc_next_skill.py tests/unit/test_sdlc_stage_query.py tests/unit/test_sdlc_session_ensure.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| #2735 AC 1+2 | `SDLC_TARGET_REPO=$PWD .venv/bin/python -c "from tools.lane_identity import find_plan_path; assert find_plan_path(2663) is None, find_plan_path(2663); assert find_plan_path(2716) is not None"` | exit code 0 |
| Ledger carries the field | `.venv/bin/python -c "from agent.pipeline_ledger import PipelineLedger; assert 'slug' in PipelineLedger._meta.fields"` | exit code 0 |
| Migration registered | `grep -c 'confirm_pipeline_ledger_slug_readable' scripts/update/migrations.py` | output > 1 |
| Anti-criterion: no slug derived from a plan filename | `grep -rnE '\.stem\b' tools/ agent/ reflections/` | every hit reviewed by hand; **zero** hits where the `.stem` of a plan path becomes a slug or a branch name. Broadened from the old literal `Path(plan_path).stem`, which a rename to `plan_path.stem` defeated. |
| Anti-criterion: no stale import path | `grep -rn 'from tools._sdlc_utils import find_plan_path' --include=*.py --include=*.md . \| grep -v '^./.worktrees/' \| wc -l` | match count == 0 |
| Anti-criterion: no stale prose reference to the moved symbol | see `AC-PROSE` below | match count == 0 |
| Anti-criterion: docs sweep complete | see `AC-DOCSWEEP` below | every listed file names `tools/lane_identity`, none names `tools/_sdlc_utils` |
| Anti-criterion: false belief deleted | see `AC-BELIEF` below | match count == 0 |
| Anti-criterion: fallback mechanism deleted | `grep -rn '_is_ai_repo_fallback' tools/ tests/ \| wc -l` | match count == 0 |
| Anti-criterion: no hardcoded prefix in probes | `grep -rnE 'ls-remote.*session/' tools/ reflections/ \| wc -l` | match count == 0 outside `tools/lane_identity.py` — the `session/` prefix exists in exactly one place, `lane_branch_name` |
| Anti-criterion: no holder/pid/liveness field added | see `AC-LIVENESS` below | match count == 0 |
| Anti-criterion: no eager slug backfill in migration | see `AC-BACKFILL` below | match count == 0 |
| Anti-criterion: no read path heals | see `AC-HEAL` below | hits are **exactly** the four sanctioned lane-start callers; zero hits in `tools/sdlc_stage_query.py`, `tools/sdlc_next_skill.py`, `tools/sdlc_verdict.py`, `tools/sdlc_stage_marker.py` |
| Anti-criterion: no `sdlc-{N}` construction outside the resolver | see `AC-MINT` below | the only hit is the mint rung inside `tools/lane_identity.py` |
| Every plan resolvable | see `AC-TRACKING` below | exit code 0 |

**Pipe-escaping warning.** The table cells above cannot contain a literal `|` — markdown
would split the cell. Every command whose regex needs alternation therefore lives in the
fenced block below, **unescaped and runnable as written**. Do not copy a `\|` out of a
table cell into a `grep -E`: inside an ERE, `\|` is a *literal pipe character*, not
alternation, which is precisely how the prior draft's liveness-field guard came to be
vacuous.

```bash
# AC-PROSE / AC-DOCSWEEP — live docs must not name the symbol's old home.
# Scope EXCLUDES docs/plans/completed/: those are historical records of shipped
# work and are correct as written. Rewriting them would be exactly the
# "historical artifacts in docs" the repo forbids, in reverse.
grep -rn '_sdlc_utils.*find_plan_path' docs/features/ docs/sdlc/ | wc -l   # AC-PROSE, expect 0
grep -rln 'find_plan_path' docs/features/ docs/sdlc/                      # AC-DOCSWEEP
# Every file AC-DOCSWEEP lists must be read and updated. Do not work from an
# enumerated list in this plan — re-run the grep at build time; the set will
# have moved. Verified at revision time to include, at minimum,
# docs/features/off-pipeline-merge-path.md, docs/features/sdlc-pipeline-portability.md
# (twice, one of which also documents the _git_toplevel helper), and
# docs/sdlc/do-plan-critique.md.

# AC-BELIEF — the false "repo never creates session/sdlc-{N}" comments are gone
grep -rnE 'never creates one|this repo never creates' tools/ tests/ | wc -l   # expect 0

# AC-LIVENESS — no holder/pid/host/heartbeat/last_seen field on the ledger
grep -nE '^[[:space:]]+(holder|pid|host|heartbeat|last_seen)[[:space:]]*=' \
  agent/pipeline_ledger.py | wc -l                                            # expect 0

# AC-BACKFILL — the migration is a read-only probe, it writes nothing
grep -n 'slug' scripts/update/migrations.py | grep -cE 'save|create|update_fields'  # expect 0

# AC-HEAL — only lane-start paths opt into healing
grep -rn 'allow_heal=True' tools/ agent/ reflections/
# expect exactly: tools/sdlc_session_ensure.py, tools/valor_session.py,
#                 reflections/sdlc_upvote_lanes.py, reflections/sdlc_progress.py

# AC-MINT — the lane-slug literal is constructed in exactly one place.
# The trailing filter excludes OTHER `sdlc-` namespaces by shape (not by site):
# sdlc-local-{N} session ids, the sdlc-cold-issue-marker-{N} synthetic id, and
# the sdlc-{stage} tag form are unrelated identifiers and stay where they are.
grep -rnE 'sdlc-\{|sdlc-%s|"sdlc-" *\+|f"sdlc-' tools/ agent/ reflections/ \
  | grep -vE 'sdlc-local-|sdlc-cold-issue-marker-|sdlc-\{stage\}|sdlc-progress-check'
# expect exactly one hit, the mint rung in tools/lane_identity.py.
# Verified red at revision time: hits at tools/valor_session.py:92,107,
# tools/sdlc_stage_query.py:349, reflections/sdlc_upvote_lanes.py:133,329, and
# the two mint sites — every one of which this plan removes or rewrites.

# AC-TRACKING — every plan doc carries a resolvable tracking issue
.venv/bin/python -c "
import pathlib, re, sys
pat = re.compile(r'^tracking:.*(?:#\d+|issues/\d+)', re.M)
bad = [p.name for p in pathlib.Path('docs/plans').glob('*.md') if not pat.search(p.read_text())]
print(len(bad), bad)
sys.exit(1 if bad else 0)"
# Requires a real #N or issues/N token. The literal 'tracking: none yet' FAILS —
# the prior draft's '^tracking:\s*\S' accepted it, green-lighting the exact case
# Task 5 exists to fix. Verified red at revision time: 5 files, including
# resilience-simplification-three-tier.md and session-recovery-observation-audit.md.
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
yet; after the fix it must name exactly the four sanctioned callers) and the docs-sweep
row. `AC-LIVENESS` is the deliberate exception among zero-expecting rows — it guards
against *adding* something, so it is green on `main` and must stay green; its value is
that the fixed ERE can now actually fire, which the fixed spelling is verified to do
against a synthetic `holder = ...` line. Verified red at revision time: `AC-TRACKING`
(5 files), the `.stem` sweep (`tools/sdlc_next_skill.py:205,357`), `AC-BELIEF`,
`AC-MINT`, and both stale-import rows.

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | `resolve_lane_slug` defaults to `allow_heal=True` and the plan routes read-only query paths through it (`tools/sdlc_stage_query.py:487` `_compute_meta`, and `find_plan_path` rung 2). `_compute_meta` runs for any issue number the router, dashboard, or an operator asks about, so rung 5 will mint and persist `sdlc-{N}` on a `PipelineLedger` for issues that are not lanes — contradicting the plan's own thesis that the slug is minted exactly once by `ensure_session`. The plan's own example proves it: a single `stage-query --issue-number 2718` would mint `sdlc-2718` for an issue the plan says must resolve to `None`. | pending | Make `allow_heal=False` the default: `resolve_lane_slug(issue_number, *, allow_heal: bool = False)`, with `ensure_session` the only opt-in caller, plus an anti-criterion grep that no `tools/sdlc_*.py` query path passes `allow_heal=True`. Also pin the ledger-creation question the plan leaves open: the resolver must use `PipelineLedger.load(...)` (direct-key HGETALL, no create) and return `None` when no ledger exists, so a read path can never bring a ledger into existence. |
| BLOCKER | Risk & Robustness | Architectural Impact forbids `tools/lane_identity.py` from importing `tools/_sdlc_utils.py`, while Technical Approach requires the relocated `find_plan_path` to keep the plans-dir ladder "retained verbatim" — but step 2 of that ladder is `_git_toplevel()` at `tools/_sdlc_utils.py:169`. Both cannot hold. `_git_toplevel` also cannot simply move: `tools/_sdlc_utils.py:111` (`_resolve_target_repo`) calls it and 14 tests monkeypatch the literal path `tools._sdlc_utils._git_toplevel`. The asserted direction `_sdlc_utils → lane_identity` is unexplained — with `find_plan_path` deleted and no shim, no task gives `_sdlc_utils` any reason to import `lane_identity`. | pending | There is no actual cycle today: `tools/_sdlc_utils.py:24-25` imports only `agent.sdlc_router` and `models.agent_session`; `tools/pr_head_resolver.py` is stdlib-only; `agent/pipeline_ledger.py` imports only `popoto`. Pick a seam explicitly — (a) move `_git_toplevel` to a leaf module both import, and add "retarget every `patch(\"tools._sdlc_utils._git_toplevel\", ...)`" to Test Impact (14 sites: `tests/unit/test_sdlc_utils.py:420,435,447,464,495,508,521,555,597,641`, `tests/unit/test_sdlc_env_vars.py:159,174`, `tests/unit/test_sdlc_stage_query.py:1067,1101`), or (b) permit `lane_identity → _sdlc_utils` for that one helper and drop the "would cycle" claim. |
| BLOCKER | Scope & Value | The Verification row "Every plan resolvable" makes a repo-wide docs invariant an exit criterion for this lane, and the plan states it is red right now and cannot go green until Open Question 2 is answered (`session-recovery-observation-audit.md` has no `tracking:` line by design). A lane cannot ship against a verification row whose pass condition is a pending human decision. The invariant is also unenforced after merge (no hook, no test), so it rots on the next plan doc added. | pending | Resolve Open Question 2 in the plan before build (the declared default is to move the audit doc out of `docs/plans/`), or narrow the row to active lanes with an explicit skip list. Note the row's regex `^tracking:\s*\S` also accepts the literal `tracking: none yet`, so `resilience-simplification-three-tier.md` passes while being exactly the unresolved case Task 5 names — tighten it to require a `#N` or `issues/N` token or the row validates nothing it claims to. |
| BLOCKER | History & Consistency | The Problem table names three minters of `sdlc-{N}`; Task 3 removes two. `tools/valor_session.py:107` (`_derive_slug_from_message`, verified to return `f"sdlc-{match.group(1)}"` and to not call `ensure_session`) survives untouched, is absent from No-Gos, and is caught by no anti-criterion. That reproduces the exact #1915 failure the plan diagnoses ("A comment cannot enforce a convention against live minters") at one-third scale: `valor-session create "handle issue #N"` still mints a competing identity, and because the write is conditional-on-empty, whichever writer runs first wins non-deterministically. | pending | `_derive_slug_from_message` is a pure string function with no issue-lifecycle context, so it cannot call the resolver in place — the seam is its caller: resolve the issue number first, then `slug = resolve_lane_slug(issue_number, allow_heal=True) or _derive_slug_from_message(message)`. Guard the `None` issue-number case so the documented "generic message → `None` slug" behavior in `tools/valor_session.py` is preserved. |
| CONCERN | Risk & Robustness | Adoption ladder rung 2 recovers a branch name by matching the PR head SHA against one `git ls-remote --heads origin` listing, but that match is not unique. On a remote with 451 `session/` heads (the plan's own count), a re-created lane branch, a fork, or a `session/dev-*` left at the same tip yields two or more matching refs. No tie-break is defined, so the adopted identity becomes listing-order dependent — a per-invocation identity, the same failure class the plan rejects when it excludes the machine-local `git worktree list` rung. | pending | Collect all matches before deciding: `matches = [ref for sha, ref in listing if sha == head_sha and ref.startswith("refs/heads/session/")]`; adopt only when `len(matches) == 1`, otherwise log a warning naming all candidates and fall through to rung 3. Also handle merged-and-deleted: `resolve_pr_head_sha` can return a SHA whose branch no longer exists on origin — that must be a clean fall-through, not an error. |
| CONCERN | Scope & Value | Open Question 3 defers the reflections' lane-*discovery* guess (`gh pr list --head session/sdlc-{N}` in `reflections/sdlc_upvote_lanes.py` and `reflections/sdlc_progress.py`) while Success Criteria assert the class is closed. After this lane a reflection that mints nothing will still probe `session/sdlc-{N}` to find a lane whose recorded slug is human-named — the #2718 wedge shape from the opposite direction, and now silent, because the recorded slug exists and looks authoritative. | pending | Both sites already receive `issue_number`, so the substitution is one line each: `head = lane_branch_name(resolve_lane_slug(issue_number, allow_heal=False))` with an explicit `if head is None: skip the --head leg` guard — the `None` case must skip the leg, never emit `--head session/None` (the literal the plan already bans under Empty/Invalid Input Handling). If deferred instead, file the increment-2 issue as part of this stage's output and reference it in No-Gos, as the plan already does for `agent/sdk_client.py:474`. |
| CONCERN | History & Consistency | Two anti-criteria that are supposed to enforce "Closing on a sweep, not a list" cannot fail. (1) `grep -nE '^\s+(holder\|pid\|host\|heartbeat\|last_seen)\s*='` uses escaped pipes inside an ERE, where they are literal characters, not alternation — the "no holder/pid/liveness field" guard passes vacuously. (2) `grep -rn 'Path(plan_path).stem'` is a literal check against one spelling; `plan_path.stem` (the natural spelling once `find_plan_path` returns a `Path`) defeats it — a sweep defeated by a variable rename. | pending | Fix the ERE to unescaped alternation: `grep -nE '^\s+(holder\|pid\|host\|heartbeat\|last_seen)\s*=' agent/pipeline_ledger.py`. Broaden the derivation sweep to `grep -rnE '\.stem\b' tools/ agent/ reflections/` reviewed by hand, or assert it at the test layer. Validate every anti-criterion is demonstrably red against current `main` before the fix — a grep that never matches is otherwise indistinguishable from one that passes. |
| CONCERN | History & Consistency | The Documentation section names one stale reference to the moved symbol (`docs/sdlc/do-plan-critique.md:156`), but seven docs under `docs/features/` and `docs/sdlc/` reference `find_plan_path`, and three name its old home in prose the anti-criterion grep will not match: `docs/features/off-pipeline-merge-path.md:106`, `docs/features/sdlc-pipeline-portability.md:22` and `:158` (the latter also documents the `_git_toplevel` helper the move disturbs). The plan enumerates where its own doctrine says to sweep. | pending | Replace the enumerated doc list in Task 8 with a sweep: `grep -rln 'find_plan_path\|_sdlc_utils' docs/`. The existing anti-criterion matches the aliased import form (`from tools._sdlc_utils import find_plan_path as _find_plan_path` at `tools/sdlc_stage_query.py:51`, `tools/sdlc_verdict.py:104`) but nothing catches the markdown prose form — add `grep -rn '_sdlc_utils.*find_plan_path' docs/ \| wc -l` expecting 0. |
| NIT | Scope & Value | The plan's central conceptual move is "Two slugs, kept distinct", then places both resolvers in one module named after only one of them. A future reader who opens `lane_identity.py` and finds `find_plan_path` there has a standing invitation to re-unify them — the rabbit hole the plan explicitly forbids. | pending | N/A (NIT) — either name the module for what it owns (e.g. `tools/sdlc_identity.py`) or put the two-slug distinction in the module docstring's first line, not only in the ladder-order rationale. |

---

## Open Questions

1. **Slug stamping onto `AgentSession`.** Task 3 stamps the resolved slug onto
   `AgentSession.slug` so the executor's existing readers (`session_executor.py` task-list
   id, calendar slug, `sdk_client.py`'s `SDLC_SLUG` env) see the same identity. That is a
   second home for the value, which cuts against "one record". The alternative is leaving
   `AgentSession.slug` `None` for SDLC lanes and having those readers call the resolver —
   more call sites, but one authority. **Which do you want?** Default if you do not
   answer: stamp it, with the ledger documented as authoritative and the `AgentSession`
   copy explicitly a convenience mirror.

2. **`session-recovery-observation-audit.md`.** It has no `tracking:` line because it is
   an audit document, not a lane plan. The "every plan resolvable" Verification row would
   fail on it. **Move it out of `docs/plans/` (to `docs/` proper), or add an explicit
   `tracking: none — audit document` sentinel the check accepts?** Default: move it, since
   `docs/plans/` should mean "lane plans".

3. **Scope boundary confirmation.** This plan touches `reflections/sdlc_upvote_lanes.py`
   and `reflections/sdlc_progress.py` only to remove their minting. It does **not** touch
   their branch/PR-discovery mechanisms (`gh pr list --head session/sdlc-{N}`), which are
   the same guess in a different layer. Leaving them means the reflections still guess
   when *finding* a lane even though they no longer guess when *naming* one. **In scope
   for increment 1, or hold for increment 2?** Default: hold — increment 1 is about who
   writes the identity.
