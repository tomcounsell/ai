# Artifact Viewer in Workflow UI

**Issue:** #130
**Status:** Planning
**Created:** 2026-03-07

## Problem

During the research pipeline (phases 2-8), the workflow page shows checklist status but never displays the actual content of artifacts. Users can't read the research, briefing, cross-validation results, or narrative report from the workflow UI — they'd have to check Django admin or the database directly.

From `docs/plans/episode-editor-user-journey.md`:

> ❌ **No artifact viewer.** The workflow page shows checklist status but doesn't display the actual artifact content. User can't read the research, briefing, or cross-validation results from the workflow UI — they'd have to check Django admin or the database.

This is a critical gap in the user journey. At quality gates (phases 6 and 8), users are asked to review content before resuming the pipeline, but they have no way to actually see what was produced.

## Solution

Add a collapsible artifact content panel to the workflow step content template that displays the artifact body when expanded. The artifact viewer should:

1. **Display relevant artifacts** for each workflow phase
2. **Auto-expand at quality gates** (phases 6 and 8) so users can review before resuming
3. **Lazy-load content** via HTMX to avoid loading large artifacts on initial page load
4. **Render markdown to HTML** for formatted display
5. **Be collapsible** to keep the UI clean when not needed

## Scope

### In Scope

- Read-only artifact viewer for workflow phases 2-8
- Collapsible/expandable UI component
- Auto-expand behavior at quality gate phases (6, 8)
- Markdown to HTML rendering
- HTMX lazy-loading for artifact content
- Integration with existing `_workflow_step_content.html` template

### Out of Scope

- Editing artifact content (separate feature)
- Artifact download functionality
- Artifact versioning/history
- Side-by-side artifact comparison
- Real-time collaborative editing

## Design

### Data Model

No changes needed. The `EpisodeArtifact` model already has everything required:

```python
class EpisodeArtifact(Timestampable):
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, related_name="artifacts")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    workflow_context = models.CharField(max_length=200, blank=True)
    content = models.TextField(blank=True)  # ← The artifact body
    url = models.URLField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
```

### Phase → Artifact Mapping

| Phase | Number | Artifact Title | Auto-expand? |
|-------|--------|---------------|--------------|
| Setup | 1 | p1-brief | No |
| Perplexity Research | 2 | p2-research | No |
| Question Discovery | 3 | p3-questions | No |
| Targeted Research | 4 | p4-digest | No |
| Cross-Validation | 5 | p5-validation | No |
| Master Briefing | 6 | p6-briefing | **Yes** (quality gate) |
| Synthesis | 7 | p7-report | No |
| Episode Planning | 8 | p8-plan | **Yes** (quality gate) |
| Audio Generation | 9 | - | No |
| Audio Processing | 10 | - | No |
| Publishing Assets | 11 | - | No |
| Publish | 12 | - | No |

### UI Flow

#### Collapsed State (Default)

```
┌─────────────────────────────────────────┐
│ Phase 6: Master Briefing                │
│ ━━━━━━━━━━━━━━━━ 100%                   │
├─────────────────────────────────────────┤
│ ▶ View Artifact: Master Briefing        │  ← Expandable header
├─────────────────────────────────────────┤
│ ✓ Generate comprehensive briefing       │
│ ✓ Validate minimum word count (200+)    │
└─────────────────────────────────────────┘
```

#### Expanded State (Auto-expanded at quality gates)

```
┌─────────────────────────────────────────┐
│ Phase 6: Master Briefing                │
│ ━━━━━━━━━━━━━━━━ 100%                   │
├─────────────────────────────────────────┤
│ ▼ Master Briefing                        │  ← Collapsible header
│ ┌───────────────────────────────────┐   │
│ │ # Executive Summary               │   │
│ │                                   │   │
│ │ This briefing synthesizes the...  │   │  ← Rendered markdown
│ │                                   │   │
│ │ ## Key Findings                   │   │
│ │ - Finding 1                       │   │
│ │ - Finding 2                       │   │
│ └───────────────────────────────────┘   │
├─────────────────────────────────────────┤
│ ✓ Generate comprehensive briefing       │
│ ✓ Validate minimum word count (200+)    │
└─────────────────────────────────────────┘
```

### Technical Implementation

#### Template Changes

Add artifact viewer section to `_workflow_step_content.html` after the progress bar but before the pipeline action button:

```django
<!-- Artifact viewer (if artifact exists for this phase) -->
{% if phase_artifact %}
<div class="mb-6">
  <details {% if auto_expand_artifact %}open{% endif %} class="border border-gray-200">
    <summary class="px-4 py-3 bg-gray-50 hover:bg-gray-100 cursor-pointer font-mono text-sm">
      <span class="mr-2">{{ phase_artifact.title }}</span>
      <span class="text-xs text-gray-500">({{ phase_artifact.word_count }} words)</span>
    </summary>
    <div class="px-4 py-4 bg-white"
         hx-get="{% url 'podcast:artifact_content' slug=podcast.slug episode_slug=episode.slug artifact_id=phase_artifact.id %}"
         hx-trigger="load"
         hx-swap="innerHTML">
      <div class="text-sm text-gray-400">Loading...</div>
    </div>
  </details>
</div>
{% endif %}
```

#### View Changes

**New URL endpoint** for lazy-loading artifact content:

```python
# apps/podcast/urls.py
path(
    "<slug:slug>/<slug:episode_slug>/artifacts/<int:artifact_id>/",
    ArtifactContentView.as_view(),
    name="artifact_content",
),
```

**New view** to return rendered artifact content:

```python
# apps/podcast/views/podcast_views.py
class ArtifactContentView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Return rendered artifact content as HTML fragment for HTMX."""

    def test_func(self) -> bool:
        return self.request.user.is_staff

    def get(self, request, slug: str, episode_slug: str, artifact_id: int):
        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)
        artifact = get_object_or_404(
            EpisodeArtifact,
            id=artifact_id,
            episode=episode
        )

        # Convert markdown to HTML
        import markdown
        html_content = markdown.markdown(
            artifact.content,
            extensions=['fenced_code', 'tables', 'nl2br']
        )

        # Return as HTML fragment
        return HttpResponse(
            f'<div class="prose prose-sm max-w-none">{html_content}</div>'
        )
```

**Update EpisodeWorkflowView** to pass artifact data to template:

```python
# In _load_context method, add:
phase_artifact = self._get_phase_artifact(episode, step)
auto_expand = step in [6, 8]  # Quality gates

self.context["phase_artifact"] = phase_artifact
self.context["auto_expand_artifact"] = auto_expand

def _get_phase_artifact(self, episode: Episode, step: int) -> EpisodeArtifact | None:
    """Get the artifact for the given workflow phase, if it exists."""
    artifact_map = {
        1: "p1-brief",
        2: "p2-research",
        3: "p3-questions",
        4: "p4-digest",
        5: "p5-validation",
        6: "p6-briefing",
        7: "p7-report",
        8: "p8-plan",
    }
    title = artifact_map.get(step)
    if not title:
        return None
    return episode.artifacts.filter(title=title).first()
```

### Word Count Display

Add a helper method to `EpisodeArtifact` model:

```python
@property
def word_count(self) -> int:
    """Count words in artifact content."""
    if not self.content:
        return 0
    return len(self.content.split())
```

## Testing Strategy

### Unit Tests

1. **Test artifact retrieval** — Verify `_get_phase_artifact` returns correct artifact for each phase
2. **Test word count** — Verify `word_count` property calculates correctly
3. **Test markdown rendering** — Verify markdown converts to HTML correctly

### Integration Tests

1. **Test artifact content view** — Staff can load artifact content via HTMX endpoint
2. **Test auto-expand logic** — Quality gate phases (6, 8) have `auto_expand_artifact=True`
3. **Test permission checks** — Non-staff users cannot access artifact content
4. **Test missing artifacts** — Phases without artifacts don't show the viewer section

### Browser Tests

1. **Test expand/collapse** — Click summary to expand/collapse artifact viewer
2. **Test HTMX loading** — Artifact content loads when expanded
3. **Test quality gate auto-expand** — Phase 6 and 8 artifacts are pre-expanded
4. **Test markdown rendering** — Headings, lists, code blocks render correctly

## Implementation Plan

### Phase 1: Backend (30 min)

- [ ] Add `word_count` property to `EpisodeArtifact` model
- [ ] Create `ArtifactContentView` for HTMX endpoint
- [ ] Update `EpisodeWorkflowView._load_context` to fetch phase artifact
- [ ] Add URL route for artifact content endpoint

### Phase 2: Frontend (30 min)

- [ ] Update `_workflow_step_content.html` with artifact viewer section
- [ ] Add `<details>` component with HTMX lazy-loading
- [ ] Add prose styling for rendered markdown (Tailwind Typography)
- [ ] Implement auto-expand logic for quality gate phases

### Phase 3: Testing (30 min)

- [ ] Write unit tests for artifact retrieval and word count
- [ ] Write integration tests for artifact content view
- [ ] Write browser test for expand/collapse interaction
- [ ] Verify all quality gates auto-expand artifacts

## Dependencies

- `markdown` Python package (for markdown → HTML conversion)
- Tailwind Typography plugin (for prose styling) — **already installed**
- HTMX (for lazy-loading) — **already in use**

## No-Gos

- **No editing capability** — This is a viewer only. Editing is a separate feature.
- **No download button** — Users can copy/paste if needed. Download is a future enhancement.
- **No artifact comparison** — One artifact per phase. Multi-artifact comparison is out of scope.
- **No real-time updates** — Artifacts are static once created. No WebSocket polling.

## Update System

No update system changes required — this is a purely UI/frontend enhancement. The feature:
- Uses existing database schema (no migrations)
- Adds a new view endpoint (code-only change)
- Updates existing templates (no config propagation needed)

The bridge doesn't interact with this feature (it's web UI only).

## Agent Integration

No agent integration required — this feature is entirely within the Django web UI. The agent:
- Doesn't create or modify artifacts directly (that happens via Django ORM in tasks)
- Doesn't need to expose this functionality via MCP
- Doesn't need to know about the artifact viewer UI

This is a human-facing UI feature with no programmatic access requirements.

## Documentation

- [ ] Create `docs/features/artifact-viewer.md` with screenshots and usage guide
- [ ] Update `docs/features/README.md` index with artifact viewer entry
- [ ] Add inline code comments for artifact retrieval logic
- [ ] Document the phase → artifact title mapping

## Success Criteria

1. ✅ Artifacts are visible on workflow page for phases 2-8
2. ✅ Quality gate phases (6, 8) auto-expand artifacts for review
3. ✅ Markdown content renders as formatted HTML
4. ✅ HTMX lazy-loading prevents blocking page load
5. ✅ Collapsible UI keeps workflow page clean
6. ✅ All tests pass (unit, integration, browser)
7. ✅ No performance regression on workflow page load

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Large artifacts (10k+ words) slow down rendering | Medium | HTMX lazy-loading + collapse by default |
| Markdown rendering fails on malformed content | Low | Use safe markdown parser with error handling |
| Auto-expand on quality gates is confusing | Low | Clear visual indicator (open arrow vs closed) |
| HTMX request fails / times out | Low | Show error message in content area |

## Open Questions

None. The design is straightforward and uses existing patterns from the codebase.
