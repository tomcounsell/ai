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

AI agents that want to use `app.bwforce.ai` to manage podcasts have no machine-readable interface. They must scrape HTML or manually navigate the UI to discover podcasts, read episode content, check workflow status, or figure out how to create and produce episodes.

**Current behavior:**
The site is human-only. All content and management UI is in HTML templates with no structured data layer for AI consumption.

**Desired outcome:**
Every page exposes MCP tools and resources via WebMCP so that AI agents can connect to any page and programmatically read data, discover site capabilities, and get guided instructions for managing the entire podcast production system — from creating episodes through the 12-phase workflow to publishing.

## Purpose

This exists so AI agents (Claude Desktop, Claude Code, etc.) can **browse the site and manage podcasts across the entire production system**. The WebMCP surface is the agent's eyes and hands:

- **Resources** give agents structured data (episode lists, workflow status, metadata, reports)
- **Tools** give agents guided instructions for mutations, pointing them to the right pages/forms/buttons in the existing web UI
- **Auth** is session-based — agents log in via the normal login page, then all pages work

No new API endpoints are needed. Agents use the existing web interface; WebMCP makes it machine-readable.

## Prior Art

- **PR #8**: CTO Tools Security Review MCP Server — established backend MCP patterns in `apps/ai/mcp/`. This is frontend-only via WebMCP JS, no overlap.
- **Issue #179**: blendedworkforce.ai separate domain — `base.html` vs `book/base.html` means WebMCP loads only on the main site template.

## Spike Results

### spike-1: WebMCP API and transport mechanism
- **Assumption**: "WebMCP provides a JS library exposing tools/resources to MCP clients"
- **Method**: web-research
- **Finding**: Confirmed. WebMCP provides `registerTool()`, `registerResource()`, and `registerPrompt()`. Available as `@jason.today/webmcp` via npm/CDN.
- **Confidence**: high

### spike-2: Data availability in templates
- **Assumption**: "Episode data needed for MCP resources is already in template context"
- **Method**: code-read
- **Finding**: Confirmed. All views already provide the data needed. No new views or endpoints required.
- **Confidence**: high

## Data Flow

### Read Path (Resources)
1. Page load → Django view renders template with data in context
2. Template `{% block webmcp %}` serializes data via `{{ data|json_script }}`
3. WebMCP JS registers resources from serialized data
4. Agent reads resource → gets structured JSON

### Mutation Path (Instruction Tools)
1. Agent invokes a mutation tool (e.g., `how_to_create_episode`)
2. Tool callback returns text instructions: which URL to visit, what form fields to fill, what button to click
3. Agent follows the instructions using the existing web UI (navigating pages, filling forms)
4. No API calls — the agent uses the same UI a human would

## Architectural Impact

- **New dependencies**: WebMCP JS library (CDN or vendored). No Python dependencies.
- **Interface changes**: New `{% block webmcp %}` in `base.html` (empty by default). No view or URL changes.
- **Coupling**: Low. Additive JS layer reading existing template context.
- **Reversibility**: Trivial. Remove script tag and block overrides.

## Appetite

**Size:** Medium

**Team:** Solo dev

## Prerequisites

No prerequisites — WebMCP is a client-side JS library with no API keys or server-side dependencies.

## Solution

### Key Elements

- **WebMCP base integration**: Script in `base.html` (main site only, not book site) with empty `{% block webmcp %}`
- **Page-specific MCP registrations**: Each page overrides the block to register relevant tools and resources
- **JSON data bridge**: Django's `json_script` filter serializes model data into `<script>` tags
- **Instruction tools**: Mutation tools return text guides pointing agents to the correct UI flows
- **Session auth**: Agents log in via `/admin/login/` to access workflow and management pages

### Technical Approach

- Include WebMCP via CDN in `base.html` `<head>`
- Add `{% block webmcp %}` before `</body>` (empty by default)
- Each template overrides the block to register page-appropriate primitives
- Use `{{ data|json_script:"data-id" }}` + `JSON.parse(document.getElementById('data-id').textContent)`
- Tool callbacks for mutations return structured text instructions, not API calls

### Pages and Their MCP Primitives

#### Public Pages (no auth required)

**1. Podcast list** (`podcast_list.html`):
- Resource `site://podcasts` — all podcasts with title, description, slug, episode count
- Tool `search_episodes(query)` — client-side search across podcast titles/descriptions

**2. Podcast detail** (`podcast_detail.html`):
- Resource `podcast://{slug}/episodes` — published episodes with title, number, date, description
- Resource `podcast://{slug}/info` — podcast metadata (title, description, author, links)
- Tool `how_to_create_episode()` — returns instructions: "Navigate to {create_url}, fill in title and description (this becomes the research prompt), optionally add tags, then click Create Episode"

**3. Episode detail** (`episode_detail.html`):
- Resource `episode://{id}/metadata` — full episode metadata (title, number, audio_url, published_at)
- Resource `episode://{id}/show-notes` — show notes content
- Resource `episode://{id}/report` — research report text (if available)
- Resource `episode://{id}/sources` — source citations

**4. Episode report** (`episode_report.html`):
- Resource `report://{id}` — full report text

#### Auth-Gated Pages (session login required)

**5. Episode create** (`episode_create.html`):
- Tool `create_episode_guide()` — returns field descriptions: title (required, max 200), description (required, becomes research prompt), tags (optional, comma-separated)
- Resource `create://form-fields` — serialized form field metadata

**6. Episode workflow** (`episode_workflow.html`):
- Resource `workflow://{id}/status` — current step, phase statuses, overall progress, blocked_on
- Resource `workflow://{id}/phases` — all 12 phases with number, name, status (complete/in_progress/pending)
- Resource `workflow://{id}/artifacts` — available artifacts with type, word count, and content summary
- Tool `how_to_navigate_workflow(step_number)` — returns instructions for viewing a specific phase
- Tool `how_to_manage_research(action)` — returns instructions for: retrying failed research sources, pasting manual research, adding file research
- Tool `how_to_manage_audio(action)` — returns instructions for: uploading audio file, checking audio status
- Tool `how_to_edit_metadata()` — returns instructions for editing title, description, show_notes, tags at Step 11
- Tool `how_to_manage_cover_art(action)` — returns instructions for: regenerating cover art, uploading custom cover
- Tool `how_to_publish()` — returns instructions for the Step 12 publish confirmation
- Tool `how_to_resume_workflow()` — returns instructions for clicking the resume/pause pipeline action button

**7. Podcast edit** (`podcast_edit.html`):
- Resource `podcast://{slug}/edit-fields` — current field values for the podcast
- Tool `how_to_edit_podcast()` — returns field descriptions and instructions for saving

### Mutation Tool Response Format

All `how_to_*` tools return structured text that agents can follow:

```
ACTION: Create a new episode
URL: /podcasts/yudame-research/episodes/create/
METHOD: Fill the form and submit

FIELDS:
- title (required): Episode title, max 200 characters
- description (required): Research prompt — this drives the entire production pipeline. Be specific about the topic, angle, and what to investigate.
- tags (optional): Comma-separated tags for categorization

SUBMIT: Click "Create Episode" button
AFTER: You'll be redirected to the workflow page where automated production begins.
```

## Failure Path Test Strategy

### Exception Handling Coverage
- WebMCP failures are client-side and silent (widget disappears, page still works)
- Tool callbacks wrap logic in try/catch, return error text on failure

### Empty/Invalid Input Handling
- [ ] Missing JSON data returns empty result, not crash
- [ ] `search_episodes` with empty query returns all episodes
- [ ] Pages with no episodes return empty arrays
- [ ] Workflow page with no artifacts returns empty list

### Error State Rendering
- [ ] WebMCP CDN failure → page renders normally (progressive enhancement)
- [ ] Malformed `json_script` data → tool callbacks return informative error text

## Test Impact

No existing tests affected — greenfield client-side JavaScript. The new `{% block webmcp %}` is empty by default.

Manual verification via Claude Desktop is the primary test path. Automated tests can verify `json_script` data is embedded correctly in page source.

## Rabbit Holes

- **Building backend API endpoints for mutations**: Agents use the existing web UI. Don't create REST endpoints for WebMCP tools.
- **Custom WebMCP transport/proxy**: Use the library as-is, don't proxy through Django.
- **Vendoring vs CDN**: Start with CDN. Vendor later if reliability is an issue.
- **Real-time workflow polling in WebMCP**: The workflow page already polls via HTMX. Don't duplicate this in WebMCP — agents can re-read the resource.

## Risks

### Risk 1: WebMCP library stability
**Impact:** If CDN goes down, MCP surface disappears (pages still render).
**Mitigation:** Progressive enhancement. Pin CDN version.

### Risk 2: Data size in templates
**Impact:** Large episode lists or reports bloat page size.
**Mitigation:** Limit serialized fields on list pages. Full report text only on report page.

### Risk 3: Stale instruction text
**Impact:** If UI changes, mutation tool instructions become outdated.
**Mitigation:** Keep instructions in template blocks (not hardcoded JS) so they update with the template. Document this in feature docs so future UI changes cascade to tool text.

## Race Conditions

None — all operations are synchronous client-side JS reading pre-rendered DOM data.

## No-Gos (Out of Scope)

- Django admin pages — not exposed via WebMCP
- Backend MCP server changes (`apps/ai/mcp/`) — this is frontend-only
- Direct mutation tools (tools that POST/PATCH themselves) — agents use the UI
- Book site (`blendedworkforce.ai`) — only main `base.html` gets WebMCP
- Automated E2E tests for MCP connection — manual verification only

## Update System

No update system changes — client-side JS via template changes and CDN script only.

## Agent Integration

No changes to backend MCP servers in `apps/ai/mcp/`. WebMCP creates its own frontend MCP surface independently.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/webmcp-integration.md` with: pages covered, tools/resources per page, how to connect, how agents authenticate, mutation workflow examples
- [ ] Add entry to feature docs index

### Inline Documentation
- [ ] Comment each `{% block webmcp %}` override explaining registered tools/resources
- [ ] Document JSON data structures serialized for each page
- [ ] Document the mutation tool response format convention

## Success Criteria

- [ ] WebMCP script loads on all main site pages (not book site)
- [ ] Podcast list page exposes resource listing available podcasts
- [ ] Episode detail page exposes metadata, show notes, and report as resources
- [ ] Workflow page exposes phase statuses and artifact data as resources
- [ ] Mutation tools return clear text instructions for: creating episodes, managing research, managing audio, editing metadata, publishing
- [ ] `search_episodes` tool works on podcast list page
- [ ] Claude Desktop can connect to a page and read a resource (manual verification)
- [ ] Claude Desktop can invoke a mutation tool and follow the instructions successfully (manual verification)
- [ ] Session auth works — agent logs in, then workflow pages expose MCP data
- [ ] No regressions to existing page functionality or load performance
- [ ] Pages render normally if WebMCP CDN is unavailable (progressive enhancement)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (webmcp-integration)**
  - Name: webmcp-builder
  - Role: Add WebMCP to base.html, implement all page-specific MCP registrations
  - Agent Type: builder
  - Resume: true

- **Validator (webmcp-integration)**
  - Name: webmcp-validator
  - Role: Verify pages render, MCP primitives registered, no regressions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add WebMCP to base template
- **Task ID**: build-base-template
- **Depends On**: none
- **Validates**: Manual page load; template renders without errors
- **Assigned To**: webmcp-builder
- **Agent Type**: builder
- **Parallel**: true
- Add WebMCP CDN script tag to `apps/public/templates/base.html` `<head>`
- Add empty `{% block webmcp %}` before `</body>` in `base.html`
- Confirm `book/base.html` does NOT include WebMCP

### 2. Implement public podcast page MCP registrations
- **Task ID**: build-public-pages
- **Depends On**: build-base-template
- **Validates**: `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -x -q`
- **Assigned To**: webmcp-builder
- **Agent Type**: builder
- **Parallel**: true
- Podcast list: resource (podcasts), tool (search_episodes), tool (how_to_create_episode)
- Podcast detail: resources (episodes, info)
- Episode detail: resources (metadata, show-notes, report, sources)
- Episode report: resource (full report text)
- Serialize via `json_script` in each template

### 3. Implement episode create MCP registrations
- **Task ID**: build-create-page
- **Depends On**: build-base-template
- **Assigned To**: webmcp-builder
- **Agent Type**: builder
- **Parallel**: true
- Tool: `create_episode_guide()` with field descriptions
- Resource: form field metadata

### 4. Implement workflow page MCP registrations
- **Task ID**: build-workflow-page
- **Depends On**: build-base-template
- **Assigned To**: webmcp-builder
- **Agent Type**: builder
- **Parallel**: true
- Resources: workflow status, phases, artifacts
- Tools: navigate_workflow, manage_research, manage_audio, edit_metadata, manage_cover_art, publish, resume_workflow
- Serialize workflow data via `json_script` (add to view context if not already present)

### 5. Implement podcast edit MCP registrations
- **Task ID**: build-edit-page
- **Depends On**: build-base-template
- **Assigned To**: webmcp-builder
- **Agent Type**: builder
- **Parallel**: true
- Resource: current field values
- Tool: how_to_edit_podcast with field guide

### 6. Validate integration
- **Task ID**: validate-webmcp
- **Depends On**: build-public-pages, build-create-page, build-workflow-page, build-edit-page
- **Assigned To**: webmcp-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all templates render without errors
- Verify WebMCP widget appears
- Verify `json_script` data embedded correctly
- Verify book site does NOT include WebMCP
- Verify auth-gated pages work after login
- Run full test suite

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-webmcp
- **Assigned To**: webmcp-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `docs/features/webmcp-integration.md`
- Add entry to feature docs index
- Document: pages, tools/resources per page, auth flow, mutation tool format, how to connect

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: webmcp-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `DJANGO_SETTINGS_MODULE=settings pytest -x -q` | exit code 0 |
| Lint clean | `uv run pre-commit run --all-files` | exit code 0 |
| WebMCP in base template | `grep -c 'webmcp' apps/public/templates/base.html` | output > 0 |
| WebMCP NOT in book template | `grep -c 'webmcp' apps/public/templates/book/base.html` | exit code 1 |
| Podcast list has MCP block | `grep -c 'block webmcp' apps/public/templates/podcast/podcast_list.html` | output > 0 |
| Workflow has MCP block | `grep -c 'block webmcp' apps/public/templates/podcast/episode_workflow.html` | output > 0 |

## Critique Results

**Date**: 2026-03-27
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings**: 10 total (0 blockers, 6 concerns, 4 nits)
**Verdict**: READY TO BUILD

### Concerns

#### 1. WebMCP + Claude Desktop compatibility unvalidated
- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Spike Results > spike-1
- **Finding**: The spike confirms WebMCP provides registerTool/registerResource, but does not validate that Claude Desktop (or other MCP clients) can actually connect to and read from a WebMCP-enabled page. This is a critical path assumption.
- **Suggestion**: Add a spike-3 or prerequisite check: "Verify Claude Desktop can connect to a page using WebMCP and read a registered resource." This is a 30-minute validation that de-risks the entire project.

#### 2. Instruction-based mutation tools are fragile and may be premature
- **Severity**: CONCERN
- **Critics**: Skeptic, Simplifier
- **Location**: Solution > Mutation Path / Pages and Their MCP Primitives
- **Finding**: The plan assumes agents can reliably follow multi-step text instructions. The workflow page alone has 7 instruction tools. These are static text that can become stale, and there's no feedback loop to confirm the agent completed the action. This is a lot of surface area for a first integration.
- **Suggestion**: Split into two phases: Phase 1 covers all resources (~15) plus search and how_to_create_episode. Phase 2 adds remaining how_to_* instruction tools after validating the core value proposition.

#### 3. No monitoring for WebMCP load failures
- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: Risks > Risk 1
- **Finding**: The mitigation for CDN failure ("progressive enhancement, pin version") lacks operational specifics. If the library silently fails to load, nobody knows until an agent complains.
- **Suggestion**: Add a lightweight JS error handler for WebMCP load failures, or document that availability is verified via manual Claude Desktop testing during deploy verification.

#### 4. Auth-gated page data in DOM needs security confirmation
- **Severity**: CONCERN
- **Critics**: Adversary
- **Location**: Solution > JSON data bridge / Data Flow > Read Path
- **Finding**: Auth-gated pages serialize sensitive data (workflow status, artifacts) into the DOM via json_script. If pages are CDN-cached or session expires, stale data could be exposed.
- **Suggestion**: Confirm auth-gated pages have @login_required and Cache-Control: private. Document this assumption explicitly.

#### 5. Success criteria lack user-story-style acceptance tests
- **Severity**: CONCERN
- **Critics**: User
- **Location**: Success Criteria
- **Finding**: Success criteria are almost entirely technical. The two user-facing criteria are manual-only with no definition of "successfully."
- **Suggestion**: Add acceptance criteria like: "An AI agent can connect to the podcast list page, discover podcasts, navigate to one, and read episode metadata without human assistance."

#### 6. Open Question #1 (CDN URL) blocks Task 1
- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Open Questions > #1
- **Finding**: The CDN URL is listed as an open question but Task 1 requires adding the script tag to base.html.
- **Suggestion**: Resolve before build, or specify fallback: "use unpkg.com/@jason.today/webmcp@latest, pin version after validation."

### Nits

#### 7. CSRF token handling for agent auth not documented
- **Severity**: NIT
- **Critics**: Operator
- **Location**: Solution > Session auth
- **Finding**: Agents log in via /admin/login/ which requires CSRF token handling. This works but should be documented.
- **Suggestion**: Document explicit auth flow steps in the feature documentation.

#### 8. Duplicate create_episode_guide tool and form-fields resource
- **Severity**: NIT
- **Critics**: Simplifier
- **Location**: Solution > Episode create page
- **Finding**: The create page has both a tool (create_episode_guide) and a resource (form field metadata) that serve nearly identical purposes.
- **Suggestion**: Consolidate into a single resource that includes field metadata and instructions.

#### 9. No existing block name conflicts (verified clean)
- **Severity**: NIT
- **Critics**: Archaeologist
- **Location**: Prior Art section
- **Finding**: No evidence of checking for existing template block name conflicts. Verified via grep: no existing "webmcp" or "mcp" blocks in templates.
- **Suggestion**: No action needed -- confirmed clean.

#### 10. Client-side search exposes full dataset
- **Severity**: NIT
- **Critics**: Adversary
- **Location**: Solution > search_episodes tool
- **Finding**: search_episodes operates on all data already in the DOM. This is fine for public pages but worth acknowledging if pagination is added later.
- **Suggestion**: Document that search operates on the page's rendered dataset, not a separate API.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | Tasks 1-8, sequential, no gaps |
| Dependencies valid | PASS | All Depends On references point to valid task IDs |
| File paths exist | PASS | 9 of 9 template files exist. 1 doc file intentionally new (Task 7) |
| Prerequisites met | PASS | No prerequisites declared |
| Cross-references | PASS | Success criteria map to tasks. No-Gos do not appear in solution. Rabbit holes avoided in tasks. |

### Verdict

**READY TO BUILD** -- No blockers. The 6 concerns are acknowledged risks and scope suggestions, not plan defects. The most actionable item is Concern #1 (validate Claude Desktop compatibility with WebMCP) which should be a 30-minute check before or at the start of Task 1. Concern #2 (phasing) is a scope management suggestion the builder can adopt or defer.

---

## Open Questions

1. **WebMCP CDN URL**: The npm package is `@jason.today/webmcp`. Need to confirm stable CDN link (unpkg/jsdelivr) before build. Vendor as fallback?

2. **Widget visibility**: The WebMCP widget (colored square) is always visible. Acceptable for all visitors, or gate behind `?mcp=1` query param?

3. **Workflow data serialization**: The workflow page already has rich data in the template context. Need to verify all phase statuses and artifact metadata are available without adding new view context — may need minor view updates to expose `json_script`-friendly data.
