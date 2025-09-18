# TODO List for Cuttlefish Project

## Current Status

This document tracks pending infrastructure components and improvements for the codebase. Check here before implementing features that need these components.

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
