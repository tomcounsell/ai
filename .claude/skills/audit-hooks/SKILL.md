---
name: audit-hooks
description: "Audit Claude Code hooks for safety, correctness, and best practices compliance. Checks settings.json configuration, hook scripts, error logging, and deployment readiness. Use when reviewing hook health, checking hook safety, validating hooks, or after adding/modifying hooks."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash
---

# Hook Audit

Audits all Claude Code hooks registered in `.claude/settings.json` against the best practices defined in `BEST_PRACTICES.md`. Produces a structured report with PASS/WARN/FAIL dispositions per hook.

## What this skill does

1. Parse `.claude/settings.json` and extract all hook entries
2. Classify each hook as advisory (has `|| true`) or validator (no `|| true`)
3. Run safety checks against BEST_PRACTICES.md rules
4. Inspect each hook script for code-level issues
5. Check deployment readiness
6. Output a structured findings report

**This skill is read-only. It does NOT auto-fix anything.** Review findings and fix manually.

## Audit Procedure

### Step 1: Parse settings.json

Read `.claude/settings.json` and extract every hook entry. For each hook, record:
- Event type (UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop)
- Matcher (empty string = all, or specific tool name)
- Command string
- Timeout value
- Whether `|| true` is present

### Step 2: Classify hooks

| Classification | Criteria | `|| true` required? |
|---------------|----------|-------------------|
| **Validator** | PreToolUse/PostToolUse + matcher + named `validate_*.py` | NO — must NOT have `|| true` |
| **Advisory** | Any hook whose purpose is logging/tracking/enrichment | YES — must have `|| true` |
| **Stop/SubagentStop** | Any hook on Stop or SubagentStop events | YES — must have `|| true` |

### Step 3: Check each hook against rules

For EACH hook command, check:

**Settings-level checks:**
- [ ] `|| true` correctness (Rules 1-4 from BEST_PRACTICES.md)
- [ ] Timeout appropriateness (Rule 10: 5s simple, 10s git, 15s API)
- [ ] Matcher specificity (empty matcher on PreToolUse fires on every tool call — WARN if timeout > 5s)

**Python script checks** (for hooks targeting `.py` files):
- [ ] Has `try/except` + `log_hook_error()` at `__main__` level (Rule 5)
- [ ] No bare `sys.exit(1)` in advisory hooks
- [ ] No top-level imports of known-heavy modules: `anthropic`, `openai`, `pandas`, `numpy`, `httpx`, `pydantic` (Rule 9)
- [ ] File exists and is syntactically valid Python

**Bash script checks** (for hooks targeting `.sh` files):
- [ ] Uses `set +e`, not `set -e` (Rule 6)
- [ ] No bare `exec` without error handling (Rule 7)
- [ ] Prefers venv binaries over system PATH (Rule 8)
- [ ] Has error logging to `logs/hooks.log`
- [ ] File exists and is executable

### Step 4: Deployment readiness

- [ ] Every script path referenced in settings.json exists
- [ ] Python scripts are importable (no syntax errors)
- [ ] Shell scripts are executable (`chmod +x`)
- [ ] `log_hook_error()` is importable from `hook_utils.constants`

### Step 5: Check logs/hooks.log

If `logs/hooks.log` exists, scan for recent errors (last 24h). Report:
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
