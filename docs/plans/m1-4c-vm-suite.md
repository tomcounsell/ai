# 1.4c follow-up: this repository's suite runs clean in the verification VM

`tests/README.md` and `m1-4c-verifier.md` say this repository's suite runs in
the review's verification VM (`core/container.py`) with only the `macos`
tests skipped. On 2026-10-07, task 6fd4e0439ac1's `verify.ran` (ledger
event 842, `where: "vm"`, release 1.5.0) had the base 5c11e496d failing 7
tests and erroring 63 there. This plan makes each one pass in the VM or
carry `macos`, and records which and why.

`verify.ran` keeps test ids only, never what the candidate printed, so the
causes below come from reading each test's path against the VM: Debian
bookworm arm64, `run.sh`'s environment, the source from `git archive`, no
`sandbox-exec`, procps `ps`, glibc. The base run had also aborted:

- **What happened.** The 63 errors were 62 email tests and `pytest::internal`.
  - `tests/denials.py`'s shared-tmp probe caught only `PermissionError` from `os.listdir("/private/tmp")`. On Linux that path is missing, so the probe raised `FileNotFoundError` inside `pytest_runtest_makereport`, and pytest stopped the session.
  - The run ended after `test_serve::test_a_kill_mid_provision_is_redone_on_restart[SIGTERM]`. That left 234 unmarked tests unrun, from the rest of `test_serve` through `test_workspace`.
- **How the hidden tests were sorted.** They were run on the Mac under the check profile, and those the profile skips for `/bin/ps` were run again with the process listing stubbed. Both were diagnostics, not committed.
  - Two of them hang without reaping, and reading them shows they need macOS: they drive the fresh runner and the test runner, which run under `sandbox-exec`.
  - The rest pass or skip themselves off macOS (node at its Homebrew path).

## Each test, its cause, and what is done

| Test | Cause in the VM | Done |
|---|---|---|
| `pytest::internal`, the session aborted | The shared-tmp probe's `FileNotFoundError` off macOS (above) | **Portable**: a missing `/private/tmp` is not a denial |
| `test_email_*` (62 errors) | Dovecot isn't in the base image, so the `mail` fixture raises (`tests/mailserver.py:161`) | **Dovecot in the base image.** These are plain IMAP/SMTP over loopback |
| `test_mailserver::test_dovecot_does_not_outlive_a_pytest_process_that_was_killed` | Same cause. The child's `Dovecot.start()` raises, and `int(b"")` fails the test | **Dovecot in the base image** |
| `test_provision_restart_gaps::test_remove_of_a_missing_root_is_nothing_to_clear` | `workspace.remove` → `runs.reap` → `_turn_processes` runs `ps -A -E`. procps has no `-E`, and `check=True` raises | **Portable**: `_turn_processes` reads `/proc/<pid>/environ` off Darwin |
| `test_provision_restart_gaps::test_remove_clears_a_dangling_link_at_the_root` | The `ps -E` cause, then `rmtree` → `_cleared` calls `setattrlistat`, which glibc lacks | **Portable**: also `_cleared` is the `lstat` alone off Darwin |
| `test_provision_restart_gaps::test_workspace_remove_refuses_while_the_task_is_being_provisioned` | Its `finally` calls `rmtree` → `setattrlistat` | **Portable** (`_cleared`) |
| `test_objective_tree::test_start_parent_from_the_command_line_on_both_paths` | A refused `start --project` calls `workspace.remove`, which hits both causes | **Portable** (both) |
| `test_ports::test_listen_draws_from_the_span` | Its last part runs a probe under `sandbox-exec` | **Split**: the span logic stays unmarked, and the probe is a `macos` test of its own |
| `test_expiry::test_items_due_start_one_project_task_the_kernel_carries_to_a_held_merge` | It drives the fresh and docs runners, which fetch under `sandbox-exec`. Every other test that drives them carries `macos` | **`macos`** |
| `test_slot_priority::test_a_background_critique_preempted_for_tom_reruns_with_no_verdict` (hidden) | The fresh critique runner | **`macos`** |
| `test_slot_priority::test_a_background_check_preempted_for_tom_is_cancelled_with_no_verdict_and_runs_again` (hidden) | The test runner runs the suite under `sandbox-exec` | **`macos`** |
| Seven `test_session.py` tests that give a turn oversized output (hidden) | Turn collection copies a 260 MB output many times. One of these alone peaks at 4.2 GB in pytest on the Mac, past the VM's 4 GB | **Skipped off macOS, temporarily** (`OVERSIZED`, reason "needs task d823cd5f0214: oversized turn output is copied in memory"). Tom's call: the VM stays at 4 GB, and task d823cd5f0214 removes the copies and the skip |

## What is built

1. **Dovecot 2.4.5 in the base image** (`core/images/base/Containerfile`).
   - Bookworm on arm64 ships 2.3, and Dovecot's own 2.4 repository has amd64 only. `tests/mailserver.py` writes a 2.4 config, the version Homebrew installs on the Macs.
   - The image builds it from `https://dovecot.org/releases/2.4/dovecot-2.4.5.tar.gz`, sha256 `868c2686a61b5f8e00a3e4721789b1ab46e6528fd773a5fbed07a6ecba7731e6`. That was computed from two downloads, and it equals the sha256 in the Homebrew formula installed on the build Mac (`/opt/homebrew/Cellar/dovecot/2.4.5/.brew/dovecot.rb`). The `.sig` is not checked.
   - It adds `libssl-dev` and `zlib1g-dev`, turns optional backends off, and links `dovecot` into `/usr/local/bin`, which is on `VM_PATH`.
2. **`tests/denials.py`**: the shared-tmp probe treats a missing `/private/tmp` as not denied.
3. **`core/runs.py::_turn_processes`**: `ps -E` on Darwin, as before. Elsewhere it uses `ps` without `-E`, and the mark is an exact entry of `/proc/<pid>/environ`; a process that is gone or unreadable carries none.
4. **`core/workspace.py::_cleared`**: `setattrlistat` on Darwin, as before. Elsewhere it is the `lstat` alone, since no owner-clearable flag or ACL of that kind exists.
5. **Tests.**
   - The marks and the split above.
   - The `OVERSIZED` skip.
   - Two portable tests:
     - Reap by an environment mark: an exact match only, so a longer id or no mark is left running, and a process that is gone, or pid 1, carries none.
     - `rmtree` of mixed modes and links, leaving what a link names.
6. **Docs.**
   - `tests/README.md`: where Dovecot comes from, `ps -E` and `setattrlistat` among macOS's own, and the temporary oversized-output exception.
   - `m1-4c-verifier.md`: Dovecot in the base image's contents.

No check, gate, hook, validator, review round, or approval step is added.

## Out of scope

- Removing `macos` from tests the portable `ps` and `rmtree` may now let run on Linux (`test_remove_reaps_what_a_setup_command_left`, the plain `test_rmtree_*`). Each needs its own VM evidence.
- The copies in turn collection (task d823cd5f0214).
- pgvector in the base image.
- `core/container.py`, `run.sh`, `verify_memory_mb`.

## How it is shown to work

- **On the Mac (check profile):** the full suite against base's list, and lint.
- **In the VM** (the review's `verify.ran`, on the kernel's own base image, which has no Dovecot yet):
  - no `pytest::internal`;
  - every collected test reported;
  - the seven named failures pass or skip as `macos` as the table says;
  - the oversized tests skip.
  - The 62 email tests and the Dovecot kill test still error, as at base, until the kernel builds the new base image after merge and restart. Any other failure is a test the Mac could not show, and it is the next thing to fix.
- **Not verifiable in this job:**
  - the new base image;
  - the email tests on Linux;
  - whether the full suite's peak fits 4 GB with the oversized tests skipped.
- **Recommended before merge:** run `uv run pytest tests/test_container.py` on the Mac outside the check profile.
