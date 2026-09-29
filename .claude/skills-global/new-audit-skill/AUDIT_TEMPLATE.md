---
name: audit-SUBJECT
description: "Audit SUBJECT for PROBLEMS. Use when checking, validating, or reviewing SUBJECT, or after SUBJECT changes."
allowed-tools: Read, Grep, Glob, Bash
disable-model-invocation: true
# model / effort: see the placement notes in new-audit-skill/SKILL.md
---

# SUBJECT Audit

OBJECTIVE: what gets audited (TARGET_LOCATION), why it matters, and what the reader gets.
DISPOSITION: "Report only; a human decides fixes." | "Auto-fixes trivial issues, reports the
rest." | "Applies corrections to the working tree for review."

Done when: every item in TARGET_LOCATION has a verdict on every check, each finding cites
evidence (file:line or a value), and the report below is produced.

## Repo context

If `.claude/skill-context/audit-SUBJECT.md` exists, read it for repo-specific paths,
exemptions, and conventions. Otherwise use the defaults below.

## Run

```bash
python scripts/audit.py $ARGUMENTS   # if script-backed; delete otherwise
```

## Checks

| Check | Severity | Fails when |
|---|---|---|
| `check-name` | FAIL | CONCRETE, VERIFIABLE CONDITION (and why it matters, if not obvious) |
| `check-name` | WARN | … |

## Report

```
## SUBJECT Audit: PASS N · WARN N · FAIL N

#### FAIL
- [check-name] ItemName: expected X, found Y (path:line)
#### WARN
- [check-name] ItemName: …
```
