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

## Critique Results (round 1, 2026-09-16)

**Verdict: NEEDS REVISION.** Six blockers, each independently re-verified
against source by the orchestrator rather than taken from the critique.

The plan's central insight is correct and survives: the repo guards worktree
*deletion* and not worktree *acquisition*. Almost everything else about the
implementation is wrong. The guard is at a site the incident path never
reaches (B1), the probe it calls cannot answer the question asked of it (B2),
its busy predicate would wedge normal lanes (B3), its process scan refuses the
caller itself (B4), the skill points agents at a different door entirely (B5),
and its verification cannot run (B6).

**This plan must not go to build in its current form.** The revision is large
enough to be a re-plan of Tasks 1-4, not an edit pass.

### B1 — The guard is wired to a call site the incident path never reaches

`agent/session_executor.py:1435` calls `get_or_create_worktree` only when:

```python
if needs_wt and (WORKTREES_DIR not in str(working_dir) or not working_dir.exists()):
```

In the duplicate-dispatch case the second session arrives with `working_dir`
**already** pointing at `.worktrees/{slug}` and the directory **already**
present, because the first owner created it. The condition is False, the block
is skipped, and no acquisition call happens. Anything wired into
`get_or_create_worktree` is dead code for that dispatch.

The executor's own comment at `:1409-1417` names this as the *typical* shape:
a parent eng session creating child eng sessions with `working_dir` already set
to `.worktrees/{slug}/`. So the highest-volume duplicate-producing path is
exactly the path that bypasses acquisition.

This is confirmed by the live incident on lane `sdlc-3259` (see below): both
sessions arrived with the lane path already set. **The plan as written would
not have prevented the incident it was written for, and would not even have
fired.** Q1 is therefore moot as posed — the choice is not "new function vs
parameter", it is "wrong site vs right site".

**Required revision:** the ownership check moves to **lane entry, not lane
creation** — after `working_dir` is finally resolved and before
`agent_session.exec_cwd = str(working_dir)` (`agent/session_executor.py:1580`),
for *any* session whose resolved `working_dir` is under `WORKTREES_DIR`,
regardless of whether this run created the worktree. Guarding
`get_or_create_worktree` as well is fine as defence in depth, but it cannot be
the primary site. This reshapes Tasks 1, 2, 4 and Success Criteria 1 and 5.

### B2 — `worktree_busy_probe` does not return what Task 1 assumes

Task 1 says the probe returns `(session_id, agent_session_id)`. It returns
`(state, detail)` — `agent/worktree_manager.py:653-667` collapses the two ids
with `a or b or "unknown"`. Task 1's owner-identity carve-out ("busy with a
session that IS the caller") is not implementable against that signature: when
`session_id` is non-empty the `agent_session_id` is never visible, and
`valor-session resume --id` accepts either, so a caller holding only the
`agent_session_id` would fail to recognize itself and refuse its own lane.

**Required revision:** call `_scan_worktree_sessions` directly (it returns
`(state, session_id, agent_session_id)` — `:503-505`) or add a
`worktree_owner_probe` wrapper that preserves both ids.

### B3 — "Busy" is status-only, with no liveness fence

`_scan_worktree_sessions` marks a worktree busy for any row whose status is
outside `TERMINAL_STATUSES`. `NON_TERMINAL_STATUSES`
(`models/session_lifecycle.py:74-89`) includes `dormant`, `paused`,
`paused_circuit`, `waiting_for_children`, `superseded`, `admitted` and
`paused_budget`. None of these implies a live process.

A `dormant` lane would read as occupied forever. `paused_budget` is documented
as **human-only recovery**, so such a lane would be permanently unacquirable by
any agent. Wiring this predicate into *acquisition* converts every paused or
dormant lane into a permanent denial — trading a rare duplicate-owner incident
for a routine, silent wedge. That is a worse failure than the one being fixed.

**Required revision:** the acquisition predicate must be a conjunction of
status and liveness, not status alone. It must reconcile with this repo's
existing position that liveness is judged by worktree mtimes plus pid/turn
rather than a probe-refreshed `updated_at`, and with the single-authoritative-
liveness convention owned by the session runner (`AgentSession.live_fence` is
referenced at `agent/session_executor.py:1570-1580`). Deletion may keep the
conservative status-only predicate; acquisition may not.

### B4 — The OS process scan refuses the legitimate owner, and refuses the caller itself

`_worktree_has_live_process` (`agent/worktree_manager.py:716-750`) returns the
first live process whose cwd is rooted in the worktree, **with no session
attribution**. Task 1 step 3 runs it unconditionally after the identity
carve-out.

Two consequences. A resuming owner with a leftover pytest, server or shell in
its lane is locked out of its own worktree. Worse, this repo's documented
acquisition entry point (`docs/sdlc/do-build.md:63`) is a `python -c` invocation
run by an agent **whose cwd is already inside the lane** — so the acquiring
process is itself a live process rooted in the worktree. That is self-refusal on
every single build, by construction.

**Required revision:** restrict the OS scan to the *creation* branch, where a
brand-new worktree can hold no legitimate process. On the re-entry branch either
drop it or exclude the caller's own process and its ancestry before treating a
pid as an owner.

### B5 — A third lane-entry route bypasses any guard on the wrapper

`get_or_create_worktree` is a one-line delegation to `create_worktree`
(`agent/worktree_manager.py:1642`), and `create_worktree` (`:1507`) is public.
The shipped skill points agents at the *inner* function and at raw git:

- `.claude/skills-global/do-build/SKILL.md:94` tells cross-repo builders to call
  `create_worktree(Path(TARGET_REPO), slug)` — not `get_or_create_worktree`.
- `:118-119` offers a raw `git -C "$TARGET_REPO" worktree add ...` baseline.

So guarding the wrapper leaves open both the door the skill actually names and
the raw-git door beside it.

**Required revision:** the check belongs in `create_worktree`, or in a shared
`_enter_lane` helper both call. Task 3 must also fix `SKILL.md:94` and the
raw-git fallback so the raw form is not offered where the context declares a
worktree manager.

### B6 — Verification rows that cannot pass

- Plan `:162` and `:267` name `tests/unit/test_worktree_manager.py`, which
  **does not exist**. The suite is a package:
  `tests/unit/worktree_manager/test_worktree_manager_{busy_guards,cleanup,creation,uncommitted,venv_provisioning}.py`.
  pytest exits 4 on a missing path, so the row can never pass. Point both at
  `tests/unit/worktree_manager/`.
- Remaining rows must be re-derived once the guard's location is settled, since
  several assert on symbols in `agent/session_executor.py` that the revised
  design may not place there.

### Live evidence: this failure class occurred during Phase 0

Hours after this plan was committed, two sessions were independently
dispatched onto lane `sdlc-3259` and both wrote to `.worktrees/sdlc-3259` on
branch `session/sdlc-3259`. It resolved without damage only by luck: the second
session committed and pushed cleanly rather than force-pushing, so there was no
divergence. Both arrived with the lane path already provisioned, which is what
makes B1 concrete rather than theoretical.

## Questions for the owner

**Q1 is superseded by critique blocker B1** — the question was "which function
do we guard", and the answer is that neither of them is on the incident path.
The guard belongs at lane entry in the executor. Left below for the record.

**Q1 (superseded). New function or a parameter on `get_or_create_worktree`?**
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

## Critique Results (round 1, continued): Concerns, Nits, and the two settled questions

All independently re-verified against source before being recorded.

### Q2 — SETTLED: refuse, do not queue

A `RuntimeError` at the lane-entry site does **not** strand the session.
`agent/agent_session_queue.py:2901` → `except Exception` → `:2966-2970` sets
`session_failed`; the `finally` at `:2971,2990,2996` finalizes to `failed` with
a diagnostic snapshot. `models/session_lifecycle.py:734-748` releases the issue
lock and clears the supervised-run signal on finalize, compare-and-delete and
exception-isolated, so **the issue lock is not stranded**. Nothing re-queues it:
the drip handles only `paused`/`paused_circuit` (`models/session_lifecycle.py:101-108`)
and startup recovery re-queues only rows left `running`
(`agent/agent_session_queue.py:2895-2898`). This is the same disposition as the
existing #887 eng-only refusal (`agent/session_executor.py:1456`, `:1487`), so
the path is already proven in production.

### Q3 — SETTLED: the busy predicate

```
busy(slug, acquirer) ⇔ ∃ row R :
     R.status ∈ NON_TERMINAL_STATUSES                          # index filter, NOT the verdict
  ∧  path_rooted(R.exec_cwd | R.working_dir, .worktrees/slug)  # existing matcher
  ∧  ¬owns(acquirer, R)                                        # ownership family, not id equality
  ∧  fence_is_live(R.live_fence["pid"], R.live_fence["create_time"])   # THE verdict
OR   _worktree_has_live_process(dir) attributable to a non-self pid    # unregistered writer
```

Error arm unchanged: an unanswerable query means refuse.

The verdict arm is the runner-owned liveness signal, not a new one:
`agent/pid_fence.py:127`, stamped by `models/agent_session.py:1361-1363`,
exposed as `live_fence` (`models/agent_session.py:1311`), consumed the same way
at `models/session_lifecycle.py:525`, `agent/session_health.py:5056`,
`agent/agent_session_queue.py:2073`. This honours the single-authoritative-liveness
convention instead of inventing a second inference.

This un-wedges **B3**: `dormant` / `paused` / `paused_budget` / crashed-`running`
rows have no live subprocess, so the fence is dead and the lane is acquirable,
while a live owner is still refused. The status set stays exactly as it is at
`agent/worktree_manager.py:495` — the plan's error was treating it as the verdict.

**Worktree mtimes stay out of the predicate.** They answer "is this session
progressing", not "who owns this lane", and cannot separate a live owner from a
stale artifact. Put the lane's newest mtime in the refusal log as operator
context instead. `updated_at` appears nowhere (see the SDLC liveness mirage).

**Residual race the re-plan must name:** `pending`/`admitted` rows have no fence
yet, so `fence_is_live(None, …)` is False and two unspawned duplicates both read
clear, then race at spawn. Mitigate with check-stamp-recheck around
`agent/session_executor.py:1580`. This is optimistic, not a mutex — say so in
the plan rather than implying exclusion.

**Explicit decision required (do not default it):** an acquirable dormant lane
means a new owner can take a lane whose dormant owner may later wake, at which
point the woken session is refused. That is correct but it is a behavior change.
Reserving dormant-with-uncommitted-changes is a deliberate carve-out.

### Concerns

- **C5 (highest).** Once the guard moves to lane entry, these tests go
  *vacuously green* — they patch `get_or_create_worktree` by name, which is no
  longer the guard site: `tests/unit/test_session_executor_runner_dispatch.py:167,1096,1159,1244,1309`;
  `tests/unit/test_session_executor_lane_visibility.py:136`;
  `tests/unit/test_valor_session_working_dir_resolution.py` (7 sites);
  `tests/unit/test_pm_session_auto_slug.py:89,129,162,199`;
  `tests/integration/test_runner_dispatch_e2e.py:108,183`;
  `tests/integration/test_parent_child_round_trip.py:199`.
  Enumerate and re-target before building. Test Impact currently lists four files.
- **C2.** The guard sits downstream of issue-lock acquisition
  (`models/session_lifecycle.py:734-748`), so a refused duplicate that holds the
  lock releases it on finalize → the issue is unlocked while the first builder
  is still working → a third dispatch sails in. Either move the occupancy check
  ahead of lock acquisition, or skip the release when the refusal reason is
  `WorktreeOccupiedError`. Task 2 must pick one.
- **C3.** `:193-199` defers Q3 to "the guard's own refusal log", but no task
  produces one. Make a structured refusal line (slug, holder ids, refusing
  session id, lane mtime) a deliverable **and** a verification row.
- **C4.** `:69-72` claims item 1 makes the kill-producing inference unreachable.
  Per B1/B5 a builder can still be a second writer, so Task 3 is load-bearing,
  not defence in depth. Reword so it cannot be dropped as redundant.
- **C1.** `:40` "consumed by exactly one caller: `tools/disk_reclaim.py`" is
  false — also `agent/worktree_manager.py:1197,1203` (`reap_idle_worktree`) and
  `:2205,2230` (`remove_worktree`). Correct the caller set; any probe signature
  change must also update
  `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py:175-234`
  and `tests/unit/test_disk_reclaim.py:73,417`.

### Nits

- `:44` — the early return lives in `create_worktree`
  (`agent/worktree_manager.py:1507`), not `get_or_create_worktree` (`:1642`, a
  bare delegation). This is the distinction **B5** turns on.
- `:165-166` — the grep commands use repo-relative paths with no stated cwd.
- `:169-172` — the `! grep -q` anti-criterion row is well-formed; no blockquote
  breaks the table and no `-k` selectors exist. No change needed.
- `:76-83`, `:226-229` (lane Redis DB out of scope) and `:237-244` (Update
  System) are correct as written.

### Status

Critique complete: six blockers, five concerns, the nits above, nothing pending.
**This plan does not go to build.** Tasks 1-4 need a re-plan, not an edit pass.
Q2 and Q3 are now answered and should be folded in as decisions rather than
re-derived.
