# QuickBooks MCP Integration - Codebase Primer

## 🛑 IMPORTANT: Context Loading Protocol

**This command loads essential codebase context. After running this primer:**
1. The assistant will have deep understanding of the codebase structure
2. The assistant will know where to find specific components
3. The assistant will understand the project's patterns and conventions
4. **The assistant will WAIT for your instructions before making any edits**

**What happens next:**
- The assistant has loaded comprehensive context about your codebase
- You can now provide specific instructions for what you want to accomplish
- The assistant will use this context to navigate efficiently and make appropriate changes

## Project Overview
This is a Django application that provides a Model Context Protocol (MCP) server for QuickBooks integration. It enables AI assistants to interact with QuickBooks data through a standardized interface, featuring OAuth authentication, async API operations, and comprehensive Django admin tools.

## Core Technology Stack
- **Framework**: Django 5.0+
- **Database**: PostgreSQL (required for JSON field support)
- **Package Management**: uv (modern Python package manager)
- **Frontend**: HTMX + Tailwind CSS v4
- **Testing**: pytest with factory_boy
- **Code Quality**: Black, isort, Ruff, pyright
- **Python**: 3.11+

## Application Architecture

### Core Components & Where to Find Them

```
apps/
├── common/           # Foundation layer - NO dependencies on other apps
│   ├── behaviors/    # Reusable model mixins (Timestampable, Authorable, etc.)
│   ├── models/       # Base models (User, Address, Organization, Image)
│   ├── tests/        # Shared test utilities and factories
│   └── utils/        # Helper functions and utilities
│
├── integration/      # External service integrations
│   └── quickbooks/   # QuickBooks API integration (depends on common)
│       ├── client.py         # Async API client with OAuth handling
│       ├── models.py         # QuickBooksConnection model
│       ├── oauth_views.py    # OAuth flow implementation
│       ├── api.py           # Synchronous API wrapper
│       └── tests/           # Integration tests
│
├── ai/              # MCP server implementation (depends on integration)
│   ├── mcp/         # Model Context Protocol server
│   │   ├── quickbooks_server.py  # Main MCP server entry point
│   │   └── quickbooks_tools.py   # Tool definitions and schemas
│   ├── models/      # MCP session and API key models
│   └── tests/       # MCP server tests
│
├── api/             # REST API endpoints (depends on all above)
│   └── quickbooks/  # QuickBooks-specific API views
│
├── public/          # Web UI with HTMX (depends on all above)
│   ├── templates/   # All templates centralized here
│   ├── views/       # HTMX-powered web views
│   └── static/      # CSS, JS, images
│
└── staff/           # Admin tools and internal interfaces
    └── admin.py     # Django admin customizations
```

### Layered Settings Configuration
Settings are modularized and loaded in a specific order:

```
settings/
├── env.py         # Environment detection (LOCAL/STAGE/PRODUCTION)
├── base.py        # Core Django configuration
├── database.py    # Database setup from DATABASE_URL
├── third_party.py # External service configurations
├── production.py  # Production-specific settings
└── local.py       # Local development overrides
```

## Key Design Patterns

### Behavior Mixins
Reusable model behaviors in `apps/common/behaviors/`:
- **Timestampable**: Automatic created/modified tracking
- **Authorable**: Content authorship tracking
- **Publishable**: Publishing workflow management
- **Expirable**: Time-based content expiration
- **Permalinkable**: SEO-friendly URL generation
- **Locatable**: Geographic data handling
- **Annotatable**: Flexible notes system

### Template Organization
- All templates centralized in `/templates/` (not in individual apps)
- Template inheritance hierarchy for consistent UI
- Partial templates for HTMX components
- No JavaScript-heavy SPA approach - server-side rendering with HTMX enhancements

### Frontend Philosophy
- **HTMX-first**: Dynamic interactions without complex JavaScript
- **Tailwind CSS v4**: Utility-first styling with django-tailwind-cli
- **Progressive Enhancement**: Works without JavaScript, enhanced with it
- **Server-side State**: Django handles state, not client-side frameworks

## Development Workflow

### Environment Setup
1. Python virtual environment with venv
2. Dependency management exclusively through uv
3. PostgreSQL database (SQLite not supported due to JSON fields)
4. Environment variables in `.env.local` (from `.env.example`)

### Testing Strategy
- **Test-Driven Development**: Write tests before implementation
- **Test Organization**: Separate directories for models, views, serializers, etc.
- **Factory Pattern**: Use factory_boy for test data generation
- **Coverage Goal**: 100% for models and behaviors
- **E2E Testing**: Browser automation tests for critical user paths

### Code Quality Standards
- **Type Hints**: Required for all new code
- **Formatting**: Black (88 char lines) + isort for imports
- **Linting**: Ruff for fast, comprehensive checks
- **Type Checking**: pyright for static type analysis
- **Naming Conventions**: 
  - Datetime fields end with `_at`
  - Boolean fields start with `is_` or `has_`
  - Clear, descriptive variable names

## QuickBooks Integration Deep Dive

### MCP Server Entry Points
The MCP server enables AI assistants to interact with QuickBooks data:

1. **Main Server**: `apps/ai/mcp/quickbooks_server.py`
   - Handles MCP protocol communication
   - Routes tool calls to appropriate handlers
   - Manages authentication and sessions

2. **Tool Definitions**: `apps/ai/mcp/quickbooks_tools.py`
   - Defines available QuickBooks operations
   - JSON schemas for tool parameters
   - Tool documentation for AI assistants

3. **QuickBooks Client**: `apps/integration/quickbooks/client.py`
   - Async HTTP client for QuickBooks API
   - OAuth token management
   - Request/response handling

### OAuth Authentication Flow
Located in `apps/integration/quickbooks/oauth_views.py`:
1. User initiates connection at `/api/quickbooks/connect/`
2. Redirected to QuickBooks for authorization
3. Callback processes tokens at `/api/quickbooks/callback/`
4. Tokens stored in `QuickBooksConnection` model
5. MCP server uses tokens for API access

### Key Models and Their Locations

**QuickBooks Models** (`apps/integration/quickbooks/models.py`):
- `QuickBooksConnection`: OAuth tokens and company info
- Stores access/refresh tokens, realm ID, company name
- Handles token refresh automatically

**MCP Models** (`apps/ai/models/`):
- `MCPSession`: Active MCP session tracking
- `APIKey`: Authentication for MCP clients
- Session management and security

**Common Models** (`apps/common/models/`):
- `User`: Custom user model
- `Organization`: Multi-tenant support
- `Address`: Shared address functionality

## Finding What You Need - Quick Reference

### Adding New QuickBooks Operations
1. **Define the tool**: `apps/ai/mcp/quickbooks_tools.py`
   - Add to `QUICKBOOKS_TOOLS` list
   - Define parameter schema

2. **Implement handler**: `apps/ai/mcp/quickbooks_server.py`
   - Add case in `handle_tool_call()`
   - Call appropriate client method

3. **Add API method**: `apps/integration/quickbooks/client.py`
   - Implement async method
   - Handle QuickBooks API specifics

4. **Write tests**: 
   - MCP tests: `apps/ai/tests/test_mcp_quickbooks.py`
   - Integration tests: `apps/integration/quickbooks/tests/`

### Common File Locations

**Configuration**:
- Environment variables: `.env.local` (copy from `.env.example`)
- Django settings: `settings/` directory
- QuickBooks config: `settings/third_party.py`

**URLs and Routing**:
- Main URLs: `urls.py`
- API URLs: `apps/api/urls.py`
- QuickBooks OAuth: `apps/integration/quickbooks/urls.py`

**Templates** (all in `apps/public/templates/`):
- Base layout: `base.html`
- HTMX partials: `partials/`
- QuickBooks UI: `quickbooks/`

**Static Files**:
- CSS: `apps/public/static/css/`
- JavaScript: `apps/public/static/js/`
- Tailwind config: `tailwind.config.js`

**Tests**:
- Test factories: `apps/common/tests/factories.py`
- MCP tests: `apps/ai/tests/`
- Integration tests: `apps/integration/quickbooks/tests/`
- E2E tests: `apps/*/tests/test_e2e_*.py`

## Working Principles

### Separation of Concerns
- Models handle business logic
- Views coordinate between models and templates
- Templates focus on presentation
- Behaviors encapsulate reusable model patterns

### Don't Repeat Yourself (DRY)
- Behavior mixins for common model patterns
- Template inheritance for UI consistency
- Centralized configuration in settings
- Shared utilities in `apps/common/`

### Convention Over Configuration
- Standard Django project structure
- Predictable file locations
- Consistent naming patterns
- Clear import organization

### Progressive Complexity
- Start simple with Django defaults
- Add complexity only when needed
- Keep third-party dependencies minimal
- Optimize for developer understanding

## Critical Code Paths to Understand

### MCP Request Flow
1. **MCP Client** → connects to server via stdio
2. **quickbooks_server.py** → receives tool call request
3. **Authentication** → validates API key from request
4. **Tool Router** → matches tool name to handler
5. **QuickBooks Client** → makes async API request
6. **Token Refresh** → automatic if token expired
7. **Response** → formatted and returned to MCP client

### OAuth Connection Flow
1. **User clicks "Connect"** → `/api/quickbooks/connect/`
2. **OAuth URL built** → `apps/integration/quickbooks/oauth_views.py:26`
3. **QuickBooks auth** → User authorizes in QuickBooks
4. **Callback received** → `/api/quickbooks/callback/`
5. **Tokens exchanged** → `apps/integration/quickbooks/oauth_views.py:45`
6. **Connection saved** → `QuickBooksConnection` model
7. **MCP enabled** → Server can now access QuickBooks data

### Data Flow Architecture
```
MCP Client Request
    ↓
quickbooks_server.py (MCP protocol handling)
    ↓
quickbooks_tools.py (parameter validation)
    ↓
client.py (async QuickBooks API calls)
    ↓
QuickBooks API
    ↓
Response formatting & return
```

## Common Tasks Quick Reference

### Daily Development
```bash
# Start Django dev server
uv run python manage.py runserver

# Start MCP server for testing
uv run python -m apps.ai.mcp.quickbooks_server

# Django shell with all models loaded
uv run python manage.py shell

# Run tests with proper settings
DJANGO_SETTINGS_MODULE=settings pytest

# Run specific test file
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_quickbooks.py -v
```

### Code Quality
```bash
# Format code (MUST run before committing)
black . && isort . --profile black

# Lint code
uv run ruff check .

# Type checking
uv run pyright

# Run all pre-commit hooks
uv run pre-commit run --all-files
```

### Database Operations
```bash
# Create migrations (requires approval)
uv run python manage.py makemigrations

# Apply migrations
uv run python manage.py migrate

# Database shell
uv run python manage.py dbshell

# Create superuser for admin
uv run python manage.py createsuperuser
```

### QuickBooks MCP Testing
```bash
# Test MCP server locally
uv run python -m apps.ai.mcp.quickbooks_server

# Test specific QuickBooks tool
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_quickbooks.py::test_get_company_info -v

# Test OAuth flow
DJANGO_SETTINGS_MODULE=settings pytest apps/integration/quickbooks/tests/test_oauth.py -v
```

## Architecture Decisions

### Why PostgreSQL Only?
- JSON field support for flexible data storage
- Advanced querying capabilities
- Production-proven reliability
- Better performance for complex queries

### Why HTMX Over React/Vue?
- Simpler mental model
- Leverages Django's template system
- Reduces JavaScript complexity
- Better SEO and accessibility
- Faster initial page loads

### Why uv for Package Management?
- 10-100x faster than pip
- Built-in virtual environment management
- Reproducible builds with lock files
- Modern dependency resolution

### Why Modular Settings?
- Environment-specific configurations
- Easier debugging and testing
- Clear configuration hierarchy
- Secrets isolation

## Next Steps for New Features

1. **Identify the appropriate app** for your feature
2. **Create models** with appropriate behavior mixins
3. **Write tests first** following TDD principles
4. **Implement views** (HTMX for web, ViewSets for API)
5. **Create templates** in the centralized directory
6. **Add URL patterns** to the app's urls.py
7. **Run tests** and ensure coverage
8. **Format code** with Black and isort
9. **Create focused commits** with clear messages

## Important Constraints

- **No SQLite**: Tests and development require PostgreSQL
- **No pip install**: Use uv exclusively for packages
- **No app-specific templates**: All templates in /templates/
- **No app-specific static files**: All static files in /static/
- **No migration creation**: Wait for approval before migrations
- **No JavaScript frameworks**: Use HTMX for interactivity

## Documentation Deep Dives

The `docs/` directory contains comprehensive guides for specific topics. Here's when to consult each:

### Essential Reading Before Starting
- **docs/ARCHITECTURE.md** - Understand the system's overall design
- **docs/REPO_MAP.md** - Navigate the codebase structure
- **CLAUDE.md** - Primary reference for commands and guidelines

### Feature Development Guides

#### Building Models
- **docs/MODEL_CONVENTIONS.md** - Naming, structure, relationships
- **docs/BEHAVIOR_MIXINS.md** - Reusable model behaviors
- Review existing models in `apps/common/models/` for patterns

#### Frontend Implementation
- **docs/HTMX_INTEGRATION.md** - HTMX patterns and best practices
- **docs/TEMPLATE_CONVENTIONS.md** - Template organization and naming
- **docs/VIEW_CONVENTIONS.md** - View class patterns (MainContentView, HTMXView)
- **docs/MODAL_PATTERNS.md** - Modal dialogs with HTMX
- **docs/TAILWIND_V4.md** - Styling with Tailwind CSS v4

#### Testing Strategy
- **docs/advanced/TEST_CONVENTIONS.md** - Test organization and patterns
- **docs/advanced/E2E_TESTING.md** - End-to-end browser tests
- **docs/advanced/BROWSER_TESTING.md** - Browser automation framework
- **docs/advanced/TEST_TROUBLESHOOTING.md** - Common issues and solutions
- **docs/advanced/AI_BROWSER_TESTING.md** - AI-assisted test generation

#### Error Management
- **docs/ERROR_HANDLING.md** - Error handling patterns and strategies

### Setup and Configuration
- **docs/guides/SETUP_GUIDE.md** - Detailed environment setup
- **docs/guides/CONTRIBUTING.md** - Contribution workflow
- **docs/guides/PYCHARM_CONFIG.MD** - PyCharm IDE configuration

### Migration and Upgrade Guides
- **docs/guides/TAILWIND_V4_UPGRADE.md** - Upgrading from Tailwind v3
- **docs/guides/TAILWIND_V4_MIGRATION_CHECKLIST.md** - Migration checklist

### Code Examples
- **docs/examples/modal_example_view.py** - Complete modal implementation
- **docs/examples/item_list_example.html** - List view with pagination
- **docs/examples/list_items_partial.html** - HTMX partial rendering
- **docs/guides/example_unfold_admin.py** - Admin customization

### Advanced Topics
- **docs/advanced/CICD.md** - CI/CD pipeline configuration
- **docs/advanced/SCREENSHOT_SERVICE.md** - Visual testing service
- **docs/advanced/HTMX_AND_RESPONSIVE_TESTING.md** - Responsive design testing

### Quick Reference Workflow

1. **Starting a new feature?**
   - Read: ARCHITECTURE.md → relevant convention docs → examples

2. **Implementing models?**
   - Read: MODEL_CONVENTIONS.md → BEHAVIOR_MIXINS.md

3. **Building UI components?**
   - Read: HTMX_INTEGRATION.md → TEMPLATE_CONVENTIONS.md → VIEW_CONVENTIONS.md

4. **Writing tests?**
   - Read: TEST_CONVENTIONS.md → relevant test type docs

5. **Debugging issues?**
   - Read: ERROR_HANDLING.md → TEST_TROUBLESHOOTING.md

6. **Setting up environment?**
   - Read: SETUP_GUIDE.md → CLAUDE.md setup section

### Documentation Philosophy

The documentation follows a layered approach:
- **CLAUDE.md**: Commands and quick reference
- **prime.md**: High-level architecture and concepts
- **docs/**: Deep dives into specific topics
- **examples/**: Working code examples
- **sphinx_docs/**: Auto-generated API documentation

Always start with the high-level docs to understand context, then drill down into specific guides as needed. The examples provide practical implementations of the patterns described in the documentation.

## Debugging Guide

### Common Issues and Solutions

**MCP Server Won't Start**:
- Check: `QUICKBOOKS_CLIENT_ID` and `QUICKBOOKS_CLIENT_SECRET` in `.env.local`
- Verify: PostgreSQL is running (`psql -l`)
- Ensure: Virtual environment activated (`source .venv/bin/activate`)

**OAuth Flow Fails**:
- Check: Redirect URI matches QuickBooks app settings
- Verify: `QUICKBOOKS_SANDBOX_MODE` matches QuickBooks environment
- Debug: Check logs in `apps/integration/quickbooks/oauth_views.py`

**Token Refresh Issues**:
- Location: `apps/integration/quickbooks/client.py:refresh_token()`
- Check: Token expiry in `QuickBooksConnection` model
- Debug: Enable logging in client.py

**MCP Tool Not Found**:
- Verify: Tool added to `QUICKBOOKS_TOOLS` list
- Check: Handler implemented in `quickbooks_server.py`
- Test: Run MCP server with debug logging

### Key Debug Points

**Enable Debug Logging**:
```python
# In apps/ai/mcp/quickbooks_server.py
import logging
logging.basicConfig(level=logging.DEBUG)
```

**Test QuickBooks Connection**:
```python
# Django shell
from apps.integration.quickbooks.client import QuickBooksClient
from apps.integration.quickbooks.models import QuickBooksConnection

conn = QuickBooksConnection.objects.first()
client = QuickBooksClient(conn)
await client.test_connection()
```

**Inspect MCP Requests**:
```python
# In quickbooks_server.py handle_tool_call()
logger.debug(f"Tool: {tool_name}, Args: {arguments}")
```

## Context Loaded - Ready for Instructions

✅ **Context successfully loaded.** The assistant now has comprehensive understanding of:
- The QuickBooks MCP integration architecture
- Where to find all major components
- How data flows through the system
- Common patterns and conventions
- Testing and debugging approaches

**The assistant is now waiting for your specific instructions.**

You can ask the assistant to:
- Add new QuickBooks MCP tools
- Fix bugs in the integration
- Improve the OAuth flow
- Add new API endpoints
- Enhance the MCP server
- Write tests for new features
- Debug existing issues
- Or any other development task

This primer provides the essential context for understanding and contributing to this QuickBooks MCP integration. The architecture emphasizes clean separation of concerns, async operations, and comprehensive testing while providing a solid foundation for AI-assisted QuickBooks interactions.
