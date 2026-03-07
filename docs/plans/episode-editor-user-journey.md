# Episode Editor: Full User Journey

## Overview

Maps the complete user journey from "I have an idea for an episode" to "I can see it on Spotify." Identifies what exists today, what's missing, and where user decisions happen.

---

## The Journey

### 1. IDEA → Navigate to Podcast

**User has:** An idea for a new episode on an existing podcast.

**Current flow:**
- `/podcast/` → Podcast list → Click podcast → Podcast detail page
- Or direct URL: `/podcast/{slug}/`

**What they see:** Podcast detail page with cover art, description, published episodes, and (if owner/staff) a **"+ New Episode"** button in the header.

**Status: ✅ Exists**

---

### 2. CREATE DRAFT → Click "New Episode"

**Action:** Click "+ New Episode" button (staff-only, POST form).

**What happens:**
- Creates bare `Episode(title="Untitled Episode", slug=<uuid>, status="draft")`
- Redirects to `/podcast/{slug}/{episode_slug}/edit/1/` (workflow step 1)

**What's missing:**
- ❌ **No episode creation form.** The user can't set a title, description, or topic before the episode is created. They get "Untitled Episode" and land on the workflow page.
- ❌ **Owner access.** Currently staff-only (`is_staff`). Podcast owners who aren't staff can't create episodes.

**Decision point → needs UI:**
> Before the pipeline starts, the user should be able to provide:
> - **Title** (or working title)
> - **Description / topic idea** (this becomes the `p1-brief` seed — the research prompt)
> - **Tags** (optional, for categorization)

---

### 3. EDIT EPISODE DETAILS → Workflow Step 1 (Setup)

**Current flow:** Lands on workflow page at step 1.

**What they see:**
- Left sidebar with 12 phases (all gray/pending)
- Phase 1: "Setup" with sub-steps checklist
- "Start Pipeline" green button

**What's missing:**
- ❌ **No editable fields on the workflow page.** There's no way to edit the episode title, description, or tags from the workflow UI. The user has to go to Django admin.
- ❌ **No episode brief/prompt editor.** The `p1-brief` artifact is generated from `Episode.description`, but there's no UI to write or edit the description before starting the pipeline.

**This is the critical gap.** The workflow page is a *monitoring dashboard*, not an *editor*. Before the user clicks "Start Pipeline," they need a place to:
1. Set the episode title
2. Write the episode description/topic (which seeds all the research)
3. Optionally configure depth, tone, or research focus

---

### 4. START PIPELINE → Automated Research Phases (2-6)

**Action:** Click "Start Pipeline."

**What happens (automated):**
1. `produce_episode` task enqueued
2. `setup_episode()` creates `p1-brief` artifact from `Episode.description`
3. Perplexity research → Question discovery → Targeted research (parallel) → Cross-validation → Master briefing

**What the user sees:**
- Workflow sidebar updates: phases go green as they complete, amber while running
- Sub-step checklists show progress
- Each phase has a progress bar

**Decision points where system pauses:**
- 🔵 **Quality Gate: Wave 1** (after Phase 6, Master Briefing) — Workflow pauses at gate. User reviews the briefing quality (200+ word minimum). User clicks "Resume Pipeline" to continue.

**What's missing:**
- ❌ **No artifact viewer.** The workflow page shows checklist status but doesn't display the actual artifact content. User can't read the research, briefing, or cross-validation results from the workflow UI — they'd have to check Django admin or the database.
- ❌ **No edit capability at quality gates.** When paused at Wave 1, the user can only resume or... nothing. They can't edit the briefing or add manual research to improve it.

---

### 5. SYNTHESIS & PLANNING → Phases 7-8

**Automated:**
- Phase 7: `synthesize_report()` — Generates 5,000-8,000 word narrative report (Opus)
- Phase 8: `plan_episode_content()` — Creates content plan for NotebookLM

**Decision point:**
- 🔵 **Quality Gate: Wave 2** (after Phase 8, Episode Planning) — Workflow pauses. User reviews the content plan. Clicks "Resume Pipeline."

**What's missing:**
- ❌ Same as above — no way to view or edit the report/plan from the workflow UI.

---

### 6. AUDIO GENERATION → Phase 9 (Human-in-the-loop)

**What happens:**
- Workflow status → `paused_for_human`
- `local_audio_worker` (running on local machine) picks up the episode
- Generates audio via NotebookLM using the content plan
- Uploads audio to Supabase
- Resumes workflow

**What the user sees:**
- Workflow shows "Paused — Waiting for audio generation"
- "Resume Pipeline" button appears once audio is uploaded

**What's missing:**
- ❌ **No audio upload UI.** If the automated worker isn't running, there's no way for the user to manually upload an audio file through the web UI.
- ❌ **No audio preview.** Can't listen to the generated audio from the workflow page before proceeding.

---

### 7. POST-PRODUCTION → Phases 10-11

**Automated:**
- Phase 10: Whisper transcription + chapter generation
- Phase 11 (parallel): Cover art generation + metadata + companion resources

**What the user sees:**
- Phases going green in sidebar

**What's missing:**
- ❌ **No metadata editor.** Episode description, show notes, title — all generated by AI. No UI to review/edit before publishing.
- ❌ **No cover art preview/selection.** Cover art is generated automatically. No way to see it, regenerate, or upload a custom one from the workflow UI.

---

### 8. PUBLISH → Phase 12

**What happens:**
- `publish_episode()` sets `Episode.status = complete`, `Episode.published_at = now()`
- Episode immediately appears in RSS feed

**What the user sees (currently):**
- Phase 12 goes green in sidebar. That's it.

**What's missing:**
- ❌ **No publish confirmation.** No "Are you sure?" before publishing. No preview of what will appear in the feed.
- ❌ **No post-publish success page.** After publishing, the user stays on the workflow page. No celebration, no links to where the episode is live.

---

### 9. VIEW PUBLISHED EPISODE → Episode Detail Page

**Current flow:** Navigate to `/podcast/{slug}/{episode_slug}/`

**What they see:**
- Episode title, metadata, tags
- Audio player with download button
- Platform links (Spotify, Apple Podcasts, RSS)
- Resources (View Report, View Sources)

**Status: ✅ Exists and works well**

**What's missing:**
- ❌ **No direct link from workflow completion to episode page.** User has to navigate back manually.
- ❌ **Spotify link is podcast-level, not episode-level.** The `spotify_url` is on the Podcast model. There's no per-episode Spotify deep link. (This is a Spotify limitation — episodes appear in the feed automatically, but getting a direct episode URL requires the Spotify API or manual copy.)

---

## Gap Summary

### Critical Path Gaps (Must Have)

| Gap | Where | Impact |
|-----|-------|--------|
| **Episode creation form** | Step 2 | User can't set title/topic before pipeline starts |
| **Episode brief editor** | Step 3 | Description seeds all research — must be editable |
| **Artifact viewer** | Steps 4-8 | User can't see research/reports from workflow UI |
| **Metadata review/edit** | Step 7 | Can't review AI-generated content before publish |
| **Post-publish navigation** | Step 8 | Dead end after publishing |

### Important Gaps (Should Have)

| Gap | Where | Impact |
|-----|-------|--------|
| **Owner access** (not just staff) | Step 2 | Podcast owners locked out of creating episodes |
| **Audio upload UI** | Step 6 | No fallback when automated worker isn't running |
| **Cover art preview** | Step 7 | Can't see/change cover before publishing |
| **Publish confirmation** | Step 8 | Risk of accidental publish |

### Nice to Have

| Gap | Where | Impact |
|-----|-------|--------|
| Quality gate editing | Steps 4-5 | Can't improve content at review points |
| Audio preview in workflow | Step 6 | Can't listen before proceeding |
| Episode-level Spotify link | Step 9 | Podcast-level link only |
| Real-time progress (WebSocket) | Steps 4-8 | Currently requires page refresh to see updates |

---

## Proposed Flow (What It Should Look Like)

```
Podcast Detail Page
  └─ Click "+ New Episode"
       └─ Episode Editor (new page, not workflow)
            ├── Title field
            ├── Description / topic textarea (→ becomes research prompt)
            ├── Tags
            └── "Start Research" button
                  └─ Workflow Dashboard (existing, enhanced)
                       ├── Artifact viewer panel (read-only, collapsible)
                       ├── Quality gate review with content display
                       ├── Audio section with player + manual upload
                       ├── Metadata review/edit before publish
                       └── "Publish" with confirmation
                             └─ Success page
                                  ├── "View Episode" link
                                  ├── RSS feed link
                                  ├── Spotify/Apple links
                                  └── "Create Another Episode" link
```

---

## Screens Inventory

| Screen | Exists? | URL Pattern |
|--------|---------|-------------|
| Podcast list | ✅ | `/podcast/` |
| Podcast detail | ✅ | `/podcast/{slug}/` |
| Podcast edit | ✅ | `/podcast/{slug}/edit/` |
| **Episode create/edit form** | ❌ | `/podcast/{slug}/new/` (needs redesign) |
| Workflow dashboard | ✅ (monitoring only) | `/podcast/{slug}/{ep}/edit/{step}/` |
| **Artifact viewer** | ❌ | (inline on workflow page) |
| **Publish confirmation** | ❌ | (modal or step 12 content) |
| **Post-publish success** | ❌ | (redirect or step 12 content) |
| Episode detail (public) | ✅ | `/podcast/{slug}/{ep}/` |
| Episode report | ✅ | `/podcast/{slug}/{ep}/report/` |
| Episode sources | ✅ | `/podcast/{slug}/{ep}/sources/` |
| RSS feed | ✅ | `/podcast/{slug}/feed.xml` |
