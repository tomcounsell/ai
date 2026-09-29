# Hook Rules

The rules `/audit-hooks` checks. Source of truth for hook semantics:
https://docs.anthropic.com/en/docs/claude-code/hooks

1. **Stop and SubagentStop hooks need a crash guard.** A failing Stop hook blocks session exit
   and leaves the session hung. The guard is `|| true` or an equivalent exit-code capture the
   repo declares.
2. **Advisory hooks need a crash guard.** Logging, tracking, and enrichment hooks observe; a
   crash must not block the tool call.
3. **Validators must not have a crash guard.** Exit code 2 is the block, and `|| true`
   swallows it. A repo's skill-context validator inventory is authoritative.
4. **Guarded hooks must log their failures.** A `try/except` at `__main__` calls the repo's
   error helper (default `log_hook_error(hook_name, message)` writing to `logs/hooks.log`).
   A guard without logging makes failure invisible.
5. **Bash hooks use `set +e`, not `set -e`.** Under `set -e`, a benign non-zero exit (a
   `grep` with no match) kills the hook silently.
6. **No bare `exec` in bash hooks.** It replaces the shell, so no logging or recovery runs
   afterwards. Capture `$?` and log instead.
7. **Use venv binaries, not bare names.** Hooks run with the system PATH, where project tools
   in `.venv/bin/` are missing. Reference `$CLAUDE_PROJECT_DIR/.venv/bin/<tool>`.
8. **Keep heavy imports out of module scope.** Hooks run on every matching tool call.
   `anthropic`, `openai`, `pandas`, `numpy`, `httpx`, and `pydantic` each add 50ms or more,
   so import them inside the function that needs them.
9. **Timeouts fit the workload.** About 5s for file reads and JSON, 10s for git and local
   tools, 15s for API calls. Too short kills a hook mid-operation; too long stalls the session
   when a hook hangs.
