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
  (`core/settings.py:317`) names no volume. It also has no `uv`: the job's
  `PATH` (`core/serve.py:666-671`) is the system directories plus Claude's,
  git's and Postgres's, and `uv` lives in `/opt/homebrew/bin`, which only
  a workspace's `PATH` names (`core/workspace.py:829`).
- Rollout rows need no schema change: the fold ignores a row type it does
  not know (`core/machine.py:561-563`, falling through to `return None`),
  and `events.type` is free text (`core/schema.sql:19`).

## Design

One new module, `core/rollout.py`, called from the resident kernel's wake.

**Which merges.** A merge is the kernel's own when its `effect.outcome`
(kind `done`, action type `merge`) names a `remote` equal, as a string, to
the push URL of the kernel's checkout (`git.push_url(checkout)`,
`core/git.py:590`). Its `branch` must equal the checkout's branch
(`git.branch`). The facts come from the outcome the kernel wrote and from
the checkout's own config, never from the task's workspace or anything a
turn wrote. `projects/valor.toml` is not consulted: the checkout is the
thing being rolled, so its own remote and branch are the definition.

**Which are due.** `rollout.due(conn, checkout, started)` returns, oldest
outcome first, each such merge with no `rollout.ended` row for its effect
id, whose `sha` is not already an ancestor of (or equal to) `started`, the
commit the running process started from (`runs.kernel_commit()` read once
in `serve` before `recover`). A merge the running code already contains
writes nothing, so merges made before this lands are never touched.

**What a rollout does**, each step one `rollout.step` row
`{effect_id, step, outcome, detail}` on the merged task, in this order, a
failed step ending the rollout:

1. **fetch**: `git fetch --no-tags --no-recurse-submodules
   --no-write-fetch-head <remote> +refs/heads/<branch>:refs/valor-kernel/rollout`
   in the checkout, through `git.run` with the merge performer's
   credential for a non-local remote (the same `_with_credential` the merge
   used, `tools/push_branch.py:159-165`). The commit must then be an
   ancestor of, or equal to, that ref (`git.is_ancestor`), as `git.holds`
   does (`core/git.py:667-697`).
2. **restart class**: the paths changed between `started` and `sha`
   (`git.diff_paths`, `core/git.py:537`). The kernel restarts unless every
   path is under `persona/`, `skills/` (read per turn), `docs/` or `tests/`
   (never read by the kernel), or is a top-level `*.md`. Everything else may
   be read at import or at start, so it restarts.
3. **backup** (restart class only): `backup.dump()` in a thread, before
   the checkout moves. Source for backup first: `SKILL.md:226-227`.
4. **fast-forward**: `git merge --ff-only <sha>` in the checkout. Never a
   reset, never force: a checkout that diverged or holds a local change the
   merge would overwrite is git's refusal, recorded as the step's failure.
   A local change the merge does not touch stays.
5. **uv sync** (restart class only): `<settings.uv> sync --frozen` in the
   checkout, so the restarted process imports what the new lockfile names.
6. **migrate** (restart class only): `<checkout>/.venv/bin/python -m core
   migrate` as a subprocess, so the merged schema and the merged
   `machine.VERDICTS` are applied by the merged code. `db.migrate` is
   idempotent (`core/schema.sql` uses `IF NOT EXISTS` and
   `DROP ... IF EXISTS` before each trigger).
7. **restart** (restart class only): append `rollout.restarting
   {effect_id, sha}`, then run `launchctl kickstart -k
   gui/<uid>/com.valor.kernel` in a new session (`start_new_session=True`,
   so launchd's kill of the job's process group does not take the client
   with it). Success kills this process; launchd starts the job again
   (`KeepAlive`, `core/serve.py:681`). A non-zero exit (no such job) is the
   step's failure.

A rollout that is not restart class ends after step 4 with
`rollout.ended {effect_id, outcome: done, head}`. A failed step writes
`rollout.ended {effect_id, outcome: failed, step, reason, head}` and one
notice (`notices.request`, kind `rollout`, about key
`rollout:<effect_id>`, `core/notices.py:41`), sent by the bridge like any
other (`core/bridge.py:436-447`). The notice is information; nothing waits
on it.

**After the restart.** `recover` gains one pass: each `rollout.restarting`
with no `rollout.ended` for its effect gets `rollout.ended` with
`outcome: done` when the new process's `kernel_commit()` equals the
restarting row's `sha` or descends from it, else `failed` with both
commits. This records what happened; it changes nothing.

**Where it runs.** `Kernel.tick` (`core/serve.py:309-322`) runs due
rollouts after `intake.bind` and before `schedule`, one at a time, inline,
so no new job starts between a restart-class rollout's first step and its
restart, and two rollouts never move the checkout at once. The kernel's
advisory lock (`core/serve.py:597`) already makes this the only kernel on
the machine. A merge released by the command line (`core/__main__.py:755`)
or a bridge is rolled out by the resident kernel on its next wake, since
the outcome row notifies (`core/schema.sql:125`).

**Wiring.** `Kernel` and `serve` take `checkout: Path = ROOT` and
`restart: Callable[[], int]` (default the `launchctl` call above), so
tests pass a temporary checkout and a stand-in restart and never reach
launchd. `settings.uv` (`VALOR_UV`, default `/opt/homebrew/bin/uv`, the
directory `core/workspace.py:829` already names). `PLIST_ENV` gains
`VALOR_BACKUP_DIR`, `VALOR_BACKUP_KEEP` and `VALOR_UV`. `tasks.status`
shows the task's rollout rows.

**What a failure leaves.** A failure before step 4 leaves the checkout
where it was. A failure at step 5 or 6 leaves the checkout on the merged
commit and the old process running; the next turn reads the merged
persona and stage files, and the notice names the step. Moving the
checkout back would be a reset of a directory the lead also works in, so
it is not done; the ledger and the notice say where it stands.

## Governance

Nothing here judges work or can stop it. The merge has landed before any
step runs, and no step refuses, delays or sends back a task: the rollout
is the tail of an effect that already exists, as #3610 proposes. The
kernel-project match and the restart class choose which effect steps
apply; a failed step ends the rollout, never the work. The notice and
`recover`'s `rollout.ended` are records. So the plan asks for no grant.
If review answers the governance boolean yes on a hunk, the incident is
commit `2d6ed8a8e` (a merged kernel change reached the running kernel only
by a hand rollout), the mission item is 1, and the hunk waits for Tom's
tap.

## Threat model

The turn controls the merged commit's whole content (code, lockfile,
schema, paths) and its message. Running that code is the point of a
merge; the checks before the merge are the defence. The kernel must never:
roll out a commit other than the `sha` its own `effect.outcome` recorded,
or before that outcome is `done`; decide whether or where to roll out from
anything in the task's workspace or a turn's files (only the outcome row,
the checkout's own config and the diff between two commits it names);
move the checkout by anything but a fast-forward; run git in the checkout
outside `git.run`, which refuses a hostile config (`core/git.py:427-454`);
or carry the GitHub credential into any call other than the fetch from
the merge's own remote.

## Done, as evidence

1. A test kernel with a temporary checkout cloned from a local bare
   remote, a merge task whose outcome lands a commit touching `core/`:
   rows `rollout.step` fetch, backup, fast-forward, uv sync, migrate, then
   `rollout.restarting` written before the stand-in restart is called, and
   the checkout's head is the merged sha.
2. The same with a commit touching only `skills/` and `docs/`: fetch and
   fast-forward only, `rollout.ended done`, the stand-in restart never
   called, no backup taken.
3. `recover` after (1): `rollout.ended done` with the running commit.
4. A live run on the build Mac after this merges: the next kernel merge of
   a `core/` change is followed by `rollout.ended done` and a kernel whose
   `kernel recovered` log line follows the restart, with no hand step.
   Recorded under "Merged".
5. `docs/plans/valor-rebuild.md:98-101` and the docs below say what the
   code does. Suite green, ruff clean.

## Tests (`tests/test_rollout.py`, Postgres on `VALOR_TEST_DB`)

- The two cases of Done 1 and 2, each asserting row order.
- Merge to another remote: no row. Same remote, checkout on another
  branch: `rollout.ended failed` at the match, a notice, checkout unmoved.
- A merge whose sha is an ancestor of `started`: no row (merges older than
  the running code). A checkout the lead already fast-forwarded past
  `started` while the process kept running: fast-forward is a no-op, the
  restart class is computed from `started`, so it still restarts.
- Checkout diverged (a local commit not on the target): fast-forward
  refused, `failed`, no migrate, no restart, a notice. A dirty file the
  merge changes: refused the same way. A dirty file it does not change:
  rolled, the file kept.
- Backup fails (the backup directory missing): `failed` at backup, the
  checkout unmoved.
- Migrate fails (a merged `schema.sql` with a syntax error): `failed` at
  migrate, head is the merged sha, no restart.
- The stand-in restart returns non-zero: `failed` at restart, a notice.
- A merge settled by `reconcile` (outcome from `lookup`) rolls out; a
  merge outcome `failed` does not.
- Two merges landed before a wake: rolled in outcome order, each with its
  own rows; a second wake writes nothing more.
- `recover` with `rollout.restarting` and the running commit not
  descending from its sha: `rollout.ended failed` naming both.
- A hostile key in the checkout's config (`core.hooksPath`): `failed` at
  fetch, nothing run.
- The fetch carries the credential only for a non-local remote (header
  file used once, for that URL).
- `plist` carries `VALOR_BACKUP_DIR`, `VALOR_BACKUP_KEEP`, `VALOR_UV` when
  set; the default restart's argv is `launchctl kickstart -k
  gui/<uid>/com.valor.kernel` with `start_new_session=True` (asserted on
  the argv built, never run).
- `tick` runs a due rollout before `schedule`: with a restart-class
  rollout whose stand-in restart raises, no job is started that wake.

## Absorbs

- The kernel's launchd job could not run `backup` or `uv`: `PLIST_ENV`
  and `settings.uv`.
- `docs/plans/valor-rebuild.md:98-101` says "a separate checkout" and
  "the same tapped effect"; neither is true (the checkout is the lead's;
  merges are Valor's call, `docs/plans/valor-rebuild-feedback.md:33`).

## Leaves out

- The bridges' and routines' launchd jobs (`bridges/telegram/__main__.py:13`,
  `bridges/email/__main__.py:15`, `bridges/local/__main__.py:14`,
  `core/routines.py:38`). They run from the same checkout, but each is
  its own job, live only in test windows; a merge touching `bridges/` or
  `routines/` still has its rollout steps run by the lead. The kernel
  imports `routines/emulator` itself, so such a merge is restart class
  for the kernel all the same.
- Installing or reloading any plist. A change to the kernel's plist
  itself needs a `bootout` and `bootstrap`, which `kickstart` does not do.
- Merges the lead makes by hand in phase A: no merge effect, no rollout.
  The lead keeps the steps in `SKILL.md:224-228` for those.
- Moving the checkout back after a failed step, and any post-merge watch
  or revert (R3, dropped in `docs/plans/critique-issues-out.md`).

## Files it changes

`core/rollout.py` (new), `core/serve.py` (`Kernel`, `tick`, `recover`,
`serve`, `PLIST_ENV`), `core/settings.py` (`uv`), `core/tasks.py`
(`status`), `core/__main__.py` (passes nothing new unless `serve`'s wiring
needs it), `tests/test_rollout.py` (new), `core/README.md`,
`docs/architecture.md` (restart and rollout), `docs/data.md` (the
`rollout.*` rows), `docs/plans/valor-rebuild.md:98-101`,
`.claude/skills/build/SKILL.md` (the merge bullet: a kernel-released merge
rolls itself out).

## Rollout (the last one by hand)

On Valor's Mac, from `~/src/valor-rebuild`, after the merge is
fast-forwarded:

1. `.venv/bin/python -m core backup`.
2. `uv sync`. No schema change, so `migrate` is not needed.
3. Regenerate the kernel plist with `VALOR_BACKUP_DIR` and `VALOR_UV` in
   the environment (`python -m core serve --plist`), then `launchctl
   bootout` and `bootstrap` the job, since its environment changed.
4. Check `kernel recovered` in `~/Library/Logs/valor/kernel.log`.
5. Done 4 waits on the next kernel merge; record it under "Merged".

## Questions for Tom

1. A code merge restarts the kernel at once, so a live turn is ended and
   run again, paying for its work twice. Worth it, against waiting for a
   moment with no live turn? Assumed: restart at once; the metered spend
   shows the cost, and waiting would leave the old code running for as
   long as turns keep coming.
2. Should the bridges and routines roll forward the same way later?
   Assumed: not in this task; their windows are Tom's (live only during
   test windows), so the lead keeps those steps for now.

## Decisions and records

- Planned 2026-10-08 by `plan-3610` at base `2d6ed8a8e`.
