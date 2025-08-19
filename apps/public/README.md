# Public App

## Purpose

The Public app handles all user-facing web interfaces, providing the front-end experience for the application. It manages templates, views, and static assets that create the public-facing website and authenticated user interfaces.

## Features

### Views

- **MainContentView**: Base view class for rendering templates with consistent handling
- **HTMXView**: Specialized view for HTMX interactions with OOB support
- **TeamSessionMixin**: Mixin for handling team-specific views and permissions
- **Account**: User authentication and account management views
- **Components**: Reusable UI components (to be replaced with standard templates)

### Templates

- **Base Templates**: Foundation templates (`_base.html`, `_partial.html`)
- **Layout**: Reusable page layout components (navbar, footer)
- **Pages**: Content pages like home and dashboard
- **Account**: Login, registration, and account settings templates
- **Messages**: Toast notifications and alerts

### Static Assets

- **CSS**: Base styling (will be migrated to Tailwind)
- **JavaScript**: Client-side functionality
- **Assets**: Images, favicons, and other static resources

### Middleware

- **UserState**: Manages user-specific state across requests

## Technical Approach

The Public app follows these architectural principles:

1. **HTMX-First**: Uses HTMX for dynamic interactions rather than client-side JavaScript
2. **Progressive Enhancement**: Works without JavaScript, enhanced with HTMX
3. **Server-Rendered**: Primarily server-rendered templates
4. **Partial Updates**: Support for partial page updates via HTMX

## Development Guidelines

- Place all new templates in the root `/templates` directory (not app-specific)
- Use HTMX for interactive features instead of custom JavaScript
- For view classes:
  - Use `MainContentView` for standard page rendering
  - Use `HTMXView` for HTMX-specific interactions with OOB support
  - Add `TeamSessionMixin` for views that need team context
- Use Tailwind for styling instead of custom CSS
- Avoid adding `<script>` tags to templates unless absolutely necessary

## Helpers Package

The `apps.public.helpers` package contains reusable view classes and mixins:

- `MainContentView`: Base view class for all template rendering
- `HTMXView`: Specialized view for HTMX requests with out-of-band support
- `SessionStateMixin`: Basic session state handling
- `TeamSessionMixin`: Team context and authorization
