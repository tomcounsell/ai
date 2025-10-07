# Cuttlefish - AI Integration Platform

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

Cuttlefish is a Django-based AI integration platform that provides multiple services:

1. **Model Context Protocol (MCP) Servers**
   - QuickBooks integration for AI assistants
   - Creative Juices for creative thinking prompts
   - Hosted and accessible via web endpoints

2. **Django Web Application**
   - Serves MCP server landing pages and manifests
   - OAuth integration flows
   - REST API endpoints
   - Web-based interfaces with HTMX

3. **Future: Voice-Driven Desktop Agent**
   - Tauri-based desktop application
   - Voice-controlled AI agent for creating mini-applications
   - Real-time code generation and execution

## Core Technology Stack

- **Framework**: Django 5.0+
- **Database**: PostgreSQL (required for JSON field support)
- **Package Management**: uv (modern Python package manager)
- **Frontend**: HTMX + Tailwind CSS v4
- **MCP Framework**: FastMCP for MCP server implementation
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
├── ai/              # MCP servers and AI functionality (depends on integration)
│   ├── mcp/         # Model Context Protocol servers
│   │   ├── quickbooks_server.py      # QuickBooks MCP server
│   │   ├── creative_juices_server.py # Creative Juices MCP server
│   │   ├── creative_juices_words.py  # Word lists for Creative Juices
│   │   ├── CREATIVE_JUICES_README.md # Creative Juices installation guide
│   │   ├── creative_juices_manifest.json # MCP manifest
│   │   ├── creative_juices_web.html  # Landing page HTML
│   │   └── DEPLOYMENT.md             # Django deployment guide
│   ├── views/       # Web views for MCP landing pages
│   │   └── mcp_views.py   # Django views serving MCP assets
│   ├── models/      # MCP session and AI-related models
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

## MCP Server Implementations

### 1. QuickBooks MCP Server

**Purpose**: Enables AI assistants to interact with QuickBooks data through standardized MCP protocol.

**Key Components**:
- **Server**: `apps/ai/mcp/quickbooks_server.py` - Main MCP server with tool handlers
- **Client**: `apps/integration/quickbooks/client.py` - Async QuickBooks API client
- **OAuth**: `apps/integration/quickbooks/oauth_views.py` - OAuth authentication flow
- **Models**: `apps/integration/models/quickbooks.py` - QuickBooksConnection, Organization

**Running**:
```bash
uv run python -m apps.ai.mcp.quickbooks_server
```

**Authentication**: OAuth 2.0 flow with token refresh
**Data Access**: Customers, invoices, vendors, payments, company info

### 2. Creative Juices MCP Server

**Purpose**: Provides randomness tools to encourage creative thinking through concrete metaphors and strategic frameworks.

**Key Components**:
- **Server**: `apps/ai/mcp/creative_juices_server.py` - FastMCP server with three tools
- **Word Lists**: `apps/ai/mcp/creative_juices_words.py` - Curated verb-noun combinations
- **Web Assets**: Landing page, manifest, README served via Django

**Tools**:
1. `get_inspiration()` - Gentle creative nudges with everyday metaphors
2. `think_outside_the_box()` - Intense creative shocks with dramatic metaphors
3. `reality_check()` - Strategic validation using Elon Musk's frameworks

**Running**:
```bash
uv run python -m apps.ai.mcp.creative_juices_server
```

**Deployment**: https://ai.yuda.me/mcp/creative-juices (Django-hosted)
**Authentication**: None required (stateless, public tools)
**Privacy**: No data collection, fully local operation

### MCP Web Hosting Architecture

Both MCP servers are deployed via Django web views:

**Django Views** (`apps/ai/views/mcp_views.py`):
- `CreativeJuicesLandingView` - Serves HTML landing page
- `CreativeJuicesManifestView` - Serves manifest.json with CORS headers
- `CreativeJuicesReadmeView` - Serves README.md as markdown

**URL Routes** (`apps/ai/urls.py`):
```python
path("mcp/creative-juices/", CreativeJuicesLandingView.as_view())
path("mcp/creative-juices/manifest.json", CreativeJuicesManifestView.as_view())
path("mcp/creative-juices/README.md", CreativeJuicesReadmeView.as_view())
```

**Deployment**: See `apps/ai/mcp/DEPLOYMENT.md` for full Django deployment guide

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

2. **QuickBooks Client**: `apps/integration/quickbooks/client.py`
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

**QuickBooks Models** (`apps/integration/models/quickbooks.py`):
- `Organization`: B2B organization management
- `QuickBooksConnection`: OAuth tokens and company info
- `MCPSession`: Active MCP session tracking
- Stores access/refresh tokens, realm ID, company name
- Handles token refresh automatically

**AI Models** (`apps/ai/models/`):
- `ChatSession`: Chat conversation tracking
- `ChatMessage`: Individual chat messages
- `ChatFeedback`: User feedback on responses

**Common Models** (`apps/common/models/`):
- `User`: Custom user model
- `Address`: Shared address functionality
- `Team`: Team/group management

## Creative Juices MCP Deep Dive

### Design Philosophy

**Concrete Over Abstract**:
- Uses tangible, everyday words rather than abstract concepts
- Larger conceptual gap = stronger creative reframing effect
- Examples: "baking-shoe" vs "crystallize-entropy"

**Three-Stage Creative Process**:
1. **Early stage**: Gentle nudges with familiar concepts (inspiring)
2. **Stuck stage**: Dramatic shocks with intense concepts (out-of-the-box)
3. **Validation stage**: Strategic frameworks for reality-testing (Musk frameworks)

**Historical Dimension**:
- Spans human development: primitive → ancient → modern → futuristic
- Maximizes metaphorical range across all domains

### Word Lists

**Inspiring Category** (300+ words):
- Human actions: painting, baking, melting, climbing
- Animal behaviors: flying, burrowing, nesting
- Natural elements: rain, tree, seed, rock
- Primitive tools: hammerstone, hide, gourd

**Out-of-the-Box Category** (250+ words):
- Destructive actions: crushing, burning, exploding
- Predatory behaviors: hunting, stalking, swarming
- Sci-fi technology: teleporting, warping, cloaking
- Alien biology: spore, tentacle, exoskeleton

**Strategic Frameworks**:
- First Principles: Challenge assumptions
- Limit Thinking: Scale to extremes
- Platonic Ideal: Start with perfection
- Five-Step Optimization: Question→Delete→Optimize→Accelerate→Automate

### Technical Characteristics

- **No external dependencies**: Uses Python stdlib `random` only
- **No authentication**: Stateless tools with no credentials
- **No user data**: Nothing stored or transmitted
- **Fully local operation**: No external API calls
- **No configuration needed**: Works immediately with built-in word lists

## Finding What You Need - Quick Reference

### Adding New MCP Servers

1. **Create server file**: `apps/ai/mcp/{name}_server.py`
   - Use FastMCP framework with `@mcp.tool()` decorators
   - Define tools with type hints for automatic schema generation

2. **Create Django views**: `apps/ai/views/mcp_views.py`
   - Add views for landing page, manifest, README
   - Include CORS headers for manifest.json

3. **Add URL routes**: `apps/ai/urls.py`
   - Map `/mcp/{name}/` to landing page
   - Map `/mcp/{name}/manifest.json` to manifest
   - Map `/mcp/{name}/README.md` to documentation

4. **Create web assets**:
   - `{name}_web.html` - Landing page
   - `{name}_manifest.json` - MCP manifest
   - `{NAME}_README.md` - Installation guide

5. **Write tests**:
   - MCP tests: `apps/ai/tests/test_mcp_{name}.py`
   - Test all tools and edge cases

6. **Document deployment**:
   - Add deployment guide to `apps/ai/mcp/DEPLOYMENT.md`
   - Update spec in `docs/specs/`

### Adding New QuickBooks Operations

1. **Define the tool**: `apps/ai/mcp/quickbooks_server.py`
   - Add `@mcp.tool()` decorated function
   - Use type hints for parameter validation

2. **Add API method**: `apps/integration/quickbooks/client.py`
   - Implement async method
   - Handle QuickBooks API specifics

3. **Write tests**:
   - MCP tests: `apps/ai/tests/test_mcp_quickbooks.py`
   - Integration tests: `apps/integration/quickbooks/tests/`

### Common File Locations

**Configuration**:
- Environment variables: `.env.local` (copy from `.env.example`)
- Django settings: `settings/` directory
- QuickBooks config: `settings/third_party.py`

**URLs and Routing**:
- Main URLs: `settings/urls.py`
- AI/MCP URLs: `apps/ai/urls.py`
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

**Documentation**:
- Specs: `docs/specs/` - Feature specifications
- Guides: `docs/guides/` - How-to guides
- Advanced: `docs/advanced/` - Deep dives
- Examples: `docs/examples/` - Code examples

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

### MCP Request Flow (QuickBooks)

1. **MCP Client** → connects to server via stdio
2. **quickbooks_server.py** → receives tool call request
3. **Authentication** → validates API key from request
4. **Tool Router** → matches tool name to handler
5. **QuickBooks Client** → makes async API request
6. **Token Refresh** → automatic if token expired
7. **Response** → formatted and returned to MCP client

### MCP Request Flow (Creative Juices)

1. **MCP Client** → connects to server via stdio
2. **creative_juices_server.py** → receives tool call request
3. **Tool Router** → matches tool name (get_inspiration, think_outside_the_box, reality_check)
4. **Word Selection** → random selection from appropriate category
5. **Response** → formatted sparks/questions with instructions

### OAuth Connection Flow

1. **User clicks "Connect"** → `/api/quickbooks/connect/`
2. **OAuth URL built** → `apps/integration/quickbooks/oauth_views.py:26`
3. **QuickBooks auth** → User authorizes in QuickBooks
4. **Callback received** → `/api/quickbooks/callback/`
5. **Tokens exchanged** → `apps/integration/quickbooks/oauth_views.py:45`
6. **Connection saved** → `QuickBooksConnection` model
7. **MCP enabled** → Server can now access QuickBooks data

### Django Web Deployment Flow

1. **User requests URL** → https://ai.yuda.me/mcp/creative-juices
2. **Django routing** → `settings/urls.py` → `apps/ai/urls.py`
3. **View executed** → `CreativeJuicesLandingView.get()`
4. **File loaded** → `apps/ai/mcp/creative_juices_web.html`
5. **Response sent** → HTML content with proper content-type

## Common Tasks Quick Reference

### Daily Development

```bash
# Start Django dev server
uv run python manage.py runserver

# Start QuickBooks MCP server
uv run python -m apps.ai.mcp.quickbooks_server

# Start Creative Juices MCP server
uv run python -m apps.ai.mcp.creative_juices_server

# Django shell with all models loaded
uv run python manage.py shell

# Run tests with proper settings
DJANGO_SETTINGS_MODULE=settings pytest

# Run specific test file
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_creative_juices.py -v
```

### Code Quality

```bash
# Format code (MUST run before committing)
uv run black . && uv run isort . --profile black

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

### MCP Testing

```bash
# Test QuickBooks MCP server locally
uv run python -m apps.ai.mcp.quickbooks_server

# Test Creative Juices MCP server
uv run python -m apps.ai.mcp.creative_juices_server

# Test with MCP Inspector
mcp-inspector uv run python -m apps.ai.mcp.creative_juices_server

# Run MCP tests
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_quickbooks.py -v
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_creative_juices.py -v
```

### Deployment

```bash
# Collect static files
uv run python manage.py collectstatic

# Run production server
uv run gunicorn settings.wsgi:application --bind 0.0.0.0:8000

# Test MCP endpoints
curl https://ai.yuda.me/mcp/creative-juices
curl https://ai.yuda.me/mcp/creative-juices/manifest.json
```

### Render MCP Deployment

The Render MCP provides tools to manage Render.com deployments directly from Claude:

```bash
# List workspaces
mcp__render__list_workspaces

# Select workspace (required before other operations)
mcp__render__select_workspace(ownerID="tea-...")

# List existing services
mcp__render__list_services

# Create PostgreSQL database
mcp__render__create_postgres(
    name="cuttlefish-db",
    plan="free",  # or basic_256mb, pro_4gb, etc.
    region="oregon",
    version=16
)

# Create web service
mcp__render__create_web_service(
    name="cuttlefish",
    repo="https://github.com/yudame/cuttlefish",
    branch="main",
    runtime="python",
    buildCommand="./build.sh",
    startCommand="gunicorn settings.wsgi:application",
    plan="starter",
    region="oregon",
    envVars=[
        {"key": "DEBUG", "value": "False"},
        {"key": "DATABASE_URL", "value": "postgres://..."},
        # Add more env vars as needed
    ]
)

# Update environment variables (merges with existing by default)
mcp__render__update_environment_variables(
    serviceId="srv-...",
    envVars=[{"key": "SECRET_KEY", "value": "new-value"}],
    replace=False  # Set to True to replace all env vars
)

# Get deployment status
mcp__render__get_deploy(serviceId="srv-...", deployId="dep-...")

# View logs
mcp__render__list_logs(
    resource=["srv-..."],
    limit=50,
    type=["build", "app"]  # Filter by log type
)

# Get service metrics
mcp__render__get_metrics(
    resourceId="srv-...",
    metricTypes=["cpu_usage", "memory_usage", "http_request_count"],
    startTime="2024-01-01T00:00:00Z",
    endTime="2024-01-01T23:59:59Z"
)
```

**Important Notes**:
- Always select a workspace before performing operations
- Environment variables are merged by default (use `replace=True` to replace all)
- Database URLs are automatically provided by Render when linking services
- Health checks should be defined at `/health/` endpoint
- The build.sh script must use `uv pip install . --system` for Render compatibility

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

### Why Django for MCP Hosting?

- Serves dynamic content and static assets
- OAuth integration for QuickBooks
- Centralized deployment and monitoring
- CORS handling for manifest.json
- Future extensibility (analytics, user management)

## Future: Desktop Agent Application

### Overview

Voice-driven desktop application for creating mini-applications through AI interaction.

**Spec Location**: `docs/specs/DESKTOP_AGENT_SPEC.md`

**Technology Stack**:
- **Desktop**: Tauri (Rust + TypeScript)
- **Backend**: Django REST API
- **Voice**: Whisper (transcription) + OpenAI TTS
- **AI**: Claude API with MCP tools

**Key Features**:
1. Voice input for natural interaction
2. Real-time visual feedback during app generation
3. Voice summaries of agent activity
4. File system access via Tauri
5. Local and remote tool execution

**Status**: Specification complete, implementation pending

## Next Steps for New Features

1. **Identify the appropriate app** for your feature
2. **Create models** with appropriate behavior mixins
3. **Write tests first** following TDD principles
4. **Implement views** (HTMX for web, ViewSets for API, MCP for tools)
5. **Create templates** in the centralized directory (if web UI)
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
- **MCP servers use FastMCP**: Follow FastMCP patterns for tool definitions

## Documentation Deep Dives

The `docs/` directory contains comprehensive guides for specific topics:

### Essential Reading Before Starting

- **docs/ARCHITECTURE.md** - System design overview
- **docs/REPO_MAP.md** - Codebase navigation
- **CLAUDE.md** - Commands and guidelines
- **docs/specs/** - Feature specifications (QuickBooks, Creative Juices, Desktop Agent)

### Feature Development Guides

**Building Models**:
- **docs/MODEL_CONVENTIONS.md** - Naming, structure, relationships
- **docs/BEHAVIOR_MIXINS.md** - Reusable model behaviors

**Frontend Implementation**:
- **docs/HTMX_INTEGRATION.md** - HTMX patterns
- **docs/TEMPLATE_CONVENTIONS.md** - Template organization
- **docs/VIEW_CONVENTIONS.md** - View class patterns
- **docs/MODAL_PATTERNS.md** - Modal dialogs
- **docs/TAILWIND_V4.md** - Styling with Tailwind

**Testing Strategy**:
- **docs/advanced/TEST_CONVENTIONS.md** - Test organization
- **docs/advanced/E2E_TESTING.md** - Browser tests
- **docs/advanced/BROWSER_TESTING.md** - Automation framework

**MCP Development**:
- **apps/ai/mcp/DEPLOYMENT.md** - Django deployment guide
- **docs/specs/CREATIVE_JUICES_MCP.md** - Creative Juices spec
- Review existing servers for patterns

### Setup and Configuration

- **docs/guides/SETUP_GUIDE.md** - Environment setup
- **docs/guides/CONTRIBUTING.md** - Contribution workflow

### Code Examples

- **docs/examples/modal_example_view.py** - Modal implementation
- **docs/examples/item_list_example.html** - List view with pagination
- **apps/ai/mcp/creative_juices_server.py** - Complete MCP server example

## Debugging Guide

### Common Issues and Solutions

**MCP Server Won't Start**:
- Check: Environment variables in `.env.local`
- Verify: PostgreSQL is running (`psql -l`)
- Ensure: Virtual environment activated

**OAuth Flow Fails**:
- Check: Redirect URI matches QuickBooks app settings
- Verify: `QUICKBOOKS_SANDBOX_MODE` matches environment
- Debug: Check logs in `apps/integration/quickbooks/oauth_views.py`

**Django 404 on MCP URLs**:
- Check: Django routing in `settings/urls.py` includes `apps/ai/urls.py`
- Verify: Views imported in `apps/ai/views/__init__.py`
- Test: `uv run python manage.py runserver` and visit URL

**Creative Juices Not Working**:
- Check: Word lists loaded correctly in `creative_juices_words.py`
- Verify: FastMCP installed (`uv add fastmcp`)
- Test: Run with debug logging enabled

### Key Debug Points

**Enable Debug Logging**:
```python
# In MCP server files
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

**Test Django Views**:
```python
# Django shell
from django.test import Client
c = Client()
response = c.get('/ai/mcp/creative-juices/')
print(response.status_code, response.content[:100])
```

## Context Loaded - Ready for Instructions

✅ **Context successfully loaded.** The assistant now has comprehensive understanding of:
- The full scope of Cuttlefish: MCP servers, Django web app, future desktop agent
- QuickBooks MCP integration with OAuth and async API
- Creative Juices MCP with randomness tools and web hosting
- Django architecture with behavior mixins and HTMX
- Where to find all major components
- How data flows through the system
- Common patterns and conventions
- Testing and debugging approaches
- Deployment strategies for MCP servers via Django

**The assistant is now waiting for your specific instructions.**

You can ask the assistant to:
- Add new MCP servers or tools
- Enhance existing MCP functionality
- Fix bugs in QuickBooks or Creative Juices
- Improve the OAuth flow
- Add new Django views or API endpoints
- Create web interfaces with HTMX
- Write tests for new features
- Debug existing issues
- Prepare for Desktop Agent implementation
- Deploy MCP servers via Django
- Or any other development task

This primer provides the essential context for understanding and contributing to the Cuttlefish AI integration platform. The architecture emphasizes clean separation of concerns, MCP protocol standards, Django web hosting, and comprehensive testing while providing a solid foundation for AI-assisted integrations and creative tools.
