---
name: audit-skills
description: "Audit skill quality: lint structure, descriptions, rot, orphans; --arch for architecture dispositions. Use when auditing, linting, or checking skills."
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash, Agent
argument-hint: "[--fix] [--json] [--skill <name>] [--no-sync] [--arch]"
---

# Skills Audit

Keeps every skill (`.claude/skills-global/`, `.claude/skills/`, user-level
`~/.claude/skills/` copies) and agent definition lean, correct, and on the right model.
Two layers: a **deterministic lint** for what code can verify, and an **architecture pass**
(`--arch`) where a model judges each skill against [references/rubric.md](references/rubric.md).

Done when: the lint reports no FAIL (or each FAIL has an owner), and for `--arch`, every
skill and agent has a findings row, every gate-skill cut has been verified, and the changes
are applied on a branch or filed.

## Lint

```bash
python .claude/skills-global/audit-skills/scripts/audit_skills.py $ARGUMENTS
```

If `$ARGUMENTS` shows up literally, pass through whatever followed `/audit-skills`.

| Flag | Effect |
|------|--------|
| `--fix` | Fix trivial issues (missing name, whitespace, untracked artifacts, empty husk dirs) |
| `--json` | JSON output (contract in the script docstring; the skills-audit reflection reads it) |
| `--skill <name>` | One skill |
| `--no-sync` | Skip the best-practices sync (offline, fast) |
| `--apply` / `--update-skills` / `--force-refresh` | Best-practices sync controls |

The 21 rules cover structure (line count, frontmatter, name, sub-file links, known fields
and valid `effort`, `argument-hint`), descriptions (trigger phrase, length, duplicates, the
fleet-wide 4,000-char budget, near-duplicate trigger surfaces), classification flags, the
repo-agnostic seam for global bodies (rules 13 and 21), and rot (dead paths, tracked junk,
unreferenced sub-files, husk directories, diverged user-level copies). Each rule's exact
test is in the script. The audit must pass on itself: `--skill audit-skills` runs first.

## Architecture pass (`--arch`)

Current models do best from an objective, the constraints they can't infer, and a
definition of done. The pass cuts everything else and places each skill on the fastest
model and effort that holds quality. It is judgment, not benchmarks. Follow the rubric for
the lenses, the evidence test, placement rules, schema, and verifier prompt.

Fleet runs fan out one analyst subagent per domain cluster, in parallel, each with only the
rubric and its files. Analysts can apply their changes on a branch (disjoint files per
cluster), and the pass ships as one pull request whose review is the human check.

## Best-practices sync

`scripts/sync_best_practices.py` fetches Anthropic's current skills docs and the upstream
skill-creator into `references/` (7-day cache) and diffs them against our template
standards. Re-read the cached frontmatter table when a new field appears; rule 11's
`KNOWN_FIELDS` must list every documented field.

## After the audit

- `--fix` changes only mechanical findings. Merges, splits, and description rewrites change
  trigger behavior, so a human reviews them (in the PR or an issue).
- FAIL findings that persist across two consecutive runs are filed as issues by the daily
  `skills-audit` reflection.
- Moving or retiring a skill needs the repo's hardlink-removal entry and a doc sweep.
