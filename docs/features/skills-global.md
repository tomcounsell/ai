# Skills Global

Global skills live in `.claude/skills-global/` and are hardlinked to `~/.claude/skills/` by `scripts/update/hardlinks.py` on each `/update` run. They are available in all Claude Code sessions across all projects.

## Current Skills

| Skill | Invocation | Description |
|-------|-----------|-------------|
| audit-hooks | User + Model | Audit Claude Code hooks for correctness and completeness |
| audit-models | User + Model | Audit Popoto/Django model definitions for quality and conventions |
| audit-skills | User | Deterministic validation of all SKILL.md files |
| audit-tools | User + Model | Audit MCP and Python tools for correctness and architecture compliance |
| build-agent | User + Model | Execute the create→launch→grade→schedule loop for a Claude Managed Agent (CMA) in a client's Anthropic account |
| computer-use | User + Model | Drive native macOS apps without stealing focus |
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
| ontologies | User + Model | Build and maintain ONTOLOGIES.md domain vocabulary |
| pen-design | User + Model | Create designs via the pen.dev CLI; edit .pen files via the Pen MCP server |
| prime | Infra | Codebase onboarding and architecture guide |
| reclassify | Infra | Reclassify plan type during Planning phase |
| sdlc | User + Model | Single entry point — dispatcher for all development work |
| setup | Infra | Configure new machine for Valor bridge |
| weekly-review | User + Model | Engineering summary of recent commits organized by category |
| zoom-out | User + Model | In-session course-correction: am I solving the right problem? |

## Sync Mechanism

`scripts/update/hardlinks.py` `sync_claude_dirs()` iterates all directories in `.claude/skills-global/` and hardlinks each `SKILL.md` to `~/.claude/skills/<name>/SKILL.md`. New skills appear in `~/.claude/skills/` on the next `/update` run with no manual step.

The hardlink *is* the propagation: one inode, two paths, so editing the repo copy edits the live skill. That property is fragile in one specific way.

### Edits break the hardlink

The Write and Edit tools do not write in place. They write a replacement file and rename it over the target, which allocates a **new inode** and drops the link count to 1. The repo copy holds the edit; the `~/.claude/` copy keeps serving the pre-edit text at the old inode indefinitely. Nothing fails and nothing warns — the skill change simply does not take effect on the machine that authored it, which is how a merged skills-global change can still run on pre-merge text.

`.claude/hooks/validators/relink_global_skills.py` closes this. It is a PostToolUse hook on the `Write` and `Edit` matchers: after any write under `.claude/skills-global/`, `.claude/commands/`, or `.claude/agents/`, it relinks the `~/.claude/` destination to the current inode. It repairs rather than reports, stays silent when the link is already intact, and prints a loud warning naming the stale path if the relink itself fails.

Its `SYNCED_DIRS` map mirrors `sync_claude_dirs()` — when you add a synced directory to one, add it to the other. Regression coverage is `tests/unit/test_relink_global_skills.py`, which reproduces the replace-and-rename breakage directly rather than trusting the hook's own account of it.

To verify a skill change actually landed, compare inodes rather than diffing text:

```bash
stat -f %i .claude/skills-global/<name>/SKILL.md ~/.claude/skills/<name>/SKILL.md
```

Differing inodes mean the live copy is stale; `/update` re-establishes every link.

## Skill Liveness and Husks

A skill is *live* if and only if its directory holds a `SKILL.md` file. Directory
existence alone is not liveness — a directory can survive on disk after its `SKILL.md`
is deleted (a `__pycache__` dir, a stray reference file, an incomplete rename), and a
bare `Path.is_dir()` probe reads that leftover as a live skill. Such a directory is a
**husk**.

`tests/unit/test_update_hardlinks.py::test_no_husk_directories_in_skill_roots` fails
the build whenever a husk exists under `.claude/skills-global/` or `.claude/skills/`.
All liveness checks in that file (`_skill_exists_in_any_root`, `test_renamed_removals_entries_are_not_stale`)
route through the same `_skill_is_live` helper, so a directory-existence check cannot
silently reappear at a second call site.

The one directory allowed to lack a `SKILL.md` is `.claude/skills/_shared/` — an
intentional shared-resource directory (it tracks `test-quality.md`, consumed by other
skills rather than being one itself). It is permitted via an explicit
`HUSK_GUARD_ALLOWLIST` frozenset in `test_update_hardlinks.py`, not an underscore-prefix
convention: an allowlist of one names exactly what's exempt and fails loudly the moment
a second non-skill directory shows up, instead of a naming convention silently absorbing
anything that happens to start with `_`.

When a skill is renamed or removed, delete its directory entirely (not just `SKILL.md`)
and add a `RENAMED_REMOVALS` entry — see "Global vs. Project-Only Skills" in the root
`CLAUDE.md`. Leaving the emptied directory behind is exactly what creates a husk.

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
