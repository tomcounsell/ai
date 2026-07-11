---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-12
tracking: https://github.com/tomcounsell/ai/issues/2034
last_comment_id:
---

# merge_predicate: resolve the SDLC-tracked issue, not the first Closes #N

## Problem

`tools/merge_predicate.py::_extract_issue_number()` uses `.search()` (first-match)
against `Closes/Fixes/Resolves #N` in the PR body. When a PR closes several
sub-issues under an umbrella tracking issue, the predicate keys its SDLC checks on
the FIRST closed sub-issue instead of the umbrella issue where the recorded REVIEW
verdict and DOCS marker actually live.

**Current behavior:**
On PR #2033 (body: `Closes #1871` / `Closes #1267` / `Closes #1760`, tracked under
umbrella #2029), the predicate resolved to #1871 — which has zero SDLC substrate —
and false-failed the merge gate with `"no recorded REVIEW verdict"` and
`"DOCS marker not authoritative"`. Ground truth on #2029 was REVIEW=APPROVED (0
blockers), DOCS=completed, PR MERGEABLE/CLEAN, CI green. The merge had to proceed
via the documented break-glass override (`data/merge_authorized_2033`). Every
multi-issue-closure PR following the umbrella pattern hits this and requires a
manual override.

**Desired outcome:**
The merge predicate keys groups (b) DOCS and (c) REVIEW-verdict on the
SDLC-tracked issue derived from the PR's branch slug, so a multi-issue-closure PR
passes the gate on its own recorded state. Single-issue PRs behave exactly as
today. No manual override required for the umbrella pattern.

## Freshness Check

**Baseline commit:** `30fbebb6`
**Issue filed at:** 2026-07-11T18:45:42Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/merge_predicate.py:237` — `_extract_issue_number` uses `_ISSUE_REF_CAPTURE_RE.search(body)` (first-match only) — **still holds**.
- `tools/merge_predicate.py:461-462` — resolved `issue_number` is passed to `_check_docs_stage` and `_check_verdict_freshness` — **still holds**.
- `tools/merge_predicate.py:445` — `head_ref = (pr or {}).get("headRefName")` already available in `evaluate_merge_predicate` — **still holds** (this is the seam the fix uses).
- `models/agent_session.py:201` (`issue_number = IntField(null=True)`) and `:298` (`slug = KeyField(null=True)`) — the slug→issue carrier — **still holds**.
- `agent/pipeline_ledger.py` — `PipelineLedger` keyed by `(target_repo, issue_number)`, no slug field — **still holds** (confirms the umbrella issue number is the only key reaching the recorded verdict/marker).

**Cited sibling issues/PRs re-checked:**
- PR #2033 / issue #2029 — the reported repro. #2033 already merged via override; the defect in the predicate remains.
- PR #2003 / #2010 (`2f324bff`) — introduced the current predicate. Last commit touching `tools/merge_predicate.py`; no later changes.

**Commits on main since issue was filed (touching referenced files):** none touching `tools/merge_predicate.py`.

**Active plans in `docs/plans/` overlapping this area:** `sdlc-router-convergence-redesign.md` references `tools/merge_predicate.py` as a *foundation/context* (the #2003 live merge gate) but does not modify `_extract_issue_number` or the issue-resolution logic. No build-target overlap. This plan owns the file's issue-resolution change exclusively.

**Notes:** #2000 is building concurrently in adjacent SDLC areas (`agent/sdk_client.py`, `agent/session_runner/`, `agent/sdlc_router.py`, `models/agent_session.py`). This plan MUST NOT touch any of those — see No-Gos and the scope fence in Technical Approach.

## Prior Art

- **PR #2003 / #2010** (`2f324bff`, "SDLC substrate: run_id ownership, live merge-predicate enforcement"): built the current `merge_predicate.py` and its `_extract_issue_number`. It correctly enforces PR-state + DOCS + REVIEW-verdict groups but never anticipated the umbrella multi-issue-closure pattern; the first-match `.search()` is the residual gap.
- Searched closed issues and merged PRs for `_extract_issue_number`, `multi-issue-closure`, `umbrella tracking` — **no prior fix attempt found**. This is the first fix for this defect.

## Data Flow

1. **Entry point**: `/do-merge` skill runs `python -m tools.merge_predicate --pr-number {PR} --json` (subprocess, full repo venv), OR the merge-guard hook imports `evaluate_merge_predicate(pr_number)` in-process.
2. **`evaluate_merge_predicate`**: probes substrate, calls `_check_pr_state` → `_gh_pr_view` returns the PR body + `headRefName`.
3. **Issue resolution (the defect site)**: `_extract_issue_number(body)` returns the FIRST `Closes #N`. Today this single value feeds both the group (a) body-link presence check and groups (b)/(c).
4. **Groups (b)/(c)**: `_check_docs_stage` and `_check_verdict_freshness` shell out to `sdlc-tool stage-query` / `verdict get` for that issue number. Wrong issue → empty state → false-fail.
5. **Output**: `PredicateResult(allowed=..., failed_checks=[...])`.

The fix inserts a slug→tracked-issue resolution between steps 3 and 4: groups (b)/(c) consume the tracked issue when resolvable; step 3's body check stays for group (a) presence.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

A single-file bug fix with a new helper and a focused regression test. The design is settled by recon; the bottleneck is review, not coding.

## Prerequisites

No external prerequisites — the fix uses only existing repo imports (`AgentSession`) and the already-invoked `gh`/`sdlc-tool` seams. Building and testing require the repo venv (already present).

## Solution

### Key Elements

- **`_resolve_tracked_issue(head_ref, repo_root)`** — new pure-ish helper in `tools/merge_predicate.py`. Derives the branch slug and returns the SDLC-tracked issue number carried by the matching `AgentSession`, or `None` when it cannot resolve one.
- **Preference wiring in `evaluate_merge_predicate`** — prefer the tracked issue for groups (b)/(c); fall back to the body-parsed issue when no tracked issue is resolvable.
- **Group (a) unchanged** — the PR body must still carry a `Closes/Fixes/Resolves #N` link (a PR must close something); this presence check keeps using `_extract_issue_number(body)`.

### Flow

Merge command → predicate reads PR body + head ref → resolve tracked issue from `session/{slug}` (umbrella #2029) → run DOCS + REVIEW checks against #2029 → PASS → merge allowed.

Fallback: no session for slug (or ambiguous) → use first `Closes #N` from body (today's behavior) → unchanged for single-issue PRs.

### Technical Approach

**Scope fence (load-bearing):** the build touches **`tools/merge_predicate.py` and its test file ONLY**. It MUST NOT modify `agent/sdk_client.py`, `agent/session_runner/`, `agent/sdlc_router.py`, `models/agent_session.py`, or `tools/sdlc_stage_query.py` (other lanes / #2000 own those). No new `sdlc-tool` subcommand.

1. **Add `_resolve_tracked_issue(head_ref: str, repo_root: Path) -> int | None`:**
   - `slug = _derive_slug(head_ref)`; if empty (`main`/`master`/`HEAD`/empty), return `None`.
   - Lazily import `from models.agent_session import AgentSession` inside a `try/except Exception` (mirrors the existing lazy-import fallback at lines 241-247). Any import or query failure returns `None` — never raises. This keeps the module stdlib-only at import time (bare hook interpreters degrade gracefully to body-parse).
   - `sessions = list(AgentSession.query.filter(slug=slug).all())` (indexed KeyField lookup; precedent `reflections/sdlc_progress.py:198`).
   - Collect the set of distinct non-null `issue_number` values across those sessions.
     - Exactly one distinct value → return it (the tracked umbrella issue).
     - Zero → return `None` (fall back to body parse).
     - More than one distinct value → **ambiguous → return `None`** (fall back to body parse; refuse to guess which umbrella wins). This is the conservative branch; it is a defined, tested path.

2. **Wire preference in `evaluate_merge_predicate` (lines ~444-462):**
   - After `pr, issue_number = _check_pr_state(...)` and `head_ref = (pr or {}).get("headRefName") or ""`, compute the effective issue only on the substrate branch:
     ```
     tracked = _resolve_tracked_issue(head_ref, root)
     effective_issue = tracked if tracked is not None else issue_number
     ```
   - Groups (b)/(c) run against `effective_issue`. When `tracked` is used and differs from the body issue, append a `note` recording the substitution (`"substrate checks keyed on SDLC-tracked issue #{tracked} (branch slug), not first Closes #{body}"`) for observability.
   - The existing `elif issue_number is None:` guard becomes `elif effective_issue is None:` — groups (b)/(c) still skip with a note when neither source resolves.
   - Group (a)'s "PR body lacks a Closes/Fixes/Resolves #N issue link" failure is untouched: it still triggers when `_extract_issue_number(body) is None`, independent of the tracked lookup.

3. **Single-issue invariance:** for a normal single-issue PR, the session's `issue_number` equals the body's `Closes #N`, so `tracked == issue_number` → identical checks. If the session lookup fails (no Redis / bare interpreter), `tracked is None` → body issue used → identical to today. Both paths are covered by tests.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `_resolve_tracked_issue` wraps the `AgentSession` import+query in `try/except Exception → return None`. Add a test that monkeypatches the import/query to raise and asserts the helper returns `None` (not a raise) AND the predicate falls back to the body issue (observable: groups b/c still run against the body issue).
- [ ] No `except Exception: pass` silent-swallow — the except returns `None`, a defined fallback signal, and the substitution/fallback is surfaced via `notes`.

### Empty/Invalid Input Handling
- [ ] `_resolve_tracked_issue("")`, `_resolve_tracked_issue("main")`, `_resolve_tracked_issue("session/")` all return `None` (no usable slug) — add direct unit tests.
- [ ] `head_ref=None` path: `_derive_slug(None)` already handles this; assert `_resolve_tracked_issue` tolerates a `None`/empty head ref.

### Error State Rendering
- [ ] When groups (b)/(c) fail on the *tracked* issue (genuinely un-approved), the failed-check messages still name the leg. Assert a failing tracked-issue case produces the same named failures as today, not a swallowed pass.

## Test Impact

- [ ] `tests/unit/test_validate_merge_guard.py` — no change required. It exercises the hook wrapper and monkeypatches `_evaluate_predicate` wholesale; it does not call `_extract_issue_number` or `_resolve_tracked_issue` directly. Verified by reading the file (tests at lines 61-260 all stub the predicate).
- [ ] `tests/unit/test_merge_predicate.py` — CREATE. No dedicated unit test for `merge_predicate.py` exists today; this is the new home for the regression test and the helper's unit coverage.

No existing test asserts the first-match behavior as correct, so nothing needs UPDATE/DELETE — the change is additive plus one new test file. (Justification exceeds 50 chars: the only merge-predicate-adjacent test file stubs the predicate entirely and never touches the resolution helpers.)

## Rabbit Holes

- **Adding a `sdlc-tool slug-to-issue` subcommand.** Tempting for the bare-interpreter hook path, but it touches `tools/sdlc_stage_query.py` — out of scope, and #2000-adjacent. The in-process guarded import fully covers the reported `/do-merge` subprocess path.
- **Reworking `PipelineLedger` to carry a slug field.** Out of scope; the `AgentSession.slug → issue_number` mapping already exists and is indexed.
- **Disambiguating multiple umbrella issues per slug via recency/status heuristics.** By convention a slug ties 1:1 to one tracked issue; the ambiguous branch conservatively falls back to body-parse. Building a ranking heuristic is gold-plating.
- **Changing `_extract_issue_number` to return all matches.** Not needed — group (a) only needs presence; groups (b)/(c) get the tracked issue from the slug.

## Risks

### Risk 1: Bare hook-interpreter cannot import `AgentSession`
**Impact:** In the merge-guard hook path, if the interpreter lacks repo deps (popoto/Redis), `_resolve_tracked_issue` returns `None` and the predicate falls back to the first `Closes #N` — the multi-issue false-fail persists in that path.
**Mitigation:** By design per the fix direction ("fall back to body-parsing only when no tracked issue is resolvable"). The reported break is the `/do-merge` skill, which runs `python -m tools.merge_predicate` as a subprocess under the full repo venv where the import always succeeds — fully fixed. The hook path's fail-closed posture is unchanged (a false-fail blocks, never wrongly allows). No silent regression. Documented as a known limitation; a follow-up could add a subprocess resolver if the hook path proves to bite in practice.

### Risk 2: Slug collision resolves to the wrong issue
**Impact:** If two unrelated sessions share a slug with different `issue_number`s, keying on the wrong one could false-fail or (worse) pass on the wrong issue's state.
**Mitigation:** The helper treats multiple distinct issue numbers as **ambiguous → `None` → body fallback**, never guessing. Slugs are 1:1 with a tracked issue by repo convention (slug derives branch/worktree/plan/issue). Covered by an explicit ambiguity test.

### Risk 3: Tracked issue differs from body issue for a legitimate single-issue PR
**Impact:** A spurious substitution could change which issue's state gates the merge.
**Mitigation:** For single-issue PRs the session's `issue_number` equals the body `Closes #N`, so `tracked == body` — no behavior change. Regression test asserts single-issue invariance both with and without a resolvable session.

## Race Conditions

No race conditions identified — the predicate is a synchronous, read-only evaluation (subprocess `gh`/`sdlc-tool` calls and one indexed Redis read). It mutates no shared state; concurrent evaluations of different PRs are independent.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2034] The bare hook-interpreter subprocess-resolver hardening (Risk 1) is deliberately not built here; the in-process guarded import covers the reported `/do-merge` path. If the hook path proves to bite, it is a follow-up on this same issue's learnings — no new code paths in `sdlc_stage_query.py` in this plan.
- Modifying `agent/sdk_client.py`, `agent/session_runner/`, `agent/sdlc_router.py`, `models/agent_session.py`, or `tools/sdlc_stage_query.py` — owned by other lanes (#2000 building concurrently). This plan is fenced to `tools/merge_predicate.py` + its test.

## Update System

No update system changes required — this is a purely internal fix to one tool module. No new dependencies, no config files, no `scripts/update/run.py` or `migrations.py` changes. No Popoto schema change (the fix only *reads* the existing `AgentSession.slug`/`issue_number` fields).

## Agent Integration

No agent integration required — `tools/merge_predicate.py` is already reachable via its CLI entry (`python -m tools.merge_predicate`) used by the `/do-merge` skill, and via in-process import by the merge-guard hook. The fix changes internal resolution logic behind those existing surfaces; no new MCP tool, `.mcp.json`, or bridge wiring.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/machine-readable-dod.md` OR the merge-predicate section of `docs/sdlc/do-merge.md` to note that groups (b)/(c) key on the SDLC-tracked issue derived from the branch slug (with first-`Closes` fallback), so multi-issue-closure PRs under an umbrella tracking issue pass without override.
- [ ] If no dedicated merge-predicate feature doc exists, add a short subsection to `docs/features/README.md`-indexed doc describing the tracked-issue resolution. (Confirm the exact target during build; the module docstring in `tools/merge_predicate.py` must also be updated to describe the new resolution order.)

### Inline Documentation
- [ ] Docstring on `_resolve_tracked_issue` explaining slug→session→issue resolution, the ambiguity fallback, and the guarded import.
- [ ] Update the module-level docstring's group (b)/(c) description to state they key on the tracked issue.

## Success Criteria

- [ ] `_resolve_tracked_issue` returns the umbrella `issue_number` for a `session/{slug}` head ref whose `AgentSession` carries it; returns `None` for no-slug, no-session, and ambiguous-multi-issue cases.
- [ ] `evaluate_merge_predicate` keys groups (b)/(c) on the tracked issue when resolvable, else the body `Closes #N`; group (a) body-link presence check unchanged.
- [ ] Regression test reproduces the PR #2033 shape (body `Closes #1871/#1267/#1760`, session slug → #2029) and asserts DOCS/REVIEW checks query #2029, not #1871.
- [ ] Single-issue-PR invariance test passes (tracked == body, and session-absent fallback == body).
- [ ] Change is confined to `tools/merge_predicate.py` + `tests/unit/test_merge_predicate.py` (git diff touches no other source files).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (merge-predicate)**
  - Name: predicate-builder
  - Role: Implement `_resolve_tracked_issue`, wire the preference in `evaluate_merge_predicate`, update docstrings, add `tests/unit/test_merge_predicate.py`.
  - Agent Type: builder
  - Resume: true

- **Validator (merge-predicate)**
  - Name: predicate-validator
  - Role: Verify scope fence (diff touches only the two files), regression + invariance tests pass, no raw-Redis, black-clean.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement tracked-issue resolution + wiring
- **Task ID**: build-predicate
- **Depends On**: none
- **Validates**: tests/unit/test_merge_predicate.py (create)
- **Assigned To**: predicate-builder
- **Agent Type**: builder
- **Parallel**: false
- **Domain**: redis-popoto — read-only `AgentSession.query.filter(slug=...)`; never raw Redis; wrap import+query in `try/except Exception → return None`.
- Add `_resolve_tracked_issue(head_ref, repo_root)` per Technical Approach (guarded lazy import, distinct-issue-set logic, ambiguity → None).
- Wire `effective_issue` preference into `evaluate_merge_predicate`; update the `elif` guard; add the substitution `note`.
- Update the module docstring (groups b/c key on tracked issue) and add the helper docstring.
- Work in a dedicated slug worktree at `.worktrees/merge-predicate-tracked-issue-resolution/` on branch `session/merge-predicate-tracked-issue-resolution` — NOT the shared main checkout.
- Do NOT edit any file outside `tools/merge_predicate.py` and the new test.

### 2. Write regression + unit tests
- **Task ID**: build-tests
- **Depends On**: build-predicate
- **Assigned To**: predicate-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_merge_predicate.py`:
  - Multi-issue-closure regression: body `Closes #1871/#1267/#1760`, monkeypatch `AgentSession.query.filter` (or the helper's session source) to yield a session with `slug` matching and `issue_number=2029`; assert `_check_docs_stage`/`_check_verdict_freshness` are called with `2029` (monkeypatch `_run_stage_query`/`_run_verdict_get` to record the issue arg).
  - Single-issue invariance: body `Closes #42`, session `issue_number=42` → checks keyed on 42; and session-absent → falls back to 42.
  - `_resolve_tracked_issue` unit cases: no-slug (`main`/empty/`session/`), no session, ambiguous multi-issue → all `None`; happy path → issue number.
  - Guarded-import failure: patch the import to raise → helper returns `None`, predicate falls back to body issue.
  - Group (a) unchanged: empty body → "PR body lacks a Closes/Fixes/Resolves #N issue link".

### 3. Validation
- **Task ID**: validate-predicate
- **Depends On**: build-tests
- **Assigned To**: predicate-validator
- **Agent Type**: validator
- **Parallel**: false
- `git diff --name-only main` shows ONLY `tools/merge_predicate.py` and `tests/unit/test_merge_predicate.py`.
- `pytest tests/unit/test_merge_predicate.py -q` passes; `pytest tests/unit/test_validate_merge_guard.py -q` still passes.
- `python -m ruff check tools/merge_predicate.py tests/unit/test_merge_predicate.py` and `black`-format clean.
- No raw Redis ops introduced.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-predicate
- **Assigned To**: predicate-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update the merge-predicate resolution-order note in the doc identified during build (`docs/sdlc/do-merge.md` or `docs/features/machine-readable-dod.md`).

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: predicate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification-table checks; confirm every Success Criterion; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_merge_predicate.py tests/unit/test_validate_merge_guard.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/merge_predicate.py tests/unit/test_merge_predicate.py` | exit code 0 |
| Format clean | `python -m black --check tools/merge_predicate.py tests/unit/test_merge_predicate.py` | exit code 0 |
| Scope fence (only two files changed) | `git diff --name-only main -- . ':!docs' \| grep -v -E '^(tools/merge_predicate\.py\|tests/unit/test_merge_predicate\.py)$' \| wc -l \| tr -d ' '` | output contains 0 |
| No out-of-scope source edits | `git diff --name-only main \| grep -E '^(agent/sdk_client\.py\|agent/session_runner/\|agent/sdlc_router\.py\|models/agent_session\.py\|tools/sdlc_stage_query\.py)'` | exit code 1 |
| Tracked-issue helper present | `grep -c '_resolve_tracked_issue' tools/merge_predicate.py` | output > 1 |
| No raw Redis introduced | `grep -nE '\.(delete\|srem\|sadd\|zrem)\(' tools/merge_predicate.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. Documentation target: update `docs/sdlc/do-merge.md`'s merge-predicate section, or `docs/features/machine-readable-dod.md`? (Build will confirm which already describes the predicate groups; default is `docs/sdlc/do-merge.md`.)
2. Bare hook-interpreter coverage (Risk 1): accept the body-parse fallback for the in-process hook path as a known limitation for now (the reported `/do-merge` subprocess path is fully fixed), or is subprocess-based resolution wanted in this same change? Note: a subprocess resolver would require touching `tools/sdlc_stage_query.py`, which the scope fence forbids.
