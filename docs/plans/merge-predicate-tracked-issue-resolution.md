---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-12
tracking: https://github.com/tomcounsell/ai/issues/2034
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-11T19:13:27Z
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

**Builder latitude (per critique concern #4):** this is a Small bug fix. The sole integration point is `evaluate_merge_predicate` at roughly `tools/merge_predicate.py:429-469`. The builder owns naming, exact signatures, control flow, and how the tracked/ambiguous/no-signal outcome is represented in code (a tri-state enum, a sentinel, a `(issue, ambiguous)` tuple — builder's call). The paragraphs below state the *required behavior*, not a literal diff. What is mandatory: group (a) still uses the raw `_extract_issue_number(body)` presence check; groups (b)/(c) key on the SDLC-tracked issue when one resolves; genuine ambiguity fails closed (below); and the guarded imports never raise into the merge-guard hook.

1. **Add a tracked-issue resolver (behavior contract, name at builder's discretion — e.g. `_resolve_tracked_issue(head_ref, repo_root)`):** it inspects the branch slug and returns one of three outcomes — a resolved tracked issue number, "no signal" (caller uses the body issue), or "ambiguous" (caller fails closed). Required behavior:
   - `slug = _derive_slug(head_ref)`; if empty (`main`/`master`/`HEAD`/`None`/`session/`), outcome is **no signal** (single-issue PRs and non-substrate branches behave exactly as today).
   - **Two explicit guards (per critique concern #3), both broad `except Exception`:**
     ```
     try:
         from models.agent_session import AgentSession
         from models.session_lifecycle import NON_TERMINAL_STATUSES
         from config.project_key_resolver import resolve_project_key
     except Exception:
         return <no signal>          # import-time failure (module absent OR Redis/Popoto/settings init raising) → body-parse
     try:
         sessions = list(AgentSession.query.filter(slug=slug).all())
     except Exception:
         return <no signal>          # Redis/query outage degrades to body-parse, never crashes the hook
     ```
     **Both guards are broad `except Exception`** — this is the exact `reflections/sdlc_progress.py:185-205` shape. The import guard must NOT be narrowed to `except ImportError`: importing `models.agent_session` / `config.project_key_resolver` can raise non-`ImportError` types at import time (Redis/Popoto client init, settings validation), and those are precisely the failures that would otherwise escape and crash the merge-guard hook. `ModuleNotFoundError` is already an `ImportError` subclass, so narrowing buys nothing and loses the runtime-failure coverage the "never raise into the hook" mandate requires.
   - **Project-scope the candidate set (per critique concern #2), forcing cwd-only resolution:** compute `project = resolve_project_key(cwd=str(repo_root), env={})` and keep only sessions whose `project_key == project`. **The `env={}` is load-bearing:** `resolve_project_key` returns `VALOR_PROJECT_KEY` from the ambient env at priority (2) *before* the cwd-prefix match, and worker/session-runner processes commonly inject that var — so without `env={}` the scoping would key on the ambient session's project, not `repo_root`, silently bypassing the guard (wrong-allow direction). Passing an empty dict (not `None`) neutralizes the env lookup without re-reading `os.environ` (verified: the resolver uses `env if env is not None else os.environ`). A session in a different project (cross-project slug collision) is discarded — treated identically to "no matching session". If `project` is `None` (projects.json unreadable), treat the whole lookup as **no signal** with a distinguishing note. `AgentSession` carries `project_key` (KeyField, `models/agent_session.py:143`) — no model change needed.
   - **Filter to live, non-transitional sessions:** keep only sessions whose `status in (NON_TERMINAL_STATUSES - {"superseded"})` (`models/session_lifecycle.py:72`). `NON_TERMINAL_STATUSES` includes the transitional `"superseded"` (and `"paused_budget"`); the `any(...)` precedent in `sdlc_progress.py` tolerates those, but this plan's stricter *distinct-`issue_number`* logic does not — a stale `superseded` session with a divergent issue would inflate the set to ≥2 → false-ambiguous → a legitimate merge blocked (a self-inflicted outage). Excluding `"superseded"` is required; also exclude `"paused_budget"` if it can carry a divergent issue. This makes "a stale/transitional session cannot pollute the distinct-issue set" literally true.
   - Collect the set of distinct non-null `issue_number` values across the surviving (project-scoped, live) sessions:
     - **Exactly one** distinct value → outcome is that tracked umbrella issue.
     - **Zero** → **no signal** (fall back to body parse; distinguishing note "no session found for slug {slug}").
     - **More than one** distinct value → **ambiguous** (below). Do NOT return the body issue here — refuse to guess, and do not silently reuse the pre-fix first-match value.

2. **Wire preference in `evaluate_merge_predicate` (~lines 444-462):**
   - After `_check_pr_state(...)` yields the body `issue_number` and `head_ref` is available, consult the resolver and pick the effective issue for groups (b)/(c):
     - **Tracked issue resolved** → groups (b)/(c) key on it. When it differs from the body issue, append a substitution `note` (e.g. `"substrate checks keyed on SDLC-tracked issue #{tracked} (branch slug), not first Closes #{body}"`) for observability.
     - **No signal** → groups (b)/(c) key on the body `issue_number` exactly as today. Append a distinguishing note ("no session found for slug ..." vs "project unresolved for repo_root ...") so an on-call engineer can tell *which* no-signal path fired (per critique nit).
     - **Ambiguous → FAIL CLOSED (per critique concern #1):** do not run groups (b)/(c) against a guessed issue. Instead append an explicit gate failure to `failed` — mirroring the existing `_check_pr_state` "PR body lacks a Closes/Fixes/Resolves #N issue link" append at `tools/merge_predicate.py:314-316` — e.g. `"tracked-issue lookup ambiguous: {N} distinct issues for branch slug '{slug}'; cannot determine which issue carries the SDLC substrate"`. This blocks the merge (fail-closed → human resolves or break-glass override), which is strictly safer than silently reverting to the first-match body issue this plan exists to eliminate. Ambiguity is a defined, tested outcome, not an incidental `None`.
   - The `elif ... is None:` skip guard for groups (b)/(c) keys on the *effective* issue (the tracked issue when resolved, else the body issue): they still skip-with-note when neither the tracked lookup nor the body yields an issue. The ambiguous outcome does not reach this branch — it has already appended its own gate failure.
   - Group (a)'s "PR body lacks a Closes/Fixes/Resolves #N issue link" failure is untouched: it still triggers when `_extract_issue_number(body) is None`, independent of the tracked lookup.

3. **Single-issue invariance:** for a normal single-issue PR, the live session's `issue_number` equals the body's `Closes #N`, so the resolver returns that same issue → identical checks. If the session lookup fails (no Redis / bare interpreter / project unresolved), the outcome is no-signal → body issue used → identical to today. Both paths are covered by tests. The ambiguous fail-closed path is unreachable for a well-formed single-issue PR (one live session, one issue number).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The resolver's query guard is exercised: monkeypatch the session query to raise and assert the helper degrades to the **no-signal** outcome (not a raise) AND the predicate falls back to the body issue (observable: groups b/c still run against the body issue). Cover the import-guard path too (simulate `AgentSession` import failure → no signal).
- [ ] No `except Exception: pass` silent-swallow — each guard returns a defined outcome (no signal), and the fallback/substitution/ambiguity is surfaced via `notes` or a `failed` entry.

### Empty/Invalid Input Handling
- [ ] Empty/non-substrate head refs (`""`, `"main"`, `"session/"`, `None`) all resolve to **no signal** (no usable slug) — add direct unit tests; assert the helper tolerates a `None`/empty head ref via `_derive_slug`.

### Ambiguity & Project-Scope Coverage (critique concerns #1, #2)
- [ ] **Ambiguity fails closed:** two live, same-project sessions sharing the slug with *distinct* `issue_number`s → the predicate appends the explicit "tracked-issue lookup ambiguous" gate failure to `failed` and does NOT run groups (b)/(c) against a guessed issue. Assert the failure text names the slug and the distinct count.
- [ ] **Terminal sessions don't pollute:** one live session (`issue_number=2029`) plus a terminal (e.g. `completed`) session carrying a different `issue_number` for the same slug → resolves to 2029, NOT ambiguous (the terminal one is filtered out before the distinct-issue set).
- [ ] **Project scoping discards cross-project collisions:** a session with the matching slug but a *different* `project_key` than `resolve_project_key(repo_root)` is ignored → treated as no matching session (body fallback), never substituted. Assert groups (b)/(c) key on the body issue, not the foreign session's issue.
- [ ] **Project unresolved → no signal:** monkeypatch `resolve_project_key` to return `None` → the whole lookup is no-signal (body fallback) with a distinguishing note, never accepting an unscoped candidate.
- [ ] **Distinguishing notes:** assert the no-session note ("no session found for slug ...") and the project-unresolved note are distinct strings, so the fired path is verifiable from predicate output (critique nit).

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
- [ ] Docstring on the tracked-issue resolver explaining slug→(project-scoped, live)→session→issue resolution, the three outcomes (tracked / no-signal / ambiguous), the two guards, and the fail-closed ambiguity direction.
- [ ] Update the module-level docstring's group (b)/(c) description to state they key on the SDLC-tracked issue derived from the branch slug, with first-`Closes` fallback and fail-closed on ambiguity.

## Success Criteria

- [ ] The resolver returns the umbrella `issue_number` for a `session/{slug}` head ref whose single live, same-project `AgentSession` carries it; yields **no signal** for no-slug, no-session, project-unresolved, and cross-project-only cases; yields **ambiguous** for >1 distinct `issue_number` among live same-project sessions.
- [ ] `evaluate_merge_predicate` keys groups (b)/(c) on the tracked issue when resolved, else the body `Closes #N`; on **ambiguous** it appends an explicit fail-closed gate failure and does not guess; group (a) body-link presence check unchanged.
- [ ] Regression test reproduces the PR #2033 shape (body `Closes #1871/#1267/#1760`, session slug → #2029) and asserts DOCS/REVIEW checks query #2029, not #1871.
- [ ] Ambiguity, terminal-session filtering, and cross-project scoping each have a passing unit test (critique concerns #1, #2); no-session vs project-unresolved notes are distinct strings.
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

> **Normativity (per critique concern #4):** the **Technical Approach** section is the single authoritative behavior contract — its "Builder latitude" note and the four mandatory behaviors govern. The steps below are an *illustrative* task breakdown for sequencing and ownership; where any wording here reads as more prescriptive than Technical Approach (a specific name, signature, or literal edit), treat it as illustrative and defer to Technical Approach.

### 1. Implement tracked-issue resolution + wiring
- **Task ID**: build-predicate
- **Depends On**: none
- **Validates**: tests/unit/test_merge_predicate.py (create)
- **Assigned To**: predicate-builder
- **Agent Type**: builder
- **Parallel**: false
- **Domain**: redis-popoto — read-only `AgentSession.query.filter(slug=...)`; never raw Redis. Guard the lazy imports (narrow `except ImportError`) and the query (broad `except Exception`) as two separate blocks per Technical Approach step 1; each degrades to the no-signal outcome, never raises.
- Add the tracked-issue resolver per Technical Approach (name/signature at builder's discretion): two guards, project-scope via `resolve_project_key(cwd=repo_root)`, `NON_TERMINAL_STATUSES` filter, distinct-issue-set logic yielding tracked / no-signal / ambiguous.
- Wire the effective-issue preference into `evaluate_merge_predicate` at its sole integration point (~`tools/merge_predicate.py:429-469`): tracked → key groups (b)/(c) on it with a substitution note; no-signal → body issue with a distinguishing note; ambiguous → append an explicit fail-closed gate failure (mirroring the `:314-316` append) and do not guess.
- Update the module docstring (groups b/c key on tracked issue, fail-closed on ambiguity) and add the resolver docstring.
- Build in the session worktree at `.worktrees/dev-186fff1e/` on branch `session/dev-186fff1e` (this session owns it) — NOT the shared main checkout.
- Do NOT edit any file outside `tools/merge_predicate.py` and the new test.

### 2. Write regression + unit tests
- **Task ID**: build-tests
- **Depends On**: build-predicate
- **Assigned To**: predicate-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_merge_predicate.py`:
  - Multi-issue-closure regression: body `Closes #1871/#1267/#1760`, monkeypatch `AgentSession.query.filter` (or the helper's session source) to yield a session with `slug` matching and `issue_number=2029`; assert `_check_docs_stage`/`_check_verdict_freshness` are called with `2029` (monkeypatch `_run_stage_query`/`_run_verdict_get` to record the issue arg).
  - Single-issue invariance: body `Closes #42`, live same-project session `issue_number=42` → checks keyed on 42; and session-absent → falls back to 42.
  - Resolver unit cases: no-slug (`main`/empty/`session/`) → no signal; no session → no signal; happy path → tracked issue number.
  - **Ambiguity fails closed** (concern #1): two live same-project sessions, distinct `issue_number`s → predicate appends the "tracked-issue lookup ambiguous" gate failure; groups (b)/(c) are not keyed on a guessed issue.
  - **Terminal-session filtering** (concern #1): a live `issue_number=2029` session plus a `completed` session with a different issue for the same slug → resolves to 2029, not ambiguous.
  - **Project scoping** (concern #2): a matching-slug session with a foreign `project_key` is discarded → body fallback; and `resolve_project_key → None` → no signal with its distinguishing note.
  - Guarded-import / query failure: patch the import (ImportError) and separately the query (Exception) to raise → resolver yields no signal, predicate falls back to body issue.
  - Distinguishing notes: no-session note ≠ project-unresolved note (concern nit).
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

<!-- Populated by /do-plan-critique (war room), 2026-07-12. Verdict: READY TO BUILD (with concerns). 0 blockers, 4 concerns, 2 nits. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness + History & Consistency | Ambiguous slug resolution (`>1` distinct `issue_number`, or a stale terminal session polluting the set) makes the resolver return `None`, so the effective issue silently falls back to the first-match body-parsed `issue_number` — the exact defect this plan fixes, for the umbrella case. | **RESOLVED — revision** (Technical Approach step 1/2, Failure Path "Ambiguity & Project-Scope Coverage", task 2) | Ambiguity is now a distinct tri-state outcome: filter to `NON_TERMINAL_STATUSES` before the distinct-issue set; on genuine ambiguity append an explicit fail-closed gate failure (mirrors `:314-316`) instead of reusing the body issue. Tested. |
| CONCERN | Risk & Robustness | `AgentSession.query.filter(slug=slug)` has no repo/project scoping, so a cross-project slug collision could substitute a wrong (possibly APPROVED) `issue_number` — the wrong-allow direction. | **RESOLVED — revision** (Technical Approach step 1, task 2) | Candidate sessions are post-filtered by `project_key == resolve_project_key(cwd=repo_root)` (`config/project_key_resolver.py`); a project mismatch is treated as no matching session; `project=None` → no-signal (never accept unscoped). `AgentSession.project_key` exists — no model change. Tested. |
| CONCERN | History & Consistency | The plan cited two different precedents for the guarded lazy import as if they were one (`except ImportError` only vs broad `except Exception`). | **RESOLVED — revision** (Technical Approach step 1) | Two explicit guards: narrow `except ImportError` around the imports, separate broad `except Exception` around the query — the `sdlc_progress.py:190-205` shape, stated precisely. |
| CONCERN | Scope & Value | For a Small bug fix the plan over-specified the builder's implementation (exact signature, rename, literal `elif`). | **RESOLVED — revision** ("Builder latitude" note in Technical Approach; tasks reworded to behavior) | Added an explicit builder-latitude paragraph: sole integration point `~:429-469`, builder owns naming/control flow/outcome representation; only the required behavior (group-a presence, groups-b/c tracked keying, fail-closed ambiguity, non-raising guards) is mandated. |
| NIT | Risk & Robustness | No `notes` entry distinguished the ambiguity path from the no-session path. | **RESOLVED — revision** (Technical Approach step 2, task 2) | Distinct notes: "no session found for slug ..." vs project-unresolved vs the ambiguous `failed` entry; tested as distinct strings. |
| NIT | Scope & Value | The ambiguous-multi-issue branch may be theoretical; keep the cheap defensive branch but don't over-invest a real-world repro. | build | Synthetic-fixture unit test only (task 2) — no live repro. |

### Re-critique round 2 (2026-07-11, verdict: READY TO BUILD — 0 blockers, 4 concerns, 2 nits)

Re-critique of the revised plan confirmed all round-1 findings resolved and surfaced four sharpening concerns, folded into Technical Approach for the builder:

| Severity | Finding | Addressed By |
|----------|---------|--------------|
| CONCERN | Import guard `except ImportError` is narrower than its precedent (`sdlc_progress.py:185-205` is broad `except Exception`) and would let non-`ImportError` import-time failures (Redis/Popoto/settings init) escape and crash the hook. | **RESOLVED** — Technical Approach step 1: both guards are now broad `except Exception`; citation corrected to `185-205`. |
| CONCERN | `resolve_project_key(cwd=repo_root)` honors `VALOR_PROJECT_KEY` env at priority (2) before the cwd match; worker/session-runner inject it, so scoping could key on the ambient project (wrong-allow). | **RESOLVED** — Technical Approach step 1: call `resolve_project_key(cwd=str(repo_root), env={})` to force cwd-only scoping. |
| CONCERN | `NON_TERMINAL_STATUSES` includes transitional `"superseded"`/`"paused_budget"`; a stale such session inflates the distinct-issue set → false-ambiguous → self-inflicted merge outage. | **RESOLVED** — Technical Approach step 1: filter on `NON_TERMINAL_STATUSES - {"superseded"}` (exclude `paused_budget` too). |
| CONCERN | "Builder latitude" note contradicted the still-prescriptive step-by-step (ambiguous normativity). | **RESOLVED** — added a Normativity note at the top of Step by Step Tasks: Technical Approach is authoritative; steps are illustrative. |

Nits (framing only): "eliminates" → "reduces" break-glass framing; precedent line-range 190-205 → 185-205 (both corrected in text).

---

## Open Questions

1. Documentation target: update `docs/sdlc/do-merge.md`'s merge-predicate section, or `docs/features/machine-readable-dod.md`? (Build will confirm which already describes the predicate groups; default is `docs/sdlc/do-merge.md`.)
2. Bare hook-interpreter coverage (Risk 1): accept the body-parse fallback for the in-process hook path as a known limitation for now (the reported `/do-merge` subprocess path is fully fixed), or is subprocess-based resolution wanted in this same change? Note: a subprocess resolver would require touching `tools/sdlc_stage_query.py`, which the scope fence forbids.
