---
name: audit-SUBJECT
description: "Audit SUBJECT for WHAT_YOU_CHECK. Use when reviewing SUBJECT health, validating SUBJECT quality, or after SUBJECT changes."
allowed-tools: Read, Grep, Glob, Bash
disable-model-invocation: true
---

# SUBJECT Audit

BRIEF_DESCRIPTION — what gets audited, why it matters, what outcome the user gets.

## What this skill does

1. Scans TARGET_LOCATION to discover all SUBJECT items
2. Runs RULE_COUNT deterministic checks against each item
3. Produces a structured findings report organized by severity
4. DISPOSITION — one of: "Pauses for discussion (no auto-fix)" | "Optionally auto-fixes trivial issues" | "Applies corrections and commits"

## When to load sub-files

- CONDITION_A → read [SUB_FILE_A.md](SUB_FILE_A.md)
- CONDITION_B → read [SUB_FILE_B.md](SUB_FILE_B.md)

## Quick start

```bash
# If script-backed:
python .claude/skills/audit-SUBJECT/scripts/audit.py $ARGUMENTS

# If prompt-only: follow the steps below
```

1. **Enumerate**: Find all items to audit in TARGET_LOCATION
2. **Check**: Run each audit rule against each item
3. **Report**: Present findings grouped by severity
4. **Act**: Apply the disposition (fix, report, or pause)

## Audit Checks

### 1. CHECK_NAME
DESCRIPTION of what this check validates.
**Severity**: CRITICAL | WARNING | INFO

### 2. CHECK_NAME
DESCRIPTION.
**Severity**: CRITICAL | WARNING | INFO

## Output Format

```
## SUBJECT Audit Report

### Items Scanned
- ItemName (key metrics)

### Findings

#### CRITICAL
- [check-name] ItemName: specific finding

#### WARNING
- [check-name] ItemName: specific finding

#### INFO
- [check-name] ItemName: specific finding

### Summary
PASS: N  WARN: N  FAIL: N
```

## After the Audit

DISPOSITION_DETAILS — what happens next. Options:
- "Findings only. Next steps decided by the human."
- "Auto-fixes trivial issues. Creates GitHub issue for complex findings."
- "Applies corrections, commits, and reports."

## Version history

- v1.0.0 (YYYY-MM-DD): Initial
