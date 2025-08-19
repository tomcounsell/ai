# Template Directory Structure

This directory contains all templates used throughout the application. Templates are organized in a hierarchical structure to promote reuse and maintainability.

## Organization

- `base.html` - Main layout template for full pages
- `partial.html` - Base template for HTMX partial updates
- `components/` - Reusable UI components
  - `_component_base.html` - Base template for all components
  - `cards/` - Card components (team cards, user cards, etc.)
  - `forms/` - Form input components (text inputs, checkboxes, etc.)
  - `lists/` - List components (team members list, etc.)
  - `modals/` - Modal dialog templates
- `layout/` - Page layout elements
  - `footer.html` - Site footer
  - `modals.html` - Modal containers
  - `navbar.html` - Main navigation bar
  - `messages/` - Notification/messaging templates
  - `nav/` - Navigation components (account menu, search, etc.)
- `pages/` - Full page templates
- `account/` - User account templates (login, password reset, etc.)
- `admin/` - Admin dashboard templates
- `teams/` - Team-related templates

## Template Types

1. **Page Templates** - Full HTML pages that extend `base.html`
2. **Component Templates** - Reusable UI elements that extend `components/_component_base.html`
3. **Partial Templates** - Templates for HTMX partial updates that extend `partial.html`

## Usage Guidelines

1. **Template Naming**:
   - Use lowercase, underscore-separated names
   - Group related templates in subdirectories
   - Component templates should describe their function (e.g., `text_input.html`)

2. **Inheritance**:
   - Page templates should extend `base.html`
   - Components should extend `components/_component_base.html` or be standalone
   - HTMX partial responses should extend `partial.html`

3. **HTMX Integration**:
   - Use the `layout/modals.html` template for modal containers
   - Use `layout/messages/toast.html` for toast notifications

## For more detailed guidelines, see:

- [TEMPLATE_CONVENTIONS.md](/docs/TEMPLATE_CONVENTIONS.md)
- [VIEW_CONVENTIONS.md](/docs/VIEW_CONVENTIONS.md)
- [HTMX_INTEGRATION.md](/docs/HTMX_INTEGRATION.md)
