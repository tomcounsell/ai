# Canonical design-file organization (Step 0 reference)

Load this when auditing or organizing a repo's design files against the canonical
structure — before any moodboard work.

## Canonical file structure

```
docs/designs/
├── README.md                    # Landing page, indexes everything
├── charter.md                   # Principles, voice, a11y, taxonomy, licensing
├── design-system.pen            # RESERVED — skill territory, source of truth
├── gap-audit.md                 # Append-only changelog of system changes
├── product/                     # Product team's .pen files (free-form)
│   ├── README.md                # Index with slug, kind, date, status
│   └── <slug>-<kind>.pen        # kind ∈ flow|wireframe|mockup|journey|sitemap|prototype
├── inspiration/
│   ├── README.md                # Index of moodboard passes
│   └── YYYY-MM-DD-<theme>/      # One folder per pass
│       ├── README.md            # Source URL + motif table + image legend
│       ├── cover.webp
│       └── NN-<author-or-theme>.webp
└── exports/                     # Optional — rendered component PNGs

<css-root>/                      # e.g. static/css/, assets/css/, styles/
├── brand.css                    # :root tokens — 1:1 mirror of design-system.pen
└── source.css                   # Tailwind @theme bridge (if project uses Tailwind)
```

## Invariants

| Rule | Why |
|---|---|
| Exactly one reserved `.pen` named `design-system.pen` | Unambiguous skill target |
| Skill refuses to edit any other `.pen` | Product `.pen` files have different schemas |
| Each moodboard pass gets its own `inspiration/YYYY-MM-DD-<theme>/` folder | Passes are referenceable forever |
| Motif table lives in `inspiration/<pass>/README.md` | Lives with the images it describes |
| `gap-audit.md` has a dated `## YYYY-MM-DD — <theme>` section per pass | Append-only log |
| `brand.css` and `source.css` token names match exactly | Divergence is silent |
| `product/` filenames follow `<slug>-<kind>.pen` | Sortable, greppable |
| `product/` files never appear in `gap-audit.md` | Audit is system-only |
| No version numbers or dates in `design-system.pen` filename | Git history is the version log |

## Step 0 audit checklist

Check each of:

- `docs/designs/` exists
- `docs/designs/charter.md` exists and is non-empty
- `docs/designs/design-system.pen` exists (may be under a legacy name)
- `docs/designs/gap-audit.md` exists
- `docs/designs/inspiration/` exists
- `docs/designs/product/` exists (may be empty)
- Downstream CSS files present and token names match

## Gap → proposed migration

Propose migrations; do NOT auto-apply.

| Gap | Proposed migration |
|---|---|
| No `charter.md` | Read `charter-template.md`, scaffold to `docs/designs/charter.md`, **HALT** and ask user to fill it before proceeding |
| `.pen` under a non-canonical name | Rename to `design-system.pen` |
| Multiple `.pen` at `docs/designs/` root | Keep `design-system.pen`, move others to `product/<slug>-<kind>.pen` |
| No `gap-audit.md` | Create with header, empty body |
| No `inspiration/` | Create with `README.md` listing passes |
| `inspiration/` flat (images mixed) | Move all images into `inspiration/YYYY-MM-DD-initial/` with scaffolded `README.md` |
| No `product/` | Create with empty `README.md` |
| `brand.css` ↔ `source.css` token names diverge | List mismatches; do NOT auto-fix (renames are breaking) |
