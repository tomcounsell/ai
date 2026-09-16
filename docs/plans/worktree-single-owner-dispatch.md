---
tracking: 3258
slug: sdlc-3258
status: draft
---

# Worktree single-owner dispatch, and a non-destructive debris path

**Issue:** #3258 — the SDLC harness dispatched duplicate builders into three
worktrees that already had a live builder, and one duplicate `kill -9`'d two
live benchmark arms it mistook for orphans.

**Authored by:** the fan-out orchestrator, 2026-09-16, as Phase 0c. The owner is
afk; open questions are parked under "Questions for the owner" rather than
blocking.

## What is actually broken

The issue proposes three directions. Reading the code changes the shape of two
of them.

### 1. The ownership primitive exists. The acquisition path never asks it.

`agent/worktree_manager.py` already carries a complete, well-considered
tri-state busy check:

- `worktree_busy_check(repo_root, slug)` (line 637) — fail-OPEN. An unanswerable
  query reads as clear. Documented as being for interactive and post-merge
  removal, where a human is present to notice.
- `worktree_busy_probe(repo_root, slug)` (line 653) — fail-CLOSED tri-state
  `("clear" | "busy" | "error", detail)`. Its own docstring says *"Unattended
  callers must use `worktree_busy_probe` instead."*
- `worktree_busy_probe_many(repo_root, slugs)` (line 670) — one Redis fetch for
  a whole sweep, sharing the single-slug matcher so the two cannot drift.
- `_worktree_has_live_process(worktree_dir)` (line 716) — an OS-level scan for a
  process whose cwd is rooted in the worktree, which catches a *foreign* process
  with no `AgentSession` row. This is the probe that would have seen the
  benchmark arms.

Every one of these is consumed by exactly one caller: `tools/disk_reclaim.py`,
which uses them to decide whether a worktree is safe to **delete**.

Nothing on the **acquisition** path consults any of them. `get_or_create_worktree`
(line 1612) is a one-line delegation to `create_worktree`, which returns early
when the directory is present — with no question asked about who is already
working there. Its two production callers are:

- `agent/session_executor.py:1439` — the harness dispatch path, the one the
  incident came through.
- `tools/valor_session.py:781` — `valor-session create --slug`.

So the guard the issue asks for is not a new mechanism. It is a call to a
function that already exists, from the one path that was never wired to it. The
asymmetry is the bug: we are careful about who owns a worktree when we are about
to delete it, and careless when we are about to put a second writer in it.

### 2. The destructive cleanup is agent behavior, not harness code

No `kill -9` on a worktree's processes exists in `agent/` for this path. The
duplicate builder *reasoned* its way to the kill: it found long-running
processes in a worktree it believed it owned, inferred "crashed run, orphaned
debris" from "I did not start these", and cleaned up. `agent/session_health.py`'s
cross-process orphan reaper is a separate, fenced mechanism (`agent/pid_fence.py`)
and is not what fired here.

That means the fix for the issue's item 2 is a **skill** change, not a code
change, and the guarantee it needs is the one already stated in
`agent/pid_fence.py`: a positive liveness signal that a pid belongs to a dead
run, never an inference from authorship. The single most valuable property is
that with item 1 in place, a builder can no longer *be* the second writer, so
the inference that produced the kill becomes unreachable in the first place.
The skill change is defense in depth behind that, not the primary fix.

### 3. Lane Redis DB assignment

The issue's "related, same root cause class" note describes hand-assigned lane
Redis DBs in the popoto run, with #586's tests and its benchmark arms both
initially on DB 12. In *this* repo that is already derived from one place:
`tests/db_claim.py` claims from the pool and `pytest_configure` exports the
claimed db process-wide, and `scripts/pytest-clean.sh` refuses a colliding
fallback (#2628). **Out of scope here.** It is a popoto-side gap, and folding a
second repo's allocator into this change would make the blast radius of a
substrate fix larger than the substrate.

## Tasks

### Task 1 — Fail-closed ownership check on the acquisition path

Add `acquire_worktree(repo_root, slug, base_branch="main", *, owner)` to
`agent/worktree_manager.py`, or extend `get_or_create_worktree` with a
`require_sole_owner: bool` parameter (see Question 1). Behavior:

1. If the worktree directory does not exist, there is no owner to conflict
   with. Create and return, unchanged from today.
2. If it exists, run `worktree_busy_probe(repo_root, slug)`.
   - `"clear"` → return the existing path, unchanged from today.
   - `"busy"` with a session that IS the caller → return the path. Re-entry by
     the same owner is the normal resume case and must not be broken; this is
     what `valor-session resume` and every multi-stage lane does.
   - `"busy"` with a DIFFERENT live session → raise
     `WorktreeOccupiedError(slug, holder_session_id, holder_agent_session_id)`.
   - `"error"` → raise `WorktreeOccupiedError` with the probe's reason. Fail
     closed. An unattended dispatcher that cannot ask the question must not
     answer it optimistically; that is the documented contract of `_probe`
     versus `_check` and the whole reason both exist.
3. Then run `_worktree_has_live_process(worktree_dir)`. A live foreign process
   with no `AgentSession` row is the benchmark-arm case, and it is invisible to
   the Redis probe. Treat a hit as occupied.

Identifying "the caller" is the one genuinely fiddly part: the probe returns
`(session_id, agent_session_id)`, and both call sites must pass in whichever of
those they hold. `session_executor` has the session in hand; `valor_session.py`
is creating one and has the `session_id` before the enqueue.

### Task 2 — Wire both call sites

- `agent/session_executor.py:1437-1439`: the `except Exception` around this
  block already has an `eng`-session branch that refuses to fall back to the
  main checkout (#887). A `WorktreeOccupiedError` must take that same
  refuse-and-fail path for **every** session type, not just `eng` — falling back
  to the shared checkout because a lane is occupied is strictly worse than
  failing the dispatch. The session should fail with the holder named in the
  error, so the supervising session sees *which* lane already has an owner
  rather than a generic failure.
- `tools/valor_session.py:781`: surface the holder in the CLI error. A human
  running `valor-session create --slug X` against an occupied lane wants the
  holder's id, not a traceback.

### Task 3 — Non-destructive debris handling in the build skill

`.claude/skills-global/do-build/SKILL.md`. State the rule positively: a builder
that finds processes in its worktree it did not start **reports them and stops**.
Name the reason — the processes may be the live work of another lane, and the
cost of being wrong is silent and unrecoverable (two benchmark arms, #3258) while
the cost of stopping is a message. If any cleanup path survives, it requires a
positive dead-run signal per `agent/pid_fence.py`, never the inference "I did not
start this, therefore it is debris."

### Task 4 — Tests

`tests/unit/test_worktree_single_owner.py` (new):

- A second acquirer against a worktree held by a live session raises, and the
  error names the holder.
- The **same** owner re-acquiring does NOT raise. This is the regression that
  matters most: a guard that breaks resume is worse than the bug, because every
  multi-stage lane re-acquires its own worktree on every stage.
- A probe `"error"` raises (fail closed), and is distinguishable from `"busy"`.
- A live foreign process with no `AgentSession` row is treated as occupied.
- `session_executor` does not fall back to the main checkout on
  `WorktreeOccupiedError`, for every session type, not only `eng`.

Prove it RED: with the guard removed, the second-acquirer test must fail. A
guard certifying absence is worthless until it has been seen to fail against the
known-bad.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New tests pass | `scripts/pytest-clean.sh tests/unit/test_worktree_single_owner.py -q` | exit code 0 |
| Worktree manager suite intact | `scripts/pytest-clean.sh tests/unit/test_worktree_manager.py -q` | exit code 0 |
| Session executor suite intact | `scripts/pytest-clean.sh tests/unit/test_session_executor_lane_visibility.py -q` | exit code 0 |
| Disk reclaim unaffected | `scripts/pytest-clean.sh tests/unit/test_disk_reclaim.py -q` | exit code 0 |
| Acquisition path consults the probe | `grep -q 'worktree_busy_probe' agent/session_executor.py \|\| grep -q 'acquire_worktree' agent/session_executor.py` | exit code 0 |
| Anti-criterion: the fail-OPEN check is not used by the dispatcher | `! grep -q 'worktree_busy_check' agent/session_executor.py` | exit code 0 |
| Lint | `python -m ruff check agent/ tools/` | exit code 0 |

Every anti-criterion row is written in the negated-quiet `! grep -q` form on
purpose. `grep -c` exits **1** when the count is zero and **0** when there are
matches, so a row phrased "count == 0" and judged by exit status scores a
correct fix as FAILURE and a broken one as PASS.

## Questions for the owner

**Q1. New function or a parameter on `get_or_create_worktree`?**
A new `acquire_worktree` leaves every existing caller's behavior untouched and
makes the fail-closed path opt-in, which is safe but leaves the unguarded door
open for the next caller to walk through. A `require_sole_owner=True` default on
`get_or_create_worktree` closes the door for everyone, at the cost of possibly
breaking a caller I have not found. There are only two production callers and
both should be guarded, so the orchestrator's recommendation is to **guard
`get_or_create_worktree` itself** and give the escape hatch an explicit,
noisy name. Overturn this in `/do-plan-critique` if the blast radius is wider
than the two call sites suggest.

**Q2. Is failing the dispatch the right outcome, or should it queue?**
The issue says "refuse (or queue)". This plan refuses. Queueing needs a queue
with an owner, a retry policy, and a starvation story, none of which exist
today, and a refusal that names the holder is strictly more debuggable than a
silent wait. If you want queueing, it is a separate issue on top of this one.

**Q3. Where did the duplicates come from?**
This plan makes a duplicate builder *harmless*. It does not explain why the
harness produced duplicates into lanes the supervising session was tracking
one-agent-per-lane. That root cause is still unknown and is not closed by this
work. Recommend filing it separately once the guard is in and can report how
often it fires — the guard's own refusal log is the instrument that would find
it.

## Success Criteria

1. A dispatch into a worktree already held by a **different** live session
   fails with `WorktreeOccupiedError` naming the holder, rather than producing
   a second writer.
2. A probe that cannot be answered (`"error"`) also fails the dispatch. Fail
   closed: the unattended dispatcher is exactly the caller
   `worktree_busy_probe`'s docstring says must not use the fail-open variant.
3. A live process rooted in the worktree with no `AgentSession` row counts as an
   owner. This is the benchmark-arm case and it is invisible to the Redis probe.
4. The **same** owner re-acquiring its own worktree still succeeds. Every
   multi-stage lane does this on every stage; a guard that breaks resume is a
   worse regression than the bug it fixes.
5. `agent/session_executor.py` does not fall back to the main checkout on an
   occupancy failure, for any session type.
6. `.claude/skills-global/do-build/SKILL.md` instructs a builder that finds
   processes it did not start to report and stop.
7. The second-acquirer test is observed RED with the guard removed.

## No-Gos (Out of Scope)

- **Queueing an occupied dispatch.** This plan refuses and names the holder. A
  queue needs an owner, a retry policy, and a starvation story, none of which
  exist. See Question 2.
- **Lane Redis DB allocation.** The issue's "related" note describes a popoto
  hand-assignment gap. In this repo it is already derived from one place
  (`tests/db_claim.py` + `pytest_configure` + `scripts/pytest-clean.sh`'s
  no-colliding-fallback rule, #2628). Folding another repo's allocator into a
  substrate fix would make the blast radius larger than the substrate.
- **Root-causing where the duplicate dispatches came from.** This plan makes a
  duplicate harmless; it does not explain the duplicate. See Question 3 — the
  guard's own refusal log is the instrument that would find it, so this is
  correctly sequenced *after* this work, not folded into it.
- **Reworking `agent/session_health.py`'s cross-process orphan reaper.** It is
  fenced (`agent/pid_fence.py`) and is not what fired in the incident.

## Update System

No `/update` payload changes. Nothing here is hardlinked to `~/.claude/` except
`.claude/skills-global/do-build/SKILL.md` (Task 3), which propagates to the
fleet by the existing skills-global hardlink sync — so `/update` IS required
after merge for the skill change to reach running machines, but no entry in
`RENAMED_REMOVALS` or `scripts/update/hardlinks.py` is needed: no file is added,
moved, or renamed.

## Agent Integration

The behavior change is visible to every agent that acquires a lane worktree:

- A builder dispatched into an occupied lane now **fails with a named holder**
  instead of starting work. Supervising sessions must read that failure as
  "this lane already has an owner", not as a transient error to retry.
- `.claude/skills-global/do-build/SKILL.md` gains the report-and-stop rule for
  unexpected processes (Task 3). This is the agent-facing half of the fix.
- No new tool or CLI surface. `valor-session create --slug` on an occupied lane
  reports the holder rather than silently resuming into it.

## Test Impact

Existing tests that touch the acquisition path and may need dispositions:

- [ ] `tests/unit/test_session_executor_lane_visibility.py` — UPDATE if any case
      dispatches twice into one slug without a live-session row; the fail-closed
      `"error"` branch can turn a previously-silent Redis-unavailable path into
      a raise. Audit before building; the file's own comment says it exercises
      the `worktree_busy_check` chain genuinely rather than mocked.
- [ ] `tests/unit/test_worktree_manager.py` — UPDATE if any test calls
      `get_or_create_worktree` against an existing worktree while a live
      `AgentSession` row for that slug is present in the test db.
- [ ] `tests/unit/test_session_revival_cleanup.py` — AUDIT only. It already
      monkeypatches `worktree_busy_probe` to `("clear", "")`, so it should be
      unaffected, but that patch now covers a second call site.
- [ ] `tests/unit/test_disk_reclaim.py` — no change expected. It patches the
      probes for the deletion path, which this work does not touch.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-lane-identity.md` — the slug ties together task
      list, branch, and worktree; add that a lane worktree has at most one
      owner and how a second acquirer is refused.
- [ ] Row in `docs/features/README.md` **only if** a new feature doc is created.
      This repo's `README.md` index is the fan-out's serialized collision
      surface: the lane must request the row from the orchestrator rather than
      editing the file.

### External Documentation Site
Not applicable. This repo has no Sphinx/MkDocs site.

### Inline Documentation
- [ ] `acquire_worktree` / `get_or_create_worktree` docstring states the
      fail-closed contract and why it differs from `worktree_busy_check`'s
      fail-open one, with the #3258 incident named.
- [ ] `WorktreeOccupiedError` carries the holder ids in its message, not only in
      attributes — the message is what reaches the supervising session's log.
- [ ] The `session_executor` refusal path comments why falling back to the main
      checkout is never correct here (#887 established this for `eng`; #3258
      extends it to every type).
