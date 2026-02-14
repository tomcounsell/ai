# Django Project Settings

This project uses a modular approach to organize Django settings, which makes them more maintainable and easier to understand.

## Structure

- `settings/__init__.py`: Main entry point that loads all settings modules in the correct order
- `settings/env.py`: Handles environment variables and deployment type
- `settings/base.py`: Core Django settings (apps, middleware, templates, REST framework, background tasks)
- `settings/database.py`: Database and cache configuration
- `settings/third_party.py`: External services and integrations
- `settings/logging.py`: Logging configuration for different environments
- `settings/local.py`: Local development overrides (not in git)
- `settings/local_template.py`: Template for local settings
- `settings/production.py`: Production-specific settings (DatabaseBackend for tasks, Supabase storage)
- `settings/asgi.py`: ASGI application entry point (for async features)
- `settings/wsgi.py`: WSGI application entry point

### Background Tasks (`TASKS` setting)

Django 6.0's native task framework is configured via the `TASKS` setting:

- **`settings/base.py`**: `ImmediateBackend` — runs tasks synchronously (dev/test)
- **`settings/production.py`**: `DatabaseBackend` via `django-tasks-db` — persists tasks to PostgreSQL, executed by `manage.py db_worker`

Task status values: `NEW`, `RUNNING`, `SUCCESSFUL`, `FAILED`

## Environment Variables

Configuration is primarily controlled through environment variables loaded from a `.env.local` file in the project root. See `.env.example` for the required variables.

## Local Development

1. Copy `.env.example` to `.env.local` and fill in your values
2. If needed, copy `settings/local_template.py` to `settings/local.py` for additional overrides

## Adding New Settings

- Place settings in the appropriate module file based on functionality
- For third-party packages, add settings to `third_party.py`
- For environment-specific overrides, use the appropriate settings file
- Always add new environment variables to `.env.example`
