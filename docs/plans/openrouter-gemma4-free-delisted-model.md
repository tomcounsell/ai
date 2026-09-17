---
status: Planning
type: bug
appetite: Small
owner: Eng session
created: 2026-09-16
tracking: https://github.com/tomcounsell/ai/issues/3338
last_comment_id: none
---

# OPENROUTER_GEMMA4_FREE delisted-model fix

## Problem

Every default cheap-inference call fails. `config/models.py::OPENROUTER_GEMMA4_FREE` names `google/gemma-4-e2b:free`, which OpenRouter delisted, so a completion against it returns HTTP 400.

**Current behavior:**
- `reflections/improvement_collect.py::_judge_model()` falls back to the delisted id whenever `ImprovementSettings.cheap_inference_model` is empty (the default), so the promise-detector judge 400s on every call until an operator overrides the setting.
- `tests/ai_judge/judge.py:140` hardcodes the same id as a string literal instead of importing the constant, so it fails independently and drifts silently from `config/models.py` (which documents itself as the single source of truth: "Import model constants from here rather than hardcoding model strings").

**Desired outcome:**
- The constant points at a listed free Gemma 4 id, both consumers resolve through it, and a future delisting surfaces as a named failure at test time instead of a 400 mid-cycle.

## Freshness Check

**Baseline commit:** `6e22a9ec5d7392f56db1674f2828eb9b0c8708dc`
**Issue filed at:** 2026-09-15T15:05:29Z
**Disposition:** Minor drift

**File:line references re-verified (read 2026-09-16, all still hold):**
- `config/models.py:139` — `OPENROUTER_GEMMA4_FREE = "google/gemma-4-e2b:free"` — still holds
- `tests/ai_judge/judge.py:140` — `"model": "google/gemma-4-e2b:free"` literal — still holds
- `reflections/improvement_collect.py:790-795` — `_judge_model()` falls back to the constant — still holds
- `config/settings.py:743-753` — `cheap_inference_model` references the constant by name only — still holds, no edit needed

**Cited sibling issues/PRs re-checked:**
- #3177 — still OPEN (parent self-improvement epic; this fix is surfacing context for it)
- #3217 — CLOSED (lane-5 work item; surfacing context only)
- PR #3337 — MERGED as `fb97c7e89` (introduced the `_judge_model` consumer; did not touch the delisted constant or the judge.py literal)

**Commits on main since issue was filed (touching referenced files):**
- `fb97c7e89` Improvement controller lane 5 (#3337) — touched `reflections/improvement_collect.py` and `config/settings.py` (added the consumer that inherits the bug); did NOT touch `config/models.py` or `tests/ai_judge/judge.py`. Irrelevant to root cause; defect unchanged.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/recursive-self-improvement.md:372` references the old id descriptively in prose. Coordination signal only: this plan updates that reference (see Documentation). No scope conflict.

**Notes:** Only two code sites carry the id literal (verified by repo-wide grep); both are in scope below.

## Prior Art

- **#3217 / PR #3337**: Improvement-controller lane 5 surfaced the 400 during its build and merged the `_judge_model` consumer that inherits the bug. Not a fix attempt; no fix to learn from or avoid repeating.
- **`docs/plans/completed/gemma4-ollama-standardization-671.md`, `docs/plans/completed/gemma4_ollama_consolidation.md`, `docs/features/local-model-policy.md`**: the local-Ollama `gemma4:e2b` retirement. Different system (local model namespace vs OpenRouter id namespace share a name root but are distinct registries); no approach to reuse.
- **Closed-issue / merged-PR search** (`gemma openrouter`, `cheap inference delisted`): only #3217/#3337 matched. No prior fixes found related to this work.

## Research

No new external search performed. The model-listing facts were verified against `GET https://openrouter.ai/api/v1/models` on 2026-09-15 (444 models; `google/gemma-4-e2b:free` absent; `google/gemma-4-26b-a4b-it:free` and `google/gemma-4-31b-it:free` both listed, both 262144 context, both multimodal) and are carried as plan ground truth. The build re-confirms the listing live through the new probe test instead of re-deriving it here.

**Queries used:** none (ground truth supplied; re-querying the listing now would duplicate the build-time probe).

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (bug with pre-verified fix shape; no scope decisions open)
- Review rounds: 1 (standard review-blocker pass)

## Prerequisites

No hard prerequisites — the model-listing endpoint is public and the fix itself needs no credentials.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| OPENROUTER_API_KEY (optional) | printenv OPENROUTER_API_KEY \| wc -c (nonzero enables the live smoke completion; zero is fine) | Only needed if the builder runs a live completion smoke test; the listing probe and all unit tests run without it |

Run via `python scripts/check_prerequisites.py docs/plans/openrouter-gemma4-free-delisted-model.md` where applicable.

## Solution

### Key Elements

- **Repointed constant**: `OPENROUTER_GEMMA4_FREE` becomes a listed id, so every consumer heals at once.
- **Single source of truth**: `tests/ai_judge/judge.py` imports the constant instead of restating it, ending the silent drift.
- **Named-failure probe**: a test-time check of configured OpenRouter ids against the live model listing, so the next delisting fails loudly by name instead of 400ing mid-cycle.

### Flow

Delisted default 400s on every call → repoint constant at listed id → judge.py imports constant → probe test guards the listing → default path and ai-judge both resolve through one guarded source.

### Technical Approach

- `config/models.py:139`: repoint to `"google/gemma-4-26b-a4b-it:free"` (the closer capability match for a cheap-inference default; build re-confirms 262144 context and multimodal flags in the live listing before finalizing the choice between the two candidates). Update the `:138` comment, which currently claims 128K context.
- `tests/ai_judge/judge.py`: extend the existing `:17` import (`from config.models import OPENROUTER_URL`) to include `OPENROUTER_GEMMA4_FREE` and use it at `:140`. Zero import risk: `config.models` is stdlib-only, and the import already exists. This also restores compliance with that module's own "import from here" rule.
- Probe is test-time, not startup: a new test fetches `GET https://openrouter.ai/api/v1/models` (public endpoint, no key) and asserts each configured `OPENROUTER_*` id is listed. A delisting fails the test naming the expected id and the listing URL; a pure network error fails with a message that distinguishes "endpoint unreachable" from "id not listed" so CI flakes are diagnosable. Default: fail-closed on network error (a silent skip would re-create exactly the silent-drift class this fix removes); build may soften to skip-with-warning only with reviewer sign-off recorded in the PR.
- No migration: `cheap_inference_model` defaults to `""` (fallback path), and explicit overrides are operator choice that this fix must not rewrite.
- No change: `reflections/improvement_collect.py` (already imports the constant) and `config/settings.py` (name-only reference).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tests/ai_judge/judge.py::_call_openrouter` has a broad `except Exception: return None`. That swallow-and-None behavior is existing design (callers treat None as "judge unavailable") and out of scope to redesign; the new probe test covers the delisting case instead, asserting it surfaces by name at test time. No exception handlers in the new probe code go untested: both the delisted-id and unreachable-endpoint paths get a test each.

### Empty/Invalid Input Handling
- [ ] `_judge_model()` with an empty/whitespace override falls back to the constant — already covered by `test_the_default_model_falls_back_to_the_free_gemma`; builder confirms it still passes under the repoint.
- [ ] The constant itself is a non-empty literal; no new parsing of operator input is introduced.

### Error State Rendering
- [ ] Probe failure messages name the expected model id and the listing URL, so the failure points at the delisting rather than a downstream 400.

## Test Impact

No existing tests affected — the two behavioral tests in scope both import the constant (not the literal) and the ai-judge unit tests assert Ollama-path ids only, so the repoint changes no asserted value. Verified by grep: no `gemma-4-e2b` literal exists in any test file.

Checked, no disposition change:
- `tests/unit/test_improvement_evidence.py::test_the_default_model_falls_back_to_the_free_gemma` — KEEP: asserts `_judge_model() == OPENROUTER_GEMMA4_FREE` via import; passes under repoint, confirms fallback still resolves through the constant.
- `tests/ai_judge/test_ai_judge.py` (full file) — KEEP: asserts `gemma4:31b-cloud` / `llama3:8b` Ollama-path ids only; untouched by this fix.

New coverage (CREATE):
- [ ] Probe test asserting each configured `OPENROUTER_*` id appears in the live `GET /api/v1/models` listing (new file or alongside existing model-config tests, builder's choice; Verification pins the behavior, not the path).

## Rabbit Holes

- Startup-time listing probe in bridge boot: adds a network dependency and a new failure mode to the boot path for a condition that changes rarely. Rejected; test-time only.
- Switching to a paid model or the 31b variant on capability grounds: a cost/capability decision owned by the #3177 epic evaluation, not this delisting fix. The 26b choice stands unless the build-time listing check contradicts the ground truth.
- Auditing every `OPENROUTER_*` id individually: only `GEMMA4_FREE` is a known-delisted default on a hot path; the probe covers the remaining ids generically with no per-id work.
- Redesigning `_call_openrouter`'s swallow-and-None error handling: existing contract relied upon by callers; out of scope.

## Risks

### Risk 1: Replacement id is delisted again later
**Impact:** Default cheap-inference path 400s again.
**Mitigation:** The probe test fails loudly naming the id; the constant remains the single edit point.

### Risk 2: Live listing contradicts the ground truth (context length, flags, availability)
**Impact:** 26b choice wrong; plan bakes in a bad default.
**Mitigation:** Build re-confirms listing metadata before finalizing the id; a smoke completion runs in Verification when a key is available; deeper suitability evaluation stays with #3177.

### Risk 3: Probe test flakes CI on network blips (fail-closed default)
**Impact:** Noisy red builds unrelated to code changes.
**Mitigation:** Error message distinguishes unreachable-endpoint from id-not-listed; softening to skip-with-warning requires reviewer sign-off recorded in the PR, not a silent builder call.

## Race Conditions

No race conditions identified — the fix is a synchronous constant edit plus a single-threaded test; no shared mutable state, async flow, or cross-process handoff is introduced or touched.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3177] Suitability evaluation of the replacement model on representative tasks beyond a smoke completion — owned by the parent self-improvement epic, which already scopes inexpensive-inference evaluation.
- [EXTERNAL] Provisioning or rotating the OpenRouter API key if the builder's environment lacks one: needs a human with vault access. Without a key the builder runs the keyless listing probe plus unit tests and records the skipped live smoke completion in the PR instead of blocking on access.

## Update System

No update system changes required — the repoint ships with the normal code deploy. No new dependencies, no new config files, no migration steps, nothing to propagate to fleet machines beyond the code itself.

## Agent Integration

No agent integration required — no new CLI entry point and no bridge changes. Existing consumers (`_judge_model`, ai-judge) call through unchanged imports; the agent-visible behavior is simply that the default judge stops 400ing.

## Documentation

- [ ] Update the `google/gemma-4-e2b:free` reference in `docs/plans/recursive-self-improvement.md:372` to the new id so the parent epic's prose matches the constant.
- [ ] Confirm no other docs name the old id: `grep -rn "gemma-4-e2b:free" docs/ README.md 2>/dev/null` must come back empty (local-Ollama `gemma4:e2b` mentions in `docs/features/local-model-policy.md` are a different namespace and stay).

## Success Criteria

- [ ] `OPENROUTER_GEMMA4_FREE` resolves to a listed id; no `gemma-4-e2b:free` literal remains in any `*.py` under `config/ tests/ reflections/ tools/ bridge/ worker/ agent/`
- [ ] `tests/ai_judge/judge.py` imports the constant (grep confirms reference)
- [ ] New probe test passes against the live listing; existing fallback and ai-judge tests pass
- [ ] Parent-epic prose reference updated; docs grep clean
- [ ] Verification checks below all green

## Team Orchestration

Small appetite: one builder plus one validator, no parallel lanes.

### Team Members

- **Builder (model-fix)**
  - Name: model-fix-builder
  - Role: Repoint constant, single-source judge.py, add probe test, update parent-epic prose reference
  - Agent Type: builder
  - Resume: true

- **Validator (model-fix)**
  - Name: model-fix-validator
  - Role: Run Verification commands, confirm success criteria including documentation
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Tier 1 default choices apply (`builder`, `validator`); no domain framing needed — synchronous config edit plus a keyless HTTPS test, no concurrency, Redis, or untrusted-input surface.

## Step by Step Tasks

### 1. Repoint, single-source, probe, prose
- **Task ID**: build-model-fix
- **Depends On**: none
- **Validates**: new probe test, `tests/unit/test_improvement_evidence.py::test_the_default_model_falls_back_to_the_free_gemma`, `tests/ai_judge/` suite
- **Assigned To**: model-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Repoint `OPENROUTER_GEMMA4_FREE` at the verified listed id in `config/models.py:139` and correct the `:138` context comment
- Extend the `config.models` import in `tests/ai_judge/judge.py:17` and use the constant at `:140`
- Add the live-listing probe test (fail-closed, named-id failures, unreachable-vs-delisted distinction)
- Update the old-id prose reference in `docs/plans/recursive-self-improvement.md:372`

### 2. Validate everything
- **Task ID**: validate-all
- **Depends On**: build-model-fix
- **Assigned To**: model-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands below
- Verify every Success Criteria item, including the documentation grep
- Report pass/fail with verbatim failures if any

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Fallback and judge tests pass | `scripts/pytest-clean.sh tests/unit/test_improvement_evidence.py tests/ai_judge/ -q` | exit code 0 |
| Probe test passes | `scripts/pytest-clean.sh tests/unit/test_models.py -q` | exit code 0 |
| No stale id literals in code | `grep -r "gemma-4-e2b:free" config tests reflections tools bridge worker agent --include="*.py" \| wc -l` | match count == 0 |
| judge.py single-sourced on constant | `grep -c "OPENROUTER_GEMMA4_FREE" tests/ai_judge/judge.py` | output > 0 |
| Parent-epic prose updated | `grep -c "gemma-4-e2b:free" docs/plans/recursive-self-improvement.md` | match count == 0 |
| Formatting clean | `python -m ruff format --check config/models.py tests/ai_judge/judge.py tests/unit/test_models.py` | exit code 0 |

Lint (`ruff check`) is intentionally excluded from this table per the operator's standing instruction (formatting only, no lint runs); repo pre-commit hooks remain the lint backstop at commit time.

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Consolidated Critic (Failure Modes) | Probe asserts each configured OPENROUTER_* id is listed, but only the Gemma 4 candidates were verified against the live listing; Kimi/Qwen exact ids were not. | pending | Partition probe failures by id: hard-fail only on OPENROUTER_GEMMA4_FREE, warn-only for other unlisted ids so a stale paid-model id never reds the build. |
| CONCERN | Consolidated Critic (Scope and Value) | Test Impact leaves the probe location to builder's choice and says Verification pins behavior not path, but Verification hard-codes tests/unit/test_models.py, which does not exist. | pending | Pin tests/unit/test_models.py in all Verification rows naming it and delete the builder's-choice clause in Test Impact. |
| CONCERN | Consolidated Critic (Internal Consistency) | The docs-grep check cannot pass as written: two further docs files name the old id and get no disposition, so the mandated empty grep stays red after all listed tasks are done. | pending | Scope the docs-grep check to the parent-epic prose path and add KEEP dispositions for docs/plans/critiques/recursive-self-improvement-capability-matrix.md:245 and docs/features/improvement-research-cycle.md:730 as historical records. |

---

## Open Questions

1. Probe fail-closed default: on an unreachable listing endpoint, should CI fail (recommended default, keeps delistings loud) or skip-with-warning (quieter CI, risks re-creating silent drift)? Recommend fail-closed; a reviewer can soften it in the build PR with the decision recorded there.
2. Probe scope: assert all configured `OPENROUTER_*` ids (recommended, generic coverage at no extra cost) or just `OPENROUTER_GEMMA4_FREE`? Recommend all ids; flag in review if any unrelated id is already unlisted.
