---
tracking: none
slug: m4-3-routines-record
type: plan
status: built
---

# 4.3 Routines and the status page: record

The decisions taken and the build and patch records of
[m4-3-routines.md](m4-3-routines.md).

## Decided by default

Each is reversible; Tom can overturn any.

- **The expiry sweep's deletion branch merges without Tom's tap.** The
  governance paragraph says an unfired guard "is deleted by default", so
  the deletion is the default and needs no tap. Keeping an item is the
  exception, and Tom gives it in review. Merges are the build lead's call,
  so the deletion merge is released like any other merge, with no tap.

- **A guard that fired before its expiry** is listed 90 days after its
  last firing; an unfired one at expiry (the governance paragraph).
- **Use of a routine** is as [routines.md](../routines.md) defines it: an
  effect performed, a question Tom answered, or a child task that
  delivered. The emulator sweep delivers, so it is not listed.
- **Instance grants show "no firing record"**; no hook records firing.
- **The plist is printed, not committed.** It names the checkout and its
  interpreter, which differ per Mac; `core backup --plist` already works
  this way. The schedule is in the toml, in git.
- **One live objective per (name, ceiling)**, with runs as its children, so
  4.1's rollup gives the period figure and one stop ends the routine.
- **The period is the rolling 30 days ending at the report**, by the
  charge row's time, and includes the emulator's calibration tasks for the
  routine's replays.
- **A foreground step runs beside a background one.** `schedule` starts a
  ready foreground step even while a background step is running, and a
  background step only when no foreground step is ready. This changes how
  many steps the kernel runs at once, from one to two at most: the
  background one is only waiting for or holding the slot, and the slot
  still lets one `claude -p` run at a time. The reason is Done item 4: a
  kernel busy with a background step cannot otherwise start Tom's step to
  announce itself and preempt it. The source is the slot's order section of the plan and
  [machine.md](../machine.md) (Concurrency, Tom's work first). It adds no
  bound beyond the plan's.
- **A background turn is preempted** when a foreground step is ready,
  since waiting for it would break the Done. Its spend is metered and lost,
  and counted in the sweep's report.
- **Replays are background**, whether a routine or a person started them.
  The forced `clarify` arm would otherwise keep `intake.underspecified`
  alive, and a hand-run gate replay would preempt routine turns.
- **The expiry sweep covers guards and routines**, whose use is recorded.
- **One open sweep at a time**, continued by later firings.
- **The expiry routine is not listed by itself** (Mission item 5).
- **Both routines carry ceiling `act`.** The sweep's merge and the replays'
  local pushes are `act`, and each waits for a tap. The replays' taps come
  under the standing permission emulator.md records, which this task does
  not change.
- **Schedules.** Expiry runs daily at 04:00 and costs nothing when nothing
  is due. Emulator runs weekly, Sunday 01:00, with all items in three
  arms, one run each. Spending is reported, never capped.
- **The page** is aiohttp, loopback only, port 8790, read-only, by hand.
- **A stopped routine stays stopped** across scheduled firings until Tom
  runs `python -m core routine NAME --restart`, which needs no diff.
- **No limit beyond the cited ones.** Only the emulator driver holds a
  run lock, as `run` does; the kernel, not the command, drives the expiry
  task. No routine sets a timeout, a run count, or a spending figure. The
  numbers are the 30-day period (the rebuild plan), the 90 days (the
  governance paragraph, Mission item 5), the schedules, and the port.

## Decided by default at build

Where the code at e70a91d92 differs from what the plan assumed, the build
follows the code. Each is reversible.

- **A preempted step returns `{"status": "moved", "preempted": True}`.** The
  kernel pops the task from `seen` so it runs again; `router.run` loops on
  "moved" and the kernel uses `router.step`.
- **`schedule` yields `(background, latest, task_id)`** and the kernel starts
  a foreground step beside a background one, and a background step only when
  no foreground step is ready.
- **A preempted turn** writes `turn.ended` with outcome `preempted` and result
  `{}`. `machine.fold` already counts a turn only when it is done, and
  `LAST_WORKING_ENDED` excludes preempted, so no fold changed.
- **`--name` is an alias of `--run` in the replay driver**, and `--parent`
  starts a replay under a routine's run. The driver's lock-file slot is
  gone; `machine_lock` stays in `tests/emulator/common.py` for the judge.
- **The emulator's open-run rule.** A run with no report is continued; the
  session lock `run:<run>` makes a second process say `already running`.
- **The expiry toml names the `valor` project and the
  rebuild branch**, the project and branch this checkout builds.
- **The status page answers 404 for an unknown task**, by reading the Brief
  before the status.
- **`core/__main__.py` has `_routine_runners()`** where the plan named a
  `ROUTINE_RUNNERS` table.
- **`tests/scripted.provisioned` takes `brief_kw`** to put `routine` or
  `replay` in a test task's Brief.
- **The emulator sweep runs Sunday 01:00**, as planned.

## Build record

Built on 4.1 (e70a91d92) over the plan commit ca0cfb898. New tests:
`test_routines.py`, `test_slot_priority.py`, `test_expiry.py`,
`test_ui.py`, and `test_live_routine.py` (gated on `VALOR_LIVE=1`, not run).
Docs rewritten to the built behavior: `routines.md`, `machine.md`,
`emulator.md`, `tech-stack.md`, and the `routines`, `ui` and `core` READMEs.
Suite and lint results are in the builder's report.

## Patch round 1

Rebased the three 4.3 commits onto the tip of the merged 4.1 (61241b374);
the 4.1 candidate commits dropped out because the merged 4.1 holds them.
The one conflict was `docs/tech-stack.md`: the Telegram and secrets rows
follow the tip, the Scheduling and Dashboard rows follow 4.3.

Locks checked against 4.1's order (slot session locks, then `tree:<root>`,
then `task:<id>`; intake takes the tree first on every bound reply). 4.3
takes `routine:<name>` alone, before `tasks.start` registers a root, which
takes no tree lock for a root. `stop_tree`, `start_child`, intake and
`session.feedback` take tree then task. The slot and the shared foreground
lock are held on connections that open no transaction, and nothing takes
them while a tree or task lock is held. No inversion found, no code change.
`test_a_stop_of_the_tree_beside_a_preempting_step_deadlocks_nowhere` stays;
it fails if a writer takes task before tree.

## Patch round 2

Review R1 and R2 and notes N1, N2, N4, N5, N6 (review-4-3).

- **R1.** The expiry instruction now reads "Remove nothing the list does not
  name, and keep everything else. One branch, one delivery; its merge is
  released like any merge." The tap wording is gone. Test: the rendered
  sweep prompt says so.
- **R2.** `routines.due` treats an instance grant that a merged sweep listed
  as removed (fix (a)): the grant has no code to check, so the merged
  deletion is its removal and the fold reads it from the sweep's
  `task.started` and the sweep's state. A seeded guard and a routine already
  leave the fold when their code leaves the checkout. Test fails without it.
- **N1.** `routines.PLIST_ENV` adds `VALOR_MACHINE`, `VALOR_WORK`,
  `VALOR_PROJECTS` (set only when present; test).
- **N4.** `tasks.start` no longer refuses a calibration marker; no caller
  passes one, and a root started that way breaks nothing `start_child`'s
  refusal protects.
- **N5.** The page shows the whole instruction; the sweep report keeps the
  whole output of a failed replay.
- **N6.** `routines/README.md` and `docs/routines.md` no longer say Tom taps
  a sweep's merge or gives the keep reason.
- **N2.** Decided by default: preemption is heard at the turn's process wait
  and the check's suite wait; other waits in a hold finish first. One line in
  machine.md.
- **N3, N7** need no change.

### Patch round 2, test-4-3 findings

- **Printed line.** `routines.run` takes the report's time after the runner
  (unless a caller gives `now`), so the line counts the run it made and
  matches the page. Test.
- **Paused replay.** When any arm's result still has no outcome after the
  driver, the emulator runner writes no report and returns `running`
  ("N replays paused"); the next firing finds the run without a report and
  resumes it under the same names. Test: two firings, one run.
- **Expiry merge.** The sweep's merge test now approves the held merge as the
  build lead and releases it to `merged`; no other approval is asked.
- **The real spec.** A test loads `projects/valor.toml` through the routine's
  path (`Spec.load`, the routine's branch) and checks the merge target:
  refused without a grant, accepted with one. No clone, no push.
- **Decided by default:** the toml's `branch` stays as the rebuild
  branch; it is the granted merge target for now.
- **Decided by default:** the hand-run replay's `machine_lock` (the
  `VALOR_DEMO_SLOTS` throttle, 3 slots) stays removed. Its count had no
  source. The kernel's turn slot now serialises the machine's turn resource,
  and a replay is background work under it.
- **Decided by default:** a judgement call inside a check (breadth,
  governance) is not preempted; a foreground task waits for it.
- `tests/emulator/replay.py`'s usage no longer lists `--replay`.

### Decided by default, patch round 2

- **Instance grants only.** A grant that a merged sweep listed is removed for
  good (option (a)); keeping one means issuing a new grant.
- **A kept guard returns every 90 days.** A seeded guard a merged sweep kept
  is listed again 90 days later. That is the governance paragraph's expiry
  working: a guard is reviewed again at each expiry.

### Patch round 3

Scope from the lead, after review-4-3-p2 (R3) and test-4-3-p2 (gaps 1 and 2).

- **R3.** In `routines.due` a merged sweep's listed grant counts as removed
  only when the grant is in this repository (its task's project is `valor`).
  Another project's grant stays listed under `outside`. Each grant row has its
  own id and an instance is granted once per task, so keeping a grant is a new
  grant row with its own id and expiry: the earlier row counts as removed, the
  new one is live and falls due 90 days after it was given. Two tests:
  another project's grant stays listed after the merge; a grant written after
  the merge is live and due on its own date.
- **Gap 1.** `routines.load` refuses a `[schedule]` value launchd would reject
  (not a whole number, or outside minute 0-59, hour 0-23, day 1-31, weekday
  0-7, month 1-12; `interval` not a whole number of seconds of 1 or more) and
  refuses `interval` together with calendar keys. Parametrized test.
- **Gap 2.** `due` reads each registered routine inside a `Refused` catch. A
  routine whose toml is malformed is returned under `malformed` and the
  sweep's run line says "not read, <reason>"; the rest go on. Test.
- Gap 3 stays: an empty items directory gives a finished sweep with 0 items.
- **Decided by default:** the "removed" test is by grant id, not by merge time,
  since ids are unique per row.

### Patch round 4

Scope from the lead, after review-4-3-p3 (R4, N8) and test-4-3-p3 (T1, T2).

- **R4.** `guards.grant` on an expiry sweep's task in `merge` takes, in place of
  an instance, a grant id the sweep's `task.started` list names. Tom's tap is
  the same `grant` command and the same approval path; no new step. It writes
  a new `guard.granted` row (fresh id, `kept` naming the old id, a new
  90-day expiry, the old row's incident and mission items, and an instance id of the old
  one plus the new guard id, since the ledger holds one grant row per task and
  instance) on the
  old grant's own task, so `due` still sees its project and the earlier row
  counts as removed while the new one is live. Test goes through `grant`.
- **N8.** routines.md says the ranges and the integer come from launchd and
  the plist, and that "not both" is the printer writing one trigger
  (launchd itself accepts both).
- **T1.** `load` refuses a non-text `ceiling`, a non-table `schedule`, and a
  `need` that is not a list of text, as `Refused`; `due` lists them under
  `malformed`. Tested each, and through `due`.
- **T2.** `interval` must fit a plist integer (signed 64-bit, below 2**63),
  so `plist()` cannot overflow. `created` as a datetime is refused: the
  documented key is "the date".

## Rebase onto 777d894d1

The merge candidate holding 1.4c part two and 4.3 (`m4-3-merge` at
eeb6b858e, on 66ac98a48) was rebased onto the rebuild branch at
777d894d1 (2.4, 2.4a, 2.4b, 2.4c) as branch `m4-3-rebase`. Conflicts and
how each was resolved, keeping both sides' documented behavior:

- `pyproject.toml` dev group: both sides pin the same three; the tip's
  one-line form kept. `uv.lock` regenerated with `uv lock`; it equals the
  tip's.
- `tests/README.md`: the tip's `VALOR_TEST_PORTS` and denials bullets kept
  above the candidate's markers bullet (its fuller patch round 8 text).
- `core/README.md`: the tip's paragraph (2.4b's redo of an unfinished
  provisioning and `workspace remove` of its directory) with the
  candidate's clauses added: the review's rerun in `container` VMs,
  `containers.reaped`, and `grant` taking a listed grant id on a sweep
  task; the tip's bridge-port bullet (local chat) with the candidate's
  routines bullet after it.
- `docs/tech-stack.md`: the tip's Bridges and Secrets rows (local chat);
  the candidate's Scheduling row (`in use`, naming the local bridge's job
  too) and Dashboard row.
- `tests/test_kernel.py`, the revoke test: the candidate's local server
  that accepts and never answers, with it and the gateway binding
  `ports.listen()` as 2.4c has every test server do.
- `tests/test_workspace.py`: both sides' tests kept (2.4b's unfinished
  provisioning tests, the candidate's container-profile tests); the tip's
  denials skip on the disk-image test and its `monkeypatch` signature on
  the scratch-cluster test, each with the candidate's `macos` mark.
- `tests/test_judgement.py`: both sides wrote a password-file helper for
  the no-key CLI tests; the tip's `_passfile` (a copy, when the file
  exists) kept in place of the candidate's link, since a copy also logs in
  where the cluster asks for a password.
- `tests/conftest.py`: both kept (the tip's reserved-ports file and task
  port spans, the candidate's `VALOR_PG_BIN` fallback for the VM).
- `.claude/skills/build/SKILL.md`: the tip's text, with the 1.4c part two
  row saying `container` is installed on Valor's machine and not on Tom's
  Mac.

Adapted to the tip's interface: the gateways in `tests/test_expiry.py`
and `tests/test_slot_priority.py` start on `ports.listen()`, and
`tests/test_ui.py`'s `TestServer` binds `ports.listen()`, as 2.4c has
every test server do.

**Tests** (Tom's Mac, `VALOR_TEST_PORTS=6430-6439`, `-m "not container"`;
this Mac has no `container` binary, so the `container` tests were not
run): 1,551 passed, 3 failed, 54 skipped, 62 errors. The 62 errors and
`test_mailserver`'s failure are Dovecot, not installed here; `test_pi`'s
command test needs node, not installed here; both fail the same way at
777d894d1. `test_emulator_metering`'s
`test_review_is_run_by_the_kernels_runner_and_docs_pauses_the_driver_for_its_verdict`
fails because the review's rerun needs `container`; it passes at
777d894d1, where the rerun is on the host, and carries no `container`
mark. `ruff check` and `ruff format --check` pass.
