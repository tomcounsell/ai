---
status: Ready
type: feature
appetite: Small
owner: valorengels
created: 2026-04-08
tracking: https://github.com/yudame/cuttlefish/issues/234
last_comment_id:
---

# Briefing Landing Page

## Problem

There is no page that explains the core "AI Briefing" product promise to someone who has never heard of Yudame. A cold visitor arriving at `/` sees a list of existing podcast episodes — they have no idea they could request a custom briefing for their own upcoming meeting.

**Current behavior:**
`/` renders `HomeView`, which shows recent public podcast episodes and two AI tool cards. Authenticated users are immediately redirected to the dashboard. There is no messaging about the "get briefed before your meeting" value proposition. No `/briefing/` route exists.

**Desired outcome:**
A dedicated page at `/briefing/` leads with the emotional hook, explains the three-step workflow, and drives anonymous visitors to sign up. Authenticated users see a direct "Start a Briefing" CTA instead.

## Prior Art

No prior issues found related to this work. The `/briefing/` route is greenfield.

## Architectural Impact

- **New dependencies**: None — no new models, forms, migrations, or Python packages
- **Interface changes**: None — purely additive (new view + new URL + new template)
- **Coupling**: Minimal — `BriefingLandingView` reads `request.user.is_authenticated` from standard Django auth; no new coupling introduced
- **Data ownership**: No change — no data owned or stored
- **Reversibility**: Trivially reversible — delete three files and one URL entry

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Standard Django + brand.css are already in place.

## Solution

### Key Elements

- **`BriefingLandingView`**: Django class-based view extending `MainContentView`. Passes `is_authenticated` flag to template context for conditional CTA rendering. No redirect logic — both user states see the full page.
- **URL route**: `path("briefing/", BriefingLandingView.as_view(), name="briefing")` added to the "Example pages" block in `apps/public/urls.py`. Named `public:briefing`.
- **`briefing.html` template**: Four sections using existing brand.css classes. Hero with emotional hook headline, how-it-works three-step flow, what-you-get two-column card, CTA button that varies by auth state.

### Flow

Anonymous visitor → `/briefing/` → Sees full landing page → Clicks "Get Your First Briefing" → `/accounts/signup/`

Authenticated user → `/briefing/` → Sees full landing page with different CTA → Clicks "Start a Briefing" → `/podcast/` (podcast list, entry to episode creation)

### Technical Approach

- Subclass `MainContentView` (same pattern as `PricingView` in `apps/public/views/pages.py`)
- `get()` method adds `user_is_authenticated = request.user.is_authenticated` to `self.context`, then calls `self.render(request)`
- Template placed at `apps/public/templates/briefing.html` (top-level, not in `pages/` subdirectory — matches `home.html` placement convention)
- Template extends `base.html` and uses `{% block main_header %}` for hero, `{% block content %}` for remaining sections
- CSS classes: `.section-hero`, `.product-card`, `.divider-technical`, `.text-technical-label`, `.btn-brand`, `.btn-brand-accent` — all present in `brand.css`
- CTA variation: `{% if user_is_authenticated %}` → link to `{% url 'podcast:list' %}` with text "Start a Briefing"; else → `/accounts/signup/` with text "Get Your First Briefing"
- No inline styles beyond CSS custom properties already used throughout the codebase (e.g., `var(--space-xl)`)
- `PrivacyPolicyView` is the simplest existing reference implementation (no extra context data, just `return self.render(request)`)

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope — this is a static view with no database queries or external calls.

### Empty/Invalid Input Handling
No user input in scope — GET only, no form processing.

### Error State Rendering
No error states in scope — the view cannot fail unless Django's template engine breaks, which is infrastructure-level. Standard 500 handling applies.

## Test Impact

No existing tests are affected — this is a greenfield feature. New tests must be written:
- `apps/public/tests/test_views/test_briefing_landing.py` (create): test anonymous GET returns 200, authenticated GET returns 200, anonymous response contains "Get Your First Briefing", authenticated response contains "Start a Briefing".

## Rabbit Holes

- **Briefing request form**: The CTA links to an existing flow. Do not add a `/briefing/request/` intake form here — that is issue #233's territory.
- **Audio player embed**: Confirmed dropped from scope. Using a visual icon + duration text instead of an interactive audio element avoids the dependency on a specific published episode.
- **SEO meta tags**: Do not over-engineer metadata. One `{% block meta_description %}` block is sufficient.
- **Animation or JavaScript**: The page is server-side rendered with no JS requirement. Do not add scroll animations or JS-dependent interactions.

## Risks

### Risk 1: `podcast:list` URL requires a podcast slug
**Impact:** The authenticated CTA `{% url 'podcast:list' %}` might error if the URL pattern requires arguments.
**Mitigation:** Confirmed via `apps/podcast/urls.py` — `name="list"` maps to `path("", PodcastListView.as_view())` with no required arguments. This is safe.

### Risk 2: `/accounts/signup/` route may not exist
**Impact:** Anonymous CTA links to a dead URL.
**Mitigation:** This is a hardcoded path (allauth standard route). Verify the route exists at test time. If allauth is not installed, the link will 404 but the page itself will still render correctly (CTA is just an `<a>` tag).

## Race Conditions

No race conditions identified — all operations are synchronous, single-threaded, and read-only.

## No-Gos (Out of Scope)

- Briefing request/intake form (separate issue)
- Any modification to `HomeView` or the root `/` route
- New CSS rules or new stylesheet
- JavaScript-dependent interactions
- Authentication-gating the page (both user states see the page)
- Audio player or embedded media

## Update System

No update system changes required — this feature is a standard Django view/template addition with no new dependencies or config.

## Agent Integration

No agent integration required — this is a web UI page with no MCP server exposure needed.

## Documentation

No dedicated feature doc required — the page is self-documenting (it IS the user-facing documentation of the briefing product). Update `CLAUDE.md` is not needed. If `docs/features/` has a podcast or public UI index, add a one-line entry after shipping.

## Success Criteria

- [ ] `GET /briefing/` returns HTTP 200 for anonymous users
- [ ] `GET /briefing/` returns HTTP 200 for authenticated users
- [ ] Anonymous response contains "Get Your First Briefing" and links to `/accounts/signup/`
- [ ] Authenticated response contains "Start a Briefing" and does not redirect away
- [ ] Page includes hero headline, three-step section, and what-you-get section
- [ ] No new CSS rules, no inline styles beyond CSS variable references
- [ ] `{% url 'public:briefing' %}` resolves without error
- [ ] All existing public app tests pass: `DJANGO_SETTINGS_MODULE=settings pytest apps/public/ -v`

## Team Orchestration

### Team Members

- **Builder (briefing-page)**
  - Name: briefing-builder
  - Role: Implement view, URL, and template
  - Agent Type: builder
  - Resume: true

- **Validator (briefing-page)**
  - Name: briefing-validator
  - Role: Verify rendering, CTA logic, CSS-only constraint, test pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add BriefingLandingView to landing_views.py
- **Task ID**: build-view
- **Depends On**: none
- **Validates**: `apps/public/tests/test_views/test_briefing_landing.py` (create)
- **Assigned To**: briefing-builder
- **Agent Type**: builder
- **Parallel**: true
- In `apps/public/views/landing_views.py`, add `BriefingLandingView(MainContentView)` class after `HomeView`
- Import `MainContentView` from `apps.public.views.helpers.main_content_view`
- Set `template_name = "briefing.html"`
- Implement `get()`: add `user_is_authenticated` to context, call `self.render(request)`

### 2. Register URL route in apps/public/urls.py
- **Task ID**: build-url
- **Depends On**: build-view
- **Assigned To**: briefing-builder
- **Agent Type**: builder
- **Parallel**: false
- Import `BriefingLandingView` from `.views.landing_views`
- Add `path("briefing/", BriefingLandingView.as_view(), name="briefing")` to the pages block

### 3. Create briefing.html template
- **Task ID**: build-template
- **Depends On**: build-url
- **Assigned To**: briefing-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/public/templates/briefing.html` extending `base.html`
- Hero section: `section-hero` div, `PRIVATE_BRIEFING_01` technical label, emotional hook headline, sub-headline
- How-it-works section: three `product-card` items labeled `STEP_01`/`STEP_02`/`STEP_03`
- What-you-get section: two columns — "Your Briefing" card and "Companion Docs" card
- CTA: `.btn-brand.btn-brand-accent` — conditional on `user_is_authenticated`
- `divider-technical` between sections
- No inline styles beyond CSS custom property references (e.g., `var(--space-xl)`)

### 4. Write tests
- **Task ID**: build-tests
- **Depends On**: build-template
- **Assigned To**: briefing-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `apps/public/tests/test_views/test_briefing_landing.py`
- Test: anonymous GET `/briefing/` → 200, contains "Get Your First Briefing"
- Test: authenticated GET `/briefing/` → 200, contains "Start a Briefing"
- Test: `{% url 'public:briefing' %}` resolves (use `reverse('public:briefing')`)
- Test: anonymous response does NOT redirect (status_code == 200, not 302)

### 5. Validate
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: briefing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/public/ -v` — all pass
- Verify `briefing.html` contains no `<style>` tags and no `style="..."` attributes beyond CSS var references
- Verify `BriefingLandingView` has no `redirect()` calls
- Confirm `public:briefing` is registered and reversible

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Public app tests | `DJANGO_SETTINGS_MODULE=settings pytest apps/public/ -v` | exit code 0 |
| Route resolves | `DJANGO_SETTINGS_MODULE=settings python -c "from django.urls import reverse; reverse('public:briefing')"` | exit code 0 |
| No new CSS | `grep -n '<style' apps/public/templates/briefing.html` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
