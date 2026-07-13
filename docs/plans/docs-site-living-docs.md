---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2058
last_comment_id: none
---

# Bring the valorengels.com docs site into main as living documentation

## Problem

The static docs site serving https://valorengels.com lives only on branch `docs/valor-site` (`site/` — 22 files, force-added past a stale `.gitignore` rule — plus `wrangler.jsonc` and `src/index.js`). It went live on 2026-07-13 and is already drifting toward staleness by construction: every documentation tool in this repo — the `/do-docs` cascade, the semantic doc-impact finder, the stale-reference sweep — only sees markdown files on `main`. When code changes rename a concept, retire a pattern, or restructure a subsystem, the cascade updates `docs/features/*.md` and `CLAUDE.md` but has no idea `site/runtime.html` describes the same subsystem to the public.

**Current behavior:**
Site pages are a frozen snapshot on a side branch. A `/do-docs` cascade after any code change cannot discover, sweep, or update them. Nothing prompts a redeploy when site content changes.

**Desired outcome:**
`site/` lives on `main` as a first-class documentation location. `/do-docs` inventories site pages, sweeps them for retired terms, edits them alongside markdown docs, and knows a commit touching `site/` implies a `wrangler deploy`. The gitignore hack is gone.

## Freshness Check

**Baseline commit:** `2e550ba2` (main, plan time 2026-07-13)
**Issue filed at:** 2026-07-13T07:49:18Z (#2058, filed minutes before this plan — recon was performed live during planning)
**Disposition:** Unchanged

**File:line references re-verified (at plan time):**
- `.gitignore:121` and `.gitignore:322` — duplicate boilerplate `/site` rules under "# mkdocs documentation" comments — confirmed present
- `tools/doc_impact_finder.py:58` — `DOC_PATTERNS` lists only `docs/**/*.md`, `CLAUDE.md`, skills/commands markdown, and persona/identity files — confirmed
- `tools/impact_finder_core.py:97` — `chunk_markdown` splits on `## ` heading lines; HTML content would land in a single unstructured chunk — confirmed
- `.claude/skill-context/do-docs.md` — "Doc inventory locations" table and "Stale-reference sweep paths" have no `site/` entry — confirmed
- `reflections/docs_auditor.py` — markdown-link-regex based throughout (`\[..\]\(..\.md\)`); HTML out of scope by construction — confirmed
- `origin/main..docs/valor-site` — 5 commits, 22 files, 38,780 insertions, **zero modifications to files that exist on main** — merge is trivially clean

**Active plans in `docs/plans/` overlapping this area:** none. The predecessor plan (`plans/valorengels-site-hosting.md`, on the branch at repo root — a pre-convention location) is status **done** and is relocated to `docs/plans/completed/` by this plan.

## Prior Art

- **`plans/valorengels-site-hosting.md`** (on `docs/valor-site`): shipped the hosting itself — wrangler config, custom domains, www redirect, 404/robots/sitemap/og-image, vault-`.env` credentials. Explicitly deferred "Merge `docs/valor-site` to main … drop the `/site` gitignore rule if `site/` becomes permanent" — this plan is that deferral, plus the living-docs integration.
- **Issue #2059**: knowledge-graph regeneration pipeline — filed during this planning pass as the separate-scope companion (machine-generated `graph.js` refresh vs. this plan's hand-written-copy integration).
- No closed issues or merged PRs touch `site/` or the doc-impact finder's pattern list in a conflicting way.

## Research

No relevant external findings — proceeding with codebase context and training data. (The only external surface, `wrangler deploy`, shipped and was verified live in the predecessor plan; no new external libraries or APIs are involved.)

## Data Flow

The `/do-docs` cascade after this plan, for a code change that renames a concept the site describes:

1. **Entry point**: `/do-docs` invoked with a PR/commit/description; Agent A extracts key terms and retired terms
2. **Agent B (inventory)**: scans the locations declared in `.claude/skill-context/do-docs.md` — now including `site/*.html` — and lists each page with its topics
3. **Agent C (semantic finder)**: `tools/doc_impact_finder.py` indexes `site/*.html` (HTML preprocessed to heading-delimited text, then `chunk_markdown`), so conceptually-related site pages surface even without keyword overlap
4. **Step 2b (stale sweep)**: `rg <retired-term>` now includes `site/` in its path list
5. **Step 3 (edits)**: surgical edits to affected `site/*.html` sections like any other doc; `site/sitemap.xml` updated if pages are added/removed
6. **Output**: commit includes site edits; the skill-context declares the deploy rule — if anything under `site/` changed, run `wrangler deploy` when available on this machine (guarded, non-fatal), otherwise report "site changed — redeploy needed" in the cascade summary

## Architectural Impact

- **New dependencies**: none — HTML preprocessing uses stdlib `html.parser`
- **Interface changes**: `DOC_PATTERNS` gains one glob; `_discover_doc_files`/indexing gains an HTML-to-text preprocessing branch keyed on file suffix
- **Coupling**: none new — the skill-context seam (`.claude/skill-context/do-docs.md`) is the designed extension point; the global `do-docs` skill body is untouched
- **Data ownership**: `site/` moves from branch-only to main; deploys keep reading credentials from the vault `.env`
- **Reversibility**: high — revert the merge commit and re-add the gitignore rule

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (this plan review)
- Review rounds: 1 (PR review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `docs/valor-site` branch reachable | `git fetch origin docs/valor-site && git rev-parse origin/docs/valor-site` | Merge source |
| `wrangler` on PATH (deploy verification only) | `command -v wrangler` | Post-merge redeploy-from-main proof; machine-local, build itself does not require it |
| `CLOUDFLARE_API_TOKEN` in vault `.env` (deploy verification only) | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('CLOUDFLARE_API_TOKEN')"` | Same |

## Solution

### Key Elements

- **Merge**: land `origin/docs/valor-site` on the feature branch, drop both `/site` gitignore rules, relocate the completed hosting plan to `docs/plans/completed/`
- **Discovery (inventory + sweep)**: declare `site/*.html` in `.claude/skill-context/do-docs.md` — inventory table row, stale-sweep path, sitemap-maintenance note, and a guarded deploy-on-site-change rule
- **Discovery (semantic)**: add `site/*.html` to `DOC_PATTERNS` with an HTML→heading-delimited-text preprocessor so `chunk_markdown` produces per-section chunks
- **Feature doc**: `docs/features/valorengels-site.md` as the canonical reference for the site — what it is, redeploy path, living-docs integration, graph.js snapshot caveat

### Flow

Code change merges → `/do-docs` cascade → site pages inventoried/swept/matched like markdown docs → surgical HTML edits committed → guarded `wrangler deploy` (or explicit "redeploy needed" report) → https://valorengels.com stays current

### Technical Approach

- Merge is additive-only (verified: no shared files between branch delta and main), so no conflict handling is needed
- HTML preprocessing: strip tags with stdlib `html.parser`, emitting `<h2>`/`<h3>` text as `## `/`### ` lines so the existing `chunk_markdown` and index/rerank pipeline work unchanged; skip `site/assets/` entirely (pattern only matches `site/*.html`, so `graph.js` — 38k lines of data — never reaches the embedder)
- The global `do-docs` SKILL.md body is **not** edited — all repo-specific behavior lands in `.claude/skill-context/do-docs.md`, per the skill-context convention (`docs/features/skill-context-convention.md`)
- Deploy rule wording in the skill-context: after the cascade's commit, if `git diff --name-only <before>..HEAD -- site/` is non-empty AND `command -v wrangler` succeeds, run `wrangler deploy` (never fail the cascade on deploy errors — report them); otherwise append "site/ changed — redeploy needed (`wrangler deploy` from a machine with the vault token)" to the cascade summary
- `wrangler.jsonc` and `src/index.js` land at repo root as-is (working deployed config; renaming/moving them is churn with zero benefit)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The HTML preprocessor must not raise on malformed/empty HTML — stdlib `HTMLParser` is tolerant by design; test that garbage input yields text output (possibly empty), never an exception
- [ ] No `except Exception: pass` blocks are added; indexing already logs and skips unreadable files in `impact_finder_core` — no change to that behavior

### Empty/Invalid Input Handling
- [ ] Test: HTML page with no `<h2>` headings → single preamble chunk (mirrors existing markdown behavior)
- [ ] Test: empty HTML file → zero chunks, no crash
- [ ] Deploy guard: `wrangler` absent → cascade reports redeploy-needed and continues (documented in skill-context; no code path to test in this repo)

### Error State Rendering
- [ ] Cascade summary must state deploy outcome explicitly (deployed / failed / skipped-report) — declared in the skill-context wording so the do-docs report renders the error rather than swallowing it

## Test Impact

No existing tests affected — `tests/unit/test_doc_impact_finder.py` builds its indexes from tmp-path fixture repos driven by the `DOC_PATTERNS` globs; an added `site/*.html` pattern matches nothing in existing fixtures, and no test asserts the contents of the pattern list itself. Changes are purely additive.

New tests (created, not modified):
- [ ] `tests/unit/test_doc_impact_finder.py::TestHtmlDocs` — UPDATE (additive): HTML file discovered via `site/*.html` fixture; heading-delimited chunking; no-headings single chunk; empty-file no crash; `site/assets/graph.js` NOT discovered

## Rabbit Holes

- **Regenerating the knowledge graph** — `graph.js` has no in-repo generator; rebuilding the machine-analysis pipeline is its own project. Filed as #2059; this plan only integrates the hand-written page copy.
- **Making `reflections/docs_auditor.py` HTML-aware** — the substrate is markdown-link-regex based end to end; teaching it HTML is a rewrite for marginal auto-fix value. Site pages get manual edits in cascade Step 3.
- **Converting the site to a static-site generator** — the site is deliberately no-build, self-contained HTML. Keep it that way.
- **CI auto-deploy** — the predecessor plan already scoped this to "low priority"; the guarded deploy rule in the skill-context covers the practical need.

## Risks

### Risk 1: Graph-derived site content stays stale even after this plan
**Impact:** Stats and per-file summaries on the live site describe commit `c06b9fa6` regardless of cascades — visitors may see outdated numbers.
**Mitigation:** #2059 tracks the regeneration pipeline; the feature doc explicitly labels graph content as a snapshot with a pointer to #2059, so the caveat is documented rather than silent.

### Risk 2: Cascades edit site pages but nobody redeploys
**Impact:** The repo and the live site diverge — worse than before, because the repo now *looks* authoritative.
**Mitigation:** The deploy rule in the skill-context makes deploy-or-report a mandatory cascade step whenever `site/` is touched; the report string names the exact command.

### Risk 3: HTML chunks pollute the embedding index or rerank quality
**Impact:** Noisy chunks lower semantic-finder precision for all docs.
**Mitigation:** Preprocessing strips tags/scripts before embedding; `site/assets/` never matches the glob; existing `MIN_SIMILARITY_THRESHOLD` and Haiku rerank already filter weak candidates.

## Race Conditions

No race conditions identified — all touched surfaces (merge, gitignore edit, skill-context markdown, `DOC_PATTERNS` indexing, tests) are synchronous, single-process CLI/doc tooling. The doc-impact index build already handles concurrent invocation via full rebuild semantics, unchanged here.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2059] Knowledge-graph regeneration pipeline (refreshing `site/assets/graph.js` and the `data-meta="commit"` reference) — machine-analysis tooling, its own appetite.
- Nothing else deferred — gitignore removal, plan relocation, discovery wiring, feature doc, and tests are all in scope.

## Update System

No update system changes required. `site/` propagates to other machines via ordinary `git pull` (~1 MB, one-time). `wrangler` and the Cloudflare credentials deliberately stay machine-local — other machines' cascades hit the "redeploy needed" report path, which is correct behavior. No new dependencies enter `pyproject.toml`, no Popoto models change (no migration), and `scripts/update/run.py` needs no edits.

## Agent Integration

No agent integration required — `/do-docs` is a skill the agent already invokes, and `wrangler` is called via the Bash tool. No MCP server, `.mcp.json`, or `pyproject.toml [project.scripts]` changes. The one wiring change (skill-context declarations) is exactly the seam the skill already reads at runtime.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/valorengels-site.md` — what the site is, page inventory, redeploy path (`wrangler deploy` + vault `.env` credentials), do-docs integration contract, graph.js snapshot caveat (→ #2059)
- [ ] Add entry to `docs/features/README.md` index table

### External Documentation Site
- [ ] The site itself needs no content changes in this plan; verify post-merge `wrangler deploy` from main serves identical content

### Inline Documentation
- [ ] Docstring on the HTML preprocessing helper explaining the heading-mapping contract (`<h2>` → `## `)
- [ ] Update the relocated `docs/plans/completed/valorengels-site-hosting.md` redeploy section: deploys now run from the main checkout, not the branch worktree

## Success Criteria

- [ ] `site/`, `wrangler.jsonc`, `src/index.js` are tracked on `main`; both `/site` gitignore rules removed; `git add site/...` works without `-f`
- [ ] `plans/valorengels-site-hosting.md` relocated to `docs/plans/completed/valorengels-site-hosting.md` (no repo-root `plans/` directory remains)
- [ ] `.claude/skill-context/do-docs.md` declares site pages in the inventory table, the stale-sweep paths, sitemap maintenance, and the guarded deploy rule
- [ ] `tools/doc_impact_finder.py` discovers and sensibly chunks `site/*.html`; `site/assets/graph.js` is never indexed
- [ ] `docs/features/valorengels-site.md` exists and is indexed in `docs/features/README.md`
- [ ] Post-merge `wrangler deploy` from the main checkout succeeds and https://valorengels.com still serves correctly (machine-gated criterion)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (site-integration)**
  - Name: site-integration-builder
  - Role: merge, gitignore, plan relocation, skill-context declarations, DOC_PATTERNS + HTML preprocessing, tests
  - Agent Type: builder
  - Resume: true

- **Validator (site-integration)**
  - Name: site-integration-validator
  - Role: verify all Verification rows and success criteria
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: site-docs-writer
  - Role: feature doc + index entry + relocated-plan touch-up
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Merge the site branch and clean up its landing
- **Task ID**: build-merge-site
- **Depends On**: none
- **Validates**: `git ls-files site/` non-empty on the feature branch; `grep '^/site$' .gitignore` finds nothing
- **Assigned To**: site-integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Merge `origin/docs/valor-site` into the feature branch (additive-only; verified no conflicts)
- Remove both `/site` rules (`.gitignore:121`, `.gitignore:322`) and their now-orphaned "# mkdocs documentation" comment lines
- `git mv plans/valorengels-site-hosting.md docs/plans/completed/valorengels-site-hosting.md`; remove the empty `plans/` dir; update its Redeploy section to reference the main checkout instead of the branch worktree

### 2. Declare site pages in the do-docs skill-context
- **Task ID**: build-skill-context
- **Depends On**: build-merge-site
- **Validates**: `grep 'site/' .claude/skill-context/do-docs.md` hits inventory + sweep + deploy sections
- **Assigned To**: site-integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `site/*.html` row to the "Doc inventory locations" table (purpose: published docs site pages at valorengels.com; sorted after `docs/features/*.md`)
- Add `site/` to the Step 2b stale-reference sweep path list
- Add a Step 3 note: when adding/removing site pages, update `site/sitemap.xml`
- Add the guarded deploy rule (Step 4, after commit): if the cascade touched `site/` and `command -v wrangler` succeeds, run `wrangler deploy` (non-fatal on error, outcome always reported); otherwise report "site/ changed — redeploy needed"

### 3. Teach the semantic doc-impact finder HTML
- **Task ID**: build-impact-finder
- **Depends On**: build-merge-site
- **Validates**: tests/unit/test_doc_impact_finder.py (new HTML cases)
- **Assigned To**: site-integration-builder
- **Agent Type**: builder
- **Parallel**: true (independent of build-skill-context)
- Add `site/*.html` to `DOC_PATTERNS` in `tools/doc_impact_finder.py`
- Add an HTML→text preprocessor (stdlib `html.parser`): strip tags/scripts/styles, emit `<h2>`/`<h3>` text as `## `/`### ` lines, then feed the existing `chunk_markdown`
- Wire the preprocessor into the index path keyed on `.html` suffix
- Write the new unit tests listed under Test Impact

### 4. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-skill-context, build-impact-finder
- **Assigned To**: site-integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows; report pass/fail per row

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: site-docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/valorengels-site.md` per the Documentation section
- Add the `docs/features/README.md` index entry

### 6. Final validation and deploy proof
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: site-integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria
- After PR merge (or on the branch, content being identical): `wrangler deploy` from the main checkout; `curl -s -o /dev/null -w '%{http_code}' https://valorengels.com` → 200

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Gitignore rules dropped | `grep -c '^/site$' .gitignore` | match count == 0 |
| Site tracked | `git ls-files site/ \| wc -l` | output > 15 |
| Root plans/ dir gone | `test -d plans && echo exists \|\| echo gone` | output contains gone |
| Hosting plan relocated | `test -f docs/plans/completed/valorengels-site-hosting.md` | exit code 0 |
| DOC_PATTERNS extended | `grep 'site/\*.html' tools/doc_impact_finder.py` | exit code 0 |
| Skill-context declares site | `grep -c 'site/' .claude/skill-context/do-docs.md` | output > 2 |
| graph.js never indexed | `python -c "from pathlib import Path; from tools.doc_impact_finder import _discover_doc_files; print(sum('graph.js' in str(p) for p in _discover_doc_files(Path('.'))))"` | output contains 0 |
| Feature doc exists | `test -f docs/features/valorengels-site.md` | exit code 0 |
| Feature indexed | `grep valorengels-site docs/features/README.md` | exit code 0 |
| Impact-finder tests | `pytest tests/unit/test_doc_impact_finder.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/doc_impact_finder.py tests/unit/test_doc_impact_finder.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/doc_impact_finder.py tests/unit/test_doc_impact_finder.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Deploy-on-cascade default**: the plan makes the do-docs cascade auto-run `wrangler deploy` when it touched `site/` and the machine has wrangler + the vault token (non-fatal, always reported). Alternative: never auto-deploy, always just report. Auto-deploy is assumed since a stale live site is the failure mode this plan exists to prevent — flag if you'd rather keep deploys manual.
2. **`docs/valor-site` branch retirement**: after merge, the branch and its worktree (`.worktrees/valor-site`) serve no purpose. Assumed: delete both post-merge. Flag if you want the branch kept as a historical marker.
