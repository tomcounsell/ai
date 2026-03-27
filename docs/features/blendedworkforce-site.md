# Blended Workforce Site (blendedworkforce.ai)

A standalone book website for *Blended Workforce 2026* served from the same Django instance as Cuttlefish using per-request URL routing.

## How It Works

### Domain Routing

`DomainRoutingMiddleware` (in `apps/common/utilities/django/middleware.py`) inspects the `HTTP_HOST` header on every request. When the hostname matches a known book domain (`blendedworkforce.ai` or `www.blendedworkforce.ai`), it sets:

- `request.site_name = "book"`
- `request.urlconf = "apps.book.root_urls"`

This uses Django's built-in [per-request URL configuration](https://docs.djangoproject.com/en/5.1/topics/http/urls/#how-django-processes-a-request) so the URL resolver uses `apps.book.root_urls` instead of the default `ROOT_URLCONF`. All other hostnames fall through to normal Cuttlefish routing.

### URL Structure

`apps/book/root_urls.py` is the domain-level URL config. It includes `apps/book/urls.py` under the `book` namespace, so `{% url 'book:landing' %}` works in templates.

| Path | View | Name |
|------|------|------|
| `/` | `LandingView` | `book:landing` |
| `/announcements/` | `AnnouncementListView` | `book:announcements` |
| `/signup/` | `EarlyReaderSignupView` | `book:signup` |
| `/signup/success/` | `SignupSuccessView` | `book:signup_success` |
| `/chat/` | `ValorChatView` | `book:chat` |
| `/chat/send/` | `ValorSendMessageView` | `book:chat_send` |
| `/chapters/` | `ChapterListView` | `book:chapters` |
| `/chapters/<int:pk>/` | `ChapterDetailView` | `book:chapter_detail` |
| `/health/` | `health_check` | Health check (shared) |

Feedback is collected via an external Google Form (configured via `BOOK_FEEDBACK_FORM_URL` env var).

### Models

| Model | Key Fields | Purpose |
|-------|------------|---------|
| `Announcement` | title, body, published_at | Public updates about the book |
| `EarlyReader` | name, email (unique), company, role, research_question, is_confirmed | Visitors who signed up to read draft chapters |
| `Testimonial` | quote, author_name, company, role, is_featured | Social proof from readers/reviewers |
| `DraftChapter` | title, volume, chapter_number, body_markdown, published_at | Draft chapter content in Markdown |

- Announcements and DraftChapters are only shown when `published_at` is set and in the past.
- `DraftChapter.body_html` property renders Markdown to HTML using the `markdown` package (with `extra`, `codehilite`, and `toc` extensions).
- `EarlyReader.role` uses choices: CEO, Founder, Head of Department, Other.

### Templates

All templates are in `apps/public/templates/book/` and extend `book/base.html`, which is a standalone template with editorial typography (Playfair Display + Inter) and a warm color palette separate from Cuttlefish branding.

| Template | Purpose |
|----------|---------|
| `landing.html` | Rich landing page with hero, pitch, chapter previews, chat, testimonials, series roadmap |
| `signup.html` | Early reader signup form |
| `signup_success.html` | Post-signup thank-you page |
| `chat.html` | Standalone Valor chat page |
| `chapters.html` | List of published draft chapters (login required) |
| `chapter_detail.html` | Single chapter rendered from Markdown (login required) |

## Early Reader Funnel

The site converts visitors into early readers through a signup flow integrated with Loops for email delivery.

### Signup Flow

1. Visitor fills out the form at `/signup/` (name, email, company, role, optional research question)
2. `EarlyReaderSignupView` (a Django `CreateView`) validates and creates an `EarlyReader` record
3. On success, fires `send_early_reader_welcome_email()` from `apps/integration/loops/shortcuts.py`
4. Visitor is redirected to `/signup/success/`
5. If the Loops call fails, the signup still succeeds -- email delivery is fire-and-forget

### Loops Sync Pattern

The `send_early_reader_welcome_email()` function in `apps/integration/loops/shortcuts.py` follows the same pattern as other Loops shortcut functions in the codebase:

- Creates a `LoopsClient` instance (with `debug_mode=True` when `settings.TESTING` is set)
- Calls `loops_client.transactional_email()` with the reader's email and a template ID placeholder (`__loops_early_reader_welcome_id__`)
- Data variables sent to Loops: `name`, `company`, `role` (display value)
- Wraps the entire call in a try/except -- logs the exception but returns `False` instead of crashing
- The Loops template ID placeholder must be replaced with a real ID once the template is created in Loops

This pattern ensures the signup record is always saved to the database regardless of email delivery status.

## Valor Chat

An AI chat companion powered by PydanticAI and Anthropic Claude, where visitors can talk with Valor Engels -- the AI co-author of the book.

### Architecture

The chat system is self-contained within the book app (`apps/book/chat.py`), separate from the main Cuttlefish AI chat system in `apps/ai/`. It uses:

- **Model**: Configured via `BOOK_CHAT_MODEL` setting (default: `anthropic:claude-sonnet-4-20250514` -- Sonnet chosen for fast response times in a chat context)
- **Agent**: `book_chat_agent` -- a PydanticAI `Agent` with `BookChatDeps` dataclass for conversation history
- **No tools**: Chat-only, no code execution or external tool access

### System Prompt Rationale

The system prompt (`VALOR_SYSTEM_PROMPT` in `apps/book/chat.py`) is deliberately scoped:

**What Valor can discuss:**
- The book's themes: hiring, onboarding, and managing AI employees alongside human teams
- Practical frameworks for CEO-level decision making around AI integration
- Real-world case studies of blended workforces
- The cultural shift when AI agents become colleagues

**What Valor cannot do:**
- Act as a general-purpose assistant (explicitly blocked: "You are NOT a general-purpose assistant")
- Execute code or use tools
- Discuss topics outside the book's scope (steers back to book topics)

**Why scoped this way:**
- Prevents prompt injection and abuse -- anonymous visitors cannot repurpose Valor for unrelated tasks
- Keeps conversations on-brand and valuable for the book's marketing purpose
- Encourages visitors to sign up as early readers for deeper engagement

### Conversation Handling

- Conversation history is stored in Django's session (`request.session["book_chat_history"]`)
- History is capped at 20 messages to prevent session bloat
- **Rate limiting**: 20 messages per hour per session, tracked via `book_chat_rate_timestamps` session key. Returns 429 when exceeded. Only successful responses count toward the limit.
- The `get_valor_response()` async function (called via `async_to_sync`) rebuilds PydanticAI `ModelRequest`/`ModelResponse` objects from the session history
- `ValorSendMessageView` returns JSON with an HTML fragment (built with `format_html`) for the chat UI (vanilla JS fetch, not full HTMX)
- On agent errors, a graceful fallback message is returned instead of a 500. Failed responses do not consume rate limit slots or pollute conversation history.

### View Structure

The views module was refactored from a single `views.py` into a package (`apps/book/views/`):

| Module | Views |
|--------|-------|
| `landing.py` | `LandingView` -- rich landing page, queries Testimonials with table-existence check |
| `announcements.py` | `AnnouncementListView` |
| `signup.py` | `EarlyReaderSignupView`, `SignupSuccessView` |
| `chat.py` | `ValorChatView` (template page), `ValorSendMessageView` (POST endpoint) |
| `chapters.py` | `ChapterListView`, `ChapterDetailView` -- both use `LoginRequiredMixin` |

### Pre-Migration Safety

`LandingView` checks whether the `book_testimonial` table exists before querying it (`_table_exists()` in `landing.py`). This prevents transaction aborts when the landing page is visited before migrations for the new models have been run.

## Draft Chapter Reader

Early readers with login access can read draft chapters rendered from Markdown.

- `/chapters/` lists all published chapters (where `published_at` is set and in the past)
- `/chapters/<int:pk>/` renders a single chapter using `DraftChapter.body_html` (Markdown to HTML)
- Both views require authentication via `LoginRequiredMixin` with `login_url = "/admin/login/"`
- Empty `body_markdown` renders as an empty string (no special "coming soon" state in the model)

## Infrastructure Setup

### DNS (Cloudflare)

Create a CNAME record:
- **Name**: `blendedworkforce.ai` (or `@`)
- **Target**: `cuttlefish-ea1h.onrender.com`
- **SSL mode**: Full (not Full Strict)

### Render

Add `blendedworkforce.ai` as a custom domain on the cuttlefish web service via the Render dashboard. Render auto-issues TLS certificates.

### Django Settings

- `ALLOWED_HOSTS`: includes `blendedworkforce.ai` and `www.blendedworkforce.ai` (in `settings/env.py`)
- `CORS_ORIGIN_WHITELIST`: includes both domains (in `settings/env.py`)
- `CSRF_TRUSTED_ORIGINS`: includes both domains (in `settings/production.py`)
- `MIDDLEWARE`: `DomainRoutingMiddleware` registered after `SecurityMiddleware` (in `settings/base.py`)

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `BOOK_FEEDBACK_FORM_URL` | External Google Form URL for feedback |
| `ANTHROPIC_API_KEY` | Required for Valor chat agent |
| `BOOK_CHAT_MODEL` | PydanticAI model string for Valor chat (default: `anthropic:claude-sonnet-4-20250514`, set in `settings/base.py`) |
| `LOOPS_API_KEY` | Required for early reader welcome emails |
| `LOOPS_EARLY_READER_TEMPLATE_ID` | Loops transactional email template (placeholder: `__loops_early_reader_welcome_id__`) |

## Adding Another Domain-Based Site

To serve a new site on a different domain:

1. Add the domain to `ALLOWED_HOSTS`, `CORS_ORIGIN_WHITELIST`, and `CSRF_TRUSTED_ORIGINS`
2. Add the hostname to the `BOOK_DOMAINS` set in `middleware.py` (or create a new set for the new site)
3. Create a new Django app with models, views, and URL config
4. Create a `root_urls.py` that includes the app URLs under a namespace
5. Update `DomainRoutingMiddleware` to set `request.urlconf` for the new domain
6. Add the custom domain in Render and create DNS records
