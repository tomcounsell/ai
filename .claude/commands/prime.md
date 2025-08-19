# Django Project Template - Application Primer

## Project Overview
This is a modern Django application template designed as a production-ready foundation for building web applications. It emphasizes clean architecture, maintainability, and developer experience through modern Python tooling and best practices.

## Core Technology Stack
- **Framework**: Django 5.0+
- **Database**: PostgreSQL (required for JSON field support)
- **Package Management**: uv (modern Python package manager)
- **Frontend**: HTMX + Tailwind CSS v4
- **Testing**: pytest with factory_boy
- **Code Quality**: Black, isort, Ruff, pyright
- **Python**: 3.11+

## Application Architecture

### Modular App Structure
The application is organized into focused, single-responsibility Django apps:

```
apps/
├── common/      # Foundation layer - shared models, behaviors, utilities
├── public/      # Web UI layer - HTMX views, templates, user-facing features
├── api/         # REST API layer - JSON endpoints for programmatic access
├── integration/ # External services layer - third-party API integrations
└── ai/          # AI capabilities layer - LLM and ML model integrations
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

## Application Purpose

This template serves as a foundation for:
- **SaaS Applications**: Multi-tenant web services
- **Content Platforms**: Publishing and content management
- **API Services**: RESTful backends with web interfaces
- **Data-Driven Apps**: Applications requiring complex data models
- **AI-Enhanced Products**: Integration points for LLM and ML features

## Key Features Out-of-the-Box

### User Management
- Custom User model in `apps/common/`
- Authentication and authorization setup
- Profile and account management scaffolding

### Admin Interface
- Django admin customizations
- Model admin classes with filters and search
- Inline editing for related models

### API Foundation
- Django REST Framework integration
- Versioned API structure
- Authentication and permissions setup
- Serializer patterns for common operations

### Frontend Components
- Base templates with responsive layouts
- HTMX view classes for partial rendering
- Tailwind component library
- Form handling with Django forms

### Development Tools
- Comprehensive test suite structure
- Factory classes for test data
- Browser testing framework
- Visual regression testing setup

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

## Common Tasks Quick Reference

### Daily Development
```bash
uv run python manage.py runserver  # Start dev server
uv run python manage.py shell      # Django shell
DJANGO_SETTINGS_MODULE=settings pytest  # Run tests
```

### Code Quality
```bash
black . && isort .     # Format code
uv run ruff check .    # Lint code
uv run pyright         # Type check
```

### Database Operations
```bash
uv run python manage.py makemigrations  # Create migrations
uv run python manage.py migrate         # Apply migrations
uv run python manage.py dbshell        # Database shell
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

This primer provides the essential context for understanding and contributing to this Django application. The architecture emphasizes simplicity, maintainability, and developer productivity while providing a solid foundation for building modern web applications.
