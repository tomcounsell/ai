# Fan-out: open questions for the owner (2026-09-16)

Raised during the multi-lane fan-out described in the orchestrator brief. The
owner was afk and asked that blockers be parked rather than waited on, so work
continued around each one. **Nothing below has been acted on.**

Filed here rather than under `docs/plans/` deliberately: this is a decision
record, not a plan, and the plan-doc validators require sections (Test Impact,
Verification, No-Gos) that would be noise on it.

---

## Q1. PR #3171 (#3003, raw Redis clients) — patch it, or leave it to its author?

`tomcounsell` opened PR #3171 ("Route every non-ORM Redis client through
`utils/redis_client.py`") and it sits at `CHANGES_REQUESTED`. Issue #3003 is a
genuine defect — roughly twenty call sites bypass the ORM with raw Redis
clients — but the work is already half-done by someone else.

No competing lane was opened. The two live options:

- **Patch #3171 in place** via `/do-patch` against the existing head. Fastest
  path to landing, but it means editing another author's PR.
- **Leave it.** #3003 stays open until its author returns.

**Recommendation:** patch it. The review blockers are addressable and the branch
is not stale the way the two Valor PRs are. A `pr-3171-review` worktree already
exists on branch `redis-client-accessor-3003`.

**Decision:** _pending_

---

## Q2. PR #3165 — rebuild on fresh main, or close?

"Settle the release verify past a process's boot window". Head `6472cbd41`,
**713 commits behind main**, `CONFLICTING` / `DIRTY`, zero reviews, untouched
since 2026-09-05. Small: +208/−4 across 6 files. Its worktree
`.worktrees/dev-a4e15370` is the reason that lane survives GC (`open_pr`).

Its `Refs #1898` is **already closed**, so the defect it addresses may be moot.

**Recommendation:** before spending anything on it, re-confirm the defect still
reproduces on today's main. If it does, rebuild the change fresh rather than
rebase 713 commits — at +208 lines a rebuild is cheaper than the conflict
resolution. If it does not reproduce, close the PR and say so on #1898's trail.

**Decision:** _pending_

---

## Q3. PR #3123 — close and re-scope, or attempt the rebase?

"Route the SDLC control plane on read facts, grade verification three-valued,
and gate merge on it (Refs #3065)". Head `a9d411beb`, **777 commits behind
main**, +6999/−387 across 38 files, three rounds of `CHANGES REQUESTED`.

The sequencing gate the owner set in their own 2026-09-10 comment is now met:
the #3249/#3260 three-call-site fix was to land first, and **#3249 and #3260 are
both CLOSED/COMPLETED**. That comment also asked for "a real rebase onto it and
a re-justification of what remains".

**Recommendation:** close it and re-scope. A 777-commit-behind rebase of 6999
lines across 38 files is a rewrite wearing a rebase's clothes, and the
re-justification the owner asked for is most of the work either way. Re-scope
the surviving behavior into fresh issues, and check specifically whether the
routing changes preserve the absent-`pr_head_sha` fail-closed behavior that
#3260 closed — that is the one property a re-implementation could silently drop.

**Decision:** _pending_

---

## Q4. The 13 unmerged worktree lanes

Phase 0b of the brief called for `scripts/worktree-gc.sh --apply`. **That script
no longer exists** — `tools/disk_reclaim.py` replaced it explicitly ("Replaces
the unguarded `scripts/worktree-gc.sh`"), and the replacement is guarded:
removal goes through `cleanup_after_merge`, so a lane with uncommitted changes,
a live session, or an unmerged branch is never removed.

Run against `.worktrees/`, even with the age floor dropped from its default 14
days to 2, the sweep reclaims **2 of 32** lanes. The rest:

| Reason | Count |
|---|---|
| `unmerged` (branch carries work not on main) | 13 |
| `uncommitted_changes` | 6 |
| `too_young` | 7 |
| `open_pr` (`dev-a4e15370`, backs #3165) | 1 |
| `protected` (`nightly-baseline`) | 1 |

So the worktree count is not garbage awaiting a sweep — it is **13 lanes of
unlanded work plus 6 dirty trees**, and no automated sweep can or should touch
them. Reducing it is a per-lane land-or-abandon decision.

The eight `.claude/worktrees/agent-*` trees are outside this sweep's scope
entirely (it walks `.worktrees/` only) and were left alone.

**Recommendation:** treat this as its own triage pass, one lane at a time,
rather than a GC step. Until then the fan-out holds to the 3-concurrent-lane cap
and never reuses an existing worktree slug.

**Decision:** _pending_

## Q5. #3258 (Phase 0c) — re-plan, or leave parked?

Phase 0c of the brief called for fixing the duplicate-builder defect. It reached
the plan stage and stopped there. `docs/plans/worktree-single-owner-dispatch.md`
now carries a completed round-1 critique: **six blockers, five concerns, and the
nits**, with Q2 (refuse, do not queue) and Q3 (the busy predicate, with
`live_fence` as the verdict and status as the Redis index filter only) settled.

The critique's conclusion is that Tasks 1-4 need a **re-plan, not an edit pass**.
The load-bearing reason is B1: the guard as designed is wired to a call site the
incident path never reaches, so building it as written would ship a guard that
cannot fire on the failure it exists to prevent. B5 compounds it — a third
lane-entry route bypasses any guard on the wrapper.

I did not take this to build, and I did not re-plan it, because a re-plan is a
full lane and Phase 0 was supposed to be the serial prelude to the fan-out, not
a fan-out lane of its own.

One correction to the record, since I got it wrong in this document's earlier
revision and in my report: the plan's "Live evidence, incident 2" was initially
written up as an unattributed second writer on the #3259 lane. It was not. It
was **that lane's own review subagent**, which I dispatched, checking out a SHA
inside the shared worktree to read files and then restoring the branch. It
disclosed this itself. The incident is corrected in the plan doc. It is still
real evidence of something, but of a *different* hazard than #3258's: a reader
that needs a pinned SHA is not a duplicate builder, yet it mutates the shared ref
anyway, and the plan's acquire/refuse framing has no disposition for it. Do not
cite incident 2 for B1's motivating scenario.

**Recommendation:** leave parked. The duplicate-builder risk is currently held
off by process (3-concurrent-lane cap, never reuse an existing worktree slug)
rather than by code, which is weaker but is holding. A re-plan should be its own
lane with B1 and B5 as its starting constraints.

**Decision:** _pending_

---

## Status at the Wave 1 boundary

**Phase 0 — closed.**

| Item | Issue | Outcome |
|---|---|---|
| 0a red main | #3313 | Closed. Ratchet re-verified green on `main @ 23964450c` (54 passed). |
| 0b worktree GC | — | **Deviation.** Script no longer exists; the replacement reclaims 2 of 32. See Q4. |
| 0c duplicate builders | #3258 | **Deviation.** Parked at plan stage after critique. See Q5. |
| 0d commit-hook cwd | #3259 | **Merged** (PR #3342, squash). `/update` run; fleet-deployed guard now shares an inode with main and passes its behavioral self-check in both directions. |

**Filed during Phase 0, did not exist at the start:** #3345 (the `cd` option-grammar
defect surviving verbatim in two other validators), #3346 (rung 2 honors only a
*leading* `cd`, so `git add -A && cd ~/src/popoto && git commit` evaluates the
wrong repo), #3347 (the nightly detector files `gh` quota exhaustion as code
regressions).

**Triaged, not taken as lanes:** #3287 and #3288 closed as environmental after
re-running both nodes four consecutive times green on current main with the quota
healthy; the class went to #3347, cross-linked with #3243. #3333 and #3262
consolidated onto #3262 (the older open one), with #3333's triage carried across
and the finding re-verified still live at 543 lines against a 500-line cap.

**Wave 1 — dispatched**, three lanes at the verified concurrency cap: A #3091
(session_id uniqueness, the Wave 2 blocker), B #2652 (forum topics, resuming its
existing plan doc), C #2862 (expectation blocked state, resuming its existing
plan doc). Wave 2 (#2494, then #3282 behind it, #3256 alongside) stays shut until
Lane A merges.
