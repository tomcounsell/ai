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

Registered as the `skills-audit` reflection (`reflections.audits.skills_audit.run`). The wrapper iterates `load_local_projects()` and invokes each project's local copy of `.claude/skills/do-skills-audit/scripts/audit_skills.py` via `--no-sync --json` mode. Projects without that script are skipped silently. Each project's FAIL findings are prefixed with `[{slug}]` and aggregated into a single run record with a per-project breakdown — see [reflections.md → Per-Project Audit Iteration](reflections.md#per-project-audit-iteration).

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

## Reflection Issue Filing

The `skills-audit` reflection (`reflections.audits.skills_audit.run`, registered in `config/reflections.yaml`) runs the audit nightly across every local project. As of issue #1395 Phase 2, it also **files a GitHub issue** for each FAIL finding that persists across two consecutive runs, so structural problems can no longer accumulate silently in reflection telemetry.

### Streak gate

A FAIL finding does **not** file an issue on first sight. Instead the helper `_file_skills_audit_issue_if_streaked` increments a Redis streak counter keyed by `SHA-256(project_slug/skill_name/rule_id)[:16]` (message text is intentionally excluded from the hash so rewording does not break dedup). Only after the counter reaches `2` is an issue filed. This filters out single-run transient regressions in `audit_skills.py` itself.

| Run | Streak | Action |
|---|---|---|
| 1 | 1 | counter incremented, no issue |
| 2 | 2 | **issue filed**, dedup key set (30d TTL) |
| 3 - 30 | (any) | dedup key blocks re-fire |
| Issue closed manually | — | streak counter resets naturally when the rule passes; dedup key still expires after 30 days |

### Labels and identity

Issues are filed against the **project's own repository** (resolved via `gh repo view --json nameWithOwner`, cached once per project per run), not the AI repo. Labels: `skills` and `bug`. Title format: `skills-audit FAIL: <skill-name> (rule <N>)`.

### Failure tolerance

- **`gh issue create` fails** → the dedup key is NOT set, so the next run retries. No state poisoning.
- **Redis unavailable** → the helper returns `False` and skips filing; the audit's structural telemetry continues uninterrupted.
- **Filing lock contention** (`SET NX EX 60`) → a second concurrent reflection tick observing the same finding backs off without re-incrementing the streak.
- **100 distinct FAIL findings in a single run** (e.g. a regression in `audit_skills.py`) → all streak=1, zero issues filed.

### Manual operator escape hatch

To silence a noisy finding without fixing the underlying rule:
- Close the auto-filed issue. The dedup key keeps it from re-filing for 30 days.
- If the rule itself is wrong, fix `audit_skills.py` — the FAIL stops appearing, the streak resets, no further issues file.
- To force a clean slate before the 30-day window expires, delete the matching `skills_audit:issues_filed:<hash>` key in Redis manually.

### What this is NOT

- **Not a `--file-issues` flag** on `audit_skills.py` itself. The streak gate is a reflection-cadence concept that does not make sense for one-off CLI invocations. The script remains side-effect-free.
- **Not WARN-finding filing.** Only FAIL findings file issues. WARNs are surfaced in reflection telemetry only.
- **Not auto-closing.** Issues stay open until a human closes them. The reflection has no opinion about when a finding has been "fixed".
