---
status: Planning
type: feature
appetite: Medium
owner: Tom Counsell
created: 2026-03-17
tracking: https://github.com/yudame/cuttlefish/issues/179
last_comment_id:
---

# Blended Workforce Site (blendedworkforce.ai)

## Problem

*Blended Workforce 2026* is a serialized field manual for CEOs on integrating AI employees into human teams. The book needs a public web presence for announcements, feedback collection, and buying links. There is no existing site — the domain `blendedworkforce.ai` is registered on Cloudflare but not pointed anywhere.

**Current behavior:**
Cuttlefish serves a single domain (`ai.yuda.me`) with one set of URLs, templates, and branding. There is no mechanism to serve different content based on the incoming hostname.

**Desired outcome:**
Visitors to `https://blendedworkforce.ai` see a standalone book site with its own landing page, visual identity, and content — completely separate from Cuttlefish. The two sites share one Django instance, one database, and one Render service but look like independent products.

## Definitions

| Term | Definition | Reference |
|------|-----------|-----------|
| Cuttlefish | Django 6.0 SaaS app deployed on Render at `ai.yuda.me`, serving AI tools, podcasts, and MCP servers | [README](https://github.com/yudame/cuttlefish/blob/main/README.md) |
| Render custom domain | A user-owned domain added to a Render web service; requires CNAME DNS record and issues TLS cert automatically | [Render docs](https://docs.render.com/custom-domains) |
| MainContentView | Base view class in `apps/public/views/helpers/main_content_view.py` that handles HTMX partial/full page rendering | [Source](https://github.com/yudame/cuttlefish/blob/main/apps/public/views/helpers/main_content_view.py) |
| ALLOWED_HOSTS | Django setting that validates the `Host` header on incoming requests; requests to unlisted hosts get 400 | [Django docs](https://docs.djangoproject.com/en/5.1/ref/settings/#allowed-hosts) |

## Prior Art

No prior issues or PRs found related to multi-domain routing or hostname-based content switching in this repo.

## Architectural Impact

- **New dependencies**: None. Uses only Django, Tailwind, and HTMX (already in the stack).
- **Interface changes**: New middleware sets `request.site_name` attribute. Existing views are unaffected.
- **Coupling**: Low — the book app is a leaf node in the dependency graph (depends on `apps.common`, nothing depends on it).
- **Data ownership**: Book app owns its own models (Announcement, Feedback). No shared models modified.
- **Reversibility**: Fully reversible — remove the app from INSTALLED_APPS, remove middleware, remove domain from ALLOWED_HOSTS.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (landing page copy review)
- Review rounds: 1 (visual review of landing page)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Cloudflare DNS access | Manual — verify login to Cloudflare dashboard | CNAME record creation |
| Render dashboard access | Manual — verify login to Render dashboard | Custom domain addition |

## Solution

### Key Elements

- **DomainRoutingMiddleware**: Sets `request.site_name` based on hostname so downstream views/templates know which site to render.
- **`apps/book/` Django app**: Models (Announcement, Feedback), views (landing, announcements list, feedback form), URL config.
- **Hostname-conditional URL routing**: When hostname is `blendedworkforce.ai`, mount book URLs at root `/`. Otherwise, normal Cuttlefish routing applies.
- **Separate base template**: `book/base.html` with its own fonts, colors, layout — no visual connection to Cuttlefish.

### Flow

**blendedworkforce.ai/** → Landing page (book info, authors, buying links) → **Announcements** → Read updates → **Feedback** → Submit form → Thank you

### Technical Approach

- Single Render web service serves both domains (add custom domain via dashboard, not a second service)
- Cloudflare CNAME: `blendedworkforce.ai` → `cuttlefish-ea1h.onrender.com`, SSL mode "Full"
- Middleware reads `request.META['HTTP_HOST']`, strips port, checks against known book domains
- `settings/urls.py` uses a helper function to build urlpatterns conditionally — but since hostname isn't known at import time, the middleware + separate URL dispatch approach is cleaner: a custom `get_urls()` method or a catch-all view that delegates based on `request.site_name`
- Simpler approach: middleware sets `request.urlconf` to `apps.book.urls` for book domains, letting Django's built-in per-request URL resolution handle it natively

## Data Flow

1. **Entry point**: HTTP request arrives at Render service with `Host: blendedworkforce.ai`
2. **DomainRoutingMiddleware**: Reads `HTTP_HOST`, recognizes book domain, sets `request.site_name = "book"` and `request.urlconf = "apps.book.urls"`
3. **Django URL resolver**: Uses `request.urlconf` (per-request override) instead of `ROOT_URLCONF`, resolving against `apps/book/urls.py`
4. **Book view**: Renders template extending `book/base.html` with book-specific branding
5. **Output**: HTML response with book site content and styling

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Middleware must handle malformed `HTTP_HOST` headers gracefully (default to Cuttlefish site)
- [ ] Feedback form view must handle invalid POST data and render errors

### Empty/Invalid Input Handling
- [ ] Feedback form: empty fields show validation errors, don't create records
- [ ] Announcements list: empty state shows "No announcements yet" message

### Error State Rendering
- [ ] 404 on book domain renders a book-branded 404, not Cuttlefish's
- [ ] Feedback form errors display inline, not as a redirect loop

## Test Impact

No existing tests affected — this is a greenfield feature adding a new app and middleware. Existing URL routing and views remain unchanged.

## Rabbit Holes

- **Django Sites framework**: Looks like the right tool but adds complexity (database-backed site IDs, `SITE_ID` setting). The `request.urlconf` approach is simpler and sufficient.
- **Separate Render service**: Doubles infrastructure cost and env var management for zero benefit — single service with custom domain is the right call.
- **Newsletter/signup forms in v1**: Tempting to add now but out of scope. The data model should not preclude them, but don't build them yet.
- **Custom admin for announcements**: Django's built-in admin is sufficient for managing announcements. No need for a custom CRUD UI.

## Risks

### Risk 1: Cloudflare proxy + Render TLS conflict
**Impact:** HTTPS doesn't work, site shows SSL errors
**Mitigation:** Set Cloudflare SSL mode to "Full" (not "Full Strict" since Render auto-issues certs). If issues persist, disable Cloudflare proxy (grey cloud the CNAME) and let Render handle TLS directly.

### Risk 2: `request.urlconf` override breaks HTMX partial rendering
**Impact:** HTMX requests on the book site resolve against wrong URL config
**Mitigation:** The middleware sets `urlconf` before any view processing, so HTMX and full-page requests both use the correct URL config. Verify with manual testing.

## Race Conditions

No race conditions identified — all operations are synchronous request/response with no shared mutable state.

## No-Gos (Out of Scope)

- Newsletter signup (post-v1)
- Free draft copy signup (post-v1)
- Author chat feature (post-v1)
- Buying/payment processing — v1 uses external links only (Amazon, Gumroad, etc.)
- Mobile app or PWA
- User authentication on the book site
- Analytics integration (add separately later)
- Custom admin UI for announcements (use Django admin)

## Update System

No update system changes required — this is a standard Django app addition deployed via Render's existing git-push workflow.

## Agent Integration

No agent integration required — this is a public-facing website with no AI agent interaction.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/blendedworkforce-site.md` describing the multi-domain setup and book app
- [ ] Add entry to `docs/features/README.md` index table (if exists)

### Inline Documentation
- [ ] Document the `request.urlconf` pattern in the middleware docstring (non-obvious Django feature)

## Success Criteria

- [ ] CNAME record exists on Cloudflare: `blendedworkforce.ai` → `cuttlefish-ea1h.onrender.com`
- [ ] Custom domain added in Render dashboard with valid TLS
- [ ] `https://blendedworkforce.ai` shows book landing page
- [ ] `https://ai.yuda.me` continues showing Cuttlefish (no regression)
- [ ] Landing page displays: book title, subtitle, author names (Tom Counsell & Valor Engels), positioning copy, buying link placeholders
- [ ] `/announcements/` lists announcements ordered by date
- [ ] `/feedback/` form submits and stores feedback
- [ ] Book site has its own base template, color palette, and typography
- [ ] All book pages work without authentication
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (infra)**
  - Name: infra-builder
  - Role: DNS, Render config, ALLOWED_HOSTS, middleware
  - Agent Type: builder
  - Resume: true

- **Builder (book-app)**
  - Name: book-app-builder
  - Role: Django app scaffold, models, views, URL config
  - Agent Type: builder
  - Resume: true

- **Builder (templates)**
  - Name: template-builder
  - Role: Base template, landing page, announcements, feedback form, CSS
  - Agent Type: designer
  - Resume: true

- **Validator (site)**
  - Name: site-validator
  - Role: Verify both domains work, visual check, form submission test
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Infrastructure Setup (Manual + Code)
- **Task ID**: build-infra
- **Depends On**: none
- **Assigned To**: infra-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `"blendedworkforce.ai"` and `"www.blendedworkforce.ai"` to `ALLOWED_HOSTS` in `settings/env.py`
- Add `"https://blendedworkforce.ai"` to `CORS_ORIGIN_WHITELIST` in `settings/env.py`
- Add `"https://blendedworkforce.ai"` to `CSRF_TRUSTED_ORIGINS` in `settings/production.py`
- Create `DomainRoutingMiddleware` in `apps/common/utilities/django/middleware.py`
- Register middleware in `settings/base.py` MIDDLEWARE list (after SecurityMiddleware)
- Document manual steps: Cloudflare CNAME, Render custom domain, Cloudflare SSL mode

### 2. Book App Scaffold
- **Task ID**: build-app
- **Depends On**: none
- **Validates**: `apps/book/tests/test_models.py` (create)
- **Assigned To**: book-app-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-infra)
- Create `apps/book/` directory: `__init__.py`, `apps.py`, `urls.py`, `admin.py`
- Create models: `Announcement` (title, body, published_at, created_at) and `Feedback` (name, email, message, created_at)
- Register models in Django admin
- Create views: `LandingView`, `AnnouncementListView`, `FeedbackFormView` (with success redirect)
- Define URL patterns in `apps/book/urls.py` (app_name = "book")
- Register app in `settings/base.py` INSTALLED_APPS
- Run `makemigrations` and `migrate`
- Write model tests

### 3. Templates and Styling
- **Task ID**: build-templates
- **Depends On**: build-app
- **Assigned To**: template-builder
- **Agent Type**: designer
- **Parallel**: false
- Create `apps/public/templates/book/base.html` — standalone base template with:
  - Clean, editorial typography (e.g., serif headings, clean sans-serif body)
  - Color palette distinct from Cuttlefish (book-appropriate, professional)
  - Responsive layout, mobile-first
  - Navigation: Home, Announcements, Feedback
  - Footer with author info
- Create `apps/public/templates/book/landing.html` — hero section with book title/subtitle, author names, positioning copy, buying link placeholders
- Create `apps/public/templates/book/announcements.html` — chronological list with empty state
- Create `apps/public/templates/book/feedback.html` — form with name, email, message fields
- Create `apps/public/templates/book/feedback_success.html` — thank you page
- Create `static/css/book.css` — book site design system (or use Tailwind inline)
- Landing page copy sourced from `~/work-vault/book-blended-workforce-2026/`

### 4. Integration Validation
- **Task ID**: validate-site
- **Depends On**: build-infra, build-app, build-templates
- **Assigned To**: site-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `request.urlconf` override works in local dev (test with `Host` header override)
- Verify Cuttlefish URLs still resolve correctly (no regression)
- Verify book landing page renders with correct content
- Verify announcements page shows empty state
- Verify feedback form submits and creates Feedback record
- Verify 404 on book domain doesn't show Cuttlefish branding
- Run full test suite

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-site
- **Assigned To**: template-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/blendedworkforce-site.md`
- Document the multi-domain pattern for future reference

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: site-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `DJANGO_SETTINGS_MODULE=settings pytest apps/book/ -x -q` | exit code 0 |
| Migrations clean | `uv run python manage.py makemigrations --check --dry-run` | exit code 0 |
| Format clean | `uv run black --check apps/book/` | exit code 0 |
| Book URLs resolve | `uv run python manage.py shell -c "from django.urls import reverse; print(reverse('book:landing'))"` | output contains / |
| Middleware registered | `grep -c DomainRoutingMiddleware settings/base.py` | output > 0 |

## Open Questions

1. **Landing page copy** — Should the landing page use the full positioning statement from the vault ("An annual field manual for CEOs at incumbent companies...") or a shorter hook? The vault has extensive copy to choose from.

2. **Buying links** — What external platforms should be linked for purchasing? Amazon KDP, Gumroad, Substack, or placeholder "Coming soon" buttons for v1?

3. **Announcements authoring** — Is Django admin sufficient for creating announcements, or do you want a simpler front-end form (would push to post-v1)?

4. **Design direction** — The Cuttlefish design system uses warm minimalism with monospace headers. Should the book site go editorial/serif (more book-like) or something else entirely? Any reference sites you like?
