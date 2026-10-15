# Plan: read the process table in-process, not through setuid `/bin/ps`

Task 73d4f1ce9971. Ten tests red at main d814880d6604 with
`PermissionError: [Errno 1] Operation not permitted: '/bin/ps'`.

## The cause

The verify check runs the suite under the turn's sandbox-exec profile
(`core/checks.py`, `workspace.sandboxed`). macOS refuses `exec` of a setuid
binary from inside any sandbox, and `/bin/ps` is setuid root
(`-rwsr-xr-x root`). Reproduced in this turn, which runs under the same
profile: `/bin/ps -p $$` fails with "operation not permitted".

Every turn's end calls `core.runs.reap`, which lists processes with
`/bin/ps -A -E`. So any test that runs a turn (broker, pipeline, kernel
stop, reap, rollout's recover, serve) dies there, and two test helpers
(`tests/test_reap.py:_alive`, `tests/test_serve.py:gone`) exec `ps` too.
The kernel in production runs unsandboxed, so it is not broken there; the
defect is that the reaper depends on a privilege the suite's environment,
and any sandboxed caller, does not have. The same class was reported at
fccf34305787 (task 1c807db81cba).

## What will be built

A process table read in-process through libproc and `sysctl`, which need
no privilege for this user's processes and work inside the sandbox
(prototyped in this turn, under the profile):

- `proc_listallpids` for the listing;
- `proc_pidinfo(PROC_PIDT_SHORTBSDINFO)` for pgid and uid (an exited or
  zombie pid reads as absent: ESRCH);
- `sysctl(KERN_PROCARGS2)` for argv and the initial environment. For this
  user's processes it returns the environment, except for platform
  binaries such as `/bin/sleep`, which hide it; `docs/harnesses.md`
  already says so, and the sandbox mark covers them.

Where: a small section in `core/runs.py` beside `_sandbox_check` (the
existing ctypes idiom), with one public `alive(pid)` the tests use. Then:

1. `_turn_processes`, `sandboxed_pids`, `marked_services`, `_commands` read
   that table instead of `ps`. The three marks (pgid, `VALOR_TURN=<turn>`
   in the environment, the mach-name sandbox mark) are unchanged.
2. `workspace._group` (the check's peak-footprint sampler) reads the same
   table.
3. Delete `binaries.PS`, and the off-macOS `/proc/<pid>/environ` branch
   (`_environ_marked`, the `darwin` switch): `docs/tech-stack.md` says no
   Linux assumptions, and libproc is macOS-only, as `_sandbox_check` is.
4. `tests/test_reap.py:_alive` and `tests/test_serve.py:gone` use
   `runs.alive` instead of exec'ing `ps`.
5. `docs/harnesses.md` mark 2 says where the environment is read from;
   any README naming `ps` follows.

No check, gate, hook, or review step is added.

## Out of scope

- The check's sandbox profile. Letting `/bin/ps` run as root from inside a
  turn's sandbox would let a turn read every process's environment,
  the kernel's secrets included. Not done.
- The other sandbox-environment reds (socket bind EPERM in
  `test_gateway_meter`, `test_memory`) belong to their own tasks.

## Tests that show it works

All run by the builder inside this turn's sandbox, which is the verify
check's environment, and the full suite run once at the end:

- The ten named tests pass: `test_broker` (2), `test_kernel` stop,
  `test_pipeline` (2), `test_reap` (2), `test_rollout` core merge,
  `test_serve` (2).
- The rest of `tests/test_reap.py` passes, including the bystander left
  alive and the cancelled-again turn.
- New unit tests of the process table in `tests/test_reap.py`:
  - a child started with `VALOR_TURN=<id>` in its environment and in a new
    session (`setsid`) is found by `reap` by the environment mark alone,
    and its argv is the `command` in the reaped row;
  - a child in the turn's process group with no mark is found by pgid;
  - a process of another user (pid 1, root) is never listed, whatever its
    group;
  - the caller's own pid is never listed;
  - `alive` is false for an exited-but-unwaited child (zombie) and for a
    reaped pid, true for a running one;
  - an empty pid list gives no commands and does not fail.
- Grep: no `ps` exec remains in `core/` or `tests/`.

## Stakes

The reaper is how a turn's processes end with the turn; a wrong pgid or
uid read either leaves daemons running or kills a process the turn did
not start. It is kernel code, reversible by revert.
