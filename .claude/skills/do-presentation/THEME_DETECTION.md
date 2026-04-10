# Theme Detection: Adapting to the Repo's Design System

## Goal

Generate a Marp `style:` block that makes the presentation look like it belongs to the project. The slides should feel native — same colors, same fonts, same visual language as the repo's UI.

## Detection Strategy

Search in order of specificity. Stop as soon as you find a usable design system.

### Priority 1: CSS Custom Properties / Design Tokens

Search for explicit design token definitions:

```
Glob: **/tokens.{css,scss,json,js,ts,yaml}
Glob: **/variables.{css,scss,less}
Glob: **/theme.{css,scss,js,ts,json}
Glob: **/design-system/**
Glob: **/design-tokens/**
```

Look for CSS custom properties (`--color-*`, `--font-*`, `--spacing-*`, `--radius-*`).

### Priority 2: Tailwind / CSS Framework Config

```
Glob: **/tailwind.config.{js,ts,cjs,mjs}
Glob: **/theme.config.*
```

Extract from `theme.extend.colors`, `theme.extend.fontFamily`, etc.

### Priority 3: UI Component Styles

```
Glob: **/ui/**/*.{css,scss}
Glob: **/styles/**/*.{css,scss}
Glob: **/components/**/*.{css,scss,module.css}
Glob: **/*.styles.{ts,js}
Glob: **/global*.css
```

Grep for common patterns:
```
Grep: background-color|backgroundColor|--bg
Grep: font-family|fontFamily|--font
Grep: border-radius|borderRadius|--radius
Grep: color:\s*#[0-9a-fA-F]
```

### Priority 4: Existing Presentations

Check if the repo already has Marp presentations:
```
Grep: "marp: true" across **/*.md
```

If found, extract and reuse the existing theme.

### Priority 5: README / Brand Assets

```
Glob: **/brand/**
Glob: **/assets/logo*
Glob: **/.github/*.{yml,yaml}  # GitHub theme colors
```

### Fallback: Clean Default Theme

If no design system is found, use this professional dark theme:

```css
section {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background-color: #1a1a2e;
  color: #eaeaea;
}
h1, h2, h3 { color: #64ffda; }
strong { color: #82b1ff; }
code {
  font-family: "SF Mono", "Fira Code", monospace;
  background: #16213e;
  color: #f7768e;
}
```

## Token Mapping

Map discovered design tokens to Marp CSS:

| Design Token | Marp Target | Notes |
|-------------|-------------|-------|
| Background (darkest) | `section { background-color }` | Page background |
| Background (secondary) | `pre, th, code { background }` | Code blocks, table headers |
| Background (tertiary) | `tr:hover td { background }` | Hover states |
| Text (primary) | `section { color }` | Body text |
| Text (secondary) | `blockquote, em { color }` | Dimmed text |
| Text (muted) | `section::after { color }` | Page numbers |
| Accent (primary) | `h1, h2, a, ul li::marker { color }` | Headings, links |
| Accent (hover) | `h3, strong { color }` | Sub-headings, bold |
| Accent (secondary) | Lead slide gradient | Title gradient if available |
| Border color | `td, pre, code { border-color }` | All borders |
| Border radius | `code, pre, blockquote { border-radius }` | Rounded corners |
| Font (sans) | `section { font-family }` | Body text |
| Font (mono) | `code, pre { font-family }` | Code blocks |
| Success color | Highlight or status indicators | Optional |
| Error color | Warning callouts | Optional |

## Building the Style Block

Once tokens are extracted, generate the full Marp CSS. Include ALL of these sections:

```css
/* === [Project Name] Theme === */

/* Base */
section {
  font-family: <sans-font>;
  background-color: <bg-primary>;
  color: <text-primary>;
  padding: 40px 60px;
  line-height: 1.5;
}

/* Headings */
h1 {
  color: <accent>;
  font-size: 2.2em;
  font-weight: 600;
  border-bottom: 2px solid <border>;
  padding-bottom: 12px;
}
h2 {
  color: <accent>;
  font-size: 1.6em;
  font-weight: 600;
  border-bottom: 1px solid <border>;
  padding-bottom: 8px;
}
h3 {
  color: <accent-hover>;
  font-size: 1.15em;
  font-weight: 600;
}

/* Inline elements */
strong { color: <accent-hover>; }
em { color: <text-secondary>; }
a { color: <accent>; text-decoration: none; }

/* Code */
code {
  font-family: <mono-font>;
  background: <bg-secondary>;
  color: <orange-or-contrast>;
  padding: 2px 8px;
  border-radius: <radius>;
  font-size: 0.85em;
  border: 1px solid <border>;
}
pre {
  background: <bg-secondary>;
  border: 1px solid <border>;
  border-radius: <radius>;
  padding: 16px;
  font-size: 0.75em;
  line-height: 1.7;
}
pre code {
  background: none;
  border: none;
  padding: 0;
  color: <text-primary>;
}

/* Lead slides (title, section breaks) */
section.lead {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  text-align: center;
  background: linear-gradient(180deg, <bg-primary> 0%, <bg-secondary> 100%);
}
section.lead h1 {
  font-size: 3em;
  border-bottom: none;
}
section.lead p {
  font-size: 1.15em;
  color: <text-secondary>;
}

/* Tables */
table {
  font-size: 0.82em;
  width: 100%;
  border-collapse: collapse;
}
th {
  background: <bg-secondary>;
  color: <accent>;
  font-size: 0.85em;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 8px 12px;
  border: 1px solid <border>;
  text-align: left;
}
td {
  background: <bg-primary>;
  border: 1px solid <border>;
  padding: 8px 12px;
}

/* Blockquotes */
blockquote {
  border-left: 4px solid <accent>;
  background: <accent-bg-translucent>;
  color: <text-secondary>;
  padding: 8px 16px;
  border-radius: 0 <radius> <radius> 0;
  font-style: italic;
}

/* Lists */
ul li::marker { color: <accent>; }
ol li::marker { color: <accent>; font-weight: 600; }

/* Page numbers */
section::after {
  color: <text-muted>;
  font-family: <mono-font>;
  font-size: 11px;
}
```

## Light Theme Adaptation

If the repo uses a light theme:
- Invert the background hierarchy (lightest → darkest for emphasis)
- Use dark text on light background
- Reduce border contrast
- Use `section.lead` with a subtle gradient (light → slightly darker)
- Ensure code blocks have enough contrast (light gray background, dark text)

## Multi-Theme Repos

Some repos have both light and dark themes. Prefer:
1. Dark theme (better for presentations/projection)
2. Whichever theme the `ui/` or dashboard uses
3. Whichever theme has more complete token definitions
