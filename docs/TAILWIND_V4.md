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
  
  .bg-slate-900 {
    background-color: var(--color-slate-900);
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

Our project uses this custom color palette defined as CSS variables:

```
--color-accent: #ffd404      /* Yellow accent color */
--color-slate-900: #0a192f    /* Deep navy blue */
--color-slate-800: #112240    /* Navy blue */
--color-slate-700: #1d3557    /* Lighter navy blue */
```

### Usage

```html
<div class="text-accent bg-slate-900">
  <!-- Content with yellow text on navy background -->
</div>
```

## Building Components

### Component Classes
```css
@layer components {
  .btn-primary {
    @apply py-2 px-4 bg-slate-900 text-white rounded-md hover:bg-slate-800;
  }
}
```

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
