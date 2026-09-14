---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2058
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-07-13T08:02:02Z
---

# Bring the valorengels.com docs site into main as living documentation

## Problem

The static docs site serving https://valorengels.com lives only on branch `docs/valor-site` (`site/` — 22 files, force-added past a stale `.gitignore` rule — plus `wrangler.jsonc` and `src/index.js`). It went live on 2026-07-13 and is already drifting toward staleness by construction: every documentation tool in this repo — the `/do-docs` cascade, the semantic doc-impact finder, the stale-reference sweep — only sees markdown files on `main`. When code changes rename a concept, retire a pattern, or restructure a subsystem, the cascade updates `docs/features/*.md` and `CLAUDE.md` but has no idea `site/runtime.html` describes the same subsystem to the public.

**Current behavior:**
Site pages are a frozen snapshot on a side branch. A `/do-docs` cascade after any code change cannot discover, sweep, or update them. Nothing prompts a redeploy when site content changes.

**Desired outcome:**
`site/` lives on `main` as a first-class documentation location. `/do-docs` inventories site pages, sweeps them for retired terms, edits them alongside markdown docs, and knows a commit touching `site/` implies a `wrangler deploy`. The gitignore hack is gone.

## Freshness Check

**Baseline commit:** `e6f053c4` (main, re-verified at critique/revision time 2026-07-13T08:02Z; original recon baseline `2e550ba2` went stale — main advanced 76 commits past the site branch between recon and critique)
**Issue filed at:** 2026-07-13T07:49:18Z (#2058, filed minutes before this plan — recon was performed live during planning)
**Disposition:** Minor drift (merge-cleanliness claim held, but its stated evidence was wrong and has been restated; see Critique Results BLOCKER 1)

**File:line references re-verified (at critique time):**
- `.gitignore:121` and `.gitignore:322` — duplicate boilerplate `/site` rules under "# mkdocs documentation" comments — confirmed present
- `tools/doc_impact_finder.py:58` — `DOC_PATTERNS` lists only `docs/**/*.md`, `CLAUDE.md`, skills/commands markdown, and persona/identity files — confirmed
- `tools/impact_finder_core.py:97` — `chunk_markdown` splits on `## ` heading lines; HTML content would land in a single unstructured chunk — confirmed; `build_index()` (line ~294) applies ONE `chunk_file` callable uniformly — there is no per-suffix dispatch, so Task 3 authors a small dispatching wrapper
- `.claude/skill-context/do-docs.md` — "Doc inventory locations" table and "Stale-reference sweep paths" have no `site/` entry — confirmed
- `reflections/docs_auditor.py` — link/symbol detectors are markdown-regex based, **but** in `pr-changed-files` mode `audit()` resolves changed files with no `.md` filter and `_detect_stale_term_fixes` does bare-term apply-mode rewrites — HTML **is** reachable; Task 2b adds a guard (Critique Results BLOCKER 2)
- **Merge cleanliness (restated method):** the earlier "zero modifications to files on main" two-dot-diff reasoning was invalid once main advanced (raw `origin/main..docs/valor-site` now shows 275 files / 21,708 deletions — main's advance appearing as deletions). The correct evidence is an actual 3-way test merge: `git merge --no-commit --no-ff origin/docs/valor-site` into main `906bc9dd` exits 0 with no conflicts, diff scoped to exactly 22 files under `{site/, src/index.js, wrangler.jsonc, plans/}` (merge-base `c52be651`). Main may drift further before build, so Task 1 re-runs this test merge as a build-time guard and aborts if the scope grows.

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
4. **Step 2b (stale sweep)**: `rg <retired-term> --glob 'site/*.html'` — HTML pages only; `site/assets/` (incl. the 38k-line `graph.js`) is excluded from the sweep
5. **Step 3 (edits)**: surgical edits to affected `site/*.html` sections like any other doc; `site/sitemap.xml` updated if pages are added/removed
6. **Output**: commit includes site edits on the feature branch like any other doc change
7. **Deploy (at merge)**: `docs/sdlc/do-merge.md` declares a post-merge step — if the merged diff touched `site/`, `wrangler.jsonc`, or `src/index.js`, run `scripts/deploy-site.sh` (wrangler deploy + liveness curl; exits 0 with a "redeploy needed" notice on machines without wrangler/token)

## Architectural Impact

- **New dependencies**: none — HTML preprocessing uses stdlib `html.parser`
- **Interface changes**: `DOC_PATTERNS` gains one glob; `index_docs()` passes a small dispatching wrapper chunk function to `_core_build_index` (a single `if path.endswith('.html')` branch — `build_index()` itself has no per-suffix dispatch and is not modified); `reflections/docs_auditor.py` apply-mode detectors gain a `.md`-only guard; new `scripts/deploy-site.sh` (plain bash, no pyproject entry point)
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

- **Merge**: land `origin/docs/valor-site` on the feature branch behind a merge-scope guard, drop both `/site` gitignore rules, relocate the completed hosting plan to `docs/plans/completed/`
- **Auto-fix safety**: `.md`-only guard on `reflections/docs_auditor.py` apply-mode detectors so the Step 2d substrate can never silently rewrite committed HTML (Critique BLOCKER 2)
- **Deploy**: `scripts/deploy-site.sh` — the simplest possible deploy: `wrangler deploy` + liveness curl, graceful exit-0 notice when wrangler/token absent; invoked by `do-merge` (declared in `docs/sdlc/do-merge.md`) when the merged diff touched site files, and runnable by hand anytime
- **Discovery (inventory + sweep)**: declare `site/*.html` in `.claude/skill-context/do-docs.md` — inventory table row, HTML-scoped stale-sweep glob, sitemap-maintenance note, and a one-line pointer to the deploy script
- **Discovery (semantic)**: add `site/*.html` to `DOC_PATTERNS` with an HTML→heading-delimited-text preprocessor so `chunk_markdown` produces per-section chunks
- **Feature doc**: `docs/features/valorengels-site.md` (short — ~30 lines) as the canonical reference for the site — page inventory, redeploy + rollback path, living-docs integration, graph.js snapshot caveat

### Flow

Code change merges → `/do-docs` cascade → site pages inventoried/swept/matched like markdown docs → surgical HTML edits committed → PR merges → `do-merge` runs `scripts/deploy-site.sh` → https://valorengels.com stays current

### Technical Approach

- Merge cleanliness is proven by a 3-way test merge, not diff inspection (see Freshness Check), and re-proven at build time: Task 1 runs `git merge --no-commit --no-ff origin/docs/valor-site` and asserts the resulting diff is scoped to `{site/, src/index.js, wrangler.jsonc, plans/}` — anything else aborts the task for re-planning
- HTML preprocessing: strip tags with stdlib `html.parser`, emitting `<h2>`/`<h3>` text as `## `/`### ` lines so the existing `chunk_markdown` and index/rerank pipeline work unchanged. Exact seam (there is no per-suffix dispatch in `build_index()`): `index_docs()` passes a wrapper `chunk_doc(content, path)` to `_core_build_index` — `if path.endswith('.html'): return chunk_markdown(preprocess_html(content), path); return chunk_markdown(content, path)`. Single `if` branch, no plugin/registry abstraction. `site/assets/` never matches the `site/*.html` glob, so `graph.js` (38k lines of data) never reaches the embedder; the Step 2b stale sweep is likewise scoped to `site/*.html`
- docs_auditor guard: in `reflections/docs_auditor.py`, the apply-mode detectors (`_detect_stale_term_fixes` and friends) skip non-`.md` files in `pr-changed-files` mode — committed `site/*.html` must never be auto-rewritten by the Step 2d substrate
- The global `do-docs` SKILL.md body is **not** edited — all repo-specific behavior lands in `.claude/skill-context/do-docs.md`, per the skill-context convention (`docs/features/skill-context-convention.md`)
- Deploy (approved: auto-deploy, simplest possible form): one script, `scripts/deploy-site.sh` —
  ```bash
  #!/usr/bin/env bash
  # Deploy site/ to https://valorengels.com. Safe to run anywhere:
  # exits 0 with a notice on machines without wrangler or the vault token.
  set -euo pipefail
  cd "$(dirname "$0")/.."
  command -v wrangler >/dev/null 2>&1 || { echo "deploy-site: wrangler not installed — run from a machine with the vault token"; exit 0; }
  wrangler deploy
  curl -sf https://valorengels.com/ >/dev/null && echo "deploy-site: live OK" || { echo "deploy-site: liveness check FAILED — consider wrangler rollback"; exit 1; }
  ```
  Wired in exactly one place: `docs/sdlc/do-merge.md` declares a post-merge step — if the merged diff touched `site/`, `wrangler.jsonc`, or `src/index.js`, run `scripts/deploy-site.sh` (non-fatal to the merge; report the outcome). The do-docs skill-context carries only a one-line pointer to the script — no deploy logic in the cascade
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

No existing tests affected — `tests/unit/test_doc_impact_finder.py` builds its indexes from tmp-path fixture repos driven by the `DOC_PATTERNS` globs; an added `site/*.html` pattern matches nothing in existing fixtures, and no test asserts the contents of the pattern list itself. The docs_auditor `.md` guard only *narrows* apply-mode file eligibility, and existing docs_auditor tests exercise markdown fixtures, which remain eligible. Changes are purely additive.

New tests (created, not modified):
- [ ] `tests/unit/test_doc_impact_finder.py::TestHtmlDocs` — UPDATE (additive): HTML file discovered via `site/*.html` fixture; heading-delimited chunking; no-headings single chunk; empty-file no crash; `site/assets/graph.js` NOT discovered
- [ ] `tests/unit/test_docs_auditor_substrate.py` — UPDATE (additive): a `site/*.html` fixture containing a stale term inside `class="…"` must be left byte-identical by `audit(scope_mode='pr-changed-files', apply_mode='apply')`

## Rabbit Holes

- **Regenerating the knowledge graph** — `graph.js` has no in-repo generator; rebuilding the machine-analysis pipeline is its own project. Filed as #2059; this plan only integrates the hand-written page copy.
- **Making `reflections/docs_auditor.py` HTML-aware** — teaching the substrate to *fix* HTML is a rewrite for marginal auto-fix value; site pages get manual edits in cascade Step 3. (Note the inverse is NOT a rabbit hole and IS in scope: a `.md`-only guard so apply mode can't accidentally rewrite HTML — Task 2b. The critique found `pr-changed-files` mode resolves changed files with no `.md` filter, so "HTML out of scope by construction" was false.)
- **Converting the site to a static-site generator** — the site is deliberately no-build, self-contained HTML. Keep it that way.
- **CI auto-deploy** — the predecessor plan already scoped this to "low priority"; the guarded deploy rule in the skill-context covers the practical need.

## Risks

### Risk 1: Graph-derived site content stays stale even after this plan
**Impact:** Stats and per-file summaries on the live site describe commit `c06b9fa6` regardless of cascades — visitors may see outdated numbers.
**Mitigation:** #2059 tracks the regeneration pipeline; the feature doc explicitly labels graph content as a snapshot with a pointer to #2059, so the caveat is documented rather than silent.

### Risk 2: Cascades edit site pages but nobody redeploys
**Impact:** The repo and the live site diverge — worse than before, because the repo now *looks* authoritative.
**Mitigation:** `do-merge` runs `scripts/deploy-site.sh` whenever the merged diff touched site files — deploys happen from merged `main`, the only state worth publishing; the script is also a one-command manual fallback.

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
- [ ] Create `docs/features/valorengels-site.md` — keep it short (~30 lines, per critique NIT): page inventory, redeploy path (`wrangler deploy` + vault `.env` credentials), rollback path (`wrangler rollback`), do-docs integration contract, graph.js snapshot caveat (→ #2059)
- [ ] Add entry to `docs/features/README.md` index table

### External Documentation Site
- [ ] The site itself needs no content changes in this plan; verify post-merge `wrangler deploy` from main serves identical content

### Inline Documentation
- [ ] Docstring on the HTML preprocessing helper explaining the heading-mapping contract (`<h2>` → `## `)
- [ ] Update the relocated `docs/plans/completed/valorengels-site-hosting.md` redeploy section: deploys now run via `scripts/deploy-site.sh` from the main checkout, not the branch worktree

## Success Criteria

- [ ] `site/`, `wrangler.jsonc`, `src/index.js` are tracked on `main`; both `/site` gitignore rules removed; `git add site/...` works without `-f`
- [ ] `plans/valorengels-site-hosting.md` relocated to `docs/plans/completed/valorengels-site-hosting.md` (no repo-root `plans/` directory remains)
- [ ] `.claude/skill-context/do-docs.md` declares site pages in the inventory table, the HTML-scoped stale-sweep glob (`site/*.html`, never bare `site/`), sitemap maintenance, and the deploy-script pointer
- [ ] `scripts/deploy-site.sh` exists, is executable, and `docs/sdlc/do-merge.md` declares the post-merge invocation
- [ ] `tools/doc_impact_finder.py` discovers and sensibly chunks `site/*.html`; `site/assets/graph.js` is never indexed nor swept
- [ ] `reflections/docs_auditor.py` apply mode skips non-`.md` files; regression test proves committed HTML stays byte-identical
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
- **Merge-scope guard first**: `git merge --no-commit --no-ff origin/docs/valor-site`; assert exit 0 AND `git diff --name-only --cached` is a subset of `{site/**, src/index.js, wrangler.jsonc, plans/**}`. Any file outside that set → `git merge --abort` and stop for re-planning (main may have drifted since the critique's test merge at `906bc9dd`)
- Commit the merge once the guard passes
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
- Add the Step 2b stale-reference sweep entry scoped to HTML pages only — `rg <term> --glob 'site/*.html'` (never bare `site/`, which would grep the 38k-line `site/assets/graph.js` every cascade)
- Add a Step 3 note: when adding/removing site pages, update `site/sitemap.xml`
- Add a one-line Step 4 note: site changes deploy at merge via `scripts/deploy-site.sh` (declared in `docs/sdlc/do-merge.md`); if the cascade committed `site/` changes directly on `main`, run the script immediately and report its output
- Create `scripts/deploy-site.sh` exactly as specified in Technical Approach (`chmod +x`)
- Add the post-merge step declaration to `docs/sdlc/do-merge.md`: if the merged diff touched `site/`, `wrangler.jsonc`, or `src/index.js`, run `scripts/deploy-site.sh` and report the outcome (non-fatal to the merge)

### 2b. Guard docs_auditor apply mode against non-markdown files
- **Task ID**: build-auditor-guard
- **Depends On**: build-merge-site
- **Validates**: docs_auditor regression test (new; see Test Impact)
- **Assigned To**: site-integration-builder
- **Agent Type**: builder
- **Parallel**: true (independent of build-skill-context)
- In `reflections/docs_auditor.py`, skip non-`.md` files before the apply-mode detectors run in `pr-changed-files` mode (`_detect_stale_term_fixes` and any other write-back path) — the substrate must never rewrite committed `site/*.html`
- Add the regression test: HTML fixture with a stale term inside an attribute stays byte-identical under `audit(scope_mode='pr-changed-files', apply_mode='apply')`

### 3. Teach the semantic doc-impact finder HTML
- **Task ID**: build-impact-finder
- **Depends On**: build-merge-site
- **Validates**: tests/unit/test_doc_impact_finder.py (new HTML cases)
- **Assigned To**: site-integration-builder
- **Agent Type**: builder
- **Parallel**: true (independent of build-skill-context)
- Add `site/*.html` to `DOC_PATTERNS` in `tools/doc_impact_finder.py`
- Add an HTML→text preprocessor (stdlib `html.parser`): strip tags/scripts/styles, emit `<h2>`/`<h3>` text as `## `/`### ` lines, then feed the existing `chunk_markdown`
- Wire it at the exact seam: `index_docs()` passes a wrapper `chunk_doc(content, path)` to `_core_build_index` with a single `if path.endswith('.html')` branch (no per-suffix dispatch exists in `build_index()` and none is added; no plugin/registry abstraction). `find_affected()`'s `chunk_file` param is vestigial for docs — chunking happens only at index-build time
- Write the new unit tests listed under Test Impact

### 4. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-skill-context, build-auditor-guard, build-impact-finder
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
- After PR merge: run `scripts/deploy-site.sh` from the main checkout — it must print "deploy-site: live OK"
- Post-merge cleanup (approved): delete the `docs/valor-site` branch (local + origin) and remove the `.worktrees/valor-site` worktree

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Gitignore rules dropped | `grep -c '^/site$' .gitignore` | match count == 0 |
| Site tracked | `git ls-files site/ \| wc -l` | output > 15 |
| Root plans/ dir gone | `test -d plans && echo exists \|\| echo gone` | output contains gone |
| Hosting plan relocated | `test -f docs/plans/completed/valorengels-site-hosting.md` | exit code 0 |
| DOC_PATTERNS extended | `grep 'site/\*.html' tools/doc_impact_finder.py` | exit code 0 |
| Skill-context declares site | `grep -c 'site/' .claude/skill-context/do-docs.md` | output > 2 |
| Sweep is HTML-scoped (anti-criterion) | `grep -n "rg .*site/" .claude/skill-context/do-docs.md \| grep -vc "site/\*.html"` | match count == 0 |
| Auditor guard regression | `pytest tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| graph.js never indexed | `python -c "from pathlib import Path; from tools.doc_impact_finder import _discover_doc_files; print(sum('graph.js' in str(p) for p in _discover_doc_files(Path('.'))))"` | output contains 0 |
| Feature doc exists | `test -f docs/features/valorengels-site.md` | exit code 0 |
| Feature indexed | `grep valorengels-site docs/features/README.md` | exit code 0 |
| Impact-finder tests | `pytest tests/unit/test_doc_impact_finder.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/doc_impact_finder.py tests/unit/test_doc_impact_finder.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/doc_impact_finder.py tests/unit/test_doc_impact_finder.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room), FULL depth, 3/3 roster complete. Verdict: NEEDS REVISION. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + History & Consistency (both) | **Freshness Check is factually wrong.** Plan asserts "5 commits, 22 files, 38,780 insertions, ZERO modifications to files that exist on main — merge trivially clean" off baseline `2e550ba2`. Reality: current `origin/main` is `906bc9dd` (baseline is stale); merge-base is `c52be651`; main is 76 commits ahead of the branch. Raw `git diff origin/main..docs/valor-site` shows 275 files / 21,708 deletions (main's advance appears as deletions). The merge IS clean — but via git 3-way semantics, NOT the plan's stated reason. A plan whose core risk-mitigation claim is false-as-written passes review on wrong grounds. | ✅ Revision 2026-07-13: Freshness Check restated (3-way test-merge method, Minor drift); Task 1 gained the build-time merge-scope guard | Verified by actual test merge: `git merge --no-commit --no-ff origin/docs/valor-site` into current main → exit 0, no conflicts, diff scoped to exactly 22 files under `{site/, src/index.js, wrangler.jsonc, plans/}`. Revision: restate method as "3-way merge test, not two-dot diff"; add build-time guard in Task 1 — run the no-commit merge in a scratch worktree, assert exit 0 AND `git diff --name-only HEAD` ⊆ `{site/, src/index.js, wrangler.jsonc, plans/}`, else abort/re-plan (main may drift further before build). |
| BLOCKER | Risk & Robustness + History & Consistency (both) | **docs_auditor auto-fix can mutate committed HTML.** Rabbit Hole claims `reflections/docs_auditor.py` is "markdown-link-regex based end to end; HTML out of scope by construction." False: in `pr-changed-files` mode (the mode the do-docs skill-context invokes at Step 2d, BEFORE manual edits) `audit()` resolves `_resolve_pr_changed_files(root)` with no `.md` filter, then runs `_detect_stale_term_fixes` (a bare-term rename, NOT link-scoped) with `apply_mode='apply'` and writes back. Any `site/*.html` already committed in the same PR is eligible for auto-rewrite inside tags/attrs/inline `<script>` — silently shipping to the public site. | ✅ Revision 2026-07-13: new Task 2b adds the `.md` guard in `reflections/docs_auditor.py` + regression test in `tests/unit/test_docs_auditor_substrate.py`; Rabbit Hole wording corrected | In `reflections/docs_auditor.py` `audit()` pr-changed-files loop, add `if not str(path).endswith('.md'): continue` before the stale-term/link/symbol detectors apply-write (or exclude `site/*.html` from `apply_mode='apply'`). Regression test: a `site/*.html` fixture with a stale term inside `class="…"` must be left untouched by `audit(scope_mode='pr-changed-files', apply_mode='apply')`. This is a NEW code change the plan currently omits (plan scopes docs_auditor as a Rabbit Hole "no change"). |
| CONCERN | Risk & Robustness + Scope & Value (both) | **Auto-`wrangler deploy` inside the cascade: blast radius + no partial-success detection.** The guarded deploy fires implicitly whenever `site/*.html` is in the changed-files diff, reading `CLOUDFLARE_API_TOKEN` from shared vault `.env` on any machine with wrangler on PATH. It checks only the wrangler exit code — not post-deploy liveness — so a bad HTML edit (incl. one injected by the docs_auditor BLOCKER above) ships to production docs with no verification and no documented rollback. Conflates "make docs discoverable" with "run a CD pipeline for a public site." | ✅ Revision 2026-07-13: adopted option (b) — deploy fires only on the cascade's own site commits, post-deploy `curl -sf` liveness check, deployment id logged, `wrangler rollback` documented in feature doc + skill-context. Opt-in flag (option a) not adopted. **Final (user-approved 2026-07-13): auto-deploy, simplest form — a standalone `scripts/deploy-site.sh` run by `do-merge` post-merge (deploys only merged `main`, closing the unattended-branch-deploy concern), doubling as the manual fallback** | Two options embedded per revision: (a) require an explicit opt-in (`--deploy-site`) rather than firing on any `site/` diff, so an unrelated PR touching site/ can't push to prod unattended; and/or (b) after `wrangler deploy` exits 0, run `curl -sf https://valorengels.com/ >/dev/null` and only report success if both pass; log the wrangler deployment id and document `wrangler rollback <id>` in the skill-context rule. |
| CONCERN | History & Consistency | **graph.js "never indexed" contradicts the stale-sweep `rg site/`.** Technical Approach says `site/assets/graph.js` (38k lines) "never reaches the embedder" (glob is `site/*.html`) — true for embedding. But Task 2 / Data Flow step 4 add `site/` to the do-docs stale-reference `rg` path list (skill-context line 100 greps whole dirs), so `rg <retired-term> site/` scans graph.js every cascade run. "Never touched" (embedder) vs "swept" (rg) contradict for the same file. | ✅ Revision 2026-07-13: sweep scoped to `--glob 'site/*.html'` throughout (Data Flow, Task 2, Success Criteria); anti-criterion Verification row added | In the skill-context Step 2b sweep, scope the site path to `site/*.html` or add `--glob '!site/assets/**'` so the 38k-line generated file isn't grepped/flagged every run; update the "never touched" wording to "excluded from embedding; HTML-only in the sweep." |
| CONCERN | Scope & Value | **Embedder/preprocessor wiring: "keyed on .html suffix" implies infra that doesn't exist, and proportionality is thin.** `impact_finder_core.build_index()` applies ONE `chunk_file` callable uniformly to every discovered file (line 294) — there is NO per-suffix dispatch to hook into. The builder must author a new dispatching wrapper chunk fn. For 22 pages whose only known drift source (graph.js) is deferred to #2059, this is real code with no demonstrated retrieval failure motivating it now. | ✅ Revision 2026-07-13: seam named exactly (wrapper `chunk_doc` passed to `_core_build_index` in `index_docs()`, single `if`, no registry) in Technical Approach + Task 3. Kept in scope rather than deferred: skill-context declaration alone leaves Agent C blind to site pages | Name the seam precisely in Task 3: pass a wrapper `chunk_doc(content, path)` to `_core_build_index` inside `index_docs()` — `if path.endswith('.html'): return chunk_markdown(preprocess_html(content), path); return chunk_markdown(content, path)`. Cap it: single `if` branch, no plugin/registry abstraction; preprocessor is `html.parser`-only heading extraction, no markdown-conversion lib. (Optionally defer the whole embedder wiring to #2059 alongside graph.js regen and land only the skill-context declaration now.) `find_affected()`'s `chunk_file` param is vestigial for docs — chunking happens only at build_index time. |
| NIT | Scope & Value | **Feature doc may be over-specified** for a 1-day-old, largely-frozen static site. A full `docs/features/valorengels-site.md` treatment vs. a short inventory + #2059 caveat note. | ✅ Revision 2026-07-13: feature doc capped at ~30 lines in Documentation section | Keep the doc under ~30 lines: inventory + redeploy path + graph.js snapshot pointer to #2059. Not worth blocking on. |

