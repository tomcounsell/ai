# WebMCP AI Agent Integration

WebMCP exposes structured data and guided instructions on every podcast page so AI agents can browse the site and manage the entire podcast production system via the MCP protocol.

## How It Works

The [WebMCP polyfill](https://www.npmjs.com/package/@mcp-b/webmcp-polyfill) script is loaded in `apps/public/templates/base.html`. It provides `navigator.modelContext` with `registerResource()` and `registerTool()` methods. Each podcast template overrides `{% block webmcp %}` to register page-specific resources and tools.

**Progressive enhancement**: If the CDN is unavailable, pages render normally. All WebMCP JavaScript is guarded with `if (!navigator.modelContext) return`.

**Auth model**: Session-based. Agents log in via `/admin/login/`, then all pages (including auth-gated workflow pages) expose MCP data.

## Pages and MCP Primitives

### Public Pages

| Page | Template | Resources | Tools |
|------|----------|-----------|-------|
| Podcast list | `podcast_list.html` | `site://podcasts` (all podcasts) | `search_podcasts(query)` |
| Podcast detail | `podcast_detail.html` | `site://podcast/{slug}`, `site://podcast/{slug}/episodes` | `search_episodes(query)`, `how_to_create_episode` (owner-only) |
| Episode detail | `episode_detail.html` | Metadata, show notes, report link | -- |
| Episode report | `episode_report.html` | Full report text | -- |

### Auth-Gated Pages

| Page | Template | Resources | Tools |
|------|----------|-----------|-------|
| Episode create | `episode_create.html` | -- | `create_episode_guide` |
| Episode workflow | `episode_workflow.html` | Workflow status (phases, current step) | `how_to_navigate`, `how_to_manage_research`, `how_to_manage_audio`, `how_to_edit_metadata`, `how_to_manage_cover_art`, `how_to_publish`, `how_to_resume` |
| Podcast edit | `podcast_edit.html` | -- | `how_to_edit_podcast` |

## Resource Data Format

Resources return JSON via `contents[].text` with `mimeType: application/json`. Data is serialized from Django template context using `json_script` filter or inline template JSON.

Example resource response:
```json
{
  "slug": "yudame-research",
  "title": "Yudame Research",
  "description": "...",
  "episode_count": 12,
  "latest_episode_at": "2026-03-15T10:00:00+00:00"
}
```

## Mutation Tool Format

Mutation tools (all `how_to_*` tools) return text instructions guiding agents to use the existing web UI. No API endpoints are needed -- agents follow the instructions to fill forms and click buttons.

```
ACTION: Create a new episode
URL: /podcast/yudame-research/new/
METHOD: Fill the form and submit

FIELDS:
- title (required): Episode title, max 200 characters
- description (required): Research prompt for the AI pipeline
- tags (optional): Comma-separated tags

SUBMIT: Click the submit button
AFTER: Redirects to the workflow page where production begins
```

## Technical Details

- **CDN**: `@mcp-b/webmcp-polyfill@2.2.0` via jsDelivr with SRI hash
- **Template block**: `{% block webmcp %}` in `base.html`, overridden per page
- **Data bridge**: Django's `json_script` filter for safe JSON serialization
- **Show notes**: Uses `data-webmcp="show-notes"` attribute for stable DOM selection
- **Book site**: `book/base.html` does NOT include WebMCP (separate template inheritance)

## Agent Workflow Example

1. Agent connects to `/podcast/` via MCP client
2. Reads `site://podcasts` resource to discover available podcasts
3. Navigates to `/podcast/yudame-research/`
4. Reads `site://podcast/yudame-research/episodes` for episode list
5. Invokes `how_to_create_episode` tool for creation instructions
6. Follows instructions: navigates to `/podcast/yudame-research/new/`, fills form
7. On the workflow page, reads status resource and uses management tools as needed
