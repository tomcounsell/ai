---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/51
---

# Persona Story Framing Device for Podcast Episodes

## Problem

Podcast episodes currently open with a generic two-host format — informative but lacking emotional stakes. There is no character the listener can identify with, no concrete scenario to ground the abstract research, and no closing callback that reinforces personal relevance.

**Current behavior:**
The episode planner (`plan_episode.py`) generates a hook, three-section arc, and closing callback, but these are topic-oriented ("this research says X") rather than person-oriented ("Kevin needs X for his meeting Tuesday"). The `episodeFocus` prompt tells NotebookLM to "hook with a specific stat/story" but gives no persona to anchor it.

**Desired outcome:**
Each episode is made *for* a real person — typically a paying user. Their name and situation are introduced at the opening, referenced throughout, and called back at the closing. This serves three purposes:

1. **Narrative** — grounds abstract research in a real person's concrete situation
2. **Value delivery** — the paying user receives something made specifically for them
3. **Distribution** — people share content they're personally featured in

Example opening: *"This episode is for Kevin. Kevin is preparing for a big meeting next week on [topic]. So we're going to prepare him with the understanding and action steps to make him the most prepared person in the room."*

Example closing: *"Kevin is now ready for that meeting — and so are you. If you know someone who could use this, share this episode with them."*

## Appetite

**Size:** Small

**Team:** Solo dev. Additions to existing Pydantic models, prompt updates, and episodeFocus template changes. No new services or infrastructure.

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Solution

### Design Decision: Real Personas (User-Provided), AI-Generated Fallback

The persona is primarily a **real person** — typically the paying user who requested or inspired the episode. Their name and situation are provided at episode creation (Phase 1) and flow through the entire pipeline.

- **Primary path:** Real person provided during episode setup. Name used by default; pseudonym available as opt-out if they prefer privacy.
- **Fallback path:** When no real person is associated, the AI planner generates a persona from the research (who would benefit most?).

This makes the persona a product feature, not just a narrative device. Being named in a podcast episode makes paying users feel part of the production and drives organic sharing.

### Overview

The persona is captured at episode setup, enriched by the AI planner into a full framing device, and woven into the NotebookLM audio generation.

```
Episode setup (real person) → plan_episode (Opus) enriches → EpisodePlan.persona
       OR                                                          ↓
AI-generated fallback                                    content_plan artifact (JSON)
                                                                   ↓
                                                          Uploaded to NotebookLM
                                                                   ↓
                                                          episodeFocus prompt references it
```

### File Changes

#### 1. `apps/podcast/services/plan_episode.py` — Add Persona model + integrate into EpisodePlan

Add a new Pydantic model:

```python
class ListenerPersona(BaseModel):
    name: str               # Real name (or pseudonym if opted out)
    situation: str           # e.g. "preparing for a board presentation on AI strategy next Tuesday"
    why_this_episode: str    # e.g. "needs to understand the latest research on LLM deployment costs"
    what_prepared_looks_like: str  # e.g. "can confidently answer tough questions about ROI and risk"
    opening_line: str        # Pre-written line for hosts to use
    closing_callback: str    # Pre-written closing callback
```

Add `persona: ListenerPersona` field to `EpisodePlan`.

When a real person is provided via Episode fields, the AI planner uses their name and situation as input and crafts the opening/closing lines around it. When no person is provided, the planner generates a persona from the research.

#### 2. `apps/podcast/services/prompts/plan_episode.md` — Instruct agent to craft persona framing

Add a new section to the system prompt (between existing items 6 and 7):

```markdown
7. LISTENER PERSONA: Craft the persona framing for this episode.
   - If a real person's name and situation are provided, use them as the foundation
   - If no person is provided, infer who would benefit most from this research and create a persona
   - Describe a concrete, time-bound situation (not generic "wants to learn about X")
   - Explain why THIS episode matters for THEIR situation
   - Define what "prepared" looks like for them after listening
   - Write the opening line hosts should use to introduce the persona by name
   - Write the closing callback that ties back to the persona
   - The persona should feel real and specific — this is someone the episode is genuinely made for
```

Renumber existing item 7 (NOTEBOOKLM GUIDANCE) to 8.

Also add to the "Key principles" section:
```markdown
- Opening persona introduction should come after the hook, before the structure preview
- Closing persona callback should come before the CTA
```

#### 3. `apps/podcast/tools/notebooklm_api.py` — Update `generate_episode_focus()` to include persona instructions

Update the `episodeFocus` prompt template. Add persona section after the brand elements:

```python
LISTENER PERSONA - IMPORTANT:
- Find the "persona" section in content_plan.md
- After the opening hook, introduce the persona using the provided opening line
- This creates emotional stakes: the episode is FOR someone specific
- Throughout the episode, occasionally reference how this applies to the persona's situation
- In the closing, use the persona callback line before the general CTA
- The persona makes abstract research feel personal and actionable
```

Update the EPISODE ARC section to reference persona placement:

```python
EPISODE ARC:
- Opening (3-5 min): Hook with specific stat/story, INTRODUCE PERSONA, define the problem, preview structure
- Middle (20-30 min): Build from foundation to evidence to application with clear mode-switching
- Closing (3-5 min): Synthesize key takeaways, PERSONA CALLBACK, callback to opening hook, call-to-action
```

#### 4. `apps/podcast/services/synthesis.py` — Store persona in artifact metadata

In `plan_episode_content()`, add persona fields to the artifact metadata dict:

```python
"metadata": {
    # ... existing fields ...
    "persona_name": result.persona.name,
    "persona_situation": result.persona.situation,
},
```

This makes persona data queryable without parsing the full JSON content.

#### 5. `apps/podcast/services/prompts/plan_episode.md` — Update NotebookLM Guidance section

Add to the NotebookLM Guidance description:

```markdown
8. NOTEBOOKLM GUIDANCE: Specific instructions for NotebookLM including key terms to define, studies to emphasize, stories to feature, counterpoint execution, AND persona introduction/callback placement.
```

### Additional: Episode Model — Persona Input Fields

Add two optional fields to the `Episode` model to capture the real person:

```python
persona_name = models.CharField(max_length=100, blank=True, default="")
persona_context = models.TextField(blank=True, default="")  # their situation / why this episode
```

These are set during episode creation when a real person is associated. The `setup_episode` service passes them into the p1-brief artifact metadata. When blank, the AI planner falls back to generating a persona from research.

#### 0. Episode Intake (Django Admin) — Add persona fields to the creation flow

The Episode admin (`apps/podcast/admin.py`) is the current intake form. Add `persona_name` and `persona_context` to the admin fieldsets so they're visible when creating a new episode:

- Add a "Persona" fieldset to `EpisodeAdmin` with both fields
- Place it after the core fields (title, slug, description) and before status/publishing fields
- Both fields are optional — blank means AI-generated fallback

The `setup_episode` service (`apps/podcast/services/setup.py`) already creates the p1-brief artifact from `episode.description`. Update it to also store persona data in the artifact metadata:

```python
EpisodeArtifact.objects.update_or_create(
    episode=episode,
    title="p1-brief",
    defaults={
        "content": episode.description,
        "description": "Initial episode brief derived from the episode description.",
        "workflow_context": "Setup",
        "metadata": {
            "persona_name": episode.persona_name,
            "persona_context": episode.persona_context,
        },
    },
)
```

This ensures persona data flows from intake through to the planning step without requiring additional service changes — `plan_episode` already reads the p1-brief artifact.

### What Does NOT Change

- **PodcastConfig model** — No persona config at podcast level. Each episode gets a unique persona.
- **`services/audio.py`** — No changes. It already uploads `content_plan.md` to NotebookLM and passes the `episodeFocus` prompt. Both of those will contain persona data after the upstream changes.
- **Workflow steps** — No new steps. Persona enrichment happens inside the existing Episode Planning step.

## Rabbit Holes

- **Persona library/templates**: Not needed. Real people are the primary source; the AI fallback varies naturally.
- **Persona images/avatars**: Out of scope. Audio-only podcast — no visual persona representation needed.
- **Persona in companion resources**: Could reference the persona in companion materials (checklist, summary), but that's a separate concern for the companion generation service.
- **User accounts / linking to auth**: The persona fields are simple text, not a FK to a user model. Keep it lightweight.

## No-Gos

- No new workflow steps or phases
- No persona configuration at the podcast/series level
- No complex consent management system — pseudonym opt-out is handled by simply putting a different name in the field

## Acceptance Criteria

1. `Episode` model has `persona_name` and `persona_context` fields (optional, text)
2. `ListenerPersona` Pydantic model exists with name, situation, why_this_episode, what_prepared_looks_like, opening_line, closing_callback fields
3. `EpisodePlan` includes a `persona` field of type `ListenerPersona`
4. When `persona_name` / `persona_context` are set on Episode, the AI planner uses them as input
5. When persona fields are blank, the AI planner generates a persona from research
6. `plan_episode.md` system prompt instructs the agent to craft persona framing (real or generated)
7. `generate_episode_focus()` includes persona instructions telling NotebookLM hosts when and how to use the persona
8. `content_plan` artifact metadata includes `persona_name` and `persona_situation`
9. Existing tests continue to pass
10. Pre-commit hooks pass (black, ruff, flake8)
