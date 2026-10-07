# 1.4c follow-up: this repository's suite runs clean in the verification VM

`tests/README.md` and `m1-4c-verifier.md` say this repository's suite runs in
the review's verification VM (`core/container.py`) with only the `macos`
tests skipped. On 2026-10-07, task 6fd4e0439ac1's `verify.ran` (ledger
event 842, `where: "vm"`, release 1.5.0) has the base 5c11e496d failing 7
tests and erroring 63 there. This plan makes each one pass in the VM or
carry `macos`, and records which and why.

`verify.ran` keeps test ids only, never what the candidate printed, so the
causes below come from reading each test's path against the VM: Debian
bookworm arm64, `run.sh`'s environment, the source from `git archive`, no
`sandbox-exec`, procps `ps`, glibc.

## Each test, its cause, and what is done

| Test | Cause in the VM | Done |
|---|---|---|
| `test_email_*` (63 errors) | Dovecot isn't in the base image, so the `mail` fixture raises (`tests/mailserver.py:161`) | **Dovecot in the base image.** The email tests are plain IMAP/SMTP over loopback and need nothing of macOS, so they belong in the VM |
| `test_mailserver::test_dovecot_does_not_outlive_a_pytest_process_that_was_killed` | Same cause. The child's `Dovecot.start()` raises, the child prints no pid, and `int(b"")` fails the test (`tests/test_mailserver.py:40`) | **Dovecot in the base image** |
| `test_provision_restart_gaps::test_remove_of_a_missing_root_is_nothing_to_clear` | `workspace.remove` → `runs.reap` → `_turn_processes` runs `ps -A -E` (`core/runs.py:380`). `-E` (show environment) is BSD's; procps refuses it, and `check=True` raises | **`macos`** (patch 1; see below) |
| `test_provision_restart_gaps::test_remove_clears_a_dangling_link_at_the_root` | The `ps -E` cause above, then `rmtree` → `_cleared` calls `setattrlistat` through ctypes (`core/workspace.py:2104`), which glibc lacks | **`macos`** (patch 1) |
| `test_provision_restart_gaps::test_workspace_remove_refuses_while_the_task_is_being_provisioned` | Its `finally` calls `rmtree` (`tests/test_provision_restart_gaps.py:127`) → `setattrlistat` | **Portable**: the test clears its plain tree with `shutil.rmtree` (patch 1) |
| `test_objective_tree::test_start_parent_from_the_command_line_on_both_paths` | A refused `start --project` calls `workspace.remove` (`core/__main__.py:428`), which hits both causes above. The CLI then prints a traceback, not `start refused:`, and leaves the task directory behind | **Parametrized** (patch 1): the plain path runs everywhere, the `--project` path carries `macos` |
| `test_ports::test_listen_draws_from_the_span` | The test's last part runs a probe under `sandbox-exec` (`tests/test_ports.py:91`). Everything before it is portable socket logic | **Split.** The span logic stays an unmarked test, and the sandbox probe moves to a test of its own marked `macos`, as the README asks for a parametrized test |
| `test_expiry::test_items_due_start_one_project_task_the_kernel_carries_to_a_held_merge` | It drives the fresh and docs runners, and their `workspace.fetch_into_mirror` requires `sandbox-exec` (`core/workspace.py:1387`, from `core/fresh.py:575`). Every other test that drives those runners already carries `macos` (`tests/test_fresh.py`, `tests/test_docs_runner.py`) | **`macos`**: the fresh runner's sandbox is macOS itself |

## What will be built

1. **Dovecot 2.4.5 in the base image** (`core/images/base/Containerfile`).
   - Bookworm on arm64 ships 2.3.19. Dovecot's own 2.4 repository has amd64 only, and trixie's 2.4.1 would pull in trixie's libc.
   - `tests/mailserver.py` writes a 2.4 config, the version Homebrew installs on both Macs.
   - So the image builds Dovecot 2.4.5 from source, pinned by checksum like uv and Node:
     - Source: `https://dovecot.org/releases/2.4/dovecot-2.4.5.tar.gz`
     - sha256: `868c2686a61b5f8e00a3e4721789b1ab46e6528fd773a5fbed07a6ecba7731e6`, computed from the download on 2026-10-07, recomputed from a second download at build and equal to the sha256 Homebrew's formula pins. Dovecot publishes no `.sha256`. If the builder can get Dovecot's signing key, they also check the `.sig`, and the Containerfile comment says which was done.
   - Build dependencies come from the signed bookworm repository: `libssl-dev` (2.4 requires OpenSSL) and `zlib1g-dev`.
   - Build: `--prefix=/usr/local`, with docs and optional backends off. Source and build tree are removed in the same layer.
   - `dovecot` installs to `/usr/local/sbin`, which `VM_PATH` lacks. It is linked into `/usr/local/bin`, the same way git's exec path is linked now, so `core/container.py` is unchanged.
   - The Containerfile's header comment names Dovecot and why it is built from source.
2. **`core/runs.py` and `core/workspace.py` unchanged** (patch 1). The build first made `_turn_processes` read `/proc/<pid>/environ` and `_cleared` plain `lstat` off Darwin. The review's VM run of that candidate was killed for memory: 4096 MB, peak 3810 MB at 263 s, against the base's 2013 MB in 130 s on the same image. No cause was found by reading, and no Linux was reachable to bisect. Those calls are `ps -E` and `setattrlistat`, which `tests/conftest.py` already counts as macOS itself, and the kernel runs only on the Macs. So the core change was dropped, and the tests that reach them carry `macos` (or are parametrized) instead.
3. (dropped with 2)
4. **Tests.** Split `test_listen_draws_from_the_span` and add `macos` to the expiry test, each with a one-line comment naming what needs macOS.
5. **Docs.**
   - `tests/README.md`: the email tests run in the VM against the base image's Dovecot, and Dovecot comes from Homebrew on the Macs.
   - `m1-4c-verifier.md`, in the "suite in the VM" record: the 7 and 63 above, what was done, and the ledger event.
   - `core/README.md` or `docs/tech-stack.md` only where they list the base image's contents.

No check, gate, hook, validator, review round, or approval step is added.
Nothing here is guarded: each change fixes the code or the environment.

## Out of scope

- Removing `macos` from other tests that `ps -E` may have pushed to macOS, for example `test_provision_restart_gaps.py::test_remove_reaps_what_a_setup_command_left`. Each would need its own VM evidence. The delivery names them.
- pgvector in the base image (`docs/machine.md:441`).
- Any change to `core/container.py`, `run.sh`, or the spec's environment.

## Stakes

If this goes wrong, every verification fails with a `kernel` cause until the image is fixed, or the kernel's process reaping on macOS breaks. Both can be undone with a revert, but the Darwin paths in `core/runs.py` and `core/workspace.py` must stay byte-for-byte as they are.

Critique may send the plan back once. The diagnosis is from reading, and the image choice is the one real design call. Review may send the work back twice, because this touches the kernel's reaping and tree removal and an image only rollout exercises.

## How it will be shown to work

- **On the Mac (the builder's check profile).**
  - The full suite, against the base's list.
  - The Darwin branches must leave every existing `runs`, `workspace`, `provision_restart_gaps`, and `objective_tree` test passing.
  - `test_ports`: the span test and the new `macos` sandbox-probe test both pass on the Mac.
  - Lint.
- **In the VM (the review's own `verify.ran` on this branch).**
  - Of the seven failures, the head run should pass three (`test_workspace_remove_refuses_while_the_task_is_being_provisioned`, the objective-tree test's `plain` parameter, and the span half of `test_ports`). It should skip as `macos` the two `remove` tests, the objective-tree `project` parameter, the expiry test, and the sandbox-probe test. The Dovecot kill test and the 63 email tests still error, as at base, until the kernel builds the new base image. If the head run is still killed for memory, the cause is not this diff, which then changes nothing that runs in the VM except those marks.
  - It runs on the kernel's own base image. `core/container.py` builds from the kernel's `core/images/base/`, never the candidate's. So the email tests and the Dovecot kill test still error at head, and they show as `failing_at_base`.
- **Not verifiable in this job.**
  - The new base image, and the 63 email tests plus the kill test passing in it. The check profile denies `container`, and the review runs on the kernel's image.
  - They are first exercised after merge and the kernel's restart, when the base image's new digest makes the next verification build it.
  - The delivery says so. Running `uv run pytest tests/test_container.py` on Valor's Mac outside the profile before the restart would show whether the image builds. Whether that happens is Tom's call; it is not added here as a step.
  - Once Dovecot is present, any Linux-only failure in the email tests only shows in that run.
