---
name: audit-hooks
description: "Audit Claude Code hooks for safety patterns, error handling, and best practices compliance. Use when reviewing hook health or after adding/modifying hooks."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash
---

# Hook Audit

Inspects `.claude/settings.json` and all referenced hook scripts to surface safety violations against the project's codified best practices. Designed for human-in-the-loop review sessions.

## What this skill does

1. Read `BEST_PRACTICES.md` from this skill directory to load the 9 audit rules
2. Parse `.claude/settings.json` (and `.claude/settings.local.json` if present) to enumerate all hook entries
3. For each hook, run the checks described below
4. Output a structured findings report organized by severity
5. Pause for discussion — do NOT auto-fix anything

## Audit Checks

### 1. `|| true` Correctness

For each hook command in settings.json:

- **Stop and SubagentStop hooks**: MUST have `|| true` (Rule 1)
- **Advisory hooks** (empty matcher on UserPromptSubmit, PreToolUse, PostToolUse; also `sdlc_reminder.py`, `calendar_hook.sh`, `calendar_prompt_hook.sh`): MUST have `|| true` (Rule 2)
- **Validator hooks** (filenames matching `validate_*.py`, `validate_*.sh`): MUST NOT have `|| true` (Rule 3)

### 2. Error Logging Coverage

For each Python hook script with `|| true` in its settings.json command:

- Check that the `if __name__ == "__main__"` block has a `try/except` that calls `log_hook_error()` or writes to `logs/hooks.log` (Rule 4)
- Flag bare `except: pass` or `except Exception: pass` without logging

### 3. Bash Safety

For each `.sh` hook script:

- Check for `set -e` — flag as FAIL, should be `set +e` (Rule 5)
- Check for bare `exec` — flag as WARN (Rule 6)
- Check if it uses `python` or `python3` directly instead of `$CLAUDE_PROJECT_DIR/.venv/bin/python` (Rule 7)

### 4. Import Weight

For each Python hook script:

- Scan top-level imports (not inside functions) for known heavy modules: `anthropic`, `openai`, `pandas`, `numpy`, `torch`, `transformers`, `pydantic`, `sqlalchemy`, `boto3`
- Flag any top-level heavy import as WARN (Rule 8)
- Lazy imports inside functions are fine

### 5. Timeout Appropriateness

For each hook entry in settings.json:

- Missing `timeout` field: flag as WARN (Rule 9)
- Timeout < 3s for hooks that make API/network calls: flag as WARN
- Timeout > 30s: flag as WARN (likely misconfigured)

### 6. Deployment Readiness

For each hook command:

- Extract the script path from the command string
- Verify the file exists on disk
- For Python scripts: verify the file is syntactically valid (`python -m py_compile`)
- Flag missing or broken scripts as FAIL

## How to Run

Read `.claude/settings.json`. For each event type (UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop):

1. Extract all hook entries with their matchers, commands, and timeouts
2. For each hook command, resolve the script path (handle `$CLAUDE_PROJECT_DIR` substitution using the current project root)
3. Read the script file
4. Run each applicable check from the list above
5. Collect findings

Then present the report.

## Output Format

```
## Hook Audit Report

### Hooks Scanned
- Stop/"": python .claude/hooks/stop.py --chat || true (timeout: 10s)
- PreToolUse/"Bash": python .claude/hooks/validators/validate_commit_message.py (timeout: 10s)
...

### Findings

#### FAIL
- [stop-must-or-true] Stop/"": stop.py missing || true
- [deployment] PreToolUse/"": referenced script .claude/hooks/missing.py does not exist

#### WARN
- [lazy-imports] PostToolUse/"": post_tool_use.py imports `anthropic` at top level
- [timeout-match] UserPromptSubmit/"": calendar_prompt_hook.sh has no timeout

#### PASS
- [validator-no-or-true] PreToolUse/"Bash": validate_commit_message.py correctly has no || true
- [bash-no-set-e] scripts/calendar_hook.sh: no set -e found

### Summary
- Total hooks: 12
- FAIL: 1
- WARN: 3
- PASS: 8
```

## After the Audit

This skill produces findings only. Next steps are decided by the human:
- Fix critical findings immediately
- Create a GitHub issue for non-urgent findings
- Update BEST_PRACTICES.md if new patterns emerge
- Use `/sdlc` to plan and execute fixes
