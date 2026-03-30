---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-02-13
tracking: https://github.com/yudame/cuttlefish/issues/54
---

# Public vs Private Feed: Dynamic Content Plan Variables

## Problem

The podcast workflow produces identical content packaging regardless of whether an episode targets a public or private feed. Sponsor breaks, CTAs, persona stories, companion resource gating, and depth assumptions are all hardcoded for the public "Yudame Research" feed.

**Current behavior:**
Every episode gets the same public-facing branding ("Welcome to Yuda Me Research by Valor Engels..."), the same CTA ("subscribe and share"), the same sponsor splice point placeholder, and the same companion resource links pointing to `research.bwforce.ai`. Private feed episodes go through the same pipeline and then require manual post-processing to strip/replace these elements.

**Desired outcome:**
Episode metadata declares its target feed (public or private). The content plan, metadata, NotebookLM prompt, and companion resources automatically adapt their packaging based on feed type - while sharing the same research pipeline (Phases 1-7) and core content.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM. 1-2 check-ins on the variable schema design and which elements should actually differ.

**Interactions:**
- PM check-ins: 1-2 (scope alignment on what changes per feed, what stays same)
- Review rounds: 1 (validate the variable system works end-to-end)

## Prerequisites

No prerequisites - this work has no external dependencies. All changes are to the podcast workflow tooling and templates.

## Solution

### Key Elements

- **PodcastConfig OneToOne model**: Production workflow settings (scripts, depth, sponsor, companion access) separated from the `Podcast` publishing model — managed via Django admin inline
- **Episode-level feed declaration**: `setup_episode.py` accepts a `--podcast` parameter, queries the DB, and snapshots the config into `episode_config.json` for offline tool use
- **Conditional template variables**: Skills, agents, and tools read feed context and adapt output accordingly
- **Unchanged research pipeline**: Phases 1-7 remain identical regardless of feed type

### Flow

**`setup_episode.py --podcast stablecoin --slug overview`** → Creates episode directory under `pending-episodes/stablecoin/overview/` with `episode_config.json` containing feed context → **Phases 1-7** (research, unchanged) → **Phase 8: Episode Planner** reads `episode_config.json`, adapts CTA/branding/sponsor sections → **Phase 9: NotebookLM** reads config for opening/closing scripts → **Phase 11: Metadata/Publishing** adapts URLs, companion gating, feed-specific metadata

### Technical Approach

1. **Create a `PodcastConfig` OneToOne model** linked to `Podcast`. The `Podcast` model owns published feed metadata (title, slug, description, `is_public`). The new `PodcastConfig` model owns production workflow settings — these have a different management scope and audience.

   **`apps/podcast/models/podcast_config.py`:**
   ```python
   class PodcastConfig(Timestampable):
       podcast = models.OneToOneField(Podcast, on_delete=models.CASCADE, related_name="config")
       opening_script = models.TextField(blank=True)      # NotebookLM opening
       closing_script = models.TextField(blank=True)      # NotebookLM closing
       depth_level = models.CharField(max_length=20, choices=[
           ("accessible", "Accessible"),
           ("intermediate", "Intermediate"),
           ("advanced", "Advanced"),
       ], default="accessible")
       sponsor_break = models.BooleanField(default=True)  # Include sponsor splice point
       companion_access = models.CharField(max_length=20, choices=[
           ("public", "Public"),
           ("gated", "Gated"),
       ], default="public")
   ```

   The `Podcast` model keeps its existing published fields:
   - `title` → podcast name (customer-facing)
   - `is_public` → feed visibility
   - `website_url` → domain for links

   Producer credit ("Produced by Valor Engels") is a constant in the tools, not per-podcast.

   **Depth/pacing model (future — start simple, evolve later):**
   The `depth_level` field is a placeholder for a richer per-customer pacing system. The actual variables it controls:
   - **Information rate**: how quickly topics switch (dense vs. spacious)
   - **Explanation thoroughness**: how carefully concepts are unpacked
   - **Simplification level**: how much jargon is assumed vs. explained

   This will eventually be:
   - Collected at customer intake (initial comfort level)
   - Updated over time via customer feedback/ratings
   - Adaptive per customer, not just per podcast

   For now, the model stores a simple choice field that the episode planner uses as guidance. The future system will layer feedback-driven adjustments on top.

   **Private feed messaging patterns (future — document as model fields later, don't build yet):**
   - CTA: link in show notes to share this private episode with a colleague (controlled sharing)
   - Confidentiality notice: "This podcast may contain private company information and should not be shared outside the business"
   - Instead of sponsor break: reiterate service value + how to refer a friend
   - Reminder to queue new topics for your feed before finishing this episode
   - How to give feedback on the episode/service

   These will eventually become model fields (e.g. `confidentiality_notice`, `sponsor_replacement_message`, `feedback_instructions`) but for now the model just needs the structural flags (`sponsor_break`, `is_public`) so the workflow knows to leave space for them.

   **Why OneToOne model over fields on Podcast or a JSON file:**
   - **Separation of concerns**: `Podcast` owns published feed metadata (title, description, categories). `PodcastConfig` owns production workflow settings (scripts, depth, sponsor behavior). Different audiences manage each.
   - **Independent evolution**: production config fields will grow (future: `confidentiality_notice`, `feedback_instructions`, adaptive pacing) without bloating the publishing model.
   - New customer onboarding = creating a Podcast record + PodcastConfig via admin
   - Future adaptive depth levels can be updated via admin/API without code changes
   - No duplication between database state and a config file
   - `backfill_episodes.py` already creates Podcast records — can be extended to create default PodcastConfig records

2. **Update `setup_episode.py`** to accept `--podcast <slug>` (defaults to `yudame-research`). The script queries the Podcast model to validate the slug exists, then writes a lightweight `episode_config.json` to the episode directory as a snapshot of the podcast config at creation time. This snapshot lets tools work offline without hitting the database.

3. **Create a shared utility `apps/podcast/tools/episode_config.py`** that any tool can import to load config from an episode directory. Resolution order: (1) read `episode_config.json` if present, (2) infer podcast slug from directory path (e.g., `pending-episodes/stablecoin/*`) and query the Podcast model, (3) fall back to public yudame-research defaults. This ensures legacy stablecoin/Solomon Islands episodes work without explicit config files.

4. **Update tools that produce feed-specific output:**
   - `notebooklm_prompt.py` - read config for opening/closing scripts, CTA
   - `generate_landing_page.py` - read config for domain/URLs
   - `generate_companion_resources.py` - read config for access gating
   - `update_feed.py` - skip (confirmed obsolete, replaced by Django dynamic feed views)

5. **Update agent prompts/templates that reference brand elements:**
   - `podcast-episode-planner` agent/skill - read config for CTA, sponsor break section, depth level
   - `podcast-metadata-writer` agent - read config for domain, CTA, resource URLs

6. **Update `new-podcast-episode.md` skill** to pass `--podcast` in setup examples and propagate config context to sub-agents.

## Existing Episode Migration Context

The stablecoin and Solomon Islands series already exist as private Podcasts in the database (`is_public=False`). These episodes were imported via `backfill_episodes.py` from the research repo. The series-to-podcast mapping is:

| Series Directory | Podcast Slug | Public |
|---|---|---|
| `stablecoin-series` | `stablecoin` | No |
| `solomon-islands-telecom-series` | `solomon-islands-telecom` | No |
| `active-recovery`, `algorithms-for-life`, etc. | `yudame-research` | Yes |

These existing private-feed episodes will **not be reproduced** through the new workflow. Some may be deleted. However, they must remain compatible with:
- The `backfill_episodes.py` import command (idempotent, may re-run for audits)
- The `publish_episode.py` command (already extracts podcast_slug from directory path)
- The Django feed views (already filter by `Podcast.is_public`)

The config system must recognize these legacy episodes. When `episode_config.py` encounters an episode under `pending-episodes/stablecoin/` or `pending-episodes/solomon-islands-telecom/`, it should infer the podcast slug from the directory structure and load the corresponding config - even without an explicit `episode_config.json` file.

## Rabbit Holes

- **Building a custom UI for podcast config management** - Django admin already handles this. No need for a bespoke settings page.
- **Dynamic template rendering engine** - simple if/else in Python tools and conditional sections in agent prompts are sufficient. No need for Jinja2 or a template system.
- **Authentication/gating for companion resources** - out of scope for this issue. The config declares "gated" but actual gating is a separate concern (private-podcast-feeds.md plan).
- **Updating `update_feed.py`** - confirmed obsolete; replaced by Django dynamic feed views at `/podcast/{slug}/feed.xml`. Will be removed as part of this work (cleanup, not a rabbit hole).
- **Re-processing existing episodes** - the stablecoin and Solomon Islands episodes were produced before this system. Don't try to retroactively generate `episode_config.json` for them or repackage their content.

## Risks

### Risk 1: Backward compatibility with existing episodes
**Impact:** Existing stablecoin and Solomon Islands episodes lack `episode_config.json`. The `backfill_episodes.py` command may re-run for audits. Tools must not break on these legacy directories.
**Mitigation:** `episode_config.py` uses a two-step resolution: (1) check for `episode_config.json` in the episode directory, (2) if absent, infer podcast slug from the directory path (e.g., `pending-episodes/stablecoin/*` → stablecoin config), (3) fall back to public Yudame Research defaults. This mirrors the existing `SERIES_TO_PODCAST` mapping in `backfill_episodes.py`.

### Risk 2: Agent prompt drift
**Impact:** Agent prompts (`.claude/agents/*.md`) contain hardcoded brand elements. If we only update the config but agents still have hardcoded values, the config gets ignored.
**Mitigation:** Audit all agent and skill files for hardcoded brand elements. Replace with instructions to read episode config. The plan includes a validation step.

### Risk 3: Directory structure ambiguity
**Impact:** `setup_episode.py` currently creates directories as `pending-episodes/{date}-{slug}/` (standalone) or `pending-episodes/{series}/{ep-slug}/` (series). Adding `--podcast` introduces a third pattern: `pending-episodes/{podcast-slug}/{ep-slug}/`. This could conflict with the series pattern.
**Mitigation:** The `--podcast` parameter replaces the implicit series-based nesting. When `--podcast` is provided, the directory is `pending-episodes/{podcast-slug}/{ep-slug}/`. When omitted, it defaults to the existing yudame-research public config. The `--series` parameter continues to work for series metadata within a podcast but no longer drives directory structure.

## No-Gos (Out of Scope)

- Actual sponsor break audio insertion or splicing (separate feature)
- Companion resource authentication/gating implementation
- Private feed URL generation or token-based access (covered by `private-podcast-feeds.md`)
- Changes to the research pipeline (Phases 1-7 unchanged)
- Re-processing or repackaging existing stablecoin/Solomon Islands episodes
- Deleting or archiving legacy episodes (separate operational decision)

## Update System

No update system changes required - this feature is purely internal to the podcast production workflow tooling.

## Agent Integration

No agent integration required beyond updating existing agent prompt files. The changes affect the podcast workflow skill and sub-agent prompts, not the Telegram bridge or MCP servers.

## Documentation

### Feature Documentation
- [ ] Update `.claude/skills/new-podcast-episode.md` with `--podcast` parameter usage
- [ ] Document `podcast_configs.json` schema in a comment block within the file

### Inline Documentation
- [ ] Docstring on `episode_config.py` explaining the config loading flow and defaults

## Success Criteria

- [ ] `setup_episode.py --podcast stablecoin --slug test` creates `episode_config.json` with private feed settings
- [ ] `setup_episode.py --slug test` (no --podcast) creates config with public defaults
- [ ] `notebooklm_prompt.py` generates different opening/closing for public vs private episodes
- [ ] `podcast-episode-planner` produces content_plan.md without sponsor break for private feed
- [ ] `podcast-metadata-writer` uses correct domain/URLs based on feed config
- [ ] Existing episodes without `episode_config.json` continue to work with public defaults
- [ ] All hardcoded brand references in agents/skills replaced with config-aware instructions

## Team Orchestration

### Team Members

- **Builder (config-system)**
  - Name: config-builder
  - Role: Add fields to Podcast model, create episode_config.py utility, update setup_episode.py
  - Agent Type: builder
  - Resume: true

- **Builder (tool-updates)**
  - Name: tool-updater
  - Role: Update notebooklm_prompt.py, generate_landing_page.py, generate_companion_resources.py
  - Agent Type: builder
  - Resume: true

- **Builder (prompt-updates)**
  - Name: prompt-updater
  - Role: Update agent prompts and skill files to be config-aware
  - Agent Type: builder
  - Resume: true

- **Validator (config-system)**
  - Name: config-validator
  - Role: Verify config loading, defaults, and setup_episode.py integration
  - Agent Type: validator
  - Resume: true

- **Validator (end-to-end)**
  - Name: e2e-validator
  - Role: Verify full workflow produces different outputs for public vs private
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create PodcastConfig model and episode config utility
- **Task ID**: build-config
- **Depends On**: none
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `apps/podcast/models/podcast_config.py` with `PodcastConfig` OneToOne model
- Register in `apps/podcast/models/__init__.py`
- Create migration (migration creation only — Tom runs it)
- Add `PodcastConfig` as inline on `PodcastAdmin` in `apps/podcast/admin.py`
- Create `apps/podcast/tools/episode_config.py` with `load_config(episode_dir)` function that reads `episode_config.json`, falls back to DB lookup via `podcast.config`, then to public defaults
- Update `apps/podcast/tools/setup_episode.py` to accept `--podcast` parameter, query Podcast model + config, and write `episode_config.json` snapshot

### 2. Update Python tools for config awareness
- **Task ID**: build-tools
- **Depends On**: build-config
- **Assigned To**: tool-updater
- **Agent Type**: builder
- **Parallel**: false
- Update `notebooklm_prompt.py` to read episode config for brand elements
- Update `generate_landing_page.py` to use config domain/URLs
- Update `generate_companion_resources.py` to respect access gating flag
- Remove `apps/podcast/tools/update_feed.py` (obsolete — replaced by Django dynamic feeds)

### 3. Update agent prompts and skills
- **Task ID**: build-prompts
- **Depends On**: build-config
- **Assigned To**: prompt-updater
- **Agent Type**: builder
- **Parallel**: true (parallel with build-tools)
- Update `.claude/agents/podcast-episode-planner.md` to instruct reading episode config
- Update `.claude/agents/podcast-metadata-writer.md` to use config for URLs/CTAs
- Update `.claude/skills/podcast-episode-planner/SKILL.md` brand elements section
- Update `.claude/skills/new-podcast-episode.md` setup examples to include `--podcast`, remove all `update_feed.py` references from Phase 11
- Update `docs/templates/podcast/content_plan-enhanced.md` to include feed-conditional sections
- Update `docs/templates/podcast/metadata-enhanced.md` to include feed-conditional URLs

### 4. Validate config system
- **Task ID**: validate-config
- **Depends On**: build-config
- **Assigned To**: config-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `setup_episode.py --podcast stablecoin --slug test` and verify episode_config.json
- Run `setup_episode.py --slug test` and verify public defaults
- Verify `load_config()` returns correct values for both cases
- Verify backward compatibility with directories that lack episode_config.json

### 5. Validate end-to-end
- **Task ID**: validate-e2e
- **Depends On**: build-tools, build-prompts
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `notebooklm_prompt.py` output differs between public and private episodes
- Verify content_plan-enhanced template has conditional sections
- Verify metadata-enhanced template has conditional URLs
- Verify no remaining hardcoded `research.bwforce.ai` in agent/skill files (except as defaults)
- Run all success criteria checks

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-config, validate-e2e
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings uv run python -c "from apps.podcast.tools.episode_config import load_config; print(load_config('/tmp/test'))"` - verify default config loads
- `uv run python apps/podcast/tools/setup_episode.py --podcast stablecoin --slug test --title Test && cat apps/podcast/pending-episodes/stablecoin/test/episode_config.json` - verify config snapshot written
- `DJANGO_SETTINGS_MODULE=settings uv run python -c "from apps.podcast.models import Podcast; p = Podcast.objects.get(slug='stablecoin'); print(p.config.depth_level, p.config.sponsor_break)"` - verify PodcastConfig accessible via OneToOne
- `grep -r 'research.bwforce.ai' .claude/agents/podcast-*.md .claude/skills/podcast-episode-planner/` - verify no remaining hardcoded public URLs in agent prompts (should only appear as defaults)

---

## Open Questions

None — plan is ready for implementation.
