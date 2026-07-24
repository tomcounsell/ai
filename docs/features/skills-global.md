# Skills Global

Global skills live in `.claude/skills-global/` and are hardlinked to `~/.claude/skills/` by `scripts/update/hardlinks.py` on each `/update` run. They are available in all Claude Code sessions across all projects.

## Current Skills

| Skill | Invocation | Description |
|-------|-----------|-------------|
| analyze | User + Model | Strategic business analysis — decisions, ideas, plans, tradeoffs |
| audit-hooks | User + Model | Audit Claude Code hooks for correctness and completeness |
| audit-models | User + Model | Audit Popoto/Django model definitions for quality and conventions |
| audit-skills | User | Deterministic validation of all SKILL.md files |
| audit-tools | User + Model | Audit MCP and Python tools for correctness and architecture compliance |
| build-agent | User + Model | Execute the create→launch→grade→schedule loop for a Claude Managed Agent (CMA) in a client's Anthropic account |
| claude-standards | Infra | Anthropic best-practice standards reference for skill authoring |
| computer-use | User + Model | Drive native macOS apps without stealing focus |
| deepen | User + Model | Add structured logging, metrics, and tracing to a specified module |
| do-build | User + Model | Execute a plan document using team orchestration |
| do-debrief | User + Model | Send a spoken debrief to a Telegram chat via TTS |
| do-deploy | Infra | Deploy merged changes to production across bridge machines |
| do-deploy-example | Infra | Template for creating a repo-specific /do-deploy skill |
| do-design-audit | User + Model | Audit an existing web UI against premium design criteria |
| do-design-system | User + Model | Translate a moodboard into design system tokens and components |
| do-discover-paths | User + Model | Discover happy paths on a target site using BYOB MCP |
| do-docs | User + Model | Cascade documentation updates after code changes |
| do-integration-audit | User + Model | Audit integration test coverage for gaps |
| do-investigation-issue | User + Model | Post a GitHub investigation issue for an unverified finding |
| do-issue | User + Model | Create a self-contained GitHub issue |
| do-merge | User + Model | Merge a PR that has cleared all SDLC pipeline gates |
| do-oop-audit | User + Model | Audit code for OOP discipline and deep-module principles |
| do-patch | User + Model | Targeted fix for failing tests or review blockers |
| do-plan | User + Model | Create or update feature plan documents |
| do-plan-critique | User + Model | War-room review of a plan before build |
| do-pr-review | User + Model | Review PRs with code analysis and visual proof |
| do-presentation | User + Model | Create a polished Marp presentation |
| do-sdlc | User + Model | Supervise a full SDLC pipeline run end-to-end until merge |
| do-test | User + Model | Run the test suite with intelligent dispatch |
| do-voice-recording | User + Model | Convert text to a spoken-audio file (OGG/Opus) via Kokoro or OpenAI TTS |
| email | User + Model | Read and send email via Gmail MCP or IMAP bridge |
| frontend-design | User + Model | Create production-grade frontend interfaces |
| google-workspace | User + Model | Access Google Workspace services (Gmail, Calendar, Docs, Drive, Sheets) |
| grill-me | User + Model | Socratic interrogation of the human — one question at a time |
| imagine-agent | User + Model | Interview a client and produce a build-sheet.json handoff for /build-agent |
| mermaid-render | User + Model | Render Mermaid diagrams via BYOB browser |
| new-audit-skill | User + Model | Create a new audit skill from the established pattern |
| new-skill | Infra | Generic skill creator (repo-agnostic) |
| observability | User + Model | Wire up dashboards, alerts, and health checks for a module |
| ontologies | User + Model | Build and maintain ONTOLOGIES.md domain vocabulary |
| pen-design | User + Model | Create designs via the pen.dev CLI; edit .pen files via the Pen MCP server |
| prime | Infra | Codebase onboarding and architecture guide |
| pthread | User + Model | Spawn parallel agents for independent tasks |
| reclassify | Infra | Reclassify plan type during Planning phase |
| sdlc | User + Model | Single entry point — dispatcher for all development work |
| setup | Infra | Configure new machine for Valor bridge |
| skillify | User only | Capture a session's repeatable process into a reusable skill |
| tdd | User + Model | Red-green-refactor loop with hard sequencing constraint |
| weekly-review | User + Model | Engineering summary of recent commits organized by category |
| zoom-out | User + Model | In-session course-correction: am I solving the right problem? |

## Sync Mechanism

`scripts/update/hardlinks.py` `sync_claude_dirs()` iterates all directories in `.claude/skills-global/` and hardlinks each `SKILL.md` to `~/.claude/skills/<name>/SKILL.md`. New skills appear in `~/.claude/skills/` on the next `/update` run with no manual step.

## Invocation Types

- **User + Model**: Both user and agent can trigger via `/skill-name`
- **Model only**: Agent uses as background reference (`user-invocable: false`)
- **Infra**: Infrastructure skill (`disable-model-invocation: true`)
- **User only**: User can invoke; model does not self-trigger

## Adding a New Global Skill

1. Create `.claude/skills-global/<name>/SKILL.md` using the template from `new-skill`
2. Add a row to the Current Skills table above
3. Add a row to `docs/features/README.md` in the Skills section
4. Run `/update` to hardlink to `~/.claude/skills/`
