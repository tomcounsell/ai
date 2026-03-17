# Skills & Agents Reorganization

Structural reorganization of Claude Code skills, commands, and agents to follow the canonical SKILL.md template, progressive disclosure patterns, and proper frontmatter classification.

## What Changed

### Skills Split (Progressive Disclosure)
Oversized SKILL.md files were split into a concise navigator + sub-files:
- `do-plan`: Plan template, scoping guide, and examples extracted to sub-files
- `do-build`: Workflow details and troubleshooting extracted to sub-files
- All SKILL.md files now under 500 lines

### Frontmatter Classification
Every skill now has proper frontmatter fields:
- `disable-model-invocation: true` on infrastructure skills (update, setup, reclassify)
- `user-invocable: false` on background reference skills (agent-browser, telegram, reading-sms, checking-system-logs, google-workspace)
- `context: fork` on skills that spawn parallel work (do-build, do-pr-review, do-docs-audit)

### Commands Consolidated
- **Thin wrapper commands deleted**: `do-build.md`, `do-plan.md`, `do-test.md`, `do-docs.md`, `do-pr-review.md`, `update.md` — skills handle `replace with concrete examples like `/do-plan`, `/do-build`, etc. or clarify as a pattern placeholder` invocation directly
- **Substantial commands converted to skills**: `setup.md`, `prepare_app.md`, `prime.md`, `add-feature.md`, `pthread.md`, `audit-next-tool.md`, `sdlc.md` — each now has a proper `this is a template pattern; either keep with explicit note that `{name}` is a placeholder or replace with concrete example like `.claude/skills/do-plan/SKILL.md` with frontmatter
- **`.claude/commands/` is now empty** — all functionality migrated to skills

### Generic New-Skill Extracted
- Created `.claude/skills/new-skill/` — repo-agnostic skill creator
- Refactored `new-valor-skill` to be a thin Valor-flavored wrapper

### Hardlink System Updated
- `PROJECT_ONLY_SKILLS` set prevents project-specific skills from syncing to `verify and clarify the hardlink destination path; if this directory doesn't exist in the codebase, remove reference or explain it as an external installation target`
- Project-only: telegram, reading-sms-messages, checking-system-logs, google-workspace
- Retired commands added to `RENAMED_REMOVALS` for cleanup on update

## Related
- `.claude/skills/README.md` — full skills index
- `scripts/update/hardlinks.py` — hardlink sync logic
