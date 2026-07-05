---
name: audit-hooks
description: "Audit Claude Code hooks for safety and correctness. Use when reviewing hook health, checking hook safety, validating hooks, or after adding/modifying hooks."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash
---

# Hook Audit

**Goal:** verify that every Claude Code hook registered in `.claude/settings.json` is safe — advisory hooks can never hang or block a session, validators actually block what they exist to block, and no hook fails silently. Check each hook against the rules in [BEST_PRACTICES.md](BEST_PRACTICES.md) and report PASS/WARN/FAIL per hook. Read-only: findings only, fixes are applied by a human.

## Repo Context Probe

If `.claude/skill-context/audit-hooks.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares its validator inventory (hooks that must NOT have `|| true`), its error-logging convention, and its hook log path. Generic defaults: validators are identified by a `validate_*` script name on PreToolUse/PostToolUse with a matcher; the error log is `logs/hooks.log`; the logging helper is `log_hook_error()` if the repo has one.

## Audit Procedure

### Step 1: Parse settings.json

Read `.claude/settings.json` and extract every hook entry. For each hook, record:
- Event type (UserPromptSubmit, PreToolUse, PostToolUse, Stop)
- Matcher (empty string = all, or specific tool name)
- Command string
- Timeout value
- Whether `|| true` is present

### Step 2: Classify hooks

| Classification | Criteria | `|| true` required? |
|---------------|----------|-------------------|
| **Validator** | PreToolUse/PostToolUse + matcher + named `validate_*.py` | NO — must NOT have `|| true` |
| **Advisory** | Any hook whose purpose is logging/tracking/enrichment | YES — must have `|| true` |
| **Stop** | Any hook on Stop events | YES — must have `|| true` |

### Step 3: Check each hook against rules

For EACH hook command, check:

**Settings-level checks:**
- [ ] `|| true` correctness (Rules 1-3 from BEST_PRACTICES.md)
- [ ] Timeout appropriateness (Rule 9: 5s simple, 10s git, 15s API)
- [ ] Matcher specificity (empty matcher on PreToolUse fires on every tool call — WARN if timeout > 5s)

**Python script checks** (for hooks targeting `.py` files):
- [ ] Has `try/except` + error logging at `__main__` level (Rule 4)
- [ ] No bare `sys.exit(1)` in advisory hooks
- [ ] No top-level imports of known-heavy modules: `anthropic`, `openai`, `pandas`, `numpy`, `httpx`, `pydantic` (Rule 8)

**Bash script checks** (for hooks targeting `.sh` files):
- [ ] Uses `set +e`, not `set -e` (Rule 5)
- [ ] No bare `exec` without error handling (Rule 6)
- [ ] Prefers venv binaries over system PATH (Rule 7)
- [ ] Has error logging to the hook log

### Step 4: Deployment readiness

- [ ] Every script path referenced in settings.json exists
- [ ] Python scripts are importable (no syntax errors)
- [ ] Shell scripts are executable (`chmod +x`)
- [ ] The error-logging helper is importable from where the repo declares it lives

### Step 5: Check the hook log

If the hook log (default `logs/hooks.log`) exists, scan for recent errors (last 24h). Report:
- Total error count
- Unique hook names that errored
- Most frequent error message

## Report Format

Output findings as a structured table:

```
## Hook Audit Report

### Summary
- Total hooks: N
- PASS: N | WARN: N | FAIL: N

### Findings

| Hook | Event | Type | Finding | Severity |
|------|-------|------|---------|----------|
| stop.py | Stop | Advisory | Has || true, has log_hook_error() | PASS |
| validate_commit_message.py | PreToolUse | Validator | No || true (correct) | PASS |
| calendar_prompt_hook.sh | UserPromptSubmit | Advisory | Missing || true | FAIL |

### Recommendations
[List specific fixes for FAIL and WARN items]
```

## Severity Levels

- **FAIL**: Rule violation that can cause session hangs, silent failures, or security issues
- **WARN**: Suboptimal pattern that should be improved but is not immediately dangerous
- **PASS**: Hook follows all applicable best practices
