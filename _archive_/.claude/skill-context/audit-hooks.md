# audit-hooks context — this repo (ai)

This repo's conventions for the `/audit-hooks` checks. The global skill body runs a generic
baseline; this file supplies the repo-specific declarations.

## Validator inventory (Rule 3 — must NOT have `|| true`)

Authoritative list: every script under `.claude/hooks/validators/` (all named `validate_*.py`).
Enumerate that directory at audit time rather than trusting any static list — validators are
added frequently. Anything registered in `.claude/settings.json` that points into
`validators/` is a validator; everything else is advisory or Stop.

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
