# Hook Best Practices

Authoritative reference for Claude Code hook safety patterns. Used by the `/audit-hooks` skill and as a human-readable guide.

**Source of truth:** [Claude Code hooks documentation](https://docs.anthropic.com/en/docs/claude-code/hooks)

---

## Rules

### 1. Stop hooks MUST have `|| true`

A failing Stop hook blocks session exit entirely. The user's session hangs with no recourse.

**Good:**
```json
{"command": "python .claude/hooks/stop.py || true", "timeout": 10}
```

**Bad:**
```json
{"command": "python .claude/hooks/stop.py", "timeout": 10}
```

**Rationale:** Stop hooks are observational (logging, calendar events, memory extraction). They must never prevent the session from ending.

---

### 2. Advisory hooks MUST have `|| true`

Advisory hooks observe but do not enforce. They include: memory recall/extraction, calendar logging, SDLC reminders, pre-tool-use logging.

**How to identify:** If the hook's purpose is logging, tracking, or enrichment (not validation), it is advisory.

---

### 3. Validator hooks MUST NOT have `|| true`

Validator hooks exist to block invalid operations. Adding `|| true` defeats the purpose.

**How to identify:** scripts named `validate_*.py` registered on PreToolUse/PostToolUse with a matcher. A repo's `.claude/skill-context/audit-hooks.md` may declare an explicit validator inventory — treat that list as authoritative.

---

### 4. All `|| true` hooks MUST call `log_hook_error()` on failure

Silent failure is invisible failure. Every advisory hook must log errors to `logs/hooks.log`.

**Good:**
```python
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_hook_error("my_hook", str(e))
```

**Bad:**
```python
if __name__ == "__main__":
    main()  # errors swallowed by || true
```

**Utility:** the repo's error-logging helper (default: `log_hook_error()` in `.claude/hooks/hook_utils/constants.py`, writing `YYYY-MM-DD HH:MM:SS - hook_name - ERROR - message` to `logs/hooks.log`). The repo context file may declare a different helper or log path.

---

### 5. Bash hooks MUST use `set +e`

`set -e` causes the entire hook to exit on any subcommand failure, even benign ones (like `grep` finding no matches). In a hook context, this leads to silent exits.

**Good:**
```bash
#!/bin/bash
set +e
# ... commands ...
```

**Bad:**
```bash
#!/bin/bash
set -e
# one failed grep kills the whole hook
```

---

### 6. Bash hooks MUST NOT use bare `exec`

`exec` replaces the shell process, preventing any error recovery or logging after the exec'd command.

**Good:**
```bash
python "$CLAUDE_PROJECT_DIR/.venv/bin/my_tool" "$@"
exit_code=$?
if [ $exit_code -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - my_hook - ERROR - exit code $exit_code" >> logs/hooks.log
fi
```

**Bad:**
```bash
exec python my_tool "$@"
# nothing runs after this
```

---

### 7. Shell hooks MUST prefer venv binaries

Hook scripts run as subprocesses with the system PATH. Project-specific tools installed in `.venv/` are not on PATH unless explicitly referenced.

**Good:**
```bash
if [ -x "$CLAUDE_PROJECT_DIR/.venv/bin/my-tool" ]; then
    "$CLAUDE_PROJECT_DIR/.venv/bin/my-tool" "$@"
else
    python -m tools.my_tool "$@"
fi
```

**Bad:**
```bash
my-tool "$@"  # may not be on PATH
```

---

### 8. Python hooks MUST minimize imports

Hooks run on every tool invocation. Heavy imports (anthropic, openai, pandas, numpy, requests) add latency to every operation.

**Known heavy modules** (>50ms import time): `anthropic`, `openai`, `pandas`, `numpy`, `httpx`, `pydantic`

**Good:**
```python
def main():
    # Import heavy modules only when needed
    from anthropic import Anthropic
    client = Anthropic()
```

**Bad:**
```python
import anthropic  # 100ms+ on every hook invocation
import pandas     # 200ms+ for unused import

def main():
    pass
```

---

### 9. Hook timeouts MUST match expected workload

| Workload | Recommended timeout |
|----------|-------------------|
| Simple file reads, JSON parsing | 5s |
| Git operations, local tool calls | 10s |
| API calls (calendar, memory) | 15s |

Timeouts that are too short cause hooks to be killed mid-operation. Timeouts that are too long delay session responsiveness when hooks hang.
