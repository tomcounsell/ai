# Site Vault Content

The valorengels.com docs site (`site/`) surfaces three pieces of the human-curated
work vault (`~/work-vault/AI Valor Engels System/`) that previously had no path onto
the site: the four strategic-analysis decks, the "Valor AI System Overview" narrative,
and the four `Personas/` bios. This is frontend-lane work from issue #2084 — the
backend-lane companion (the vault↔site drift detector) is documented separately in
[`docs/features/vault-drift-audit.md`](vault-drift-audit.md).

## Research page (`site/research.html`)

A new site page, added alongside the existing `index`, `runtime`, `layers`,
`pipeline`, `memory`, and `tour` pages, and wired into `site/sitemap.xml`, the
top nav, and the `index.html §05 "Where to go next"` directory as entry `06`.

It distills the vault's four strategic-analysis decks into house-style HTML — the
same `runtime.html` scaffold (`<head>`, header, footer, CSS classes) rather than a
new page structure:

1. **Managed Agents vs Valor** — the division of labour between Valor's local
   orchestration and Claude Managed Agents' hosted execution.
2. **Valor vs Paperclip** — a vertical development teammate compared against a
   horizontal multi-agent control plane.
3. **Managed Agents vs Perplexity Computer** — a programmatic execution API
   compared against a vision-language "digital worker" with no equivalent API.
4. **OpenHuman vs Hermes** — two self-hosted agent stacks with opposite memory-shape
   bets (single-user Markdown vault vs. headless multi-tenant session store).

Each deck is distilled to a `<h2>` + several paragraphs of prose (not a pixel
reproduction of the Marp slides — see the plan's Rabbit Holes) and is paired with a
**house-style inline-SVG cover card**: a small `<svg>` diagram built from the site's
existing `.dg-box` / `.dg-title` / `.dg-sub` / `.dg-edge` CSS classes (GitHub-light
palette, hairline borders, the single `#0969da` accent, `"SF Mono"` labels), captioned
by a `.diagram-cap` paragraph underneath. No external chart library, no stock imagery,
no color illustration — every figure is checked-in inline markup in the page itself,
consistent with the rest of the site's generated diagrams.

## Overview canonical/reference dedup

Before this work, `index.html §04` and the vault's `Valor AI System Overview.md`
covered overlapping ground with no stated relationship between them. Per the plan's
Resolved Question 2, **the vault is canonical** — it is the human-curated source of
truth per the repo `CLAUDE.md` Knowledge Base convention. The site does not
reproduce the narrative; it references it.

`index.html §04 "Who is behind this"` now closes with an explicit provenance note:

> This page is the site's always-current, machine-generated companion to Valor's
> fuller system-overview narrative, which is maintained by hand in Yudame's internal
> knowledge base; the narrative there is the canonical account, and this page is
> regenerated from the code so the two never drift far apart.

That sentence is the dedup mechanism: it tells a reader which copy is authoritative
without duplicating vault content into the generated site, and it is one of the two
`VAULT_SITE_MAPPING` entries the backend drift detector (see
[`vault-drift-audit.md`](vault-drift-audit.md)) checks for staleness going forward.

## Persona and portrait treatment

The plan (Resolved Question 3) constrained persona work to the site's two *existing*
sections — no new page structure beyond Research.

### Hedcut byline (`index.html §04`)

`index.html §04 "Who is behind this"` gained a `.byline` block: a 72×72 cropped hedcut
portrait (`site/assets/valor-hedcut-byline.png`) next to the name/role text
("Valor Engels · software engineer · Yudame"). The crop is a square taken from the
vault source hedcut per CEO art direction (upper-third eye line), rendered as a
1-bit dithered engraving to match the site's monochrome system — grayscale, no color,
well under the 100 KB budget (8.6 KB).

### Runtime three-hats enrichment (`runtime.html`)

`runtime.html`'s existing `"Three hats, one runner"` section (the PM/Dev/Teammate
runtime-role table) is untouched in meaning, but the page now adds a **second,
clearly distinct subsection directly beneath it**: `"Org-leadership personas"`.

The distinction matters and is stated explicitly on the page: the three hats are
*runtime roles* — execution modes a single turn wears (PM orchestrates, Dev builds,
Teammate converses). The org-leadership personas are a different axis — named
leadership voices, drawn from the vault's `Personas/` bios, that color tone and
priorities when the system is "wearing a business hat rather than an engineering
one." The enrichment adds depth and voice; it does not overwrite or merge with the
runtime-role semantics.

The subsection renders a `.persona-grid` of four `.persona-card`s, one per vault
persona:

| Persona | Role | Portrait |
|---------|------|----------|
| Philip Pullman | Head of Product ("the dreamer") | monogram placeholder (`PP`) |
| Jules Verne | Head of Engineering ("the builder") | monogram placeholder (`JV`) |
| H.G. Wells | Head of Operations ("lands it on time") | monogram placeholder (`HW`) |
| Valor Engels | The engineer who ships | `site/assets/valor-hedcut-persona.png` (2× hedcut derivative) |

Each bio is a one-paragraph distillation of the corresponding vault `Personas/*.md`
file. The three org-leadership personas (Pullman, Verne, Wells) have no portrait by
design — the plan's Rabbit Holes explicitly rule out commissioning or synthesizing
photoreal portraits for them. Each instead uses a **monogram/line-art placeholder**:
a small inline `<svg viewBox="0 0 48 48">` rendering the persona's initials in
`#0969da` on the house `.persona-figure` frame, with an `aria-label` that states
plainly the portrait doesn't exist by design (e.g. `"Monogram placeholder for Philip
Pullman — no portrait exists by design"`). Valor's card uses the real hedcut
derivative instead of a monogram, since Valor is the one persona with a portrait.

## Asset provenance rule

The site never references a vault filesystem path. Every image the site displays —
the byline hedcut, the persona-section hedcut, and any future vault-derived asset —
is a **committed derivative under `site/assets/`**, produced once (via PIL/`sips`
cropping) and checked into the repo. There is no live read from
`~/work-vault/AI Valor Engels System/files/...` at request time or at build time.
This is enforced by an anti-criterion check (`grep -rn 'work-vault' site/*.html`
returns no matches) that the frontend validation lane runs before merge.

## AC6 Decision: "Shipped This Week" Feed

**Recommendation: no-build.** Do not build a public "shipped this week" feed as part of
this plan. Neither candidate source is fit to drive a zero-review public widget today, and
the ongoing upkeep cost outweighs the marginal value of a vanity feed. If the idea is
revisited later, it should spin off its own issue rather than expand this plan's scope.

**What `daily-logs/` actually is.** The vault's `daily-logs/` holds machine-generated
aggregator output, not curated narrative. As of this evaluation there are five files
(2026-05-02 through 2026-05-06) and the newest is roughly ten weeks stale — the aggregator
has not produced a log since early May, so any feed sourced from it would silently go dark.
Volume is wildly uneven: one day is 42 KB, another is a 73-byte "(No system activity
recorded)" stub. The content is raw and leaky: an `## Aggregator Notes` block full of
`[ERROR: gh:...]` GraphQL and token-policy failures that name internal orgs and repos
(`yudame`, `chainstarters/gato`, `yudame/pba.ai`), followed by unfiltered commit dumps —
raw SHAs, dependency-bump churn, and repeated "Plan revision" commits. None of this is
structured or clean enough to auto-summarize into something we'd want on a public site
without a heavy curation and redaction pass every single time.

**What `/weekly-review` already produces.** The `weekly-review` skill is the stronger
source: it is purely git-based, and its output is deliberately stakeholder-facing — named
categories with plain-language bullets, contributor stats, and an explicit "would a product
manager or executive understand this?" test that strips jargon, code paths, and file
references. Its format could plausibly feed a site widget with modest formatting work. The
catch is that it is not a deterministic pipeline — it is an interactive skill whose Phase 2
is an LLM analysis pass Claude runs on demand. Wiring it to a public feed means standing up
a *new* recurring automated job to invoke that pass on a schedule; the skill itself is not
that job.

**The upkeep cost.** A "shipped this week" feed forces a choice between two bad options.
(a) A recurring automated job that publishes to a public site with zero human review — which
must stay correct and non-embarrassing indefinitely. Given that even the cleaner source rides
on an LLM summarization pass, and the raw source leaks internal repo names and error text, the
failure mode is publishing something wrong, stale, or embarrassing with no one in the loop.
(b) Manual curation before each publish — which directly competes for attention with the
actual engineering work the feed would report on, and predictably lapses (the daily-logs
already went dark for ten weeks).

**Conclusion.** The value is a low-stakes vanity feed with no clearly-defined audience; the
cost is a standing correctness-and-embarrassment liability on a public surface, or recurring
manual toil. Recommendation stands: **no-build now.** Revisit only as a separate,
appropriately-scoped issue — and if pursued, gate it on `/weekly-review` output that a human
approves before it goes live, rather than any unattended pipeline over `daily-logs/`.

## See Also

- [`docs/features/vault-drift-audit.md`](vault-drift-audit.md) — the backend-lane
  drift detector that watches the Overview and the four decks for vault↔site divergence.
- [`docs/features/docs-auditor.md`](docs-auditor.md) — the substrate the drift
  detector is built into.
- `docs/plans/vault-site-integration.md` — the plan this feature shipped from
  (issue #2084).
