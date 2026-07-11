---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/1829
last_comment_id:
---

# Memory extraction: LLM-based refusal-detector complement to _REFUSAL_PATTERNS

## Problem

`_looks_like_refusal()` in `agent/memory_extraction.py` filters Haiku refusal
echoes via a **closed-vocabulary** `_REFUSAL_PATTERNS` tuple (15 full-phrase
substrings today, including the 7 phrasings appended by #1822). The vocabulary
is fundamentally open-ended: whenever Haiku invents a new way to phrase "there
is nothing to extract here," that refusal prose escapes every filter, gets
parsed into observations, and is persisted as high-confidence memory noise. It
is then caught a day later by the `memory-quality-audit` reflection — which
re-files the same anomaly-cluster issue over and over (the #1497/#1786/#1931/#2016
cluster). Today's only fix is a human noticing the new junk and manually
appending another substring.

**Current behavior:**
A novel refusal phrasing that is not a substring of any `_REFUSAL_PATTERNS`
entry passes the post-LLM filter at `agent/memory_extraction.py:495`, reaches
`_parse_categorized_observations`, and is saved as one or more Memory records.
Self-healing requires a manual pattern append.

**Desired outcome:**
An **optional, default-OFF** LLM-based refusal complement: one yes/no Haiku call
(via the existing `_llm_call(MODEL_FAST, ...)` helper) on the post-LLM
extraction path, gated behind an env flag. When enabled, a refusal phrasing the
closed vocabulary missed is still caught and dropped before persistence — future
rephrasings self-heal without a code change. Default-OFF keeps cost and behavior
unchanged until an operator opts in. The #1822 pattern extension remains the
interim mitigation; this is the root-cause fix behind the flag.

## Freshness Check

**Baseline commit:** `35301b57` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-06-30T08:06:43Z (`gh issue view 1829 --json createdAt`)
**Disposition:** Minor drift + Overlap (concurrent plan #1925 already sequenced behind this one)

**File:line references re-verified (all against HEAD 35301b57):**
- `agent/memory_extraction.py:198` — `_looks_like_refusal(text)`, closed-vocab
  substring match against `_REFUSAL_PATTERNS` OR `_JSON_SHRAPNEL_RE`. Still holds.
- `agent/memory_extraction.py:59-82` — `_REFUSAL_PATTERNS` tuple, 15 entries; the
  7 #1822 additions are present (`:75-81`). Still holds.
- `agent/memory_extraction.py:495` — post-LLM `_looks_like_refusal(raw_text)`
  check inside `extract_observations`. This is the "post-LLM extraction path"
  the issue names; the complement inserts here. Still holds.
- `agent/memory_extraction.py:250` — `_llm_call(model, max_tokens, messages)`
  helper (AsyncAnthropic + double-timeout + concurrency semaphore). Still holds.
- `config/models.py:304` — `MODEL_FAST = HAIKU`. Still holds.
- `agent/tool_budget.py:40` — `_env_true(name, default)` env-flag helper to
  mirror for the default-OFF flag. Still holds.

**Cited sibling issues/PRs re-checked:**
- **#1822** (parent, PR #1831) — MERGED; its 7 pattern additions are live. No
  longer a prerequisite.
- **#1923** ("Drop ollama… classifier") — **CLOSED 2026-07-10 as NOT_PLANNED**;
  never implemented. There is **no PydanticAI classifier from #1923 to reuse.**
  The task's "reuse the #1923 classifier pattern" therefore resolves to: reuse
  the existing `_llm_call(MODEL_FAST, ...)` yes/no call structure this module
  already ships. Do NOT introduce PydanticAI here (see Overlap + No-Gos).
- **#1925** ("PydanticAI standardization for non-harness LLM calls") — **OPEN,
  active parallel lane.** Its plan (`docs/plans/pydantic-ai-nonharness-llm-standardization.md`)
  explicitly declares `agent/memory_extraction.py` as a #1829 overlap and records
  the ordering: **"#1829 … must merge before this plan's build touches the file."**
  #1829 is upstream; #1925 rebases onto it. No blocker for this plan.

**Commits on main since issue was filed (touching `agent/memory_extraction.py`):**
- `a214847f` (#2016 junk-cluster re-filing) — touched `_parse_categorized_observations`
  JSON-branch per-record filtering, NOT the `:495` callsite or `_looks_like_refusal`.
  Approach unaffected.
- `0f68f09e` (#1822) — added the 7 patterns already accounted for above.

**Active plans in `docs/plans/` overlapping this area:**
- `pydantic-ai-nonharness-llm-standardization.md` (#1925) — same file, already
  sequenced to build AFTER #1829 merges. Coordination, not a blocker.

**Notes:** Line numbers are current as of the baseline SHA (read directly, not
inferred). The only behavioral coordination is merge ordering: land #1829 first.

## Prior Art

- **#1212** "Memory extraction stores JSON shrapnel and refusal prose as
  observations" — introduced `_REFUSAL_PATTERNS` + `_looks_like_refusal` +
  tolerant JSON parsing; established the pre-LLM / post-LLM / per-line filter
  layering. The post-LLM filter (`:495`) is the load-bearing defense this plan
  complements.
- **#1822** (PR #1831) "three systematic noise sources" — appended 7 full-phrase
  refusal patterns; scoped OUT the LLM complement into this issue (#1829) to
  avoid gold-plating the noise-source PR. Interim mitigation.
- **#2016** (PR #2023) "recurring junk-cluster re-filing" — closed the tolerant-JSON
  per-record gap so the JSON branch and line-fallback apply the identical
  `_looks_like_refusal` predicate. Confirms the per-record predicate is the
  extension point; the LLM complement rides the same whole-response predicate.
- **#1231** — `memory-quality-audit` reflection imports `_looks_like_refusal`
  directly so detection cannot drift from prevention. Any complement must keep
  `_looks_like_refusal` intact (the reflection depends on it); the LLM check is
  ADDITIVE, layered around it, not a replacement.
- **#1497 / #1786 / #1931** — recurring `[memory-audit]` anomaly-cluster issues
  from the same `extraction-*` agent_id. These are the symptom the root-cause
  fix targets.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| #1212 | Added closed-vocab `_REFUSAL_PATTERNS` + regex | Closed vocabulary — any unseen phrasing escapes |
| #1822 | Appended 7 more full phrases | Same class of fix; the vocabulary is still finite and open-ended |
| #2016 | Applied the predicate per-record on the JSON branch | Tightened WHERE the predicate runs, not WHAT it recognizes — a novel phrasing still isn't a pattern |

**Root cause pattern:** every prior fix extended a **static substring list**.
The failure mode is unbounded natural-language variation the list can never
enumerate. The root-cause fix is a semantic (LLM) judge that generalizes beyond
the enumerated phrasings — exactly what this issue scopes, behind a default-OFF
flag so cost/behavior stay unchanged until opted in.

## Architectural Impact

- **New dependencies:** none. Reuses `_llm_call`, `MODEL_FAST`, `get_anthropic_api_key`,
  and `_record_extraction_error`, all already in the module.
- **Interface changes:** none to public signatures. `extract_observations`
  keeps its signature and return type (`list[dict]`). One new private helper
  (`_looks_like_refusal_llm` or equivalent) and one env-flag reader.
- **Coupling:** unchanged. The complement is layered strictly AFTER the existing
  closed-vocab `_looks_like_refusal(raw_text)` check and only when the flag is
  on. `_looks_like_refusal` itself is untouched, preserving the
  `memory-quality-audit` import contract (#1231).
- **Data ownership:** unchanged.
- **Reversibility:** trivial — set the flag off (the default) to fully disable;
  the code path is dormant when off.

## Appetite

**Size:** Small

**Team:** Solo dev, 1 review round

**Interactions:**
- PM check-ins: 0-1 (scope is fully specified by the issue + this plan)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` (for the real integration test only; not required for unit tests or for the feature when the flag is OFF) | `python -c "from utils.api_keys import get_anthropic_api_key as g; assert g()"` | The AI-judge integration test makes real Haiku calls |

The feature itself requires no key when the flag is OFF (default). A key is
required only (a) when an operator enables the flag, and (b) to run the real
integration test.

## Solution

### Key Elements

- **Default-OFF env flag** (`MEMORY_REFUSAL_LLM_ENABLED`): read at call time via
  an `_env_true`-style helper defaulting to `false`, mirroring
  `agent/tool_budget.py:40`. Read-at-call-time (not module-capture) so tests
  toggle it with `monkeypatch.setenv`.
- **LLM refusal complement** (`_looks_like_refusal_llm`): one yes/no Haiku call
  via `_llm_call(MODEL_FAST, ...)` with a tight classification prompt returning a
  single token (`REFUSAL` / `CONTENT`). Pure of side effects except the API call.
- **Insertion at the post-LLM path** (`extract_observations`, after the existing
  `:495` closed-vocab check returns False, before `_parse_categorized_observations`):
  when the flag is ON, run the complement; if it verdicts REFUSAL, log and return
  `[]`. When OFF, the complement is never invoked — zero extra calls, identical
  behavior to today.
- **Fail-open on classifier error:** `TimeoutError` / any exception from the
  complement call is caught, recorded via `_record_extraction_error`, and
  extraction PROCEEDS to parse. The complement is a bonus layer over the
  closed-vocab check; a judge failure must never discard a legitimate extraction.

### Flow

Haiku extraction returns `raw_text` → closed-vocab `_looks_like_refusal(raw_text)`
(unchanged; catches known phrasings, at most zero extra calls) → **if flag ON and
not already caught**: one yes/no Haiku complement call → REFUSAL ⇒ return `[]`
(dropped); CONTENT ⇒ continue; error ⇒ fail-open, continue → `_parse_categorized_observations`
→ persist.

Call-count guarantee: at most **one** extra Haiku call per non-empty extraction,
only when the flag is ON AND the closed-vocab check did not already short-circuit
(obvious refusals never reach the complement).

### Technical Approach

- Add `_env_true`-style reader (local to this module — do not import from
  `tool_budget.py` to avoid new coupling) and a `MEMORY_REFUSAL_LLM_ENABLED`
  default-`false` gate function.
- Add `async def _looks_like_refusal_llm(text: str) -> bool` that issues one
  `_llm_call(model=MODEL_FAST, max_tokens=5, messages=[...])`. Prompt: instruct
  the model to reply with exactly `REFUSAL` or `CONTENT`; parse
  `raw.strip().upper().startswith("REFUSAL")`. Any non-`REFUSAL` (including
  unexpected output) ⇒ treat as CONTENT (fail-open at the parse boundary too).
- In `extract_observations`, after the `:495` block, insert:
  `if _refusal_llm_enabled() and await _looks_like_refusal_llm(raw_text): log; return []`,
  wrapped so `TimeoutError`/`Exception` fail-open and call `_record_extraction_error`.
- Reuse the existing truncation (`raw_text` is already the model output; classify
  a bounded slice, e.g. `raw_text[:4000]`, to cap tokens).
- **Do NOT** touch `_looks_like_refusal`, `_REFUSAL_PATTERNS`, or
  `_parse_categorized_observations` semantics — the complement wraps around them.
- **Do NOT** add the complement to `extract_post_merge_learning` or
  `detect_outcomes`; the issue scopes the post-LLM *extraction* path only.
- **Do NOT** introduce PydanticAI — that is #1925's lane, sequenced after this
  merge. Riding the existing `_llm_call` helper lets #1925 absorb this call site
  uniformly later.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new complement call is wrapped in `try/except TimeoutError` and
  `except Exception`; each branch calls `_record_extraction_error` and
  fails-open (returns to the parse path). Add a unit test asserting a raised
  `TimeoutError` from the complement ⇒ observations still saved AND
  `_record_extraction_error` invoked (assert the analytics/log side effect).
- [ ] No `except Exception: pass` silent swallow — every catch logs or records.

### Empty/Invalid Input Handling
- [ ] The complement is only reached for `raw_text` that already passed the
  50-char guard, whitespace guard, closed-vocab check, and `NONE`/empty check
  at `:486`, so empty/whitespace input never reaches it. Document this; add a
  unit test that empty extraction output short-circuits before any complement
  call (assert 0 complement calls).
- [ ] Flag-OFF path: assert `extract_observations` makes exactly ONE Haiku call
  (the extraction) and never the complement — no silent extra spend.

### Error State Rendering
- [ ] Not user-facing (bridge-internal memory pipeline). The observable failure
  surface is the `memory.extraction.error` analytics counter + debug logs;
  tested above. State "no user-visible output" for this row.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py::TestRunPostSessionExtraction::test_refusal_output_not_saved` — NO CHANGE: exercises the closed-vocab path with the flag OFF (default); behavior identical. Verify it still passes unchanged.
- [ ] `tests/unit/test_memory_extraction.py` mock-based extraction tests (`_make_async_anthropic_mock`, single-`_llm_call` assumption) — NO CHANGE with flag OFF: the complement never fires, so total call counts are unchanged.

No existing tests require UPDATE/DELETE/REPLACE — the feature is additive behind
a default-OFF flag, so every existing test (which does not set the flag) observes
byte-identical behavior. New tests are added; none are modified.

## Rabbit Holes

- **Rewriting `_REFUSAL_PATTERNS` or `_looks_like_refusal`.** Out of scope and
  breaks the `memory-quality-audit` import contract (#1231). The complement wraps
  around the closed-vocab check; it does not replace it.
- **Introducing PydanticAI / a typed output model here.** That is #1925's lane,
  explicitly sequenced after this merge. Adding it now causes a head-on collision
  on the same file and violates the "reuse `_llm_call`" scope.
- **A classifier, training set, or caching layer.** Explicit non-goals in the
  issue. One stateless yes/no call per extraction, nothing persisted.
- **Adding the complement to the post-merge / outcome-detection paths.** The
  issue scopes the post-LLM extraction path only.
- **Prompt over-engineering.** A tight two-token (`REFUSAL`/`CONTENT`) prompt is
  enough; resist few-shot bloat that inflates tokens on every extraction.
- **Tuning `MEMORY_REFUSAL_LLM_ENABLED` on by default.** Ships OFF by design.

## Risks

### Risk 1: False positives drop legitimate observations
**Impact:** An over-eager LLM judge classifies a real observation as a refusal
and silently discards memory. This is worse than a missed refusal (which the
nightly audit still catches).
**Mitigation:** Default-OFF ships zero risk until opted in. The prompt is
narrow ("refusal / meta-commentary about the ABSENCE of content"). The real
integration test with an AI judge (below) measures false-positive rate against
genuine observations and asserts zero false drops on the positive-control set.
Fail-open on error ensures classifier failures never drop content.

### Risk 2: Extra latency / cost per extraction when enabled
**Impact:** One additional Haiku round-trip on every non-empty extraction that
passed the closed-vocab check.
**Mitigation:** Default-OFF. When on, the call is bounded by the same
double-timeout (`_EXTRACTION_HARD_TIMEOUT`) and concurrency semaphore as all
module calls, and classifies a truncated slice (`max_tokens=5`). Obvious refusals
are short-circuited by the closed-vocab check and never reach the complement.

### Risk 3: Merge-order collision with #1925 on the same file
**Impact:** #1925 rewrites `_llm_call` call sites to PydanticAI; a rebase
conflict if it lands first.
**Mitigation:** #1925's own plan sequences #1829 to merge first. This plan
touches only `extract_observations` + two new private helpers, leaving `_llm_call`
untouched, minimizing the rebase surface #1925 inherits.

## Race Conditions

No race conditions identified. The complement is a single awaited call inserted
synchronously into the existing single-flight `extract_observations` coroutine;
it introduces no shared mutable state, no new concurrency, and no cross-process
data flow. The env flag is read (never written) at call time.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1925] PydanticAI adoption / typed-output wrapper for this
  call site — owned by #1925, sequenced to build after #1829 merges. This plan
  reuses the existing `_llm_call` helper and adds no PydanticAI.
- Modifying `agent/sdk_client.py` — other lane; not touched.
- Modifying `agent/session_runner/` — other lane; not touched.
- Modifying `models/agent_session.py` — other lane; not touched.
- Modifying `agent/sdlc_router.py` — other lane; not touched.
- Editing `_REFUSAL_PATTERNS` or the body of `_looks_like_refusal` /
  `_parse_categorized_observations` — the complement wraps around them; changing
  them would break the `memory-quality-audit` import contract (#1231). Anti-criterion
  row in Verification asserts these bodies are untouched.
- Adding the complement to `extract_post_merge_learning` / `detect_outcomes` —
  the issue scopes the post-LLM extraction path only.
- A classifier, training set, or caching layer — explicit issue non-goals.

## Update System

No update system changes required — this feature is purely internal to
`agent/memory_extraction.py`. `MEMORY_REFUSAL_LLM_ENABLED` is a runtime toggle
that defaults OFF and is read directly from `os.environ` (mirroring
`TOOL_BUDGET_ENABLED` / `REDIS_OFFLOAD_ENABLED`, which are deliberately absent
from `.env.example`). No new dependency, no config file to propagate, no
migration. `scripts/update/run.py` and `scripts/update/migrations.py` are
untouched. The flag is documented in `docs/features/subconscious-memory.md` for
operator discoverability rather than in `.env.example`, following the existing
optional-flag precedent.

## Agent Integration

No agent integration required — this is a bridge-internal change to the
post-session memory-extraction pipeline. No new CLI entry point in
`pyproject.toml [project.scripts]`, no `mcp_servers/` or `.mcp.json` change, and
no new import in `bridge/telegram_bridge.py`. `extract_observations` is already
invoked internally by the post-session extraction hook; its signature and return
type are unchanged, so no caller is affected. The agent reaches the behavior only
through the env flag, which an operator sets on the worker environment.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — the "Refusal-pattern filter"
  subsection (around `:186-194`) already forward-references #1829 ("A follow-up
  (issue #1829) tracks an optional, default-off LLM refusal-complement so future
  rephrasings self-heal without a manual append"). Replace that forward-reference
  with a description of the shipped `MEMORY_REFUSAL_LLM_ENABLED` flag: what it
  does, that it defaults OFF, the one-extra-Haiku-call cost, the fail-open error
  behavior, and that it complements (never replaces) `_looks_like_refusal`.
- [ ] Update the maintenance-contract paragraph (`:194`) to note the LLM
  complement as the self-healing alternative to manual pattern appends when the
  flag is enabled.

### Inline Documentation
- [ ] Module-level comment block above the new helpers explaining the default-OFF
  rationale, the fail-open contract, and the single-call guarantee.
- [ ] Docstring on `_looks_like_refusal_llm` documenting the yes/no contract and
  that it wraps (does not replace) the closed-vocab check.

`docs/features/README.md` needs no new row — `subconscious-memory.md` is already
indexed; this updates an existing feature.

## Success Criteria

- [ ] `MEMORY_REFUSAL_LLM_ENABLED` defaults OFF; with the flag unset,
  `extract_observations` makes exactly one Haiku call (unit test asserts the
  complement is never invoked).
- [ ] With the flag ON and a novel refusal phrasing NOT in `_REFUSAL_PATTERNS`,
  `extract_observations` returns `[]` (the complement caught it).
- [ ] With the flag ON and genuine observation text, observations are parsed and
  saved (no false drop).
- [ ] Complement raising `TimeoutError`/`Exception` fails open (observations
  saved) and records `memory.extraction.error` (unit test asserts both).
- [ ] `_looks_like_refusal`, `_REFUSAL_PATTERNS`, and `_parse_categorized_observations`
  bodies are unchanged (anti-criterion, grep-verified).
- [ ] A REAL integration test (gated on `ANTHROPIC_API_KEY`) drives the flag-ON
  path against live Haiku over a fixture set of novel refusals + genuine
  observations, and an independent AI-judge Haiku call grades the detector's
  drop/keep decisions as correct — no keyword-only assertions.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (refusal-complement)**
  - Name: `refusal-builder`
  - Role: Implement the env flag, `_looks_like_refusal_llm`, and the
    `extract_observations` insertion in `agent/memory_extraction.py`
  - Agent Type: builder
  - Domain: async (one awaited `_llm_call` inside an existing coroutine;
    fail-open error handling)
  - Resume: true

- **Test Engineer (refusal-tests)**
  - Name: `refusal-tester`
  - Role: Unit tests (flag OFF no-call, flag ON refusal-drop, flag ON
    content-keep, fail-open) + the REAL AI-judge integration test
  - Agent Type: test-engineer
  - Resume: true

- **Validator (refusal-validate)**
  - Name: `refusal-validator`
  - Role: Verify all success criteria + anti-criterion (unchanged predicate
    bodies), run the Verification table
  - Agent Type: validator
  - Resume: true

- **Documentarian (refusal-docs)**
  - Name: `refusal-documenter`
  - Role: Update `docs/features/subconscious-memory.md`
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement the flag + complement + insertion
- **Task ID**: build-complement
- **Depends On**: none
- **Validates**: `tests/unit/test_memory_extraction.py` (existing suite must stay green)
- **Assigned To**: refusal-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a module-local default-`false` env reader (`_refusal_llm_enabled()`) mirroring `agent/tool_budget.py:40`, read at call time.
- Add `async def _looks_like_refusal_llm(text: str) -> bool` issuing one `_llm_call(MODEL_FAST, max_tokens=5, messages=[...])` with a two-token `REFUSAL`/`CONTENT` prompt; parse `startswith("REFUSAL")`; treat unexpected output as CONTENT.
- Insert into `extract_observations` immediately after the `:495` closed-vocab block: `if _refusal_llm_enabled() and await _looks_like_refusal_llm(raw_text): log + return []`, wrapped in `try/except TimeoutError`/`except Exception` that fail-open and call `_record_extraction_error`.
- Do NOT modify `_looks_like_refusal`, `_REFUSAL_PATTERNS`, or `_parse_categorized_observations`.

### 2. Unit tests
- **Task ID**: test-unit
- **Depends On**: build-complement
- **Validates**: `tests/unit/test_memory_extraction.py`
- **Assigned To**: refusal-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Flag OFF (default): assert exactly one Haiku call, complement never invoked.
- Flag ON + mocked complement returns REFUSAL: `extract_observations` returns `[]`.
- Flag ON + mocked complement returns CONTENT: observations saved.
- Flag ON + complement raises `TimeoutError` and generic `Exception`: fail-open (observations saved) AND `_record_extraction_error` invoked.
- Flag ON but empty/`NONE` extraction output: complement not reached (0 complement calls).

### 3. Real AI-judge integration test
- **Task ID**: test-integration
- **Depends On**: build-complement
- **Validates**: `tests/integration/test_memory_refusal_llm.py` (create)
- **Assigned To**: refusal-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- `@pytest.mark.integration`; autouse fixture skips when `get_anthropic_api_key()` is falsy (pattern: `tests/integration/test_unthreaded_routing.py:191`).
- Enable `MEMORY_REFUSAL_LLM_ENABLED` via `monkeypatch.setenv`.
- Fixture set: 3+ novel refusal phrasings deliberately NOT substrings of any `_REFUSAL_PATTERNS` entry, plus 3+ genuine observation texts.
- Drive the real flag-ON path against live Haiku; collect the detector's drop/keep decision per input.
- An independent AI-judge Haiku call grades whether each drop/keep decision was correct (refusals dropped, genuine content kept). Assert the judge reports 100% correct on the positive-control (no false drops) and catches the novel refusals — NO keyword-only assertions.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-complement
- **Assigned To**: refusal-documenter
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md` refusal-filter subsection + maintenance-contract paragraph per the Documentation section.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: test-unit, test-integration, document-feature
- **Assigned To**: refusal-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm every Success Criterion; confirm the anti-criterion (unchanged predicate bodies) via grep.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_memory_extraction.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/memory_extraction.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/memory_extraction.py` | exit code 0 |
| Flag defaults OFF | `grep -n 'MEMORY_REFUSAL_LLM_ENABLED' agent/memory_extraction.py` | output contains MEMORY_REFUSAL_LLM_ENABLED |
| Complement reuses `_llm_call` | `grep -n '_llm_call' agent/memory_extraction.py` | output contains _llm_call |
| Integration test exists | `test -f tests/integration/test_memory_refusal_llm.py` | exit code 0 |
| Anti-criterion: `_REFUSAL_PATTERNS` tuple body unchanged | `git diff origin/main -- agent/memory_extraction.py \| grep -E '^[-+].*_REFUSAL_PATTERNS' \| grep -v 'def \|# '` | match count == 0 |
| Anti-criterion: no PydanticAI introduced (that is #1925's lane) | `grep -c 'pydantic_ai' agent/memory_extraction.py` | match count == 0 |
| Anti-criterion: complement not added to post-merge/outcome paths | `grep -n '_looks_like_refusal_llm' agent/memory_extraction.py \| grep -E 'post_merge\|detect_outcomes'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. Flag name: `MEMORY_REFUSAL_LLM_ENABLED` proposed (mirrors `TOOL_BUDGET_ENABLED`
   style). Confirm, or prefer a `MEMORY_EXTRACTION_*` prefix for grouping.
2. On classifier error the plan fails OPEN (keeps the extraction) to avoid
   false-dropping legitimate observations. Confirm this is the desired bias
   versus fail-closed (drop on uncertainty).
