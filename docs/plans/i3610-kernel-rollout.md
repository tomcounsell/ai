---
tracking: none
slug: i3610-kernel-rollout
type: plan
status: merged
issue: tomcounsell/ai#3610
critique_rounds: 2
review_rounds: 2
governance_grant: none
---

# A merge of the kernel's own code rolls the running kernel forward

Issue #3610. Citations are to the rebuild branch at `2d6ed8a8e`.

**Goal.** Mission item 1, "Tom never coordinates the gaps between those
steps", and the constraint **Docs describe reality**. Once Valor merges
its own kernel changes, the running kernel takes them up with no hand-run
step after the merge.

## What is true today

- The plan says the merge does it. `docs/plans/valor-rebuild.md:98-101`:
  "The running kernel is a separate checkout. Releasing a merge that
  touches `core/` also pulls that checkout, applies any schema change, and
  restarts the kernel service, as part of the same tapped effect."
- No code does it. `core/` and `tools/` hold no `kickstart`, no pull, and
  no fetch into the kernel's checkout. `core/serve.py:86` names the label
  `com.valor.kernel` only for `plist` (`core/serve.py:666-688`).
- The merge performer pushes one commit to the target and returns
  `{"remote", "branch", "sha"}` (`tools/push_branch.py:167-185`); the
  broker writes that as `effect.outcome` with `kind: done`
  (`core/broker.py:633-673`). A merge settled by `reconcile` gets the same
  result shape from `lookup` (`tools/push_branch.py:198-203`).
- The resident kernel skips a merged task entirely
  (`core/serve.py:371-373`): nothing runs after `effect.outcome`.
- The lead does the rest by hand: `.claude/skills/build/SKILL.md:224-228`
  ("push, run the plan's rollout steps (back up first with
  `python -m core backup`)"); commit `2d6ed8a8e` is such a rollout.
- "Separate checkout" is not true. The kernel's job runs from
  `~/src/valor-rebuild` (its plist's `WorkingDirectory`, written by
  `core/serve.py:677-679` from `ROOT`, `core/serve.py:87`), the same
  checkout the lead fast-forwards (`docs/plans/m4-2-persona.md:573`: "the
  kernel checkout is `~/src/valor-rebuild` itself").
- The persona and the stage files are read from that checkout on every
  turn (`core/runs.py:112-125`), so a merge touching only `persona/` or
  `skills/` needs the pull and no restart (`docs/plans/m4-2-persona.md:365-368`).
- A restart is already safe for work in flight: `recover`
  (`core/serve.py:168`) ends a live turn as `interrupted` with reason
  "kernel restarted" (`core/serve.py:227-240`), and the scheduler starts
  it again (`tests/test_serve.py:160-173`). There is no drain to wait on
  (`docs/plans/critique-issues-out.md`, R4).
- The kernel process runs no Homebrew program today. `settings.pg_bin`
  (`/opt/homebrew/opt/postgresql@18/bin`, `core/settings.py:256`) is used
  only by `core/backup.py`, which only the command line and the backup job
  import; that prefix is user-writable, which `core/binaries.py:1-11`
  names as replaceable by a turn. So the kernel takes no backup.
- Every program the kernel runs outside the sandbox is root-owned
  (`core/binaries.py:1-11`); `/opt/homebrew/bin/uv` is not, so the kernel
  runs no `uv`. The project has three dependencies
  (`pyproject.toml:6-10`), so a lockfile change is rare.
- The kernel's job has `KeepAlive: True` (`core/serve.py:681`, and the
  installed plist): when `serve` returns or raises, launchd starts it
  again. `serve`'s `finally` cancels jobs, settles services and closes the
  gateway (`core/serve.py:633-637`).
- The gateway runs inside the kernel process (`core/serve.py:603-604`),
  and live turns' API calls go through it, so nothing slow may run on the
  loop.
- `db.migrate` defaults to `settings.database` (`core/db.py:45`).
  `python -m core migrate` also runs `secure-login`
  (`core/__main__.py:815-817`), which sets role passwords and rewrites
  `pg_hba.conf`.
- Rollout rows need no schema change: the fold ignores a row type it does
  not know (`core/machine.py:561-563`, falling through to `return None`),
  and `events.type` is free text (`core/schema.sql:19`).

## Design

One new module, `core/rollout.py`, called from the resident kernel's wake.
Every git call and the migrate run off the loop, through `git.threaded`
(`core/git.py:366`), so a slow step never stalls the in-process gateway.
Git and the migrate start through the watch (`git.start`), so cancelling
the loop kills what they started. The kernel runs no backup.

**Which merges.** A merge is the kernel's own when its `effect.outcome`
(kind `done`, action type `merge`) names a `remote` equal, as a string, to
the push URL of the kernel's checkout (`git.push_url(checkout)`,
`core/git.py:590`), and a `branch` equal to the checkout's branch
(`git.branch`). The facts come from the outcome the kernel wrote and from
the checkout's own config, never from the task's workspace or anything a
turn wrote. `projects/valor.toml` is not consulted: the checkout is the
thing being rolled, so its own remote and branch are the definition. A
merge to another remote or branch is not the kernel's and writes nothing.

**Which one is due.** `started` is the commit the process started from,
`git.head(checkout)` read once in `serve` before `recover`. Each wake
first reads the merge outcomes with no `rollout.ended` (SQL), then drops
the effect ids this process has already judged: contained in `started`,
or to another remote or branch. The checkout's facts (push URL, branch)
and the ancestry of a new merge are read only when something is left, so
a wake with nothing new runs no git, and each historic merge costs one
`is_ancestor` per process. Merges made before this lands are contained
and write nothing.

What is left are the due merges. A due merge that failed since the last
`serve_tick_s` wake is not tried on a wake from a ledger row; one that
failed at dependencies, schema or migrate is not tried again by this
process.

**Prepare.** Run while the kernel goes on scheduling:

1. **fetch**: `git fetch --no-tags --no-recurse-submodules
   --no-write-fetch-head <remote> +refs/heads/<branch>:refs/valor-kernel/rollout`
   in the checkout, through `git.run`, with the merge performer's
   credential for a non-local remote (a header file for that URL, as
   `Merge._with_credential` makes, `tools/push_branch.py:159-165`). Every
   due merge whose `sha` is not an ancestor of, or equal to, that ref (the
   branch was rewritten) gets `rollout.ended superseded`, the newest
   included. Of the rest, the one rolled is the one whose `sha` descends
   from every other's; when none does (diverged shas), the latest by row.
   The others it contains are covered by it.
2. **restart class**: the paths changed between `started` and `sha`
   (`git.diff_paths`, `core/git.py:537`). The kernel restarts unless every
   path is under `persona/`, `skills/` (read per turn), `docs/` or `tests/`
   (never read by the kernel), or is a top-level `*.md`. Everything else
   may be read at import or at start, so it restarts.
3. **dependencies** (restart class only): a changed `uv.lock` or
   `pyproject.toml` ends the rollout here, before the checkout moves, with
   a notice naming the merge. The kernel runs no `uv`; the lead's hand
   steps (`uv sync`, then the rollout) take it from there, and the restart
   they end in puts the merge in `started`.
4. **schema** (restart class only): a changed `core/schema.sql` ends the
   rollout the same way. The lead backs up, migrates and restarts by hand.
5. **fast-forward check**: the checkout's head must equal `sha`, descend
   from it (the lead moved past it), or be its ancestor with no
   uncommitted or untracked path (`git.dirty`) among the paths the merge
   changes from that head. Otherwise the rollout fails at `fast-forward`
   here, before any hold, so a checkout that keeps refusing never holds
   scheduling and never moves.

A rollout that is not restart class then fast-forwards at once and writes
`rollout.ended {effect_id, outcome: done, head, steps}`, and `rollout.ended
done` with `rolled_by` for each merge it covers: the persona and stage
files are read when a turn renders, so no job needs to end first.

A restart-class rollout sets `Kernel.restart_due`. From then on `schedule`
starts no new job. `intake.bind` and `notices.owe` go on. Once every job
but the background turn's has ended, the kernel runs prepare again, and
its result replaces `restart_due` outright: a failure or nothing due
clears it; a rollout that is not restart class relative to `started` (a
revert can remove `core/` paths) fast-forwards at once and clears it; a
restart-class one, possibly a newer merge that landed during the wait, is
applied. A prepare that finds no job running applies in the same wake.

**Apply.**

1. **cancel the background turn**, as `close()` does, and wait for it to
   end, before anything moves.
2. **fast-forward**: `git merge --ff-only <sha>` in the checkout. Never a
   force. A local change the merge does not touch stays. A checkout the
   lead already moved past `sha` makes this a no-op.
3. **migrate**: the injected `migrate(database)`, by default
   `<checkout>/.venv/bin/python -c "import sys; from core import db;
   db.migrate(sys.argv[1])" <database>`, started through the watch with
   `cwd=<checkout>`, so the merged `machine.VERDICTS` constraint and the
   merged seeds are applied by the merged code. `core/schema.sql` is
   unchanged here (step 4), and `db.migrate` is idempotent. It calls
   `db.migrate` alone, not `python -m core migrate`, which also runs
   `secure-login`. That interpreter is the one the kernel's own job runs
   (`core/serve.py:678`), so it carries no trust the kernel lacks.
   `<database>` is the database named by the kernel's own `dsn`, never
   `settings.database`, so a test kernel migrates its test database.
4. **restart**: append `rollout.restarting {effect_id, sha, from: started,
   steps, covers}`, `covers` naming each covered merge's effect, task and
   sha, then raise `rollout.Restart` out of `tick`. `serve`'s loop gains
   `except rollout.Restart: raise` ahead of its `except Exception`
   (`core/serve.py:613`), its `finally` settles services and closes the
   gateway, and `python -m core serve` exits with `kernel restarting for
   <sha>` and status 1. launchd starts the job again (`KeepAlive`). No
   `launchctl`, no label, no uid.

**Records.** Rows go on the merged task. `rollout.ended` (outcome `done`
or `superseded`) is the only terminal row. A failed step writes
`rollout.failed {effect_id, sha, step, reason, steps}` when its step or
reason differs from the latest `rollout.failed` for that effect (a read of
rows the kernel wrote), so a retry that fails the same way writes nothing.
The first failure requests one notice (`notices.request`, kind `rollout`,
about key `rollout:<effect_id>`, `core/notices.py:41`). A migrate failure
that leaves the checkout moved also requests one under
`rollout:<effect_id>:mixed`. A notice is information; nothing waits on
it. While the bridges are off (4.3), the rows and `kernel.log` are where a
failure shows.

**Retries.** A failure is not final. A failed step clears `restart_due`,
so scheduling resumes on the old code, and the merge stays due. It is
tried again on the next `serve_tick_s` wake, on the same `parked_at` reset
on which parked tasks are tried again (`core/serve.py:312-315`); a wake
from a ledger row before then does not retry it. This covers the lead's
`index.lock`, the lead's unpushed plan commits making the checkout diverge
until the lead rebases and pushes, a dirty file the merge changes, and a
network failure on the fetch. Three failures repeat the same way on every
try, so the process does not retry them: dependencies, schema and
migrate. Each is held in memory for that merge until the process
restarts; a newer kernel merge is a new due merge and is tried.

**After the restart.** `recover` gains one pass. Every kernel merge with a
`rollout.restarting` or `rollout.failed` row, or named in a restarting
row's `covers`, that has no `rollout.ended` and whose `sha` the running
commit contains gets `rollout.ended done` with `head: <running commit>`,
and with `rolled_by: <effect>` when another merge's restarting row covers
it, or `by: started` when no restarting row names it (the lead rolled it
by hand, or a later restart carried it). A restarting row whose `sha` the
running commit does not contain writes nothing, and its merge is due
again. A merge contained in `started` that never had a rollout row and
was never covered gets no row.

**Where it runs.** `Kernel.tick` (`core/serve.py:309-322`) runs the due
rollout after `notices.owe` and before `schedule`, one at a time, so two
rollouts never move the checkout at once. The kernel's advisory lock
(`core/serve.py:597`) already makes this the only kernel on the machine. A
merge released by the command line (`core/__main__.py:755`) or a bridge is
rolled out by the resident kernel on its next wake, since the outcome row
notifies (`core/schema.sql:125`). Any other failure inside the rollout is
logged and the wake goes on to `schedule`.

**Wiring.** `serve` takes `checkout: Path = ROOT`, `migrate` (default the
subprocess above) and `credential` (default `settings.github_keyfile`).
`Kernel` takes the same plus `started`; a `Kernel` built with no checkout
rolls nothing, so the existing tests that build one never touch a real
checkout. Tests pass a temporary checkout and a stand-in migrate and never
touch the real ledger.

**What a failure leaves.**

- Before apply step 2: the checkout where it was, the old process on old
  code.
- Apply steps 2 to 4 are the only window in which the old process runs
  from a moved checkout, and by then every job has ended, the background
  turn cancelled first. A lazy import in the old process in that window
  (for example `core.gateway` in `serve`) loads the merged module.
- A migrate failure: the kernel reads the head again. Only when it is
  still `sha` and the fast-forward moved it does the kernel run `git reset
  --keep <the head before the fast-forward>`, which refuses to touch a
  local change it would overwrite. `--keep` also unstages the lead's
  staged changes in files the merge does not touch (git's own table for
  `--keep`); their content stays in the working tree. If the head moved
  (the lead committed meanwhile) or git refuses, the checkout stays where
  it is, the kernel runs mixed code, and the failure row (`mixed: true`)
  and the `:mixed` notice say so.
- A merged commit that fails at import: launchd starts the job again at
  once, then about every ten seconds, `recover` never runs, and no
  `rollout.ended` row or notice is written. The signal is the 4.3 status
  page (the kernel is down) and `~/Library/Logs/valor/kernel.log`. No
  watcher is added (R3, dropped in `docs/plans/critique-issues-out.md`);
  the lead fixes it by hand.

## Governance

Nothing here judges work or can stop it. The merge has landed before any
step runs, and no step refuses or sends back a task. The hold delays a
task's next job only as the ordering of a restart, never by reading what
a task produced. The rollout is the tail of an effect that already exists,
as #3610 proposes. The kernel-project match and the restart class choose
which effect steps apply; the `dependencies` and `schema` stops end a
rollout, never a task; a failed step ends the attempt, never the work.
The hold has no timeout, retries reuse `serve_tick_s`, and the
per-process no-retry for dependencies, schema and migrate is a loop guard
on the rollout's own steps, not a limit on work. The notice and the
`rollout.*` rows are records. So the plan asks for no grant. If review
answers the governance boolean yes on a hunk, the incident is commit
`2d6ed8a8e` (a merged kernel change reached the running kernel only by a
hand rollout), the mission item is 1, and the hunk waits for Tom's tap.

## Threat model

The turn controls the merged commit's whole content (code, lockfile,
schema, paths) and its message. Running that code is the point of a
merge; the checks before the merge are the defence. The kernel must never:
roll out a commit other than the `sha` its own `effect.outcome` recorded,
or before that outcome is `done`; decide whether or where to roll out from
anything in the task's workspace or a turn's files (only the outcome row,
the checkout's own config and the diff between two commits it names); run
a program a turn can replace (no `uv`, no `pg_dump`; the migrate
interpreter is the kernel's own); move the checkout by anything but a
fast-forward, or a `reset --keep` back to where its own fast-forward
started while the head is still the commit it moved to; run git in the
checkout outside `git.run`, which refuses a hostile config
(`core/git.py:427-454`); or carry the GitHub credential into any call
other than the fetch from the merge's own remote.

## Done, as evidence

1. A test kernel with a temporary checkout cloned from a local bare
   remote, a merge task whose outcome lands a commit touching `core/`:
   `rollout.restarting` with steps fetch, restart class, fast-forward,
   migrate in that order, written before `tick` raises `Restart`; the
   checkout's head is the merged sha; the stand-in migrate was called with
   the test database; no job started on that wake.
2. The same with a commit touching only `skills/` and `docs/`: fetch and
   fast-forward only, `rollout.ended done`, no migrate, no `Restart`,
   scheduling never held.
3. `recover` after (1): `rollout.ended done` with the running commit.
4. A live run on the build Mac after this merges: the next kernel merge of
   a `core/` change is followed by `rollout.ended done` and a kernel whose
   `kernel recovered` log line follows the restart, with no hand step.
   Recorded under "Merged".
5. `docs/plans/valor-rebuild.md:98-101` and the docs below say what the
   code does. Suite green, ruff clean.

## Tests (`tests/test_rollout.py`, Postgres on `VALOR_TEST_DB`)

- The cases of Done 1 and 2, each asserting step order.
- Merge to another remote, or to another branch of the same remote: no
  row, checkout unmoved.
- A merge whose sha is an ancestor of `started`: no row, and a second
  wake runs no git for it. A checkout the lead already fast-forwarded
  past `started`: fast-forward is a no-op, the restart class is computed
  from `started`, so it still restarts.
- Two merges landed before a wake: one rollout to the newer sha, one
  `Restart`, the older in `covers`; after `recover` both have
  `rollout.ended done`, the older with `rolled_by`. A due merge whose sha
  is not on the fetched branch, the newest included:
  `rollout.ended superseded`. Of two due merges recorded newer sha first,
  the descendant is the one rolled.
- A merge with only a `rollout.failed` row whose sha the running commit
  contains (the lead rolled it by hand): `recover` writes
  `rollout.ended done` with `by: started`.
- A restart-class rollout with a harness job running: no new job starts,
  no fast-forward, no `Restart` until that job ends; then all of apply
  runs on the next wake. With a background turn running, apply cancels
  it before the fast-forward.
- A merge landed during the wait: the rollout that applies is to the
  newer sha. A re-prepare at the end of the wait that finds nothing due,
  or a newer merge that is not restart class, lifts the hold.
- A diff touching `uv.lock` or `pyproject.toml`: `rollout.failed` at
  dependencies, a notice, checkout unmoved, scheduling not held, and no
  retry on the next `serve_tick_s` wake. A diff touching neither runs no
  dependency step. A diff touching `core/schema.sql`: the same at schema.
- Checkout diverged (a local commit not on the target): `rollout.failed`
  at fast-forward, a notice, scheduling never held, no migrate. Across
  three `serve_tick_s` wakes it is tried each time, never holds, and
  writes no second failure row; a wake from a ledger row in between
  fetches nothing. After the local commit is rebased onto the merge, the
  retry restarts. A dirty file the merge changes: refused the same way.
  A dirty file it does not change: rolled, the file kept.
- Migrate fails (the stand-in raises): `failed` at migrate, the checkout
  back at the head before the fast-forward, no `Restart`, no retry by
  this process. With a local change in a file the merge touched, `reset
  --keep` refuses: head stays on the merged sha, the row says mixed, and
  a `:mixed` notice is requested even after an earlier failure's notice.
  With a commit made after the fast-forward: no reset, a mixed row.
- The default migrate's argv and cwd (asserted on a stand-in start, never
  run against a real database).
- `serve` with a stand-in kernel whose `tick` raises `Restart`: the loop
  ends, `kernel.close()` and the gateway close run, and the exception
  leaves `serve`. A plain exception from `tick` is still logged and the
  loop goes on.
- Every step runs off the loop: with a stand-in fetch that blocks, a
  concurrent coroutine on the loop still runs.
- A merge settled by `reconcile` (outcome from `lookup`) rolls out; a
  merge outcome `failed` does not.
- `recover` with `rollout.restarting` and the running commit not
  descending from its sha: no row, and the merge is due again.
- A hostile key in the checkout's config (`core.hooksPath`): `failed` at
  fetch, nothing run. (The lead's checkout config passes `git.hostile`
  today: it carries only `core.*`, `remote.origin.*`, `branch.*` and
  `rerere.enabled`.)
- The fetch carries the credential only for a non-local remote (header
  file used once, for that URL).
- `python -m core serve` exits with status 1 and the restart line on
  `Restart`.

## Absorbs

- `docs/plans/valor-rebuild.md:98-101` says "a separate checkout" and
  "the same tapped effect"; neither is true (the checkout is the lead's;
  merges are Valor's call, `docs/plans/valor-rebuild-feedback.md:33`).

## Leaves out

- A merge that changes `uv.lock` or `pyproject.toml`: the kernel stops
  before the fast-forward and the lead runs `uv sync` and the rollout by
  hand. The kernel runs no `uv` (`core/binaries.py:1-11`).
- A merge that changes `core/schema.sql`: the kernel stops before the
  fast-forward and the lead backs up, migrates and restarts by hand. The
  kernel runs no `pg_dump` (the same rule).
- The bridges' and routines' launchd jobs (`bridges/telegram/__main__.py:13`,
  `bridges/email/__main__.py:15`, `bridges/local/__main__.py:14`,
  `core/routines.py:38`). They run from the same checkout, but each is
  its own job, live only in test windows; a merge touching `bridges/` or
  `routines/` still has its rollout steps run by the lead. The kernel
  imports `routines/emulator` itself, so such a merge is restart class
  for the kernel all the same.
- Installing or reloading any plist. A change to the kernel's plist
  itself needs a `bootout` and `bootstrap`, which a restart does not do.
- Merges the lead makes by hand in phase A: no merge effect, no rollout.
  The lead keeps the steps in `SKILL.md:224-228` for those.
- Commits that reach the branch without a merge outcome in this Mac's
  ledger: Tom's pushes, and merges made by the kernel on Tom's Mac (each
  Mac has its own ledger, SKILL §1). They reach the running kernel with
  the next kernel merge on this Mac, whose diff is taken from `started`,
  or by a hand step. Rolling whenever origin's tip moves would need a
  credentialed fetch on every wake and would roll unchecked pushes, which
  #3610 does not ask for.
- On Tom's Mac the same code fast-forwards Tom's working checkout; his
  unpushed commits make every try fail at fast-forward until he pushes or
  rebases (one failure row and one notice per merge).
- A watch or revert after a merge, including a merged commit that fails
  at import (R3, dropped in `docs/plans/critique-issues-out.md`).
- Showing rollout rows in `tasks.status`: the issue asks for the rollout
  to be ledgered, which the rows do.

## Files it changes

`core/rollout.py` (new), `core/serve.py` (`Kernel`, `tick`, `schedule`,
`recover`, `serve`), `core/__main__.py` (`serve` exits with status 1 on
`Restart`), `tests/test_rollout.py` (new), `tests/test_serve.py` (the loop
and `Restart`), `core/README.md`, `docs/architecture.md` (restart and
rollout), `docs/data.md` (the `rollout.*` rows),
`docs/plans/valor-rebuild.md:98-101`, `.claude/skills/build/SKILL.md` (the
merge bullet: a kernel-released merge rolls itself out, except a
dependency or schema change).

## Rollout (the last one by hand)

On Valor's Mac, from `~/src/valor-rebuild`, after the merge is
fast-forwarded:

1. `.venv/bin/python -m core backup`.
2. No dependency or schema change, so neither `uv sync` nor `migrate` is
   needed.
3. Restart the kernel so it runs the merged code (`launchctl kickstart -k
   gui/$(id -u)/com.valor.kernel`). The plist is unchanged.
4. Check `kernel recovered` in `~/Library/Logs/valor/kernel.log`.
5. Done 4 waits on the next kernel merge; record it under "Merged".

## Questions for Tom

None. Whether to wait for a live turn before restarting, and whether the
bridges and routines roll the same way, are technical and scope calls,
decided below.

## Decisions and records

- Planned 2026-10-08 by `plan-3610` at base `2d6ed8a8e`.

### Decided by default

- **A restart waits for the running jobs.** A restart-class rollout holds
  new jobs and restarts once every job but the background turn has ended,
  so a foreground turn is never paid for twice. The wait is at most the
  running jobs, with no timeout. The background turn is cancelled at the
  start of apply, as a foreground step preempts it today. This is not the drain R4 dropped
  (`docs/plans/critique-issues-out.md:289`): it reads no status field,
  only the kernel's own `jobs`, and a restart that cuts a job off is
  still safe through `recover`.
- **Bridges and routines are not rolled by the kernel.** They stay the
  lead's steps (Leaves out).
- **A dependency or schema change is the lead's.** The kernel runs no
  `uv` and no `pg_dump`.

### Critique round 1 (`critic-3610-r1`, verdict revise)

1. `uv` breaks the binary rule. Resolved as recommended: no
   `settings.uv`, `VALOR_UV` or `PATH` change; a diff touching `uv.lock`
   or `pyproject.toml` ends the rollout at a dependencies step before the
   fast-forward, with a notice; listed under Leaves out.
2. The test seam would migrate the real ledger. Resolved: `migrate` is
   injected, both it and `backup.dump` take the database from the
   kernel's own `dsn`, and a test asserts the database argument.
3. A blocking step would stall the gateway. Resolved: every step runs
   through `git.threaded`; a test proves the loop keeps running.
   (Corrected in round 2: this first said cancelling kills what every
   step started, which held only for git; the backup is gone and the
   migrate now starts through the watch.)
4. Restart by exiting. Resolved: `tick` raises `Restart` after
   `rollout.restarting`, `serve`'s `finally` runs, `python -m core serve`
   exits 1, `KeepAlive` restarts it. No `launchctl`. Tests cover a
   restart that starts no job and leaves the loop.
5. Wait for the running job. Resolved, with one change: the kernel holds
   scheduling as soon as a restart-class rollout is prepared and runs
   backup, fast-forward and migrate only after the jobs end, rather than
   migrating first and then waiting. Reason: the old process then never
   runs against a migrated schema or a moved checkout while turns are
   live, which also shrinks finding 8's window. (Corrected in round 2:
   this first said so of every turn, but the background turn ran through
   apply until round 2's finding 3 cancelled it at apply's start.) It waits for every job but
   the background turn, not only the harness slot: kernel-owned steps and
   performs are short, nothing new starts, and a merge perform cut off
   mid-push would only add a `reconcile`. Q1 and Q2 deleted and recorded
   under Decided by default.
6. A failed rollout is final. Resolved: due is the newest kernel merge
   not contained in `started` and with no `rollout.ended`; `rollout.ended`
   is written only on done or superseded; failures retry on the
   `serve_tick_s` wake, as parked tasks do, with a failure row only when
   the step or reason changes and one notice per effect. Two failures
   that repeat on every try (dependencies, migrate) are not retried by the
   same process; a newer merge or a restart tries again.
7. Older merges scanned on every wake. Resolved: an in-memory set of
   contained effect ids, rebuilt once per process; only the newest due
   merge rolls, older ones get `rollout.ended done` with `rolled_by`.
8. Mixed versions. Resolved: the window is steps 5 to 7 after the jobs
   have ended, stated plainly; a migrate failure runs `git reset --keep`
   to the head before the fast-forward, and if git refuses, the row and
   notice say the kernel runs mixed code.
9. Crash loop. Resolved: listed under "What a failure leaves" with the
   status page and `kernel.log` as the signal; no watcher.
10. The hand rollout's plist step. Resolved: Rollout step 3 exports the
    four operator variables and `VALOR_BACKUP_DIR` from the installed
    plists and diffs before `bootout` and `bootstrap`; the U+F028 citation
    is corrected.
11. The trigger's blind spots. Resolved: both are under Leaves out.
12. (a) Done 1 no longer asserts a uv row; tests assert no dependency step
    without a lockfile change and a stop with one. (b) Checked:
    `git.hostile` on `~/src/valor-rebuild` returns nothing today; noted in
    the test. (c) Restart class kept as is. (d) The `tasks.status` change
    is dropped and listed under Leaves out.

### Critique round 2 (`critic-3610-r2`, verdict revise)

Both critique rounds are spent. The lead accepted all twelve findings,
folded as the report's fixes describe, and sent the plan to build with no
third critique. Reason: each fix is concrete and local, the critic said so
("concrete enough to apply without a third round"), and the five that
must be fixed narrow the kernel's reach (no backup, no hold on a
refusing checkout, no reset over the lead's commit) rather than add any.

1. The in-kernel backup ran Homebrew's `pg_dump`, a program a turn can
   replace. Resolved as recommended: no backup in the kernel. A diff
   touching `core/schema.sql` stops at a `schema` step and goes to the
   lead; listed under Leaves out. `PLIST_ENV` is unchanged, and the hand
   rollout no longer regenerates or reloads the plist.
2. A refusing fast-forward repeated the hold and a full dump every
   minute. Resolved: prepare checks that the fast-forward can succeed
   (ancestry, and no dirty path among those the merge changes) before it
   holds; a failure there is `fast-forward` and never holds. Test: three
   tick wakes on a diverged checkout, no hold, one failure row.
3. The background turn ran through apply. Resolved: apply cancels it and
   waits for it to end before the fast-forward; the test asserts the
   order.
4. `reset --keep` could drop the lead's commit and unstages the lead's
   index. Resolved: reset only when HEAD is still the merged sha and the
   fast-forward moved it; otherwise a mixed row. The unstaging is stated
   in "What a failure leaves". Test with a commit after the fast-forward.
5. Who closes older merges was undefined. Resolved: one pass in `recover`
   closes every kernel merge with a rollout row, or named in a restarting
   row's `covers`, that the running commit contains: `rolled_by` when a
   restarting row covers it, else `by: started`. The restarting row's
   `covers` is the builder's addition: without it an older merge with no
   rollout row of its own is never closed, which Done's two-merges test
   requires. Merges contained in `started` with no row and no cover get
   none. Both cases tested.
6. A due merge that falls off the branch, and "newest". Resolved: any due
   merge not on the fetched ref is superseded; the one rolled descends
   from every other due sha, else the latest by row.
7. The re-prepare at the end of the wait. Resolved: its result replaces
   `restart_due` outright; nothing due or not restart class lifts the
   hold. Tested.
8. `serve` swallows every `Exception`. Resolved: `except rollout.Restart:
   raise` ahead of `except Exception`.
9. Cancelling kills only git. Resolved: the default migrate starts through
   the watch with `cwd=<checkout>`; argv and cwd asserted.
10. Git on every wake. Resolved: merge outcomes are read by SQL first,
    judged effect ids (contained, or another remote or branch) are
    dropped, and the checkout's facts are read only when something is
    left.
11. The mixed-code notice could be deduped away. Resolved: about key
    `rollout:<effect_id>:mixed`.
12. Governance wording. Resolved: "delays a task's next job only as the
    ordering of a restart, never by reading what a task produced", with
    the rest of the critic's substance.

The two overstated round 1 claims (#3, #5) are corrected in place above.


## Build record

Built on `i3610-kernel-rollout` from the plan and both critique rounds.

- `core/rollout.py`: `judge`, `fetch`, `prepare`, `refused`,
  `fast_forward`, `step_back`, `migrate_argv`, `migrate`, `Restart`.
- `core/serve.py`: `Kernel.roll`, `_apply`, `_failed`, `_ended`, the hold
  in `schedule`, the `waiting` reset on the `serve_tick_s` wake, the
  `rolled` pass in `recover`, and `serve`'s `checkout`, `migrate`,
  `credential` and `except rollout.Restart: raise`.
  `core/__main__.py`: `serve` exits with `kernel restarting for <sha>`.
- `tests/test_rollout.py` holds every test the Tests section names;
  `tests/test_serve.py` holds the loop's `Restart` and failed-wake test.
  `tests/kernel_child.py` passes `checkout=None`, so the kill tests'
  kernels never roll the build's checkout.
- Docs: `core/README.md`, `docs/architecture.md`, `docs/data.md` (the
  three `rollout.*` rows), `docs/plans/valor-rebuild.md` (the takeover
  point), `.claude/skills/build/SKILL.md` (the merge bullet).
- A judge step refused by the checkout's config fails at `fetch` on the
  newest merge to the checkout's origin URL and branch (patch round 1); a
  failure with `mixed` always writes its row.
- Suite without `tests/test_container.py`: 1804 passed, 26 skipped, 3
  failed. The three (two in `test_pi.py`, one in `test_workspace.py`)
  pass with `--basetemp` outside `~/src`, which their sandbox profile
  treats as the checkout.

### Patch round 1

From the blind review (`review-3610`), all four findings accepted by the
lead, on top of the docs check's commit.

- The dependency and schema stops classify the diff from `started` to the
  commit the restart runs (`rollout.runs_at`: the checkout's head when it
  is at or past the merged sha, else the sha), and count the checkout's
  uncommitted paths among `uv.lock`, `pyproject.toml` and
  `core/schema.sql`. Uncommitted paths only stop a restart; a rollout with
  no restart is unaffected. Tests: a schema and a `uv.lock` change beyond
  the sha; an uncommitted `core/schema.sql` and `pyproject.toml`; a dirty
  `core/schema.sql` under a docs-only merge still rolls.
- A refused config fails the newest merge to the checkout's
  `remote.origin.url` and branch, read by `git.origin` from the checkout's
  files with no hostile check (`config --get` and `symbolic-ref` run
  nothing). With no such merge, or no origin URL, it is logged and no row
  is written. Tests: the failure lands on the kernel's merge, not another
  project's newer one; another project's merge alone gets no row.
- Every `serve.serve` call in `tests/test_serve.py` passes `checkout=None`
  (five calls; the review named four, the fifth is the failed-wake test).
- Docs: `docs/data.md` says `steps` is only on a no-restart target's
  `rollout.ended`; `docs/architecture.md` and `core/README.md` name the
  diff the stops read and the uncommitted paths. The cherry-picked docs
  commit already says a failed migrate is not retried.
- `test_rollout`, `test_serve`, `test_broker`: 80 passed. Six of the seven
  new cases fail on `081981b5d`; the docs-only roll passes there too.

### Patch round 2

From the lead, after `review-3610-p1` passed on `4bc39d828`.

- `3bf51bf82`: a refused config with no merge of the kernel's own puts the
  wake's merges back in `waiting`, so a wake with nothing new runs no git;
  they are tried again on the `serve_tick_s` wake. The test ticks twice and
  asserts `rollout.judge` ran once; it fails without the fix.

## Merged

- Checks:
  - **Test: `pass`** on `081981b5d`. Its 17 probes also pass at `3bf51bf82`.
  - **Review: `changes`**, four findings, all taken in patch round 1;
    the re-review `review-3610-p1` gave **`pass`** on `4bc39d828`.
  - **Docs: `updated`.** `f6c7e8980`, cherry-picked as `7b6505577`.
- Patches `4bc39d828` and `3bf51bf82`. `test_rollout`, `test_serve` and
  `test_broker` pass at `3bf51bf82`: 80 tests.
- Known limits, not changed:
  - a checkout whose origin has a `pushurl` is always log-only, which
    fails safe;
  - git quotes non-ASCII paths, so classification errs toward a restart,
    and a dirty non-ASCII file falls back to the ff-only failure and a
    retry.
- Backup `valor_rebuild-20261009T054048Z.dump` was taken before the merge.
