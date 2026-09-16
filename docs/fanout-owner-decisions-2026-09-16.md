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
