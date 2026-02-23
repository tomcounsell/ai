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
- **Thin wrapper commands deleted**: `do-build.md`, `do-plan.md`, `do-test.md`, `do-docs.md`, `do-pr-review.md`, `update.md`, `sdlc.md` — skills handle `/slash-command` invocation directly
- **Substantial commands converted to skills**: `setup.md` → `.claude/skills/setup/`

### Generic New-Skill Extracted
- Created `.claude/skills/new-skill/` — repo-agnostic skill creator
- Refactored `new-valor-skill` to be a thin Valor-flavored wrapper

### Hardlink System Updated
- `PROJECT_ONLY_SKILLS` set prevents project-specific skills from syncing to `~/.claude/skills/`
- Project-only: telegram, reading-sms-messages, checking-system-logs, google-workspace
- Retired commands added to `RENAMED_REMOVALS` for cleanup on update

## Related
- `.claude/skills/README.md` — full skills index
- `scripts/update/symlinks.py` — hardlink sync logic
