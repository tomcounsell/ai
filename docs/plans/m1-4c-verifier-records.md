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
- In the VM this repository's suite passes 895, skips 453 (the `macos`
  tests among them) and fails 20 in 184 s: 18 in `tests/test_credentials.py`
  and `tests/test_judgement.py`, which assume the host's local Postgres
  roles; `tests/test_emulator_package.py`, which runs `git ls-files` in a
  source tree that is not a checkout; and the deep request file in
  `tests/test_session.py`, which overflows the VM's 8 MB stack.

**Tests:** the host suite (`-m "not container"`) passes 1,301 and skips 21
(live spend, a measurement, Pi with no subagents, git that does not trust a
planted commit-graph); the `container` tests pass 44 and skip 2 (live
spend), the repository-suite test among them. `ruff check` passes; `ruff
format --check` passes.
