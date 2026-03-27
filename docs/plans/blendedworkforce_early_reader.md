---
status: Complete
type: feature
appetite: Large
owner: Tom Counsell
created: 2026-03-17
tracking: https://github.com/yudame/cuttlefish/issues/180
last_comment_id:
---

# Blended Workforce: Early Reader Funnel & Rich Content

## Problem

After #179 ships, `blendedworkforce.ai` will have a bare-bones landing page, announcements, and feedback form. That's not enough to convert visitors into early readers or demonstrate the book's thesis. The site needs to do three things: (1) pitch the book compellingly, (2) capture early readers who will shape the final product, and (3) let visitors chat with Valor — the AI employee the book is about — as a live proof-of-concept.

**Current behavior:**
The v1 site (#179) has a generic landing page, a plain announcements list, and a feedback form. No signup funnel, no content previews, no chat, no social proof.

**Desired outcome:**
Visitors land on a content-rich page that pitches the book, previews chapters, and funnels them into an early reader program. Signed-up readers receive draft chapters and can provide feedback. Anyone can chat with Valor about the book's concepts. Social proof builds organically as readers sign up and leave testimonials.

## Prior Art

No prior issues or PRs found related to early reader programs, book sites, or chat features scoped to specific content domains.

Existing infrastructure to build on:
- **Chat system**: Full PydanticAI chat agent with Django models (`ChatSession`, `ChatMessage`), HTMX views, and message polling — see [`apps/ai/agent/chat.py`](https://github.com/yudame/cuttlefish/blob/main/apps/ai/agent/chat.py) and [`apps/ai/views/chat.py`](https://github.com/yudame/cuttlefish/blob/main/apps/ai/views/chat.py)
- **Loops email integration**: Production-ready client with transactional email, event tracking, and debug mode — see [`apps/integration/loops/`](https://github.com/yudame/cuttlefish/tree/main/apps/integration/loops)
- **Magic link auth**: User model already supports `four_digit_login_code` and `get_login_url()` for passwordless login

## Data Flow

### Early Reader Signup
1. **Entry point**: Visitor fills out signup form on landing page
2. **Book app view**: Validates form, creates `EarlyReader` record in DB
3. **Loops integration**: Fires `send_early_reader_welcome_email()` shortcut with reader's name and email
4. **Loops.so**: Delivers welcome email with confirmation, adds to early reader audience for drip campaigns
5. **Output**: Reader sees thank-you confirmation on site; receives welcome email

### Valor Chat
1. **Entry point**: Visitor types message in embedded chat widget on book site
2. **Book chat view**: Creates anonymous `ChatSession` (no user FK), saves `ChatMessage`
3. **PydanticAI agent**: Processes message with book-scoped system prompt (Valor's identity + book content as context). Uses Anthropic Claude, not OpenAI.
4. **Response**: Agent response saved as `ChatMessage`, rendered via HTMX swap
5. **Rate limiting**: Middleware or view-level check — max N messages per session per hour for anonymous users

### Draft Chapter Access
1. **Entry point**: Early reader clicks magic link in email
2. **Auth**: Magic link logs reader in (creates User if needed, links to EarlyReader)
3. **Chapter view**: Authenticated view renders `DraftChapter` markdown as HTML
4. **Output**: Reader sees formatted chapter with book typography

## Architectural Impact

- **New dependencies**: `markdown` Python package for rendering draft chapters. No other new deps.
- **Interface changes**: New Loops shortcut function. New PydanticAI agent configuration (book-scoped system prompt using Anthropic Claude).
- **Coupling**: Book app depends on `apps.integration.loops` (email) and `apps.ai` (chat). Both are existing, stable interfaces.
- **Data ownership**: Book app owns `EarlyReader`, `Testimonial`, `DraftChapter`. Reuses `ChatSession`/`ChatMessage` from AI app.
- **Reversibility**: Fully reversible — all new models and views, no modifications to existing interfaces.

## Appetite

**Size:** Large

**Team:** Solo dev, PM (for copy review)

**Interactions:**
- PM check-ins: 2 (landing page copy, early reader email copy)
- Review rounds: 1-2 (visual review, chat behavior review)

This is Large because it spans 6 feature areas, but each is individually Small/Medium. The work is parallelizable.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| #179 shipped | `python -c "from apps.book.models import Announcement; print('OK')"` | Base book app must exist |
| `ANTHROPIC_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('ANTHROPIC_API_KEY')"` | Valor chat agent |
| `LOOPS_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('LOOPS_API_KEY')"` | Early reader welcome email |
| `markdown` package | `python -c "import markdown; print('OK')"` | Draft chapter rendering |

## Solution

### Key Elements

- **Rich landing page**: Structured sections (hero, pitch, author, chapter previews, companion repo, series roadmap) with book content from the work vault
- **EarlyReader model + signup form**: Captures name, email, company, role, optional research question; syncs to Loops
- **Valor chat widget**: Embedded PydanticAI chat using Anthropic Claude with a book-scoped system prompt, scoped to onboarding concepts and the blended workforce framework
- **Social proof section**: Testimonials model, reader count, company logos — renders when data exists, hides gracefully when empty
- **Draft chapter reader**: Magic link auth, markdown-to-HTML rendering with book typography
- **Loops email integration**: Welcome email for signups, chapter notification emails for draft distribution

### Flow

**Landing page** → Read pitch, author bio, chapter previews → **Sign up as early reader** → Welcome email → **Read draft chapters** (magic link) → **Provide feedback** (existing form) → **Chat with Valor** → Ask about book concepts

### Technical Approach

- Landing page content hardcoded in template (not CMS) — the content is known and stable
- Valor chat reuses existing `ChatSession`/`ChatMessage` models but with a new PydanticAI agent config: custom system prompt with Valor's identity, book content as context, Anthropic Claude as model (not OpenAI)
- Chat is embedded on the landing page as a collapsible widget, not a separate page
- Early reader signup creates both an `EarlyReader` record and optionally a Django `User` (for magic link access to drafts later)
- Magic link auth uses existing `User.get_login_url()` infrastructure
- Draft chapters stored as model instances with markdown body, rendered to HTML at view time
- Social proof section uses `{% if testimonials %}` — zero-state is simply not rendering the section

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] Chat agent: handle missing `ANTHROPIC_API_KEY` gracefully — show "Chat unavailable" message instead of 500
- [x] Loops integration: handle missing `LOOPS_API_KEY` — log warning, still create EarlyReader record, skip email
- [x] Rate limiting: return friendly message when chat limit hit, not an error page

### Empty/Invalid Input Handling
- [x] Signup form: validate email format, required fields; show inline errors
- [x] Chat: empty message input does nothing (frontend validation)
- [x] Draft chapters: empty `body_markdown` renders as empty string (no special empty state)

### Error State Rendering
- [x] Chat widget: show "Something went wrong" with retry option on agent failure
- [x] Signup: show success even if Loops email fails (record is still saved)

## Test Impact

No existing tests affected — this is a greenfield feature building on top of #179's new app. Existing chat and Loops tests remain unchanged since we're adding new agent configs and shortcut functions, not modifying existing ones.

## Rabbit Holes

- **CMS for landing page content**: The content is known, static, and changes rarely. Hardcoded templates are simpler and faster than building a CMS or using Django Wagtail.
- **Real-time chat (WebSockets)**: The existing HTMX polling pattern works fine for the expected traffic. WebSockets add deployment complexity (sticky sessions on Render) for minimal UX gain.
- **Full user registration flow**: Early readers don't need passwords, profiles, or dashboards. Magic link auth is sufficient. Don't build a registration system.
- **Payment/purchase flow**: v1 uses external buying links (Amazon, Gumroad). Don't integrate Stripe for book sales yet.
- **Custom email templates in Loops**: Start with Loops' default templates. Custom branded templates are a polish task, not a launch blocker.
- **Chat history persistence for anonymous users**: Session-based is fine. Don't build "resume conversation" for users who haven't signed up.

## Risks

### Risk 1: Chat abuse / prompt injection
**Impact:** Anonymous users try to misuse Valor chat for non-book purposes, or inject prompts to make Valor say inappropriate things.
**Mitigation:** Strict system prompt scoping ("You are Valor Engels. You only discuss topics related to the Blended Workforce book, AI employee management, and onboarding frameworks. Politely decline other topics."). Rate limiting (N messages/hour per session). No tool access — chat-only, no code execution.

### Risk 2: Loops template IDs not configured
**Impact:** Signup works but welcome email never sends; early readers get no confirmation.
**Mitigation:** Create Loops templates and store IDs in env vars before launch. Graceful degradation: signup still creates record if email fails. Add a manual "resend welcome" action in Django admin.

### Risk 3: Draft chapter content not ready
**Impact:** Early readers sign up but there's nothing to read.
**Mitigation:** Launch signup form first, clearly stating "Vol. 1 drafts coming [date]." Don't promise immediate access. The `DraftChapter` model supports `published_at` — only published chapters are visible.

## Race Conditions

No race conditions identified — all operations are synchronous request/response. Chat polling uses DB reads with no concurrent write conflicts. Early reader signup is a simple create operation with no uniqueness race (email uniqueness enforced at DB level).

## No-Gos (Out of Scope)

- Payment/purchase processing (external links only)
- User profiles or dashboards
- Newsletter archive / blog
- Case studies from early adopters
- Workshop or speaking page
- Custom Loops email template design
- Chat conversation history across sessions for anonymous users
- Mobile app / PWA
- Analytics dashboard for signup metrics (use Django admin + Loops analytics)

## Update System

No update system changes required. The `markdown` package needs to be added via `uv add markdown` — standard dependency addition handled by the existing build process.

## Agent Integration

No agent integration required — this is a public-facing website. The Valor chat feature uses PydanticAI directly (not via MCP), similar to the existing chat implementation in `apps/ai/`.

## Documentation

### Feature Documentation
- [x] Update `docs/features/blendedworkforce-site.md` (created by #179) with early reader funnel, chat, and content features
- [x] Add entry to `docs/features/README.md` index table if not already present

### Inline Documentation
- [x] Document Valor chat system prompt rationale (why scoped, what's allowed/blocked)
- [x] Document early reader → Loops sync pattern

## Success Criteria

- [x] Landing page has all structured sections: hero with hook, pitch (who/what/why/author), chapter previews with sample excerpt, companion repo link, series roadmap, repeated CTA
- [x] Early reader signup form captures name, email, company, role, optional question
- [x] Signup creates `EarlyReader` record and sends welcome email via Loops
- [x] Valor chat widget is functional on landing page, scoped to book content
- [x] Chat uses Anthropic Claude with Valor's identity and book-scoped system prompt
- [x] Chat is rate-limited for anonymous users
- [x] Social proof section renders testimonials when they exist, is hidden when empty
- [x] Draft chapter reader works with magic link auth
- [x] `DraftChapter` model renders markdown to HTML with book typography
- [x] All features work without authentication (except draft chapters)
- [x] Mobile responsive across all sections
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (models)**
  - Name: models-builder
  - Role: Create EarlyReader, Testimonial, DraftChapter models + admin registration
  - Agent Type: builder
  - Resume: true

- **Builder (chat)**
  - Name: chat-builder
  - Role: Valor chat agent config, book-scoped system prompt, chat views for book app
  - Agent Type: builder
  - Resume: true

- **Builder (email)**
  - Name: email-builder
  - Role: Loops integration for early reader welcome email, signup view
  - Agent Type: builder
  - Resume: true

- **Builder (templates)**
  - Name: template-builder
  - Role: Rich landing page, signup form, chat widget, chapter reader, social proof section
  - Agent Type: designer
  - Resume: true

- **Validator (funnel)**
  - Name: funnel-validator
  - Role: End-to-end validation of signup → email → chapter access flow
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. New Models
- **Task ID**: build-models
- **Depends On**: #179 complete (apps/book/ exists)
- **Validates**: `apps/book/tests/test_models.py` (extend)
- **Assigned To**: models-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `EarlyReader` model: name, email (unique), company, role (choices: CEO/Founder/Head of/Other), research_question (optional), created_at, is_confirmed (bool)
- Create `Testimonial` model: quote, author_name, company, role, is_featured (bool), created_at
- Create `DraftChapter` model: title, volume (int), chapter_number (int), body_markdown (TextField), published_at (nullable), created_at
- Register all in Django admin with list_display, list_filter, search_fields
- Add `markdown` dependency: `uv add markdown`
- Run makemigrations + migrate
- Write model tests (creation, str, ordering, email uniqueness constraint)

### 2. Loops Email Integration
- **Task ID**: build-email
- **Depends On**: build-models
- **Validates**: `apps/integration/loops/tests/test_early_reader.py` (create)
- **Assigned To**: email-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `send_early_reader_welcome_email()` in `apps/integration/loops/shortcuts.py` following existing pattern
- Accept name, email, and optional research_question as params
- Use placeholder Loops template ID (configurable via env var `LOOPS_EARLY_READER_TEMPLATE_ID`)
- Handle missing API key gracefully (log warning, don't raise)
- Write test with mocked Loops client

### 3. Early Reader Signup View
- **Task ID**: build-signup
- **Depends On**: build-models, build-email
- **Validates**: `apps/book/tests/test_views.py` (create)
- **Assigned To**: models-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `EarlyReaderSignupView` in `apps/book/views/`
- Django form with validation (email format, required fields)
- On valid submit: create EarlyReader, call Loops shortcut, redirect to success page
- Handle duplicate email: show friendly "You're already signed up!" message
- Add URL patterns: `/signup/`, `/signup/success/`
- Write view tests (valid submit, duplicate email, missing fields)

### 4. Valor Chat Agent
- **Task ID**: build-chat
- **Depends On**: #179 complete
- **Validates**: `apps/book/tests/test_chat.py` (create)
- **Assigned To**: chat-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-models)
- Create `apps/book/chat.py` with book-scoped PydanticAI agent:
  - Model: Anthropic Claude (not OpenAI)
  - System prompt: Valor's identity + book content context + scope constraints
  - No tools (chat only, no code execution)
- Create `ValorChatView` (HTMX) and `ValorSendMessageView` in `apps/book/views/`
- Anonymous `ChatSession` support (no user FK required — already nullable)
- Rate limiting: track message count in session, cap at 20 messages/hour
- Add URL patterns: `/chat/`, `/chat/send/`
- Write tests: agent responds within scope, rate limiting works, anonymous sessions work

### 5. Rich Landing Page Template
- **Task ID**: build-landing
- **Depends On**: build-models, build-chat, build-signup
- **Validates**: manual visual review
- **Assigned To**: template-builder
- **Agent Type**: designer
- **Parallel**: false
- Replace v1 landing page with structured sections:
  - **Hero**: "Blended Workforce 2026" title, subtitle ("The CEO's Guide to Training, Managing, and Scaling Your AI + Human Team"), hook line, email signup CTA, hero visual
  - **The Pitch**: 4 blocks — who it's for, what it is, why now, why this author
  - **About the Author**: Tom Counsell bio, Valor story (2-3 sentences), photo placeholder, link to tomcounsell.com
  - **Chapter Previews**: Vol. 1 TOC (5 chapters with one-line descriptions), sample excerpt (Ch. 1 opening paragraph), volume timeline
  - **Chat with Valor**: Embedded chat widget with framing "Meet the AI employee this book is about"
  - **Companion Repo**: Link to `tomcounsell/ai-employee`, credibility framing
  - **Social Proof**: `{% if testimonials %}` section with quotes, hidden when empty
  - **Series Roadmap**: Visual timeline (Vol. 1 → Vol. 2 → Vol. 3)
  - **Footer**: Repeated email signup CTA, links, tagline
- All content hardcoded from work vault sources
- Mobile responsive, editorial typography

### 6. Draft Chapter Reader
- **Task ID**: build-chapters
- **Depends On**: build-models
- **Assigned To**: email-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-landing)
- Create `ChapterListView` (authenticated) showing published draft chapters
- Create `ChapterDetailView` rendering `body_markdown` to HTML via `markdown` package
- Magic link: generate via `User.get_login_url()`, send in chapter notification email
- Add URL patterns: `/drafts/`, `/drafts/<int:volume>/<int:chapter>/`
- Simple auth gate: `LoginRequiredMixin` — magic link handles the login
- Write view tests: authenticated access works, unauthenticated redirects

### 7. Signup Form Template + Chat Widget Template
- **Task ID**: build-form-templates
- **Depends On**: build-signup, build-chat
- **Assigned To**: template-builder
- **Agent Type**: designer
- **Parallel**: true (parallel with build-landing)
- Create `book/signup.html`: form with name, email, company, role dropdown, optional research question textarea
- Create `book/signup_success.html`: thank-you message with "what happens next" info
- Create `book/chat_widget.html`: collapsible chat panel, message list, input field, HTMX integration
- Create `book/drafts/list.html`: chapter list for authenticated readers
- Create `book/drafts/detail.html`: rendered markdown chapter with book typography
- Follow book site design system (from base.html established in #179)

### 8. Integration Validation
- **Task ID**: validate-funnel
- **Depends On**: build-landing, build-form-templates, build-chapters
- **Assigned To**: funnel-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify landing page renders all sections correctly
- Verify signup form creates EarlyReader record
- Verify duplicate email shows friendly message
- Verify Valor chat responds within scope (test with book question and off-topic question)
- Verify rate limiting kicks in after threshold
- Verify social proof section is hidden when no testimonials exist
- Verify draft chapter renders markdown correctly
- Verify magic link auth flow works end-to-end
- Run full test suite

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-funnel
- **Assigned To**: template-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/blendedworkforce-site.md` with early reader funnel docs
- Document Valor chat system prompt and scoping rationale
- Document Loops integration for early reader emails

### 10. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: funnel-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `DJANGO_SETTINGS_MODULE=settings pytest apps/book/ -x -q` | exit code 0 |
| Loops tests pass | `DJANGO_SETTINGS_MODULE=settings pytest apps/integration/loops/tests/ -x -q` | exit code 0 |
| Migrations clean | `uv run python manage.py makemigrations --check --dry-run` | exit code 0 |
| Format clean | `uv run black --check apps/book/` | exit code 0 |
| Markdown installed | `python -c "import markdown"` | exit code 0 |
| EarlyReader model exists | `uv run python manage.py shell -c "from apps.book.models import EarlyReader; print('OK')"` | output contains OK |
| Chat agent configured | `uv run python manage.py shell -c "from apps.book.chat import valor_chat_agent; print('OK')"` | output contains OK |

---

## Open Questions

1. **Valor chat model choice** — The existing chat system uses OpenAI GPT-5.2. Valor should use Anthropic Claude (since Valor is built on Claude). Should we use `claude-sonnet-4-6` (fast, cheaper) or `claude-opus-4-6` (more capable, Valor's "real" model)? Recommend Sonnet for chat — fast responses matter more than depth here.

2. **Early reader → User conversion** — When an early reader signs up, should we immediately create a Django `User` account (for magic link access to drafts later), or defer user creation until the first draft chapter is published? Creating upfront is simpler but adds users who may never read drafts.

3. **Chat widget placement** — Embedded on the landing page as a collapsible panel (always visible), or on a separate `/chat/` page? The TODO.md suggests embedded, but a long landing page with an embedded chat might be cluttered on mobile.

4. **Landing page copy tone** — The work vault has both a peer-to-peer CEO tone and a more marketing-forward tone. Which direction for the landing page? The book itself uses peer-to-peer, but landing pages often need more punch.

5. **Hero visual** — The TODO.md mentions "blended org chart concept" as a hero visual. Should we commission/generate this, or use a clean text-only hero for v1 and add imagery later?
