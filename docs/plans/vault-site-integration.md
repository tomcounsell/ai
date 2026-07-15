---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-15
tracking: https://github.com/tomcounsell/ai/issues/2084
last_comment_id: 4976296145
---

# Integrate the work-vault knowledge base with the valorengels.com docs site

## Problem

The valorengels.com docs site (`site/`, landed on `main` via #2058/#2087) renders
the machine-generated knowledge graph plus a handful of authored HTML pages. The
human-curated **work vault** (`~/work-vault/AI Valor Engels System/`) holds the
narrative corpus — the "Valor AI System Overview," four strategic-analysis decks,
persona bios, and daily logs — that the site never surfaces. The two corpora are
maintained by hand, independently, and drift apart with nothing catching it.

**Current behavior:**
- The site shows the generated graph + authored pages; no vault narrative flows into it.
- The vault `Valor AI System Overview.md` and the site's system-overview narrative
  cover the same ground but are edited separately and diverge over time.
- The four strategic decks and the persona bios exist only in the vault; the site
  has no path to display them, and its media mix is text-heavy.
- **There is no live vault↔docs cross-reference/drift audit at all** (see Freshness
  Check — the tool the issue names was deleted, and its consolidated successor never
  implemented vault handling). So nothing detects vault↔site (or vault↔docs) drift.

**Desired outcome:**
- A standing, repeatable drift audit that cross-references vault content against the
  site's HTML pages (and repo docs), catching divergence continuously.
- Each overlapping narrative (starting with the Overview) has a single canonical home;
  the other location references it instead of duplicating.
- The vault's strategic decks and persona bios are surfaced on the site through
  new/enriched pages, improving knowledge coverage and the media mix.
- `secrets/` is excluded from every vault sweep, unconditionally.

## Freshness Check

**Baseline commit:** `bb758b92` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-14T06:05:53Z (CEO art-direction comment added 2026-07-15T02:34:13Z)
**Disposition:** **Major drift** — the issue's central implementation target no longer exists.

**File:line references re-verified:**
- `.claude/skills/do-xref-audit/SKILL.md` — issue's Recon Summary claims this is the
  canonical, project-only file to edit for AC1. **GONE from the repo.** `git ls-files | grep -i xref`
  returns nothing; there is no `.claude/skills/do-xref-audit/` or `.claude/skills-global/do-xref-audit/`
  in the repo. A stale copy exists only at `~/.claude/skills/do-xref-audit/SKILL.md` (untracked,
  machine-local, orphaned by a prior sync — legacy cruft, not source of truth).
- `docs/features/docs-auditor.md:12` — confirms `/do-xref-audit` was **"deleted (no replacement)"**,
  consolidated (along with 4 other pieces) into `reflections/docs_auditor.py` per #1247.
- `reflections/docs_auditor.py:1089-1095` (`_select_primary_doc`) — the consolidated substrate's
  doc selection **only globs `docs/features/*.md`**. The vault-namespaced rotation-hash schema
  (`_vault_field` at :181, `DEFAULT_VAULT_WEIGHT` at :71) is **vestigial** — no code enumerates,
  selects, or audits vault docs. The feature doc's claim (line 120/126) that "vault docs are
  picked at half the rate" describes a schema hook that was never wired to a producer.
- `reflections/docs_auditor.py:968-974` — apply-mode writes are already markdown-only and
  explicitly guard against rewriting `site/*.html` (added by #2058). So the substrate is *aware*
  of site pages but does not cross-reference them.
- `.claude/skill-context/do-docs.md:54` — already lists `site/*.html` as a `/do-docs` cascade
  inventory location. This is the docs-cascade context, **not** a vault inventory; the issue's
  phrase "the vault-side inventory in `.claude/skill-context/do-docs.md`" is imprecise — no vault
  inventory lives there.

**Cited sibling issues/PRs re-checked:**
- #2058 — CLOSED 2026-07-14T08:19:33Z; merged as PR #2087. `site/` is on `main` with pages
  `index, runtime, layers, pipeline, memory, tour, 404` + `site/assets/`. Prerequisite satisfied.
- #2059 — OPEN. Knowledge-graph regeneration pipeline; complementary, out of scope here.

**Commits on main since issue was filed (touching referenced files):**
- `dfb781ca` "Bring valorengels.com docs site into main…" (#2087) — this is #2058 landing; it
  *created* the site this issue builds on. Expected, not disruptive.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/agent_wiki.md` mentions
`work-vault`/`xref` tangentially (a wiki concept, not the site). No direct overlap; note for
coordination but not a blocker.

**Notes — why this is Major drift, not a blocker:** The issue's *goal* is fully valid — the
site exists, the vault exists, drift is real. Only the *implementation target* moved: AC1 cannot
"extend the existing xref audit" because the xref audit was deleted with no replacement, and its
consolidated successor never handled the vault. This plan proceeds on a **revised premise**
(build the vault↔{docs,site} drift audit into the live substrate, `reflections/docs_auditor.py`,
rather than editing a deleted skill) and surfaces the reframing as the headline Open Question so
the supervisor/critique can confirm the direction before build. Per the do-plan freshness
protocol, this drift is documented here loudly rather than silently built around.

## Prior Art

- **#2058 / PR #2087**: "Bring valorengels.com docs site into main as living documentation" —
  merged; created `site/` and wired `site/*.html` into the `/do-docs` cascade + `deploy-site.sh`.
  This is the prerequisite; its patterns (markdown-only apply guard, sitemap maintenance, deploy
  step) are the ones AC1 extends.
- **#1247 / `docs/features/docs-auditor.md`**: consolidated `feature-docs-audit`,
  `documentation-audit`, `knowledge-reindex`, `/do-xref-audit`, `/do-docs-audit` into
  `reflections/docs_auditor.py`. **Directly relevant:** it deleted the xref skill this issue
  assumed still exists, and it is the substrate this plan extends.
- No prior closed issues found for vault↔site cross-referencing (`gh issue list --state closed
  --search "vault site xref cross-reference"` → empty).

## Research

This work is almost entirely internal (site HTML in an existing hand-rolled design system, the
vault filesystem, and the `reflections/docs_auditor.py` substrate). Per the CEO art direction,
new figures must be in-house inline SVG line art + the single `#0969da` accent — **no external
libraries, no stock imagery, no color illustration**. Image derivatives (hedcut crop) use
already-present tooling (PIL/`sips`), not a new dependency.

No relevant external findings — proceeding with codebase context and training data.

## Spike Results

The one verifiable technical assumption ("does the consolidated substrate already audit vault
docs?") was resolved by direct code-read during the Freshness Check rather than a dispatched
spike — answer: **no** (`_select_primary_doc` globs `docs/features/*.md` only; vault schema is
vestigial). No further prototype spikes were dispatched because the remaining forks are design/
scope decisions requiring human judgment (see Open Questions), not code-verifiable facts. Running
prototypes on a premise that may be redirected by Q1 would be wasteful.

## Data Flow

**AC1 — vault↔{docs,site} drift audit (rotation caller, `reflections/docs_auditor.py`):**
1. **Entry point**: `docs-auditor` daily rotation reflection → `run_docs_auditor()` →
   `audit(scope_mode='rotation', project_key='valor')`.
2. **Vault path resolution** (new): read `~/Desktop/Valor/projects.json`, resolve `valor`'s
   `knowledge_base` → vault root. Enumerate vault `*.md`, **excluding `secrets/`** and markitdown
   sidecars (`generated_by: markitdown` frontmatter).
3. **Overlap/drift detection** (new): for each canonical vault narrative, compare against its
   mapped site page (`site/*.html`) and repo doc; emit a drift finding (file-as-issue, advisory)
   when the two have diverged. `secrets/` never enters the sweep.
4. **Output**: advisory GitHub issue(s) tagged `documentation` on drift, deduped via the existing
   two-tier gate. No auto-rewrite of `site/*.html` (existing markdown-only apply guard preserved).

**AC4 — Research page (build-time authored content):**
1. **Entry point**: builder authors `site/research.html` from vault `*-report.md` narratives.
2. **Transform**: distill each of the 4 decks into house-style HTML + a line-art cover card.
3. **Output**: committed `site/research.html` + `site/assets/` figures; `site/sitemap.xml` +
   nav/`§05` directory updated; deployed post-merge via `scripts/deploy-site.sh`.

**AC5 — persona enrichment + hedcut portrait:**
1. **Entry point**: crop `valor-hedcut.png` (vault `files/valor-portraits-2026-07-14/`) → optimized
   `site/assets/` derivatives (never reference the vault path from the site).
2. **Transform**: distill the 4 `Personas/` bios into house-style copy.
3. **Output**: enriched `index.html §04 "Who is behind this"` (byline treatment) and/or
   `runtime.html` "Three hats" section, with monogram/line-art placeholders for the portrait-less
   personas.

## Architectural Impact

- **New dependencies**: none (in-house SVG; PIL/`sips` for the crop already present).
- **Interface changes**: `reflections/docs_auditor.py` gains vault enumeration + a vault↔page
  drift detector. New file `site/research.html`. New `site/assets/` image derivatives.
- **Coupling**: increases coupling between the docs-auditor substrate and the vault filesystem +
  `projects.json` (`knowledge_base` field) — but that coupling was already *designed for* (the
  vault rotation-hash schema exists); this wires the producer the schema anticipated.
- **Data ownership**: introduces a canonical/reference relationship for the Overview narrative —
  one location owns the content, the other links to it (AC2).
- **Reversibility**: high. New page + additive detector; the detector is advisory (files issues,
  no auto-rewrite), so it cannot corrupt content. Portrait/figures are committed assets.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer, designer (frontend/SVG), documentarian

**Interactions:**
- PM check-ins: 2-3 (the Major-drift reframing must be confirmed before build; scope of AC1
  detector; AC6 build/no-build call)
- Review rounds: 2+ (design review for the media-mix/portrait work; code review for the substrate
  detector)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `site/` on main | `test -f site/index.html && test -f site/runtime.html` | #2058 prerequisite satisfied |
| Vault accessible | `test -d "$HOME/work-vault/AI Valor Engels System"` | Source corpus present |
| Hedcut asset present | `test -f "$HOME/work-vault/AI Valor Engels System/files/valor-portraits-2026-07-14/valor-hedcut.png"` | AC5 portrait source |
| `projects.json` maps `valor` knowledge_base | `python -c "import json,os; d=json.load(open(os.path.expanduser('~/Desktop/Valor/projects.json'))); assert d['projects']['valor'].get('knowledge_base')"` | AC1 vault path resolution |

Run via `python scripts/check_prerequisites.py docs/plans/vault-site-integration.md`.

## Solution

### Key Elements

- **Vault↔{docs,site} drift detector** (`reflections/docs_auditor.py`): actually enumerate vault
  `*.md` (excluding `secrets/` + markitdown sidecars) and emit advisory drift findings comparing a
  canonical vault narrative against its mapped site page / repo doc. Replaces the deleted
  `/do-xref-audit` function inside the live substrate.
- **`secrets/` exclusion**: a single, unconditional exclusion constant applied to every vault
  enumeration path, with a test asserting no `secrets/` path is ever yielded.
- **Overview dedup**: designate one canonical home for the "Valor AI System Overview" narrative;
  the other location becomes a short reference/pointer.
- **`site/research.html`**: a new site page surfacing the 4 strategic decks, each with a line-art
  cover card in the house style.
- **Persona enrichment + hedcut portrait**: cropped hedcut derivatives in `site/assets/`; enriched
  provenance/three-hats sections from `Personas/` bios; monogram/line-art placeholders for
  portrait-less personas.
- **Media-mix upgrade**: one inline-SVG figure per currently text-only page where a diagram
  genuinely explains structure (per CEO art direction — strictly inside the existing design system).
- **"Shipped this week" feed — evaluation**: a written build/no-build decision (AC6), not a build.

### Flow

Vault edit or site edit → next `docs-auditor` daily rotation → vault + site inventoried
(`secrets/` excluded) → drift compared → advisory issue filed on divergence (deduped) → human
reconciles → canonical/reference stays in sync.

Site visitor → `index.html` → `§04 Who is behind this` (hedcut byline + persona depth) →
`§05 Where to go next` → new **Research** entry → `research.html` (4 deck cover cards).

### Technical Approach

- **AC1 lands in `reflections/docs_auditor.py`, not a skill.** Add vault enumeration
  (path from `projects.json` `knowledge_base`, matching the existing `do-xref-audit` orphan's
  resolution logic) + a vault↔page drift detector emitting advisory findings. Preserve the
  existing markdown-only apply guard — never auto-rewrite `site/*.html`.
- **`secrets/` exclusion is a single shared constant** applied at every vault-walk site, tested
  independently (an inverse/anti-criterion Verification row asserts no `secrets/` path is emitted).
- **Overview canonical decision (Q2)**: recommend the **site page as public canonical** and the
  vault Overview reduced to a pointer + private-only notes — but this is a judgment call flagged
  for the supervisor.
- **`research.html`** follows the exact structure of an existing authored page (clone
  `runtime.html`'s `<head>`, header, footer, and CSS classes). Cover cards use inline SVG +
  `.diagram-cap` captions. Add to `site/sitemap.xml`, the `§05` directory list, and page-next nav.
- **Hedcut crop** per CEO spec (square ≈ x210–810, y90–690 of the 1024² source; eyes upper-third):
  produce a ~360px byline derivative + a ~2× persona-section derivative, grayscale PNG, <100 KB,
  committed to `site/assets/`. The site references only `site/assets/` copies.
- **Persona/three-hats reconciliation (Q3)**: `runtime.html`'s "Three hats" = PM/Dev/Teammate
  *runtime roles*; the vault `Personas/` = HG Wells (Ops) / Jules Verne (Eng) / Philip Pullman
  (Product) *org-leadership* personas + Valor. These are different "three hats." The natural home
  for the org personas is `index.html §04`; the hedcut anchors it. Flag the mapping for confirmation.
- **AC6** is an evaluation deliverable: a short written build/no-build decision (in the plan's
  eventual feature doc or a vault note) weighing `daily-logs/` + `/weekly-review` against upkeep cost.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `reflections/docs_auditor.py` swallows exceptions throughout (Redis, git, gh) by design so
  the auditor never crashes the worker. The **new** vault-enumeration + drift code must follow the
  same swallow-and-log pattern, and each new `except` must have a test asserting a
  `logger.warning`/no-op observable outcome (not a silent pass). Specifically: vault path
  unresolvable → logged warning + empty candidate list (audit continues on repo docs).
- [ ] `secrets/`-exclusion filter must be tested as an assertion (present even when the walk
  errors partway), not just happy-path.

### Empty/Invalid Input Handling
- [ ] Vault root missing / empty / no `knowledge_base` in `projects.json` → detector yields zero
  vault candidates and the rotation audit proceeds on repo docs (no crash, logged).
- [ ] A vault narrative with no mapped site page → no drift finding (not a false positive).

### Error State Rendering
- [ ] `research.html` must render with valid, self-contained HTML (no broken asset refs); add a
  link-integrity check that every `site/assets/` reference on new/edited pages resolves to a
  committed file.
- [ ] Drift finding → surfaces as a real, deduped GitHub issue (advisory), not swallowed.

## Test Impact

- [ ] `tests/unit/test_docs_auditor_substrate.py` — UPDATE: add cases for vault enumeration,
  `secrets/` exclusion, markitdown-sidecar skip, and the vault↔page drift detector; assert the
  existing `docs/features/*.md`-only rotation behavior is preserved (no regression).
- [ ] No site-page tests exist today — a new lightweight HTML/link-integrity check for
  `site/*.html` (assets resolve, sitemap matches page set) is REPLACE-level new coverage, not a
  modification of existing tests.

No other existing tests are affected — the change is additive to the substrate and site; it does
not alter the `/do-docs` thin-caller contract or the markdown-only apply guard those tests assert.

## Rabbit Holes

- **Rebuilding a full parallel-agent LLM xref engine** like the deleted `/do-xref-audit`. The
  advisory drift detector should be a focused, mechanical/heuristic comparison inside the existing
  substrate — not a resurrection of the deleted skill's two-agent inventory pipeline.
- **Auto-syncing / auto-rewriting site or vault content on drift.** Detection is advisory (file an
  issue); a human reconciles. Auto-rewrite of `site/*.html` is explicitly guarded against and must
  stay that way.
- **Rendering the Marp decks to pixel-faithful HTML.** The Research page distills the *narrative*
  into house-style HTML; it does not reproduce deck slides. Linking the committed PDF is acceptable
  if a deck resists distillation.
- **Generating portraits for HG Wells / Jules Verne / Pullman.** They have none by design — use
  monograms / line-art placeholders. Do not commission or synthesize photoreal portraits.
- **Building the "shipped this week" feed before the AC6 evaluation says to.** AC6 is a decision,
  not a build.
- **Wiring vault docs into the auto-*fix* path.** Vault content is source-of-truth narrative; the
  auditor should not auto-edit vault files — advisory only.

## Risks

### Risk 1: Major-drift reframing is rejected by the supervisor
**Impact:** If the supervisor wants AC1 as a standalone skill (not a `docs_auditor` extension),
the substrate work is misdirected.
**Mitigation:** Q1 is the headline Open Question; build does not start until it is answered. The
reframing is documented in Freshness Check with code evidence so the decision is well-informed.

### Risk 2: `secrets/` leaks into a sweep, docs site, or filed issue
**Impact:** Plaintext-credential paths/content exposed in a public site or a GitHub issue —
security incident.
**Mitigation:** Single unconditional exclusion constant, applied at every vault-walk site, with a
dedicated test and an inverse Verification anti-criterion (grep asserts no `secrets/` reference in
any new site page or detector output). Non-negotiable across every pipeline stage.

### Risk 3: docs-auditor rotation regression
**Impact:** Adding vault selection could starve or destabilize the existing `docs/features/*.md`
rotation, or flood the tracker with vault drift issues.
**Mitigation:** Preserve the existing per-run issue cap (5 rotation) and two-tier dedup; keep the
vault-weight (0.5) so vault is sampled at half rate; assert existing rotation behavior in tests.

### Risk 4: Design drift from the house system
**Impact:** New figures/portrait break the GitHub-light editorial system (hairlines, mono labels,
monochrome + single blue accent).
**Mitigation:** Bind to CEO art direction; route the media-mix/portrait work through a design review
round before merge; reuse existing CSS tokens (`--border`, `--accent #0969da`, `.diagram-cap`).

## Race Conditions

No race conditions identified. The drift detector runs inside the single-threaded, SETNX-locked
`docs-auditor` rotation reflection (`docs_audit:running:global`, TTL 1h) that already serializes
runs; vault enumeration is read-only filesystem I/O. The site/portrait work is static-file authoring
with no concurrent access.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2059] Knowledge-graph regeneration (`site/assets/graph.js`) — distinct pipeline,
  tracked separately.
- Building the "shipped this week" feed itself — AC6 delivers only a written build/no-build
  *decision*; if the decision is "build," that is a follow-up. (Not tagged EXTERNAL/ORDERED/
  DESTRUCTIVE because the *decision* is fully in scope here; only the conditional downstream build
  is deferred, and it will get its own issue if greenlit.)
- [EXTERNAL] Rotating/relocating any credential — the `secrets/` relocation precondition is already
  done (2026-07-14); this plan only *excludes* `secrets/`, it does not touch credential content.
- [EXTERNAL] Production deploy of the site — `scripts/deploy-site.sh` runs post-merge on a machine
  with `wrangler` + the vault token; the agent cannot deploy from an arbitrary machine.

## Update System

- **`reflections/docs_auditor.py`** is the live substrate; the change ships with the repo — no
  `scripts/update/run.py` change needed for the detector itself.
- **Legacy cruft removal (update-system relevant):** the orphaned `~/.claude/skills/do-xref-audit/`
  and `~/.claude/skills/do-xref/` copies exist on this machine but are **not** git-tracked and are
  **not** produced by `scripts/update/hardlinks.py` (no `xref` entry there). They are stale sync
  residue. Per the no-legacy-code rule, add a `RENAMED_REMOVALS` entry in
  `scripts/update/hardlinks.py` so `/update` removes these stale hardlinks on every machine
  (confirm they are not re-created by any active sync source first).
- No new dependencies to propagate. `config/reflections.yaml` already registers `docs-auditor`;
  no new reflection is added (the detector is a new capability inside the existing one).
- Site deploy already handled: `docs/sdlc/do-merge.md` runs `scripts/deploy-site.sh` post-merge when
  the diff touches `site/`. No change needed.

## Agent Integration

No new agent/MCP tool surface is required. AC1 runs as part of the existing `docs-auditor` reflection
(scheduled, not agent-invoked) and the `/do-docs` SDLC stage — both already wired. The site pages are
static and reached by browser, not by the agent. No `mcp_servers/` or `.mcp.json` change. No
`bridge/telegram_bridge.py` import change.

- Integration coverage: the substrate's existing thin-caller contract test
  (`tests/unit/test_docs_auditor_substrate.py`) is extended to exercise the vault path; no new
  agent-invocation path is introduced.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/docs-auditor.md` to document the new vault enumeration + vault↔page
  drift detector and the `secrets/` exclusion (correct the current doc's overstated "vault docs are
  picked at half the rate" claim, which described a then-unwired schema hook).
- [ ] Create `docs/features/vault-site-integration.md` describing the standing vault↔site sync,
  the Research page, and the persona/portrait treatment; add it to `docs/features/README.md` index.
- [ ] Update `.claude/skill-context/do-docs.md` if the site inventory guidance needs to reference
  vault↔site drift.

### External Documentation Site
- [ ] Add `site/research.html`; update `site/sitemap.xml`, `index.html §05` directory, and
  page-next nav.
- [ ] Verify the site renders (link-integrity check; `scripts/deploy-site.sh` liveness on a
  deploy-capable machine).

### Inline Documentation
- [ ] Docstrings on the new vault-enumeration + drift-detector functions in `docs_auditor.py`.
- [ ] Comment the `secrets/` exclusion constant with the security rationale.

## Success Criteria

- [ ] `reflections/docs_auditor.py` enumerates vault `*.md` (excluding `secrets/` + markitdown
  sidecars) and emits advisory vault↔page drift findings; existing `docs/features/*.md` rotation
  behavior is preserved.
- [ ] `secrets/` is excluded from every vault sweep (asserted by a dedicated test and an inverse
  Verification row).
- [ ] The "Valor AI System Overview" narrative has one canonical home; the other location references it.
- [ ] `site/research.html` surfaces the 4 strategic decks with house-style line-art cover cards;
  sitemap + nav updated.
- [ ] `index.html §04` (and/or `runtime.html`) is enriched from the 4 `Personas/` bios with the
  cropped hedcut byline; portrait-less personas use monogram/line-art placeholders.
- [ ] A written build/no-build decision for the "shipped this week" feed is recorded.
- [ ] The site references only `site/assets/` copies — no vault paths.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (drift-detector)**
  - Name: `auditor-builder`
  - Role: vault enumeration + `secrets/` exclusion + vault↔page drift detector in `docs_auditor.py`
  - Agent Type: builder — Domain: Redis/Popoto + filesystem; untrusted-path safety
  - Resume: true
- **Builder (site-content)**
  - Name: `site-builder`
  - Role: `research.html`, Overview dedup, persona enrichment, sitemap/nav — house-style HTML
  - Agent Type: builder — Domain: frontend/design-system
  - Resume: true
- **Designer (media-mix)**
  - Name: `figure-designer`
  - Role: hedcut crop derivatives + inline-SVG cover cards/figures in the house idiom
  - Agent Type: designer
  - Resume: true
- **Validator**
  - Name: `integration-validator`
  - Role: verify `secrets/` never emitted, link-integrity, rotation non-regression, ACs met
  - Agent Type: validator
  - Resume: true
- **Documentarian**
  - Name: `docs-writer`
  - Role: feature docs + index + docs-auditor doc correction
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Vault enumeration + `secrets/` exclusion + drift detector
- **Task ID**: build-auditor
- **Depends On**: none (gated on Q1 answer)
- **Validates**: `tests/unit/test_docs_auditor_substrate.py` (update)
- **Assigned To**: auditor-builder
- **Agent Type**: builder
- **Parallel**: true
- Add vault path resolution from `projects.json` `knowledge_base`; enumerate vault `*.md`.
- Apply the single unconditional `secrets/` exclusion + markitdown-sidecar skip.
- Add the vault↔page (site/doc) drift detector as an advisory (issue-filing) finding, deduped.
- Preserve markdown-only apply guard and existing rotation/cap/dedup behavior.

### 2. Site content: Research page + Overview dedup + persona enrichment
- **Task ID**: build-site
- **Depends On**: none
- **Validates**: link-integrity check on `site/*.html`
- **Assigned To**: site-builder
- **Agent Type**: builder
- **Parallel**: true
- Author `site/research.html` (clone `runtime.html` scaffold); update sitemap + `§05` + nav.
- Reduce the non-canonical Overview location to a reference pointer (per Q2 answer).
- Enrich `index.html §04` (and/or `runtime.html` per Q3) from `Personas/` bios.

### 3. Figures + hedcut crop
- **Task ID**: build-figures
- **Depends On**: none
- **Assigned To**: figure-designer
- **Agent Type**: designer
- **Parallel**: true
- Crop `valor-hedcut.png` per CEO spec → `site/assets/` derivatives (<100 KB, grayscale).
- Produce inline-SVG cover cards for the 4 decks + monogram/line-art persona placeholders.
- Add one inline-SVG figure to text-only pages where a diagram genuinely explains structure.

### 4. AC6 evaluation write-up
- **Task ID**: build-eval
- **Depends On**: none
- **Assigned To**: site-builder
- **Agent Type**: builder
- **Parallel**: true
- Write the build/no-build decision for the "shipped this week" feed (`daily-logs/` + `/weekly-review`).

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-auditor, build-site, build-figures, build-eval
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/vault-site-integration.md` + README index entry.
- Correct + extend `docs/features/docs-auditor.md`.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Assert no `secrets/` path in any detector output or site page; link-integrity; rotation
  non-regression; every AC met.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| Lint clean | `python -m ruff check reflections/docs_auditor.py` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/docs_auditor.py` | exit code 0 |
| Research page exists | `test -f site/research.html && echo ok` | output contains ok |
| Research page in sitemap | `grep -c 'research.html' site/sitemap.xml` | output > 0 |
| No `secrets/` in site pages (anti-criterion) | `grep -rn 'secrets/' site/*.html` | match count == 0 |
| No vault absolute paths in site (anti-criterion) | `grep -rn 'work-vault' site/*.html` | match count == 0 |
| Hedcut derivative committed | `ls site/assets/ \| grep -c hedcut` | output > 0 |
| `secrets/` exclusion tested | `grep -c 'secrets' tests/unit/test_docs_auditor_substrate.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **[HEADLINE — Major drift] Confirm the AC1 reframing.** The issue names
   `.claude/skills/do-xref-audit/SKILL.md`, but that skill was **deleted** (consolidated into
   `reflections/docs_auditor.py`, "no replacement"), and the substrate **never implemented vault
   selection**. This plan proposes building the vault↔{docs,site} drift audit **into
   `reflections/docs_auditor.py`** (the live substrate) and removing the stale
   `~/.claude/skills/do-xref-audit/` orphan. Is that the right home, or do you want a fresh
   standalone skill/script instead?
2. **Overview canonical direction (AC2).** Recommend the **site page as public canonical**, vault
   Overview reduced to a pointer. Confirm — or is the vault the canonical source with the site
   referencing it?
3. **Persona mapping (AC5).** `runtime.html`'s "Three hats" = PM/Dev/Teammate *runtime roles*; the
   vault `Personas/` = HG Wells/Jules Verne/Pullman *org-leadership* personas + Valor — a different
   "three hats." Should the org personas land in `index.html §04 "Who is behind this"` (recommended,
   hedcut anchors it), in `runtime.html`, or both?
4. **Drift-detector strictness.** Should the vault↔page drift audit be a coarse "these two exist and
   one changed since the other" heuristic (low false-positive, cheap) or a semantic overlap
   comparison? Coarse is recommended to avoid resurrecting the deleted LLM-agent xref engine.
