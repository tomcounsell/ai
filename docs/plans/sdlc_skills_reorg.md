---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2025-02-22
tracking:
---

# Skills & Agents Reorganization

## Problem

Our 15 skills, 32 agents, and 13 commands grew organically and need reorganization to align with the official Agent Skills spec (agentskills.io) and Claude Code best practices (platform.claude.com). While nothing is broken, several structural issues will cause scaling problems.

**Current behavior:**

1. **Skills are monolithic**: 10 of 15 skills have only a SKILL.md file with no sub-files. The largest (do-plan: 633 lines, do-build: 405 lines) exceed the recommended 500-line limit. All instructions, examples, and templates live in one file, defeating progressive disclosure.

2. **No scripts/ directories**: Zero skills use `scripts/` for executable automation. Everything is inline instructions. The spec recommends bundling utility scripts that execute without consuming context tokens.

3. **No references/ pattern**: Detailed reference material (API docs, schemas, templates) is embedded in SKILL.md instead of split into separate files loaded on demand.

4. **Commands and skills are redundant**: Claude Code has merged commands into skills (commands at `.claude/commands/*.md` create the same `/name` slash commands as skills). We maintain both 13 commands AND 15 skills, with the command files being thin wrappers that just invoke skills. This is unnecessary overhead.

5. **Agents lack categorization structure**: 32 agents sit flat in `.claude/agents/`. No grouping, no progressive disclosure. All agent metadata loads at startup.

6. **Missing frontmatter fields**: Skills don't use `disable-model-invocation`, `user-invocable`, `context: fork`, or `agent` fields from the latest spec. Infrastructure skills like `update` and `setup` should use `disable-model-invocation: true` to prevent Claude from auto-triggering them.

7. **Hardlink system syncs everything**: The symlinks.py system hardlinks all skills/commands/agents to `~/.claude/`, making everything personal-scope. There's no distinction between project-specific and personal skills.

**Desired outcome:**

- Skills follow progressive disclosure: SKILL.md < 500 lines, detail in sub-files
- Oversized skills split into SKILL.md + references/ + scripts/
- Commands consolidated into skills (commands that are thin wrappers become skills directly)
- Frontmatter uses all relevant fields from the latest spec
- Skills categorized by invocation pattern (model-invocable vs. user-only vs. background)
- Agent definitions remain flat but get better README documentation
- Hardlink system updated to only sync appropriate skills to personal scope

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (confirm which commands to consolidate vs. keep)
- Review rounds: 1

## Prerequisites

No prerequisites — this is internal restructuring with no external dependencies.

## Solution

### Key Elements

- **Split oversized skills**: Extract reference material and templates from do-plan, do-build, do-docs, and do-test into sub-files
- **Add frontmatter fields**: Annotate all skills with `disable-model-invocation`, `user-invocable`, and `context` where appropriate
- **Consolidate commands into skills**: Migrate command-only definitions into skill directories, retire the thin wrapper commands
- **Add scripts/ where useful**: Move executable validation/check logic into skill scripts
- **Update hardlink system**: Distinguish project-only vs. shared skills

### Flow

**Audit current skills** → Split oversized SKILL.md files into SKILL.md + references/ → Add missing frontmatter → Consolidate commands → Update symlinks.py → Validate all skills load correctly → Update README

### Technical Approach

#### Phase 1: Split oversized skills

Target skills exceeding 500 lines. Extract stable reference content into sub-files while keeping SKILL.md as a concise navigator.

| Skill | Current Lines | Split Strategy |
|-------|--------------|----------------|
| do-plan | 633 | Extract template into `PLAN_TEMPLATE.md`, Shape Up principles into `SCOPING.md`, examples into `EXAMPLES.md` |
| do-build | 405 | Extract workflow steps into `WORKFLOW.md`, error handling into `TROUBLESHOOTING.md` |
| setup | 417 (command) | Convert to skill with `references/CHECKLIST.md` |
| prepare_app | 305 (command) | Convert to skill with preparation scripts |

SKILL.md becomes a high-level guide with links: "For the plan template, see [PLAN_TEMPLATE.md](PLAN_TEMPLATE.md)".

#### Phase 2: Add frontmatter fields

Classify every skill by invocation pattern:

| Skill | Model-Invocable | User-Invocable | Context | Rationale |
|-------|----------------|----------------|---------|-----------|
| do-plan | true | true | - | Both agent and user trigger planning |
| do-build | false | true | fork | User-triggered, runs in subagent |
| do-test | true | true | - | Both can trigger tests |
| do-docs | true | true | - | Both can trigger doc updates |
| do-pr-review | false | true | fork | User-triggered review |
| do-docs-audit | false | true | fork | User-triggered audit |
| update | false | true | - | User-only infrastructure |
| setup | false | true | - | User-only infrastructure |
| sdlc | true | true | - | Both can trigger SDLC |
| agent-browser | true | false | - | Background reference for agent |
| telegram | true | false | - | Background reference for agent |
| reading-sms | true | false | - | Background reference for agent |
| checking-system-logs | true | false | - | Background reference for agent |
| google-workspace | true | false | - | Background reference for agent |
| new-valor-skill | false | true | - | User-only meta-skill |
| reclassify | false | true | - | User-only plan management |
| frontend-design | true | true | - | Both can trigger design work |

#### Phase 3: Consolidate commands into skills

Commands that are thin wrappers (< 15 lines pointing to a skill) become unnecessary. The skill itself handles `/name` invocation.

**Commands to retire** (already have corresponding skills):
- `do-build.md`, `do-plan.md`, `do-test.md`, `do-docs.md`, `do-pr-review.md`, `update.md`

**Commands to convert to skills** (substantial content, no matching skill):
- `setup.md` → `.claude/skills/setup/SKILL.md`
- `prepare_app.md` → `.claude/skills/prepare-app/SKILL.md`
- `prime.md` → `.claude/skills/prime/SKILL.md`
- `add-feature.md` → `.claude/skills/add-feature/SKILL.md`
- `pthread.md` → `.claude/skills/pthread/SKILL.md`
- `audit-next-tool.md` → `.claude/skills/audit-next-tool/SKILL.md`
- `sdlc.md` — already exists as skill, retire command

#### Phase 4: Update hardlink system

Update `scripts/update/symlinks.py` to categorize skills:

- **Project-only skills**: Those tightly coupled to this repo (telegram, reading-sms, checking-system-logs, google-workspace) — do NOT sync to `~/.claude/skills/`
- **Shared skills**: SDLC workflow skills (do-plan, do-build, etc.) and general utilities — sync to `~/.claude/skills/`

Add a `_shared` marker or config in each skill, or use a central config file.

#### Phase 5: Create skills README

Create `.claude/skills/README.md` (not loaded by Claude, for human reference):

```markdown
# Skills Index

| Skill | Description | Invocation | Lines |
|-------|-------------|------------|-------|
| do-plan | Create feature plans | User + Claude | ~200 |
| ... | ... | ... | ... |
```

## Rabbit Holes

- **Don't reorganize agents into subdirectories**: Claude Code expects flat `.claude/agents/*.md`. Adding subdirectories would break discovery. The README is sufficient organization.
- **Don't create a skill registry/database**: A README index is enough. No need for JSON manifests or automated skill discovery tooling.
- **Don't refactor skill content**: This is structural reorganization, not content rewriting. Keep the actual instructions identical; just move them into the right files.
- **Don't migrate to the API Skills format**: We use Claude Code filesystem skills, not the API upload format. They're different deployment models.

## Risks

### Risk 1: Breaking slash command discovery
**Impact:** `/do-plan` stops working if migration is incomplete
**Mitigation:** Migrate one skill at a time, test each before proceeding. Keep commands as fallbacks until skills are verified working.

### Risk 2: Hardlink breakage during transition
**Impact:** Personal skills become stale or duplicated
**Mitigation:** Run `symlinks.py` with verbose logging, verify inode matches before and after. Clean up old hardlinks explicitly.

### Risk 3: Skills exceeding character budget
**Impact:** With more skills, descriptions may exceed the 2% context window budget, causing some skills to be excluded
**Mitigation:** Keep descriptions concise (< 200 chars each). 20 skills × 200 chars = 4000 chars, well within budget.

## No-Gos (Out of Scope)

- No content rewriting — structure only
- No agent reorganization beyond README updates
- No new skills created (just existing content restructured)
- No changes to hook validators or settings.json hooks
- No changes to the bridge or agent SDK

## Update System

The update script's `symlinks.py` module needs changes to support the shared/project-only distinction. The `RENAMED_REMOVALS` list needs entries for retired commands. No other update system changes.

## Agent Integration

No agent integration required — this is a restructuring of Claude Code configuration files. The agent interacts with skills through the standard Claude Code skill discovery mechanism, which is unchanged.

## Documentation

- [ ] Create `.claude/skills/README.md` as a human-readable index of all skills
- [ ] Update `.claude/agents/README.md` to reflect any naming changes
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Code comments in `symlinks.py` explaining the shared vs. project-only distinction

## Success Criteria

- [ ] No SKILL.md exceeds 500 lines
- [ ] All skills have `description` in frontmatter
- [ ] Infrastructure skills (`update`, `setup`) have `disable-model-invocation: true`
- [ ] Background reference skills have `user-invocable: false`
- [ ] Thin wrapper commands (do-build.md, do-plan.md, etc.) retired
- [ ] Substantial commands converted to proper skill directories
- [ ] `symlinks.py` distinguishes project-only vs. shared skills
- [ ] All `/slash-command` invocations still work after migration
- [ ] `.claude/skills/README.md` exists with complete index
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill-splitter)**
  - Name: skill-splitter
  - Role: Split oversized SKILL.md files into sub-files, add frontmatter
  - Agent Type: builder
  - Resume: true

- **Builder (command-migrator)**
  - Name: command-migrator
  - Role: Convert commands to skills, retire thin wrappers
  - Agent Type: builder
  - Resume: true

- **Builder (symlinks-updater)**
  - Name: symlinks-updater
  - Role: Update symlinks.py for shared vs. project-only distinction
  - Agent Type: builder
  - Resume: true

- **Validator (skills-validator)**
  - Name: skills-validator
  - Role: Verify all skills load, slash commands work, no regressions
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create README indexes and update docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Split oversized skills
- **Task ID**: build-split-skills
- **Depends On**: none
- **Assigned To**: skill-splitter
- **Agent Type**: builder
- **Parallel**: true
- Split `do-plan/SKILL.md` (633 lines): extract plan template, scoping guide, examples into sub-files
- Split `do-build/SKILL.md` (405 lines): extract workflow details, troubleshooting into sub-files
- Keep SKILL.md as concise navigator with links to sub-files
- Verify each skill stays under 500 lines

### 2. Add frontmatter fields to all skills
- **Task ID**: build-frontmatter
- **Depends On**: build-split-skills
- **Assigned To**: skill-splitter
- **Agent Type**: builder
- **Parallel**: false
- Add `disable-model-invocation: true` to: update, setup, new-valor-skill, reclassify, do-build, do-pr-review, do-docs-audit
- Add `user-invocable: false` to: agent-browser, telegram, reading-sms, checking-system-logs, google-workspace
- Add `context: fork` where appropriate (do-build, do-pr-review, do-docs-audit)
- Verify all descriptions are < 1024 chars and written in third person

### 3. Consolidate commands into skills
- **Task ID**: build-consolidate-commands
- **Depends On**: build-frontmatter
- **Assigned To**: command-migrator
- **Agent Type**: builder
- **Parallel**: false
- Delete thin wrapper commands that duplicate existing skills: do-build.md, do-plan.md, do-test.md, do-docs.md, do-pr-review.md, update.md
- Convert substantial commands to skills: setup.md, prepare_app.md, prime.md, add-feature.md, pthread.md, audit-next-tool.md
- For each conversion: create skill directory, move content to SKILL.md, add frontmatter
- Update any cross-references in other skills/docs

### 4. Update hardlink system
- **Task ID**: build-symlinks
- **Depends On**: build-consolidate-commands
- **Assigned To**: symlinks-updater
- **Agent Type**: builder
- **Parallel**: false
- Add retired command names to `RENAMED_REMOVALS` in symlinks.py
- Add project-only skill list (skills not synced to ~/.claude/)
- Update sync logic to skip project-only skills
- Test hardlink creation and cleanup

### 5. Validate migration
- **Task ID**: validate-migration
- **Depends On**: build-symlinks
- **Assigned To**: skills-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify every `/skill-name` slash command works
- Verify no SKILL.md exceeds 500 lines
- Verify frontmatter fields are set correctly
- Verify hardlinks are correct in ~/.claude/
- Run tests to ensure no regressions

### 6. Documentation
- **Task ID**: document-changes
- **Depends On**: validate-migration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `.claude/skills/README.md` with full skills index table
- Update `.claude/agents/README.md`
- Add entry to `docs/features/README.md`

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-changes
- **Assigned To**: skills-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria
- Generate final report

## Validation Commands

- `find .claude/skills -name "SKILL.md" -exec wc -l {} + | sort -n` - Check all skill line counts
- `grep -r "disable-model-invocation" .claude/skills/*/SKILL.md` - Verify frontmatter fields
- `ls .claude/commands/` - Verify retired commands are gone
- `python -c "import json; json.load(open('.claude/settings.json'))"` - Settings still valid
- `python scripts/update/symlinks.py --dry-run` - Verify hardlink changes
- `pytest tests/ -v` - Full test suite
- `ruff check . && black --check .` - Code quality

## Open Questions

1. Should we keep the `.claude/commands/` directory at all after migration, or fully retire it? Claude Code still supports both locations but skills are the recommended path. **Recommendation:** keep the directory but empty it; the system reminder about skills clarifies the merge.
2. For the hardlink shared/project-only distinction, should we use a config file (`skills_config.json`) or a marker in each skill's frontmatter (e.g., `scope: project`)? **Recommendation:** use frontmatter `metadata.scope: project` since it follows the spec's metadata field pattern and keeps config co-located with the skill.
