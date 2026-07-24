# Skills Audit

Two-layer audit of every skill the repo has: a deterministic lint (20 rules, script-run,
no model judgment) and an opt-in architecture pass (`--arch`, model judgment against a
fixed rubric). Renovated 2026-07 as groundwork for the skills-architecture-audit plan
(issue #1883).

## Overview

The `/audit-skills` skill audits both skills roots — `.claude/skills-global/` (synced
to every machine by `/update`) and `.claude/skills/` (project-only) — plus user-level
`~/.claude/skills/` orphan detection. It produces a structured PASS/WARN/FAIL report and,
by default, syncs against Anthropic's latest published skill documentation to detect drift.

The audit's organizing idea is context economics: a skill's description ships in every
session (fleet budget 4,000 chars), its body loads per invocation (cap 500 lines), and its
sub-files load on demand. Most findings are misplacements across those three tiers.

## Components

### `audit_skills.py`

Main validation script with 20 deterministic rules:

| # | Rule | Severity |
|---|------|----------|
| 1 | Line count <= 500 | FAIL |
| 2 | Frontmatter exists | FAIL |
| 3 | Name field valid (lowercase, hyphenated, matches directory, <= 64 chars) | FAIL |
| 4 | Description contains trigger phrasing ("Use when", "Triggered by") | WARN |
| 5 | Description <= 200 chars (target <= 120; Anthropic hard cap 1024) | WARN |
| 6 | Infrastructure skills have `disable-model-invocation: true` | WARN |
| 7 | Background skills have `user-invocable: false` | WARN |
| 8 | Fork skills have `context: fork` | WARN |
| 9 | Sub-file links resolve to existing files | FAIL |
| 10 | No duplicate descriptions across skills | WARN |
| 11 | Only known frontmatter fields used | WARN |
| 12 | Skills using `$ARGUMENTS` have `argument-hint` | WARN |
| 13 | Global skills with executable ai-repo coupling carry the skill-context probe step (project-only skills exempt) | FAIL |
| 14 | Fleet description total within 4,000-char budget | WARN |
| 15 | Referenced paths resolve (missing own assets FAIL; other unresolvable paths WARN; seam paths and placeholder tokens skipped) | FAIL/WARN |
| 16 | No git-tracked junk files in skill dirs (README/CHANGELOG/pyc/__pycache__) | WARN |
| 17 | No near-duplicate trigger surfaces (word-overlap Jaccard >= 0.5) | WARN |
| 18 | Every bundled sub-file referenced by SKILL.md or a sibling file | WARN |
| 19 | No husk directories (a dir in a skills root without SKILL.md is a move leftover) — `--fix` auto-prunes ones that are empty except for build artifacts; husks with real orphaned files are left for a human to delete or restore | FAIL |
| 20 | User-level `~/.claude/skills/` copies trace to a repo source and haven't diverged | WARN |

Rules 10, 14, 17, 19, 20 are fleet-level and run only on full-fleet invocations (not
`--skill`). Rule 13 applies to the `global` root only — project-only skills run solely in
this repo and may reference `valor-*`, `sdlc-tool`, etc. freely.

**JSON contract** (consumed by the skills-audit reflection):
`{"summary": {"total_skills", "pass", "warn", "fail", "description_total_chars",
"description_budget"}, "findings": [{"skill", "rule", "severity", "message", "dir"}]}`.
Legacy aliases `results` and `skills_audited` are kept. The `dir` field labels the root:
`global` | `project` | `user`.

**Repo-root resolution**: prefers cwd when it has skills roots (so hardlinked copies run
from a foreign repo audit that repo), else derives from the script's own location.

### `references/rubric.md` — the `--arch` architecture pass

The judgment layer: five lenses (context economy, primitive fit, consolidation, model
tier, efficiency), a disposition vocabulary (keep / merge / split / workflow / subagent /
script / retire), a findings schema, and an adversarial verifier prompt. Subagent
dispositions must cite one of the two legitimate reasons (parallelism or fresh-mind
isolation); merge dispositions must include the proposed merged description and argue
trigger precision survives. Dispositions are recommendations — accepted ones become
GitHub issues, never auto-applied.

### `sync_best_practices.py`

Fetches Anthropic's official skill documentation and compares against our template/validator:

- Sources: `code.claude.com/docs/en/skills` and `github.com/anthropics/skills`
- Caches fetched docs for 7 days in `references/` (see `references/metadata.json`)
- Deterministic field extraction (no LLM required)
- Generates delta report: alignments, drifts, recommendations
- Optional `--apply` flag to update template automatically

### Reflections Integration

Registered as the `skills-audit` reflection (`reflections.audits.skills_audit.run`). The
wrapper iterates `load_local_projects()` and invokes each project's local copy of
`audit_skills.py` via `--no-sync --json`, resolving the script under
`.claude/skills-global/` first and falling back to `.claude/skills/` (foreign repos that
vendor the skill project-locally). Projects without the script are skipped silently. Each
project's FAIL findings are prefixed with `[{slug}]` and aggregated into a single run
record with a per-project breakdown — see
[reflections.md → Per-Project Audit Iteration](reflections.md#per-project-audit-iteration).

## Usage

```bash
# Full audit with best practices sync (default)
python .claude/skills-global/audit-skills/scripts/audit_skills.py

# Fast offline audit
python .claude/skills-global/audit-skills/scripts/audit_skills.py --no-sync

# Audit single skill
python .claude/skills-global/audit-skills/scripts/audit_skills.py --skill telegram

# Auto-fix trivial issues (missing name, whitespace, untracked build artifacts,
# and empty rule-19 husk directories)
python .claude/skills-global/audit-skills/scripts/audit_skills.py --fix

# JSON output for CI / reflections
python .claude/skills-global/audit-skills/scripts/audit_skills.py --json
```

## Design Decisions

- **Deterministic over LLM**: all 20 lint rules use regex/string/stat checks, not AI
  inference. Reproducible, fast, no API costs. Judgment lives only in the `--arch` rubric.
- **Sync as default**: best practices sync runs by default because staying aligned with
  Anthropic matters. Use `--no-sync` for speed.
- **WARN vs FAIL**: FAIL is reserved for findings that are unambiguous and mechanical
  (broken links, husks, missing own assets, coupling without probe) — FAILs auto-file
  issues via the reflection streak gate, so false FAILs are worse than missed WARNs.
  Rule 15 deliberately demotes ambiguous path findings (cross-skill mentions,
  create-this-file instructions) to WARN for exactly this reason.
- **Tracked-junk only** (rule 16): untracked build artifacts (test-import `__pycache__`)
  regenerate constantly and are gitignored; flagging them would be perpetual noise.
  `--fix` deletes them locally instead.
- **Template variable awareness**: rules 9 and 15 skip `{variable}`, `<placeholder>`, glob,
  and `$VAR` tokens, plus the skill-context seam paths (whose references are conditional
  by convention).
- **Classification lists**: infrastructure, background, and fork skill lists are
  maintained as frozensets in the script, updated as skills are added/removed.
- **Self-audit**: the skill must pass its own audit; `--skill audit-skills` is the
  first check of a fleet run.

## Files

- `.claude/skills-global/audit-skills/SKILL.md` - Skill definition
- `.claude/skills-global/audit-skills/scripts/audit_skills.py` - 20-rule validation script
- `.claude/skills-global/audit-skills/scripts/sync_best_practices.py` - Best practices sync
- `.claude/skills-global/audit-skills/references/rubric.md` - Architecture-pass rubric (`--arch`)
- `.claude/skills-global/audit-skills/references/` - Cached Anthropic docs (auto-refreshed)
- `tests/unit/test_skills_audit.py` - Unit tests

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

## Architecture audit

The 20-rule lint above catches hygiene (rot, husks, budget, trigger collisions) —
it does not judge whether a skill is the *right shape*. That judgment lives in
a separate, human-gated pass: the `--arch` rubric at
`.claude/skills-global/audit-skills/references/rubric.md`, executed as a
multi-agent fan-out (one analyst per skill cluster, adversarial verification of
every non-`keep` disposition, single synthesis report) rather than as a
deterministic script, since primitive-fit and consolidation calls require
judgment the lint layer can't make.

The rubric applies five lenses to every skill: context economy (progressive
disclosure vs. always-loaded body), primitive fit (Skill vs. Workflow vs.
Subagent vs. Script — a Subagent needs a cited reason of parallelism or
fresh-mind isolation, a Workflow needs named stage boundaries and a handoff
schema), consolidation (merge direction + trigger-precision-preserving
description text), model tier (sonnet mechanical / opus multi-step / fable
frontier-judgment), and efficiency (token cost estimate per invocation).

The first full run (2026-07) is recorded at
[`docs/audits/skills-architecture-audit-2026-07.md`](../audits/skills-architecture-audit-2026-07.md) —
65 rows (60 live skills + 5 tracking-artifact orphans), zero restructuring
actions survived adversarial review this cycle (the fleet was largely
pre-renovated by PR #1894), with ~14 minor findings and 6 cross-cluster
observations flagged as candidate follow-up issues rather than executed. A
disposition report of this shape is a one-shot analysis, not part of the
regular lint cadence — re-run manually when the fleet has drifted enough to
warrant a fresh pass (see the report's own header for the baseline commit).
