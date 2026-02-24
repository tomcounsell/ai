---
name: do-skills-audit
description: "Audit all Claude Code skills for compliance with canonical template standards. Use when checking skill quality, validating frontmatter, or verifying progressive disclosure. Runs deterministic validation rules and best practices sync against latest Anthropic docs by default."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash
argument-hint: "[--fix] [--json] [--skill <name>] [--no-sync]"
---

# Skills Audit

Validates all `.claude/skills/*/SKILL.md` files against canonical template standards and Anthropic's latest published best practices.

## What this skill does

1. Runs 12 deterministic validation rules (frontmatter, structure, classification, content)
2. Fetches latest Anthropic skill docs and compares against our standards (by default)
3. Produces a structured PASS/WARN/FAIL report per skill
4. Optionally auto-fixes trivial issues

## Quick start

```bash
python .claude/skills/do-skills-audit/scripts/audit_skills.py $ARGUMENTS
```

## Arguments

| Flag | Description |
|------|-------------|
| `--fix` | Auto-fix trivial issues (missing name, trailing whitespace) |
| `--json` | Output JSON only |
| `--skill <name>` | Audit a single skill |
| `--no-sync` | Skip best practices sync (fast, offline) |
| `--apply` | Apply best practices updates to template/validator |
| `--update-skills` | Update existing skills to match new best practices |
| `--force-refresh` | Bypass doc cache and re-fetch |

## Validation Rules

**Structural (FAIL):** line count ≤500, frontmatter exists, name valid, broken sub-file links
**Quality (WARN):** description trigger-oriented, description ≤1024 chars, known fields only, argument-hint presence
**Classification (WARN):** infrastructure/background/fork skills have correct frontmatter flags
**Content (WARN):** no duplicate descriptions across skills
