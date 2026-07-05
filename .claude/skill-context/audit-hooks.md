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
