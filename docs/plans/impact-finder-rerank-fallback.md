---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-08
tracking: https://github.com/tomcounsell/ai/issues/1950
last_comment_id:
---

# impact_finder: distinguish "rerank endpoint down" from "nothing scored"

## Problem

`find_affected()` in `tools/impact_finder_core.py` is the shared two-stage
pipeline behind both `find_affected_docs` (doc-impact finder) and its code
counterpart. When the Haiku rerank endpoint is unreachable (e.g.
`ANTHROPIC_BASE_URL` misconfigured, pointing at a host that 404s on the Haiku
model id), the function silently returns `[]` even though Stage 1 embedding
recall found strong candidates. A caller sees "zero affected docs" and trusts
it — there is no way to tell "reranker is down" from "genuinely nothing is
affected."

**Current behavior:** With a broken Haiku endpoint, every per-candidate rerank
call raises inside `_rerank_single_candidate`, which catches the exception
broadly (`except Exception`, line 362) and returns `None`. `_rerank_candidates`
filters out all `None` results and returns `[]`. `find_affected` then calls
`result_builder([])` unconditionally, returning `[]` with no fallback — despite
`fallback_builder` existing and already working for the "Anthropic client
construction failed" case (lines 487-494).

**Desired outcome:** When every rerank request in a batch fails due to a
transport/API error (not "scored below threshold"), `find_affected()` calls
`fallback_builder(candidates)` — the same embedding-only path used when client
construction fails — and logs a warning naming the likely cause. A legitimate
"rerank ran, nothing scored >= 5" must still return `[]` (no false-positive
fallback dump).

## Freshness Check

**Baseline commit:** `37d4cc74f5ae0727f67a9a6b9093739eabf67775`
**Issue filed at:** 2026-07-08T06:43:04Z
**Disposition:** Unchanged (issue filed same day as this plan; no commits since)

**File:line references re-verified:**
- `tools/impact_finder_core.py:413` (`find_affected` def) — confirmed, matches.
- `tools/impact_finder_core.py:371` (`_rerank_candidates` def) — confirmed, matches.
- `tools/impact_finder_core.py:325-368` (`_rerank_single_candidate`) — confirmed;
  broad `except Exception` at line 362 logs via `logger.exception` and returns
  `None`, indistinguishable from a below-threshold score.
- `tools/impact_finder_core.py:487-494` (client-construction fallback) — confirmed,
  the only extant call site of `fallback_builder`.
- `tools/impact_finder_core.py:28` (`MIN_SIMILARITY_THRESHOLD = 0.3`) — confirmed.
- `tests/unit/test_doc_impact_finder.py:356` — confirmed; already patches
  `tools.impact_finder_core._rerank_single_candidate` directly (see
  `TestFullPipelineIntegration.test_end_to_end_with_mocked_apis`), and a second
  test (`test_end_to_end_embedding_only_fallback`, line 373) patches
  `builtins.__import__` to simulate Anthropic-client-construction failure — the
  new "all-requests-failed" case is a third, currently-missing variant along
  this same axis.

**Cited sibling issues/PRs re-checked:**
- PR #1948 (issue #1835) — merged prior to this issue being filed; this issue
  was discovered during its DOCS-stage review. No re-check needed; it is the
  originating context, not a blocker.

**Commits on main since issue was filed (touching referenced files):** none
(`git log --since=<createdAt> -- tools/impact_finder_core.py tests/unit/test_doc_impact_finder.py tests/unit/test_code_impact_finder.py` returned empty).

**Active plans in `docs/plans/` overlapping this area:** none found.

**Notes:** No drift. Root cause and line numbers verified verbatim against
current main; no prior fix exists for this specific bug.

## Prior Art

No prior issues or PRs found addressing this specific fallback gap. `gh issue
list --state closed --search "impact finder rerank fallback"` returned one
unrelated result (#1247, docs-hygiene consolidation). `gh pr list --state
merged --search "impact finder fallback"` returned PR #1885 (embedding
truncation fix, unrelated) and PR #1763 (SDLC cross-repo plan resolution,
unrelated). This is the first fix attempt for this bug.

## Research

No relevant external findings — this is an internal control-flow fix with no
external library or API surface change. `anthropic` client usage and error
shapes are already understood from the existing codebase.

## Data Flow

1. **Entry point**: `find_affected_docs(change_summary, ...)` (doc-impact
   finder) or the code-impact finder's equivalent wrapper calls
   `find_affected()` in `tools/impact_finder_core.py`.
2. **Stage 1 (embedding recall)**: `find_affected` embeds `change_summary`,
   computes cosine similarity against the loaded index's chunks, and takes the
   top `top_n` candidates (lines 465-484).
3. **Stage 2 (Haiku rerank)**: `_rerank_candidates` (line 371) fans out one
   Haiku call per candidate via `ThreadPoolExecutor(max_workers=5)`, collecting
   `(score, reason, chunk)` tuples for scores >= 5 and dropping everything else
   (including failures) as `None`.
4. **Result assembly**: `find_affected` calls `result_builder(results)` (line
   498) unconditionally on whatever `_rerank_candidates` returned — no branch
   distinguishes "5 failures" from "5 legitimate low scores."
5. **Fix point**: after Stage 2 returns, count how many candidates produced a
   hard failure (transport/API error) vs. a legitimate sub-threshold score. If
   *all* candidates failed, route to `fallback_builder(candidates)` instead of
   `result_builder(results)`.
6. **Output**: `AffectedDoc` (or the code-impact finder's equivalent model)
   list, either from `result_builder` (Haiku-scored) or `fallback_builder`
   (embedding-only, similarity >= `MIN_SIMILARITY_THRESHOLD`).

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `_rerank_single_candidate`'s return contract gains a
  third outcome (hard failure) in addition to `None` (below threshold) and a
  scored tuple. The chosen mechanism (sentinel vs. re-raise-and-count, decided
  below) changes `_rerank_candidates`'s internal aggregation logic but not its
  public return type (`list[tuple[float, str, dict]]`).
- **Coupling**: `find_affected` becomes coupled to a new signal ("how many
  candidates hard-failed") coming out of `_rerank_candidates`. This is a small,
  local increase in coupling between two functions already in the same module.
- **Data ownership**: unchanged — no new state, no new storage.
- **Reversibility**: fully reversible; this is a pure control-flow change with
  no persisted state or schema impact.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a small, well-scoped control-flow fix confined to one module plus its
test file. No cross-team coordination needed.

## Prerequisites

No prerequisites — this work has no external dependencies. It runs against the
existing mocked-API test patterns already present in `tests/unit/test_doc_impact_finder.py`.

## Solution

### Key Elements

- **Failure-vs-threshold distinction in `_rerank_single_candidate`**: the
  transport/API-error path (currently swallowed by the broad `except
  Exception:` at line 362) must produce a signal distinguishable from a
  legitimate "scored below 5" result.
- **Aggregation in `_rerank_candidates`**: count hard failures alongside
  successes; expose whether *every* candidate hard-failed (as opposed to a
  partial failure, where legitimate scored results should still be trusted).
- **Fallback routing in `find_affected`**: after Stage 2, if every candidate
  hard-failed, log a warning naming the likely cause and call
  `fallback_builder(candidates)`; otherwise proceed to
  `result_builder(results)` as today.

### Flow

Caller invokes `find_affected_docs(...)` → Stage 1 embedding recall finds
candidates → Stage 2 Haiku rerank: every request 404s (broken
`ANTHROPIC_BASE_URL`) → **new**: `find_affected` detects "all candidates
hard-failed" → logs `logger.warning(...)` naming base URL/model id as the
likely cause → calls `fallback_builder(candidates)` → caller receives
embedding-only `AffectedDoc` results instead of `[]`.

### Technical Approach

Use the **raise-and-count** approach from the issue's solution sketch (cleaner
than a sentinel value, since it keeps `_rerank_single_candidate`'s existing
`None` return meaning intact for the "parsed but below threshold" case):

- In `_rerank_single_candidate`, split the single broad `except Exception:`
  block (line 362-367) into two:
  - Keep the existing `except (json.JSONDecodeError, KeyError, IndexError):`
    branch as-is (parse failures return `None`, i.e. "ran, didn't qualify").
  - Replace the generic `except Exception:` with a narrower catch for
    transport/API errors (`anthropic.APIError` and subclasses, plus any
    connection-level exception the Anthropic SDK raises) that **re-raises**
    instead of swallowing, after logging at `logger.warning` (not
    `logger.exception`, to avoid duplicate noise once the caller also logs).
  - Keep a final bare fallback (`except Exception:` at the end, if anything
    unanticipated bubbles up) that also re-raises, so no exception path is
    silently absorbed anymore — every failure is visible to the caller.
- In `_rerank_candidates`, wrap each `future.result()` call in a `try/except`.
  On exception, increment a `failure_count`; do not append to `results`. After
  the loop, return `(results, failure_count, len(candidates))` — or thread a
  small dataclass through if that reads cleaner — so `find_affected` can tell
  "0 results because all N failed" apart from "0 results because none scored
  >= 5."
- In `find_affected`, after calling `_rerank_candidates`, check: if
  `failure_count == len(candidates)` (every single candidate hard-failed),
  log:
  ```python
  logger.warning(
      "All %d Haiku rerank requests failed (check ANTHROPIC_BASE_URL / model "
      "id); falling back to embedding-only candidates.",
      len(candidates),
  )
  return fallback_builder(candidates)
  ```
  Otherwise (partial or zero failures), proceed to `result_builder(results)`
  as today — this preserves "rerank ran, nothing scored >= 5" behavior even
  when a handful of individual requests transiently failed but most succeeded.
- Both `tools/doc_impact_finder.py` and the code-impact finder's equivalent
  wrapper call `find_affected()` with their own `fallback_builder` already
  wired (per the issue, `_candidates_to_affected_docs`-style callables exist
  today) — no caller-side changes needed; the fix is entirely inside
  `impact_finder_core.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_rerank_single_candidate`'s three-way except split (JSON parse errors /
  transport-API errors / catch-all) each needs a dedicated test: parse-error
  still returns `None`; transport-error re-raises and is caught one level up
  in `_rerank_candidates`, incrementing `failure_count`.
- [ ] `_rerank_candidates`'s new failure-counting path needs a test asserting
  `failure_count` reflects the number of raised futures, and that a mix of
  success + failure does NOT trigger the all-failed threshold.

### Empty/Invalid Input Handling
- [ ] `find_affected` with zero Stage 1 candidates already returns `[]` early
  (line 483-484, unchanged) — add a comment-level note only if a test doesn't
  already cover this (existing coverage: `TestGracefulDegradation`, verify
  before adding a redundant test).
- [ ] Explicitly test `find_affected` with `candidates` non-empty but every
  rerank request raising a simulated `anthropic.APIStatusError`-like
  exception, confirming `fallback_builder` is invoked with the full candidate
  list.

### Error State Rendering
- [ ] Confirm the new `logger.warning` fires exactly once per `find_affected`
  call (not once per candidate) — assert via `caplog` or a mocked logger, not
  string-matching stdout.
- [ ] Not user-facing UI output; this pipeline's "error state" is the returned
  list vs. an internal log line, both covered above.

## Test Impact

- [ ] `tests/unit/test_doc_impact_finder.py::TestFullPipelineIntegration::test_end_to_end_embedding_only_fallback` — UPDATE: this test currently patches `builtins.__import__` to make `anthropic` import raise, simulating client-construction failure. Add a sibling test in the same class for the new "rerank endpoint reachable but every request errors" path (patch `_rerank_single_candidate` or the underlying `client.messages.create` to raise), asserting the same embedding-only fallback outcome.
- [ ] `tests/unit/test_doc_impact_finder.py::TestFullPipelineIntegration::test_end_to_end_with_mocked_apis` — no change needed; already exercises the "Haiku succeeds" happy path via patching `_rerank_single_candidate` with a working `side_effect` — confirms the non-fallback branch still works after the refactor.
- [ ] `tests/unit/test_code_impact_finder.py` — UPDATE (if it has an analogous integration test class): verify the same all-rerank-failure scenario for the code-impact finder wrapper, since it shares `find_affected()`. Add a test only if one doesn't already exist covering this fallback path; otherwise state coverage is inherited from the shared core module's tests.
- [ ] No `pytest.xfail` markers found related to this bug in either test file (`grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/` returned no hits in these files) — Phase 4.5 xfail-conversion is not applicable.

## Rabbit Holes

- Do not attempt to distinguish *which kind* of transport/API error occurred
  (rate limit vs. 404 vs. connection refused) for differentiated handling —
  the issue only asks for a binary "did the reranker run at all" signal. A
  single warning naming "check ANTHROPIC_BASE_URL / model id" is sufficient;
  building a taxonomy of Anthropic SDK exception subclasses is scope creep.
- Do not refactor `_rerank_candidates`'s `ThreadPoolExecutor` concurrency model
  (e.g. switching to `asyncio`, changing `max_workers`) — that is an unrelated
  performance concern, not this bug.
- Do not touch `MIN_SIMILARITY_THRESHOLD` or the embedding-only fallback's
  scoring logic itself — `fallback_builder` already works correctly; the bug
  is purely about *reaching* it.

## Risks

### Risk 1: Partial-failure threshold choice hides a degraded-but-not-fully-broken reranker
**Impact:** If, say, 4 of 5 candidates hard-fail but 1 succeeds with a
legitimate low score, the current design (`failure_count == len(candidates)`
gate) will NOT fall back — it proceeds with `result_builder(results)` on the
one surviving result, silently dropping the other 4 candidates' embedding
signal even though the reranker is clearly unhealthy.
**Mitigation:** This matches the issue's explicit acceptance criteria (only
routes to fallback when *every* rerank request fails) — a partial degradation
is a separate, unfiled concern. Document this boundary in the docstring for
`find_affected` so future readers know it's a deliberate all-or-nothing gate,
not an oversight.

### Risk 2: Re-raising inside a `ThreadPoolExecutor` future changes exception propagation timing
**Impact:** `future.result()` re-raises the worker's exception in the calling
thread when called. If `_rerank_candidates`' loop doesn't wrap each
`future.result()` call, an unhandled exception from one worker would crash the
entire `as_completed` loop, losing results from other already-completed
futures.
**Mitigation:** Wrap each `future.result()` call in `try/except` inside the
`as_completed` loop (not around the whole loop), so one raised exception is
caught and counted without aborting collection of the remaining futures.

## Race Conditions

No race conditions identified — `_rerank_candidates` already uses
`ThreadPoolExecutor` + `as_completed` for controlled parallelism, and this fix
only changes what happens to each future's *result* (or exception) after
`as_completed` yields it; it does not introduce new shared mutable state or
change the synchronization pattern.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1950] This plan implements the full fix described in issue
  #1950; there is no further deferred scope from that issue itself.

Nothing else deferred — every acceptance criterion in issue #1950 is in scope
for this plan.

## Update System

No update system changes required — this is a pure code fix inside
`tools/impact_finder_core.py` with no new dependencies, config files, or
deployment steps. Existing callers (`doc_impact_finder.py`, code-impact
finder) require no changes since `find_affected()`'s public signature and
return type are unchanged.

## Agent Integration

No agent integration required — `find_affected()` and its callers
(`find_affected_docs`, the code-impact finder wrapper) are already invoked via
existing CLI/tool surfaces (`tools.code_impact_finder`, doc-impact tooling used
during the DOCS SDLC stage). This fix changes only their internal failure
handling, not their entry points or contracts.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/semantic-doc-impact-finder.md` to document the
  all-rerank-failure fallback behavior (what triggers it, what log line to
  look for when debugging a misconfigured `ANTHROPIC_BASE_URL`).
- [ ] Update `docs/features/code-impact-finder.md` with a cross-reference note
  since it shares the same `find_affected()` core and inherits the same
  fallback behavior.

### Inline Documentation
- [ ] Add a docstring note to `find_affected()` explaining the all-or-nothing
  fallback gate (per Risk 1 above) so future maintainers understand the
  boundary is deliberate.
- [ ] Add a comment above the new except-split in `_rerank_single_candidate`
  explaining why transport/API errors now re-raise instead of being swallowed.

## Success Criteria

- [ ] With a misconfigured `ANTHROPIC_BASE_URL` (all Haiku requests fail),
  `find_affected()` returns `fallback_builder(candidates)` results instead of
  `[]`, when Stage 1 recall found candidates.
- [ ] Legitimate "rerank ran, nothing scored >= 5" still returns `[]` (no
  false-positive fallback).
- [ ] A partial failure (some candidates hard-fail, at least one legitimately
  scores) does NOT trigger fallback — documented as deliberate in Risk 1.
- [ ] Unit tests added in `tests/unit/test_doc_impact_finder.py` covering: (a)
  all rerank requests fail → fallback used, (b) rerank succeeds but all score
  < 5 → empty result, no fallback, (c) mixed failure/success → no fallback,
  legitimate results returned.
- [ ] Warning logged on the fallback path naming the likely cause (base URL /
  model id) — asserted via `caplog`, not string-matched stdout.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.
The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (impact-finder-fallback)**
  - Name: impact-finder-builder
  - Role: Implement the failure-vs-threshold distinction and fallback routing
    in `tools/impact_finder_core.py`, and add the corresponding unit tests.
  - Agent Type: builder
  - Resume: true

- **Validator (impact-finder-fallback)**
  - Name: impact-finder-validator
  - Role: Verify the fallback triggers correctly on all-failure, does not
    trigger on partial failure or legitimate low scores, and that the warning
    log fires exactly once.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Tier 1 — Core: `builder`, `validator`, `code-reviewer`, `test-engineer`,
`documentarian`, `plan-maker`, `frontend-tester`.

## Step by Step Tasks

### 1. Split exception handling in `_rerank_single_candidate`
- **Task ID**: build-rerank-exception-split
- **Depends On**: none
- **Validates**: tests/unit/test_doc_impact_finder.py (new tests, see task 3)
- **Assigned To**: impact-finder-builder
- **Agent Type**: builder
- **Parallel**: true
- Split the broad `except Exception:` (line 362-367) into: (a) keep
  `except (json.JSONDecodeError, KeyError, IndexError):` returning `None`
  unchanged, (b) catch transport/API errors and re-raise after a
  `logger.warning` (not `logger.exception`, to avoid double-logging once the
  caller logs the aggregate failure).
- Do not swallow any exception silently — every failure path either returns
  `None` (legitimate below-threshold/parse-failure) or re-raises (hard
  failure).

### 2. Aggregate failures in `_rerank_candidates` and route fallback in `find_affected`
- **Task ID**: build-fallback-routing
- **Depends On**: build-rerank-exception-split
- **Validates**: tests/unit/test_doc_impact_finder.py (new tests, see task 3)
- **Assigned To**: impact-finder-builder
- **Agent Type**: builder
- **Parallel**: false
- In `_rerank_candidates`, wrap each `future.result()` call in `try/except`
  inside the `as_completed` loop; count failures without aborting collection
  of remaining futures. Return failure count alongside the existing results
  list (adjust return type/signature as needed; update the one caller,
  `find_affected`).
- In `find_affected`, after `_rerank_candidates` returns, check if
  `failure_count == len(candidates)`. If so, log the warning naming
  `ANTHROPIC_BASE_URL` / model id as likely causes and call
  `fallback_builder(candidates)`. Otherwise call `result_builder(results)` as
  today.
- Add a docstring note to `find_affected` documenting the all-or-nothing
  fallback gate (Risk 1).

### 3. Add unit tests for the three failure scenarios
- **Task ID**: build-fallback-tests
- **Depends On**: build-fallback-routing
- **Validates**: tests/unit/test_doc_impact_finder.py
- **Assigned To**: impact-finder-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a test in `TestFullPipelineIntegration` (or a new `TestRerankFallback`
  class) covering: (a) all rerank requests raise → `fallback_builder` invoked
  with the full candidate list, (b) all rerank requests return scores < 5 (no
  exceptions) → `[]` returned, no fallback, (c) mixed: some raise, at least one
  scores >= 5 → legitimate results returned via `result_builder`, no fallback.
- Add a test asserting the warning log fires exactly once per `find_affected`
  call in the all-failure case (via `caplog`).
- Reuse the existing mock patterns at lines 340-371 and 373-410 (patching
  `tools.impact_finder_core._rerank_single_candidate` or
  `builtins.__import__`) rather than inventing a new mocking approach.

### 4. Validate fallback behavior end-to-end
- **Task ID**: validate-fallback
- **Depends On**: build-fallback-tests
- **Assigned To**: impact-finder-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_doc_impact_finder.py tests/unit/test_code_impact_finder.py -x -q` and confirm all pass.
- Confirm no `except Exception: pass`-style silent swallowing remains in the
  touched functions (`grep -n "except Exception" tools/impact_finder_core.py`
  and inspect each hit).
- Report pass/fail status.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-fallback
- **Assigned To**: impact-finder-builder (or a documentarian if available)
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/semantic-doc-impact-finder.md` and
  `docs/features/code-impact-finder.md` per the Documentation section above.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: impact-finder-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands from the Verification table below.
- Verify all Success Criteria are met, including documentation.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_doc_impact_finder.py tests/unit/test_code_impact_finder.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/impact_finder_core.py tests/unit/test_doc_impact_finder.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/impact_finder_core.py tests/unit/test_doc_impact_finder.py` | exit code 0 |
| No silent exception swallowing remains | `grep -n "except Exception:" tools/impact_finder_core.py \| grep -v "re-raise\|raise$"` | match count == 0 |
| Fallback path is reachable from all-failure case | `grep -n "fallback_builder(candidates)" tools/impact_finder_core.py` | output > 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. Should the exact Anthropic SDK exception type caught for "transport/API
   error" be `anthropic.APIError` (broad, covers rate limits/5xx/4xx) or
   narrower (e.g. `anthropic.APIStatusError` + `anthropic.APIConnectionError`)?
   Recommend starting broad (`anthropic.APIError`) since the goal is "the
   reranker didn't run," not differentiating error subtypes — builder should
   confirm this import path exists in the installed `anthropic` SDK version
   before finalizing.
2. Should the new failure-count return value from `_rerank_candidates` be a
   plain tuple `(results, failure_count, total)` or a small dataclass for
   readability? Recommend a tuple for minimal footprint given this is the only
   caller, but the builder should use judgment if a dataclass reads
   meaningfully cleaner at the call site.
