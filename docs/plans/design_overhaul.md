---
status: Ready
type: feature
appetite: Large
owner: Tom
created: 2026-02-22
tracking: https://github.com/yudame/cuttlefish/issues/98
---

# Design Overhaul: Rebrand to Yudame + Fix UX Weaknesses

## Problem

The site was designed as an MCP developer tool site ("Yudame AI / MCP Server Hub"). The monospace-heavy aesthetic, `UNDERSCORE_CASE` labeling, and technical spec-box layouts all serve that framing. But the platform actually hosts multiple apps — MCP servers, a podcast platform, medication tracking, and more. The niche tech-tool aesthetic doesn't represent the breadth of what Yudame is.

A UX critique confirmed seven structural weaknesses (documented in `docs/BRANDING_BRIEF.md` § Known Weaknesses & Guardrails) that compound this problem:

**Current behavior:**
- Brand name is "Yudame AI" with an "MCP Server Hub" headline — signals a single-purpose dev tool
- Monospace font (IBM Plex Mono) is used for headings, descriptions, and prose — not just code and labels. This pigeonholes the brand as "developer aesthetic"
- `UNDERSCORE_CASE` labeling (`BENEFIT_01`, `MCP_SERVER_01`, `PROTOCOL_OVERVIEW`) makes every page feel like a terminal interface
- Homepage hero is an inert title card with no CTA
- Benefits are labels without supporting sentences
- Every section has equal visual weight — no hierarchy
- Green status dots have no meaning
- Footer columns have no headers
- Podcast pages use orange accents instead of brand colors
- Three unfinished Tailwind Plus boilerplate templates with lorem ipsum and stock images
- Heavy inline `style=` attributes throughout

**Desired outcome:**
The site presents as **Yudame** — a multi-product platform with a distinctive black-on-cream identity. Inter is the primary voice; monospace is reserved for code and technical metadata. Each product area (MCP, Podcast, etc.) gets clear representation. Pages have purposeful hierarchy and CTAs. The architectural precision of the brand remains, but it serves a broader audience than just developers.

## Appetite

**Size:** Large

**Team:** Solo dev + PM. 2–3 check-ins to align on direction, 2 review rounds.

**Interactions:**
- PM check-ins: 2–3 (homepage redesign direction, typography pivot, final review)
- Review rounds: 2 (CSS/foundation pass, template integration pass)

## Brand Assets

Available at `/Users/tomcounsell/Dropbox (Personal)/Work/Yudame/Corp Branding/`:

| File | Usage |
|------|-------|
| `logo-brand-trans.png` | Primary logo (transparent background) — navbar, hero |
| `logo-brand.png` | Primary logo (white background) — fallback |
| `logo-favicon.png` | Yellow chevron mark — favicon (already referenced in base.html) |
| `logo-square.png` | Square format — social/og:image |
| `logo-circle.png` | Circle format — profile/avatar contexts |
| `logo.ai` | Source vector — for SVG extraction if needed |

The logo is a geometric sans-serif wordmark ("Yudame") with a gold/yellow chevron "A" mark. The font is rounded, warm, and decidedly not monospace — this confirms the typography pivot direction. Copy needed assets to `static/assets/` during build.

## Prerequisites

No prerequisites — this is CSS, templates, and copy only.

## Solution

### Key Elements

- **Rebrand to Yudame**: Replace "Yudame AI" and "MCP Server Hub" with just "Yudame" throughout navbar, footer, hero, and page titles. The platform identity is the company, not a product category.
- **Typography pivot**: Inter becomes the dominant font for all headings, descriptions, and copy. Monospace (IBM Plex Mono) is strictly for: code blocks, technical metadata fields, and small data labels. Headings like `h1`, `h2`, `h3` switch from monospace uppercase to Inter.
- **Scale back UNDERSCORE_CASE**: Remove `BENEFIT_01`, `PROTOCOL_OVERVIEW`, `AVAILABLE_SERVERS` section labels. Replace with plain English section headers or remove entirely where they add no information. Keep the convention available in brand.css for pages that need it (MCP docs), but it's no longer the default voice.
- **Homepage led by Podcast**: The podcast platform is the primary product — it leads the homepage with a prominent section featuring recent episodes and a clear "Listen Now" CTA. MCP servers are a secondary section below. The drugs/meds app is not publicly available yet and does not appear.
- **Hero with CTA**: Replace the inert spec-box hero with a clear statement of what Yudame is + a CTA that directs visitors to the podcast (primary) or MCP servers (secondary).
- **Visual hierarchy**: Two focal points per page (hero + primary content). Use size, spacing, and the red accent to differentiate importance.
- **Component cleanup**: Extract inline styles into brand.css classes, fix broken base.html footer tag, remove boilerplate templates.
- **Podcast brand alignment**: Replace orange accents with brand red.

### Flow

**Visitor arrives at homepage** → Reads what Yudame is → Sees featured podcast episodes (primary product) → Clicks "Listen Now" or browses episodes → Scrolls to discover MCP tools (secondary) → Clicks into a server's docs or install guide

### Technical Approach

**Phase A — CSS & Typography Foundation**

1. Update `brand.css`:
   - Change `h1, h2, h3` from monospace uppercase to Inter (keep `font-weight: 600`, drop `text-transform: uppercase` and `letter-spacing: 0.05em` defaults)
   - Add a `.text-technical-label` class for opt-in UNDERSCORE_CASE labeling (what `.label` does today, but explicitly opt-in rather than default)
   - Add `.section-hero` class for dominant hero sections
   - Add `.product-card` class for multi-product homepage cards
   - Add `.footer-section-header` for footer column labels
   - Extract common inline patterns into reusable classes
2. Update `source.css` Tailwind theme tokens to match the typography pivot
3. Fix `base.html` broken footer tag syntax

**Phase B — Template Overhaul**

Templates modified in priority order:

1. **`layout/nav/navbar.html`** — Replace monospace "YUDAME AI" text with the actual logo image (`logo-brand-trans.png`, sized to ~24–32px height)

2. **`home.html`** (complete restructure)
   - Hero: Inter headline describing Yudame. Supporting sentence. Primary CTA links to podcast.
   - **Featured Podcast section** (dominant): Show recent episodes from the podcast platform with cover art, titles, and "Listen Now" links. This is the primary content visitors see after the hero — it's the product with the most public value.
   - **MCP Servers section** (secondary): Compact cards for Creative Juices and CTO Tools with brief descriptions. Keep install guide but visually subordinate to podcast. Remove the "What is MCP?" explainer from the homepage (it belongs on the MCP docs page).
   - Remove `technical-spec-box` hero pattern — too MCP-specific
   - Remove `UNDERSCORE_CASE` section labels (`AVAILABLE_SERVERS`, `PROTOCOL_OVERVIEW`, etc.)
   - Status dots: add text labels or remove
   - **Do not show the drugs/meds app** — it's not publicly available yet

3. **`layout/footer.html`**
   - "YUDAME AI" → "Yudame"
   - Add column headers (Products, Resources, Legal)

4. **`podcast/podcast_list.html`**
   - Add page intro sentence
   - Ensure Inter typography throughout

5. **`podcast/podcast_detail.html`**
   - Replace orange accents with brand red
   - Typography cleanup

6. **`podcast/episode_detail.html`**
   - Replace orange accents with brand red
   - Ensure prose uses Inter

7. **Boilerplate cleanup**:
   - Delete `pages/landing.html` (generic Tailwind Plus, not branded)
   - Delete `pages/home.html` (duplicate Tailwind Plus boilerplate with lorem ipsum)
   - Delete `components/layout/landing_page.html` (unused Tailwind Plus template)
   - Delete `components/layout/navbar.html` and `components/layout/footer.html` (unused Tailwind Plus templates — real ones are in `layout/`)
   - Verify no views or URLs reference these before deleting

8. **`base.html`**
   - Update default title from "Django Project Template" to "Yudame"
   - Fix unclosed footer tag

## Rabbit Holes

- **Don't redesign the podcast audio player** — native `<audio>` works fine
- **Don't add animations or transitions beyond hover states**
- **Don't introduce dark mode**
- **Don't redesign admin/staff/account templates** — public pages only
- **Don't build a formal component library** — brand.css with well-named classes is enough
- **Don't create a logo or wordmark** — text-based "Yudame" is fine for now
- **Don't redesign the MCP documentation pages** (e.g. `/mcp/creative-juices/`) — those can keep the technical labeling style since their audience is developers
- **Don't rethink information architecture or URL structure** — keep existing routes

## Risks

### Risk 1: Losing the distinctive identity
Removing monospace and UNDERSCORE_CASE from the default might make the site look generic. The technical precision was what set it apart.
**Impact:** Site becomes another bland Inter-on-cream layout.
**Mitigation:** Keep the architectural precision through layout, grid, spacing, and the red annotation system. The identity shifts from "looks like a terminal" to "looks like an architect's portfolio" — still distinctive, just broader.

### Risk 2: Homepage scope creep
Restructuring the homepage around the podcast could pull in podcast feature work (player redesign, subscription flows, etc.).
**Impact:** Appetite overrun.
**Mitigation:** The homepage features podcast content via links to existing podcast pages. No new podcast features are built — just display of recent episodes with links to the existing detail pages.

### Risk 3: Template breakage from boilerplate deletion
**Impact:** 500 errors if views reference deleted templates.
**Mitigation:** Grep all view files and URL configs for template references before deleting.

### Risk 4: Typography cascade breaks
Changing the global `h1, h2, h3` styles affects every page, including ones not explicitly overhauled.
**Impact:** Visual regressions on pages outside scope.
**Mitigation:** The change is from monospace → Inter, which is generally more readable, not less. Verify non-target pages (account, teams, admin) still look acceptable after the global change.

## No-Gos (Out of Scope)

- Admin dashboard templates
- Account/login/password templates
- Team management templates
- Drug dashboard templates (not publicly available; will get their own design pass later)
- Swagger UI template
- New pages or features
- SEO/social meta tags
- Logo/wordmark design
- URL restructuring
- MCP documentation pages (keep technical style for dev audience)

## Update System

No update system changes required — frontend only.

## Agent Integration

No agent integration required — UI only.

## Documentation

### Feature Documentation
- [ ] Rewrite `docs/BRANDING_BRIEF.md` to reflect the rebrand (Yudame, not Yudame AI)
- [ ] Update typography section to document the Inter-first approach
- [ ] Document new CSS classes added in Phase A
- [ ] Update "The Vibe" section to reflect multi-product platform identity

### Inline Documentation
- [ ] Update template header comments to reflect new structure

## Success Criteria

- [ ] Navbar uses actual Yudame logo image (not monospace text)
- [ ] "Yudame AI" renamed to "Yudame" in footer, hero, and base title
- [ ] Homepage leads with featured podcast content, MCP servers are secondary
- [ ] Drugs/meds app does not appear on the homepage
- [ ] Homepage hero has a visible CTA button
- [ ] `h1, h2, h3` elements use Inter, not monospace (globally in brand.css)
- [ ] No prose or value-prop text uses monospace font
- [ ] `UNDERSCORE_CASE` section labels removed from homepage (available as opt-in class)
- [ ] Benefits/features have supporting sentences, not just labels
- [ ] Two visual focal points per page (hero + primary content dominant)
- [ ] Status dots have text labels or are removed
- [ ] Footer columns have section headers
- [ ] Podcast pages use brand red accent, not orange
- [ ] Boilerplate templates deleted (after verifying no route references)
- [ ] `base.html` footer tag and default title fixed
- [ ] BRANDING_BRIEF.md updated to reflect rebrand
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (CSS & foundation)**
  - Name: css-builder
  - Role: Typography pivot in brand.css/source.css, new component classes, base.html fixes
  - Agent Type: designer
  - Resume: true

- **Builder (homepage)**
  - Name: homepage-builder
  - Role: Restructure home.html as multi-product hub with hero CTA, product cards, Inter typography
  - Agent Type: designer
  - Resume: true

- **Builder (podcast pages)**
  - Name: podcast-builder
  - Role: Replace orange with red, typography cleanup, add intro copy
  - Agent Type: designer
  - Resume: true

- **Builder (nav, footer, cleanup)**
  - Name: chrome-builder
  - Role: Rename to Yudame in nav/footer, add footer headers, delete boilerplate, fix base.html
  - Agent Type: designer
  - Resume: true

- **Validator (visual)**
  - Name: visual-validator
  - Role: Verify rendering, typography rules, hierarchy, no regressions
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Rewrite BRANDING_BRIEF.md for rebrand, document new CSS classes
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Typography pivot and new CSS classes
- **Task ID**: build-css
- **Depends On**: none
- **Assigned To**: css-builder
- **Agent Type**: designer
- **Parallel**: true
- Change `h1, h2, h3` default from monospace to Inter in brand.css
- Add `.text-technical-label` as opt-in for UNDERSCORE_CASE
- Add `.section-hero`, `.product-card`, `.footer-section-header` classes
- Extract common inline patterns into reusable classes
- Update source.css Tailwind tokens for typography alignment

### 2. Rename and fix site chrome
- **Task ID**: build-chrome
- **Depends On**: none
- **Assigned To**: chrome-builder
- **Agent Type**: designer
- **Parallel**: true
- Replace monospace "YUDAME AI" text with logo image in navbar
- "YUDAME AI" → "Yudame" text in footer
- Copy brand assets from Dropbox to `static/assets/`
- Fix `base.html` footer tag syntax and default title
- Add footer column headers
- Delete boilerplate templates (after checking view references)

### 3. Restructure homepage
- **Task ID**: build-homepage
- **Depends On**: build-css, build-chrome
- **Assigned To**: homepage-builder
- **Agent Type**: designer
- **Parallel**: false
- New hero: Inter headline, platform description, CTA linking to podcast
- Featured podcast section: update `HomeView` to query recent published episodes, pass to template context. Display with cover art, titles, links to detail pages.
- MCP servers as secondary section with compact cards
- Remove "What is MCP?" explainer from homepage (move to MCP docs if needed)
- Remove UNDERSCORE_CASE section labels
- Add text labels to status dots or remove them
- Do not show drugs/meds app
- Replace inline styles with brand.css classes

### 4. Overhaul podcast templates
- **Task ID**: build-podcast
- **Depends On**: build-css
- **Assigned To**: podcast-builder
- **Agent Type**: designer
- **Parallel**: true (parallel with build-homepage)
- `podcast_list.html`: add intro text, Inter typography
- `podcast_detail.html`: orange → red accents, typography
- `episode_detail.html`: orange → red accents, Inter for prose

### 5. Visual validation
- **Task ID**: validate-visual
- **Depends On**: build-homepage, build-podcast, build-chrome
- **Assigned To**: visual-validator
- **Agent Type**: validator
- **Parallel**: false
- Check every modified template renders correctly
- Verify no monospace in prose
- Verify hero CTA works
- Verify Yudame naming throughout
- Verify podcast red accents
- Verify footer headers
- Check non-target pages (account, teams) for typography cascade regressions
- Flag any issues

### 6. Update branding brief and documentation
- **Task ID**: document-rebrand
- **Depends On**: validate-visual
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Rewrite BRANDING_BRIEF.md: "Yudame" not "Yudame AI", Inter-first typography, multi-product framing
- Document new CSS classes
- Update template header comments

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-rebrand
- **Assigned To**: visual-validator
- **Agent Type**: validator
- **Parallel**: false
- Run test suite
- Verify all success criteria met
- Generate final report

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/public/ -v` — template rendering
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_views.py -v` — podcast views
- `grep -r 'Yudame AI' apps/public/templates/` — verify rename complete
- `grep -r 'text-orange' apps/public/templates/podcast/` — verify no orange accents
- `grep -r 'style="' apps/public/templates/home.html | wc -l` — track inline style reduction

---

## Resolved Questions

1. **Gold vs. red accent**: Gold stays logo-only. Red remains the sole UI accent. A logo variation matching the site palette may come later but is out of scope.

2. **Homepage podcast section**: Use live data — update `HomeView` to query recent published episodes. This keeps the homepage fresh without manual updates.

3. **Boilerplate deletion**: Yes, delete all five Tailwind Plus templates (`pages/landing.html`, `pages/home.html`, `components/layout/landing_page.html`, `components/layout/navbar.html`, `components/layout/footer.html`) after verifying no views reference them.
