# Skills Audit

Deterministic validation of all `.claude/skills/*/SKILL.md` files against canonical template standards and Anthropic's latest published best practices.

## Overview

The `/do-skills-audit` skill runs 12 validation rules across all skills in the repository, producing a structured PASS/WARN/FAIL report. By default, it also syncs against Anthropic's latest published skill documentation to detect drift.

## Components

### `audit_skills.py`

Main validation script with 12 deterministic rules:

| # | Rule | Severity |
|---|------|----------|
| 1 | Line count <= 500 | FAIL |
| 2 | Frontmatter exists | FAIL |
| 3 | Name field valid (lowercase, hyphenated, matches directory, <= 64 chars) | FAIL |
| 4 | Description contains trigger phrasing ("Use when", "Triggered by") | WARN |
| 5 | Description <= 1024 characters | WARN |
| 6 | Infrastructure skills have `disable-model-invocation: true` | WARN |
| 7 | Background skills have `user-invocable: false` | WARN |
| 8 | Fork skills have `context: fork` | WARN |
| 9 | Sub-file links resolve to existing files | FAIL |
| 10 | No duplicate descriptions across skills | WARN |
| 11 | Only known frontmatter fields used | WARN |
| 12 | Skills using `$ARGUMENTS` have `argument-hint` | WARN |

### `sync_best_practices.py`

Fetches Anthropic's official skill documentation and compares against our template/validator:

- Sources: `code.claude.com/docs/en/skills` and `github.com/anthropics/skills`
- Caches fetched docs for 7 days in `data/best_practices_cache.json`
- Deterministic field extraction (no LLM required)
- Generates delta report: alignments, drifts, recommendations
- Optional `--apply` flag to update template automatically

### Reflections Integration

Added as step 12 in the reflections daily maintenance system. Runs `--no-sync --json` mode during automated maintenance and reports FAIL findings to the reflections state for GitHub issue creation.

## Usage

```bash
# Full audit with best practices sync (default)
python .claude/skills/do-skills-audit/scripts/audit_skills.py

# Fast offline audit
python .claude/skills/do-skills-audit/scripts/audit_skills.py --no-sync

# Audit single skill
python .claude/skills/do-skills-audit/scripts/audit_skills.py --skill telegram

# Auto-fix trivial issues
python .claude/skills/do-skills-audit/scripts/audit_skills.py --fix

# JSON output for CI
python .claude/skills/do-skills-audit/scripts/audit_skills.py --json
```

## Design Decisions

- **Deterministic over LLM**: All 12 rules use regex/string matching, not AI inference. Reproducible, fast, no API costs.
- **Sync as default**: Best practices sync runs by default because staying aligned with Anthropic matters. Use `--no-sync` for speed.
- **WARN vs FAIL**: Structural violations (broken links, missing frontmatter) are FAIL. Quality suggestions (trigger phrasing, classification hints) are WARN.
- **Template variable awareness**: Rule 9 skips `{variable_name}` patterns in links to avoid false positives.
- **Classification lists**: Infrastructure, background, and fork skill lists are maintained as frozensets in the script, updated as skills are added/removed.

## Files

- `.claude/skills/do-skills-audit/SKILL.md` - Skill definition
- `.claude/skills/do-skills-audit/scripts/audit_skills.py` - Main validation script
- `.claude/skills/do-skills-audit/scripts/sync_best_practices.py` - Best practices sync
- `tests/unit/test_skills_audit.py` - 53 unit tests
- `data/best_practices_cache.json` - Cached Anthropic docs (auto-generated)
