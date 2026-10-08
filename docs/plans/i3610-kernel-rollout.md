---
tracking: none
slug: i3610-kernel-rollout
type: plan
status: planned
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
- The kernel's launchd job cannot back up today: `PLIST_ENV`
  (`core/serve.py:640-661`) carries no `VALOR_BACKUP_DIR`, and the default
  (`core/settings.py:317`) names a volume called U+F028, which is not
  mounted on this Mac. The installed `com.valor.backup.plist` names
  `/Volumes/PINK/valor_temp`.
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
- `db.migrate` and `backup.dump` default to `settings.database`
  (`core/db.py:45`, `core/backup.py:134-136`).
- Rollout rows need no schema change: the fold ignores a row type it does
  not know (`core/machine.py:561-563`, falling through to `return None`),
  and `events.type` is free text (`core/schema.sql:19`).

## Design

One new module, `core/rollout.py`, called from the resident kernel's wake.
Every git call, the backup and the migrate run off the loop, through
`git.threaded` (`core/git.py:366`), so a slow step never stalls the
in-process gateway, and cancelling the loop kills what the step started.

**Which merges.** A merge is the kernel's own when its `effect.outcome`
(kind `done`, action type `merge`) names a `remote` equal, as a string, to
the push URL of the kernel's checkout (`git.push_url(checkout)`,
`core/git.py:590`), and a `branch` equal to the checkout's branch
(`git.branch`). Both are read from the checkout once per wake. The facts
come from the outcome the kernel wrote and from the checkout's own config,
never from the task's workspace or anything a turn wrote.
`projects/valor.toml` is not consulted: the checkout is the thing being
rolled, so its own remote and branch are the definition. A merge to
another remote or branch is not the kernel's and writes nothing.

**Which one is due.** `started` is the commit the process started from,
`git.head(checkout)` read once in `serve` before `recover`. The due merge
is the newest of the kernel's own merges that has no `rollout.ended` row
and whose `sha` is not an ancestor of, or equal to, `started`. The kernel
keeps an in-memory set of effect ids it has judged contained in
`started`, filled as it judges them, so each historic merge costs one
`is_ancestor` per process, not one per wake. Merges made before this
lands are contained and write nothing. Only the newest due merge is
rolled: one restart covers every older one.

**What a rollout does.** Two phases. Prepare runs while the kernel goes
on scheduling; apply runs once the jobs have ended.

Prepare:

1. **fetch**: `git fetch --no-tags --no-recurse-submodules
   --no-write-fetch-head <remote> +refs/heads/<branch>:refs/valor-kernel/rollout`
   in the checkout, through `git.run` with the merge performer's
   credential for a non-local remote (the same `_with_credential` the merge
   used, `tools/push_branch.py:159-165`). The `sha` must be an ancestor of,
   or equal to, that ref (`git.is_ancestor`), as `git.holds` does
   (`core/git.py:667-697`). An older due merge whose `sha` is not on the
   ref (the branch was rewritten) gets `rollout.ended superseded`.
2. **restart class**: the paths changed between `started` and `sha`
   (`git.diff_paths`, `core/git.py:537`). The kernel restarts unless every
   path is under `persona/`, `skills/` (read per turn), `docs/` or `tests/`
   (never read by the kernel), or is a top-level `*.md`. Everything else may
   be read at import or at start, so it restarts.
3. **dependencies** (restart class only): a changed `uv.lock` or
   `pyproject.toml` ends the rollout here, before the checkout moves, with
   a notice naming the merge. The kernel runs no `uv`; the lead's hand
   steps (`uv sync`, then the rollout) take it from there, and the restart
   they end in puts the merge in `started`.

A rollout that is not restart class then fast-forwards (step 5 below) at
once and writes `rollout.ended {effect_id, outcome: done, head, steps}`:
the persona and stage files are read when a turn renders, so no job needs
to end first.

A restart-class rollout sets `Kernel.restart_due`. From then on `schedule`
starts no new job. `intake.bind` and `notices.owe` go on. When every job
has ended except the background turn's (a background turn is preempted by
a restart, as today), the kernel runs prepare again, so a merge that
landed during the wait is the one rolled, and then apply:

4. **backup**: `backup.dump(database=<the kernel's database>)`, before the
   checkout moves. Source for backup first: `SKILL.md:226-227`.
5. **fast-forward**: `git merge --ff-only <sha>` in the checkout. Never a
   force: a checkout that diverged or holds a local change the merge would
   overwrite is git's refusal, recorded as the step's failure. A local
   change the merge does not touch stays. A checkout the lead already
   moved past `sha` makes this a no-op.
6. **migrate**: the injected `migrate(database)`, by default
   `<checkout>/.venv/bin/python -m core migrate --db <database>` as a
   subprocess, so the merged schema and the merged `machine.VERDICTS` are
   applied by the merged code. That interpreter is the one the kernel's own
   job runs (`core/serve.py:678`), so it carries no trust the kernel lacks.
   `<database>` is the database named by the kernel's own `dsn`, never
   `settings.database`, so a test kernel migrates and backs up its test
   database. `db.migrate` is idempotent (`core/schema.sql` uses
   `IF NOT EXISTS`, and `DROP ... IF EXISTS` before each trigger).
7. **restart**: append `rollout.restarting {effect_id, sha, from: started,
   steps}`, then raise `rollout.Restart` out of `tick`. `serve`'s loop lets
   it through (its `except Exception` re-raises it), its `finally` cancels
   the background turn, settles services and closes the gateway, and
   `python -m core serve` prints `kernel restarting for <sha>` and exits
   with status 1. launchd starts the job again (`KeepAlive`). No
   `launchctl`, no label, no uid.

**Records.** Rows go on the merged task. `rollout.ended` (outcome `done`
or `superseded`) is the only terminal row. A merge contained in the rolled
`sha` that was older than the due one gets `rollout.ended done` with
`rolled_by: <effect_id>`. A failed step writes `rollout.failed {effect_id,
sha, step, reason, steps}` when its step or reason differs from the
latest `rollout.failed` for that effect (a read of rows the kernel wrote),
so a retry that fails the same way writes nothing. The first failure
requests one notice (`notices.request`, kind `rollout`, about key
`rollout:<effect_id>`, `core/notices.py:41`), which the about key keeps to
one per effect. The notice is information; nothing waits on it. While the
bridges are off (4.3), the rows and `kernel.log` are where a failure
shows.

**Retries.** A failure is not final. A failed step clears
`restart_due`, so scheduling resumes on the old code, and the merge stays
due. The kernel tries it again on the next `serve_tick_s` wake, the same
wake on which parked tasks are tried again (`core/serve.py:313-316`); a
wake from a ledger row before then does not retry it. This covers the
lead's `index.lock`, the lead's unpushed plan commits making the checkout
diverge until the lead rebases and pushes, the backup disk not mounted,
and a network failure on the fetch. Two failures repeat the same way on
every try, so the process does not retry them: a dependency change (step
3), and a migrate failure (step 6). Each is held in memory for that `sha`
until the process restarts; a newer kernel merge is a new due merge and is
tried.

**After the restart.** `recover` gains one pass: each `rollout.restarting`
with no `rollout.ended` for its effect gets `rollout.ended done` with the
running commit when that commit is the restarting row's `sha` or descends
from it. Otherwise it writes nothing, and the merge is due again.

**Where it runs.** `Kernel.tick` (`core/serve.py:309-322`) runs the due
rollout after `notices.owe` and before `schedule`, one at a time, so two
rollouts never move the checkout at once. The kernel's advisory lock
(`core/serve.py:597`) already makes this the only kernel on the machine. A
merge released by the command line (`core/__main__.py:755`) or a bridge is
rolled out by the resident kernel on its next wake, since the outcome row
notifies (`core/schema.sql:125`).

**Wiring.** `Kernel` and `serve` take `checkout: Path = ROOT` and
`migrate: Callable[[str], None]` (default the subprocess above), so tests
pass a temporary checkout and a stand-in migrate and never touch the real
ledger. `PLIST_ENV` gains `VALOR_BACKUP_DIR` and `VALOR_BACKUP_KEEP`
(passthrough, as in `backup.PLIST_ENV`).

**What a failure leaves.**

- Before step 5: the checkout where it was, the old process on old code.
- Steps 5 to 7 are the only window in which the old process runs from a
  moved checkout, and by then every job but the background turn has
  ended. A lazy import in the old process in that window (for example
  `core.gateway` in `serve`) loads the merged module, and
  `runs.kernel_commit()` records the merged commit on any turn that
  renders.
- A migrate failure: the kernel runs `git reset --keep <the head before
  step 5>`, which moves the checkout back and refuses to touch a local
  change it would overwrite, so it is never a forced reset of the lead's
  work. The old process then runs old code again. If git refuses, the
  checkout stays on the merged commit, the kernel runs mixed code, and the
  failure row and the notice say so.
- A merged commit that fails at import: launchd starts the job about every
  ten seconds, `recover` never runs, and no `rollout.ended` row or notice
  is written. The signal is the 4.3 status page (the kernel is down) and
  `~/Library/Logs/valor/kernel.log`. No watcher is added (R3, dropped in
  `docs/plans/critique-issues-out.md`); the lead fixes it by hand.

## Governance

Nothing here judges work or can stop it. The merge has landed before any
step runs, and no step refuses, delays or sends back a task: the rollout
is the tail of an effect that already exists, as #3610 proposes. The
kernel-project match and the restart class choose which effect steps
apply; a failed step ends the attempt, never the work. Holding new jobs
until the running ones end is the ordering of a restart, not a judgement
of any task: it reads nothing a task produced, and every task's next step
starts in the restarted process. The notice and the `rollout.*` rows are
records. So the plan asks for no grant. If review answers the governance
boolean yes on a hunk, the incident is commit `2d6ed8a8e` (a merged kernel
change reached the running kernel only by a hand rollout), the mission
item is 1, and the hunk waits for Tom's tap.

## Threat model

The turn controls the merged commit's whole content (code, lockfile,
schema, paths) and its message. Running that code is the point of a
merge; the checks before the merge are the defence. The kernel must never:
roll out a commit other than the `sha` its own `effect.outcome` recorded,
or before that outcome is `done`; decide whether or where to roll out from
anything in the task's workspace or a turn's files (only the outcome row,
the checkout's own config and the diff between two commits it names); run
a program a turn can replace (no `uv`; the migrate interpreter is the
kernel's own); move the checkout by anything but a fast-forward, or a
`reset --keep` back to where its own fast-forward started; run git in the
checkout outside `git.run`, which refuses a hostile config
(`core/git.py:427-454`); or carry the GitHub credential into any call
other than the fetch from the merge's own remote.

## Done, as evidence

1. A test kernel with a temporary checkout cloned from a local bare
   remote, a merge task whose outcome lands a commit touching `core/`:
   `rollout.restarting` with steps fetch, restart class, backup,
   fast-forward, migrate in that order, written before `tick` raises
   `Restart`; the checkout's head is the merged sha; the stand-in migrate
   and the backup were called with the test database; no job started on
   that wake.
2. The same with a commit touching only `skills/` and `docs/`: fetch and
   fast-forward only, `rollout.ended done`, no backup, no migrate, no
   `Restart`, scheduling never held.
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
  wake runs no `is_ancestor` for it (the in-memory set). A checkout the
  lead already fast-forwarded past `started`: fast-forward is a no-op, the
  restart class is computed from `started`, so it still restarts.
- Two merges landed before a wake: one rollout to the newer sha, one
  `Restart`; after `recover` both have `rollout.ended done`, the older with
  `rolled_by`. An older merge whose sha is not on the fetched branch:
  `rollout.ended superseded`.
- A restart-class rollout with a harness job running: no new job starts,
  no backup, no fast-forward, no `Restart` until that job ends; then all
  of apply runs on the next wake. With only a background turn running,
  apply runs at once and the turn is cancelled by `serve`'s `finally`.
- A merge landed during the wait: the rollout that applies is to the
  newer sha.
- A diff touching `uv.lock` or `pyproject.toml`: `rollout.failed` at
  dependencies, a notice, checkout unmoved, scheduling not held, and no
  retry on the next `serve_tick_s` wake. A diff touching neither runs no
  dependency step.
- Checkout diverged (a local commit not on the target): `rollout.failed`
  at fast-forward, a notice, no migrate, scheduling resumed. On the next
  `serve_tick_s` wake it is tried again and writes no second failure row;
  after the local commit is rebased onto the merge, the retry restarts.
  A dirty file the merge changes: refused the same way. A dirty file it
  does not change: rolled, the file kept.
- Backup fails (the backup directory missing): `failed` at backup,
  checkout unmoved; the next tick's wake from a ledger row does not retry,
  the next `serve_tick_s` wake does; mounting the directory then rolls it.
- Migrate fails (the stand-in raises): `failed` at migrate, the checkout
  back at the head before the fast-forward (`reset --keep`), no
  `Restart`, no retry by this process. With a local change in a file the
  merge touched, `reset --keep` refuses: head stays on the merged sha and
  the failure row says the kernel runs mixed code.
- The default migrate's argv is `<checkout>/.venv/bin/python -m core
  migrate --db <test database>` (asserted on the argv built, never run),
  and the backup is asked for the test database.
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
- `plist` carries `VALOR_BACKUP_DIR` and `VALOR_BACKUP_KEEP` when set.

## Absorbs

- The kernel's launchd job could not run `backup`: `PLIST_ENV`.
- `docs/plans/valor-rebuild.md:98-101` says "a separate checkout" and
  "the same tapped effect"; neither is true (the checkout is the lead's;
  merges are Valor's call, `docs/plans/valor-rebuild-feedback.md:33`).

## Leaves out

- A merge that changes `uv.lock` or `pyproject.toml`: the kernel stops
  before the fast-forward and the lead runs `uv sync` and the rollout by
  hand. The kernel runs no `uv` (`core/binaries.py:1-11`).
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
`recover`, `serve`, `PLIST_ENV`), `core/__main__.py` (`serve` exits with
status 1 on `Restart`), `tests/test_rollout.py` (new),
`tests/test_serve.py` (the loop and `Restart`), `core/README.md`,
`docs/architecture.md` (restart and rollout), `docs/data.md` (the
`rollout.*` rows), `docs/plans/valor-rebuild.md:98-101`,
`.claude/skills/build/SKILL.md` (the merge bullet: a kernel-released merge
rolls itself out, except a dependency change).

## Rollout (the last one by hand)

On Valor's Mac, from `~/src/valor-rebuild`, after the merge is
fast-forwarded:

1. `.venv/bin/python -m core backup`.
2. No dependency or schema change, so neither `uv sync` nor `migrate` is
   needed.
3. Regenerate the kernel plist with the live environment kept: export
   `VALOR_EMAIL_ADDRESS`, `VALOR_OPERATOR_CHAT`, `VALOR_OPERATOR_EMAIL` and
   `VALOR_OPERATOR_TELEGRAM_ID` from the installed
   `~/Library/LaunchAgents/com.valor.kernel.plist`, and `VALOR_BACKUP_DIR`
   (`/Volumes/PINK/valor_temp`) from the installed
   `com.valor.backup.plist` (read each with `plutil -extract
   EnvironmentVariables.<name> raw`), then write
   `python -m core serve --plist` to a scratch file, `diff` it against the
   installed plist (the only change is the added `VALOR_BACKUP_DIR`), copy
   it into place, and `launchctl bootout` and `bootstrap` the job, since
   its environment changed.
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
  running jobs, with no timeout. A background turn is preempted, as any
  restart does today. This is not the drain R4 dropped
  (`docs/plans/critique-issues-out.md:289`): it reads no status field,
  only the kernel's own `jobs`, and a restart that cuts a job off is
  still safe through `recover`.
- **Bridges and routines are not rolled by the kernel.** They stay the
  lead's steps (Leaves out).
- **A dependency change is the lead's.** The kernel runs no `uv`.

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
4. Restart by exiting. Resolved: `tick` raises `Restart` after
   `rollout.restarting`, `serve`'s `finally` runs, `python -m core serve`
   exits 1, `KeepAlive` restarts it. No `launchctl`. Tests cover a
   restart that starts no job and leaves the loop.
5. Wait for the running job. Resolved, with one change: the kernel holds
   scheduling as soon as a restart-class rollout is prepared and runs
   backup, fast-forward and migrate only after the jobs end, rather than
   migrating first and then waiting. Reason: the old process then never
   runs against a migrated schema or a moved checkout while turns are
   live, which also shrinks finding 8's window. It waits for every job but
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
