---
name: audit-hooks
description: "Audit Claude Code hooks for safety and correctness. Use when reviewing, validating, or checking hooks, or after changing them."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash
model: sonnet
effort: medium
---

# Hook Audit

Verify that every registered Claude Code hook is safe. Advisory hooks must never hang or block
a session, validators must actually block what they exist to block, and no hook may fail
silently. This is read-only: report findings and let a human apply the fixes.

Done when: every hook registered in project `.claude/settings.json`, user
`~/.claude/settings.json`, and agent-definition frontmatter has a PASS/WARN/FAIL verdict
against [BEST_PRACTICES.md](BEST_PRACTICES.md), each non-PASS verdict names its fix, and the
hook log has been summarized.

## Repo context

If `.claude/skill-context/audit-hooks.md` exists, read it and follow it. It declares the
validator inventory, the error-logging helper and log path, the interpreter contract, and any
crash-guard forms or carve-outs. Defaults when it is absent:
- a validator is a `validate_*` script on PreToolUse/PostToolUse with a matcher;
- the log is `logs/hooks.log`;
- the helper is `log_hook_error()` if one exists.

## Classification

| Kind | Criteria | Crash guard (`\|\| true` or equivalent) |
|---|---|---|
| Validator | Exists to block an operation | Must NOT have one; it would swallow the block |
| Advisory | Logging, tracking, enrichment | Required |
| Stop / SubagentStop | Any hook on these events | Required |

Classify by purpose (read the script), not by name alone.

## Also check

- Every referenced script exists, and Python scripts parse.
- A PreToolUse hook with an empty matcher and a timeout over 5s is a WARN, because it runs on
  every tool call.
- Hook log, last 24h: the error count, which hooks errored, and the most frequent message.

## Report

```
## Hook Audit: N hooks · PASS N · WARN N · FAIL N
| Hook | Scope | Event | Kind | Finding | Severity |
### Fixes
```

FAIL means it can hang a session, fail silently, or let a blocked operation through. WARN
means suboptimal but not immediately dangerous.
