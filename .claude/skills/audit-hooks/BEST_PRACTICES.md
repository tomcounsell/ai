# Hook Best Practices

Codified rules for Claude Code hooks in this repository. Each rule includes rationale and examples to guide both authoring and auditing.

---

## Rule 1: Stop hooks MUST have `|| true`

**ID:** `stop-must-or-true`

**Rationale:** A failing Stop hook blocks the session from exiting. The agent cannot recover, the user cannot interact, and the session hangs until the hook times out. Stop hooks perform cleanup and reporting -- they must never prevent session termination.

**Good:**
```json
{
  "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/stop.py --chat || true",
  "timeout": 10
}
```

**Bad:**
```json
{
  "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/stop.py --chat",
  "timeout": 10
}
```

---

## Rule 2: Advisory hooks MUST have `|| true`

**ID:** `advisory-must-or-true`

**Rationale:** Advisory hooks perform logging, memory extraction, calendar updates, SDLC tracking, and other side effects. They inform but do not enforce. If an advisory hook fails, the agent should continue working -- blocking on a logging failure is worse than missing a log entry.

**Advisory hooks in this repo:** `user_prompt_submit.py`, `pre_tool_use.py`, `post_tool_use.py`, `sdlc_reminder.py`, `stop.py`, `calendar_hook.sh`, `calendar_prompt_hook.sh`, `subagent_stop.py`, `validate_sdlc_on_stop.py`

**Good:**
```json
{
  "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/post_tool_use.py || true",
  "timeout": 5
}
```

**Bad:**
```json
{
  "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/post_tool_use.py",
  "timeout": 5
}
```

---

## Rule 3: Validator hooks MUST NOT have `|| true`

**ID:** `validator-no-or-true`

**Rationale:** Validator hooks exist to block bad actions -- writing invalid plans, committing without required fields, merging without approval. Adding `|| true` silently swallows the validation failure and allows the bad action through. The hook becomes decorative.

**Validator hooks in this repo:** `validate_commit_message.py`, `validate_merge_guard.py`, `validate_documentation_section.py`, `validate_test_impact_section.py`, `validate_file_contains.py`, `validate_features_readme_sort.py`

**Good:**
```json
{
  "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_commit_message.py",
  "timeout": 10
}
```

**Bad:**
```json
{
  "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_commit_message.py || true",
  "timeout": 10
}
```

---

## Rule 4: All `|| true` hooks MUST call `log_hook_error()` on failure

**ID:** `log-on-failure`

**Rationale:** `|| true` suppresses the exit code, making failures invisible. Without explicit error logging inside the hook, you have no way to know it failed, how often, or why. Silent failure is invisible failure -- it erodes trust in the system over time.

**Good:**
```python
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_hook_error("post_tool_use", e)
        sys.exit(0)  # advisory hook, don't block
```

**Bad:**
```python
if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # silently swallowed
```

---

## Rule 5: Bash hooks MUST use `set +e`

**ID:** `bash-no-set-e`

**Rationale:** `set -e` causes the shell to exit immediately on any non-zero return code from a subcommand. This prevents error recovery, cleanup, and logging. A single failing `grep` or `curl` kills the entire hook before it can report what went wrong.

**Good:**
```bash
#!/bin/bash
set +e  # allow subcommand failures for error handling

result=$(curl -s "$API_URL" 2>/dev/null)
if [ $? -ne 0 ]; then
    echo "API call failed, continuing gracefully" >&2
fi
```

**Bad:**
```bash
#!/bin/bash
set -e  # any failure kills the hook immediately

result=$(curl -s "$API_URL")  # if this fails, hook exits with no logging
process_result "$result"
```

---

## Rule 6: Bash hooks MUST NOT use bare `exec`

**ID:** `no-bare-exec`

**Rationale:** `exec` replaces the current shell process with the exec'd command. Any code after `exec` never runs -- including error handling, cleanup, and logging. If the exec'd process fails, there is no shell left to catch or report the failure.

**Good:**
```bash
#!/bin/bash
python "$CLAUDE_PROJECT_DIR/.claude/hooks/my_hook.py" "$@"
exit_code=$?
if [ $exit_code -ne 0 ]; then
    echo "Hook failed with code $exit_code" >&2
fi
exit $exit_code
```

**Bad:**
```bash
#!/bin/bash
exec python "$CLAUDE_PROJECT_DIR/.claude/hooks/my_hook.py" "$@"
# This line NEVER executes -- no error recovery possible
echo "This is dead code" >&2
```

---

## Rule 7: Shell hooks MUST prefer venv binaries

**ID:** `venv-first`

**Rationale:** System Python and system-installed packages may differ from the project's virtual environment. Using system `python` risks importing wrong dependency versions, missing packages, or running a different Python version entirely. Always resolve binaries from the project venv first.

**Good:**
```bash
#!/bin/bash
PYTHON="${CLAUDE_PROJECT_DIR}/.venv/bin/python"
"$PYTHON" "$CLAUDE_PROJECT_DIR/.claude/hooks/my_hook.py" "$@"
```

**Bad:**
```bash
#!/bin/bash
python "$CLAUDE_PROJECT_DIR/.claude/hooks/my_hook.py" "$@"
# 'python' could be system Python 2.7, Homebrew Python 3.12, or anything else in PATH
```

---

## Rule 8: Python hooks MUST minimize imports

**ID:** `lazy-imports`

**Rationale:** Hooks run on every tool invocation or prompt submission. A hook that takes 500ms to import `anthropic` or `pandas` at the top level adds 500ms to every single agent action. Keep baseline import time under 50ms by lazy-importing heavy modules only when actually needed.

**Known heavy modules:** `anthropic`, `openai`, `pandas`, `numpy`, `torch`, `transformers`, `pydantic`, `sqlalchemy`, `boto3`

**Good:**
```python
import sys
import json  # stdlib is fast

def main():
    # Only import heavy modules when the code path actually needs them
    if needs_api_call:
        from anthropic import Anthropic
        client = Anthropic()
```

**Bad:**
```python
import sys
import json
import anthropic  # ~200ms import, runs on EVERY hook invocation
import pandas     # ~400ms import, only used in one rare code path
```

---

## Rule 9: Hook timeouts MUST match expected workload

**ID:** `timeout-match`

**Rationale:** No timeout means infinite hang risk -- a hook waiting on a dead API endpoint blocks the agent forever. Too-short timeouts cause legitimate work to be killed mid-execution. Match the timeout to the hook's actual workload.

**Guidelines:**
- **5s** for simple file I/O, JSON parsing, local checks
- **10-15s** for API calls, network requests, database queries
- **Never omit timeout** -- the default is no timeout, which means infinite hang risk

**Good:**
```json
{
  "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_commit_message.py",
  "timeout": 10
}
```

**Bad:**
```json
{
  "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_commit_message.py"
}
```
