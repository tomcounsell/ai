# Repository Map

This file contains a visualization of the Django Project Template's structure and architecture.

To regenerate this file, run:

```bash
python tools/generate_repo_map.py
```

## Project Overview

Django Project Template is a comprehensive starter template for Django web applications that provides a structured architecture, behavior mixins, and integrated tools for rapid development. It features a modular design with separate apps for different concerns, HTMX integration for interactive UIs without heavy JavaScript, and comprehensive testing utilities.

## Project Structure

This is a high-level overview of key project files and directories. Some deeper subdirectories are omitted for clarity.

- **apps/**
  - **ai/**
    - **migrations/**
      - __init__.py
    - **models/**
      - __init__.py
    - **tests/**
      - __init__.py
      - test_app_config.py
    - **views/**
      - __init__.py
    - __init__.py
    - apps.py
    - README.md
    - urls.py
  - **api/**
    - **serializers/**
      - __init__.py
      - user.py
    - **tests/**
      - __init__.py
      - api_test_case.py
      - conftest.py
      - test_api_key.py
      - test_api_structure.py
      - test_image_api.py
      - test_simple_api.py
      - test_stripe_webhook.py
      - test_twilio_webhook.py
      - test_user_api.py
    - **views/**
      - **trash/**
      - __init__.py
      - api_key.py
      - image.py
      - stripe.py
      - twilio.py
      - user.py
    - __init__.py
    - README.md
    - urls.py
  - **common/**
    - **behaviors/**
      - **tests/**
        - __init__.py
        - test_behaviors.py
      - __init__.py
      - annotatable.py
      - authorable.py
      - expirable.py
      - locatable.py
      - permalinkable.py
      - publishable.py
      - timestampable.py
    - **forms/**
      - __init__.py
      - blog_post.py
    - **management/**
      - **commands/**
        - __init__.py
        - capture_screenshot.py
      - __init__.py
    - **migrations/**
      - 0001_initial.py
      - 0002_payment_currency_payment_stripe_customer_id_and_more.py
      - __init__.py
    - **models/**
      - __init__.py
      - address.py
      - api_key.py
      - background_job.py
      - blog_post.py
      - city.py
      - country.py
      - currency.py
      - document.py
      - email.py
      - image.py
      - note.py
      - payment.py
      - sms.py
      - subscription.py
      - team.py
      - upload.py
      - user.py
    - **serializers/**
      - __init__.py
    - **tests/**
      - **test_models/**
        - __init__.py
        - test_address.py
        - test_background_job.py
        - test_blog_post.py
        - test_city.py
        - test_country.py
        - test_currency.py
        - test_document.py
        - test_email.py
        - test_image.py
        - test_note.py
        - test_payment.py
        - test_sms.py
        - test_subscription.py
        - test_team.py
        - test_upload.py
        - test_user.py
        - test_user_stripe.py
      - __init__.py
      - behaviors.py
      - factories.py
      - test_admin.py
      - test_behaviors.py
      - test_error_handling.py
      - test_factories.py
      - test_form_validation.py
      - tests.py
    - **utilities/**
      - **compression/**
        - image_compresssion.py
      - **database/**
        - __init__.py
        - db.py
        - model_fields.py
      - **django/**
        - __init__.py
        - backends.py
        - middleware.py
      - **drf_permissions/**
        - __init__.py
        - api_key.py
      - **processing/**
        - __init__.py
        - english_language.py
        - multithreading.py
        - regex.py
        - serializers.py
        - unicode_tools.py
      - __init__.py
      - email.py
      - forms.py
      - logger.py
      - screenshots.py
    - **views/**
      - __init__.py
    - __init__.py
    - admin.py
    - admin_dashboard.py
    - apps.py
    - README.md
  - **communication/**
    - **migrations/**
    - **models/**
  - **integration/**
    - **aws/**
      - **tests/**
        - __init__.py
        - test_s3.py
        - test_shortcuts.py
      - __init__.py
      - README.md
      - s3.py
      - shortcuts.py
    - **loops/**
      - **tests/**
        - __init__.py
        - test_client.py
        - test_loops_debug.py
        - test_shortcuts.py
      - __init__.py
      - client.py
      - README.md
      - shortcuts.py
      - tests.py
    - **stripe/**
      - **tests/**
        - stripe_test_utils.py
        - test_client.py
        - test_shortcuts.py
      - client.py
      - README.md
      - shortcuts.py
      - webhook.py
    - **twilio/**
      - **tests/**
        - __init__.py
        - test_client.py
        - test_shortcuts.py
        - test_verification.py
      - __init__.py
      - client.py
      - README.md
      - shortcuts.py
      - verification.py
    - README.md
  - **public/**
    - **middleware/**
      - user_state.py
    - **templatetags/**
      - __init__.py
      - component_tags.py
    - **tests/**
      - **test_views/**
        - __init__.py
        - conftest.py
        - test_account_views.py
        - test_component_views.py
        - test_example_pages.py
        - test_html_rendering.py
        - test_htmx_views.py
        - test_main_content_view.py
        - test_navigation.py
        - test_oob_support.py
        - test_session_mixin.py
      - ai_test_utils.py
      - conftest.py
      - e2e_test_config.py
      - test_account_browser.py
      - test_account_settings_e2e.py
      - test_account_settings_form.py
      - test_admin_e2e.py
      - test_ai_browser_testing.py
      - test_e2e_basic.py
      - test_e2e_example.py
      - test_e2e_patterns.py
      - test_htmx_interactions.py
      - test_partials.py
      - test_static_files.py
      - test_template_paths.py
      - test_templates.py
    - **views/**
      - **components/**
        - __init__.py
        - account_menu.py
        - example_component.py
        - examples_view.py
        - footer.py
        - navbar.py
        - oob_examples.py
        - toast.py
      - **helpers/**
        - __init__.py
        - htmx_view.py
        - main_content_view.py
        - session_mixin.py
      - **teams/**
        - __init__.py
        - member_views.py
        - team_views.py
      - __init__.py
      - account.py
      - pages.py
    - __init__.py
    - context_processors.py
    - README.md
    - urls.py
  - **staff/**
    - **migrations/**
      - __init__.py
    - **models/**
      - __init__.py
    - **tests/**
      - __init__.py
    - **views/**
      - __init__.py
    - __init__.py
    - admin.py
    - apps.py
    - README.md
    - urls.py
  - __init__.py
- **docs/**
  - **advanced/**
    - AI_BROWSER_TESTING.md
    - BROWSER_TESTING.md
    - CICD.md
    - E2E_TESTING.md
    - HTMX_AND_RESPONSIVE_TESTING.md
    - SCREENSHOT_SERVICE.md
    - TEST_CONVENTIONS.md
    - TEST_TROUBLESHOOTING.md
  - **examples/**
    - item_list_example.html
    - list_items_partial.html
    - modal_example_view.py
  - **guides/**
    - CONTRIBUTING.md
    - example_unfold_admin.py
    - PYCHARM_CONFIG.MD
    - SETUP_GUIDE.md
    - TAILWIND_V4_MIGRATION_CHECKLIST.md
    - TAILWIND_V4_UPGRADE.md
  - **scripts/**
    - build.sh
    - build_all_docs.sh
    - build_docs.sh
    - generate_api_docs.py
    - install-hooks.sh
    - setup_local_env.sh
  - ARCHITECTURE.md
  - BEHAVIOR_MIXINS.md
  - ERROR_HANDLING.md
  - HTMX_INTEGRATION.md
  - MODAL_PATTERNS.md
  - MODEL_CONVENTIONS.md
  - REPO_MAP.md
  - TAILWIND_V4.md
  - TEMPLATE_CONVENTIONS.md
  - TODO.md
  - VIEW_CONVENTIONS.md
- **requirements/**
  - base.lock.txt
  - base.txt
  - dev.lock.txt
  - dev.txt
  - generate_deployment_requirements.sh
  - install.sh
  - MIGRATION.md
  - prod.lock.txt
  - prod.txt
  - README.md
  - test.sh
- **settings/**
  - **scheduler/**
    - __init__.py
    - beat.py
    - celery.py
  - __init__.py
  - asgi.py
  - base.py
  - database.py
  - env.py
  - local.py
  - local_template.py
  - logging.py
  - production.py
  - README.md
  - third_party.py
  - unfold.py
  - urls.py
  - wsgi.py
- **static/**
  - **assets/**
    - **img/**
      - logo-yudame.png
    - favicon.png
  - **css/**
    - **components/**
    - **dist/**
      - styles.css
    - **tailwind/**
    - base.css
    - content.txt
    - source.css
    - tailwind.css
  - **img/**
  - **js/**
    - base.js
- **templates/**
  - **account/**
    - **password/**
      - change.html
      - change_done.html
      - reset.html
      - reset_complete.html
      - reset_confirm.html
      - reset_done.html
      - reset_email.html
    - login.html
    - settings.html
  - **admin/**
    - **dashboard/**
      - recent_activity.html
      - teams_summary.html
      - users_summary.html
    - **team/**
      - info.html
      - members.html
    - **user/**
      - profile.html
      - security.html
  - **components/**
    - **cards/**
      - card_team.html
    - **common/**
      - 404.html
      - error_message.html
      - notification_toast.html
      - status_badge.html
    - **forms/**
      - action_button.html
      - checkbox.html
      - form.html
      - form_buttons.html
      - form_errors.html
      - form_user.html
      - radio_set.html
      - select.html
      - text_input.html
      - textarea.html
    - **layout/**
      - bento_grid.html
      - footer.html
      - landing_page.html
      - navbar.html
    - **lists/**
      - list_team_members.html
    - **modals/**
      - examples.html
      - modal_base.html
      - modal_confirm.html
      - modal_content.html
      - modal_dialog.html
      - modal_form.html
    - **oob/**
      - examples.html
    - _component_base.html
    - oob_wrapper.html
  - **layout/**
    - **alerts/**
      - alert.html
    - **messages/**
      - toast.html
    - **modals/**
      - modal_container.html
    - **nav/**
      - account_menu.html
      - active_nav.html
      - navbar.html
      - search.html
    - footer.html
    - modals.html
  - **pages/**
    - blog.html
    - blog_post.html
    - home.html
    - landing.html
    - pricing.html
  - **staff/**
  - **teams/**
    - team_confirm_delete.html
    - team_detail.html
    - team_form.html
    - team_list.html
  - base.html
  - error.html
  - examples.html
  - partial.html
  - README.md
  - swagger-ui.html
- **test_screenshots/**
  - login_error.png
  - login_page.png
- **tools/**
  - **testing/**
    - browser_test_runner.py
    - config.py
    - coverage_reporter.py
    - README.md
    - test_manager.py
  - generate_repo_map.py
- .bandit
- .coverage
- .coveragerc
- .env.example
- .env.local
- .flake8
- .pre-commit-config.yaml
- build.sh
- CLAUDE.md
- conftest.py
- debug.log
- docker-compose.yml
- Dockerfile
- LICENSE
- manage.py
- mypy.ini
- pyproject.toml
- README.md
- render.yaml
- requirements.txt
- setup_local_env.sh
- test_runner.py

## Key Components

- **apps/** - Django application modules
  - **ai/** - AI integration components and services for machine learning features
  - **api/** - REST API endpoints and serializers for third-party integrations
  - **common/** - Shared models, utilities, and behaviors used across the project
  - **communication/** - Email, SMS, and notification services for user communications
  - **integration/** - Third-party service integrations (AWS, Stripe, Twilio) with their clients and shortcuts
  - **public/** - Public-facing views and templates for user interfaces
  - **staff/** - Staff-only views and features for administrative functions

- **docs/** - Project documentation including architecture, conventions, and guides
- **settings/** - Django configuration modules for different environments
- **static/** - Static files (CSS, JS, images) for frontend rendering
- **templates/** - HTML templates using Django template language with HTMX integration
- **tools/** - Development utilities for testing, documentation, and other tasks

## Key Files

- **CLAUDE.md** - Instructions for Claude AI assistant to help with the project
- **Dockerfile** - Container definition for deploying the application
- **apps/common/behaviors/authorable.py** - Mixin for tracking content authors
- **apps/common/behaviors/publishable.py** - Mixin for content publication workflow
- **apps/common/behaviors/timestampable.py** - Mixin that adds created_at and updated_at fields
- **apps/common/models/user.py** - Custom user model extending Django's AbstractUser
- **apps/public/views/helpers/htmx_view.py** - Base view for HTMX-based interactive components
- **apps/public/views/helpers/main_content_view.py** - Base view for standard page rendering
- **conftest.py** - Pytest configuration and shared fixtures
- **docker-compose.yml** - Multi-container Docker configuration
- **manage.py** - Django's command-line utility for administrative tasks
- **pyproject.toml** - Project configuration and dependency settings
- **requirements/base.txt** - Core Python dependencies for all environments
- **requirements/dev.txt** - Additional dependencies for development environments
- **requirements/prod.txt** - Dependencies specific to production environments
- **settings/base.py** - Base Django settings shared across all environments
- **settings/local_template.py** - Template for local development settings
- **settings/production.py** - Production-specific Django settings

## Architecture

The project follows a modular architecture with clear separation of concerns:

1. **Core Models & Behaviors** (`apps/common`) - Provides base models and reusable behaviors
2. **API Layer** (`apps/api`) - REST endpoints using Django REST Framework
3. **Public UI** (`apps/public`) - User-facing views using HTMX for interactivity
4. **Admin Interface** (`apps/staff`) - Staff-only views and functionality
5. **Integrations** (`apps/integration`) - Third-party service integrations

### Behavior Mixins

The project extensively uses behavior mixins for common model functionality:

- **Timestampable** - Adds `created_at` and `updated_at` fields
- **Authorable** - Tracks content authors and contributors
- **Publishable** - Provides publishing workflow states
- **Permalinkable** - Adds URL-friendly slugs to models
- **Expirable** - Adds functionality for content with expiration dates
- **Annotatable** - Allows adding notes/annotations to models

## Technology Stack

- **Backend**: Django 5.2
- **API**: Django REST Framework 3.16
- **Frontend**: HTMX + Tailwind CSS v4
- **Database**: PostgreSQL
- **Admin Interface**: Django Unfold
- **Testing**: pytest, Browser-Use for E2E testing
- **Dependency Management**: uv (Rust-based Python package installer)
- **Documentation**: Markdown + Sphinx

## Dependency Management

The project uses `uv` for dependency management with separate requirement files:

- **base.txt** - Core dependencies for all environments
- **dev.txt** - Additional development dependencies
- **prod.txt** - Production-specific dependencies

Lock files (**base.lock.txt**, **dev.lock.txt**, **prod.lock.txt**) ensure reproducible environments.

## Environment Variables

The application uses the following key environment variables:

- **Database configuration** (via `DATABASE_URL`)
- **Secret keys** for security (e.g., `SECRET_KEY`, `CSRF_TRUSTED_ORIGINS`)
- **Integration API keys**:
  - Stripe: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
  - AWS: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`
  - Twilio: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`
- **AI service configurations**: `OPENROUTER_API_KEY`

For a complete list, refer to the `.env.example` file.

## Development Workflow

The project follows a test-driven development approach:

1. Check documentation for existing patterns and conventions
2. Write tests for new functionality
3. Implement features until tests pass
4. Run linters and type checkers
5. Commit changes

For more details, see the [Contributing Guide](docs/guides/CONTRIBUTING.md).
