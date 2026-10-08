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

## Patch round 5 (lead's scope after checks on 928e07889)

**Checks' verdicts.** Test: red (one regression, the missing `container`
mark on the emulator's review test). Review: changes (B1, the machine lock
under 2.4c's check profile; B2, the same test). Docs: updated at 234296f30.

**The lead's decision.** Rounds are spent. The two blocking findings are
small, and left in they would hide regressions on Valor's machine, where
`container` is installed and much of the suite would fail at base and head
alike under the check profile. So one round to this scope, and the rest as
follow-ups.

**Changed.**

- **P1 (review B1).** `core/container.py` gains `denied()`, whether a
  sandbox this process runs under denies the runtime (read with
  `sandbox_check` on the runtime's first mach name, which every profile
  denies with its CLI, helpers, data directory and the lock's directory),
  and `present()`, the CLI installed and not denied. `reap` returns nothing
  unless `present()`, so a router's sweep under the profile never tries the
  lock. `tests/conftest.py` skips `container` tests unless `present()`, and
  an autouse fixture points `container.LOCK`, `BUILDER_OWNER` and `IMAGES`
  at a directory of the session's own for every test not marked
  `container`. A `container` test keeps the machine's lock, since it runs a
  real runtime beside the kernel's verifications and must wait for them.
  New test `test_checks::test_under_the_check_profile_the_runtime_is_absent_and_the_machine_lock_untouched`
  runs this repository's interpreter under the real check profile
  (`sandbox-exec`, the valor spec's environment): `denied()` is true,
  `present()` false, `reap` returns nothing; and pytest there passes
  `test_a_stop_while_waiting_on_the_lock_returns_without_taking_it` (the
  test that failed at `core/container.py:274`) and skips a `container`
  test. It shares the profile setup with the collect test, moved into one
  helper. Before the fix it failed on `denied` missing, then on the lock
  test's `PermissionError` at `LOCK.parent.mkdir`.
- **P2 (review B2).** `test_emulator_metering`'s
  `test_review_is_run_by_the_kernels_runner_and_docs_pauses_the_driver_for_its_verdict`
  carries `container` and builds its candidate with `test_checks.VM_SUITE`,
  as its sibling review tests do.
- **P3 (test check section 3).** `macos` on:
  `test_serve::test_a_kill_mid_provision_is_redone_on_restart` and
  `test_workspace::test_workspace_remove_takes_an_unfinished_provisioning_once_stopped`
  (chflags);
  `test_workspace::test_provisioning_git_is_marked_and_reaped` and
  `test_provision_restart_gaps::test_remove_reaps_what_a_setup_command_left`
  (`ps -E`); `test_checks::test_the_valor_suite_collects_under_the_check_profile`
  (sandbox-exec); `test_denials`' `/private/tmp` parameter alone.
  Of the four suspects in `test_provision_restart_gaps.py`, three are
  marked: `test_a_dangling_link_at_the_task_root_is_redone` and
  `test_the_redo_frees_the_dead_attempts_ports` (a task directory exists,
  so `_provision` calls `workspace.remove`) and
  `test_workspace_remove_takes_a_merged_tasks_unfinished_provisioning`
  (the `workspace remove` command). `workspace.remove` always calls
  `runs.reap`, which lists processes with `ps -A -E`, and clears the tree
  with `setattrlistat`, both macOS's. Not marked:
  `test_a_provisioning_job_for_a_legacy_or_calibration_task_clears_nothing`,
  since `_provision` returns on a legacy or calibration fold before
  reaching `workspace.remove`.
- **P4 (review N6).** `docs/routines.md` names `VALOR_MACHINE`,
  `VALOR_WORK` and `VALOR_PROJECTS` among the plist's settings, and why.
  `routines/README.md` says `core/__main__.py` imports it.
- `tests/README.md`'s markers bullet says a `container` test skips where
  the runtime cannot be run (absent, or denied by the sandbox the suite
  runs under) and that every other test's machine lock is the session's.

**Follow-ups, not done here.**

- Review N3: Done 4's evidence (`tasks.audit` empty, the preempted turn's
  calls charged, its process group gone, the resumed step on the same
  session with no answer spent, `schedule`'s foreground-first order) as
  asserts in `tests/test_slot_priority.py`.
- Review N4: two expiry firings at once can each start a sweep; hold
  `routine:NAME` across the runner. Fixed: `m4-3n4-expiry-lock.md`.
- Review N5: a grant on a valor task started without `--project` is listed
  as outside the repository and never removed.
- Review N7: a background turn started while a preemption is pending
  writes a `turn.started` and `turn.ended preempted` pair; a refused
  expiry start writes no `routine.ran failed`.
- The test check's breadth list (section 2): the emulator runner's failure
  branch, report content, `_drive` and `start --replay`, a stop mid-sweep,
  the status page's `/pending` and `/attention` with held effects and
  answers, the routines run table, `tasks.index` labels, `python -m ui` on
  8790, the `core routines` and `core routine NAME` CLI, `--restart`,
  `VALOR_ROUTINE_PERIOD_DAYS`, a plist linted with `plutil`.
- Seen while marking, outside this round's list:
  `test_provision_restart_gaps::test_remove_clears_a_dangling_link_at_the_root`
  and `test_remove_of_a_missing_root_is_nothing_to_clear` call
  `workspace.remove`, so they reach `ps -E` and want `macos` too; and
  `test_denials`' `/bin/ps` parameter errors on an image without `/bin/ps`.

**Tests** (Tom's Mac, `VALOR_TEST_PORTS=6430-6439`, `-m "not container"`;
no `container` binary here): 1,561 passed, 2 failed, 45 skipped, 62
errors. The failures (`test_mailserver`, Dovecot; `test_pi`, node) and the
62 errors (Dovecot) are the same as at 777d894d1. The emulator's review
test is now deselected with the other `container` tests. The run created
no `~/Library/Application Support/valor`. `ruff check` and
`ruff format --check` pass.

## Checks after patch round 5 (a75126eb7)

- test: pass. With `-m "not container"`: 1561 passed, 2 failed, 45 skipped, 62 errors at head, against 1496 passed, 2 failed, 55 skipped, 62 errors at 777d894d1. The failures and errors are Dovecot and node missing on Tom's Mac, the same at base. No regression. The suite under the check profile leaves `~/Library/Application Support/valor` untouched.
- review: pass. B1 and B2 are fixed. `present()` cannot route a review around the VM, because `container.verify` requires the runtime and fails with a `kernel` cause when it is missing. Governance boolean: no. Not blocking: two `workspace.remove` tests in tests/test_provision_restart_gaps.py (lines 50 and 63) want a `macos` mark. They fail at base and head alike in a VM run.
- docs: updated, 3610cce32 (data.md, sandbox.md, the m1-4c-verifier.md Tests section).

## Merged

The lead merged on 2026-10-06, fast-forwarding `valor-cori-rebuild` to 3610cce32 with no tap (Tom, 2026-10-03). All three checks passed after patch round 5.

**Rollout held on Tom's Mac (Decided by default).** Tom's Mac runs the kernel and has no `container`. Restarting its kernel onto this code would make every review fail with a `kernel` cause until the runtime is installed, and installing it takes Tom's admin password. So nothing on that Mac moves until Tom installs `container` (m1-4c-verifier.md, Rollout prerequisite): no kernel restart, no `migrate`, no routine plists. After he installs it, the lead runs both plans' Rollout steps in order: backup, restart, migrate, the two routine plists, a kickstart of the expiry routine. The lead records them here. Valor's Mac has `container`, and its kernel takes this code on its next pull and restart.

**Follow-ups, not planned:** review N3 (tests for Done 4's evidence), N4 (two expiry firings at once can each start a sweep; since fixed, `m4-3n4-expiry-lock.md`), N5 (a grant on a valor task started without `--project`), N7 (a preempt race leaves a row pair; a refused expiry start writes no `routine.ran failed`), the test check's breadth list, and the two unmarked `workspace.remove` tests.

**Rollout on Tom's Mac (2026-10-07).** Tom installed `container` 1.5.0. The lead did the following:
- Installed Rosetta, which was missing.
- Ran `container system start`. The runtime's launchd services could not reach the network through Little Snitch, so the default kernel download and image pulls timed out. Tom added an allow rule for the runtime's services. The lead fetched the kernel archive with `curl`, checked its sha256 against the runtime's `digest`, and installed it with `container system kernel set --tar`. A test VM (alpine) then pulled and ran.
- Backed up first: 196 events to `/Volumes//valor_temp/valor_rebuild-20261007T100257Z.dump`.
- Ran `uv sync`.
- Stopped the idle kernel by PID (55238; no turns running, nothing pending).
- Ran `migrate`. The password file was kept and `pg_hba.conf` was unchanged.
- Restarted `core serve` with the same environment and log (pid 62856).
- Wrote both routine plists. `plutil -lint` reported them OK, then they were bootstrapped.
- Kickstarted the expiry routine. It ran under launchd and reported `nothing_due` at $0.
- `core routines` lists both routines.
- Started the status page with `python -m ui` (pid 65224). It answers 200 at http://127.0.0.1:8790/.

The bridge job `com.valor.kernel.local` still listens on 8711. Still to come is 1.4c part two Rollout step 5: the first real task after this rollout runs its review rerun in a VM, and the lead reads its `verify.ran` by hand once. The docs no longer say that Tom's Mac lacks `container`, and rebuild-handoff.md Setup names the Little Snitch rule and the kernel archive fetch.

**Rollout on Valor's Mac (2026-10-08).** The kernel there had run code from 2026-10-05 and had no routine jobs. The lead did the following:
- Backed up first: 2163 events to `/Volumes/PINK/valor_temp/valor_rebuild-20261008T030556Z.dump`.
- Ran `uv sync` and `migrate`. The password file was kept and `pg_hba.conf` was unchanged.
- Restarted the kernel with `launchctl kickstart -k`. Its printed job matched the installed one. It was restarted again on 74489521a, Tom's message-seat change.
- Wrote both routine plists. `plutil -lint` reported them OK, then they were bootstrapped. The expiry routine was kickstarted, ran under launchd, and reported `nothing_due` at $0.
- Started the status page with `python -m ui`. It answers 200 at http://127.0.0.1:8790/.
- Checked the runtime with `container` 1.5.0 and Rosetta: a throwaway alpine VM ran. Ran the judgement calibration again: `task_sha256` equals `JUDGE.calibrated`, and `entry_check` is true. The long-lived Claude token answered a one-word call.
- The data volume was 99% full (2.7 GB free), which made container builds fail with `No space left on device`. Clearing test temp directories, uv's and Homebrew's caches, and the test container images left 16 GB free. With that space, `tests/test_container.py` passes except the repository suite's VM run, the base failure kernel task b4288894e613 clears.
