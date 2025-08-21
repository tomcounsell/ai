# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Core Commands

### Initial Setup
```bash
# Quick automated setup (recommended)
source setup_local_env.sh

# Manual setup with uv
uv venv && source .venv/bin/activate
uv sync --all-extras
cp .env.example .env.local
createdb quickbooks
uv run python manage.py migrate
uv run python manage.py createsuperuser
```

### Daily Development
```bash
# Django development server
uv run python manage.py runserver

# MCP server
uv run python -m apps.ai.mcp.quickbooks_server

# Django shell
uv run python manage.py shell

# Database migrations
uv run python manage.py makemigrations
uv run python manage.py migrate
```

### Testing
```bash
# Run all tests
DJANGO_SETTINGS_MODULE=settings pytest

# Run specific test file
DJANGO_SETTINGS_MODULE=settings pytest apps/common/tests/test_models/test_address.py -v

# Run with coverage
DJANGO_SETTINGS_MODULE=settings pytest --cov=apps --cov-report=html

# Run E2E tests
python tools/testing/browser_test_runner.py apps/**/tests/test_e2e_*.py

# Test MCP components
uv run python -m pytest apps/ai/tests/test_mcp_quickbooks.py
```

### Code Quality
```bash
# Install pre-commit hooks (required)
uv run pre-commit install

# Run all pre-commit hooks
uv run pre-commit run --all-files

# Format code
uv run black .
uv run isort . --profile black

# Linting
uv run flake8 . --max-line-length=88 --extend-ignore=E203,W503,E501
```

### Package Management (MUST use uv)
```bash
# Add production dependency
uv add package-name

# Add dev dependency  
uv add --dev package-name

# Add to optional group
uv add --optional test package-name

# Sync dependencies
uv sync --all-extras
```

## High-Level Architecture

### Django App Dependency Graph
The project follows a layered architecture with clear dependencies:

```
apps/common/ (foundation - no dependencies)
    ├── apps/integration/ (external services)
    ├── apps/ai/ (MCP server, depends on integration for QuickBooks)
    ├── apps/api/ (REST endpoints)
    ├── apps/public/ (web UI with HTMX)
    └── apps/staff/ (admin tools)
```

### Settings Loading Order
Settings are modular and loaded in sequence via `settings/__init__.py`:
1. `env.py` - Detects environment (LOCAL/STAGE/PRODUCTION)
2. `base.py` - Core Django configuration
3. `database.py` - Database setup from DATABASE_URL
4. `third_party.py` - External service configs
5. `production.py` or `local.py` - Environment-specific overrides

### MCP Server Architecture
The QuickBooks MCP implementation spans multiple apps:
- **apps/ai/mcp/**: MCP protocol server and tools
  - `quickbooks_server.py`: Main MCP server implementation
  - `quickbooks_tools.py`: Tool definitions and schemas
- **apps/integration/quickbooks/**: QuickBooks API integration
  - `client.py`: Async QuickBooks API client with OAuth
- **apps/integration/models/**: Integration models
  - `quickbooks.py`: Organization, QuickBooksConnection, MCPSession models
- **apps/ai/models/**: AI-specific models (chat sessions, feedback)

### Template and Frontend Architecture
- **Single template location**: `apps/public/templates/`
- **HTMX-first approach**: Dynamic interactions without heavy JavaScript
- **View pattern**: `MainContentView` for pages, `HTMXView` for partials
- **CSS**: Tailwind v4 via django-tailwind-cli

### Behavior Mixin Pattern
Common model functionality via mixins in `apps/common/behaviors/`:
- Timestampable, Authorable, Publishable, Expirable
- Permalinkable, Locatable, Annotatable
- 100% test coverage requirement
- Used across all apps for consistency

## Critical Development Rules

### Package Management
- **ONLY use uv** - Never use pip directly or `uv pip install`
- Dependencies in `pyproject.toml` only
- Python >=3.11 required

### Database
- PostgreSQL required (JSON field support)
- Never use SQLite for tests
- Migrations require approval - don't run without permission

### Testing
- Write tests BEFORE features (TDD)
- Tests organized by type in `apps/{app_name}/tests/`
- Factory classes in `apps/common/tests/factories.py`

### Code Style
- Black formatter (line length 88)
- Type hints required
- Datetime fields end with `_at`
- Import order: stdlib, third-party, Django, local
- Pre-commit hooks must pass before committing

## QuickBooks MCP Integration

### OAuth Flow
1. Start at `/api/quickbooks/connect/`
2. User authorizes in QuickBooks
3. Tokens saved to `QuickBooksConnection` model
4. MCP server accesses organization's data

### Adding MCP Tools
1. Define schema in `apps/ai/mcp/quickbooks_tools.py`
2. Add to `QUICKBOOKS_TOOLS` list
3. Implement handler in `quickbooks_server.py`
4. Add method in `apps/integration/quickbooks/client.py`
5. Write tests in both `apps/ai/tests/` and `apps/integration/quickbooks/tests/`

### Environment Variables
Required in `.env.local`:
```
DATABASE_URL=postgres://$(whoami)@localhost:5432/quickbooks
DEPLOYMENT_TYPE=LOCAL
SECRET_KEY=your-secret-key
DEBUG=True
QUICKBOOKS_CLIENT_ID=your_client_id
QUICKBOOKS_CLIENT_SECRET=your_client_secret
QUICKBOOKS_WEBHOOK_TOKEN=webhook_token
QUICKBOOKS_SANDBOX_MODE=True
```