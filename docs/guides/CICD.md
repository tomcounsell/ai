# CI/CD Configuration

This project uses Continuous Integration and Continuous Deployment workflows to automate testing, linting, building, and deployment processes.

## Pre-commit Hooks (Local Quality Gates)

The project uses [pre-commit](https://pre-commit.com/) to ensure code quality before changes are committed. These hooks run automatically on every commit and push.

### Installation

```bash
# Install pre-commit hooks (one-time setup)
uv run pre-commit install
uv run pre-commit install --hook-type pre-push

# Verify installation
uv run pre-commit --version
```

### Available Hooks

#### On Commit (Fast Checks)
- **Trailing Whitespace**: Removes trailing spaces
- **End of Files**: Ensures files end with a newline
- **File Size**: Prevents large files (>1MB) from being committed
- **Merge Conflicts**: Detects unresolved merge conflict markers
- **Black**: Python code formatter (88 char line length)
- **isort**: Sorts and organizes imports
- **Flake8**: Python linter with Django-specific plugins
- **Bandit**: Security vulnerability scanner
- **Django Upgrade**: Updates code to latest Django patterns
- **pyupgrade**: Upgrades Python syntax to 3.12+
- **Django Check**: Runs Django's system check framework
- **Migration Check**: Ensures no missing migrations

#### On Push (Comprehensive Checks)
- **Critical Tests**: Runs core test suite to prevent broken pushes

### Usage

```bash
# Run all hooks manually
uv run pre-commit run --all-files

# Run specific hook
uv run pre-commit run black --all-files
uv run pre-commit run flake8 --all-files

# Update hook versions to latest
uv run pre-commit autoupdate

# Skip hooks temporarily (use sparingly!)
git commit -m "Emergency fix" --no-verify
git push --no-verify

# Uninstall hooks
uv run pre-commit uninstall
```

### Configuration

The pre-commit configuration is in `.pre-commit-config.yaml`. Key settings:

```yaml
# Exclude patterns for all hooks
exclude: migrations|\.venv|staticfiles|node_modules

# Fail fast setting (stop on first failure)
fail_fast: false

# Default stages (when hooks run)
default_stages: [pre-commit, pre-push]
```

### Troubleshooting

**Hook failures blocking commit:**
```bash
# See detailed error output
uv run pre-commit run --verbose

# Fix formatting issues automatically
uv run black .
uv run isort .

# Then try committing again
git add -A && git commit
```

**Hooks running slowly:**
```bash
# Skip expensive hooks during development
SKIP=flake8,bandit git commit -m "WIP"

# Or temporarily disable specific hooks in .pre-commit-config.yaml
```

**Hooks not running:**
```bash
# Reinstall hooks
uv run pre-commit uninstall
uv run pre-commit install
```

## GitHub Actions

The project includes several GitHub Actions workflows that mirror and extend the pre-commit checks:

### 1. Test Workflow (`test.yml`)

- **Trigger**: Runs on pushes and pull requests to the `main` branch
- **Jobs**:
  - Spins up a PostgreSQL database service
  - Runs core tests with pytest to validate basic functionality

### 2. Lint Workflow (`lint.yml`)

- **Trigger**: Runs on pushes and pull requests to the `main` branch
- **Jobs**:
  - **Format Check**: Validates Black and isort formatting
  - **Lint**: Runs flake8 with Django plugins
  - **Type Check**: Runs mypy for type checking
- **Note**: Must pass same checks as local pre-commit hooks

### 3. Security Workflow (`security.yml`)

- **Trigger**: Runs on pushes and pull requests to the `main` branch
- **Jobs**:
  - **Bandit**: Scans for security vulnerabilities
  - **Safety**: Checks dependencies for known CVEs
- **Note**: More comprehensive than local pre-commit security checks

### 4. Documentation Workflow (`docs.yml`)

- **Trigger**: Runs on pushes and pull requests to the `main` branch when files in the `apps/` or `docs/` directories change
- **Jobs**:
  - Builds Sphinx documentation
  - Deploys documentation to GitHub Pages (only from the `main` branch)

## Setting Up CI/CD

### GitHub Actions

1. **GitHub Pages** (for documentation):
   - In your repository settings, enable GitHub Pages
   - Select the source as "GitHub Actions"

### Pre-commit Integration

1. **Team Setup**:
   ```bash
   # Add to your onboarding documentation
   git clone <repo>
   cd <repo>
   uv sync --all-extras
   uv run pre-commit install
   ```

2. **CI/CD Alignment**:
   - GitHub Actions use the same tools as pre-commit
   - Failures in pre-commit will also fail in CI
   - Fix issues locally before pushing

## Local CI Testing

To test CI workflows locally before pushing changes:

1. **Run Pre-commit Hooks**:
   ```bash
   # Simulates what happens on commit
   uv run pre-commit run --all-files
   ```

2. **Run Full Test Suite**:
   ```bash
   # Simulates GitHub Actions test workflow
   DJANGO_SETTINGS_MODULE=settings uv run pytest
   ```

3. **Check Security**:
   ```bash
   # Simulates security workflow
   uv run bandit -r apps/ settings/
   safety check
   ```

4. **Build Documentation**:
   ```bash
   # Simulates docs workflow
   cd docs/sphinx_docs
   make html
   ```

## Deployment Environments

The CI/CD setup supports multiple deployment environments:

- **Staging**: Automatically deployed from the `main` branch after all checks pass
- **Production**: Deployed from git tags after manual approval

## Best Practices

1. **Always install pre-commit hooks** after cloning the repository
2. **Fix issues locally** before pushing to save CI minutes
3. **Don't skip hooks** unless absolutely necessary
4. **Keep hooks updated** with `uv run pre-commit autoupdate`
5. **Match CI and local checks** to avoid surprises

## Workflow Diagram

```
Developer → Pre-commit Hooks → Git Commit → Push → GitHub Actions → Deploy
            ↓ (if fails)                      ↓ (if fails)
            Fix locally                       Fix and push again
```

## References

- [Pre-commit Documentation](https://pre-commit.com/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Black Documentation](https://black.readthedocs.io/)
- [Flake8 Documentation](https://flake8.pycqa.org/)
- [Bandit Documentation](https://bandit.readthedocs.io/)
