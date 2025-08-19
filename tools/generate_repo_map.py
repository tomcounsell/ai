#!/usr/bin/env python
"""
Generate a repository map for the Django Project Template.
This script creates a detailed Markdown visualization of the project structure.
"""

import os
import sys
from pathlib import Path

# Project root directory
ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "docs"


def generate_directory_tree(directory, indent=0, exclude_patterns=None, max_depth=4):
    """Generate a Markdown tree representation of the directory structure."""
    if exclude_patterns is None:
        exclude_patterns = [
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            ".mypy_cache",
            "*.pyc",
            "*.pyo",
            "*.pyd",
            "staticfiles",
            "static/node_modules",
            "*.lock.txt",
            "*.lock",
            "node_modules",
            "docs/sphinx_docs",
            "docs/sphinx_docs/**",
            ".DS_Store",
            ".idea",
            ".repo_map_cache.db",
            ".repo_map_structure.json",
            ".pytest_cache",
            "*_cache",
            "*_cache/",
            ".*_cache/",
        ]

    def should_exclude(path):
        """Check if a path should be excluded based on patterns."""
        path_str = str(path)
        rel_path = path.relative_to(ROOT_DIR)
        rel_path_str = str(rel_path)

        # Special cases for cache-related files and directories
        if path.is_dir():
            # Exclude any directory ending with _cache or .cache
            if path.name.endswith("_cache") or path.name.endswith(".cache"):
                return True
            # Also exclude __pycache__ directories at any level
            if path.name == "__pycache__":
                return True
        elif path.is_file():
            # Exclude any cache-related files
            if "cache" in path.name.lower() or path.name.endswith(".db"):
                return True

        for pattern in exclude_patterns:
            # File extension pattern
            if pattern.startswith("*.") and path.is_file():
                if path.suffix == "." + pattern[2:]:
                    return True
            # Directory pattern with wildcards
            elif pattern.endswith("/**"):
                base_dir = pattern[:-3]
                if rel_path_str.startswith(base_dir):
                    return True
            # Wildcard patterns
            elif pattern.startswith("*") and pattern.endswith("/"):
                suffix = pattern[1:-1]
                if path.is_dir() and path.name.endswith(suffix):
                    return True
            # Exact directory or file match
            elif pattern == path.name or pattern == rel_path_str:
                return True
            # Simple substring match (use with caution)
            elif pattern in path_str:
                return True

        # Special case for sphinx_docs directory
        if "sphinx_docs" in path_str:
            return True

        return False

    result = ""
    prefix = "  " * indent

    # Get all items in the directory, sorted
    items = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))

    for item in items:
        if should_exclude(item):
            continue

        if item.is_dir():
            # Directory - print with trailing slash
            result += f"{prefix}- **{item.name}/**\n"

            # Only recurse if we haven't hit the max depth
            if indent < max_depth:
                subdirectory_tree = generate_directory_tree(
                    item, indent + 1, exclude_patterns, max_depth
                )
                if subdirectory_tree:  # Only include if it has content
                    result += subdirectory_tree
            elif any(not should_exclude(p) for p in item.iterdir()):
                # If at max depth but directory has content, add a note
                result += f"{prefix}  - ... (additional files)\n"
        else:
            # File - print name only
            result += f"{prefix}- {item.name}\n"

    return result


def get_app_descriptions():
    """Return descriptions for each major app in the project."""
    descriptions = {
        "apps": "Django application modules",
        "apps/ai": "AI integration components and services for machine learning features",
        "apps/api": "REST API endpoints and serializers for third-party integrations",
        "apps/common": "Shared models, utilities, and behaviors used across the project",
        "apps/communication": "Email, SMS, and notification services for user communications",
        "apps/integration": "Third-party service integrations (AWS, Stripe, Twilio) with their clients and shortcuts",
        "apps/public": "Public-facing views and templates for user interfaces",
        "apps/staff": "Staff-only views and features for administrative functions",
        "docs": "Project documentation including architecture, conventions, and guides",
        "settings": "Django configuration modules for different environments",
        "static": "Static files (CSS, JS, images) for frontend rendering",
        "templates": "HTML templates using Django template language with HTMX integration",
        "tools": "Development utilities for testing, documentation, and other tasks",
    }
    return descriptions


def get_key_file_insights():
    """Return insights about key files in the project."""
    insights = {
        "manage.py": "Django's command-line utility for administrative tasks",
        "pyproject.toml": "Project configuration and dependency settings",
        "CLAUDE.md": "Instructions for Claude AI assistant to help with the project",
        "Dockerfile": "Container definition for deploying the application",
        "docker-compose.yml": "Multi-container Docker configuration",
        "requirements/base.txt": "Core Python dependencies for all environments",
        "requirements/dev.txt": "Additional dependencies for development environments",
        "requirements/prod.txt": "Dependencies specific to production environments",
        "settings/base.py": "Base Django settings shared across all environments",
        "settings/local_template.py": "Template for local development settings",
        "settings/production.py": "Production-specific Django settings",
        "apps/common/behaviors/timestampable.py": "Mixin that adds created_at and updated_at fields",
        "apps/common/behaviors/publishable.py": "Mixin for content publication workflow",
        "apps/common/behaviors/authorable.py": "Mixin for tracking content authors",
        "apps/common/models/user.py": "Custom user model extending Django's AbstractUser",
        "apps/public/views/helpers/htmx_view.py": "Base view for HTMX-based interactive components",
        "apps/public/views/helpers/main_content_view.py": "Base view for standard page rendering",
        "conftest.py": "Pytest configuration and shared fixtures",
    }
    return insights


def generate_repo_map():
    """Generate the repository map Markdown file."""
    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Generating repository map in {OUTPUT_DIR}...")

    # Generate basic content
    content = "# Repository Map\n\n"
    content += "This file contains a visualization of the Django Project Template's structure and architecture.\n\n"
    content += "To regenerate this file, run:\n\n```bash\npython tools/generate_repo_map.py\n```\n\n"

    # Add project overview
    content += "## Project Overview\n\n"
    content += "Django Project Template is a comprehensive starter template for Django web applications "
    content += "that provides a structured architecture, behavior mixins, and integrated tools for rapid development. "
    content += "It features a modular design with separate apps for different concerns, HTMX integration for interactive UIs without "
    content += "heavy JavaScript, and comprehensive testing utilities.\n\n"

    # Add project structure section
    content += "## Project Structure\n\n"
    content += "This is a high-level overview of key project files and directories. Some deeper subdirectories are omitted for clarity.\n\n"
    content += generate_directory_tree(ROOT_DIR, max_depth=4)

    # Add component descriptions
    content += "\n## Key Components\n\n"

    descriptions = get_app_descriptions()

    # Add app descriptions in a hierarchical list with enhanced descriptions
    content += "- **apps/** - " + descriptions["apps"] + "\n"
    content += "  - **ai/** - " + descriptions["apps/ai"] + "\n"
    content += "  - **api/** - " + descriptions["apps/api"] + "\n"
    content += "  - **common/** - " + descriptions["apps/common"] + "\n"
    content += "  - **communication/** - " + descriptions["apps/communication"] + "\n"
    content += "  - **integration/** - " + descriptions["apps/integration"] + "\n"
    content += "  - **public/** - " + descriptions["apps/public"] + "\n"
    content += "  - **staff/** - " + descriptions["apps/staff"] + "\n\n"
    content += "- **docs/** - " + descriptions["docs"] + "\n"
    content += "- **settings/** - " + descriptions["settings"] + "\n"
    content += "- **static/** - " + descriptions["static"] + "\n"
    content += "- **templates/** - " + descriptions["templates"] + "\n"
    content += "- **tools/** - " + descriptions["tools"] + "\n\n"

    # Add key files section with insights
    content += "## Key Files\n\n"
    insights = get_key_file_insights()
    for file_path, description in sorted(insights.items()):
        content += f"- **{file_path}** - {description}\n"
    content += "\n"

    # Add architecture section
    content += "## Architecture\n\n"
    content += "The project follows a modular architecture with clear separation of concerns:\n\n"
    content += "1. **Core Models & Behaviors** (`apps/common`) - Provides base models and reusable behaviors\n"
    content += (
        "2. **API Layer** (`apps/api`) - REST endpoints using Django REST Framework\n"
    )
    content += "3. **Public UI** (`apps/public`) - User-facing views using HTMX for interactivity\n"
    content += (
        "4. **Admin Interface** (`apps/staff`) - Staff-only views and functionality\n"
    )
    content += "5. **Integrations** (`apps/integration`) - Third-party service integrations\n\n"

    content += "### Behavior Mixins\n\n"
    content += "The project extensively uses behavior mixins for common model functionality:\n\n"
    content += "- **Timestampable** - Adds `created_at` and `updated_at` fields\n"
    content += "- **Authorable** - Tracks content authors and contributors\n"
    content += "- **Publishable** - Provides publishing workflow states\n"
    content += "- **Permalinkable** - Adds URL-friendly slugs to models\n"
    content += (
        "- **Expirable** - Adds functionality for content with expiration dates\n"
    )
    content += "- **Annotatable** - Allows adding notes/annotations to models\n\n"

    # Add technology stack section
    content += "## Technology Stack\n\n"
    content += "- **Backend**: Django 5.2\n"
    content += "- **API**: Django REST Framework 3.16\n"
    content += "- **Frontend**: HTMX + Tailwind CSS v4\n"
    content += "- **Database**: PostgreSQL\n"
    content += "- **Admin Interface**: Django Unfold\n"
    content += "- **Testing**: pytest, Browser-Use for E2E testing\n"
    content += "- **Dependency Management**: uv (Rust-based Python package installer)\n"
    content += "- **Documentation**: Markdown + Sphinx\n\n"

    # Add dependency management section
    content += "## Dependency Management\n\n"
    content += "The project uses `uv` for dependency management with separate requirement files:\n\n"
    content += "- **base.txt** - Core dependencies for all environments\n"
    content += "- **dev.txt** - Additional development dependencies\n"
    content += "- **prod.txt** - Production-specific dependencies\n\n"
    content += "Lock files (**base.lock.txt**, **dev.lock.txt**, **prod.lock.txt**) ensure reproducible environments.\n\n"

    # Add environment variables section
    content += "## Environment Variables\n\n"
    content += "The application uses the following key environment variables:\n\n"
    content += "- **Database configuration** (via `DATABASE_URL`)\n"
    content += (
        "- **Secret keys** for security (e.g., `SECRET_KEY`, `CSRF_TRUSTED_ORIGINS`)\n"
    )
    content += "- **Integration API keys**:\n"
    content += "  - Stripe: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`\n"
    content += "  - AWS: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`\n"
    content += "  - Twilio: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`\n"
    content += "- **AI service configurations**: `OPENROUTER_API_KEY`\n\n"
    content += "For a complete list, refer to the `.env.example` file.\n\n"

    # Add development workflow section
    content += "## Development Workflow\n\n"
    content += "The project follows a test-driven development approach:\n\n"
    content += "1. Check documentation for existing patterns and conventions\n"
    content += "2. Write tests for new functionality\n"
    content += "3. Implement features until tests pass\n"
    content += "4. Run linters and type checkers\n"
    content += "5. Commit changes\n\n"
    content += (
        "For more details, see the [Contributing Guide](docs/guides/CONTRIBUTING.md).\n"
    )

    # Write to file
    md_path = OUTPUT_DIR / "REPO_MAP.md"
    with open(md_path, "w") as f:
        f.write(content)

    print(f"Repository map generated at {md_path}")
    return md_path


def main():
    """Main entry point."""
    try:
        # Generate the repository map
        output_file = generate_repo_map()
        print(f"Repository map generation complete. Output saved to {output_file}")
        return 0
    except Exception as e:
        print(f"Error generating repository map: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
