# Lane Branch Identity: The Record, Not the Slug

An SDLC lane has two identities, not one. [SDLC Lane Identity](sdlc-lane-identity.md)
covers the first — the lane's **slug**, recorded once on `PipelineLedger.slug`
and read everywhere else. This document covers the second: the lane's
**branch**. The slug seeds the branch's name once, at worktree creation, and
is never consulted for it again.

## The invariant

At the end of every turn, `AgentSession.branch_name` equals the worktree's
live `HEAD` branch, or is empty if the worktree is detached. Everything that
needs a lane's branch reads that record. Nothing re-derives it from the slug
except to seed a worktree that has never been checkpointed.

## Root cause

A lane's branch used to be re-derived as `f"session/{slug}"` by every
consumer that needed it, instead of read from a record. On 2026-09-16 that
cost a shipped PR its report: the agent had checked out a differently-named
branch mid-turn, and `mark_work_done` (`agent/branch_manager.py`) ran a bare
`git branch -d {branch_name}` gated only on `switched and
branch_name.startswith("session/")` — no worktree check, no merge predicate
— one line before the #1646-guarded `safe_delete_branch`. The slug-derived
name was deletable precisely because it was not where the work was. 646 ms
later, the next turn re-derived that same name, found it gone, and the #1377
launch guard refused to launch. The user-visible symptom was a Telegram
thread receiving a reaction emoji and nothing else.

The bug was ordering, not an override: the #1646 guard was never bypassed by
a flag or an exception, it simply never ran before the destructive delete.
Fixing it required removing branch deletion from `mark_work_done` entirely
and establishing one recorded source of truth for what a lane's branch is.

## Three roles, deliberately not collapsed into one

- **The record** — `AgentSession.branch_name`. The sole source of truth for
  what a lane's branch *is*. Written by exactly one component,
  `checkpoint_branch_state` (`agent/agent_session_queue.py`).
- **The seed** — `session/{slug}`, built by `tools.lane_identity.lane_branch_name`.
  It names a branch at worktree creation and answers for a lane that has
  never been checkpointed. It is not a fallback consumers reach for casually
  — seeding is its only remaining job.
- **The live `HEAD`** — read by `tools.lane_identity.read_worktree_branch`,
  and not a competing source. It is the truth about *now*; the record is the
  truth about *what this lane is*. The live `HEAD` is the input the record
  is refreshed from, never an answer handed directly to a consumer.

## The one writer, the one accessor

- **`checkpoint_branch_state`** (`agent/agent_session_queue.py`) is the sole
  writer that *updates* the record. It reads the worktree's live `HEAD`
  through `read_worktree_branch` and writes it onto `AgentSession.branch_name`,
  clearing the field on a detached `HEAD` and leaving it untouched on a read
  failure.
- **`resolve_lane_branch`** (`tools/lane_identity.py`) is the sole accessor.
  Every consumer that needs a lane's branch — the #1377 launch guard, the
  end-of-turn cleanup, the nudge path, diagnostics — reads through it. Its
  ladder: the recorded branch first; the `session/{slug}` seed for a lane
  that has never been checkpointed; a session-id-derived seed for a slug-less
  ad-hoc session; `None` if nothing applies. It never returns `""` or the
  literal `"HEAD"`.

There is a second, narrower writer: `agent/session_executor.py`'s
`seed_lane_branch_if_unrecorded` writes the `session/{slug}` seed onto a
freshly-created `AgentSession` row, but only when `branch_name` is still
unset — never as an overwrite. Turn start used to write that seed
unconditionally, which made the record incoherent (two writers, last-write-
wins, neither aware of the other); the conditional is what keeps
`checkpoint_branch_state` the only component that ever *updates* an existing
record.

`refresh_lane_branch` (`tools/lane_identity.py`) wraps `checkpoint_branch_state`
for the two call sites that need to re-read the worktree's `HEAD` onto the
record around a destructive operation: once before end-of-turn cleanup, so
the deletion targets the branch that actually holds the turn's commits, and
once after, so the record follows the worktree back to `main` rather than
continuing to name a branch cleanup just deleted. Skipping the trailing call
recreates the original defect under a different, more plausible-looking
branch name.

## Truthiness, never `is None`

Popoto stores an unset string field as `""`, not `None`. Every read of
`branch_name` in this area — `derived_branch_name`, `resolve_lane_branch`,
`seed_lane_branch_if_unrecorded` — tests with `.strip()` truthiness, never
`is None`. An `is None` test reads an unset record as a set record holding an
empty name, which is indistinguishable from a real bug: the guard that
consumes it would raise on an empty expectation.

## The detached-`HEAD` rule

`git rev-parse --abbrev-ref HEAD` answers with the literal string `"HEAD"`
for a detached worktree — not a branch name, and not to be stored or
compared as one. `read_worktree_branch` is the only spelling of that git call
on the lane-identity path: every read whose answer reaches
`AgentSession.branch_name` goes through it. It normalizes `"HEAD"` (and any
git failure, missing path, or non-repo path) to `None`, and it never raises.

Other path-scoped spellings survive elsewhere in the repo and are enumerated
in `tools/lane_identity.py`'s module docstring. Two are deliberate
(`verify_worktree_branch` must raise rather than return `None`; a WIP-ref path
needs the raw `"HEAD"` literal as a gate). One is a live hazard:
`agent/branch_manager.py:38` `get_current_branch` returns the literal `"main"`
on any failure, so a failed read reports a lane as being on the default
branch — worse than storing `"HEAD"`, because `"main"` is a real branch and
nothing downstream can tell the fallback from a true answer. It is not on the
identity path today.

When a worktree is detached, `checkpoint_branch_state` clears `branch_name` rather
than storing `"HEAD"`, and the end-of-turn cleanup path skips branch cleanup
entirely — there is no branch to mark done or delete, and inventing one from
the slug would recreate the defect this document exists to close.

## Cleanup no longer deletes ahead of the merge predicate

`mark_work_done` (`agent/branch_manager.py`) no longer deletes branches at
all — it only archives the completed plan, commits that archival, and
returns the worktree to `main`. `safe_delete_branch`
(`agent/worktree_manager.py`) is the repo's sole branch-deletion site. Every
caller — the end-of-turn cleanup in `agent/session_executor.py` and the
revival-dormancy path in `bridge/telegram_bridge.py`'s
`retire_revival_branch` — calls `mark_work_done` first and `safe_delete_branch`
second, with the #1646 `merged_via_ancestor` predicate gating the delete.

This changes the revival-dormancy path's behavior for a branch that is not
merged: it is now preserved rather than destroyed to force dormancy. The
revival prompt for that lane may re-fire once per cooldown — that re-nag is
the designed cadence for genuinely unfinished work, not a regression.

## `python -m tools.lane_identity sweep`

`sweep()` walks every lane worktree under `.worktrees/` and reports, per
lane, the live `HEAD`, the recorded branch, and what `resolve_lane_branch`
would hand the launch guard next turn. It flags divergence and, separately,
whether a lane's next turn would be refused at the #1377 guard: refusal
requires either no expectation to launch against, an expectation naming a
branch that no longer exists, or a mismatch over a dirty worktree. A clean
mismatch is not a refusal — the #1377 guard auto-checks-out a clean
divergence, which is its own recovery path. `sweep()` reports only; it never
mutates. Deciding what a divergent worktree should be checked out to is an
operator judgment with uncommitted work at stake, and a wrong guess strands
exactly the lane it meant to save.

## The three guards, and what each does and does not protect

Three historical guards sit around this invariant. This work did not change
any of them — it removed the ordering bug that let a destructive delete run
ahead of the one that mattered.

- **#887 (main-checkout protection)** — refuses to run an eng session with a
  slug against the shared primary checkout instead of an isolated
  `.worktrees/{slug}/` directory. Protects against contaminating the shared
  working directory; says nothing about which branch a worktree is on.
- **#1377 (branch-mismatch guard)** — refuses to launch the harness when a
  reused worktree is not checked out to the branch `resolve_lane_branch`
  expects, auto-recovering a clean mismatch and raising on a dirty one.
  Protects the harness from starting on the wrong branch; it is also the
  guard that was tripped by the deleted branch name in the original
  incident, because it was handed an expectation the record no longer
  matched.
- **#1646 (unmerged-branch guard)** — `safe_delete_branch`'s merge
  predicate, refusing to delete a branch that is not proven merged into
  `main` (or that is checked out by another worktree). Protects work in
  progress from being destroyed by cleanup; it was never bypassed by an
  override, only run one line too late.

## See also

- [SDLC Lane Identity](sdlc-lane-identity.md) — the slug this branch identity
  is seeded from.
- [Eng Session Architecture](eng-session-architecture.md) — `derived_branch_name`
  as one of `AgentSession`'s derived properties.
