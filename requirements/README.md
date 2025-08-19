# Requirements Directory

This project uses **uv** for dependency management with `pyproject.toml` as the single source of truth.

## Quick Start

```bash
# Install all dependencies (including dev, test, e2e)
uv sync --all-extras

# Install only production dependencies
uv sync

# Install with specific extras
uv sync --extra dev --extra test
```

## Files in this Directory

- `README.md` - This file
- `setup.sh` - Quick setup script for new developers
- `export.sh` - Export requirements.txt for legacy deployment platforms

## Dependency Management

All dependencies are defined in `/pyproject.toml`:
- **[project.dependencies]** - Production dependencies
- **[project.optional-dependencies.dev]** - Development tools
- **[project.optional-dependencies.test]** - Testing libraries
- **[project.optional-dependencies.e2e]** - End-to-end testing tools

The `uv.lock` file in the root directory ensures reproducible installs.

## Common Tasks

### Adding Dependencies

```bash
# Add a production dependency
uv add package-name

# Add a development dependency
uv add --dev package-name

# Add to a specific extra group
uv add --optional test package-name
```

### Updating Dependencies

```bash
# Update all dependencies
uv lock --upgrade

# Update a specific package
uv add package-name --upgrade-package package-name
```

### For Deployment

Most modern platforms support `pyproject.toml` directly. If you need a `requirements.txt`:

```bash
# Export production dependencies
uv export --no-dev > requirements.txt

# Export all dependencies
uv export > requirements-all.txt
```
