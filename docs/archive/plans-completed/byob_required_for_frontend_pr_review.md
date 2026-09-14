---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-05-13
tracking: https://github.com/tomcounsell/ai/issues/1380
last_comment_id:
---

# BYOB Visual Proof Required for Frontend PR Reviews

## Problem

A recent PR on a sister repo shipped without browser testing and merged with serious visual bugs. The `/do-pr-review` skill already has a Step 3 "Screenshot Capture (if UI changes detected)" sub-skill (`.claude/skills-global/do-pr-review/sub-skills/screenshot.md`) that uses BYOB MCP, but it is treated as best-effort, not as a gate. Reviews can — and have — reached `APPROVED` on diffs that touched frontend code without any captured screenshots or browser-session evidence.

**Current behavior:**
- `sub-skills/code-review.md` Step 3 says "If screenshots needed: ..." — purely advisory
- Nothing in the verdict logic, the 10-item Rubric, the Pre-Verdict Checklist, or `record_verdict` checks whether screenshots were actually produced
- A reviewer can detect frontend file changes, skip Step 3 entirely (BYOB session not running, app didn't start, "looks fine from the diff"), and still emit `verdict: APPROVED` and a `success` OUTCOME marker
- No mechanical interlock between "this PR touches the rendered surface" and "a human-equivalent eye saw the rendered surface"

**Desired outcome:**
When a PR's diff includes frontend/web files, the reviewer cannot post `APPROVED` or `success` OUTCOME without visual proof captured in this review run. Visual proof = at least one screenshot under `generated_images/pr-$PR_NUMBER/` produced via BYOB during this session, OR an explicit `BLOCKED_ON_VISUAL_PROOF` verdict if the BYOB surface is unavailable (so the gap is loud, not silent — mirroring the existing `BLOCKED_ON_CONFLICT` short-circuit precedent).

## Freshness Check

**Baseline commit:** d49c29b15533d1c8507c898fac0620b1f7d510a9
**Issue filed at:** 2026-05-13T15:50:19Z (same day as plan, ~0 hours old)
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/skills-global/do-pr-review/SKILL.md` — exists, current structure matches
- `.claude/skills-global/do-pr-review/sub-skills/{checkout,code-review,post-review,screenshot}.md` — all four exist
- `sub-skills/post-review.md` lines 275, 337, 357 — `BLOCKED_ON_CONFLICT` short-circuit precedent confirmed
- `scripts/pr_shape_classify.py` — exists, is the natural home for a shared `is_frontend_pr()` helper
- `docs/features/byob-browser-control.md` — exists
- `docs/features/do-pr-review-bot-identity.md` — exists (the file pattern for do-pr-review feature docs)
- `tools/sdlc_verdict.py:362` — `--verdict` is free-form string; new verdict is docs-only enum

**Cited sibling issues/PRs re-checked:**
- #1256 — CLOSED 2026-05-06 (BYOB migration complete; BYOB is now standard infra)
- #1300 — open; orthogonal (bot identity)
- #1045 — open; the rubric/disclosure-parser recalibration this plan extends

**Commits on main since issue was filed (touching referenced files):** none (issue filed ~hours before planning).

**Active plans in `docs/plans/` overlapping this area:** `browser-screenshot-dimension-fix.md` exists but addresses a different concern (image dimensions). No overlap with the gate logic this plan introduces.

**Notes:** Issue is fresh and all premises hold. The memory `feedback_byob_is_standard.md` confirms BYOB is standard infrastructure on every machine — the "BYOB unavailable" path should be a rare-but-loud failure mode, not the common case.

## Prior Art

- **Issue #1256**: Replace agent-browser with BYOB real-Chrome control — CLOSED 2026-05-06. Established BYOB as the canonical browser surface for SDLC skills. The screenshot sub-skill's anonymous-headless fallback was retired in this work, so BYOB-or-blocked is already the architectural posture.
- **Issue #1274**: Migrate skills off agent-browser to BYOB (follow-up to #1256) — confirms BYOB is the only supported browser surface.
- **Issue #1045**: Recalibrate /do-pr-review — established the Rubric, disclosure parser, and Pre-Verdict Checklist that this plan extends with one new checklist item.
- **Issue #1300**: do-pr-review bot identity — orthogonal but informs how `gh pr comment` vs. `gh pr review` is used for the BLOCKED short-circuit (same posting pattern this plan reuses for `BLOCKED_ON_VISUAL_PROOF`).

## Research

No external research required — this is purely an internal skill/tooling change. BYOB MCP semantics are already documented in `docs/features/byob-browser-control.md` and the screenshot sub-skill. No external libraries, APIs, or ecosystem patterns are introduced.

## Data Flow

End-to-end for a self-authored frontend PR review run:

1. **Entry point**: `/sdlc` dispatches `/do-pr-review` for an open PR after BUILD completed.
2. **`checkout.md`**: clones/checks out PR head; sets `$SDLC_PR_NUMBER`, runs mergeability preflight. Output: `PREFLIGHT_VERDICT` (CLEAN | BLOCKED_ON_CONFLICT | PR_CLOSED).
3. **`code-review.md` Step 1**: gathers `gh pr diff $PR_NUMBER --name-only` — the diff filename list.
4. **NEW: frontend classification**: pass the diff filename list to `scripts.pr_shape_classify.is_frontend_pr(files)` → returns `True | False`. Result stored in `$IS_FRONTEND_PR` for downstream steps.
5. **`screenshot.md`** (existing): if `$IS_FRONTEND_PR`, run BYOB screenshot capture into `generated_images/pr-${PR_NUMBER}/`. Capture review-start timestamp `$REVIEW_START_TS` before invoking BYOB.
6. **NEW: visual proof check**: count files under `generated_images/pr-${PR_NUMBER}/*.png` with `mtime > $REVIEW_START_TS`. Store as `$VISUAL_PROOF_COUNT`.
7. **Pre-Verdict Checklist** (`code-review.md`): existing 12 items + NEW item "Visual proof captured for frontend changes" — passes if `$IS_FRONTEND_PR == False` (N/A) OR `$VISUAL_PROOF_COUNT >= 1`; fails otherwise.
8. **Verdict derivation**: if checklist item fails AND BYOB was reachable → emit a hard error (planner missed something — code-review.md will retry the screenshot path once). If checklist item fails AND BYOB was unreachable → set `VERDICT=BLOCKED_ON_VISUAL_PROOF`.
9. **`post-review.md`**: decision tree adds a new short-circuit row BEFORE the existing approval rows:
   - **Verdict: `BLOCKED_ON_VISUAL_PROOF`** → `gh pr comment` only (never `gh pr review --approve`), with a template explaining what's needed.
10. **`sdlc-tool verdict record`**: receives the verdict string (free-form today; just needs documentation update so the SDLC router doesn't trip on the new value). Emits OUTCOME marker with `verdict: BLOCKED_ON_VISUAL_PROOF` and `next_skill: null` (operator-actionable).

## Architectural Impact

- **New dependencies**: none — BYOB is already loaded for `/do-pr-review` runs that set `requires_real_chrome=True`.
- **Interface changes**: adds one new exported function `is_frontend_pr(files: list[str]) -> bool` in `scripts/pr_shape_classify.py`. Adds one new verdict string `BLOCKED_ON_VISUAL_PROOF` to the documented enum (free-form today, so no code change in `sdlc_verdict.py`).
- **Coupling**: slightly increases coupling between `code-review.md` and `screenshot.md` (verdict logic now depends on screenshot side-effects). Mitigated by checking via filesystem mtime, not by passing state between sub-skills.
- **Data ownership**: `scripts/pr_shape_classify.py` becomes the single source of truth for "is this a frontend PR." Today, the file-glob list lives in `sub-skills/screenshot.md` Step 1 as a comment. After this plan, the glob list lives in one place.
- **Reversibility**: high — the gate is a single checklist item and a single decision-tree row. Reverting is a small revert PR.

## Appetite

**Size:** Small

**Team:** Solo dev, plus existing `/do-plan-critique` war room.

**Interactions:**
- PM check-ins: 1 (resolve open questions before build)
- Review rounds: 1 (PR review will itself exercise the new gate — meta-validation)

This is a skill-prose + small-Python change with no new infrastructure. The work is in carefully wiring the gate into the existing 12-item checklist without breaking the dozen-plus other interlocks.

## Prerequisites

No external prerequisites — BYOB MCP, gh CLI, and `sdlc-tool` are already standard infrastructure.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| BYOB MCP loaded | `mcp__byob__browser_list_tabs` returns non-empty | Verifies the calling session has BYOB available (existing precondition for screenshot sub-skill) |

## Solution

### Key Elements

- **`is_frontend_pr(files)` helper** in `scripts/pr_shape_classify.py`: single shared function returning True if any file in the diff matches the frontend/web glob list.
- **New Pre-Verdict Checklist item** in `sub-skills/code-review.md`: "Visual proof captured for frontend changes" with explicit pass / fail / N-A semantics.
- **New short-circuit branch** in `sub-skills/post-review.md` decision tree: `BLOCKED_ON_VISUAL_PROOF` → comment-only path, mirroring `BLOCKED_ON_CONFLICT`.
- **Verdict taxonomy doc update**: add `BLOCKED_ON_VISUAL_PROOF` to the verdict enum table in `post-review.md` and `docs/features/do-pr-review-bot-identity.md` (the file that already enumerates verdicts).
- **Frontend-change glob list**: lives in `pr_shape_classify.py` as a module-level tuple, importable.
- **Review-start timestamp anchor**: `code-review.md` captures `REVIEW_START_TS` early (before screenshot sub-skill runs) so the visual-proof check uses mtime > start, preventing replays from old screenshots.

### Flow

PR with frontend diff arrives → `/do-pr-review` dispatched → checkout sub-skill checks out PR and runs mergeability preflight → code-review.md Step 1 captures diff filenames AND `REVIEW_START_TS` → `is_frontend_pr(files)` returns True → screenshot.md runs BYOB capture → code-review.md Pre-Verdict Checklist evaluates "Visual proof captured" item → if `mtime > REVIEW_START_TS` files exist under `generated_images/pr-${PR_NUMBER}/*.png` → checklist passes → standard verdict flow → APPROVED allowed.

If BYOB is unreachable (no tabs, MCP not loaded, `requires_real_chrome=False`) → checklist item fails → `VERDICT=BLOCKED_ON_VISUAL_PROOF` → post-review.md decision tree picks the new short-circuit row → `gh pr comment` with template explaining what's missing → OUTCOME marker emitted with `next_skill: null`.

### Technical Approach

- **Add `is_frontend_pr(files)` to `scripts/pr_shape_classify.py`**: a top-level function (not part of the existing shape-classification dataclass) that takes a list of filenames and returns `bool`. Uses `fnmatch.fnmatch` against a module-level `FRONTEND_GLOBS` tuple. Python files are matched against `templates/`, `ui/templates/`, `static/` prefixes (since Python files render HTML in this codebase). The glob list is defined as one module constant so the next skill that needs frontend detection imports it directly.
- **Add a 13th item to the Pre-Verdict Checklist** in `code-review.md`. Each existing item has a clear pass/fail/N-A pattern; the new item follows that pattern. Item text: "Visual proof captured for frontend changes — `is_frontend_pr(diff_files) == False` (N/A) OR `≥1 *.png` file under `generated_images/pr-${PR_NUMBER}/` with mtime > REVIEW_START_TS (pass)."
- **Wire the check into verdict derivation**: at the bottom of `code-review.md`, before computing the verdict string, evaluate the new item. If it fails AND `BYOB_REACHABLE == True`, the agent re-runs screenshot.md once (planner missed the screenshot step on first pass); if it fails AND `BYOB_REACHABLE == False`, the verdict is set to `BLOCKED_ON_VISUAL_PROOF`.
- **`BYOB_REACHABLE` detection**: `mcp__byob__browser_list_tabs` returning at least one tab. This is the same precondition `screenshot.md` already documents — we reuse it as a probe.
- **New row in `post-review.md` decision tree**: inserted between `PR_CLOSED` (row 1) and `BLOCKED_ON_CONFLICT` (row 2). Row "1.5: `BLOCKED_ON_VISUAL_PROOF`" → `gh pr comment` only. Mirrors the existing pattern exactly.
- **Comment template** for `BLOCKED_ON_VISUAL_PROOF`: explains the gate, names the frontend files in the diff that triggered it, and instructs the operator to run the review on a machine with BYOB available (or to start the app and re-run from a BYOB-enabled session).
- **OUTCOME marker addition**: extend the inline `<!-- OUTCOME ... -->` example in `post-review.md` to include `BLOCKED_ON_VISUAL_PROOF` alongside the existing enumerated verdicts. No code change required — the marker is parsed by free-form regex.
- **Multi-judge interaction**: the gate runs **once at the parent (orchestrator) level after fork judges return**, not in each fork. Forks don't have BYOB access; only the orchestrator does. Verdict aggregation: if any fork emits `APPROVED` and the orchestrator's visual-proof check fails, the orchestrator overrides to `BLOCKED_ON_VISUAL_PROOF`. Single check at the parent is cheaper and harder for a fork to game.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks introduced. `is_frontend_pr()` raises `ValueError` on non-list input rather than swallowing.
- [ ] BYOB MCP probe failure (`mcp__byob__browser_list_tabs` raises) is caught and translated to `BYOB_REACHABLE=False`, not silently treated as `True`.

### Empty/Invalid Input Handling
- [ ] `is_frontend_pr([])` → returns `False` (empty diff is not a frontend PR). Test asserts this explicitly.
- [ ] `is_frontend_pr([""])` (empty string filename) → returns `False`, does not raise.
- [ ] Diff with only `.gitignore` / `.github/workflows/*.yml` changes → `is_frontend_pr` returns `False`.

### Error State Rendering
- [ ] `BLOCKED_ON_VISUAL_PROOF` comment template is tested: it must name at least one frontend file from the diff (so the author knows which file triggered the gate).
- [ ] If `gh pr comment` itself fails (network), the skill exits with non-zero and surfaces the error — does NOT fall through to `gh pr review --approve`.

## Test Impact

- [ ] `tests/unit/scripts/test_pr_shape_classify.py` (or equivalent existing test file for `pr_shape_classify.py`) — UPDATE: add unit tests for the new `is_frontend_pr()` function. Cases: pure frontend diff returns True; mixed diff with one frontend file returns True; pure backend diff returns False; empty diff returns False; Python file under `templates/` returns True; Python file under `tools/` returns False.
- [ ] No existing `/do-pr-review` integration tests break — the gate is additive. The only behavioral change is that frontend PRs without screenshots now BLOCK instead of APPROVE, which is the intended outcome.
- [ ] NEW integration test (or recorded fixture): `tests/integration/test_do_pr_review_visual_proof.py` covering three cases:
  - (a) Frontend diff + zero screenshots under `generated_images/pr-N/` → verdict is `BLOCKED_ON_VISUAL_PROOF`, no `gh pr review --approve` call recorded
  - (b) Frontend diff + ≥1 screenshot with `mtime > REVIEW_START_TS` → verdict path proceeds normally (allowed to reach APPROVED)
  - (c) Non-frontend diff (e.g., pure Python/docs) → existing behavior unchanged, no visual-proof gate triggered
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE only if the parity test asserts on the Pre-Verdict Checklist length or contents; otherwise no change.

## Rabbit Holes

- **Building a full visual-regression diffing system**: out of scope. The gate only verifies a screenshot exists; comparing against a baseline image is a separate, much larger project.
- **Extending the gate to PR comments / non-PR review sessions**: out of scope. The gate triggers only inside `/do-pr-review` runs on open PRs.
- **Auto-restarting the dev server when BYOB connects to a dead localhost**: out of scope. If the app isn't running, `prepare-app` is the right surface — the visual-proof gate just reports BLOCKED.
- **Adding visual proof for backend-only PRs that happen to render HTML in tests**: out of scope. `is_frontend_pr()` keys off real frontend file types, not test-render side effects.
- **Refactoring the entire 12-item Pre-Verdict Checklist**: out of scope. We add one item; we don't touch the others.

## Risks

### Risk 1: False positives — backend PRs flagged as frontend
**Impact:** A PR with only a tiny CSS change in unrelated tooling (e.g., `docs/_static/custom.css`) gets blocked unnecessarily, slowing reviews.
**Mitigation:** The glob list is conservative. `docs/**` is explicitly excluded (docs CSS doesn't render in the product). The integration test set includes a "tooling CSS" case to lock the boundary.

### Risk 2: False negatives — frontend PRs that don't match globs
**Impact:** A PR that changes a Python file that renders HTML inline (e.g., a FastAPI route returning `HTMLResponse` directly) is NOT flagged, and merges without visual proof.
**Mitigation:** The glob list includes `templates/`, `ui/templates/`, `static/` Python files. For inline-HTML Python routes (FastAPI `HTMLResponse`, Starlette `HTMLResponse`, Django `HttpResponse(content_type="text/html")`), `is_frontend_pr()` will also flag any Python file whose diff hunks introduce or modify lines containing `HTMLResponse(`, `render_template(`, `render(`, or `Response(...content_type="text/html"`. This catches inline-HTML routes without requiring a separate Python-AST pass. The integration test set includes one inline-HTML route case to lock this behavior.

### Risk 3: BYOB unreachable becomes the common case
**Impact:** If many review sessions run on machines without BYOB, the new verdict `BLOCKED_ON_VISUAL_PROOF` becomes routine noise rather than the rare-but-loud signal it's designed to be.
**Mitigation:** Per memory `feedback_byob_is_standard.md`, BYOB is standard infrastructure on every machine; the bridge sets `requires_real_chrome=True` for SDLC runs. If this gate fires repeatedly across machines, that's a real signal that BYOB needs reinstalling somewhere — exactly the loud-failure mode we want.

### Risk 4: Gate can be gamed by dropping a stale screenshot into the directory
**Impact:** A reviewer could `cp old_screenshot.png generated_images/pr-N/01_main.png` and the gate would pass.
**Mitigation:** The mtime > REVIEW_START_TS check defeats this — old files have older mtimes than the captured review start. The check uses `os.stat().st_mtime` against a timestamp captured before BYOB runs.

### Risk 5: Multi-judge orchestrator override creates verdict inconsistency
**Impact:** Forks report APPROVED but the orchestrator overrides to BLOCKED_ON_VISUAL_PROOF. The OUTCOME marker emitted at the orchestrator level disagrees with fork-level reasoning logs.
**Mitigation:** Document the override behavior explicitly in `post-review.md`: the visual-proof gate is a parent-level overlay. Fork findings are preserved in the comment body for human reading even when the verdict is overridden.

## Race Conditions

No race conditions identified. The gate is synchronous: `REVIEW_START_TS` is captured before the screenshot sub-skill runs; the mtime check runs after the sub-skill returns. BYOB MCP calls are sequential within a single review run. The orchestrator-level override happens after all forks return — a strict happens-before ordering.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The five "Solution Sketch" open questions from issue #1380 are resolved in the Technical Approach section above (gate location: Pre-Verdict Checklist + post-review decision-tree row; frontend-detection helper: `is_frontend_pr()` in `pr_shape_classify.py`; proof bar: ≥1 png with mtime > REVIEW_START_TS; BYOB unavailable path: `BLOCKED_ON_VISUAL_PROOF` verdict with comment-only short-circuit; verdict taxonomy: documentation update only; multi-judge: parent-level overlay).

## Update System

No update system changes required — this feature is purely internal to the SDLC skill set. The new `is_frontend_pr()` function is part of `scripts/pr_shape_classify.py` which already deploys with the repo. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — `/do-pr-review` is a Claude Code skill, not an agent-callable surface. The agent invokes the skill through the existing SDLC pipeline (PM session dispatches `/do-pr-review` via `valor-session create --role dev`). No MCP server changes, no `.mcp.json` changes, no bridge changes.

The new function `is_frontend_pr()` is imported internally by the skill via its bash steps:
```bash
python -c "from scripts.pr_shape_classify import is_frontend_pr; import json,sys; print(is_frontend_pr(json.loads(sys.argv[1])))" "$DIFF_FILES_JSON"
```

## Documentation

### Feature Documentation
- [ ] Update `docs/features/do-pr-review-bot-identity.md` — extend the verdict-taxonomy table to include `BLOCKED_ON_VISUAL_PROOF`. Add the short-circuit posting rule (use `gh pr comment`, never `gh pr review --approve`).
- [ ] Create `docs/features/do-pr-review-visual-proof-gate.md` — new feature doc describing the gate, the helper function, the decision tree row, and the failure modes.
- [ ] Add the new feature doc to `docs/features/README.md` index table.

### External Documentation Site
- N/A — this repo does not maintain a public documentation site.

### Inline Documentation
- [ ] Docstring on `is_frontend_pr()` explaining the glob list, the returns-bool contract, and the empty-input semantics.
- [ ] Update the existing `docs/features/byob-browser-control.md` only if needed (likely a single cross-reference paragraph pointing to the new feature doc).

## Success Criteria

- [ ] `/do-pr-review` cannot emit `verdict: APPROVED` on a PR whose diff includes frontend/web files unless ≥1 screenshot file exists under `generated_images/pr-${PR_NUMBER}/` with mtime > REVIEW_START_TS
- [ ] When BYOB is unavailable on the reviewing machine, the skill emits `BLOCKED_ON_VISUAL_PROOF` and posts a `gh pr comment` explaining what's needed — never `gh pr review --approve`
- [ ] `is_frontend_pr()` exists in `scripts/pr_shape_classify.py` as the single shared helper; no regex copies in `screenshot.md`, `code-review.md`, or anywhere else
- [ ] The Pre-Verdict Checklist in `sub-skills/code-review.md` includes the visual-proof item with explicit pass/fail/N-A semantics (N/A only for diffs with zero frontend files)
- [ ] OUTCOME marker example in `post-review.md` and the verdict taxonomy in `docs/features/do-pr-review-bot-identity.md` both include `BLOCKED_ON_VISUAL_PROOF`
- [ ] Integration test (or recorded fixture) demonstrates: frontend diff + no screenshots → `BLOCKED_ON_VISUAL_PROOF`; frontend diff + screenshots → `APPROVED` allowed; non-frontend diff → existing behavior unchanged
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -r "is_frontend_pr" .claude/skills-global/do-pr-review/sub-skills/` confirms the helper is referenced from `code-review.md`

## Team Orchestration

### Team Members

- **Builder (helper + skill prose)**
  - Name: visual-proof-gate-builder
  - Role: Add `is_frontend_pr()` to `pr_shape_classify.py`, wire the new checklist item into `code-review.md`, and add the new decision-tree row to `post-review.md`. Update verdict taxonomy docs.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: visual-proof-gate-tests
  - Role: Add unit tests for `is_frontend_pr()` and the integration test for the three-case matrix (frontend+no-proof, frontend+proof, non-frontend).
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: visual-proof-gate-docs
  - Role: Create `docs/features/do-pr-review-visual-proof-gate.md`, update the verdict taxonomy in `do-pr-review-bot-identity.md`, add index entry to `docs/features/README.md`.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: visual-proof-gate-validator
  - Role: Verify success criteria. Run unit + integration tests. Confirm grep references. Confirm OUTCOME marker example includes new verdict.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add `is_frontend_pr()` helper
- **Task ID**: build-helper
- **Depends On**: none
- **Validates**: `tests/unit/scripts/test_pr_shape_classify.py` (existing or new)
- **Informed By**: Freshness Check (confirmed `scripts/pr_shape_classify.py` exists and uses `fnmatch`)
- **Assigned To**: visual-proof-gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a module-level `FRONTEND_GLOBS` tuple containing all globs from the issue's Definitions table
- Add `is_frontend_pr(files: list[str]) -> bool` with docstring and empty-list handling
- Include logic for Python files under `templates/`, `ui/templates/`, `static/` prefixes
- Add an optional `diff_hunks: dict[str, str] | None = None` parameter: when provided, also flag any Python file whose hunks add/modify lines matching `HTMLResponse(`, `render_template(`, `render(`, or `Response(...content_type="text/html"` (catches inline-HTML routes)
- Export it (no `__all__` restriction needed; module is already broadly imported)

### 2. Wire the checklist item and decision-tree row
- **Task ID**: build-skill-prose
- **Depends On**: build-helper
- **Validates**: `tests/integration/test_do_pr_review_visual_proof.py` (new)
- **Assigned To**: visual-proof-gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Capture `REVIEW_START_TS` in `code-review.md` Step 1 (before screenshot.md runs)
- Add the 13th Pre-Verdict Checklist item to `code-review.md` with explicit pass/fail/N-A semantics
- Add the verdict-derivation branch: failing checklist item + BYOB unreachable → `BLOCKED_ON_VISUAL_PROOF`; failing + BYOB reachable → re-run screenshot once, then hard error
- Add the new "Row 1.5" to the post-review.md decision tree with the comment template
- Update the inline `<!-- OUTCOME ... -->` example in `post-review.md` to include `BLOCKED_ON_VISUAL_PROOF`

### 3. Add unit and integration tests
- **Task ID**: build-tests
- **Depends On**: build-helper, build-skill-prose
- **Validates**: own tests + existing `/do-pr-review` integration tests still pass
- **Assigned To**: visual-proof-gate-tests
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit tests for `is_frontend_pr()`: pure frontend, pure backend, mixed, empty, edge cases (Python templates, tooling CSS)
- Integration test for the three-case matrix (frontend+no-proof → BLOCKED; frontend+proof → allowed APPROVED path; non-frontend → unchanged)
- Re-run existing `/do-pr-review` integration tests to confirm no regressions

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-skill-prose
- **Assigned To**: visual-proof-gate-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/do-pr-review-visual-proof-gate.md`
- Update verdict taxonomy in `docs/features/do-pr-review-bot-identity.md`
- Add index entry to `docs/features/README.md`
- Add cross-reference paragraph in `docs/features/byob-browser-control.md` if useful

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-helper, build-skill-prose, build-tests, document-feature
- **Assigned To**: visual-proof-gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification table checks
- Confirm grep references match expectations
- Confirm all success-criteria checkboxes are met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/scripts/test_pr_shape_classify.py -x -q` | exit code 0 |
| Integration tests pass | `pytest tests/integration/test_do_pr_review_visual_proof.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/pr_shape_classify.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/pr_shape_classify.py` | exit code 0 |
| Helper referenced by skill | `grep -l "is_frontend_pr" .claude/skills-global/do-pr-review/sub-skills/code-review.md` | output contains code-review.md |
| Verdict in taxonomy docs | `grep -l "BLOCKED_ON_VISUAL_PROOF" docs/features/do-pr-review-bot-identity.md` | output contains do-pr-review-bot-identity.md |
| OUTCOME marker updated | `grep "BLOCKED_ON_VISUAL_PROOF" .claude/skills-global/do-pr-review/sub-skills/post-review.md` | exit code 0 |
| New feature doc exists | `test -f docs/features/do-pr-review-visual-proof-gate.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->

---

## Open Questions

1. **Should the gate also fire for `*.md` files that render in a docs site?** Current scope says no — docs MD files are out of scope because this repo doesn't deploy a docs site. But if a sister repo using this skill does deploy MkDocs/Sphinx, the gate would silently miss documentation-rendering changes. Tentative answer: keep MD out of scope at v1; revisit if a sister repo trips on it.
2. **Should the orchestrator-level visual-proof override be loggable as a "fork disagreement"?** When the orchestrator overrides fork APPROVED → BLOCKED_ON_VISUAL_PROOF, should it emit a separate signal so calibration drift can be tracked? Tentative answer: yes, log it in the comment body but don't add a new analytics event at v1.
3. **The integration test for case (a) — frontend diff + no screenshots — needs a way to simulate BYOB unreachable.** Should the test patch `mcp__byob__browser_list_tabs` directly, or use a fixture that runs without BYOB infrastructure entirely? Tentative answer: fixture-based, mirroring how existing `/do-pr-review` tests handle BYOB absence.
