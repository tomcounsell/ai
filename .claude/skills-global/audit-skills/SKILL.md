---
name: audit-skills
description: "Audit skill quality: lint structure, descriptions, rot, orphans; --arch for architecture dispositions. Use when auditing, linting, or checking skills."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash, Agent
argument-hint: "[--fix] [--json] [--skill <name>] [--no-sync] [--arch]"
---

# Skills Audit

Audits every skill the repo has — `.claude/skills-global/` (synced to all machines) and
`.claude/skills/` (project-only) — plus user-level `~/.claude/skills/` orphan detection.
Two layers: a **deterministic lint** (script; code checks what code can verify) and an
optional **architecture pass** (`--arch`; model judgment against a fixed rubric).

## Why this audit exists (the economics)

A skill costs context in three places, each with different economics:

1. **Description** — ships in *every* session, used or not. The fleet total is the scarcest
   resource here: budget 4,000 chars (~2% of context), per-skill target ≤120.
2. **Body (SKILL.md)** — loads once per invocation. Only what every invocation needs. Cap 500 lines.
3. **Sub-files** — load on demand. Reference tables, edge cases, templates live here.

Always-true policy belongs in CLAUDE.md, not in any skill. Most findings are misplacements
across these three boundaries.

## Quick start

```bash
python .claude/skills-global/audit-skills/scripts/audit_skills.py $ARGUMENTS
```

**If `$ARGUMENTS` was not substituted** (the command shows a literal `$ARGUMENTS`): extract
whatever followed `/audit-skills` in the user's message and pass it through; no flags
means default behavior.

| Flag | Description |
|------|-------------|
| `--fix` | Auto-fix trivial issues (missing name, whitespace, untracked build artifacts) |
| `--json` | JSON output (contract in the script docstring; consumed by the skills-audit reflection) |
| `--skill <name>` | Audit a single skill |
| `--no-sync` | Skip best-practices sync (fast, offline) |
| `--apply` / `--update-skills` / `--force-refresh` | Best-practices sync controls (see below) |

## Layer 1 — deterministic lint (21 rules)

**Structure (1–3, 9, 11–12):** line count ≤500 · frontmatter parses · name valid + matches
dir · sub-file links resolve · only known fields · `argument-hint` when `$ARGUMENTS` used.

**Descriptions (4–5, 10, 14, 17):** trigger phrase present · length ≤200 (target ≤120,
hard cap 1024) · no duplicate descriptions · fleet total within the 4,000-char budget ·
no near-duplicate trigger surfaces (word-overlap collision detection).

**Classification (6–8):** infra skills carry `disable-model-invocation` · background
reference skills carry `user-invocable: false` · fork skills carry `context: fork`.

**Repo-agnostic seam (13, 21):** global skill bodies containing executable ai-repo coupling
(the CLI/module tokens in the script's `COUPLING_SIGNALS` set) must carry the canonical
probe step deferring to the per-repo skill-context seam. Rule 21 additionally flags
Bucket-C coupling: backtick-coded slash-invocations of project-only skills (names derived
live from the repo's `.claude/skills/` listing — e.g. a bare `/sdlc`) and curated infra
tokens (`sdk_client.py`, `SDLC_TARGET_REPO`), unless the **same physical line** carries
conditional framing ("in this repo", "this repo's", or the probe sentence). Both rules scan
every `*.md` sub-file in the skill dir, not just SKILL.md (probe coverage is read from
SKILL.md); rule 21's signals skip fenced code blocks. Project-only skills are exempt, and
this auditor skill's own docs are self-exempt (they describe the very signals).

**Rot & hygiene (15–16, 18–20):** referenced paths resolve (skills decay as repos move —
missing own assets FAIL, other unresolvable paths WARN) · no tracked junk files
(README/CHANGELOG/pyc) · every bundled sub-file is referenced by SKILL.md or a sibling ·
no husk directories (a dir without SKILL.md is a move leftover — `--fix` auto-prunes ones
that are truly empty, i.e. contain nothing but `__pycache__`/`.DS_Store`; husks holding real
orphaned files are never auto-deleted and keep failing rule 19 until a human deletes or
restores them) · user-level `~/.claude/skills/` copies trace back to a repo source and
haven't diverged.

The audit must pass on itself: `--skill audit-skills` is the first check of a fleet run.

## Layer 2 — architecture pass (`--arch`)

Model judgment, not script: for each skill produce improvement suggestions, a
**disposition** (keep / merge / split / workflow / subagent / script / retire), and a
**model tier** (sonnet / opus / fable), with adversarial verification of every non-keep
disposition. Load [references/rubric.md](references/rubric.md) for the five lenses,
disposition criteria, findings schema, and verifier prompt — do not improvise criteria.

## Best-practices sync

`scripts/sync_best_practices.py` fetches Anthropic's current skills docs and the upstream
skill-creator, caches them under `references/` (7-day TTL), and diffs against our template
standards. Runs by default; `--no-sync` skips, `--apply` updates our template/validator,
`--update-skills` propagates to existing skills, `--force-refresh` bypasses the cache.

## After the audit

- `--fix` corrects trivial mechanical findings only. Merges, splits, and description
  rewrites change trigger behavior — always human-reviewed, never auto-applied.
- One-command husk cleanup: `python .claude/skills-global/audit-skills/scripts/audit_skills.py --fix --no-sync`
  prunes every empty rule-19 husk directory across both skill roots. Anything it leaves
  behind still failing rule 19 holds real orphaned files — inspect and delete or restore
  manually.
- FAIL findings that persist across 2 consecutive runs are auto-filed as GitHub issues by
  the `skills-audit` reflection (daily).
- Accepted `--arch` dispositions become one GitHub issue each, carrying the
  `RENAMED_REMOVALS` and doc-sweep requirements that skill moves need.
