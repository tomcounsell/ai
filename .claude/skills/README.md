# Skills Index

Human-readable index of all Claude Code skills in this project. Not loaded by Claude — this is for developer reference.

| Skill | Lines | Invocation | Context | Description |
|-------|------:|------------|---------|-------------|
| add-feature | 213 | User + Model | - | Extend the system with new skills, tools, and capabilities |
| agent-browser | 336 | Model only | - | Browser automation for web testing, screenshots, data extraction |
| audit-next-tool | 160 | User + Model | - | Audit new/modified tools for quality and architecture compliance |
| checking-system-logs | 66 | Model only | - | Find bridge events, agent responses, errors in system logs |
| do-build | 115 | User + Model | fork | Execute a plan document using team orchestration |
| do-docs | 268 | User + Model | - | Cascade documentation updates after code changes |
| do-docs-audit | 298 | User + Model | fork | Audit docs against codebase for accuracy |
| do-patch | 204 | User + Model | - | Targeted fix for failing tests or review blockers |
| do-plan | 202 | User + Model | - | Create or update feature plan documents |
| do-pr-review | 218 | User + Model | fork | Review PRs with code analysis and screenshots |
| do-test | 247 | User + Model | - | Run the test suite with intelligent dispatch |
| frontend-design | 134 | User + Model | - | Create production-grade frontend interfaces |
| google-workspace | 249 | Model only | - | Gmail, Calendar, Docs, Sheets, Drive, Chat |
| new-skill | 102 | Infra | - | Generic skill creator (repo-agnostic) |
| new-valor-skill | 184 | User + Model | - | Valor-specific skill creator with repo patterns |
| prepare-app | 306 | User + Model | - | Start services and prepare app for review |
| prime | 102 | Infra | - | Codebase onboarding and architecture guide |
| pthread | 138 | User + Model | fork | Spawn parallel agents for independent tasks |
| reading-sms-messages | 38 | Model only | - | Read SMS/iMessage from macOS Messages app |
| reclassify | 52 | Infra | - | Reclassify plan type during Planning phase |
| sdlc | 284 | User + Model | - | End-to-end autonomous Plan → Build → Test → Review → Ship |
| setup | 407 | Infra | - | Configure new machine for Valor bridge |
| telegram | 68 | Model only | - | Read and send Telegram messages |
| update | 183 | Infra | - | Pull changes, sync deps, restart bridge |

**Invocation types:**
- **User + Model**: Both user and agent can trigger via `/skill-name`
- **Model only**: Agent uses as background reference (`user-invocable: false`)
- **Infra**: Infrastructure skill (`disable-model-invocation: true`)

**Context:**
- **fork**: Spawns sub-agents in separate context
- **-**: Runs in current context

**Scope:**
- Project-only (not synced to `~/.claude/skills/`): telegram, reading-sms-messages, checking-system-logs, google-workspace
- All other skills sync to personal scope via `scripts/update/symlinks.py`
