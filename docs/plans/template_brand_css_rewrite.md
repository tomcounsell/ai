---
status: Planning
type: chore
appetite: Large
owner: Tom
created: 2026-02-23
tracking: https://github.com/yudame/cuttlefish/issues/105
---

# Template Brand CSS Rewrite

## Problem

PR #104 created a comprehensive design system in `brand.css` with 30+ component classes (cards, buttons, labels, navigation, forms, dividers, status indicators) and established Inter-first typography. But only the homepage, footer, and podcast templates were updated to use it. The remaining ~16 templates still use old patterns.

**Current behavior:**
- Nav links render in IBM Plex Mono (`.nav-brand a` forces monospace) instead of Inter
- MCP product pages have 190-line `<style>` blocks that override brand.css headings back to monospace
- Auth pages (login, settings, password) use generic Tailwind (`bg-slate-700`, `ring-blue-500`) with no brand.css classes
- Dashboard has ~75 inline styles, `UNDERSCORE_CASE` labels, hardcoded `#4CAF50` colors, `onmouseover` handlers
- Error page uses nonexistent `.btn-primary`/`.btn-secondary` classes

**Desired outcome:**
Every template uses the brand.css design system consistently. Zero custom `<style>` blocks in production templates. All forms use `.input-brand`, all buttons use `.btn-brand`, all headings use Inter.

## Appetite

**Size:** Large

**Team:** Solo dev + PM (Tom reviews visual output)

**Interactions:**
- PM check-ins: 1-2 (visual review of MCP pages and auth pages)
- Review rounds: 1 (PR review before merge)

## Prerequisites

No prerequisites — this work has no external dependencies. PR #104 is already merged.

## Solution

### Key Elements

- **CSS foundation fix**: Change `.nav-brand a` from monospace to Inter; add reusable MCP page classes
- **Template rewrites**: Replace inline styles, `<style>` blocks, and generic Tailwind with brand.css classes
- **Body class hook**: Add `{% block body_class %}` to base.html so MCP pages can trigger layout overrides

### Flow

**Nav links** → Fix CSS rule → All pages instantly get Inter nav links

**MCP pages** → Delete `<style>` block → Use `.mcp-header`, `.brand-code`, `.divider-technical`, `.card-technical` → Clean product pages

**Auth pages** → Replace `bg-slate-*` buttons with `.btn-brand` → Replace generic inputs with `.input-brand` → Brand-consistent login/settings

**Dashboard** → Replace UNDERSCORE_CASE + inline styles → Use `.server-card`, `.spec-table-inline`, `.status-indicator` → Clean dashboard

### Technical Approach

- Use existing brand.css classes wherever possible (most are underutilized)
- Add 4-5 new classes to brand.css for MCP-specific patterns (`.mcp-container`, `.mcp-header`, `.install-note`, `.details-accordion`)
- Tailwind utilities for layout (grid, flex, spacing); brand.css for semantic components
- Replace generic Tailwind colors (`slate-600`, `blue-500`, `gray-*`) with brand CSS variables

## Rabbit Holes

- **Rewriting form components** (`components/forms/*.html`) — These are shared, low-traffic, and functional. Leave for a separate effort.
- **Admin dashboard templates** — Internal-only, low priority. Don't touch.
- **Custom modal/toast `<style>` blocks** — These are functional animation styles, not design-system violations. Leave as-is.
- **Perfecting `examples.html`** — It's a reference page with intentional inline styles for demos. Don't clean it up.

## Risks

### Risk 1: Nav font change affects all pages
**Impact:** Every page's nav links change from monospace to Inter simultaneously.
**Mitigation:** This is intentional — the design pivot already moved headings to Inter. Nav links should follow.

### Risk 2: MCP page layout breaks after `<style>` removal
**Impact:** MCP product pages could lose their full-width layout.
**Mitigation:** Task 1 adds `.mcp-container` and layout override classes to brand.css before any template changes.

### Risk 3: `.input-brand` uses monospace font
**Impact:** Email/name form fields rendering in monospace may feel wrong to users.
**Mitigation:** Acceptable for the brand's technical aesthetic. If feedback is negative, add `.input-brand-sans` variant later.

## No-Gos (Out of Scope)

- Form component templates (`components/forms/*.html`) — separate effort
- Admin templates (`admin/dashboard/*.html`) — internal, low priority
- Modal/toast animation styles — functional, not brand violations
- Drugs app templates — functional, rarely accessed
- Teams templates — functional, minimal traffic
- New feature work — this is purely applying existing design system

## Update System

No update system changes required — this is a frontend-only change within the web app.

## Agent Integration

No agent integration required — this is a template/CSS refactoring effort.

## Documentation

### Feature Documentation
- [ ] Update `docs/BRANDING_BRIEF.md` with any new classes added to brand.css
- [ ] Living style guide at `/design-elements/` already covers most classes

### Inline Documentation
- [ ] No new code comments needed — using existing CSS classes

## Success Criteria

- [ ] `.nav-brand a` uses Inter (font-sans), not monospace
- [ ] Zero `<style>` blocks in production templates (examples.html exempt)
- [ ] Zero `onmouseover`/`onmouseout` handlers in any template
- [ ] Zero hardcoded hex colors in templates
- [ ] All form inputs use `.input-brand` or brand-compatible styling
- [ ] All buttons use `.btn-brand` variants, not `bg-slate-*` one-offs
- [ ] UNDERSCORE_CASE labels removed from all production templates
- [ ] MCP pages use brand.css classes
- [ ] Auth pages use brand design system
- [ ] All existing tests pass with no regressions
- [ ] Tests pass
- [ ] Documentation updated

## Team Members

- **Builder (css-foundation)**
  - Name: css-builder
  - Role: Fix brand.css rules and add new utility classes
  - Agent Type: builder
  - Resume: true

- **Builder (nav-footer)**
  - Name: nav-builder
  - Role: Rewrite navbar, account menu, and footer templates
  - Agent Type: builder
  - Resume: true

- **Builder (mcp-pages)**
  - Name: mcp-builder
  - Role: Rewrite both MCP product page templates
  - Agent Type: builder
  - Resume: true

- **Builder (dashboard)**
  - Name: dashboard-builder
  - Role: Rewrite authenticated dashboard template
  - Agent Type: builder
  - Resume: true

- **Builder (auth-pages)**
  - Name: auth-builder
  - Role: Rewrite login, settings, and password flow templates
  - Agent Type: builder
  - Resume: true

- **Builder (error-page)**
  - Name: error-builder
  - Role: Rewrite error page template
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Verify all success criteria, run tests, grep for remaining issues
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix brand.css nav rule + add utility classes
- **Task ID**: build-css-foundation
- **Depends On**: none
- **Assigned To**: css-builder
- **Agent Type**: builder
- **Parallel**: false
- In `brand.css`, change `.nav-brand a` from `font-family: var(--font-mono)` to `font-family: var(--font-sans)`. Keep font-size, weight, tracking, uppercase.
- Add `.mcp-container` (max-width: 1000px, auto margins, padding var(--space-2xl) var(--space-lg))
- Add `.mcp-header` + `::before` (2px dark border, white bg, inner inset border pseudo-element)
- Add `.install-note` (0.75rem gray text, warm-gray bg, red left border, 1.6 line-height)
- Add `.details-accordion` and `.details-accordion summary` for `<details>` elements (cream bg, light border, mono summary)
- In `base.html`, add `{% block body_class %}{% endblock %}` to the body tag class attribute
- Rebuild tailwind: `uv run python manage.py tailwind build --force`
- Commit changes

### 2. Rewrite navbar + account menu + footer
- **Task ID**: build-nav-footer
- **Depends On**: build-css-foundation
- **Assigned To**: nav-builder
- **Agent Type**: builder
- **Parallel**: true
- **Navbar** (`layout/nav/navbar.html`): Remove redundant `text-mono` + inline `font-size` from mobile links; replace generic gray Tailwind on mobile button/auth section with brand vars
- **Account menu** (`layout/nav/account_menu.html`): Replace `bg-slate-700`, `focus:ring-slate-500`, `text-gray-700` with brand colors
- **Footer** (`layout/footer.html`): Replace inline mono styling on line 18 with `.text-mono`; clean remaining inline styles
- Commit changes

### 3. Rewrite MCP pages
- **Task ID**: build-mcp-pages
- **Depends On**: build-css-foundation
- **Assigned To**: mcp-builder
- **Agent Type**: builder
- **Parallel**: true
- Apply to BOTH `apps/ai/templates/mcp/creative_juices.html` and `cto_tools.html`:
- Remove entire `<style>` block (~190 lines each)
- Add `{% block body_class %}mcp-page{% endblock %}` + minimal layout override style
- Replace local `.header` with `.mcp-header`, `.divider` with `.divider-technical`, `.installation` with `.card-technical`
- Replace inline-styled `<pre>` with `<pre class="brand-code">`
- Replace inline-styled `<details>` with `.details-accordion` class
- Remove UNDERSCORE_CASE labels (`MCP_SERVER_01` → `Server`, `TOOL_01` → `Tool 1`, etc.)
- Headings use Inter (no `font-family: var(--font-mono)` overrides)
- Replace `onmouseover`/`onmouseout` in copy-button script with CSS class hover
- Commit changes

### 4. Rewrite authenticated dashboard
- **Task ID**: build-dashboard
- **Depends On**: build-css-foundation
- **Assigned To**: dashboard-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite `apps/public/templates/pages/home.html`
- Replace `max-width` inline with `.container-brand`
- Hero: Replace monospace h1 `YUDAME<br>AI` with Inter heading
- Replace all UNDERSCORE_CASE labels with descriptive text
- Replace hardcoded `#4CAF50` with `.status-indicator.status-operational`
- Replace `onmouseover`/`onmouseout` with `.footer-link` class
- Replace ~75 inline styles with brand.css + Tailwind
- Use `.spec-table-inline`, `.server-card`, `.card-technical` properly
- Commit changes

### 5. Rewrite auth pages
- **Task ID**: build-auth-pages
- **Depends On**: build-css-foundation
- **Assigned To**: auth-builder
- **Agent Type**: builder
- **Parallel**: true
- **Login** (`account/login.html`): Brand heading, `.input-brand`, `.btn-brand`, brand color vars
- **Settings** (`account/settings.html`): Same pattern, replace `ring-blue-500` focus, `.bg-warm-gray` help section
- **Password templates** (6 files in `account/password/`): Brand headings, inputs, buttons, link colors
- Commit changes

### 6. Rewrite error page
- **Task ID**: build-error-page
- **Depends On**: build-css-foundation
- **Assigned To**: error-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove `<style>` block from `error.html`
- Replace layout with Tailwind utilities
- Replace `.btn-primary`/`.btn-secondary` with `.btn-brand`
- Replace search input with `.input-brand`
- Check `components/common/error_message.html` for stale class references
- Commit changes

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-nav-footer, build-mcp-pages, build-dashboard, build-auth-pages, build-error-page
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Grep for remaining issues: `onmouseover`, `bg-slate-`, `ring-blue-`, `text-slate-`, `#4CAF50`
- Verify zero `<style>` blocks in production templates (examples.html exempt)
- Run unit tests: `DJANGO_SETTINGS_MODULE=settings pytest apps/public/ -v --ignore=apps/public/tests/test_e2e*`
- Black format check on modified Python files
- Report pass/fail on all success criteria

## Validation Commands

- `grep -r 'onmouseover\|onmouseout' apps/public/templates/ apps/ai/templates/` — should return nothing
- `grep -r 'bg-slate-\|ring-blue-\|text-slate-' apps/public/templates/ apps/ai/templates/` — should return nothing
- `grep -r '#4CAF50\|#FFA726' apps/public/templates/ apps/ai/templates/` — should return nothing
- `grep -rn '<style>' apps/public/templates/ apps/ai/templates/ | grep -v examples.html` — should return nothing
- `DJANGO_SETTINGS_MODULE=settings pytest apps/public/ -v --ignore=apps/public/tests/test_e2e*` — all pass
