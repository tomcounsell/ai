# TODO List for Cuttlefish Project

## Current Status

This document tracks pending infrastructure components and improvements for the codebase. Check here before implementing features that need these components.

## 🎯 Immediate Priorities (Next Up)

### 1. Production Health Check Tests
**Status**: ✅ Completed
**Priority**: High
**Estimated Time**: 3-4 hours
**Owner**: Claude Code
**Completed**: 2025-10-24

Create end-to-end production tests to verify services are online and functioning.

**Tasks**:
- [x] Create health check endpoints (`/health/`, `/health/deep/`)
- [x] Write production health check script (`tools/testing/production_health_check.py`)
- [x] Test critical endpoints (homepage, MCP pages, manifests)
- [x] Verify CORS headers on MCP endpoints
- [x] Add browser-based E2E tests with Playwright
- [x] Set up GitHub Actions workflow for automated checks (every 15 min)
- [x] Configure failure notifications

**Success Criteria**: ✅ All met
- All production endpoints return 200
- CORS headers present on MCP manifest/README
- Automated checks run on schedule
- Team notified on failures

**Implementation**:
- Health check views in `apps/api/views/health_views.py`
- Production test script in `tools/testing/production_health_check.py`
- E2E tests in `apps/public/tests/test_e2e_production_pages.py`
- GitHub Actions workflow in `.github/workflows/production-health-check.yml`

---

### 2. Remove /ai/ URL Prefix
**Status**: ✅ Completed
**Priority**: Medium
**Estimated Time**: 1-2 hours
**Owner**: Claude Code
**Completed**: 2025-10-24

Simplify URLs by removing the `/ai/` prefix from MCP endpoints.

**Current**: `https://ai.yuda.me/ai/mcp/creative-juices/`
**Target**: `https://ai.yuda.me/mcp/creative-juices/`

**Tasks**:
- [x] Update `settings/urls.py` to route `/mcp/` directly
- [x] Update `apps/ai/urls.py` to remove `mcp/` prefix
- [x] Search and replace hardcoded URLs in codebase
- [x] Add redirects for old URLs (optional but recommended)
- [x] Update external documentation
- [x] Update production health check script with new URLs
- [x] Deploy and verify

**Success Criteria**: ✅ All met
- MCP endpoints work at `/mcp/` prefix
- Old URLs redirect to new URLs (301 permanent)
- All documentation updated
- No broken links

**Implementation**:
- Updated main routing in `settings/urls.py`
- Removed `mcp/` prefix from `apps/ai/urls.py`
- Added permanent redirects for legacy `/ai/mcp/*` URLs
- Updated test files and documentation

---

### 3. New Landing Page for ai.yuda.me
**Status**: ✅ Completed
**Priority**: High
**Estimated Time**: 2-3 hours
**Owner**: Claude Code
**Completed**: 2025-10-24

Create a compelling single-page landing that showcases MCP servers.

**Sections**:
- Hero with value proposition and CTAs
- Featured MCP servers (Creative Juices, CTO Tools, QuickBooks Coming Soon)
- "What is MCP?" explainer
- Quick Start with installation instructions
- Footer with links

**Tasks**:
- [x] Create template (`apps/public/templates/landing/ai_platform.html`)
- [x] Create view (`apps/public/views/landing_views.py`)
- [x] Update URLs to use new landing as homepage
- [x] Test responsive design (mobile, tablet, desktop)
- [x] Verify all links work
- [x] Deploy and monitor

**Success Criteria**: ✅ All met
- Page loads in <2 seconds
- Responsive on all devices
- Clear value proposition
- Easy to find MCP servers
- Professional appearance

**Implementation**:
- Modern, gradient-themed template at `apps/public/templates/landing/ai_platform.html`
- View class at `apps/public/views/landing_views.py`
- Updated homepage route in `settings/urls.py`
- Purple/blue gradient design consistent with Creative Juices branding

---

## Completed Features ✅

- **Architecture**: Behavior mixins, consolidated templates, HTMX integration, Tailwind CSS v4
- **Testing**: Comprehensive test suite with 87.5% passing tests (372/425)
- **Models**: User, Team, Payment, Address, Image, Email, SMS, Subscription, etc.
- **Frontend**: HTMX-based UI with minimal JavaScript, responsive design
- **Admin**: Unfold admin theme with custom dashboard
- **Integrations**: AWS S3, Twilio SMS, Loops Email, Stripe Payments, QuickBooks
- **DevOps**: CI/CD workflows, Docker setup, Render deployment
- **MCP**: QuickBooks MCP server implementation
- **Documentation**: MCP Development Guide with templates and examples

## 🚧 Infrastructure Components (In Development)

### Rate Limiting System
**Status**: Not Started
**Priority**: High
**Location**: `apps/common/utilities/rate_limiting.py`

Centralized rate limiting for API endpoints and MCP servers.

**Planned Features**:
- Per-user rate limiting
- Per-IP rate limiting
- Configurable windows (sliding, fixed)
- Redis-backed for distributed systems
- Decorator support for views and methods
- MCP server integration

**Planned API**:
```python
from apps.common.utilities.rate_limiting import RateLimiter, rate_limit

# Class-based
limiter = RateLimiter(max_calls=100, window_seconds=3600)
await limiter.check(identifier)

# Decorator
@rate_limit(max_calls=100, window_seconds=3600)
async def my_function():
    pass
```

---

### Audit Logging System
**Status**: Not Started
**Priority**: High
**Location**: `apps/common/utilities/audit.py`

Comprehensive audit logging for security and compliance.

**Planned Features**:
- Structured logging with JSON output
- Automatic PII redaction
- Database backend for audit trails
- Search and filtering capabilities
- Integration with Django admin
- MCP tool call tracking

**Planned API**:
```python
from apps.common.utilities.audit import AuditLogger

audit = AuditLogger("module.name")
await audit.log_action(
    user=user,
    action="tool_call",
    resource="invoice",
    details={...}
)
```

---

### MCP Base Server Class
**Status**: Not Started
**Priority**: Medium
**Location**: `apps/ai/mcp/base.py`

Abstract base class for all MCP servers to reduce boilerplate.

**Planned Features**:
- Standard initialization
- Built-in rate limiting
- Built-in audit logging
- Error handling
- Health checks
- Metrics collection

**Planned API**:
```python
from apps.ai.mcp.base import BaseMCPServer

class MyMCPServer(BaseMCPServer):
    def get_tools(self):
        return [...]

    async def handle_tool(self, name, args):
        return await self.route_tool(name, args)
```

---

### API Key Management UI
**Status**: Not Started
**Priority**: Medium
**Location**: `apps/public/views/api_keys.py`

Web interface for users to manage their API keys.

**Planned Features**:
- Create/revoke API keys
- Set expiration dates
- Usage statistics
- Rate limit configuration
- Scoped permissions

---

### MCP Testing Framework
**Status**: Not Started
**Priority**: Medium
**Location**: `apps/common/testing/mcp.py`

Utilities for testing MCP servers.

**Planned Features**:
- Mock MCP client
- Request/response recording
- Assertion helpers
- Performance benchmarking
- Integration test base class

**Planned API**:
```python
from apps.common.testing.mcp import MCPTestCase

class TestMyMCP(MCPTestCase):
    async def test_tool_execution(self):
        response = await self.call_tool("my_tool", {})
        self.assert_tool_success(response)
```

---

### Caching Utilities
**Status**: Not Started
**Priority**: Low
**Location**: `apps/common/utilities/cache.py`

Advanced caching patterns beyond Django's cache framework.

**Planned Features**:
- Async cache decorators
- Cache warming
- Invalidation patterns
- Multi-tier caching
- Cache statistics

---

### MCP Registry System
**Status**: Not Started
**Priority**: Low
**Location**: `apps/ai/mcp/registry.py`

Central registry for discovering and managing MCP servers.

**Planned Features**:
- Auto-discovery of MCP servers
- Dynamic loading
- Version management
- Capability querying
- Admin interface

---

## Enhancement Roadmap

### Code Quality 🧪
- [ ] Refactor redundant template logic

### Documentation 📝
- [ ] Create custom documentation theme
- [ ] Build searchable documentation site with versioning

### Admin Improvements 🛠️
- [ ] Implement responsive design for admin templates
- [ ] Add consistent icons for all admin models

### Performance ⚡
- [ ] Optimize HTMX interactions and document patterns
- [ ] Implement database query optimization and indexing
- [ ] Set up Django caching (models, querysets, template fragments)
- [ ] Configure Redis cache backend (optional)
- [ ] Document performance best practices

### DevOps & Deployment 🚀
- [ ] Implement blue/green deployment

### Observability 📊
- [ ] Implement Sentry error tracking with environment settings
- [ ] Set up structured logging with request ID tracking

### Infrastructure 🏗️
- [ ] Upgrade Docker configuration for production and development
- [ ] Standardize environment variable management

### API Enhancements 🔌
- [ ] Implement rate limiting system (see Infrastructure Components above)
- [ ] Implement usage tracking, analytics, and response caching
- [ ] Define API versioning strategy

### Accessibility & Internationalization
- [ ] Implement accessibility best practices in templates
- [ ] Add internationalization (i18n) support
- [ ] Configure static asset compression
- [ ] Configure advanced secrets management

---

## 🔧 Technical Debt

- [ ] Refactor QuickBooks MCP to use base class (once created)
- [ ] Standardize error responses across all MCP servers
- [ ] Add comprehensive MCP server metrics
- [ ] Implement connection pooling for all API clients
- [ ] Add retry logic with exponential backoff

---

## 🎯 Future Considerations

- GraphQL API alongside REST
- WebSocket support for real-time MCP
- Distributed MCP servers with message queuing
- MCP server versioning and backwards compatibility
- Multi-language MCP SDK generation

---

## 📋 Detailed Implementation Plans

### Plan 1: Production Health Check Tests (Detailed)

#### Phase 1: Create Health Check Endpoints (30 min)

```python
# apps/api/views/health_views.py
from django.http import JsonResponse
from django.db import connection
from django.core.cache import cache
from django.conf import settings

def health_check(request):
    """Basic health check endpoint"""
    return JsonResponse({
        "status": "healthy",
        "service": "cuttlefish",
        "environment": settings.DEPLOYMENT_TYPE
    })

def deep_health_check(request):
    """Detailed health check with dependencies"""
    checks = {
        "database": _check_database(),
        "cache": _check_cache(),
        "static_files": True,  # Assume OK if server is running
    }

    all_healthy = all(checks.values())
    status_code = 200 if all_healthy else 503

    return JsonResponse({
        "status": "healthy" if all_healthy else "unhealthy",
        "checks": checks,
        "environment": settings.DEPLOYMENT_TYPE
    }, status=status_code)

def _check_database():
    """Check database connectivity"""
    try:
        connection.ensure_connection()
        return True
    except Exception:
        return False

def _check_cache():
    """Check cache connectivity"""
    try:
        cache.set('health_check', 'ok', 10)
        return cache.get('health_check') == 'ok'
    except Exception:
        return False
```

Add URLs:
```python
# settings/urls.py
from apps.api.views.health_views import health_check, deep_health_check

urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('health/deep/', deep_health_check, name='deep_health_check'),
    # ... existing patterns
]
```

#### Phase 2: Create Production Test Script (45 min)

```python
# tools/testing/production_health_check.py
"""
Production health check script.
Run with: python tools/testing/production_health_check.py
"""

import requests
import sys
from typing import Dict, List, Tuple

PRODUCTION_BASE_URL = "https://ai.yuda.me"

ENDPOINTS_TO_CHECK = [
    # (url, expected_status, check_cors, description)
    ("/", 200, False, "Homepage"),
    ("/health/", 200, False, "Basic health check"),
    ("/health/deep/", 200, False, "Deep health check"),
    ("/mcp/creative-juices/", 200, False, "Creative Juices landing"),
    ("/mcp/creative-juices/manifest.json", 200, True, "Creative Juices manifest"),
    ("/mcp/creative-juices/README.md", 200, True, "Creative Juices README"),
]

def check_endpoint(url: str, expected_status: int, check_cors: bool, description: str) -> Tuple[bool, str]:
    """Check a single endpoint"""
    full_url = f"{PRODUCTION_BASE_URL}{url}"

    try:
        response = requests.get(full_url, timeout=10)

        # Check status code
        if response.status_code != expected_status:
            return False, f"Expected {expected_status}, got {response.status_code}"

        # Check CORS headers if required
        if check_cors:
            cors_header = response.headers.get('Access-Control-Allow-Origin')
            if not cors_header:
                return False, "Missing CORS header"

        return True, "OK"

    except requests.exceptions.RequestException as e:
        return False, f"Request failed: {str(e)}"

def run_health_checks() -> bool:
    """Run all health checks and report results"""
    print(f"Running production health checks for {PRODUCTION_BASE_URL}...")
    print("=" * 80)

    results: List[Dict] = []
    all_passed = True

    for url, expected_status, check_cors, description in ENDPOINTS_TO_CHECK:
        print(f"\nChecking: {description}")
        print(f"  URL: {url}")

        passed, message = check_endpoint(url, expected_status, check_cors, description)

        results.append({
            "description": description,
            "url": url,
            "passed": passed,
            "message": message
        })

        if passed:
            print(f"  ✅ {message}")
        else:
            print(f"  ❌ {message}")
            all_passed = False

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    passed_count = sum(1 for r in results if r["passed"])
    total_count = len(results)

    print(f"\nPassed: {passed_count}/{total_count}")

    if all_passed:
        print("\n✅ All health checks passed!")
        return True
    else:
        print("\n❌ Some health checks failed!")
        return False

if __name__ == "__main__":
    success = run_health_checks()
    sys.exit(0 if success else 1)
```

#### Phase 3: Browser E2E Tests (1-2 hours)

```python
# apps/public/tests/test_e2e_production_pages.py
"""
E2E tests for production pages.
Run with: python tools/testing/browser_test_runner.py apps/public/tests/test_e2e_production_pages.py
"""

import pytest
from playwright.sync_api import Page, expect

PRODUCTION_URL = "https://ai.yuda.me"

@pytest.fixture
def page(browser):
    """Create a new page for each test"""
    page = browser.new_page()
    yield page
    page.close()

def test_homepage_loads(page: Page):
    """Test that homepage loads"""
    page.goto(PRODUCTION_URL)
    expect(page).to_have_title("Home")

def test_creative_juices_landing(page: Page):
    """Test Creative Juices landing page"""
    page.goto(f"{PRODUCTION_URL}/mcp/creative-juices/")
    expect(page).to_have_title("Creative Juices MCP - Break Free from Predictable AI")

def test_manifest_accessible(page: Page):
    """Test manifest.json is accessible"""
    response = page.goto(f"{PRODUCTION_URL}/mcp/creative-juices/manifest.json")
    assert response.status == 200
```

#### Phase 4: GitHub Actions (15 min)

```yaml
# .github/workflows/production-health-check.yml
name: Production Health Check

on:
  schedule:
    - cron: '*/15 * * * *'  # Every 15 minutes
  workflow_dispatch:

jobs:
  health-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install requests
      - name: Run health checks
        run: python tools/testing/production_health_check.py
```

---

### Plan 2: Remove /ai/ URL Prefix (Detailed)

#### Step 1: Update settings/urls.py

```python
# BEFORE
urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.api.urls")),
    path("ai/", include("apps.ai.urls")),  # ← Remove this
    path("", include("apps.public.urls")),
]

# AFTER
urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.api.urls")),
    path("mcp/", include("apps.ai.urls")),  # ← Direct to MCP
    path("", include("apps.public.urls")),
]
```

#### Step 2: Update apps/ai/urls.py

Remove `mcp/` prefix from all paths since it's now in settings/urls.py:

```python
# BEFORE
urlpatterns = [
    path("mcp/creative-juices/", CreativeJuicesLandingView.as_view(), ...),
]

# AFTER
urlpatterns = [
    path("creative-juices/", CreativeJuicesLandingView.as_view(), ...),
]
```

#### Step 3: Find and Replace Hardcoded URLs

```bash
# Find all references
rg "/ai/mcp/" --type py --type md --type html --type json

# Review and replace carefully
```

#### Step 4: Add Redirects (Optional)

```python
# apps/public/urls.py or settings/urls.py
from django.views.generic import RedirectView

urlpatterns = [
    # Legacy redirects
    path("ai/mcp/<path:subpath>",
         RedirectView.as_view(url="/mcp/%(subpath)s", permanent=True)),
]
```

#### Step 5: Test

```bash
# Local
python manage.py runserver
curl http://localhost:8000/mcp/creative-juices/

# Production (after deploy)
curl https://ai.yuda.me/mcp/creative-juices/
curl -I https://ai.yuda.me/ai/mcp/creative-juices/  # Should redirect
```

---

### Plan 3: New Landing Page (Detailed)

#### Template Structure

Create `apps/public/templates/landing/ai_platform.html` with sections:

1. **Hero Section** - Gradient background, bold headline, CTAs
2. **MCP Servers Cards** - Grid of available servers with icons
3. **What is MCP** - Educational section with benefits
4. **Quick Start** - Code snippet for installation
5. **Footer** - Links and copyright

#### Key Features

- Responsive design (mobile-first)
- Tailwind CSS v4 styling
- Purple/blue gradient theme (consistent with Creative Juices)
- Fast loading (no heavy images)
- SEO-friendly meta tags

#### View and URLs

```python
# apps/public/views/landing_views.py
from django.views.generic import TemplateView

class AIPlatformLandingView(TemplateView):
    template_name = "landing/ai_platform.html"

# apps/public/urls.py
urlpatterns = [
    path("", AIPlatformLandingView.as_view(), name="home"),
]
```

#### Content Guidelines

- **Hero**: "AI Integration Platform" + "MCP servers for extending AI assistants"
- **Server Cards**: Focus on benefits, not features. Keep descriptions <100 words.
- **Quick Start**: 3-5 steps max, copy-pasteable code
- **Professional tone**: Technical but accessible

---

## Contributing

When starting work on any TODO item:
1. Update the status to "In Progress" with your name and date
2. Create a feature branch
3. Update this document when complete
4. Reference the infrastructure component in your code with a TODO comment

When using planned components that aren't ready:
1. Check this document for status
2. Implement a minimal version for your immediate needs
3. Add a TODO comment referencing the planned implementation:
   ```python
   # TODO: Replace with apps.common.utilities.rate_limiting when available
   # See docs/TODO.md for planned implementation
   ```

## Priority Levels

- **High**: Blocking multiple features or security-critical
- **Medium**: Would improve developer experience significantly
- **Low**: Nice to have, can work around easily

## Status Definitions

- **Not Started**: No work has begun
- **In Progress**: Active development (include developer name and start date)
- **In Review**: Code complete, under review
- **Complete**: Merged and available for use
- **Blocked**: Waiting on dependencies or decisions
