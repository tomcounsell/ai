# Contributing to Django Project Template

Thank you for your interest in contributing to this Django project template! This document provides guidelines to ensure code quality and consistency.

## Development Philosophy

1. **Test-Driven Development**: We aim for 100% test coverage, starting with models and behavior mixins
2. **Clean Architecture**: Keep code modular with clear separation of concerns
3. **Simplicity Over Complexity**: Prefer simple, standard Django patterns over custom frameworks
4. **Documentation**: Document everything, especially behavior mixins and model conventions

## Getting Started

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/django-project-template.git
   cd django-project-template
   
   # Add original repository as upstream remote for future improvements
   git remote add upstream https://github.com/original/django-project-template.git
   ```

2. **Set up the environment**
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install uv
   uv sync --all-extras
   cp .env.example .env.local  # Edit with your private keys
   ```

3. **Install pre-commit hooks (REQUIRED)**
   ```bash
   # This ensures code quality for all commits
   uv run pre-commit install
   uv run pre-commit install --hook-type pre-push
   
   # Verify hooks are working
   uv run pre-commit run --all-files
   ```

4. **Run migrations and start the server**
   ```bash
   python manage.py migrate
   python manage.py runserver
   ```

5. **Create a feature branch**
   ```bash
   git checkout -b feature/descriptive-name
   ```

## Development Workflow

### Code Style and Quality

All code quality checks are **automatically enforced** by pre-commit hooks:

- **On every commit**: Black, isort, Flake8, Bandit, Django checks
- **On push**: Critical tests are run

Manual commands (if needed):
```bash
# Run all pre-commit checks
uv run pre-commit run --all-files

# Or run individual tools
uv run black .
uv run isort .
uv run flake8 .
uv run mypy .
```

If hooks block your commit, fix the issues and try again. Only bypass with `--no-verify` in emergencies.
- Use type hints for all function parameters and return values
- Follow the model conventions in [MODEL_CONVENTIONS.md](../MODEL_CONVENTIONS.md)
- Group imports: standard library, third-party, Django, local apps

### Testing

- Write tests before implementing features
- Run tests with coverage reporting:
  ```bash
  DJANGO_SETTINGS_MODULE=settings pytest --cov=apps
  ```
- Test isolated units with proper mocking
- For behavior mixins, create tests that verify all functionality
  ```bash
  python test_behaviors.py
  ```
- For model tests, verify field constraints and model methods
- Never use SQLite for tests since the app uses Postgres JSON fields

### Directory Structure

- Place templates in the root `templates/` directory
- Place static files in the root `static/` directory
- Organize apps by domain (common, public, api, etc.)
- Use behavior mixins from `apps.common.behaviors`

## Model Development

1. **Use Behavior Mixins**: Inherit from appropriate mixins in `apps.common.behaviors`
2. **Follow Naming Conventions**:
   - Boolean fields: start with `is_` or `has_`
   - Datetime fields: end with `_at`
   - Properties: nouns reflecting the value returned
   - Methods: verb phrases describing the action

3. **Document Models**:
   - Add docstrings explaining model purpose
   - List key attributes and properties
   - Document special behaviors

## Pull Request Process

1. **Ensure all tests pass** with 100% coverage for your changes
2. **Update documentation** relevant to your changes
3. **Update the [docs/TODO.md](docs/TODO.md)** to mark completed tasks
4. **Create a pull request** with a clear description of changes
5. **Address reviewer feedback** promptly

## Commit Messages

- Use the imperative mood ("Add feature" not "Added feature")
- First line: summary (50 chars max)
- Followed by blank line and detailed explanation if needed
- Reference issues: "Fixes #123" or "Relates to #456"

## High Priority Contributions

See [docs/TODO.md](../TODO.md) for our current priorities, including:
1. Documentation improvements
2. Performance optimizations
3. Accessibility enhancements
4. Admin interface improvements
5. API expansion

Thank you for contributing to making this project better!
