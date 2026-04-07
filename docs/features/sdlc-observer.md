# SDLC Observer

Web dashboard for real-time visibility into active development pipelines, stage transitions, and outcomes.

## Overview

The SDLC Observer at `/sdlc/` shows all `AgentSession` instances that have SDLC stage tracking enabled (`stage_states` field set). It provides a visual pipeline view with stage indicators, event timelines, and links to related artifacts.

## Views

### Active Pipelines (`/sdlc/`)
- Cards for each in-progress pipeline with:
  - Display name (slug or truncated message)
  - Status badge
  - Horizontal stage indicator showing progress through ISSUE -> PLAN -> CRITIQUE -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE
  - Links to GitHub issue, PR, and detail view
  - Branch name, start time, running duration
- Auto-refreshes via HTMX polling every 5 seconds
- Recent completions table below active pipelines

### Pipeline Detail (`/sdlc/{job_id}/`)
- Stat boxes: status, current stage, duration, session type
- Full stage progress indicator
- Details table: job ID, session ID, project, branch, slug, timestamps
- Links section: issue URL, plan URL, PR URL
- Event timeline from session history
- Original message text

### Completed Pipelines (`/sdlc/completed/`)
- Table of recently completed/failed pipelines
- Each row shows: name, status, stage indicator, duration, completion time, links

## Data Layer

### Pydantic Models

- **`StageState`**: Typed representation of a single SDLC stage status (pending, in_progress, completed, failed, skipped)
- **`PipelineProgress`**: Complete pipeline view with stages, events, links, and computed properties (duration, is_active, display_name)
- **`PipelineEvent`**: Single event from session history with role and text

### Parsing

The data layer deserializes:
- `AgentSession.stage_states` (JSON string or dict) into `StageState` objects
- `AgentSession.history` (list of `[role] text` strings) into `PipelineEvent` objects

Handles gracefully: None values, empty strings, malformed JSON, nested status dicts.

## HTMX Endpoints

| Endpoint | Trigger | Purpose |
|----------|---------|---------|
| `/_partials/active/` | `every 5s` | Refresh active pipeline cards |
| `/_partials/stage-indicator/{job_id}/` | `every 5s` | Refresh single pipeline stage indicator |

## Stage Indicator

The horizontal stage indicator is a pure HTML/CSS component showing all 8 SDLC stages as labeled dots connected by arrows:

- **Completed**: Green background
- **In progress**: Yellow background with pulse animation
- **Failed**: Red background
- **Pending**: Gray background

## Related

- [Web UI Infrastructure](web-ui.md) - Shared infrastructure
- [Agent Session Model](agent-session-model.md) - The underlying data model
- [Goal Gates](goal-gates.md) - Stage enforcement in the pipeline
