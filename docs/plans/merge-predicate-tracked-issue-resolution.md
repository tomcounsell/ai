---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-12
tracking: https://github.com/tomcounsell/ai/issues/2034
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-11T19:17:57Z
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
SDLC-tracked umbrella issue, resolved from the durable `PipelineLedger` by the PR
number, so a multi-issue-closure PR passes the gate on its own recorded state.
Single-issue PRs behave exactly as today. No manual override required for the
umbrella pattern.

## Revision Note (critique round 1 → this revision)

The first draft resolved the umbrella issue via
`AgentSession.query.filter(slug=slug) → issue_number`. Critique found that
mechanism **empirically inert** and returned NEEDS REVISION with two blockers:

1. **The lookup returns nothing on its own reported case.** Across the live
   session set, ZERO sessions carry both `slug` and `issue_number`: the two
   fields are written by disjoint creation paths
   (`sdlc_session_ensure.py::create_local` sets `issue_number`, never `slug`;
   `valor_session.py` sets `slug`, never `issue_number`). On the exact repro,
   `AgentSession.query.filter(slug='sdlc-router-convergence-redesign').all()`
   returns **0 rows** (re-verified live this revision — Empirical Verification,
   Gate 1). The slug→issue lookup did nothing.
2. **The round-1 regression test monkeypatched both fields onto one record** — a
   shape with zero production instances — so it passed green while the bug
   shipped unfixed.

This revision replaces the mechanism with a **`PipelineLedger` reverse-lookup by
PR number** (`PipelineLedger.query.filter(pr_number=<PR>) → issue_number`), which
is populated in production and resolves the #2033 → #2029 repro live. The
regression test is rebuilt on **real, production-shaped `PipelineLedger` records**
so a no-op fix FAILS. Both are proven against live data below.

## Freshness Check

**Baseline commit:** `3ddda26f` (HEAD at revision time)
**Issue filed at:** 2026-07-11T18:45:42Z
**Disposition:** Unchanged

**File:line references re-verified (this revision):**
- `tools/merge_predicate.py:237` — `_extract_issue_number` uses `_ISSUE_REF_CAPTURE_RE.search(body)` (first-match only) — **still holds**.
- `tools/merge_predicate.py:444-445` — `pr, issue_number = _check_pr_state(...)` then `head_ref = (pr or {}).get("headRefName")`; `pr_number` is already the function argument to `evaluate_merge_predicate` — **still holds** (the seam the fix wires into).
- `tools/merge_predicate.py:455-462` — resolved `issue_number` is passed to `_check_docs_stage` and `_check_verdict_freshness` — **still holds**.
- `agent/pipeline_ledger.py:83` — `pr_number = IntField(null=True)` on `PipelineLedger`, model keyed by `(target_repo, issue_number)` — **still holds** (the reverse-lookup carrier).
- `tools/sdlc_meta_set.py:15-22, 76-80` — `sdlc-tool meta-set --key pr_number` is the SINGLE writer of `PipelineLedger.pr_number` via `ledger.save()`, invoked by `/do-build` at PR creation — **still holds** (confirms the field is populated at build time, before merge).

**Commits on main since issue was filed (touching referenced files):** none touching `tools/merge_predicate.py` (last change `2f324bff`, 2026-07-11).

**Active plans in `docs/plans/` overlapping this area:** `sdlc-router-convergence-redesign.md` references `tools/merge_predicate.py` as *foundation/context* only; it does not modify the issue-resolution logic. No build-target overlap. `agent/pipeline_ledger.py` is a shipped, stable model (#2012) — this plan only READS it.

**Notes:** #2000 is building concurrently in adjacent SDLC areas (`agent/sdk_client.py`, `agent/session_runner/`, `agent/sdlc_router.py`, `models/agent_session.py`, `tools/sdlc_stage_query.py`). This plan MUST NOT touch any of those — see No-Gos and the scope fence. `agent/pipeline_ledger.py` is NOT on the fenced list and is imported read-only.

## Empirical Verification (the critique gate)

Every claim below was run live against the worker's Redis at revision time — the
evidence the critique demanded before committing to the mechanism.

**Gate 1 — the round-1 mechanism is inert (must return []):**
```
AgentSession.query.filter(slug='sdlc-router-convergence-redesign').all()  → count: 0
```
Confirmed: the slug→issue lookup resolves nothing for the repro.

**Gate 2 — the PipelineLedger reverse-lookup resolves the repro (must return #2029):**
```
PipelineLedger.query.filter(pr_number=2033)
  → ledger_key=tomcounsell/ai:2029  target_repo=tomcounsell/ai  issue_number=2029  pr_number=2033
```
Exactly one row: the umbrella ledger. `filter(pr_number=...)` is a fast indexed
IntField query (1 row in 0.03s over 187 ledgers).

**Gate 3 — sub-issues do NOT carry the PR number (no false resolution):**
```
issue 1871: ledger pr_number=None
issue 1267: NO LEDGER
issue 1760: NO LEDGER
issue 2029: ledger pr_number=2033   ← only the umbrella carries it
```

**Gate 4 — `pr_number` is 1:1 across the whole ledger (no ambiguity in practice):**
```
total ledgers: 187   with pr_number set: 7   pr_numbers on >1 ledger: {}  (none)
distinct target_repo values: ['tomcounsell/ai']
```

Conclusion: resolving the tracked issue by `pr_number` is populated, unique, and
correct on the reported case. The mechanism is empirically live, not inferred.

## Prior Art

- **PR #2003 / #2010** (`2f324bff`): built the current `merge_predicate.py` and its
  `_extract_issue_number`. It enforces PR-state + DOCS + REVIEW-verdict groups but
  never anticipated the umbrella multi-issue-closure pattern; the first-match
  `.search()` is the residual gap.
- **PR #2012** (`agent/pipeline_ledger.py`): moved the durable SDLC ledger to the
  `(target_repo, issue_number)` pair, carrying `pr_number` as a field-backed
  attribute. This is the substrate the fix reads.
- Searched closed issues and merged PRs for `_extract_issue_number`,
  `multi-issue-closure`, `umbrella tracking` — **no prior fix attempt found**.
  Round 1 of this same plan proposed a slug-based mechanism that critique
  rejected as inert; superseded here.

## Data Flow

1. **Entry point**: `/do-merge` runs `python -m tools.merge_predicate --pr-number {PR} --json` (subprocess, full repo venv), OR the merge-guard hook imports `evaluate_merge_predicate(pr_number)` in-process.
2. **`evaluate_merge_predicate`**: probes substrate, calls `_check_pr_state` → `_gh_pr_view` returns the PR body + `headRefName`. `_extract_issue_number(body)` returns the FIRST `Closes #N` (group (a) presence + body fallback).
3. **Tracked-issue resolution (the fix)**: `_resolve_tracked_issue(pr_number, root)` queries `PipelineLedger.query.filter(pr_number=pr_number)`, scopes by `target_repo` when resolvable, and returns the single distinct `issue_number` (the umbrella #2029), or `None` on no-match / ambiguity / import failure.
4. **Groups (b)/(c)**: `_check_docs_stage` and `_check_verdict_freshness` shell out to `sdlc-tool stage-query` / `verdict get` for the **effective** issue (tracked if resolved, else body first-match). Right issue → real state → correct pass/fail.
5. **Output**: `PredicateResult(allowed=..., failed_checks=[...], notes=[...])`; a substitution note records `#2029 (ledger pr_number) not first Closes #1871` for observability.

The fix inserts the reverse-lookup between steps 2 and 4: groups (b)/(c) consume
the tracked issue when resolvable; step 2's body check stays for group (a)
presence.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

A single-file bug fix with a new helper and a focused regression test. The
mechanism is settled and proven against live data; the bottleneck is review.

## Prerequisites

No external prerequisites — the fix uses only existing repo imports
(`agent.pipeline_ledger.PipelineLedger`) and the already-invoked `gh`/`sdlc-tool`
seams. Building and testing require the repo venv (already present).

## Solution

### Key Elements

- **`_resolve_tracked_issue(pr_number, repo_root)`** — new guarded helper in `tools/merge_predicate.py`. Reverse-looks-up the durable `PipelineLedger` by `pr_number`, scopes by `target_repo` when resolvable, and returns the single distinct SDLC-tracked `issue_number`, or `None` when it cannot resolve exactly one.
- **`_gh_repo_slug(repo_root)`** — small subprocess seam returning `owner/name` (`GH_REPO` env first, else `gh repo view --json nameWithOwner`; `None` on failure). Mirrors the existing subprocess-seam pattern so tests can monkeypatch it.
- **Preference wiring in `evaluate_merge_predicate`** — prefer the tracked issue for groups (b)/(c); fall back to the body-parsed issue when no tracked issue is resolvable.
- **Group (a) unchanged** — the PR body must still carry a `Closes/Fixes/Resolves #N` link; that presence check keeps using `_extract_issue_number(body)`.

### Flow

Merge command → predicate reads PR body + `pr_number` → reverse-lookup
`PipelineLedger` by `pr_number` (umbrella #2029) → run DOCS + REVIEW checks against
#2029 → PASS → merge allowed.

Fallback: no ledger for `pr_number` (or ambiguous, or import fails) → use first
`Closes #N` from body (today's behavior) → unchanged for single-issue PRs.

### Technical Approach

**Scope fence (load-bearing):** the build touches **`tools/merge_predicate.py` and
its test file ONLY**. It MUST NOT modify `agent/sdk_client.py`,
`agent/session_runner/`, `agent/sdlc_router.py`, `models/agent_session.py`, or
`tools/sdlc_stage_query.py` (other lanes / #2000 own those). `agent/pipeline_ledger.py`
is imported READ-ONLY (a `query.filter`, never a write) and is NOT modified. No new
`sdlc-tool` subcommand.

**Builder latitude:** this is a Small bug fix. The sole integration point is
`evaluate_merge_predicate` at roughly `tools/merge_predicate.py:429-469`. The
builder owns exact names, signatures, and control flow. What is mandatory: group
(a) still uses the raw `_extract_issue_number(body)` presence check; groups (b)/(c)
key on the ledger-resolved tracked issue when one resolves; the guarded import
never raises into the merge-guard hook; and the regression test is built on real,
production-shaped ledger records (below). Names like `_resolve_tracked_issue` and
`_gh_repo_slug` are illustrative.

1. **Add `_gh_repo_slug(repo_root: Path) -> str | None`** (subprocess seam):
   - Return `os.environ["GH_REPO"]` when set (already an `owner/name` slug, zero subprocess).
   - Else run `gh repo view --json nameWithOwner -q .nameWithOwner` with `cwd=repo_root`; return the stripped stdout, or `None` on any non-zero / empty / raise. Never raises.

2. **Add `_resolve_tracked_issue(pr_number: int, repo_root: Path) -> int | None`:**
   - If `pr_number` is falsy or `<= 0`, return `None`.
   - Lazily import `from agent.pipeline_ledger import PipelineLedger` inside a `try/except Exception → return None` (mirrors the existing lazy-import fallback at lines 241-247). Any import or query failure returns `None` — never raises. This keeps the module stdlib-only at import time; bare hook interpreters degrade gracefully to body-parse.
   - `rows = list(PipelineLedger.query.filter(pr_number=pr_number))` (indexed IntField lookup; proven live in Empirical Verification).
   - If `rows` is empty, return `None`.
   - Resolve `repo_slug = _gh_repo_slug(repo_root)`. If `repo_slug` is truthy, narrow `rows` to those whose `target_repo == repo_slug` **when that narrowing is non-empty** (cross-repo `pr_number` collision guard). If narrowing yields empty, keep the unscoped `rows` and rely on the ambiguity guard below (never invent a match).
   - Collect the set of distinct non-null `issue_number` values across the (possibly scoped) rows:
     - **Exactly one** distinct value → return it (the tracked umbrella issue).
     - **Zero** → return `None` (fall back to body parse).
     - **More than one** distinct value → return `None` (ambiguous → body fallback; refuse to guess). Defined, tested branch. (`pr_number` is 1:1 in production — Gate 4 — so this is a belt-and-suspenders guard, not a hot path. For a multi-issue umbrella PR the body fallback yields the first sub-issue with no substrate → false-FAIL → fail-closed direction, never a wrong allow.)

3. **Wire preference in `evaluate_merge_predicate` (lines ~442-462):**
   - Only when `substrate` is present, compute:
     ```
     tracked = _resolve_tracked_issue(pr_number, root)
     effective_issue = tracked if tracked is not None else issue_number
     ```
     (Skip the ledger import entirely in foreign repos, where `substrate` is False.)
   - The existing `elif issue_number is None:` guard becomes `elif effective_issue is None:` — groups (b)/(c) still skip with a note when neither source resolves.
   - In the `else` branch, before running the checks: if `tracked is not None and tracked != issue_number`, append a `note`:
     `"substrate checks keyed on SDLC-tracked issue #{tracked} (PipelineLedger pr_number reverse-lookup for PR #{pr_number}), not first Closes #{issue_number}"`.
   - Run `_check_docs_stage(effective_issue, head_ref, root, failed, notes)` and `_check_verdict_freshness(pr_number, effective_issue, root, failed, notes)`.
   - Group (a)'s "PR body lacks a Closes/Fixes/Resolves #N issue link" failure is untouched: it still triggers when `_extract_issue_number(body) is None`, independent of the reverse-lookup. `head_ref` still feeds `_check_docs_stage`'s `docs/features/{slug}.md` fallback unchanged.

4. **Single-issue invariance:** for a normal single-issue PR, the umbrella ledger's `issue_number` equals the body's `Closes #N`, so `tracked == issue_number` → identical checks. If the ledger lookup fails (no Redis / bare interpreter / no ledger yet), `tracked is None` → body issue used → identical to today. Both paths are covered by tests.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_resolve_tracked_issue` wraps the `PipelineLedger` import+query in `try/except Exception → return None`. Add a test that monkeypatches the import or `query.filter` to raise and asserts the helper returns `None` (not a raise) AND the predicate falls back to the body issue (observable: groups b/c run against the body issue).
- [ ] No `except Exception: pass` silent-swallow — the except returns `None`, a defined fallback signal; the substitution/fallback is surfaced via `notes`.

### Empty/Invalid Input Handling
- [ ] `_resolve_tracked_issue(0, root)`, `_resolve_tracked_issue(-1, root)`, and a `pr_number` with no matching ledger all return `None` — direct unit tests.
- [ ] `_gh_repo_slug` returning `None` (gh unavailable) still lets the pr_number-only lookup resolve when unambiguous — direct unit test.

### Ambiguity & Scoping Coverage
- [ ] **Ambiguity falls back safely:** two ledgers sharing the same `pr_number` with *distinct* `issue_number`s, `_gh_repo_slug → None` (so no scoping) → helper returns `None` → predicate keys groups (b)/(c) on the body issue (fail-closed for the umbrella case). Direct unit test.
- [ ] **`target_repo` scoping resolves an otherwise-ambiguous pair:** two same-`pr_number` ledgers under different `target_repo`s, `_gh_repo_slug` returns one of them → helper returns that repo's issue, not `None`. Direct unit test.

### Error State Rendering
- [ ] When groups (b)/(c) fail on the *tracked* issue (genuinely un-approved), the failed-check messages still name the leg. Assert a failing tracked-issue case produces the same named failures as today, not a swallowed pass.

## Test Impact

- [ ] `tests/unit/test_validate_merge_guard.py` — no change required. It exercises the hook wrapper and monkeypatches `_evaluate_predicate` wholesale; it does not call `_extract_issue_number` or `_resolve_tracked_issue` directly. Verified by reading the file (tests stub the predicate).
- [ ] `tests/unit/test_merge_predicate.py` — CREATE. No dedicated unit test for `merge_predicate.py` exists today; this is the new home for the regression test and the helper's unit coverage.

No existing test asserts the first-match behavior as correct, so nothing needs
UPDATE/DELETE — the change is additive plus one new test file. (Justification
exceeds 50 chars: the only merge-predicate-adjacent test file stubs the predicate
entirely and never touches the resolution helpers.)

## Rabbit Holes

- **Backfilling `slug` at `create_local` in `sdlc_session_ensure.py`.** Explicitly fenced OUT by critique. The `AgentSession.slug ↔ issue_number` pairing does not exist in production and this plan does NOT try to create it. The `PipelineLedger.pr_number` reverse-lookup sidesteps the whole slug/issue mismatch.
- **Adding a `sdlc-tool pr-to-issue` subcommand.** Tempting for the bare-interpreter hook path, but it touches `tools/sdlc_stage_query.py` — out of scope, #2000-adjacent. The in-process guarded import fully covers the reported `/do-merge` subprocess path.
- **Reworking `PipelineLedger` to add a slug field or a reverse index.** Out of scope; `filter(pr_number=...)` already resolves the umbrella issue (proven live). No model change.
- **Disambiguating multiple umbrella issues per `pr_number` via recency/status heuristics.** `pr_number` is 1:1 across the ledger (Gate 4); the ambiguous branch conservatively falls back to body-parse. A ranking heuristic is gold-plating.
- **Changing `_extract_issue_number` to return all matches.** Not needed — group (a) only needs presence; groups (b)/(c) get the tracked issue from the ledger.

## Risks

### Risk 1: Bare hook-interpreter cannot import `PipelineLedger`
**Impact:** In the merge-guard hook path, if the interpreter lacks repo deps (popoto/Redis), `_resolve_tracked_issue` returns `None` and the predicate falls back to the first `Closes #N` — the multi-issue false-fail persists in that path.
**Mitigation:** By design ("fall back to body-parsing only when no tracked issue is resolvable"). The reported break is the `/do-merge` skill, which runs `python -m tools.merge_predicate` as a subprocess under the full repo venv where the import always succeeds — fully fixed. The hook path's fail-closed posture is unchanged (a false-fail blocks, never wrongly allows). Documented as a known limitation.

### Risk 2: Cross-repo `pr_number` collision resolves to the wrong issue
**Impact:** If a foreign repo's ledger shares a `pr_number` with the target repo's, an unscoped lookup could key on the wrong issue.
**Mitigation:** The helper scopes matching rows by `target_repo` (resolved via `_gh_repo_slug`/`GH_REPO`) when resolvable; multiple distinct issue numbers after scoping → `None` → body fallback, never guessing. In practice `pr_number` is 1:1 and only one `target_repo` exists (Gate 4). Covered by scoping + ambiguity tests.

### Risk 3: Tracked issue differs from body issue for a legitimate single-issue PR
**Impact:** A spurious substitution could change which issue's state gates the merge.
**Mitigation:** For single-issue PRs the umbrella ledger's `issue_number` equals the body `Closes #N`, so `tracked == body` — no behavior change. Regression test asserts single-issue invariance both with and without a resolvable ledger.

### Risk 4: `PipelineLedger.pr_number` unset at merge time
**Impact:** If the ledger's `pr_number` were not written before merge, the lookup would find nothing and fall back to body-parse (re-exposing the umbrella false-fail).
**Mitigation:** `sdlc-tool meta-set --key pr_number` writes the field at `/do-build` PR-creation time — long before merge (verified: the #2029 ledger already carries `pr_number=2033`). The fallback is fail-closed, never a wrong allow. No new writer needed (this plan only reads).

## Race Conditions

No race conditions identified — the predicate is a synchronous, read-only
evaluation (subprocess `gh`/`sdlc-tool` calls and one indexed Redis read). It
mutates no shared state; concurrent evaluations of different PRs are independent.

## No-Gos (Out of Scope)

- [FENCED-OUT by critique] Backfilling `slug` onto sessions at `create_local` in `tools/sdlc_session_ensure.py`. The `AgentSession.slug ↔ issue_number` pairing is not built or repaired here; the reverse-lookup avoids it entirely.
- [SEPARATE-SLUG #2034] The bare hook-interpreter subprocess-resolver hardening (Risk 1) is deliberately not built here; the in-process guarded import covers the reported `/do-merge` path.
- Modifying `agent/sdk_client.py`, `agent/session_runner/`, `agent/sdlc_router.py`, `models/agent_session.py`, or `tools/sdlc_stage_query.py` — owned by other lanes (#2000 building concurrently). This plan is fenced to `tools/merge_predicate.py` + its test; `agent/pipeline_ledger.py` is read-only.

## Update System

No update system changes required — this is a purely internal fix to one tool
module. No new dependencies, no config files, no `scripts/update/run.py` or
`migrations.py` changes. No Popoto schema change (the fix only *reads* the
existing `PipelineLedger.pr_number`/`issue_number` fields).

## Agent Integration

No agent integration required — `tools/merge_predicate.py` is already reachable via
its CLI entry (`python -m tools.merge_predicate`) used by the `/do-merge` skill,
and via in-process import by the merge-guard hook. The fix changes internal
resolution logic behind those existing surfaces; no new MCP tool, `.mcp.json`, or
bridge wiring.

## Documentation

### Feature Documentation
- [ ] Update the merge-predicate section of `docs/sdlc/do-merge.md` (default target) to note that groups (b)/(c) key on the SDLC-tracked issue resolved from the durable `PipelineLedger` by PR number (with first-`Closes` fallback), so multi-issue-closure PRs under an umbrella tracking issue pass without override. If `docs/features/machine-readable-dod.md` already describes the groups, update it there instead. Confirm the exact target during build.

### Inline Documentation
- [ ] Docstring on `_resolve_tracked_issue` explaining the `pr_number → PipelineLedger → issue_number` reverse-lookup, the `target_repo` scoping, the ambiguity fallback, and the guarded import.
- [ ] Update the module-level docstring's group (b)/(c) description to state they key on the tracked issue resolved from the ledger.

## Success Criteria

- [ ] `_resolve_tracked_issue(2033, root)` returns `2029` against a production-shaped ledger (umbrella carries `pr_number`, sub-issues do not); returns `None` for no-match, non-positive `pr_number`, ambiguous multi-issue, and import-failure cases.
- [ ] `evaluate_merge_predicate` keys groups (b)/(c) on the tracked issue when resolvable, else the body `Closes #N`; group (a) body-link presence check unchanged.
- [ ] Regression test reproduces the PR #2033 shape with **real `PipelineLedger` records** (umbrella #2029 with `pr_number=2033`; body `Closes #1871/#1267/#1760`) and asserts DOCS/REVIEW checks query **#2029, not #1871** — such that a no-op fix (helper returning `None`) FAILS the test.
- [ ] Single-issue-PR invariance test passes (tracked == body, and ledger-absent fallback == body).
- [ ] Change is confined to `tools/merge_predicate.py` + `tests/unit/test_merge_predicate.py` (git diff touches no other source files).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (merge-predicate)**
  - Name: predicate-builder
  - Role: Implement `_gh_repo_slug` + `_resolve_tracked_issue` (PipelineLedger reverse-lookup), wire the preference in `evaluate_merge_predicate`, update docstrings, add `tests/unit/test_merge_predicate.py`.
  - Agent Type: builder
  - Resume: true

- **Validator (merge-predicate)**
  - Name: predicate-validator
  - Role: Verify scope fence (diff touches only the two files), regression + invariance tests pass AND the no-op-fails property holds, no raw-Redis, black-clean.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement reverse-lookup resolution + wiring
- **Task ID**: build-predicate
- **Depends On**: none
- **Validates**: tests/unit/test_merge_predicate.py (create)
- **Assigned To**: predicate-builder
- **Agent Type**: builder
- **Parallel**: false
- **Domain**: redis-popoto — read-only `PipelineLedger.query.filter(pr_number=...)`; never raw Redis; wrap import+query in `try/except Exception → return None`.
- Add `_gh_repo_slug(repo_root)` (GH_REPO env first, else `gh repo view`; never raises).
- Add `_resolve_tracked_issue(pr_number, repo_root)` per Technical Approach (guarded lazy import of `PipelineLedger`, `target_repo` scoping, distinct-issue-set logic, ambiguity → None).
- Wire `effective_issue` preference into `evaluate_merge_predicate` (only when substrate present); update the `elif` guard to `effective_issue`; add the substitution `note`.
- Update the module docstring (groups b/c key on the ledger-resolved tracked issue) and add the helper docstrings.
- Work in a dedicated slug worktree at `.worktrees/merge-predicate-tracked-issue-resolution/` on branch `session/merge-predicate-tracked-issue-resolution` — NOT the shared main checkout.
- Do NOT edit any file outside `tools/merge_predicate.py` and the new test. Do NOT modify `agent/pipeline_ledger.py` (import it read-only).

### 2. Write regression + unit tests (production-shaped)
- **Task ID**: build-tests
- **Depends On**: build-predicate
- **Assigned To**: predicate-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_merge_predicate.py`. Use REAL `PipelineLedger` records (the pattern in `tests/unit/test_pipeline_ledger.py`: `get_or_create(target_repo, issue)`, set `pr_number`, `save()`, with a `_cleanup` helper deleting by `ledger_key` in setup/teardown). The autouse `redis_test_db` fixture isolates these to the per-worker test DB.
  - **Multi-issue-closure regression (the anti-no-op test):** create the umbrella ledger `get_or_create(<test_repo>, 2029)` with `pr_number=2033` and `save()`; do NOT set `pr_number` on any `1871/1267/1760` ledger (mirrors production, Gate 3). PR body monkeypatched to `Closes #1871\nCloses #1267\nCloses #1760`. Monkeypatch `_gh_repo_slug` to return `<test_repo>` and `_run_stage_query`/`_run_verdict_get` to record the issue arg they receive. Assert both are called with **2029**. Add an explicit assertion that a no-op (monkeypatch `_resolve_tracked_issue` to return `None`) instead routes the checks to **1871** — proving the test distinguishes fixed from unfixed.
  - **Single-issue invariance:** umbrella ledger `get_or_create(<test_repo>, 42)` with `pr_number=<PR>`; body `Closes #42` → checks keyed on 42; and ledger-absent (delete it first) → falls back to 42.
  - **`_resolve_tracked_issue` unit cases:** `pr_number<=0` → None; no matching ledger → None; ambiguous (two ledgers with the same `pr_number`, distinct `issue_number`, `_gh_repo_slug` → None so no scoping) → None; `target_repo` scoping resolves an otherwise-ambiguous pair → that repo's issue; happy path → the issue number.
  - **Guarded-import failure:** monkeypatch `PipelineLedger.query.filter` (or the import) to raise → helper returns `None`, predicate falls back to body issue.
  - **Group (a) unchanged:** empty body → "PR body lacks a Closes/Fixes/Resolves #N issue link".

### 3. Validation
- **Task ID**: validate-predicate
- **Depends On**: build-tests
- **Assigned To**: predicate-validator
- **Agent Type**: validator
- **Parallel**: false
- `git diff --name-only main` shows ONLY `tools/merge_predicate.py` and `tests/unit/test_merge_predicate.py`.
- `pytest tests/unit/test_merge_predicate.py -q` passes; `pytest tests/unit/test_validate_merge_guard.py -q` still passes.
- Confirm the no-op-fails property: temporarily stub `_resolve_tracked_issue → None` and confirm the regression test FAILS (then revert the stub).
- `black`-format clean on both files (black only, no ruff-lint gate per repo rule).
- No raw Redis ops introduced.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-predicate
- **Assigned To**: predicate-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update the merge-predicate resolution-order note in the doc identified during build (`docs/sdlc/do-merge.md` default, or `docs/features/machine-readable-dod.md`).

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
| Format clean | `python -m black --check tools/merge_predicate.py tests/unit/test_merge_predicate.py` | exit code 0 |
| Scope fence (only two files changed) | `git diff --name-only main -- . ':!docs' \| grep -v -E '^(tools/merge_predicate\.py\|tests/unit/test_merge_predicate\.py)$' \| wc -l \| tr -d ' '` | output is 0 |
| No out-of-scope source edits | `git diff --name-only main \| grep -E '^(agent/sdk_client\.py\|agent/session_runner/\|agent/sdlc_router\.py\|models/agent_session\.py\|tools/sdlc_stage_query\.py\|agent/pipeline_ledger\.py)'` | exit code 1 |
| Reverse-lookup helper present | `grep -c '_resolve_tracked_issue' tools/merge_predicate.py` | output > 1 |
| Reads PipelineLedger by pr_number | `grep -c 'PipelineLedger.query.filter(pr_number=' tools/merge_predicate.py` | output >= 1 |
| No raw Redis introduced | `grep -nE '\.(delete\|srem\|sadd\|zrem)\(' tools/merge_predicate.py` | exit code 1 |

## Critique Results

<!-- Round 1 (2026-07-11T19:07Z) recorded verdict: NEEDS REVISION, 2 blockers. Both resolved in this revision. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | round-1 | Mechanism empirically inert: `filter(slug=slug) → issue_number` returns 0 rows because `slug` and `issue_number` are set by disjoint creation paths; on the exact repro the lookup resolves nothing. | Replaced with `PipelineLedger.query.filter(pr_number=<PR>) → issue_number` | Verified live: slug lookup → 0 rows (Gate 1); ledger reverse-lookup → #2029 (Gate 2). |
| BLOCKER | round-1 | Regression test monkeypatched a zero-production shape (slug+issue on one AgentSession), so it passed while the bug shipped. | Regression test rebuilt on real, production-shaped `PipelineLedger` records + explicit no-op-fails assertion | Umbrella carries `pr_number`, sub-issues do not (Gate 3); a no-op helper routes checks to #1871 and the test FAILS (task 2). |
