---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2025-02-23
tracking: https://github.com/tomcounsell/ai/issues/153
---

# /do-skills-audit Skill

## Problem

After the skills reorganization (#152/#156) and SDLC enforcement (#154), we have **25 skills** following a canonical SKILL.md template with specific frontmatter, line count, naming, and progressive disclosure requirements. There is no automated way to verify all skills stay compliant as the repo evolves. Manual auditing is tedious and will inevitably be forgotten, letting standards drift.

**Context from recent PRs:**
- PR #156 added 8 skills (add-feature, prepare-app, prime, pthread, audit-next-tool, sdlc, new-skill, new-valor-skill restructured) and split do-plan into sub-files (SKILL.md + PLAN_TEMPLATE.md + SCOPING.md + EXAMPLES.md)
- PR #154 added do-patch skill and hooks infrastructure
- Issue #155 (pending) will delete 26 unused agent files and trim the do-plan template agent type list to 6 — the `test-engineer` agent type used in this plan will no longer have a backing file

**Current behavior:**

Skill compliance is checked manually by eyeballing frontmatter, running `wc -l`, and grepping for patterns. No single command validates all skills against the established standards. The `/do-docs-audit` skill covers documentation files but knows nothing about skill-specific constraints.

**Desired outcome:**

A `/do-skills-audit` skill that:
1. Scans all `.claude/skills/*/SKILL.md` files
2. Validates each against the canonical template standards
3. Produces a structured report with PASS/WARN/FAIL verdicts per skill
4. Optionally auto-fixes trivial issues (missing fields, formatting)

## Appetite

**Size:** Medium (upgraded from Small — LLM sync adds complexity)

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Two parts: (1) deterministic validation — mechanical, read files, check 12 rules, report results; (2) LLM best practices sync — fetches Anthropic's latest published guidance, compares against our template/validator, and optionally updates skills to match state of the art.

## Prerequisites

- ✅ #152/#156 (Skills & Agents Reorganization) — merged. Canonical template established, 25 skills in place.
- ✅ #154 (SDLC Enforcement) — merged. do-patch skill added, hooks infrastructure in place.
- ⚠️ #155 (Condense Context Overhead) — pending. Will delete unused agent files and trim do-plan template. Not a blocker — this plan works regardless of agent file count.

## Solution

### Key Elements

- **Skill directory**: `.claude/skills/do-skills-audit/SKILL.md` — the audit skill itself
- **Validation script**: `.claude/skills/do-skills-audit/scripts/audit_skills.py` — standalone Python script that runs all checks and outputs structured results
- **Rule set**: Hardcoded checks matching the standards established in #152/#156

### Flow

**Standard audit (deterministic):**
`/do-skills-audit` → `audit_skills.py` → scan SKILL.md files → check rules → structured report

**Best practices sync (LLM-powered, new scope):**
`/do-skills-audit --sync-best-practices` → fetch latest Anthropic docs → LLM compares current skills against published standards → generates update recommendations → optionally applies fixes

### Technical Approach

#### Validation Rules (Deterministic)

Each SKILL.md is checked against these rules, aligned with [Anthropic's official skill docs](https://code.claude.com/docs/en/skills) and [skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md):

**Structural rules:**
1. **Line count**: SKILL.md must be <= 500 lines (FAIL if exceeded) — per Anthropic: "Keep SKILL.md under 500 lines"
2. **Frontmatter exists**: Must have YAML frontmatter delimited by `---` (FAIL if missing)
3. **Name field**: Must be present, lowercase, hyphens only, max 64 chars (FAIL if invalid) — per Anthropic: "Lowercase letters, numbers, and hyphens only (max 64 characters)"
4. **Description field**: Must be present and trigger-oriented — should describe both what the skill does AND when to use it (WARN if missing trigger phrasing). Per Anthropic's skill-creator: description is the "PRIMARY TRIGGERING MECHANISM" — include "when to use" information in the description, not the body, because the body only loads after triggering.
5. **Description length**: Must be <= 1024 characters (WARN if exceeded)

**Classification rules:**
6. **Infrastructure skills** (`update`, `setup`, `reclassify`, `new-skill`, `new-valor-skill`, `prime`): Must have `disable-model-invocation: true` (WARN if missing)
7. **Background reference skills** (`agent-browser`, `telegram`, `reading-sms-messages`, `checking-system-logs`, `google-workspace`): Must have `user-invocable: false` (WARN if missing)
8. **Fork skills** (`do-build`, `do-pr-review`, `do-docs-audit`, `pthread`, `do-design-review`, `sdlc`): Should have `context: fork` (WARN if missing)

**Content rules:**
9. **Sub-file references**: Any `[text](file.md)` link must point to a file that exists in the skill directory (FAIL if broken). This is critical now that PR #156 introduced sub-files — do-plan has 3 sub-files (PLAN_TEMPLATE.md, SCOPING.md, EXAMPLES.md) and do-build has 2 (WORKFLOW.md, PR_AND_CLEANUP.md).
10. **No duplicate descriptions**: No two skills should have identical or near-identical description fields (WARN if found)

**Frontmatter completeness rules (new):**
11. **Known fields only**: Frontmatter should only contain recognized fields: `name`, `description`, `argument-hint`, `disable-model-invocation`, `user-invocable`, `allowed-tools`, `model`, `context`, `agent`, `hooks` (WARN if unknown fields found)
12. **`argument-hint` for skills with `$ARGUMENTS`**: If SKILL.md body references `$ARGUMENTS` or `$0`/`$1`, the `argument-hint` field should be set (WARN if missing)

#### Script Design

`audit_skills.py` is a standalone script (no external dependencies beyond Python stdlib + `yaml` from PyYAML which is already in the project). It:

1. Discovers all `.claude/skills/*/SKILL.md` files
2. Parses frontmatter (simple YAML between `---` delimiters)
3. Runs each rule, collecting results as `(skill_name, rule_id, severity, message)`
4. Outputs results in two formats:
   - Human-readable table to stdout (for interactive use)
   - JSON to a file (for programmatic consumption by other tools)

Exit codes:
- 0: All pass (may have warnings)
- 1: At least one FAIL

#### LLM Best Practices Sync (New Scope)

`sync_best_practices.py` — a separate script that uses an LLM to:

1. **Fetch latest Anthropic docs**: Pull current content from `https://code.claude.com/docs/en/skills` and `https://github.com/anthropics/skills` (the official skill-creator and spec)
2. **Extract current best practices**: Parse the fetched docs for frontmatter fields, structural rules, progressive disclosure guidance, description format, and directory structure requirements
3. **Compare against our standards**: Diff the extracted practices against our validator rules and template
4. **Generate a delta report**: List what's new, changed, or deprecated in Anthropic's guidance vs. our current template/validator
5. **Update artifacts** (with `--apply` flag):
   - Update `new-skill/SKILL_TEMPLATE.md` to match latest canonical structure
   - Update `new-skill/SKILL.md` field constraints and best practice guidance
   - Update `audit_skills.py` validation rules to match any new fields or constraints
   - Update existing skills that violate newly-discovered best practices (with `--update-skills` flag)

**LLM choice**: Use local Ollama (lightweight) for diffing/comparison. Fall back to Claude API for complex semantic analysis if needed.

**Caching**: Cache fetched docs in `data/best_practices_cache.json` with TTL of 7 days to avoid redundant fetches.

#### Skill Design

The SKILL.md is minimal — it describes when to use the audit, then shells out to the script:

```bash
python "$CLAUDE_PROJECT_DIR/.claude/skills/do-skills-audit/scripts/audit_skills.py"
```

The skill can also accept arguments:
- `--fix` — auto-fix trivial issues (add missing `name` field from directory name, trim trailing whitespace)
- `--json` — output only JSON (for piping to other tools)
- `--skill <name>` — audit a single skill instead of all
- `--sync-best-practices` — fetch latest Anthropic docs and compare against current standards
- `--sync-best-practices --apply` — apply recommended updates to template and validator
- `--sync-best-practices --update-skills` — also update existing skills to match new standards

## Rabbit Holes

- **Don't use LLM for deterministic validation**: The 12 structural/classification rules are deterministic string/regex operations. LLM is only used for the best practices sync feature — comparing our standards against Anthropic's latest published guidance.
- **Don't enforce content structure beyond frontmatter**: Checking for specific sections (## Workflow, ## Examples, etc.) is too rigid. Skills have different needs. Only enforce frontmatter and structural constraints.
- **Don't auto-fix content issues via deterministic rules**: Auto-fix should only handle mechanical problems (missing name field, whitespace). Content rewrites happen only through the LLM best practices sync with explicit `--update-skills` flag.
- **Don't build a watch mode**: A one-shot audit is sufficient. CI integration or file watchers would be over-engineering.
- **Don't audit hooks**: PR #154 added `.claude/hooks/` with validators and post-tool-use hooks. These follow different patterns than skills and are out of scope.
- **Don't audit agent files**: Issue #155 will trim agents to 6. Agent files have no canonical template — auditing them adds no value.
- **Don't auto-apply LLM suggestions without review**: The `--apply` and `--update-skills` flags generate changes, but the user reviews the diff before committing. No blind rewrites.

## Risks

### Risk 1: Frontmatter parsing edge cases
**Impact:** YAML frontmatter with special characters, multi-line descriptions, or non-standard formatting could cause parse failures
**Mitigation:** Use PyYAML for parsing (already a project dependency). Fall back to regex extraction if YAML parsing fails. Report parse failures as WARN, not crash.

### Risk 2: Classification lists becoming stale
**Impact:** When new skills are added, the hardcoded lists of "infrastructure skills" and "background reference skills" may not include them, causing false negatives
**Mitigation:** The script reads the classification from frontmatter fields rather than maintaining a separate list. The only hardcoded check is "if `disable-model-invocation: true` is set, verify it's appropriate" — not the reverse. New skills without classification get a WARN suggesting they add one.

### Risk 3: Sub-file link validation across do-plan split
**Impact:** PR #156 split do-plan into SKILL.md + 3 sub-files. Sub-file references use relative paths like `[template](PLAN_TEMPLATE.md)`. If the audit resolves these incorrectly, it'll report false FAILs.
**Mitigation:** Resolve sub-file links relative to the skill directory (parent of SKILL.md), not the repo root.

### Risk 4: Anthropic docs change URL structure or content format
**Impact:** The LLM sync feature scrapes `code.claude.com/docs/en/skills`. If Anthropic restructures URLs or changes their docs format, fetching will break silently.
**Mitigation:** Cache last-known-good content. If fetch fails, fall back to cache and report staleness. Include a `--force-refresh` flag to bypass cache. Log fetch errors clearly.

### Risk 5: LLM hallucination in best practices extraction
**Impact:** When using an LLM to extract best practices from fetched docs, it could hallucinate rules that Anthropic never published.
**Mitigation:** The delta report shows exact source quotes alongside extracted rules. Human reviews the report before applying changes. No auto-apply without explicit `--apply` flag.

## No-Gos (Out of Scope)

- No agent directory auditing — only skills are in scope (issue #155 handles agent cleanup separately)
- No hooks auditing — `.claude/hooks/` (from PR #154) follows different patterns
- No content quality assessment — this is structural validation only
- No automated PR creation for fixes — the `--fix` flag edits in place, user commits
- No integration with CI/CD — this is a manual invocation skill
- No skill dependency analysis — just individual skill validation

## Update System

No update system changes required — this is a new skill in `.claude/skills/` which the existing `symlinks.py` system will sync to `~/.claude/skills/` automatically.

## Agent Integration

No agent integration required — this is a Claude Code skill invoked via `/do-skills-audit`. It uses standard filesystem access (Read, Bash) and produces text output. No MCP server or bridge changes needed.

## Documentation

- [ ] Create `docs/features/do-skills-audit.md` describing the skill and its validation rules
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Add entry to `.claude/skills/README.md` skills index

## Success Criteria

**Deterministic audit:**
- [ ] `/do-skills-audit` runs and produces a structured report for all 25 skills
- [ ] All 12 validation rules implemented and tested
- [ ] Sub-file link validation works for multi-file skills (do-plan with 3 sub-files, do-build with 2)
- [ ] Current skills all pass (no FAILs) — confirms rules match the standards we already enforce
- [ ] `--fix` flag auto-fixes trivial issues (missing name field)
- [ ] `--json` flag outputs machine-readable results
- [ ] `--skill <name>` flag audits a single skill

**LLM best practices sync:**
- [ ] `--sync-best-practices` fetches latest Anthropic docs and produces a delta report
- [ ] Delta report clearly shows what's new/changed/deprecated vs. our current standards
- [ ] `--apply` updates the `new-skill/SKILL_TEMPLATE.md` and `new-skill/SKILL.md` to match latest
- [ ] `--update-skills` generates specific fixes for existing skills that violate new best practices
- [ ] Fetched docs are cached (7-day TTL) to avoid redundant network requests

**General:**
- [ ] Script has unit tests covering each validation rule
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill-creator)**
  - Name: skill-creator
  - Role: Create the audit skill directory, SKILL.md, and validation script
  - Agent Type: builder
  - Resume: true

- **Builder (test-writer)**
  - Name: test-writer
  - Role: Write unit tests for all validation rules
  - Agent Type: builder
  - Resume: true

- **Validator (audit-validator)**
  - Name: audit-validator
  - Role: Run the audit against the actual repo and verify results are correct
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 0. Create audit skill and deterministic validation script
- **Task ID**: build-skill
- **Depends On**: none
- **Assigned To**: skill-creator
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/do-skills-audit/SKILL.md` with proper frontmatter
- Create `.claude/skills/do-skills-audit/scripts/audit_skills.py` implementing all 12 validation rules
- Support `--fix`, `--json`, and `--skill <name>` flags
- Ensure script exits 0 on pass, 1 on any FAIL

### 1. Create LLM best practices sync script
- **Task ID**: build-sync
- **Depends On**: none
- **Assigned To**: skill-creator
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/do-skills-audit/scripts/sync_best_practices.py`
- Implement doc fetching from `code.claude.com/docs/en/skills` and `github.com/anthropics/skills`
- Extract best practices (frontmatter fields, structural rules, description format, directory conventions)
- Compare against current `new-skill/SKILL_TEMPLATE.md`, `new-skill/SKILL.md`, and validator rules
- Generate delta report showing new/changed/deprecated practices
- Implement `--apply` to update template and validator
- Implement `--update-skills` to generate fixes for existing skills
- Cache fetched docs in `data/best_practices_cache.json` with 7-day TTL
- Wire into audit skill via `--sync-best-practices` flag

### 2. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-skill, build-sync
- **Assigned To**: test-writer
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_skills_audit.py`
- Test each of the 12 validation rules with passing and failing fixtures
- Test `--fix` mode actually corrects trivial issues
- Test JSON output format
- Test sync script's doc parsing and delta comparison (with cached test fixtures, not live fetches)

### 3. Validate against real repo
- **Task ID**: validate-audit
- **Depends On**: build-tests
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `/do-skills-audit` against the actual `.claude/skills/` directory
- Verify all current skills pass (no false positives)
- Verify the report format is clear and actionable
- If any current skills fail, determine whether the rule or the skill needs fixing
- Run `--sync-best-practices` and verify delta report is sensible

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-audit
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/do-skills-audit.md`
- Add entry to `docs/features/README.md`
- Add entry to `.claude/skills/README.md`

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python .claude/skills/do-skills-audit/scripts/audit_skills.py` — Run deterministic audit against all skills
- `python .claude/skills/do-skills-audit/scripts/audit_skills.py --json` — JSON output
- `python .claude/skills/do-skills-audit/scripts/audit_skills.py --sync-best-practices` — Fetch and compare against latest Anthropic best practices
- `pytest tests/unit/test_skills_audit.py -v` — Unit tests
- `ruff check . && black --check .` — Code quality

## Anthropic Best Practices Reference

Sources used to align validation rules and template:
- [Official skills documentation](https://code.claude.com/docs/en/skills) — frontmatter fields, progressive disclosure, invocation control
- [Anthropic's skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) — description as "PRIMARY TRIGGERING MECHANISM", directory structure, degrees of freedom
- [Anthropic skills repository](https://github.com/anthropics/skills) — official template, examples, packaging spec

Key alignment points with Anthropic's latest guidance:
- Description should contain both what + when (trigger-oriented), not just "Use when..."
- `argument-hint` field for skills accepting arguments
- `model` and `agent` fields for execution control
- 3-level progressive disclosure: metadata → SKILL.md body → linked files
- `scripts/`, `references/`, `assets/` as canonical resource subdirectories
- SKILL.md under 500 lines (Anthropic says "under 5,000 words" but we enforce stricter 500-line limit)
- Context budget: 2% of context window for all skill descriptions combined

## Open Questions

None — the requirements are well-defined by issue #153, the standards from #152/#156, and Anthropic's published skill documentation. The LLM sync feature ensures we stay aligned with Anthropic's evolving best practices without manual tracking.
