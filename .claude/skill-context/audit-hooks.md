# audit-hooks context — this repo (ai)

## Deterministic half already runs

`reflections/audits/hooks_audit.py` checks both settings scopes on a schedule: parse errors,
crash guards on Stop/SubagentStop, script existence, and agent-frontmatter hooks. Use its
logic as ground truth for those checks, and spend the audit on classification and rules 4-8.
Registrations are generated from `.claude/hooks/manifest.toml`, so fixes go there, never into
either settings file by hand.

## Validator inventory (Rule 3)

Every script under `.claude/hooks/validators/` (`validate_*.py`) is a validator. Enumerate the
directory at audit time, because validators are added often. Anything registered that points
elsewhere is advisory or Stop.

## Crash-guard forms (Rules 1-2)

Hooks generated with the `deny-only` exit policy end in
`__hook_rc=$?; [ "$__hook_rc" = 2 ] && exit 2 || exit 0`. That satisfies the crash-guard
requirement: it passes a deliberate exit 2 and maps every other exit to 0. Never recommend
adding `|| true` to it, because that would disable its deny.

## Error logging (Rule 4)

- Helper: `log_hook_error(hook_name, error)` in `.claude/hooks/hook_utils/constants.py`
- Log path: `logs/hooks.log`
- Every advisory and Stop hook must call it from a `try/except` at `__main__` level.

## Venv binaries (Rule 7)

Project CLIs are console scripts under the `valor-*` prefix in `.venv/bin/` — hooks must
reference them as `$CLAUDE_PROJECT_DIR/.venv/bin/valor-<name>`, never bare names on PATH.

## Interpreter token (issue #2503)

Hook commands are generated, never hand-written, and the interpreter is chosen by **scope**:

- Project scope → `"$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_python`
- Global scope → an absolute system `python3` resolved per machine at generation time

A generated command starting with a bare `python` is a **FAIL**: it exits 127 under the
non-interactive `/bin/sh` Claude Code runs hooks through, which silently disables the guard
rather than failing loudly. The single sanctioned exception is
`scripts/update/migrations.py::_legacy_fork_command_prefix()`, whose bare `python` is a match
key against bytes already on disk from before this contract existed — it must never be
updated to track the generator.

Global-scope scripts (`.claude/hooks/sdlc/`) must additionally be importable and runnable
under `MIN_GLOBAL_PYTHON` (3.9): stdlib-only, no PEP 604 `X | None` without
`from __future__ import annotations`, no `datetime.UTC` / `tomllib` / `typing.Self` /
`ExceptionGroup` / `asyncio.timeout`.

**Rule 6 carve-out.** `.claude/hooks/hook_python` uses `exec` deliberately — it is an
interpreter shim, not a hook. `exec` is what keeps the wrapped hook's exit code intact
(preserving `blocking = true` semantics) and leaves no extra process behind. Do not report it
as a bare-`exec` violation. It is extensionless on purpose so the audit's script-path check
inspects the wrapped hook rather than the shim.

Its fail-open branch logs `hook_python: no repo venv interpreter found` to `logs/hooks.log`;
`reflections/audits/hooks_audit.py` raises a dedicated finding on that marker. Treat a hit as
"project hooks are silently disabled on this machine", not as generic hook-error noise.

Details: [`docs/features/hook-manifest.md`](../../docs/features/hook-manifest.md#interpreter-contract).
