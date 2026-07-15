---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-15
tracking: https://github.com/tomcounsell/ai/issues/2084
last_comment_id: 4976296145
revision_applied: true
revision_applied_at: 2026-07-15T03:02:07Z
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
rather than editing a deleted skill). Per the do-plan freshness protocol, this drift was
documented loudly and escalated rather than silently built around.

**Reframing APPROVED by supervisor (2026-07-15):** build the drift audit into
`reflections/docs_auditor.py`, and remove the stale `~/.claude/skills/do-xref-audit/` orphan via
a `RENAMED_REMOVALS` entry in `scripts/update/hardlinks.py`. The issue's desired outcome
(standing repeatable drift audit, `secrets/` always excluded) is unchanged; only the
implementation target moves from the deleted skill to its live replacement. CRITIQUE should
treat this reframing as the settled premise.

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
vestigial). No further prototype spikes were dispatched because the remaining forks were design/
scope decisions requiring human judgment (now settled — see Resolved Questions), not
code-verifiable facts.

## Data Flow

**AC1 — vault↔{docs,site} drift audit (rotation caller, `reflections/docs_auditor.py`):**
1. **Entry point**: `docs-auditor` daily rotation reflection → `run_docs_auditor()` →
   `audit(scope_mode='rotation', project_key='valor')`.
2. **Vault path resolution** (new): read `~/Desktop/Valor/projects.json`, resolve `valor`'s
   `knowledge_base` → vault root. **Enumerate ALL vault `*.md` every run** (full-sweep model —
   see the BLOCKER resolution note below), **excluding `secrets/`** and markitdown sidecars
   (`generated_by: markitdown` frontmatter). This is a read-only filesystem walk, cheap and
   deterministic; it does NOT go through the weighted single-pick rotation used for
   `docs/features/*.md`.
3. **Overlap/drift detection** (new): for each canonical vault narrative, compare against its
   mapped site page (`site/*.html`) and repo doc; increment a `vault_narratives_compared` counter
   for each narrative actually compared, and emit a drift finding (file-as-issue, advisory) when
   the two have diverged. `secrets/` never enters the sweep.
4. **Per-run issue cap** (new): a named constant `VAULT_DRIFT_ISSUE_CAP` (defined next to
   `NEIGHBORHOOD_CAP` at `docs_auditor.py:53`) bounds how many vault-drift `gh issue create` calls
   a single run may make. The cap is checked BEFORE any issue is filed; once reached, remaining
   drift findings are logged and skipped (not filed) so the full-sweep model cannot flood the
   tracker. This replaces the vestigial vault-weight/half-rate sampling — see Risk 3.
5. **Output**: advisory GitHub issue(s) tagged `documentation` on drift (bounded by
   `VAULT_DRIFT_ISSUE_CAP`), deduped via the existing two-tier gate; plus a
   `vault_narratives_compared` count emitted into the liveness payload (`_write_liveness`,
   `docs_auditor.py:1269`) so "zero drift" is distinguishable from "broken mapping". No auto-rewrite
   of `site/*.html` (existing markdown-only apply guard preserved).

> **BLOCKER resolution (critique, 2026-07-15) — one model chosen: enumerate-all + named cap.**
> The prior draft mixed two incompatible drift mechanisms: full-sweep enumeration here vs. a
> "vault-weight (0.5) sampled at half rate" weighted single-pick in Risk 3. Verified against code:
> `_select_primary_doc` (`docs_auditor.py:1064-1099`) accepts `vault_weight` but never reads it and
> only globs `docs/features/*.md`; `DEFAULT_VAULT_WEIGHT` (:71) and `_vault_field` (:181) are
> vestigial. Per supervisor preference (deterministic, testable, keeps the audit standing and
> repeatable as the issue demands), AC1 uses **enumerate-all every run + a named
> `VAULT_DRIFT_ISSUE_CAP`**. The vault-weight/half-rate language is deleted from Risk 3, and the
> dead `DEFAULT_VAULT_WEIGHT` constant + `vault_weight` param path are removed as part of build
> (no dead code the Risk section describes as functional).

**AC4 — Research page (build-time authored content):**
1. **Entry point**: builder authors `site/research.html` from vault `*-report.md` narratives.
2. **Transform**: distill each of the 4 decks into house-style HTML + a line-art cover card.
3. **Output**: committed `site/research.html` + `site/assets/` figures; `site/sitemap.xml` +
   nav/`§05` directory updated; deployed post-merge via `scripts/deploy-site.sh`.

**AC5 — persona enrichment + hedcut portrait:**
1. **Entry point**: crop `valor-hedcut.png` (vault `files/valor-portraits-2026-07-14/`) → optimized
   `site/assets/` derivatives (never reference the vault path from the site).
2. **Transform**: distill the 4 `Personas/` bios into house-style copy.
3. **Output**: enriched `index.html §04 "Who is behind this"` (byline treatment) and
   `runtime.html` "Three hats" section — the two *existing* sections, no new page structure —
   with monogram/line-art placeholders for the portrait-less personas.

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

- **Vault↔{docs,site} drift detector** (`reflections/docs_auditor.py`): enumerate ALL vault
  `*.md` every run (full-sweep, excluding `secrets/` + markitdown sidecars) and emit advisory drift
  findings comparing a canonical vault narrative against its mapped site page / repo doc. Replaces
  the deleted `/do-xref-audit` function inside the live substrate. **Advisory/report-only at launch
  (Q4 — RESOLVED)**: a coarse changed-since heuristic that files deduped issues, consistent with the
  docs-auditor reflection's dry-run-first convention — never blocking, never auto-rewriting.
- **`VAULT_DRIFT_ISSUE_CAP`**: a named per-run cap constant (defined next to `NEIGHBORHOOD_CAP` at
  `docs_auditor.py:53`), checked before every vault-drift `gh issue create`, that bounds the
  full-sweep model's issue volume so it cannot flood the tracker. Replaces the removed vestigial
  `DEFAULT_VAULT_WEIGHT` sampling.
- **`vault_narratives_compared` liveness count**: emit a per-run count of narratives actually
  compared into the `_write_liveness` payload so "detector ran, found zero drift" is distinguishable
  from "narrative→page mapping is silently empty/broken".
- **`secrets/` exclusion**: a single, unconditional exclusion constant applied to every vault
  enumeration path, using a **path-component match on the resolved path** (see Technical Approach for
  exact semantics), with a test asserting no `secrets/` path is ever yielded — including on a
  partial walk interrupted by `OSError`.
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
  resolution logic) that enumerates ALL vault `*.md` every run + a vault↔page drift detector
  emitting advisory findings. This runs beside `_select_primary_doc` (which is untouched and keeps
  globbing `docs/features/*.md` only), NOT through it. Preserve the existing markdown-only apply
  guard — never auto-rewrite `site/*.html`.
- **`VAULT_DRIFT_ISSUE_CAP` constant** is defined alongside `NEIGHBORHOOD_CAP` (`docs_auditor.py:53`)
  and checked before every vault-drift `gh issue create`. Suggested value: `5` (parity with the
  existing rotation issue budget); the exact number is a build-time tunable but the cap MUST exist
  and MUST be enforced before any issue is filed. Remove the now-dead `DEFAULT_VAULT_WEIGHT` (:71)
  and the unused `vault_weight` parameter path so the Risk section describes no dead code.
- **`secrets/` exclusion is a single shared constant** applied at every vault-walk site. **Matching
  semantics (exact, per critique):** a path is excluded iff any *path component* (not substring)
  equals `secrets` case-insensitively, evaluated on the **resolved** path relative to the resolved
  vault root — i.e. the predicate is
  `any(part.lower() == "secrets" for part in path.resolve().relative_to(vault_root.resolve()).parts)`.
  This resolves symlinks first (a symlinked `secrets` tree is still excluded) and does NOT
  over-match siblings like `secrets-analysis.md` or `Secretsandbox/` (component equality, not
  prefix/substring). The filter is applied AFTER path resolution AND ALSO to any results already
  collected before a mid-walk `OSError` — nothing collected is flushed unfiltered (ties into the
  Failure Path partial-walk assertion). Tested independently (an inverse/anti-criterion Verification
  row asserts no `secrets/` path is emitted, including the partial-walk case).
- **`vault_narratives_compared` count** is threaded from the drift detector into `_write_liveness`
  (`docs_auditor.py:1269`, added to the `summary` dict) — nonzero whenever `knowledge_base` resolves
  to a populated vault; a test asserts the count is nonzero for a populated vault and `0` (not
  missing) when the vault is empty/unresolvable, so a silently-broken mapping is observable.
- **Overview canonical decision (Q2 — RESOLVED)**: the **vault is canonical** — the vault is the
  human-curated source of truth per repo `CLAUDE.md` (Knowledge Base section). The vault's
  `Valor AI System Overview.md` owns the narrative; the site's Overview content references and
  derives from it (with a provenance note on the site side pointing at the canonical source).
- **`research.html`** follows the exact structure of an existing authored page (clone
  `runtime.html`'s `<head>`, header, footer, and CSS classes). Cover cards use inline SVG +
  `.diagram-cap` captions. Add to `site/sitemap.xml`, the `§05` directory list, and page-next nav.
- **Hedcut crop** per CEO spec (square ≈ x210–810, y90–690 of the 1024² source; eyes upper-third):
  produce a ~360px byline derivative + a ~2× persona-section derivative, grayscale PNG, <100 KB,
  committed to `site/assets/`. The site references only `site/assets/` copies.
- **Persona/three-hats reconciliation (Q3 — RESOLVED)**: map the four `Personas/` bios onto the
  site's **existing** sections — `index.html §04 "Who is behind this"` and `runtime.html`'s
  "Three hats, one runner" — and do not invent new page structure beyond the Research page.
  Note for the builder: `runtime.html`'s "Three hats" describes PM/Dev/Teammate *runtime roles*
  while the vault `Personas/` are *org-leadership* personas (HG Wells/Ops, Jules Verne/Eng,
  Philip Pullman/Product) + Valor — the enrichment must respect that distinction (bios add depth
  and voice; they do not overwrite the runtime-role semantics). Hedcut byline anchors `§04`.
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
  errors partway), not just happy-path. Specifically: the component-match predicate is applied to
  results already collected before a mid-walk `OSError`, so a partial walk never flushes a
  `secrets/` path unfiltered. Test with mixed-case (`Secrets/`) and near-miss (`secrets-analysis.md`,
  which must NOT be excluded) inputs.

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

- [ ] `tests/unit/test_docs_auditor_substrate.py` — UPDATE: add cases for full-sweep vault
  enumeration, `secrets/` exclusion (path-component match; mixed-case, near-miss, and partial-walk
  cases), markitdown-sidecar skip, `VAULT_DRIFT_ISSUE_CAP` enforcement (more drift than cap files at
  most cap issues), `vault_narratives_compared` nonzero on a populated vault / `0` on empty, and the
  vault↔page drift detector; assert the existing `docs/features/*.md`-only rotation behavior is
  preserved (no regression) and that `_select_primary_doc` still globs only `docs/features/*.md`.
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

### Risk 1: Major-drift reframing misdirects the build (RESOLVED)
**Impact:** If AC1 belonged in a standalone skill (not a `docs_auditor` extension), the substrate
work would be misdirected.
**Mitigation:** Resolved — the supervisor APPROVED the reframing (2026-07-15); the substrate is
the confirmed home. The Freshness Check records the code evidence behind the decision.

### Risk 2: `secrets/` leaks into a sweep, docs site, or filed issue
**Impact:** Plaintext-credential paths/content exposed in a public site or a GitHub issue —
security incident.
**Mitigation:** Single unconditional exclusion constant, applied at every vault-walk site, using a
**path-component match on the resolved path** —
`any(part.lower() == "secrets" for part in path.resolve().relative_to(vault_root.resolve()).parts)`
— NOT a substring check (a naive `"secrets/" in str(path)` would miss `Secrets/` and a symlinked
tree, and over-match `secrets-analysis.md`). Applied after resolution and also to results collected
before any mid-walk `OSError`. Backed by a dedicated test (including the partial-walk case) and an
inverse Verification anti-criterion (grep asserts no `secrets/` reference in any new site page or
detector output). Non-negotiable across every pipeline stage.

### Risk 3: docs-auditor rotation regression
**Impact:** Adding vault enumeration could starve or destabilize the existing `docs/features/*.md`
rotation, or flood the tracker with vault drift issues.
**Mitigation:** The vault sweep runs **beside** the existing rotation, not through
`_select_primary_doc` — it does not touch the `docs/features/*.md` single-pick selection or its
rotation hash, so it cannot starve the existing rotation. Vault-drift issue volume is bounded by
the named `VAULT_DRIFT_ISSUE_CAP` constant (checked before every `gh issue create`) plus the
existing two-tier dedup gate. Assert existing rotation behavior is unchanged in tests, and assert
the cap is enforced (a run with more drift than the cap files at most `VAULT_DRIFT_ISSUE_CAP`
issues). The vestigial `DEFAULT_VAULT_WEIGHT` / `vault_weight` sampling is removed (see the AC1
BLOCKER resolution note) — it was never wired to a producer, so there is no half-rate sampling to
preserve.

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
- [ ] Update `docs/features/docs-auditor.md` to document the new full-sweep vault enumeration +
  vault↔page drift detector, the `VAULT_DRIFT_ISSUE_CAP`, the `secrets/` exclusion semantics
  (path-component match on resolved path), and the `vault_narratives_compared` liveness count.
  **Correct** the current doc's overstated "vault docs are picked at half the rate" claim and note
  the vestigial `DEFAULT_VAULT_WEIGHT` / `vault_weight` path was removed (it was never wired to a
  producer).
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

- [ ] `reflections/docs_auditor.py` enumerates ALL vault `*.md` every run (excluding `secrets/` +
  markitdown sidecars) and emits advisory vault↔page drift findings bounded by
  `VAULT_DRIFT_ISSUE_CAP`; existing `docs/features/*.md` rotation behavior is preserved and the
  vestigial `DEFAULT_VAULT_WEIGHT` / `vault_weight` path is removed.
- [ ] `VAULT_DRIFT_ISSUE_CAP` is enforced before any vault-drift `gh issue create` (asserted: a run
  with more drift than the cap files at most `VAULT_DRIFT_ISSUE_CAP` issues).
- [ ] `vault_narratives_compared` is emitted into the liveness payload and is nonzero whenever
  `knowledge_base` resolves to a populated vault (asserted by test).
- [ ] `secrets/` is excluded from every vault sweep via path-component match on the resolved path,
  including on a partial walk interrupted by `OSError` (asserted by a dedicated test — mixed-case +
  near-miss inputs — and an inverse Verification row).
- [ ] The "Valor AI System Overview" narrative has one canonical home — the **vault** — and the
  site references/derives from it.
- [ ] `site/research.html` surfaces the 4 strategic decks with house-style line-art cover cards;
  sitemap + nav updated.
- [ ] The existing `index.html §04` and `runtime.html` three-hats sections are enriched from the
  4 `Personas/` bios with the cropped hedcut byline; portrait-less personas use monogram/line-art
  placeholders; no new page structure beyond the Research page.
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
  - Role: two independent validation lanes — **backend** (`validate-backend`: `secrets/` never
    emitted incl. partial-walk, `VAULT_DRIFT_ISSUE_CAP` enforced, `vault_narratives_compared`
    nonzero, rotation non-regression) and **frontend** (`validate-frontend`: no vault path in site,
    link-integrity, sitemap, ACs met). Neither lane depends on the other; each gates its own PR.
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
- **Depends On**: none
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
- Make the vault Overview canonical (Q2): the site's Overview content references/derives from it,
  with a provenance note pointing at the canonical vault source.
- Enrich the existing `index.html §04` and `runtime.html` three-hats sections (Q3) from
  `Personas/` bios — no new page structure beyond the Research page.

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

> **Two-lane structure (critique CONCERN, 2026-07-15).** The security-sensitive backend
> (`build-auditor`: `secrets/` exclusion + drift detector) and the subjective frontend/design work
> (`build-site`/`build-figures`/`build-eval`) are split into two independent documentation+validation
> lanes so the security fix is **not held hostage by design-review iteration**. This stays **one plan
> / one slug / one issue (#2084)** but ships as **two PRs at build time**: the backend PR merges as
> soon as its lane is green; the frontend PR follows after design review. Neither lane's validation
> depends on the other. (This is the conscious "one plan, two PRs" decision the critic named as an
> acceptable resolution.)

### 5a. Backend documentation (lane: backend)
- **Task ID**: document-auditor
- **Depends On**: build-auditor
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Correct + extend `docs/features/docs-auditor.md` (vault enumeration, `VAULT_DRIFT_ISSUE_CAP`,
  `secrets/` exclusion semantics, `vault_narratives_compared`; correct the overstated "half the
  rate" claim and note `DEFAULT_VAULT_WEIGHT` was removed).
- Create the AC1/security portion of `docs/features/vault-site-integration.md`.

### 5b. Backend validation (lane: backend) — gates the backend PR
- **Task ID**: validate-backend
- **Depends On**: document-auditor
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Assert no `secrets/` path in any detector output (incl. partial-walk); `VAULT_DRIFT_ISSUE_CAP`
  enforced; `vault_narratives_compared` nonzero on a populated vault; rotation non-regression.
  **Does NOT depend on the frontend lane** — backend PR merges when this is green.

### 5c. Frontend documentation (lane: frontend)
- **Task ID**: document-site
- **Depends On**: build-site, build-figures, build-eval
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Complete the site/persona/Research portion of `docs/features/vault-site-integration.md` +
  README index entry; update `.claude/skill-context/do-docs.md` if site-inventory guidance needs it.

### 6. Frontend validation (lane: frontend) — gates the frontend PR
- **Task ID**: validate-frontend
- **Depends On**: document-site
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Assert no `secrets/` / vault path in any site page; link-integrity; sitemap matches page set;
  hedcut derivative committed; every frontend AC met. **Does NOT depend on the backend lane.**

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
| `VAULT_DRIFT_ISSUE_CAP` defined | `grep -c 'VAULT_DRIFT_ISSUE_CAP' reflections/docs_auditor.py` | output > 0 |
| Vault-weight dead code removed | `grep -c 'DEFAULT_VAULT_WEIGHT\|vault_weight' reflections/docs_auditor.py` | output == 0 |
| `vault_narratives_compared` emitted | `grep -c 'vault_narratives_compared' reflections/docs_auditor.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) 2026-07-15. Verdict: NEEDS REVISION (1 blocker). Revision applied 2026-07-15 — all four findings resolved. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + Scope & Value + History & Consistency (all 3) | AC1 describes two incompatible mechanisms: Data Flow says "enumerate ALL vault `*.md` every run and compare to mapped pages" (:133-135), but Risk 3's mitigation relies on "vault-weight (0.5) sampled at half rate" (:322-323) — a weighted single-pick rotation model. `_select_primary_doc` (docs_auditor.py:1064-1099) accepts `vault_weight` but never reads it and only globs `docs/features/*.md`; the plan's own Freshness Check + Documentation task confirm the weight is vestigial. No explicit per-run cap bounds vault-drift issue volume for the enumerate-all path, so it can flood the tracker. | **RESOLVED (revision 2026-07-15):** chose the **enumerate-all + named cap** model (supervisor preference). Data Flow AC1 now states full-sweep enumeration explicitly + a BLOCKER-resolution note; a named `VAULT_DRIFT_ISSUE_CAP` (next to `NEIGHBORHOOD_CAP`, :53) is checked before every `gh issue create`; the vault-weight/half-rate language is deleted from Risk 3 and `DEFAULT_VAULT_WEIGHT` / `vault_weight` are slated for removal as dead code (Success Criteria + Verification assert `grep` count == 0). | Pick ONE model in the plan text. If enumerate-all: add a named cap constant (e.g. `VAULT_DRIFT_ISSUE_CAP`) next to `NEIGHBORHOOD_CAP` (docs_auditor.py:53) enforced before any `gh issue create`, and delete the vault-weight/half-rate language from Risk 3. If weighted rotation: fold vault paths into `_select_primary_doc`'s `candidates` list keyed by `_vault_field(project_key, path)` (:181) with `vault_weight` applied to the sort key, and drop the "enumerate all every run" wording from Data Flow. Do not leave `vault_weight` as dead code the Risk section describes as functional. |
| CONCERN | Risk & Robustness | The `secrets/` exclusion (a Risk 2 "security incident" control) never specifies matching semantics — case sensitivity, symlink resolution, path-component vs. substring, depth. A naive `"secrets/" in str(path)` check under/over-matches (e.g. `Secrets/`, a symlinked secrets tree, or `secrets-analysis.md`). | **RESOLVED (revision 2026-07-15):** Technical Approach + Risk 2 + Failure Path now spell out the exact predicate `any(part.lower() == "secrets" for part in path.resolve().relative_to(vault_root.resolve()).parts)` — path-component equality (not substring), case-insensitive, on the resolved path, applied after resolution and to partial-walk results. Tests cover mixed-case + near-miss + partial-walk. | Use `any(part.lower() == "secrets" for part in path.resolve().relative_to(vault_root.resolve()).parts)` as the excluded-path predicate, not substring match; apply it AFTER path resolution and ALSO to results already collected before a mid-walk `OSError` (ties into the Failure Path partial-walk assertion at :260-261) so nothing is flushed unfiltered. |
| CONCERN | Scope & Value | The plan bundles a security-sensitive backend substrate change (`build-auditor`: secrets/ exclusion + drift detector, :451-461) with subjective frontend/design work (`build-figures`: hedcut crop, SVG cards, persona copy, :476-484) behind one shared merge gate — `document-feature`/`validate-all` depend on all four build tasks (:494-510). The Appetite section itself names two review lenses (design vs. code, :178-179), signalling separable workstreams. | **RESOLVED (revision 2026-07-15):** adopted the "one plan, two PRs at build time" decision. Step by Step now has two independent lanes: backend (`document-auditor`→`validate-backend`, depends only on `build-auditor`) and frontend (`document-site`→`validate-frontend`, depends on build-site/figures/eval). Neither lane's validation depends on the other, so the security fix is no longer held hostage by design-review iteration. | No code change — restructure tasks: give the backend workstream (AC1 + secrets/) its own `document-feature`/`validate-all` pair separate from the frontend workstream (AC2/AC4/AC5/media-mix/AC6), and drop `build-auditor` from the documentation task's prerequisites (:496) so the security fix isn't held hostage by design-review iteration. (Advisory: may be deferred as a conscious "one plan, two PRs at build time" decision.) |
| NIT | Risk & Robustness | No observable signal distinguishes "detector ran, found zero drift" from "detector's narrative→page mapping table is silently empty/broken" — both yield zero findings, so a broken mapping goes unnoticed. | **RESOLVED (revision 2026-07-15):** Data Flow AC1 + Technical Approach + Success Criteria + Verification now require a per-run `vault_narratives_compared` count threaded into the `_write_liveness` summary dict (:1269), asserted nonzero for a populated vault and `0` (not missing) for an empty/unresolvable vault. | Emit a per-run `vault_narratives_compared` count into the existing `_write_liveness` payload (referenced docs_auditor.py:~1343) and assert it is nonzero whenever `knowledge_base` resolves to a populated vault. |

---

## Resolved Questions

All open questions were answered by the supervisor on 2026-07-15; decisions are incorporated
into the sections above and recorded here for CRITIQUE/BUILD traceability:

1. **AC1 reframing (headline, Major drift): APPROVED.** Build the vault↔{docs,site} drift audit
   into `reflections/docs_auditor.py` (the live substrate); remove the stale
   `~/.claude/skills/do-xref-audit/` orphan via a `RENAMED_REMOVALS` entry in
   `scripts/update/hardlinks.py`. Desired outcome unchanged; only the implementation target moved.
2. **Overview canonical home: the vault.** The vault is the human-curated source of truth per repo
   `CLAUDE.md`; `Valor AI System Overview.md` is canonical and the site's Overview page
   references/derives from it.
3. **Persona mapping: existing sections only.** Map the four `Personas/` bios onto the site's
   existing "Who is behind this" (`index.html §04`) and Runtime three-hats (`runtime.html`)
   sections; no new page structure beyond the Research page.
4. **Drift-detector strictness: advisory/report-only at launch.** Coarse changed-since heuristic,
   consistent with the docs-auditor reflection's dry-run-first convention; not blocking.
