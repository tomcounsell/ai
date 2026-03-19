# Tailwind CSS v4 Cheat Sheet

This project uses Tailwind CSS v4 with django-tailwind-cli. This cheat sheet covers key features and changes from v3.

## Key Changes in v4

- **CSS-based Configuration**: Configuration now lives directly in CSS
- **Simplified Installation**: Just `@import "tailwindcss"`
- **Massive Performance Improvement**: Up to 5x faster full builds, 100x faster incremental builds
- **Modern CSS Features**: Uses cascade layers, CSS variables, color-mix(), and more
- **No More Config.js File**: Configuration now lives directly in your CSS files

## Setup with Django

### Installation
```bash
# Using django-tailwind-cli
python manage.py tailwind build # For production
python manage.py tailwind watch # For development
```

### Direct CLI Usage
```bash
# Direct CLI usage
~/.local/bin/tailwindcss-macos-arm64-4.0.6 -i ./static/css/input.css -o ./static/css/output.css
```

## Configuration

### CSS-Based Configuration
```css
/* In source.css (NO separate tailwind.config.js needed) */
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --color-accent: #ffd404;
    --color-slate-900: #0a192f;
    /* other CSS variables */
  }
}

/* Custom theme configuration */
@layer components {
  .text-accent {
    color: var(--color-accent);
  }
}
```

## New Features

### CSS Variables for Theming
```css
/* Define variables in :root */
:root {
  --color-primary: #3b82f6;
  --color-secondary: #10b981;
}

/* Use in custom components */
.btn-primary {
  background-color: var(--color-primary);
}
```

### Arbitrary Values
```html
<!-- One-off custom values -->
<div class="bg-[#316ff6]">
<div class="grid-cols-[1fr_500px_2fr]">
<div class="p-[clamp(1rem,5vw,3rem)]">
```

### Advanced Responsive Design
```html
<!-- Breakpoint variants -->
<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
```

### State Variants
```html
<!-- Hover, focus, and other states -->
<button class="bg-blue-500 hover:bg-blue-700 focus:ring-2">
```

### Combined Variants
```html
<!-- Multiple variants can be combined -->
<div class="dark:sm:hover:bg-gray-800">
```

### Container Queries
```html
<!-- Style based on container size, not viewport -->
<div class="@container">
  <div class="@lg:grid-cols-2 @xl:grid-cols-3">
  </div>
</div>
```

### 3D Transforms
```html
<!-- 3D transform utilities -->
<div class="rotate-x-45 rotate-y-45 perspective-500">
```

## Our Color System

Colors are defined as CSS variables in `static/css/brand.css`. Use Tailwind arbitrary values to reference them:

```
--color-bg-cream: #FAF9F6       /* Primary surface */
--color-bg-warm-gray: #F5F4F1   /* Subtle section differentiation */
--color-text-black: #1A1A1A     /* Headlines */
--color-text-gray: #5A5A5A      /* Labels, secondary text */
--color-border-dark: #3A3A3A    /* Primary structure */
--color-border-medium: #C4C4C4  /* Standard dividers */
--color-accent: #B91C1C         /* Red annotation accent */
```

### Usage

```html
<div class="text-[var(--color-text-gray)] border-[var(--color-border-medium)]">
  <!-- Content using brand CSS variables -->
</div>
```

## Building Components

### Component Classes

Semantic components are defined in `static/css/brand.css` (e.g., `.btn-brand`, `.card-technical`, `.server-card`). Use Tailwind for layout utilities (grid, flex, spacing) and brand.css for semantic components.

## Running Tailwind CLI Commands

```bash
# Build CSS for production
python manage.py tailwind build

# Watch for changes during development
python manage.py tailwind watch
```

## Resources

- [Tailwind CSS v4 Docs](https://tailwindcss.com/docs)
- [django-tailwind-cli Documentation](https://pypi.org/project/django-tailwind-cli/)

## Troubleshooting

If the Django command doesn't work, you can always use the CLI directly:

```bash
~/.local/bin/tailwindcss-macos-arm64-4.0.6 -i ./static/css/input.css -o ./static/css/output.css
```

When using modern Tailwind v4 features, make sure all browsers you need to support have compatibility with those CSS features.
