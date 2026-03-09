# Pencil Design Workflow

Best practices for using Pencil (.pen files) in the Cuttlefish design-to-code workflow.

## Design System File

The canonical design system lives in `pencil-design-system.pen` at the project root. It contains:
- **Variables**: All brand tokens (`$--background`, `$--accent`, `$--border-dark`, etc.)
- **Reusable components**: Button/Primary, Button/Accent, Card/Technical, Card/Spec Box, Input/Labeled, Nav/Bar, Divider/Technical, Code/Block, Footer/Brand, Status indicators
- **Reference frames**: Color palette, typography samples, spacing scale

## When to Use Pencil

- **Before implementing a new page or template**: Design the layout in a .pen file first
- **During the Template Brand CSS Rewrite**: Create target mockups per template group
- **When reviewing design changes**: Screenshot .pen components to compare against live pages
- **For design reviews**: Use `get_screenshot` to capture and evaluate visual output

## Workflow: Design → Code

1. **Open the design system**: `mcp__pencil__open_document` with `pencil-design-system.pen`
2. **Get editor state**: `mcp__pencil__get_editor_state` to see available reusable components
3. **Create a new page frame**: Insert a top-level frame sized to the target viewport (1200px for app pages, 1000px for landing pages)
4. **Compose with components**: Use `type: "ref"` to instantiate reusable components from the design system
5. **Screenshot and verify**: `mcp__pencil__get_screenshot` after each logical section
6. **Implement in templates**: Translate the .pen layout to HTML using brand.css classes and Tailwind utilities
7. **Compare**: Screenshot the live page (via agent-browser) against the .pen mockup

## Pencil MCP Tool Patterns

### Reading .pen files
- NEVER use `Read` or `Grep` on .pen files — contents are encrypted
- ALWAYS use `mcp__pencil__batch_get` to inspect node structure
- Use `mcp__pencil__get_screenshot` to visually verify

### Building designs
- Keep `batch_design` calls to **max 25 operations**
- Split large designs into logical sections (header, content, footer)
- Always set `placeholder: true` on frames you're actively building
- Always remove `placeholder: false` when done with a frame
- Use literal font names ("Inter", "IBM Plex Mono") — variable refs don't work for `fontFamily`
- Use `$--variable` references for colors, spacing, and other tokens

### Component instances
- Insert: `card=I(parent, {type: "ref", ref: "G9h8r"})` — use the component's ID
- Update descendant: `U(card+"/title", {content: "New Title"})`
- Replace descendant: `R(card+"/slot", {type: "text", ...})`
- DO NOT Update descendants of a just-Copied node (IDs change on copy)

### Creating new reusable components
- Set `reusable: true` on the root frame
- Name with category prefix: `Card/Episode`, `Button/Ghost`, `Input/Search`
- Include a `slot` array on content frames to mark them as customizable
- Place component definitions in the Components section of `pencil-design-system.pen`

## Brand Rules (enforced in designs)

- **Square corners** on everything except buttons and status dots
- **No border-radius** on cards, containers, sections, code blocks
- **Inter** for headings, body text, nav links
- **IBM Plex Mono** for labels, data, code, technical metadata
- **Red (#B91C1C)** is never a surface color — it annotates, marks, highlights
- **8px grid** — all spacing is a multiple of 8
- **1px borders** for standard elements, **2px borders** for hero/featured content
- **Max 1 accent button per page** — use sparingly

## CLI: Batch Design with config.json

For automated design generation across multiple .pen files:

```json
[
  {
    "file": "./designs/dashboard.pen",
    "prompt": "Design the authenticated dashboard using the Cuttlefish brand: cream background, Inter headings, IBM Plex Mono labels, 1px medium border cards, red accent sparingly",
    "model": "claude-4.5-sonnet",
    "attachments": ["static/css/brand.css", "docs/BRANDING_BRIEF.md"]
  }
]
```

Run: `pencil --agent-config config.json`

Prerequisites:
- Install CLI: Pencil desktop app → File → Install `pencil` command into PATH
- Create empty .pen files before running (CLI cannot create them)
- Attach `brand.css` and `BRANDING_BRIEF.md` for brand context

## File Conventions

| File | Purpose |
|------|---------|
| `pencil-design-system.pen` | Canonical design system (tokens, components, reference) |
| `designs/*.pen` | Page-specific mockups (one per template group) |
| `static/css/brand.css` | CSS implementation of the design system |
| `docs/BRANDING_BRIEF.md` | Brand rationale and guidelines |
