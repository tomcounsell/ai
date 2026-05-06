# Charter template

Copy the body below into `docs/designs/charter.md` and fill each
section. Replace italics placeholders with product-specific content.
Do NOT copy the H1 or this preamble — the first line of the target
file should be `# Design System Charter`.

The `do-design-system` skill reads this file before every moodboard
pass. Edits to tokens, components, and downstream CSS are tested
against the principles and taxonomy declared here. An empty or
boilerplate charter blocks the skill from proceeding.

---

```markdown
# Design System Charter

> This file is the charter for the design system. It is the contract
> every moodboard pass is tested against. Keep it current — if the
> product's voice or constraints change, update this file FIRST,
> then run a pass.

## Positioning

_One paragraph: what this product is, who it's for, what voice it
carries. The design system exists to serve this product, not a
generic aesthetic._

## Principles (3–7)

_Concrete, opinionated tenets. Each moodboard edit cites which
principle it supports. If an edit can't cite one, it's not tight
enough or it doesn't belong._

1. _(e.g. Honest, not clever — no decorative flourish that doesn't
   carry information)_
2. _(e.g. Editorial over marketing — read like a research journal,
   not a landing page)_
3. _(e.g. Dense information before whitespace — the user is a
   professional, not a visitor)_

## Voice & tone

- **Writing in:** _(e.g. sentence case, present tense, active voice)_
- **Avoid:** _(e.g. emoji, exclamation marks, "simply", "just",
  "easy", marketing superlatives)_
- **Button labels:** _(e.g. verb-first, ≤3 words — "Save changes"
  not "Click here to save")_
- **Error messages:** _(e.g. what went wrong + what to do next, no
  blame on the user)_
- **Empty states:** _(e.g. explain why it's empty + one concrete
  next action)_

## Accessibility targets

- **WCAG target:** _(e.g. 2.2 AA minimum, AAA where cheap)_
- **Contrast:** _(e.g. body text 4.5:1, large text 18pt+ 3:1, UI
  components 3:1)_
- **Focus ring:** _(e.g. visible on all interactive elements, 2px,
  uses `--accent`, offset 2px from element edge)_
- **Motion:** _(e.g. respect `prefers-reduced-motion`; no essential
  information conveyed in motion alone)_
- **Tap targets:** _(e.g. ≥44×44px on touch surfaces)_
- **Keyboard:** _(e.g. every interactive element reachable via Tab;
  no keyboard traps)_

## Token tiers

_Every new token fits one of these tiers. Semantic is the default._

- **Primitive** (raw values, rarely referenced by components):
  `--red-500`, `--space-8`, `--type-scale-7`
- **Semantic** (aliases onto primitives, referenced by components):
  `--color-danger`, `--space-md`, `--type-heading-lg`
- **Component** (scoped to a single component, references semantic):
  `--button-bg`, `--card-border`

## Component taxonomy

- **Format:** `Category/Variant` — e.g. `Annotation/Crosshair`
- **Categories in use:**
  - _Action_ — buttons, links, toggles
  - _Data_ — tables, charts, lists
  - _Layout_ — cards, panels, grids
  - _Annotation_ — marks, labels, overlay indicators
  - _Feedback_ — toasts, banners, empty states
  - _Navigation_ — tabs, breadcrumbs, menus
  - _(add your own, remove unused)_

New components MUST land in an existing category. To add a new
category, amend this list FIRST, in a separate commit.

## Fonts & licensing

_Every font embedded in the product or referenced by a token needs a
row here. New fonts MUST have a license listed before a moodboard
pass can land them._

| Font | License | Hosted | Used for |
|---|---|---|---|
| _(e.g. Inter)_ | _(SIL OFL 1.1)_ | _(self-hosted, /static/fonts/)_ | _(body, UI)_ |
| _(e.g. Lora)_ | _(SIL OFL 1.1)_ | _(Google Fonts CDN)_ | _(editorial headings)_ |

## Do's and don'ts

_3–7 specific misuse patterns. Too many and nobody reads it._

- ✅ **Do** _(e.g. use `--color-danger` for destructive actions only)_
- ❌ **Don't** _(e.g. use red as a brand accent — it reads destructive)_
- ✅ **Do** _(e.g. use `Annotation/*` components for data overlays)_
- ❌ **Don't** _(e.g. introduce new iconography without an SVG grid +
  stroke width spec)_

## Changelog

See [`gap-audit.md`](gap-audit.md) for the running log of
design-system changes, dated by moodboard pass.
```

---

## Notes for the scaffolder

- Delete every italics placeholder — unfilled placeholders are worse
  than no charter.
- The principles section is load-bearing. If you can't write three
  tenets the product team agrees on, stop and have that conversation
  first.
- The fonts table must list every font referenced anywhere in the
  `.pen` or CSS. Run a check after scaffolding:

  ```bash
  grep -oE "font-family: [^;]+|--font-[a-z-]+" <css-root>/brand.css
  ```

- If the product already has a brand guidelines doc elsewhere
  (Notion, vault, PDF), link to it from Positioning rather than
  copying — but the principles, taxonomy, and fonts table must
  live here in the repo.
