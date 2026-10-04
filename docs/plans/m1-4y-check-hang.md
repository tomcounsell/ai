---
tracking: none
slug: m1-4y-check-hang
type: bugfix
status: built
critique_rounds: 0
review_rounds: 0
---

# 1.4y: the valor suite waits for ever in the test check

Found by the resident kernel's first test check of the `valor` project
(task faa972801f4b, base run at 093b4f7b63ce).

## Incident

The base run `uv run pytest -q -p no:cacheprovider --junitxml=...` under
the check profile used 2 s of CPU in 1.5 hours; its output files were
empty, and the one thread left waited on a lock in a class `__init__`
during collection. Task b7adaf2a23d1 waited for the turn slot behind it.

Reproduced outside the kernel: a copy of the check directory, the same
argv, environment and profile (paths moved to the copy), a Postgres of the
copy's own, and a `sitecustomize` calling `faulthandler.dump_traceback_later`
into a file. The stack:

```
tests/judgement_upstream.py:136 in __init__   (self._ready.wait())
tests/judgement_upstream.py:274 in shared
tests/scripted.py:202 in judge
tests/scripted.py:206 in <module>
tests/test_checks.py:33 in <module>
```

## Defects

1. `tests/judgement_upstream.py`: `Upstream` starts its server on a
   thread and waits for an event the thread sets after it listens. The
   check profile allows binding only the dev ports (8000 to 8009), so the
   listen on `127.0.0.1:0` raised `PermissionError` on the thread, the
   thread ended, and the event was never set. pytest's capture took the
   thread's traceback, so nothing showed.
2. `core/workspace.py` `check_profile`: the kernel opens each step's
   output file beside the check directory (`<check>.<step>.out`), which
   the profile denies as part of the work directory. Writes through the
   inherited descriptor work, but `fstat` on it is refused. pytest takes a
   descriptor it cannot `fstat` for closed: it puts `/dev/null` on fd 1
   and 2 and closes them when capture ends, so every pytest suite run by
   the kernel lost its whole output and exited 120.

## Fix

1. `Upstream._serve` keeps a failure to listen and sets the event in all
   cases; `Upstream.__init__` raises that failure.
2. `check_profile` allows `file-read-metadata` on
   `<check_dir>.<anything>.out`, as written and resolved. The step's
   output file stays outside the check directory and unreadable to it.

## Tests

- `tests/test_judgement.py::test_an_upstream_that_cannot_listen_raises_instead_of_waiting_for_ever`:
  an `Upstream` on a port another socket holds raises `OSError`. Without
  fix 1 it was still waiting after 60 s.
- `tests/test_workspace.py::test_a_check_step_can_fstat_the_output_file_the_kernel_opened_for_it`:
  under the real check profile, a child `fstat`s its stdout, the
  kernel-opened `setup-0`, `suite` and `lint` output files, and writes to
  it. Without fix 2 it fails with `PermissionError`.

## What the fix leaves to the lead

With both fixes the valor suite no longer hangs in the check, but under
the check profile it cannot run as written:

- 25 test modules bind `127.0.0.1:0` at import (the shared judgement
  upstream and others) and connect to it; the profile allows binding only
  the dev ports and reaching only the dev ports, the gateway and the
  task's service ports. pytest stops at 25 collection errors and runs no
  test, so base and head both show only those errors.
- Every test that runs `sandbox-exec` fails: a sandboxed process cannot
  apply a second sandbox (`sandbox_apply: Operation not permitted`).

A base run at a commit without fix 1 still waits for ever, so task
faa972801f4b's base run at 093b4f7b63ce never ends by itself.

## Records

- Round 1 (build): both fixes and both tests. Suite 1415 passed, 25 skipped; ruff check and format clean.

## Decided by default (lead, 2026-10-05)

- Kernel tasks faa972801f4b and b7adaf2a23d1 are stopped by the lead. The
  first's base check could never end (its base lacks fix 1); the second's
  would hang the same way. b7adaf2a23d1's candidate 5a5ae487f is carried by
  subagent checks on branch k-b7adaf2a-n1-n3. faa972801f4b was a cosmetic
  display fix ($1.18 spent); its work directory went with the stop, and it is
  started again as a follow-up once valor's suite can run in a check.
- Valor's own suite cannot run under the host check profile even with both
  fixes: 25 modules bind loopback at import, and a sandboxed process cannot
  start a second sandbox. The check would pass with zero coverage. The
  answer is not to widen the host profile: valor's test check runs in the
  container (1.4c part two), where loopback is the container's own and the
  macOS-only tests are marked. Until 1.4c part two merges, valor tasks keep
  running through subagents, and the lead's merge suite is the test.
