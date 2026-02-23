---
status: Planning
type: feature
appetite: Small
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

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a straightforward validation tool. The rules are already defined by #152/#156. Implementation is mechanical — read files, check rules, report results.

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

**User invokes `/do-skills-audit`** → Skill runs `audit_skills.py` → Script scans all SKILL.md files → Checks each against rules → Outputs structured report → Skill summarizes findings

### Technical Approach

#### Validation Rules

Each SKILL.md is checked against these rules (derived from the canonical template and #152/#156 decisions):

**Structural rules:**
1. **Line count**: SKILL.md must be <= 500 lines (FAIL if exceeded)
2. **Frontmatter exists**: Must have YAML frontmatter delimited by `---` (FAIL if missing)
3. **Name field**: Must be present, lowercase, hyphens only, max 64 chars (FAIL if invalid)
4. **Description field**: Must be present and start with "Use when" (WARN if missing trigger phrase)
5. **Description length**: Must be <= 1024 characters (WARN if exceeded)

**Classification rules:**
6. **Infrastructure skills** (`update`, `setup`, `reclassify`, `new-skill`, `new-valor-skill`, `prime`): Must have `disable-model-invocation: true` (WARN if missing)
7. **Background reference skills** (`agent-browser`, `telegram`, `reading-sms-messages`, `checking-system-logs`, `google-workspace`): Must have `user-invocable: false` (WARN if missing)
8. **Fork skills** (`do-build`, `do-pr-review`, `do-docs-audit`, `pthread`, `do-design-review`, `sdlc`): Should have `context: fork` (WARN if missing)

**Content rules:**
9. **Sub-file references**: Any `[text](file.md)` link must point to a file that exists in the skill directory (FAIL if broken). This is critical now that PR #156 introduced sub-files — do-plan has 3 sub-files (PLAN_TEMPLATE.md, SCOPING.md, EXAMPLES.md) and do-build has 2 (WORKFLOW.md, PR_AND_CLEANUP.md).
10. **No duplicate descriptions**: No two skills should have identical or near-identical description fields (WARN if found)

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

#### Skill Design

The SKILL.md is minimal — it describes when to use the audit, then shells out to the script:

```bash
python "$CLAUDE_PROJECT_DIR/.claude/skills/do-skills-audit/scripts/audit_skills.py"
```

The skill can also accept arguments:
- `--fix` — auto-fix trivial issues (add missing `name` field from directory name, trim trailing whitespace)
- `--json` — output only JSON (for piping to other tools)
- `--skill <name>` — audit a single skill instead of all

## Rabbit Holes

- **Don't use LLM for validation**: All checks are deterministic string/regex operations. No need for AI-powered analysis — that would add latency and cost for no benefit.
- **Don't enforce content structure beyond frontmatter**: Checking for specific sections (## Workflow, ## Examples, etc.) is too rigid. Skills have different needs. Only enforce frontmatter and structural constraints.
- **Don't auto-fix content issues**: Auto-fix should only handle mechanical problems (missing name field, whitespace). Never rewrite descriptions or restructure content.
- **Don't build a watch mode**: A one-shot audit is sufficient. CI integration or file watchers would be over-engineering.
- **Don't audit hooks**: PR #154 added `.claude/hooks/` with validators and post-tool-use hooks. These follow different patterns than skills and are out of scope.
- **Don't audit agent files**: Issue #155 will trim agents to 6. Agent files have no canonical template — auditing them adds no value.

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

- [ ] `/do-skills-audit` runs and produces a structured report for all 25 skills
- [ ] All 10 validation rules implemented and tested
- [ ] Sub-file link validation works for multi-file skills (do-plan with 3 sub-files, do-build with 2)
- [ ] Current skills all pass (no FAILs) — confirms rules match the standards we already enforce
- [ ] `--fix` flag auto-fixes trivial issues (missing name field)
- [ ] `--json` flag outputs machine-readable results
- [ ] `--skill <name>` flag audits a single skill
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

### 0. Create audit skill and validation script
- **Task ID**: build-skill
- **Depends On**: none
- **Assigned To**: skill-creator
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/do-skills-audit/SKILL.md` with proper frontmatter
- Create `.claude/skills/do-skills-audit/scripts/audit_skills.py` implementing all 10 validation rules
- Support `--fix`, `--json`, and `--skill <name>` flags
- Ensure script exits 0 on pass, 1 on any FAIL

### 1. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-skill
- **Assigned To**: test-writer
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_skills_audit.py`
- Test each validation rule with passing and failing fixtures
- Test `--fix` mode actually corrects trivial issues
- Test JSON output format

### 2. Validate against real repo
- **Task ID**: validate-audit
- **Depends On**: build-tests
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `/do-skills-audit` against the actual `.claude/skills/` directory
- Verify all current skills pass (no false positives)
- Verify the report format is clear and actionable
- If any current skills fail, determine whether the rule or the skill needs fixing

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-audit
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/do-skills-audit.md`
- Add entry to `docs/features/README.md`
- Add entry to `.claude/skills/README.md`

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python .claude/skills/do-skills-audit/scripts/audit_skills.py` — Run audit against all skills
- `python .claude/skills/do-skills-audit/scripts/audit_skills.py --json` — JSON output
- `pytest tests/unit/test_skills_audit.py -v` — Unit tests
- `ruff check . && black --check .` — Code quality

## Open Questions

None — the requirements are well-defined by issue #153 and the standards from #152/#156. The skill count (currently 25) and classification lists reflect the post-PR-154/156 state. If issue #155 ships first, no changes needed here — the audit dynamically discovers skills from the filesystem.
