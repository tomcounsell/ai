# Template Conventions

This document outlines the conventions and best practices for templates in this Django project.

> **TIP:** Visit the UI Component Examples page at `/ui/examples/` to see a live demonstration of all available components.

## Base Templates

The project uses two primary base templates:

1. `apps/public/templates/base.html` - Main layout template for full pages
2. `apps/public/templates/partial.html` - Base template for HTMX partial updates

Additionally, the project uses specialized base templates for components:
- `apps/public/templates/components/_component_base.html` - Base template for UI components

### base.html

This template provides the complete HTML structure with:
- HTML5 doctype and responsive viewport
- Metadata for SEO and social sharing
- CSS imports (Tailwind CSS v4)
- JS dependencies (HTMX, HyperScript)
- Common layout elements (navbar, footer)
- Toast notification system
- Modal dialog system

```html
{% extends "base.html" %}

{% block title %}Page Title{% endblock %}

{% block content %}
  <!-- Your main content here -->
{% endblock %}
```

### partial.html

This template is designed for HTMX partial page updates without the full HTML structure:

```html
{% extends "partial.html" %}

{% block content %}
  <!-- Partial content here -->
{% endblock %}
```

For out-of-band updates, set `is_oob=True` in the view context:

```html
{% if is_oob %}
  <div id="notification-count" hx-swap-oob="true">{{ count }}</div>
{% endif %}
```

### Layout Organization

The `templates/layout/` directory contains layout elements that make up the page structure:

- `footer.html` - Site footer
- `modals.html` - Modal containers
- `navbar.html` - Main navigation bar
- `messages/` - Notification/toast templates
- `nav/` - Navigation components like navbar parts and account menu

## Design System and Color Palette

The project uses a minimalist design system with a focused color palette:

### Color System
- **Primary**: Deep navy blue from the theme palette:
  - `navy-900: #0a192f` - Primary buttons, footer background
  - `navy-800: #112240` - Button hover states
  - `navy-700: #1d3557` - Borders, focus rings, secondary accents
- **Accent**: Yellow (`accent: #ffd404`) used sparingly for important highlights and decorative elements
- **Grayscale**: Consistent gray shades for UI elements and text:
  - `gray-900` through `gray-700`: Primary and secondary text
  - `gray-600` through `gray-400`: Tertiary text, icons, and disabled states
  - `gray-300` through `gray-100`: Borders, dividers, and background variations
  - `gray-50`: Very light background for secondary content areas
- **Feedback**: Standard semantic colors for system feedback:
  - Red: Errors and destructive actions
  - Green: Success and completion states
  - Yellow: Warnings and attention states

### Standardized UI Elements

#### Buttons
- **Primary Buttons**:
  ```html
  class="inline-flex justify-center py-2 px-4 border border-transparent shadow-sm text-sm 
  font-medium rounded-md text-white bg-slate-900 hover:bg-slate-800 focus:outline-none 
  focus:ring-2 focus:ring-offset-2 focus:ring-slate-700"
  ```

- **Secondary/Gray Buttons**:
  ```html
  class="inline-flex justify-center py-2 px-4 border border-gray-300 shadow-sm text-sm 
  font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 focus:outline-none 
  focus:ring-2 focus:ring-offset-2 focus:ring-gray-500"
  ```

#### Links
- **Standard Links**: `class="text-slate-700 hover:text-slate-900"`
- **Subtle Links**: `class="text-gray-600 hover:text-slate-700 hover:underline"`

#### Cards/Panels
- **Standard Card**: `class="bg-white shadow rounded-lg p-6"`
- **Bordered Card**: `class="bg-white shadow rounded-lg overflow-hidden"` with inner container `class="border-l-4 border-slate-700 p-6"`

#### Form Elements
- **Form Layout**: Use `space-y-6` for vertical spacing and `grid` for multi-column layouts
- **Form Groups**: Apply consistent spacing with `class="mb-4"` or `class="space-y-2"`
- **Form Labels**: `class="block text-sm font-medium text-gray-700 mb-2"`
- **Form Inputs**: `class="block w-full rounded-md border-gray-300 shadow-sm focus:border-slate-700 focus:ring-slate-700 sm:text-sm"`

#### Badges/Pills
- **Status Badges**: `class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-slate-100 text-slate-800"`
- **Alternative Badges**: Use gray-scale for neutral states and standard semantic colors for others

### Design Principles
- **Minimalist**: Clean layouts with ample white space
- **Focused**: Clear hierarchy and limited color usage
- **Consistent**: Uniform spacing, typography, and interaction patterns
- **Accessible**: High contrast text and clear interactive elements

### Icons
- Font Awesome is used for all icons throughout the application
- Use `fas` prefix for solid icons (e.g. `<i class="fas fa-user"></i>`)
- Use `far` prefix for regular icons (e.g. `<i class="far fa-circle"></i>`)
- Use `fab` prefix for brand icons (e.g. `<i class="fab fa-github"></i>`)
- Add `fa-fw` class for fixed-width icons to ensure proper alignment
- Use `fa-lg`, `fa-2x`, etc. for larger icons when needed

### Page Structure Conventions
- Use `container mx-auto px-4 py-8` for main page containers
- Page titles with `text-2xl font-bold text-gray-900` and `mb-6`
- Section headers with `text-xl font-semibold text-gray-900 mb-4`
- Component headers with `text-lg font-medium text-gray-900 mb-4`

## Component System

Reusable UI components are organized in `templates/components/`:

- `forms/` - Form input components
- `modals/` - Modal dialog templates
- `buttons/` - Button components
- `cards/` - Card components
- `lists/` - List components

Components extend from `components/_component_base.html` which provides basic structure and utility blocks.

### UI Component Examples

A live, interactive showcase of all available UI components is available at `/ui/examples/` or by clicking the "UI Components" link in the footer. This page demonstrates:

- Form components (text inputs, textareas, checkboxes, selects, radio buttons, buttons)
- Card layouts
- Modal dialogs
- List displays
- Common UI elements (notifications, error messages)

Use this page as a reference when implementing interfaces to ensure consistent design across the application.

The examples page demonstrates the following standards:
- Proper page container and layout structure
- Standard headings and typography hierarchy
- Button styles for primary and secondary actions
- Card/panel layouts with consistent styling
- Form element styling and organization
- Consistent use of navy and grayscale color palette
- Modal component usage and styling
- Status badges and indicators

Access this resource at `/ui/examples/` to see live implementations of all styled components.

### Form Components

Form components provide consistent styling and error handling:

```html
<!-- Basic Input -->
<div class="sm:col-span-3">
  <label for="id_first_name" class="block text-sm font-medium text-gray-700">
    First Name
  </label>
  <div class="mt-1">
    <input type="text" name="first_name" id="id_first_name" value="{{ value|default:'' }}" 
      class="block w-full rounded-md border-gray-300 shadow-sm focus:border-slate-700 focus:ring-slate-700 sm:text-sm" />
  </div>
  {% if errors %}
    <p class="mt-2 text-sm text-red-600">{{ errors.0 }}</p>
  {% endif %}
</div>

<!-- Form Buttons -->
{% include "components/forms/form_buttons.html" with submit_text="Save Changes" %}
```

### Button Styles

The project uses consistent button styling:

```html
<!-- Primary Button -->
<button type="submit" class="inline-flex justify-center py-2 px-4 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-slate-900 hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-slate-700">
  Primary Action
</button>

<!-- Secondary Button -->
<button type="button" class="inline-flex justify-center py-2 px-4 border border-gray-300 shadow-sm text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-slate-500">
  Secondary Action
</button>
```

### Toast Notifications

Toast notifications use color-coded borders and icons:

```html
<div class="rounded-lg shadow-lg overflow-hidden bg-white border-l-4 border-green-500">
  <div class="p-4 flex items-start">
    <!-- Icon -->
    <div class="flex-shrink-0 mr-3">
      <svg class="h-5 w-5 text-green-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" />
      </svg>
    </div>
    <!-- Content -->
    <div class="flex-1">
      <p class="text-sm text-gray-800">Operation successful!</p>
    </div>
    <!-- Close Button -->
    <button class="ml-4 text-gray-400 hover:text-gray-500 focus:outline-none">
      <svg class="h-4 w-4" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
        <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
      </svg>
    </button>
  </div>
</div>
```

### Modal System

The project uses a CSS-based modal system with minimal JavaScript:

1. Modal containers are included in `base.html` automatically
2. Modal templates extend from `components/modals/modal_base.html`
3. Specialized modal types:
   - `modal_content.html` - For displaying content
   - `modal_form.html` - For forms
   - `modal_confirm.html` - For confirmations

Key features:
- CSS animations for smooth transitions
- Keyboard accessibility (Escape to close)
- Backdrop click to dismiss
- No JavaScript functions, only inline handlers
- HTMX integration for dynamic content loading

Opening a modal with HTMX:

```html
<button
  hx-get="{% url 'load_modal_content' %}"
  hx-target="#primary-modal-container"
  hx-swap="innerHTML"
>
  Open Modal
</button>
```

## Layout Patterns

### Main Content View

Use the `MainContentView` mixin for views that handle both full page and HTMX partial requests:

```python
class ProfileView(MainContentView):
    template_name = "pages/profile.html"
    partial_template_name = "partials/profile_content.html"
```

### Responsive Design

- Use Tailwind responsive prefixes (`sm:`, `md:`, `lg:`)
- Mobile-first approach (default styles are for mobile)
- Test all templates on multiple screen sizes

### Tailwind CSS Limitations with Django Templates

- **IMPORTANT**: Tailwind cannot recognize dynamic class names created with Django template syntax
- ❌ **Avoid**: `md:col-span-{% block content_width %}8{% endblock %}` 
- ✅ **Instead**: Include all potential variants: `md:col-span-8 md:col-span-9 md:col-span-10`
- ✅ **Alternative**: Use CSS custom properties or JavaScript for dynamic values
- This is because Tailwind performs static analysis at build time and cannot process Django template variables or blocks

**Note**: We've migrated from django-tailwind to django-tailwind-cli and upgraded to Tailwind CSS v4. This version uses CSS variables for theme customization in `static/css/source.css` instead of a separate `tailwind.config.js` file.

### Tailwind CSS Documentation

#### Core concepts
- [Styling with utility classes](https://tailwindcss.com/docs/styling-with-utility-classes)
- [Hover, focus, and other states](https://tailwindcss.com/docs/hover-focus-and-other-states)
- [Responsive design](https://tailwindcss.com/docs/responsive-design)
- [Dark mode](https://tailwindcss.com/docs/dark-mode)
- [Theme variables](https://tailwindcss.com/docs/theme)
- [Colors](https://tailwindcss.com/docs/colors)
- [Adding custom styles](https://tailwindcss.com/docs/adding-custom-styles)
- [Detecting classes in source files](https://tailwindcss.com/docs/detecting-classes-in-source-files)
- [Functions and directives](https://tailwindcss.com/docs/functions-and-directives)

## Best Practices

1. **Template Names**:
   - Use lowercase, underscore-separated names
   - No leading underscore in filenames (e.g., `base.html` not `_base.html`)
   - Group related templates in subdirectories

2. **Blocks**:
   - Use descriptive block names
   - Provide default content for optional blocks
   - Keep blocks focused on a single responsibility

3. **Context Variables**:
   - Document required and optional context variables using Django comment tags `{% comment %}...{% endcomment %}`
   - Use descriptive variable names
   - Provide sensible defaults with the `|default` filter

4. **Accessibility**:
   - Include proper ARIA attributes
   - Maintain proper heading hierarchy
   - Ensure sufficient color contrast
   - Support keyboard navigation

5. **Performance**:
   - Minimize template inheritance depth
   - Use template fragments for expensive operations
   - Leverage template caching for slow-changing content

## Django Template Tags

### Custom Template Tags

Custom template tags are defined in app-specific `templatetags` modules:

```html
{% load common_tags %}
{% user_display user %}
```

### Template Filters

Prefer Django's built-in filters when possible:

```html
{{ value|default:"empty" }}
{{ text|truncatewords:30 }}
```

## HTMX Integration

- Use `hx-boost` for page transitions
- Target specific elements with `hx-target`
- Use `hx-swap` to control the swap behavior
- Use `hx-trigger` to specify when the request occurs
- Add CSRF protection with the `{% django_htmx_script %}` tag

## Minimizing JavaScript

The project follows a "minimal JavaScript" approach:

- **Use modern HTML5 and CSS3**: Leverage CSS animations, transitions, and modern HTML features
- **Use inline handlers**: When JavaScript is necessary, use minimal inline event handlers
- **Prefer HTMX**: Use HTMX for interactive behaviors instead of custom JavaScript
- **Use CSS animations**: Implement animations with CSS keyframes and transitions
- **NO script tags**: Avoid using `<script>` tags with function definitions
- **Accessibility**: Ensure all interactive elements work with keyboard navigation

### CSS Animation Example

```css
/* Define animations with keyframes */
@keyframes toast-fade {
  0%, 80% { opacity: 1; transform: translateY(0); }
  100% { opacity: 0; transform: translateY(-10px); }
}

/* Apply animation to element */
.animate-toast {
  animation: toast-fade 5s ease-in-out forwards;
}
```

### Inline Handler Example

```html
<button onclick="this.closest('.modal-container').innerHTML = ''">
  Close
</button>
```

### HTMX Example

```html
<button hx-get="/path/to/content" hx-target="#content-area" hx-swap="innerHTML">
  Load Content
</button>
```
