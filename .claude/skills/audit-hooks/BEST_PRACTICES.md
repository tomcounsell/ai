# Hook Best Practices

Authoritative reference for Claude Code hook safety patterns in this project. Used by the `/audit-hooks` skill and as a human-readable guide.

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

### 2. SubagentStop hooks MUST have `|| true`

Same reasoning as Stop hooks — a failing SubagentStop hook blocks subagent completion.

---

### 3. Advisory hooks MUST have `|| true`

Advisory hooks observe but do not enforce. They include: memory recall/extraction, calendar logging, SDLC reminders, pre-tool-use logging.

**How to identify:** If the hook's purpose is logging, tracking, or enrichment (not validation), it is advisory.

---

### 4. Validator hooks MUST NOT have `|| true`

Validator hooks exist to block invalid operations. Adding `|| true` defeats the purpose.

**Validators in this project:**
- `validate_commit_message.py` — blocks commits with bad messages
- `validate_merge_guard.py` — blocks unauthorized merges
- `validate_documentation_section.py` — blocks plans without docs section
- `validate_test_impact_section.py` — blocks plans without test impact
- `validate_file_contains.py` — blocks plans missing required sections
- `validate_features_readme_sort.py` — blocks unsorted README entries

---

### 5. All `|| true` hooks MUST call `log_hook_error()` on failure

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

**Utility:** `log_hook_error()` is in `.claude/hooks/hook_utils/constants.py`. It writes `YYYY-MM-DD HH:MM:SS - hook_name - ERROR - message` to `logs/hooks.log`.

---

### 6. Bash hooks MUST use `set +e`

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

### 7. Bash hooks MUST NOT use bare `exec`

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

### 8. Shell hooks MUST prefer venv binaries

Hook scripts run as subprocesses with the system PATH. Project-specific tools installed in `.venv/` are not on PATH unless explicitly referenced.

**Good:**
```bash
if [ -x "$CLAUDE_PROJECT_DIR/.venv/bin/valor-calendar" ]; then
    "$CLAUDE_PROJECT_DIR/.venv/bin/valor-calendar" "$@"
else
    python -m tools.calendar_tool "$@"
fi
```

**Bad:**
```bash
valor-calendar "$@"  # may not be on PATH
```

---

### 9. Python hooks MUST minimize imports

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

### 10. Hook timeouts MUST match expected workload

| Workload | Recommended timeout |
|----------|-------------------|
| Simple file reads, JSON parsing | 5s |
| Git operations, local tool calls | 10s |
| API calls (calendar, memory) | 15s |

Timeouts that are too short cause hooks to be killed mid-operation. Timeouts that are too long delay session responsiveness when hooks hang.
