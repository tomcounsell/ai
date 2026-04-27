---
colors:
  primary: '#111827'
  surface: '#FFFFFF'
  text.body.primary: '#FFFFFF'
components:
  annotation-mark:
    backgroundColor: '{colors.primary}'
    height: 16px
    rounded: '{rounded.md}'
    textColor: '{colors.text.body.primary}'
    typography: '{typography.body}'
    width: 16px
  surface-card:
    backgroundColor: '{colors.surface}'
    height: 120px
    rounded: '{rounded.md}'
    width: 240px
name: fixture
rounded:
  md: 8px
spacing:
  md: 16px
typography:
  body:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: 400
    lineHeight: 24px
version: alpha
---

# fixture

## Overview

Generated from `design-system.pen`. See `docs/features/design-system-tooling.md`.

## Colors

The color palette is derived from the `.pen` source.
- **primary:** `#111827`
- **surface:** `#FFFFFF`
- **text.body.primary:** `#FFFFFF`

## Typography

Typography presets are aggregated from `--font-*`, `--text-size-*`, `--text-weight-*`, and `--text-lh-*` tokens.
- **body**

## Layout

Spacing scale is driven by `--space-*`, `--gap-*`, and `--pad-*` tokens.

## Shapes

Corner-radius scale is driven by `--radius-*` and `--rounded-*` tokens.

## Components

Reusable components map from Pencil frames (`reusable: true`, `Category/Variant` name).
- `annotation-mark`
- `surface-card`
