---
name: do-skills-audit
description: "Audit all Claude Code skills for compliance with canonical template standards. Use when checking skill quality, validating skill structure, linting SKILL.md files, verifying frontmatter, or scanning for skill issues. Runs deterministic validation rules and best practices sync against latest Anthropic docs by default."
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
python .claude/skills-global/do-skills-audit/scripts/audit_skills.py $ARGUMENTS
```

**If `$ARGUMENTS` was not substituted** (the command shows a literal `$ARGUMENTS`): Look at the user's original message — they invoked this as `/do-skills-audit <flags>`. Extract whatever follows `/do-skills-audit` and pass it to the script. If no flags were provided, run the script with no arguments (default behavior).

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

## Description Budget Target

The skill listing budget is `skillListingBudgetFraction` (currently 2% of context ≈ 4,000 chars). With 49 skills the per-skill target is **≤80 chars** to stay comfortably under budget without raising the fraction. The hard ceiling per description is 1,536 chars (Claude Code truncates beyond that).

**Goal:** total description chars across all skills ≤ 4,000 (currently ~13,000 — needs trimming).

### What a good description looks like

The description field is a **trigger**, not documentation. The body loads after invocation; the description only needs to fire it.

**Bad** (verbose, explains what it does):
```
"Audit all Claude Code skills for compliance with canonical template standards. Use when checking skill quality, validating skill structure, linting SKILL.md files, verifying frontmatter, or scanning for skill issues."
```

**Good** (punchy, trigger-first):
```
"Audit skill quality: frontmatter, descriptions, structure. Triggered by 'audit skills', 'check skill', 'lint SKILL.md'."
```

**Rules for effective descriptions:**
1. Lead with what it does (≤10 words) — the model reads left-to-right
2. List exact trigger phrases if the skill name isn't self-evident
3. Add one "do NOT trigger on X" only if false positives are a real problem
4. Never explain implementation details, file paths, or step counts — that's body content
5. Target 60–120 chars; 200+ is a signal the description is doing documentation work


## After the Audit

With `--fix`: trivial issues (missing name field, trailing whitespace) are auto-corrected in place. Complex findings (classification mismatches, trigger phrasing, duplicate descriptions) are reported for human review. Without `--fix`: all findings are report-only.
