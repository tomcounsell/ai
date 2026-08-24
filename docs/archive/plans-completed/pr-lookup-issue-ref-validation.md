---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-07-10
tracking: https://github.com/tomcounsell/ai/issues/1987
last_comment_id:
---

# sdlc-tool: validate PR body issue-reference before trusting fuzzy search match

## Problem

When the SDLC pipeline resolves the open PR for an issue, `tools/sdlc_stage_query.py::_lookup_pr_number` uses GitHub's fuzzy text search (`gh pr list --search "#{issue_number}"`) and trusts the first result unconditionally. Fuzzy search tokenizes `#N` and can return a completely unrelated PR whose title/body merely happens to contain those digits — it does NOT require a literal `Closes #N` / `Fixes #N` reference.

**Current behavior:**
Running `/do-sdlc` for issue #1950, `_lookup_pr_number(1950)` returned PR #1984 — a "Concurrent full-suite pytest coordination" PR whose body says `Closes #1967`, with no reference to #1950 anywhere. `sdlc-tool next-skill --issue-number 1950` then routes to `/do-pr-review` against the wrong PR. Any issue whose digits fuzzy-match another open PR's text is at risk. This is a latent hazard for every in-flight SDLC pipeline once open PRs exist.

**Desired outcome:**
The primary `--search` path only returns a PR whose body contains a literal word-boundary closing-keyword reference (`Closes #N` / `Fixes #N` / `Resolves #N`, incl. the `-d`/`-es` inflections GitHub honors) to the exact issue number. When the search result fails that check, the function falls through to the existing branch-head fallback (`gh pr list --head session/{slug}`) and ultimately `None` — never a false positive.

## Freshness Check

**Baseline commit:** 12ff552d7bf0c9dc2e23fc2a4f6b96294157d0a7
**Issue filed at:** 2026-07-09T12:33:00Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/sdlc_stage_query.py:277` — issue claimed `_gh_pr_list(["--search", f"#{issue_number}", "--state", "open"], repo=repo)` returns the first PR unvalidated — **still holds** (lines 276-279).
- `tools/sdlc_stage_query.py:222-257` — `_gh_pr_list` requests only `--json number`; the PR body is not fetched — **still holds**. The fix must fetch the body.
- `tools/sdlc_stage_query.py:282` — branch-head fallback `--head session/{slug}` (exact head-ref match, trustworthy) — **still holds**.

**Cited sibling issues/PRs re-checked:**
- #1950 (blocked pipeline) — still the motivating case; plan `docs/plans/impact-finder-rerank-fallback.md` exists, no branch/PR yet.
- PR #1984 — the observed false match (`Closes #1967`), unrelated to #1950.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=2026-07-09T12:33:00Z -- tools/sdlc_stage_query.py tests/unit/test_sdlc_stage_query.py` is empty.

**Active plans in `docs/plans/` overlapping this area:** None touching `_lookup_pr_number`.

**Notes:** `re` is already imported in `tools/sdlc_stage_query.py` (line 43). Precedent for the closing-keyword approach exists at `scripts/build_audit_set.py:126` (`--search 'Closes #{issue_number}'`) — but note that tighter search string is still fuzzy text matching, so the robust fix is a word-boundary regex on the returned body, not merely a tighter search argument.

## Prior Art

No prior issues or merged PRs found related to this work (`gh issue list --state closed` and `gh pr list --state merged` searches for "pr lookup fuzzy search" / "lookup_pr_number branch-head fallback" returned nothing). The two-tier resolution ladder (issue-search primary + branch-head fallback) was introduced under design item "D4" per the function docstring, but no prior fix addressed the fuzzy-match false-positive.

## Research

No relevant external findings — this is an internal fix to how `gh pr list` output is validated. GitHub's closing-keyword set (close/closes/closed, fix/fixes/fixed, resolve/resolves/resolved) is well-established and documented; no library or ecosystem research required.

## Data Flow

1. **Entry point**: `sdlc-tool stage-query --issue-number N` (CLI) or an internal `_compute_meta` call.
2. **`_compute_meta` (line 325)**: resolves the target repo once, resolves the slug, then calls `_lookup_pr_number(issue_number, slug=slug, repo=resolved_repo)` (line 360).
3. **`_lookup_pr_number` (line 260)**: primary path `gh pr list --search "#{N}"` → **[BUG: trusts first result]** → returns `pr_number`.
4. **Output**: `pr_number` lands in the `_meta` payload; `sdlc-tool next-skill` routes the pipeline (review/patch/merge) against it. A false `pr_number` here misroutes the entire downstream pipeline.

The fix inserts a body-validation gate at step 3, between the search return and the trust.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the issue)
- Review rounds: 1 (correctness-sensitive regex + fallthrough logic)

## Prerequisites

No prerequisites — this work has no external dependencies. `gh` is already available and used by the touched module; `re` is already imported.

## Solution

### Key Elements

- **Body-fetching search helper**: a new function that runs `gh pr list --search "#{N}" --state open --json number,body` and returns candidate `(number, body)` pairs instead of just the first number.
- **Issue-reference validator**: a small predicate that returns True only when a PR body contains a word-boundary closing-keyword reference (`close[sd]?` / `fix(e[sd])?` / `resolve[sd]?`, case-insensitive) immediately followed by `#{exact issue number}` with no trailing digits.
- **Guarded primary path**: `_lookup_pr_number` iterates the search candidates, returns the first whose body validates, and otherwise falls through to the unchanged branch-head fallback.

### Flow

`_lookup_pr_number(N, slug)` → search `#N` (fetch number+body) → for each candidate, does body reference `Closes/Fixes/Resolves #N`? → **yes**: return that PR number → **no candidate validates**: try `--head session/{slug}` → still none: return `None`.

### Technical Approach

- Add a module-level predicate `_body_references_issue(body: str | None, issue_number: int) -> bool` that compiles a regex of the form `\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+#{issue_number}(?!\d)` with `re.IGNORECASE`. The trailing `(?!\d)` negative lookahead is the word boundary that prevents `#195` from matching a body that says `Closes #1950`. Empty/None body → False.
- Add a search helper `_gh_pr_search_issue_ref(issue_number, repo)` that runs `gh pr list --search "#{issue_number}" --state open --json number,body`, iterates the returned list in order, and returns the first PR `number` whose `body` passes `_body_references_issue`. Returns `None` on any failure or when no candidate validates. Never raises (mirror `_gh_pr_list`'s try/except + `logger.debug` discipline).
- Rewrite `_lookup_pr_number`'s primary branch to call `_gh_pr_search_issue_ref` instead of `_gh_pr_list(["--search", ...])`. Leave the branch-head fallback (`_gh_pr_list(["--head", f"session/{slug}", ...])`) untouched — an exact head-ref match needs no body validation.
- Keep `_gh_pr_list` as-is; it is still used by the branch-head fallback and elsewhere.
- Update the `_lookup_pr_number` docstring (resolution order note) to state that the primary path now requires a validated body reference.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `_gh_pr_search_issue_ref` wraps its `subprocess.run` + `json.loads` in try/except with `logger.debug` (mirroring `_gh_pr_list`). Add a test asserting a subprocess `OSError` returns `None` (never propagates), matching the existing `test_gh_failure_returns_none` pattern.
- [ ] `_body_references_issue` has no exception handlers (pure regex over a string) — state "No exception handlers in scope" for that function.

### Empty/Invalid Input Handling
- [ ] `_body_references_issue(None, N)` and `_body_references_issue("", N)` return False — add explicit tests.
- [ ] Search returning an empty list, a non-list, or candidates with missing/None `body` fields all resolve to `None` — add a test.
- [ ] No agent-output-loop concern — this path produces a single value, not a stream.

### Error State Rendering
- [ ] No user-visible rendering here. The failure mode is "return None → pipeline treats issue as having no PR yet," which is the correct fail-safe (the same behavior the `/do-sdlc` run for #1950 exhibited). Add a test asserting a fuzzy-only match (body references a *different* issue) yields `None` when no slug fallback is available.

## Test Impact

- [ ] `tests/unit/test_sdlc_stage_query.py::TestLookupPrNumber::test_issue_search_primary_path` — REPLACE: it currently patches `_gh_pr_list` to return 55 and expects the raw hit back. After the fix the primary path calls `_gh_pr_search_issue_ref` (not `_gh_pr_list`), so rewrite it to patch the new helper and assert a *validated* hit is returned.
- [ ] `tests/unit/test_sdlc_stage_query.py::TestLookupPrNumber::test_branch_head_fallback_when_issue_search_empty` — UPDATE: the primary call is now `_gh_pr_search_issue_ref` (returns None) and only the branch-head path uses `_gh_pr_list`. Patch the two helpers separately and assert the `--head` call still yields 88.
- [ ] `tests/unit/test_sdlc_stage_query.py::TestLookupPrNumber::test_no_slug_no_branch_fallback` — UPDATE: patch `_gh_pr_search_issue_ref` to return None; assert no branch-head attempt when slug is None.
- [ ] `tests/unit/test_sdlc_stage_query.py::TestLookupPrNumber::test_gh_failure_returns_none` — no change (exercises `_gh_pr_list` with `--head` directly, still valid).

New tests to ADD in `TestLookupPrNumber` (or a sibling class):
- [ ] Regression for #1987: search returns a candidate whose body says `Closes #1967` for `issue_number=1950`, no slug → result is `None` (the exact false-match scenario).
- [ ] Search returns a candidate whose body says `Closes #1950` → returns that PR number.
- [ ] Search returns two candidates; only the second body references the issue → returns the second.
- [ ] Word-boundary: `_body_references_issue("Closes #1950", 195)` is False.
- [ ] `_body_references_issue` returns True for `Fixes #N`, `Resolves #N`, lowercase, and `Closed: #N`; False for a bare `#N` mention with no keyword, empty string, and None.

## Rabbit Holes

- **Querying GitHub's closing-issue-references API / PR timeline** instead of regex-matching the body. Heavier, adds API calls and latency, and the issue explicitly asks for the body-regex approach that already has precedent in this repo. Stay with the regex.
- **Cross-repo `owner/repo#N` reference forms** in PR bodies. Out of scope — the regex matches `#N`; cross-repo references are rare here and the branch-head fallback already recovers PRs whose body never referenced the issue. Do not try to thread the resolved repo slug into the regex.
- **Generalizing the closing-keyword regex into a shared util** consumed by `scripts/build_audit_set.py` and others. Tempting DRY, but out of scope for a targeted bug fix; keep the predicate local to `sdlc_stage_query.py`.
- **Reworking `_gh_pr_list`'s signature to always return bodies.** It is used by the branch-head path (which needs no body) and its single-number contract is relied on elsewhere. Add a new helper instead of mutating the existing one.

## Risks

### Risk 1: A legitimate PR references the issue with a non-standard keyword or phrasing
**Impact:** The primary path returns None and the pipeline relies on the branch-head fallback (or reports no PR), potentially a false negative.
**Mitigation:** The branch-head fallback (`session/{slug}`) already recovers PRs whose body never references the issue — that is its documented purpose. The regex covers all nine GitHub-honored closing keywords, which is what GitHub itself uses to auto-close. Residual risk is limited to PRs that neither use a closing keyword nor a canonical branch name, which the pre-fix code also could not reliably resolve.

### Risk 2: Regex over-matches (e.g. `#1950` matching `#19501`)
**Impact:** A different issue's PR could be falsely trusted.
**Mitigation:** The trailing `(?!\d)` negative lookahead enforces an exact numeric boundary; a dedicated test asserts `#195` does not match `#1950`.

## Race Conditions

No race conditions identified — `_lookup_pr_number` and its helpers are synchronous, single-threaded, and stateless; each invocation runs `gh` subprocesses sequentially and returns a value with no shared mutable state.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. (Cross-repo `owner/repo#N` reference parsing and shared-util extraction are documented under Rabbit Holes as deliberate non-goals, not deferred work items.)

## Update System

No update system changes required — this is a pure code fix inside an existing module (`tools/sdlc_stage_query.py`). No new dependencies, config files, or migrations; nothing to propagate through `scripts/update/`.

## Agent Integration

No new agent integration required — `_lookup_pr_number` is an internal helper reached through the already-wired `sdlc-tool stage-query` / `next-skill` CLI surface (`tools/sdlc_stage_query.py`). The fix changes behavior of an existing path; no new CLI entry point, MCP surface, or `.mcp.json` change. Existing unit tests exercise the code path directly.

## Documentation

No feature documentation changes needed — this is a bug fix to an internal resolution helper with no user-facing surface or new capability. The behavior change (validated PR-reference matching) is documented inline via the updated `_lookup_pr_number` docstring and covered by tests.

### Inline Documentation
- [ ] Update the `_lookup_pr_number` docstring to state the primary path requires a validated closing-keyword body reference.
- [ ] Docstring on the new `_gh_pr_search_issue_ref` and `_body_references_issue` helpers explaining the validation contract.

## Success Criteria

- [ ] `_lookup_pr_number(1950)` returns `None` (not a false match) when the only fuzzy search hit is a PR whose body references a different issue.
- [ ] `_lookup_pr_number` returns the correct PR when a search hit's body contains `Closes/Fixes/Resolves #{issue_number}`.
- [ ] Word-boundary matching prevents `#195` from matching `#1950`.
- [ ] Branch-head fallback behavior is unchanged.
- [ ] New and updated tests pass (`pytest tests/unit/test_sdlc_stage_query.py`).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (inline docstrings only; `/do-docs` no-op).

## Team Orchestration

### Team Members

- **Builder (pr-lookup)**
  - Name: pr-lookup-builder
  - Role: Add the body-fetching search helper + issue-reference validator, guard `_lookup_pr_number`'s primary path, update/add tests.
  - Agent Type: builder
  - Domain: untrusted-input (regex correctness / boundary matching)
  - Resume: true

- **Validator (pr-lookup)**
  - Name: pr-lookup-validator
  - Role: Verify the false-match regression is fixed, boundary tests pass, branch-head fallback unchanged, lint/format clean.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement validation + guard the primary path
- **Task ID**: build-pr-lookup
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_stage_query.py
- **Assigned To**: pr-lookup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_body_references_issue(body, issue_number)` predicate (word-boundary closing-keyword regex, `re.IGNORECASE`, `(?!\d)` numeric boundary, None/empty → False).
- Add `_gh_pr_search_issue_ref(issue_number, repo)` that fetches `--json number,body`, iterates candidates, returns the first whose body validates; never raises (try/except + `logger.debug`).
- Rewrite `_lookup_pr_number`'s primary branch to call `_gh_pr_search_issue_ref`; leave the branch-head fallback intact.
- Update docstrings on `_lookup_pr_number` and the two new helpers.

### 2. Update and add tests
- **Task ID**: build-pr-lookup-tests
- **Depends On**: build-pr-lookup
- **Validates**: tests/unit/test_sdlc_stage_query.py
- **Assigned To**: pr-lookup-builder
- **Agent Type**: builder
- **Parallel**: false
- REPLACE `test_issue_search_primary_path`; UPDATE `test_branch_head_fallback_when_issue_search_empty` and `test_no_slug_no_branch_fallback` to patch the new helper.
- ADD the #1987 regression test, the positive-match test, multi-candidate test, word-boundary test, and `_body_references_issue` unit tests (per Test Impact).

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-pr-lookup-tests
- **Assigned To**: pr-lookup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sdlc_stage_query.py -q`, `python -m ruff check .`, `python -m ruff format --check .`.
- Confirm all Success Criteria and Verification rows pass.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lookup tests pass | `pytest tests/unit/test_sdlc_stage_query.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/sdlc_stage_query.py tests/unit/test_sdlc_stage_query.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/sdlc_stage_query.py tests/unit/test_sdlc_stage_query.py` | exit code 0 |
| Validator helper present | `grep -c "_body_references_issue" tools/sdlc_stage_query.py` | output > 1 |
| Primary path no longer trusts raw search | `grep -c "_gh_pr_list(\[\"--search\"" tools/sdlc_stage_query.py` | match count == 0 |
| Regression test present | `grep -c "1967" tests/unit/test_sdlc_stage_query.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — the issue fully specifies the desired behavior (validate `Closes/Fixes #N` body references, fall through to branch-head fallback otherwise) and the suggested fix. Proceeding on that premise.
