# Routine jobs find uv

**Defect.** The verify routine's suite at 5728a8a47 failed setup with
`uv: command not found`. `routines.plist` (`core/routines.py:161`) gives
every routine job `PATH=/usr/bin:/bin:/usr/sbin:/sbin`; the routine's
check environment inherits it (`workspace.harness_env`), so the project's
`setup = ["uv sync --frozen"]` cannot find `/opt/homebrew/bin/uv`. The
plist on disk on the Cowboy was hand-edited to add `/opt/homebrew/bin`
(rollout.md step 9 asks for that by hand) but the loaded job still has the
bare PATH (`launchctl print gui/$UID/com.valor.routine.verify`). The
kernel's own job only works for the same hand-edit: `serve.plist` adds the
dirs of `claude`, `git` and Postgres, never `uv`'s.

**Fix.** One PATH for the kernel's and the routines' launchd jobs, since a
routine runs the same checks the kernel does: move `serve.plist`'s PATH
composition into a function both call, and add `dirname(settings.uv)` to
it. Rollout step 9's hand instruction becomes a statement of what the
plist carries. `backup.plist` runs no project tool and is out of scope.

**After merge.** Re-print the verify, conditions and expiry plists from the
merged checkout and re-bootstrap them, then confirm with `launchctl print`
that the loaded PATH holds `/opt/homebrew/bin`. If the turn cannot run
`launchctl`, the delivery names it as open.

**Tests** (`tests/test_machine.py`, no plist test exists today):
- a routine plist's PATH holds the directory of `settings.uv`, and of
  `settings.git_bin` and `settings.pg_bin`;
- the routine and kernel plists carry the same PATH;
- with `VALOR_UV` pointed elsewhere, that directory is the one on PATH;
- the system directories stay first, so nothing a turn can write
  shadows `/usr/bin` tools.

No governance added. Bug fix: no critique or review round.
