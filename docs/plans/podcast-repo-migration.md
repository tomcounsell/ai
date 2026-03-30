---
status: Planning
type: chore
appetite: Large
owner: Tom
created: 2026-02-11
tracking:
---

# Podcast Production System Migration

## Problem

The podcast production system (skills, tools, workflows, agents) lives in the `research` repo, but the podcast hosting platform is being built in `cuttlefish`. This split means:

**Current behavior:**
- Podcast creation workflows (12-phase episode pipeline, series planning) are defined in `research/.claude/skills/` and `research/.claude/commands/`
- Python tools (NotebookLM API, transcription, chapter generation, feed updates, cover art) live in `research/podcast/tools/`
- Templates and documentation live in `research/docs/templates/` and `research/docs/reference/`
- The `research` repo is primarily an educational framework project that accumulated podcast tooling as a secondary concern
- Episode data (MP3s, transcripts, research) also lives in `research`, creating a monolithic coupling

**Desired outcome:**
- All podcast production tooling lives in `cuttlefish` alongside the Django hosting platform
- A single repo owns the full podcast lifecycle: production → processing → hosting → distribution
- The `research` repo is freed from podcast concerns and can focus on its educational purpose
- Episode data (MP3s, transcripts, research) will be imported directly into cuttlefish once the Django podcast hosting feature is built

## Appetite

**Size:** Large

**Team:** Solo dev + PM

**Interactions:**
- PM check-ins: 2-3 (scope alignment on what moves vs. what's adapted, path decisions)
- Review rounds: 2+ (skill/tool audits, integration testing)

This is a large migration with many interdependent files. The coding is straightforward (mostly copy + adapt paths), but alignment on destination structure and testing each tool in its new context is the real work.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Cuttlefish repo exists | `test -d /Users/valorengels/src/cuttlefish && echo OK` | Target repo |
| Research repo exists | `test -d /Users/valorengels/src/research && echo OK` | Source repo |
| uv available | `which uv` | Package management |

No prerequisites on `private-podcast-feeds.md` being implemented — the production tools are independent of the Django hosting layer.

## Solution

### What Moves

The migration covers **three categories** of assets:

#### Category 1: Claude Code Skills, Commands, and Agents

These are the AI workflow definitions that orchestrate podcast production.

| Source (research) | Destination (cuttlefish) |
|-------------------|--------------------------|
| `.claude/skills/new-podcast-episode.md` | `.claude/skills/new-podcast-episode.md` |
| `.claude/skills/podcast-series.md` | `.claude/skills/podcast-series.md` | **Immediately deprecated** — series concept replaced by multi-feed model |
| `.claude/skills/podcast-episode-planner/` | `.claude/skills/podcast-episode-planner/` |
| `.claude/skills/notebooklm-enterprise-api/` | `.claude/skills/notebooklm-enterprise-api/` |
| `.claude/skills/notebooklm-audio/` | `.claude/skills/notebooklm-audio/` |
| `.claude/skills/podcast-audio-processing/` | `.claude/skills/podcast-audio-processing/` |
| `.claude/skills/podcast-feed-validator/` | `.claude/skills/podcast-feed-validator/` |
| `.claude/skills/podcast-quality-scorecard/` | `.claude/skills/podcast-quality-scorecard/` |
| `.claude/skills/podcast-cover-art/` | `.claude/skills/podcast-cover-art/` |
| `.claude/commands/podcast-episode.md` | `.claude/commands/podcast-episode.md` |
| `.claude/commands/podcast-series.md` | `.claude/commands/podcast-series.md` | **Immediately deprecated** — series concept replaced by multi-feed model |
| `.claude/agents/podcast-synthesis-writer.md` | `.claude/agents/podcast-synthesis-writer.md` |

**Research tool skills** (used during podcast research phases):

| Source (research) | Destination (cuttlefish) | Notes |
|-------------------|--------------------------|-------|
| `.claude/skills/perplexity-deep-research/` | `.claude/skills/perplexity-deep-research/` | Generic research tool, useful beyond podcasting |
| `.claude/skills/gpt-researcher/` | `.claude/skills/gpt-researcher/` | Generic research tool |
| `.claude/skills/gemini-deep-research/` | `.claude/skills/gemini-deep-research/` | Generic research tool |
| `.claude/skills/chatgpt-deep-research/` | `.claude/skills/chatgpt-deep-research/` | Generic research tool |

#### Category 2: Python Tools

Production scripts that handle audio, feeds, cover art, and research.

| Source (research) | Destination (cuttlefish) | Notes |
|-------------------|--------------------------|-------|
| `podcast/tools/notebooklm_api.py` | `apps/podcast/tools/notebooklm_api.py` | Core — NotebookLM Enterprise API |
| `podcast/tools/notebooklm_prompt.py` | `apps/podcast/tools/notebooklm_prompt.py` | Core — episodeFocus prompt generation |
| `podcast/tools/transcribe_only.py` | `apps/podcast/tools/transcribe_only.py` | Core — Whisper transcription |
| `podcast/tools/generate_chapters.py` | `apps/podcast/tools/generate_chapters.py` | Core — AI chapter generation |
| `podcast/tools/update_feed.py` | `apps/podcast/tools/update_feed.py` | Core — RSS feed updates |
| `podcast/tools/generate_companion_resources.py` | `apps/podcast/tools/generate_companion_resources.py` | Core — summary, checklist, frameworks |
| `podcast/tools/generate_landing_page.py` | `apps/podcast/tools/generate_landing_page.py` | Core — HTML episode pages |
| `podcast/tools/cover_art.py` | `apps/podcast/tools/cover_art.py` | Core — AI cover art + branding |
| `podcast/tools/add_logo_watermark.py` | `apps/podcast/tools/add_logo_watermark.py` | Support — logo branding |
| `podcast/tools/perplexity_deep_research.py` | `apps/podcast/tools/perplexity_deep_research.py` | Research integration |
| `podcast/tools/gemini_deep_research.py` | `apps/podcast/tools/gemini_deep_research.py` | Research integration |
| `podcast/tools/gpt_researcher_run.py` | `apps/podcast/tools/gpt_researcher_run.py` | Research integration |
| `podcast/tools/gpt_researcher_config.json` | `apps/podcast/tools/gpt_researcher_config.json` | Research config |
| `podcast/tools/pyproject.toml` | Merge into cuttlefish `pyproject.toml` | Dependency consolidation |
| `podcast/tools/tests/` | `apps/podcast/tools/tests/` | Test suite |

#### Category 3: Documentation and Templates

| Source (research) | Destination (cuttlefish) | Notes |
|-------------------|--------------------------|-------|
| `docs/templates/p3-briefing-enhanced.md` | `docs/templates/podcast/p3-briefing-enhanced.md` | Wave 1 research briefing |
| `docs/templates/content_plan-enhanced.md` | `docs/templates/podcast/content_plan-enhanced.md` | Wave 2 episode plan |
| `docs/templates/metadata-enhanced.md` | `docs/templates/podcast/metadata-enhanced.md` | Wave 4 publishing metadata |
| `docs/reference/podcast-content-framework.md` | `docs/reference/podcast-content-framework.md` | Content framework |
| `docs/plans/podcast_episode_improvements.md` | `docs/plans/podcast_episode_improvements.md` | Wave history (reference) |
| `docs/plans/podcast-audio.md` | `docs/plans/podcast-audio.md` | Audio specs |
| `docs/design/components/podcast-player.html` | `docs/design/components/podcast-player.html` | Player component |
| `docs/design/components/podcast-player.css` | `docs/design/components/podcast-player.css` | Player styles |
| `docs/RSS-specification.md` | `docs/reference/RSS-specification.md` | Feed spec (if exists) |

### What Stays in Research

- **Episode content** — MP3s, transcripts, research documents, reports (imported into Django hosting later)
- **`podcast/feed.xml`** — The live GitHub Pages feed (deprecated once cuttlefish hosting is live)
- **`podcast/cover.png`**, **`podcast/yudame-logo.png`** — Branding assets (copy to cuttlefish, keep originals)
- **`podcast/subscribe.html`** — Static subscription page (replaced by cuttlefish UI)
- **Educational framework** — All learning research content stays

### Architectural Decision: Multi-Feed Model (replaces Series)

The "series" concept is deprecated. Instead, each podcast topic/audience gets its own `Podcast` (feed) with independent episode numbering starting at 1. For example, "Building a Micro-School" is one feed, a future cybersecurity topic would be another feed. No season numbers — just feed + episode number. The `podcast-series.md` skill and `/podcast-series` command are migrated for reference but immediately deprecated.

### AI Services Layer (`apps/podcast/services/`)

In addition to the migrated CLI tools, a new **PydanticAI services layer** has been built for AI-powered research processing and content generation. These Named AI Tools (see [convention](../AI_CONVENTIONS.md#named-ai-tools)) replace Claude Code sub-agent delegation for tasks like cross-validation, briefing creation, synthesis, and episode planning.

| Service | Replaces Sub-Agent |
|---------|-------------------|
| `digest_research.py` | `podcast-research-digest` |
| `discover_questions.py` | `podcast-question-discovery` |
| `cross_validate.py` | `podcast-cross-validator` |
| `write_briefing.py` | `podcast-briefing-writer` |
| `write_synthesis.py` | `podcast-synthesis-writer` |
| `plan_episode.py` | `podcast-episode-planner` |
| `write_metadata.py` | `podcast-metadata-writer` |
| `generate_chapters.py` | `tools/generate_chapters.py` |

These services return typed Pydantic models instead of writing files to disk, making them composable and testable. The Claude Code sub-agents remain available as an alternative orchestration path.

### What Gets Adapted

Every migrated file needs path updates. Key patterns:

| Old Path Pattern | New Path Pattern |
|------------------|------------------|
| `podcast/tools/` | `apps/podcast/tools/` |
| `podcast/episodes/` | `apps/podcast/pending-episodes/{feed-slug}-{episode-slug}/` |
| `../episodes/` (relative from tools) | `apps/podcast/pending-episodes/{feed-slug}-{episode-slug}/` |
| `docs/templates/` | `docs/templates/podcast/` |

Skills need their tool invocation paths updated (e.g., `cd podcast/tools && uv run python transcribe_only.py` → `cd apps/podcast/tools && uv run python transcribe_only.py`).

**Working directory convention:** All in-progress episode work happens in `apps/podcast/pending-episodes/{feed-slug}-{episode-slug}/`. This directory is gitignored. Once an episode is complete, a publish script imports all files into the Django database and the pending directory can be cleaned up.

### Flow

**Before migration:**
```
research repo                     cuttlefish repo
├── .claude/skills/podcast-*      ├── apps/podcast/ (planned, not built)
├── .claude/commands/podcast-*    ├── docs/plans/private-podcast-feeds.md
├── .claude/agents/podcast-*      └── docs/plans/private-podcast-feeds.md
├── podcast/tools/*.py
├── podcast/episodes/
├── podcast/feed.xml
└── docs/templates/
```

**After migration:**
```
research repo                     cuttlefish repo
├── podcast/episodes/ (frozen)    ├── .claude/skills/podcast-* (moved)
├── podcast/feed.xml (frozen)     ├── .claude/commands/podcast-* (moved)
└── (educational content)         ├── .claude/agents/podcast-* (moved)
                                  ├── apps/podcast/services/*.py (PydanticAI AI tools)
                                  ├── apps/podcast/tools/*.py (CLI scripts, moved)
                                  ├── apps/podcast/pending-episodes/ (gitignored, WIP)
                                  ├── docs/templates/podcast/ (moved)
                                  └── CLAUDE.md (updated with podcast workflow docs)
```

### Technical Approach

- **Copy, don't move** initially — keep research repo functional until cuttlefish is verified working
- **Update all internal paths** in skills and tools to reflect new locations
- **Merge Python dependencies** from `podcast/tools/pyproject.toml` into cuttlefish's `pyproject.toml` (as optional `[podcast]` extras group)
- **Add podcast workflow section** to cuttlefish's `CLAUDE.md`
- **Remove from research** only after full verification in cuttlefish
- **Episode working directory** is `apps/podcast/pending-episodes/{feed-slug}-{episode-slug}/` — gitignored, temporary, cleaned up after publishing to database
- **Add `pending-episodes/` to `.gitignore`**
- **Publish script** (future) reads pending episode files and imports into Django database — not part of this migration but the directory convention is established here

## Rabbit Holes

- **Don't refactor the tools during migration** — Move them as-is, then improve later. Rewriting `update_feed.py` to use Django models instead of static XML is a separate task (part of `private-podcast-feeds.md`).
- **Don't integrate tools with Django yet** — The Python tools are standalone CLI scripts. Making them Django management commands or services is future work. Move them into `apps/podcast/tools/` as standalone scripts first.
- **Don't migrate episode content in this plan** — Episode data import into the Django hosting platform is a separate task. This plan moves the production system, not the produced content.
- **Don't publish new episodes until the new system is built** — The GitHub Pages feed at `research.bwforce.ai` stays as-is (frozen). No new episodes until cuttlefish private feeds are operational.
- **Don't strip podcast-specific content from research skills** — The Perplexity/Gemini/GPT-Researcher/ChatGPT skills are general-purpose but may contain podcast-helpful context. Move them as-is without removing anything.

## Risks

### Risk 1: Path breakage across interconnected skills
**Impact:** Skills reference other skills, tools reference relative paths to episodes, templates reference specific directory structures. A single missed path update breaks the workflow.
**Mitigation:** Create a path mapping table (above). After migration, run a grep for all old paths (`podcast/tools/`, `../episodes/`, etc.) in the migrated files to catch stragglers. Test each phase of the 12-phase workflow.

### Risk 2: Dependency conflicts
**Impact:** Research repo's `podcast/tools/pyproject.toml` may have dependencies that conflict with cuttlefish's existing dependencies (version mismatches, incompatible packages).
**Mitigation:** Add podcast dependencies as an optional extras group `[podcast]` in `pyproject.toml` so they can be installed separately. Resolve conflicts before merging into main deps.

### Risk 3: Environment variable differences
**Impact:** Research tools expect API keys in a `.env` file with specific names. Cuttlefish uses `.env.local` with Django settings.
**Mitigation:** Audit all `os.environ` / `dotenv` calls in podcast tools. Align env var names. Document required vars in the plan and CLAUDE.md.

### Risk 4: Episode working directory ambiguity
**Impact:** Tools currently assume episodes live at `../episodes/` relative to the tools dir. After migration, work-in-progress episodes use a new temporary directory structure.
**Mitigation:** All tools use `apps/podcast/pending-episodes/{feed-slug}-{episode-slug}/` as the working directory. Skills pass explicit paths. The `pending-episodes/` directory is gitignored and cleaned up after publishing to the database.

## No-Gos (Out of Scope)

- Building the Django `apps/podcast/` hosting platform (covered by `private-podcast-feeds.md`)
- Migrating episode audio/content to R2 (separate task after Django hosting is built)
- Refactoring tools into Django management commands
- Rewriting `update_feed.py` to generate feeds from Django models
- Building a web UI for podcast production workflow
- Deprecating or removing content from the research repo
- Wave 6 format experiments

## Update System

No update system changes required — this is a codebase reorganization within the cuttlefish repo. The tools are developer-local and not deployed as a service.

## Agent Integration

No agent integration required — these are Claude Code skills and CLI tools used in development sessions, not MCP-exposed capabilities. The podcast production workflow is human-initiated via `/podcast-episode` and `/podcast-series` slash commands.

Future consideration: an MCP tool that triggers episode creation could be built later, but that's out of scope.

## Documentation

### Feature Documentation
- [ ] Add podcast workflow section to cuttlefish `CLAUDE.md` (adapted from research `CLAUDE.md`)
- [ ] Update `docs/plans/private-podcast-feeds.md` to reference new tool locations

### Inline Documentation
- [ ] Update path references in all migrated skills
- [ ] Update path references in all migrated tools
- [ ] Add brief README in `apps/podcast/tools/` explaining the tool suite

## Success Criteria

- [ ] All 12 podcast skills copied to cuttlefish `.claude/skills/`
- [ ] Both podcast commands copied to cuttlefish `.claude/commands/`
- [ ] Synthesis writer agent copied to cuttlefish `.claude/agents/`
- [ ] All Python tools copied to cuttlefish `apps/podcast/tools/`
- [ ] All templates copied to cuttlefish `docs/templates/podcast/`
- [ ] All internal paths updated (no references to `research` repo paths in skills/tools)
- [ ] Python dependencies merged into cuttlefish `pyproject.toml`
- [ ] `uv sync --all-extras` succeeds with no errors
- [ ] Podcast tool tests pass: `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tools/tests/ -v`
- [ ] Cuttlefish `CLAUDE.md` includes podcast workflow documentation
- [ ] `/podcast-episode` command works from cuttlefish repo
- [ ] Research repo skills/tools/agents marked as deprecated (comment header, not deleted)
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (skills-migration)**
  - Name: skills-migrator
  - Role: Copy and adapt all Claude Code skills, commands, and agents
  - Agent Type: builder
  - Resume: true

- **Builder (tools-migration)**
  - Name: tools-migrator
  - Role: Copy Python tools, merge dependencies, adapt paths
  - Agent Type: builder
  - Resume: true

- **Builder (docs-migration)**
  - Name: docs-migrator
  - Role: Copy templates, update CLAUDE.md, update existing plans
  - Agent Type: builder
  - Resume: true

- **Validator (full-pipeline)**
  - Name: pipeline-validator
  - Role: Verify all paths resolve, tests pass, workflow is functional
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create destination directories
- **Task ID**: build-directories
- **Depends On**: none
- **Assigned To**: skills-migrator
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/`, `.claude/agents/` directories in cuttlefish
- Create `apps/podcast/tools/` and `apps/podcast/tools/tests/` directories
- Create `apps/podcast/pending-episodes/` directory and add to `.gitignore`
- Create `docs/templates/podcast/` directory

### 2. Migrate Claude Code skills
- **Task ID**: build-skills
- **Depends On**: build-directories
- **Assigned To**: skills-migrator
- **Agent Type**: builder
- **Parallel**: true
- Copy all 12 podcast skill files/directories from research to cuttlefish
- Copy 4 research tool skills (perplexity, gemini, gpt-researcher, chatgpt)
- Copy 2 command files
- Copy 1 agent file
- Add deprecation header to `podcast-series.md` skill and `podcast-series.md` command immediately after copying
- **Use parallel sub-agents (pthread) for path updates** — several skill files are very large (e.g., `new-podcast-episode.md` is 77KB). Spawn one sub-agent per large file to update paths concurrently:
  - `podcast/tools/` → `apps/podcast/tools/`
  - `podcast/episodes/` → `apps/podcast/pending-episodes/{feed-slug}-{episode-slug}/`
  - `docs/templates/` → `docs/templates/podcast/`
  - `../episodes/` → `apps/podcast/pending-episodes/`
  - Cross-references between skills (skill A invoking skill B)

### 3. Migrate Python tools
- **Task ID**: build-tools
- **Depends On**: build-directories
- **Assigned To**: tools-migrator
- **Agent Type**: builder
- **Parallel**: true (with build-skills)
- Copy all Python scripts from `research/podcast/tools/` to `cuttlefish/apps/podcast/tools/`
- Copy test files
- **Use parallel sub-agents** to audit and update all relative path references in scripts (multiple files can be updated concurrently)
- Audit `os.environ` / `dotenv` usage — align with cuttlefish `.env.local` conventions
- Merge podcast-specific dependencies from `podcast/tools/pyproject.toml` into cuttlefish `pyproject.toml` (as `[project.optional-dependencies.podcast]` group)
- Run `uv sync --all-extras` to verify dependency resolution

### 4. Migrate documentation and templates
- **Task ID**: build-docs
- **Depends On**: build-directories
- **Assigned To**: docs-migrator
- **Agent Type**: builder
- **Parallel**: true (with build-skills, build-tools)
- Copy templates to `docs/templates/podcast/`
- Copy reference docs
- Copy design components
- Copy improvement plans (as historical reference)
- Add podcast workflow section to cuttlefish `CLAUDE.md` (adapted from research, with updated paths)
- Update `private-podcast-feeds.md` tool path references

### 5. Validate full migration
- **Task ID**: validate-migration
- **Depends On**: build-skills, build-tools, build-docs
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Grep all migrated files for old paths (`/research/`, `podcast/tools/`, `../episodes/`)
- Verify `uv sync --all-extras` succeeds
- Run `pytest apps/podcast/tools/tests/ -v`
- Verify every skill file has valid, resolvable cross-references
- Verify CLAUDE.md podcast section is accurate
- Report pass/fail with details

### 6. Deprecate research copies
- **Task ID**: deprecate-research
- **Depends On**: validate-migration
- **Assigned To**: docs-migrator
- **Agent Type**: builder
- **Parallel**: false
- Add deprecation header to each skill/command/agent in research repo: `> DEPRECATED: Moved to cuttlefish repo. This copy is kept for reference only.`
- Update research `CLAUDE.md` to note that podcast tooling has moved
- Do NOT delete any files — just mark as deprecated

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: deprecate-research
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands below
- Confirm all success criteria met
- Generate final report

## Validation Commands

- `ls /Users/valorengels/src/cuttlefish/.claude/skills/new-podcast-episode.md` — Main workflow skill exists
- `ls /Users/valorengels/src/cuttlefish/.claude/commands/podcast-episode.md` — Slash command exists
- `ls /Users/valorengels/src/cuttlefish/.claude/agents/podcast-synthesis-writer.md` — Agent exists
- `ls /Users/valorengels/src/cuttlefish/apps/podcast/tools/notebooklm_api.py` — Core tool exists
- `ls /Users/valorengels/src/cuttlefish/docs/templates/podcast/p3-briefing-enhanced.md` — Template exists
- `grep -r "podcast/tools/" /Users/valorengels/src/cuttlefish/.claude/ | grep -v "apps/podcast/tools/"` — No old paths in skills (should return empty)
- `grep -r "/research/" /Users/valorengels/src/cuttlefish/.claude/ /Users/valorengels/src/cuttlefish/apps/podcast/tools/` — No hardcoded research paths (should return empty)
- `cd /Users/valorengels/src/cuttlefish && uv sync --all-extras` — Dependencies resolve
- `cd /Users/valorengels/src/cuttlefish && DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tools/tests/ -v` — Tests pass
- `grep -l "DEPRECATED" /Users/valorengels/src/research/.claude/skills/new-podcast-episode.md` — Research copy deprecated

---

## Resolved Decisions

1. **Episode working directory**: Use `apps/podcast/pending-episodes/{feed-slug}-{episode-slug}/` as a gitignored temporary directory for all WIP. A publish script (built later) imports into the database. Files cleaned up after publishing is verified.

2. **Research tool skills scope**: Keep as general-purpose skills at `.claude/skills/` (not nested under a research/ subdirectory). Don't strip any podcast-specific content that may be helpful.

3. **Python tools destination**: `apps/podcast/tools/` — co-located with the Django app.

4. **Feed publishing during transition**: Paused. No new episodes published until the cuttlefish hosting system is built. The research repo feed is frozen as-is.
