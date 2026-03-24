---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/437
last_comment_id:
---

# OOP/Data Modeling Audit Skill (do-oop-audit)

## Problem

When reviewing or inheriting Python codebases, structural anti-patterns in class design accumulate silently — god objects, boolean fields that should be timestamps, inconsistent naming, deep inheritance chains. These issues are hard to spot manually because they require cross-file analysis and semantic judgment about design intent.

**Current behavior:**
No general-purpose skill exists to audit OOP/class structure. `audit-models` is Popoto-specific and only covers Redis models. Developers must manually review every class file to find structural issues.

**Desired outcome:**
A prompt-only audit skill (`/do-oop-audit`) that works on any Python project, scans class definitions, and produces a severity-grouped findings report covering 14 structural anti-patterns. Framework-agnostic with detection for Django, SQLAlchemy, Pydantic, dataclasses, and vanilla Python.

## Prior Art

- **Issue #153**: Create /do-skills-audit command — established the `do-{subject}-audit` naming pattern for portable audit skills. Successfully shipped.
- **Issue #145**: Comprehensive Documentation Audit Skill — established the prompt-only audit pattern with severity grouping. Successfully shipped as `do-docs-audit`.
- **Issue #158**: Fix 11 skill audit warnings across 8 skills — demonstrated the audit-then-fix cycle that this skill enables for OOP code.
- **Issue #486**: Audit agent system prompts, personas, and SDLC stage enforcement — recent audit skill work, confirms the pattern is well-established.

No prior attempts at a general OOP audit skill exist.

## Appetite

**Size:** Small

**Team:** Solo dev, no review needed

**Interactions:**
- PM check-ins: 0 (spec is fully defined in the issue)
- Review rounds: 1 (standard PR review)

This is a single SKILL.md file following an established template. The issue contains complete specifications for all 14 checks, output format, and acceptance criteria. No ambiguity to resolve.

## Prerequisites

No prerequisites — this is a pure documentation/skill file with no external dependencies.

## Solution

### Key Elements

- **SKILL.md**: Single file at `.claude/skills/do-oop-audit/SKILL.md` following the `AUDIT_TEMPLATE.md` skeleton
- **Prompt-only approach**: All 14 checks require semantic judgment (god object detection, naming consistency, design intent) — no script needed
- **Framework detection**: Instructions for the model to detect Django/SQLAlchemy/Pydantic/dataclasses/vanilla and apply framework-specific heuristics

### Flow

**User invokes** `/do-oop-audit [path]` → **Skill scans** `.py` files for class definitions → **Checks run** (14 semantic checks) → **Report generated** (severity-grouped findings) → **Pause** for human review

### Technical Approach

- Follow `AUDIT_TEMPLATE.md` structure: frontmatter, what-it-does, checks, output format, disposition
- All checks are prompt-based (high autonomy) — the LLM reads class definitions and applies judgment
- Framework detection via import/base-class pattern matching described in the skill instructions
- Severity filtering via `--severity` argument parsed from `$ARGUMENTS`
- Report-only disposition: findings presented, no auto-fix

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this is a prompt-only skill (no Python script)

### Empty/Invalid Input Handling
- The skill instructions handle the empty case: if no `.py` files with class definitions are found, report "No classes found to audit"
- If `--severity` argument is invalid, default to showing all severities

### Error State Rendering
- The output format includes a summary line (PASS/WARN/FAIL counts) so even empty audits produce structured output

## Test Impact

No existing tests affected — this is a greenfield skill with no prior code. The skill is a single SKILL.md file that doesn't modify any existing behavior or interfaces.

## Rabbit Holes

- **Building a Python script/AST parser**: The issue explicitly specifies prompt-only. All 14 checks require semantic judgment. Resist the urge to write a scanner.
- **Supporting non-Python languages**: Out of scope. The skill targets Python OOP specifically.
- **Auto-fix mode**: The disposition is report-only. Don't add fix capabilities in v1.
- **Sub-files for check details**: The issue says SKILL.md under 500 lines. All 14 checks should fit in one file with concise descriptions.

## Risks

### Risk 1: SKILL.md exceeds 500-line limit
**Impact:** Skill becomes unwieldy, model context gets polluted
**Mitigation:** Keep check descriptions concise (2-3 sentences each explaining the why). Use the output format section efficiently with 2-3 examples, not one per check.

## Race Conditions

No race conditions identified — this is a read-only audit skill with no concurrent operations or shared state.

## No-Gos (Out of Scope)

- Auto-fixing any findings
- Supporting languages other than Python
- AST parsing or script-backed checks
- Integration with CI/CD pipelines
- Sub-files or reference documents beyond SKILL.md

## Update System

No update system changes required — this is a new skill file with no dependencies, config, or migration needs.

## Agent Integration

No agent integration required — this is a Claude Code slash command skill (`.claude/skills/`), not a bridge tool or MCP server. The skill is automatically available via `/do-oop-audit` once the SKILL.md file exists.

## Documentation

- [ ] Create `docs/features/do-oop-audit.md` describing the skill, its checks, and usage
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] Skill directory at `.claude/skills/do-oop-audit/SKILL.md`
- [ ] Follows `AUDIT_TEMPLATE.md` skeleton (frontmatter, what-it-does, checks, output format, disposition)
- [ ] All 14 checks defined with kebab-case name, description explaining why, and severity
- [ ] Trigger-rich description with synonyms (check, validate, review, scan, lint)
- [ ] Output format uses severity-grouped findings (CRITICAL, WARNING, INFO)
- [ ] 2-3 concrete examples with realistic data in the output section
- [ ] Framework detection (Django, SQLAlchemy, Pydantic, dataclasses, vanilla)
- [ ] Severity filtering via `--severity` argument
- [ ] Read-only — never modifies source files
- [ ] Works on any Python project, not just this repo
- [ ] SKILL.md under 500 lines
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill)**
  - Name: skill-builder
  - Role: Create SKILL.md following the audit template and issue spec
  - Agent Type: builder
  - Resume: true

- **Validator (skill)**
  - Name: skill-validator
  - Role: Verify SKILL.md meets all acceptance criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create SKILL.md
- **Task ID**: build-skill
- **Depends On**: none
- **Validates**: Line count < 500, all 14 checks present, frontmatter correct
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/skills/do-oop-audit/SKILL.md`
- Include frontmatter with trigger-rich description and synonyms
- Define all 14 audit checks with kebab-case names, why explanations, and severity levels
- Add framework detection instructions (Django, SQLAlchemy, Pydantic, dataclasses, vanilla)
- Add severity filtering via `--severity` argument
- Include output format with 2-3 concrete examples
- Set disposition to report-only

### 2. Validate SKILL.md
- **Task ID**: validate-skill
- **Depends On**: build-skill
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all 14 checks are present with correct kebab-case names
- Verify severity levels match the issue spec
- Verify SKILL.md is under 500 lines
- Verify frontmatter follows audit template pattern
- Verify output format includes severity grouping and concrete examples
- Verify framework detection instructions are present
- Verify `--severity` filtering is documented

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-skill
- **Assigned To**: skill-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/do-oop-audit.md`
- Add entry to `docs/features/README.md` index table

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria met
- Verify SKILL.md exists and follows template
- Verify docs created and indexed

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Skill exists | `test -f .claude/skills/do-oop-audit/SKILL.md` | exit code 0 |
| Under 500 lines | `wc -l < .claude/skills/do-oop-audit/SKILL.md` | output < 500 |
| All 14 checks | `grep -c '^### [0-9]' .claude/skills/do-oop-audit/SKILL.md` | output contains 14 |
| Has frontmatter | `head -1 .claude/skills/do-oop-audit/SKILL.md` | output contains --- |
| Feature doc exists | `test -f docs/features/do-oop-audit.md` | exit code 0 |
| Indexed | `grep 'do-oop-audit' docs/features/README.md` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions — the issue specification is complete with all 14 checks, output format, framework detection requirements, and acceptance criteria fully defined. Ready for critique and build.
