# 1.4c, part two: records

Records of [m1-4c-verifier.md](m1-4c-verifier.md).

## Critique round 1 (of 2): revise

The report covered both parts. The lead split the task: findings 1, 2, 3,
5, 6, 11, 12, and 13 are handled in m1-4c-review.md; these are handled
here. The governance question moved to Decided by default (item 9 here,
item 7 in part one).

4. The candidate controlled its base's environment and the shared
   builder: the base runs in the base manifests' image, every build uses
   a fresh builder with no cache, `manifests_differ` goes to the
   reviewer, and the builder's reach to host services is tested (Images;
   Tests).
7. Pruning raced running verifications: the machine lock is held from
   system start to system stop, and pruning runs under it (Runtime;
   Images).
8. Orphan cleanup was too narrow: any task's run start reaps every
   labelled VM and builder when the lock is free; builds are raced
   against a stop (Runtime; Images).
9. The spec's source was wrong and its rendering unspecified: the spec is
   the task document's, written into the VM as `spec.json`; `run.sh`
   renders `env`, `{port}`, `{passfile}`, and the roles; dependency images
   run the spec's own setup (Specs inside the VM; Images; The run).
10. Deny placement and an overclaim: the denies sit after the allows; the
    `launchctl` and `open` openings are tested and documented, not
    claimed closed (Profile denies; Threat model; Tests).
14. A memory kill was blamed on the candidate: `cause: memory`; the suite
    runs in a child cgroup, with a fallback where its files are absent
    (The run; Reading the result).
15. Runtime behaviour unverified: fallbacks for the read-only mount (a
    directory holding only the tar and spec), run by digest (a checked
    tag), and `/out` ownership (`result_owner`) (The run; Images).
16. Rollout gaps: the install is in rebuild-handoff.md for any machine;
    the system is started and stopped per verification, keeping
    tech-stack.md's statement true (Runtime; Docs fixed; Rollout).
17. The `../../escape` test could not arise from `git archive` and is
    dropped; m1-4-checks.md's Rosetta and "64 GB" lines are in Docs fixed.

## Critique round 2 (of 2): revise

Both rounds are spent; every finding is folded in. Findings 1 and 3 to 11
are part one's (m1-4c-review.md); 2 and 12 are handled here.

2. A per-database advisory lock guarded a machine-wide runtime: the lock
   is an `fcntl.flock` on a fixed file, every container and builder is
   labelled with its owning database, a sweep reaps only its own label
   and stops the system only when no owner's container remains, tested
   with two test databases (Runtime; Tests).
12. Smaller gaps: the wait on the lock races a stop; the base run in the
    VM is reused by base sha, base image digest, `memory_mb`, and
    `where`; part one's key carries `where`, so no host result is reused
    for a VM run; `memory_mb` in every VM key, so a raised default reruns
    a memory kill; nothing in the VM has a timeout (image builds, setup,
    the suite, and the lint), as 1.4b runs them on the host;
    `read_turn_file` is cited for its walk only (Runtime; The run;
    Reading the result).

After round 2, from 1.4u's critique: with no timeout, a command whose
output is read through a pipe waits for end of file, so a setup command
that leaves a child holding stdout hangs. Every command in the VM and
every CLI call on the host writes its output to a file, is waited on by
its exit, and has its group killed after (The run; Tests, Results).

## Build record

Built on branch `m1-4c2-verifier` from `m1-4c1-docs2` (`480b61b24`), test
database `valor_rebuild_test_14c2build`, ports 6540 to 6549. No sudo.

**Runtime facts** (Rollout step 2), read from the installed `container`
1.5.0 (commit `d265d669ecae041bf338cb3b39c4118316d138f0`): data under
`~/Library/Application Support/com.apple.container`; Mach services
`com.apple.container.apiserver`, `com.apple.container.core.container-core-images`,
`com.apple.container.core.machine-apiserver` and
`com.apple.container.network.container-network-vmnet.default`; the default
network is 192.168.64.0/24 with the host at 192.168.64.1. The builder
takes `--mount=type=cache`, and a background process left by an install
does not hold the build open. `list --all --format json` carries each
VM's `configuration.id` and `configuration.labels`. `builder status`
exits 0 when the builder is stopped, so the tests read its JSON.
`image tag` exits 0 and leaves a tag made by a build in place. `run`
prints `[n/6]` progress lines on its output. The host firewall drops
(not refuses) a connection from a VM to the host's gateway or LAN
address, so each probe of one takes about 67 s.

**Base image** (Rollout step 1): the pinned base pulls and the base image
builds in about 9 minutes; a dependency image builds in 59 to 175 s.

**RAM** (Rollout step 3), on this 16 GB M4: this repository's suite peaks
at 418 MB in the VM, with a VM footprint of 995, 1,095 and 1,224 MB at
1, 2 and 4 GB. A Django suite with Postgres and Redis (five test workers
of about 330 MB each) thrashes at 1 GB (the VM spins, exec stops
answering, no memory kill; stopped at 349 s), peaks at 1,855 MB at 2 GB
(footprint 2,215 MB) and 2,154 MB at 4 GB (footprint 2,898 MB). An idle
VM costs 293 to 427 MB, the daemons 85 to 145 MB, `system start` 0.3 to
1.0 s. `verify_memory_mb` is 4096 and `docs/machine.md` carries the
figures. The Django suite's vector tests failed at setup in the VM as on
the host: its role is not a superuser and cannot create the extension, so
the base image adds no pgvector.

**Patch round 1** (the live tests' first pass, 17 of 22 in
`tests/test_container.py`): the main test's background setup now
redirects its output, since the host's setup run waited on the open pipe;
`builder_up` reads the builder's JSON state; the retag test deletes the
built tag before tagging over it; the no-network test drops the CLI's
progress lines; the repository-suite test calls `never()` inside a
coroutine; the npm test ignores the lock file the host's install writes.
`core/images/base/deps.sh` fetches what the build backend adds for an
editable build (hatchling adds `editables`), which the offline sync of a
Python project with a build system needed.

**Patch round 2:** the repository-suite test compared HEAD with its
parent, whose suite predates the `macos` marks; in the VM that suite
failed widely and then waited with no time limit. The candidate is a
commit of HEAD's tree with HEAD as its base.

**Patch round 3** (review 1568a7ebc: changes; test check: pass):
- The plan no longer puts a memory cause in the attention log, which holds
  only Tom's acts. The cause stays on `verify.ran` for the reviewer, and
  raising `verify_memory_mb` is a machine setting, not a question for Tom.
- `deps_key` hashes `core/images/deps/Containerfile`, so an edit to it
  builds new dependency images.
- `system start`, `image inspect`, `image delete`, `kill` and `delete`,
  made before a stop, are raced against the stop as builds and runs are
  (`container.short`); the removal a stop runs is not raced.
- `cause: memory` needs a suite that did not exit 0; a clean exit at the
  peak has no cause.
- Tests: the dependency key changes with the deps Containerfile; a CLI
  that never returns ends on a stop at each short call; a clean exit at
  the peak is no memory cause.

**Patch round 4** (review bf0a08137: pass; test check: gaps):
- The test of a hung short call set its stop half a second after the
  call began, sometimes before the fake CLI had written its line. The
  stop now fires when the hung call writes to a FIFO the test reads.
- `builder delete` before and after a build, each `image delete` while
  pruning, and `system stop` at the end of a verification are raced
  against the stop as well (`builder_deleted`, `system_stopped`, `prune`
  is async); what a stop runs itself stays unraced. A stop while pruning
  leaves the image's record, so the next prune deletes it.
- Tests: the hung-call test covers the new calls; it passes 30 of 30 runs
  alone.

**Patch round 5** (the branch on 1.4c part one, 1.5 and 2.1):
- Part two is one commit on the merged code. Conflicts resolve toward the
  merged code: its review runner, serve, slot and session collection
  stay; `fresh.reusable_verify` and a host `verify.ran` are gone, so the
  rerun uses `container.reusable` and `container.verify`; the manual
  rows, the final-message checks and the setup checks of the merged
  `tests/test_review.py` stay. Tests of code the merged branch removed
  are dropped: host reuse, manual-leg registration, command time limits.
- `read_result` passes `checks.lint_locations` the lint output's lines.
- Docs keep the merged splits: `docs/sandbox.md` owns the sandbox split,
  `docs/sandbox-openings.md` the openings, `m1-4c-outline.md` the
  outline. `docs/machine.md` totals the container row with the measured
  headless browser: peak 12,078 MB.
- Tests of the merged code that need macOS itself (`sandbox-exec`, `ps
  -E`, `libproc`, `setattrlist`, `hdiutil`, the Homebrew Postgres, APFS
  sparse files) carry the `macos` mark. Without it the VM suite failed
  in 80 tests and waited on a service start under `sandbox-exec` with no
  end.

**Patch round 6** (the branch on 2.2, 1.5r and 4.1): the review runner is
registered and `verdict` records only `docs` by hand; part two's rerun
in a VM goes into that runner unchanged. `ruff` joins the dev group
beside the merged `--import-mode=importlib`, and the lock adds it.
Tests of 4.1 and 1.5r that need macOS itself carry the `macos` mark: the
review runner's docs pause in `tests/test_emulator_metering.py`, the live
stop of a root in `tests/test_objective_tree.py`, and the two replies in
`tests/test_telegram_pipeline.py`.

**Patch round 7** (review 7743b80d8: changes): the two sparse-file tests
`tests/test_look.py::test_a_sparse_screen_is_sized_without_being_read`
and `tests/test_transcripts.py::test_a_sparse_file_is_skipped_without_reading_its_holes`
carry no `macos` mark. They use only `st_size`, `st_blocks` and
`os.pread`. In the VM both failed: a file truncated to 1 PB raises
`EFBIG` on the VM's ext4, whose largest file is 16 TiB, while 8 TiB
truncates with no block used. Both now plant an 8 TiB file and pass on
the host and in the VM, and the repository-suite test asserts that both
pass in the VM. `docs/machine.md` gives the Django suite's 2 GB peak as
1,855 MB, as this record does.

**Patch round 8** (test check on 7743b80d8: pass with two gaps). The
lead's calls, which replace item 2 below: a test the VM cannot pass is
fixed to run there when its behavior is portable, else carries `macos`
with its reason in a comment, so the VM's run has no failure at base for
a regression to hide behind (the repository's own test check runs in the
VM, `docs/plans/m1-4y-check-hang.md`); a parametrized test carries
`macos` only on the parameters that need macOS. What changed:
- The two other sparse tests plant 8 TiB. `tests/test_workspace.py`'s
  sparse verdict runs in the VM; `tests/test_signals.py`'s splits into a
  sparse and written file test, which runs in the VM, and a cloned
  request test, which keeps `macos` for `cp -c`, an APFS clone.
- A scratch cluster's bootstrap superuser is `settings.owner_role`, the
  role the kernel databases' code connects as, where it was the login
  user; on the Mac they are the same name. The test session sets
  `VALOR_PG_BIN` to Debian's PostgreSQL 18 where the Homebrew one is
  absent. `tests/test_credentials.py` runs in the VM, but for
  `test_migrate_touches_no_credential_on_the_machine_cluster`, which
  reads `pg_authid` as the machine cluster's superuser owner and keeps
  `macos`.
- `tests/test_judgement.py`'s two CLI runs log in through a link, beside
  no key, to the database's own password file, where they relied on the
  host's cluster.
- `tests/test_emulator_package.py` lists `scripts/` on disk, not with
  `git ls-files`.
- `tests/test_session.py`'s request too deep for Postgres nests 50,000
  deep, which Python parses on the VM's 8 MB stack and Postgres still
  refuses.
- Per-parameter marks: `test_a_stopped_task_takes_nothing_more_in_any_state`
  runs `judge` and `plan` in the VM; `test_a_clone_with_alternates_a_shallow_file_or_hostile_config_is_refused`
  all but `config`; `test_a_clone_whose_git_directory_lives_elsewhere_is_refused`
  carries no mark.
- The repository-suite test asserts the VM's run has no failed or
  errored test.

**Decided by default** (the lead's calls on patch round 5):
1. A test that needs macOS itself carries the `macos` mark, merged tests
   included, so the repository suite ends in the VM.
2. (Replaced in patch round 8.) A test that fails in the VM for a
   reason other than macOS keeps no mark: `tests/test_emulator_package.py` (no git checkout in the VM)
   and the deep request file in `tests/test_session.py` (the VM's
   stack). It fails at base and head alike.

**Decisions the plan left open:**
- The VM mounts the source at `/valor/src` and the output at `/valor/out`
  under a root-only `/valor`.
- Built image digests sit in `images.json` beside the lock; base images
  are not pruned.
- `GIT_CANDIDATES` adds `/usr/bin/git`, the real install in the VM.
- The reap stops the container system only when no VM of ours remains.
- A `.command` opens with `open -g -j`.
- `ruff` is a dev dependency, so the VM lints with the pinned version.
- `read_result` adds lint locations only for `python-uv`.
- In the VM this repository's suite passes 1,018 and skips 450 (324 of
  them `macos` tests) with no failure or error, in 220 s, peaking at
  2,063 MB (patch round 8).

**Tests:** the host suite (`-m "not container"`) passes 1,398 and skips 23
(live spend, live Telegram, a measurement, Pi with no subagents, git that
does not trust a planted commit-graph); the `container` tests pass 44 and
skip 2 (live spend), the repository-suite test among them. `ruff check`
passes; `ruff format --check` passes.

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
