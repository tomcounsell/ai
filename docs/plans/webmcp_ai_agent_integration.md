---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-27
tracking: https://github.com/yudame/cuttlefish/issues/162
last_comment_id:
---

# WebMCP AI Agent Integration

## Problem

AI agents that want to interact with `ai.yuda.me` have no machine-readable interface. They must scrape HTML or manually navigate the UI to discover podcasts, read episode content, or understand what the site offers.

**Current behavior:**
The site is human-only. All content (episode metadata, research reports, show notes, sources) is embedded in HTML templates with no structured data layer for AI consumption.

**Desired outcome:**
Public pages expose MCP tools and resources via WebMCP so that AI agents (e.g., Claude Desktop) can connect to any page and programmatically read episode data, search content, and discover site capabilities through the standard MCP protocol.

## Prior Art

- **PR #8**: Implement CTO Tools Security Review MCP Server -- Established the pattern for MCP servers in `apps/ai/mcp/`. This is backend MCP; the current work is frontend-only via WebMCP JS, so no overlap, but the team has MCP familiarity.
- **Issue #179**: Host blendedworkforce.ai as separate domain -- Relevant because `base.html` vs `book/base.html` means WebMCP must only load on the main site's base template, not the book site.

No prior issues found related to frontend MCP or WebMCP integration.

## Spike Results

### spike-1: WebMCP API and transport mechanism
- **Assumption**: "WebMCP provides a JS library that can be included via script tag and exposes tools/resources to MCP clients"
- **Method**: web-research
- **Finding**: Confirmed. WebMCP provides a `WebMCP` constructor with `registerTool()`, `registerResource()`, and `registerPrompt()` methods. It renders a small colored widget on the page. AI clients connect via Claude Desktop MCP configuration and a token exchange through the widget. The library is available as `@jason.today/webmcp` via npm, or can be loaded via script tag.
- **Confidence**: high
- **Impact on plan**: Confirmed approach -- include via CDN/vendored script, register tools/resources per page using Django template data.

### spike-2: Data availability in templates
- **Assumption**: "Episode data needed for MCP resources is already available in template context"
- **Method**: code-read
- **Finding**: Confirmed. `PodcastListView` provides `podcasts` queryset with episode_count and latest_episode_at annotations. `EpisodeDetailView` provides full `episode` object with title, description, audio_url, report_text, show_notes, published_at, etc. `PodcastDetailView` provides `episodes` queryset. All data needed for MCP resources is already in the view context.
- **Confidence**: high
- **Impact on plan**: No new views or API endpoints needed. Template-rendered JSON is sufficient.

## Data Flow

1. **Entry point**: Page load on public-facing template (podcast list, podcast detail, episode detail)
2. **Django view**: Renders template with podcast/episode data in context (already exists)
3. **Template `{% block webmcp %}`**: Renders a `<script>` block that serializes relevant model data into JavaScript variables via `{{ data|json_script }}`
4. **WebMCP JS**: `new WebMCP()` initializes, page-specific code calls `registerTool()` and `registerResource()` with the serialized data
5. **AI client**: Connects to page via MCP token from widget, invokes tools or reads resources
6. **Output**: Structured JSON responses from tool callbacks using the page's pre-rendered data

## Architectural Impact

- **New dependencies**: WebMCP JS library (CDN or vendored static file). No Python dependencies.
- **Interface changes**: None. Existing views and templates are unchanged. New `{% block webmcp %}` added to `base.html` (empty by default).
- **Coupling**: Low. WebMCP is a purely additive JS layer reading from existing template context. No backend changes.
- **Data ownership**: No change. Data stays in Django models, rendered into templates as before.
- **Reversibility**: Trivial. Remove the script tag and block overrides.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on which tools/resources to expose)
- Review rounds: 1

## Prerequisites

No prerequisites -- WebMCP is a client-side JS library with no API keys or server-side dependencies.

## Solution

### Key Elements

- **WebMCP base integration**: Add WebMCP script to `base.html` (main site only, not book site) with an empty `{% block webmcp %}` for per-page overrides
- **Page-specific MCP registrations**: Each public podcast page overrides `{% block webmcp %}` to register relevant tools and resources using data already in the template context
- **JSON data bridge**: Use Django's `json_script` filter to safely serialize model data into `<script>` tags that WebMCP callbacks can read

### Flow

**AI agent opens page** -> WebMCP widget appears -> Agent connects via MCP token -> Agent discovers registered tools/resources -> Agent invokes tool or reads resource -> WebMCP returns JSON from pre-rendered page data

### Technical Approach

- Include WebMCP via CDN script tag in `base.html` `<head>`, wrapped in `{% block webmcp_script %}` for override flexibility
- Add `{% block webmcp %}` before `</body>` in `base.html` (empty by default, no-op for pages that don't register anything)
- Each podcast template overrides `{% block webmcp %}` to register page-appropriate primitives
- Use `{{ episodes_json|json_script:"episodes-data" }}` pattern to safely embed JSON data
- Tool callbacks read from `JSON.parse(document.getElementById('episodes-data').textContent)` -- no XHR needed

**Pages and their MCP primitives:**

1. **Podcast list** (`podcast_list.html`):
   - Resource `site://podcasts` -- list of all podcasts with title, description, slug, episode count
   - Tool `search_episodes(query)` -- client-side search across podcast titles/descriptions

2. **Podcast detail** (`podcast_detail.html`):
   - Resource `podcast://{slug}/episodes` -- list of published episodes with title, number, date, description
   - Resource `podcast://{slug}/info` -- podcast metadata (title, description, author, links)

3. **Episode detail** (`episode_detail.html`):
   - Resource `episode://{slug}/{episode_slug}/metadata` -- full episode metadata
   - Resource `episode://{slug}/{episode_slug}/show-notes` -- show notes content
   - Resource `episode://{slug}/{episode_slug}/report` -- research report text (if available)

4. **Episode report** (`episode_report.html`):
   - Resource `report://{slug}/{episode_slug}` -- full report text

## Failure Path Test Strategy

### Exception Handling Coverage
- WebMCP initialization is client-side JS; failures are silent (widget doesn't appear). No server-side exception handlers in scope.
- Tool callbacks should wrap logic in try/catch and return error text content on failure.

### Empty/Invalid Input Handling
- [ ] Tool callbacks must handle missing JSON data gracefully (e.g., `json_script` element not found returns empty result, not crash)
- [ ] `search_episodes` with empty query returns all episodes rather than erroring
- [ ] Pages with no episodes return empty arrays in resources, not undefined

### Error State Rendering
- [ ] If WebMCP fails to load (CDN down), page renders normally with no errors -- progressive enhancement only
- [ ] If `json_script` data is empty/malformed, tool callbacks return informative error text

## Test Impact

No existing tests affected -- this is a greenfield feature adding client-side JavaScript. No existing Python views, models, or URLs are modified. The new `{% block webmcp %}` in `base.html` is empty by default and does not change rendering of any existing page.

E2E browser tests could verify WebMCP loads, but that requires a Claude Desktop connection which is out of scope for automated testing. Manual verification via Claude Desktop is the primary test path.

## Rabbit Holes

- **Building a backend API for WebMCP tools**: The tools should read from pre-rendered page data, not make XHR calls to new endpoints. Adding API endpoints would be a separate feature.
- **Auth-gated pages**: Exposing workflow editor or admin pages via WebMCP is explicitly out of scope per the issue.
- **Custom WebMCP transport/proxy**: Don't attempt to proxy WebMCP through Django or build a custom transport layer. Use the library as-is.
- **Vendoring vs CDN**: Start with CDN. Vendoring the JS file is a future optimization if CDN reliability becomes an issue.

## Risks

### Risk 1: WebMCP library stability
**Impact:** If WebMCP has breaking changes or the CDN goes down, the MCP surface disappears (but pages still render normally).
**Mitigation:** Progressive enhancement -- WebMCP is additive. Pages work without it. Pin CDN version if available.

### Risk 2: Data size in templates
**Impact:** Large episode lists serialized via `json_script` could bloat page size.
**Mitigation:** Limit serialized fields to what MCP tools need (title, slug, description, episode_number, published_at). Exclude report_text from list views. Only include report_text on episode detail/report pages.

## Race Conditions

No race conditions identified -- all operations are synchronous client-side JavaScript reading from pre-rendered DOM data. No concurrent writes or shared mutable state.

## No-Gos (Out of Scope)

- Auth-gated pages (workflow editor, admin) -- not exposed via WebMCP in this iteration
- Backend MCP server changes (`apps/ai/mcp/`) -- this is frontend-only
- Write/mutation tools (e.g., creating episodes via WebMCP) -- read-only in v1
- Book site (`blendedworkforce.ai`) -- only the main `base.html` gets WebMCP
- Automated E2E tests for MCP connection -- requires Claude Desktop, manual verification only

## Update System

No update system changes required -- this feature adds only client-side JavaScript via template changes and a CDN script include. No new dependencies, config files, or migrations.

## Agent Integration

No agent integration required for the MCP servers in `apps/ai/mcp/` -- this is a frontend-only feature. The WebMCP JS library creates its own MCP surface independently of the backend MCP servers.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/webmcp-integration.md` describing what pages expose MCP, what tools/resources are available, and how to connect via Claude Desktop
- [ ] Add entry to feature docs index

### Inline Documentation
- [ ] Comment each `{% block webmcp %}` override explaining the registered tools/resources
- [ ] Docstring the JSON data structures serialized for each page

## Success Criteria

- [ ] WebMCP script loads on all public-facing main site pages (not book site)
- [ ] Podcast list page exposes a resource listing available podcasts
- [ ] Episode detail page exposes metadata and report as readable resources
- [ ] At least one callable tool is registered (`search_episodes` on podcast list)
- [ ] Claude Desktop can connect to a page and successfully invoke a tool or read a resource (manual verification)
- [ ] No regressions to existing page functionality or load performance
- [ ] Pages render normally if WebMCP CDN is unavailable (progressive enhancement)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (webmcp-integration)**
  - Name: webmcp-builder
  - Role: Add WebMCP script to base.html, implement per-page MCP registrations, serialize template data
  - Agent Type: builder
  - Resume: true

- **Validator (webmcp-integration)**
  - Name: webmcp-validator
  - Role: Verify pages render correctly, MCP primitives are registered, no regressions
  - Agent Type: validator
  - Resume: true

- **Documentarian (webmcp-docs)**
  - Name: webmcp-documentarian
  - Role: Write feature documentation for WebMCP integration
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add WebMCP to base template
- **Task ID**: build-base-template
- **Depends On**: none
- **Validates**: Manual page load check; template renders without errors
- **Informed By**: spike-1 (confirmed WebMCP API), spike-2 (confirmed data availability)
- **Assigned To**: webmcp-builder
- **Agent Type**: builder
- **Parallel**: true
- Add WebMCP CDN script tag to `apps/public/templates/base.html` `<head>` section
- Add empty `{% block webmcp %}` before `</body>` in `base.html`
- Ensure `book/base.html` does NOT include WebMCP (it has its own independent template)

### 2. Implement podcast list MCP registrations
- **Task ID**: build-podcast-list
- **Depends On**: build-base-template
- **Validates**: `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -x -q`
- **Assigned To**: webmcp-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `{% block webmcp %}` to `podcast_list.html`
- Serialize podcast list data via `json_script` in the view (add `podcasts_json` to context)
- Register `site://podcasts` resource returning podcast list
- Register `search_episodes` tool with client-side string matching

### 3. Implement podcast detail MCP registrations
- **Task ID**: build-podcast-detail
- **Depends On**: build-base-template
- **Validates**: `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -x -q`
- **Assigned To**: webmcp-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `{% block webmcp %}` to `podcast_detail.html`
- Serialize episode list and podcast info via `json_script`
- Register `podcast://{slug}/episodes` and `podcast://{slug}/info` resources

### 4. Implement episode detail MCP registrations
- **Task ID**: build-episode-detail
- **Depends On**: build-base-template
- **Validates**: `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -x -q`
- **Assigned To**: webmcp-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `{% block webmcp %}` to `episode_detail.html`
- Serialize episode metadata, show notes via `json_script`
- Register metadata, show-notes, and report resources
- Only include report resource if `episode.report_text` exists

### 5. Implement episode report MCP registrations
- **Task ID**: build-episode-report
- **Depends On**: build-base-template
- **Validates**: `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -x -q`
- **Assigned To**: webmcp-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `{% block webmcp %}` to `episode_report.html`
- Register full report text as a resource

### 6. Validate integration
- **Task ID**: validate-webmcp
- **Depends On**: build-podcast-list, build-podcast-detail, build-episode-detail, build-episode-report
- **Assigned To**: webmcp-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all templates render without errors via dev server
- Verify WebMCP widget appears on podcast pages
- Verify `json_script` data is correctly embedded in page source
- Verify book site pages do NOT include WebMCP
- Run full test suite to check for regressions

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-webmcp
- **Assigned To**: webmcp-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/webmcp-integration.md`
- Add entry to documentation index
- Document available tools/resources per page
- Document how to connect via Claude Desktop

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: webmcp-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `DJANGO_SETTINGS_MODULE=settings pytest -x -q` | exit code 0 |
| Lint clean | `uv run pre-commit run --all-files` | exit code 0 |
| WebMCP in base template | `grep -c 'webmcp' apps/public/templates/base.html` | output > 0 |
| WebMCP NOT in book template | `grep -c 'webmcp' apps/public/templates/book/base.html` | exit code 1 |
| Podcast list has MCP block | `grep -c 'block webmcp' apps/public/templates/podcast/podcast_list.html` | output > 0 |
| Episode detail has MCP block | `grep -c 'block webmcp' apps/public/templates/podcast/episode_detail.html` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **WebMCP CDN URL**: The exact CDN URL for WebMCP needs to be confirmed. The npm package appears to be `@jason.today/webmcp` but a stable CDN link (e.g., via unpkg or jsdelivr) should be verified before build. Should we vendor the JS file instead of using CDN?

2. **Widget visibility**: The WebMCP widget (colored square in corner) is always visible when the script loads. Is this acceptable UX for all visitors, or should it be hidden behind a query parameter (e.g., `?mcp=1`) or only shown to authenticated users?

3. **Report text size**: Some episode reports are very long (5,000-8,000 words). Should the episode detail page expose the full report text as a resource, or should agents be directed to the dedicated report page for that? This affects page weight for the detail page.
