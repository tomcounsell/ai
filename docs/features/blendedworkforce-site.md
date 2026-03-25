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
| `/health/` | `health_check` | Health check (shared) |

Feedback is collected via an external Google Form (configured via `BOOK_FEEDBACK_FORM_URL` env var).

### Models

| Model | Fields | Purpose |
|-------|--------|---------|
| `Announcement` | title, body, published_at, created_at | Public updates about the book |

Announcements are only shown when `published_at` is set and in the past.

### Templates

All templates are in `apps/public/templates/book/` and extend `book/base.html`, which is a standalone template with editorial typography (Playfair Display + Inter) and a warm color palette separate from Cuttlefish branding.

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

## Adding Another Domain-Based Site

To serve a new site on a different domain:

1. Add the domain to `ALLOWED_HOSTS`, `CORS_ORIGIN_WHITELIST`, and `CSRF_TRUSTED_ORIGINS`
2. Add the hostname to the `BOOK_DOMAINS` set in `middleware.py` (or create a new set for the new site)
3. Create a new Django app with models, views, and URL config
4. Create a `root_urls.py` that includes the app URLs under a namespace
5. Update `DomainRoutingMiddleware` to set `request.urlconf` for the new domain
6. Add the custom domain in Render and create DNS records
