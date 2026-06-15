---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-06-15
tracking: https://github.com/tomcounsell/ai/issues/1693
last_comment_id:
---

# Consolidate Ollama HTTP Transport into One Internal Client

## Problem

Three modules each hand-roll their own Ollama HTTP client. Two are near-identical
`urllib` POSTs to `/api/generate`; the third uses the `ollama` package's `chat()`.
There is no shared transport, so connection handling, timeouts, error-swallowing,
config resolution, and JSON parsing are reimplemented (and subtly diverge) across
three call sites.

**The three call sites:**

| File | Function | Mechanism | Endpoint | Timeout |
|------|----------|-----------|----------|---------|
| `tools/memory_search/title_generator.py` | `_resolve_ollama_config()` + `_post_ollama_generate()` | `urllib.request` | `/api/generate` | `settings.models.memory_title_timeout_s` (default 5.0) |
| `tools/knowledge/indexer.py` | `_summarize_via_ollama()` | `urllib.request` (inline imports) | `/api/generate` | hardcoded `8.0` |
| `tools/email_cs/triage.py` | inline in `triage_local()` | `ollama` package `chat()` | `/api/chat` | package default |

**Current behavior:**
A change to Ollama connection behavior (retry, a header, base-URL scheme, timeout
policy) must be made in three places. The two `/api/generate` callers keep drifting:
config resolution is duplicated with the same `gemma4:31b-cloud` fallback literal
pasted twice; the `localhost:11434` literal and `/api/generate` URL each appear in
two places; timeouts are inconsistent (configurable vs. hardcoded `8.0`).

**Desired outcome:**
One internal module owns Ollama HTTP transport + config resolution. The
`localhost:11434` literal and `/api/generate` URL appear in exactly one place.
`generate` and `chat` are both supported. Each caller keeps its own
fallback/escalation policy (a domain decision), but transport, timeout sourcing,
and the error contract are shared. This is a **behavior-preserving refactor**.

## Freshness Check

**Baseline commit:** `0d000e59cf39304b0861e93240d5623aad6f43f3` (HEAD at plan time)
**Issue filed at:** 2026-06-15T10:20:15Z (same day — fresh)
**Disposition:** Unchanged

**File:line references re-verified (all still hold):**
- `tools/memory_search/title_generator.py:43` — `_resolve_ollama_config()` returns `(base_url, model, timeout_s)`, duplicated try/except settings load with `gemma4:31b-cloud` fallback — CONFIRMED (lines 43-67).
- `tools/memory_search/title_generator.py:70` — `_post_ollama_generate()` urllib POST to `/api/generate`, fail-silent returning `None` — CONFIRMED (lines 70-103).
- `tools/knowledge/indexer.py:79` — `_summarize_via_ollama()` inline urllib imports, hardcoded `timeout=8.0`, `localhost:11434`/`gemma4:31b-cloud` fallbacks, catches `(URLError, TimeoutError, OSError, Exception)` returning `None` — CONFIRMED (lines 79-106).
- `tools/email_cs/triage.py:89-104` — `ollama.chat(model=OLLAMA_CLASSIFIER_MODEL, messages=..., options={"temperature": 0})` inside `triage_local()`, wrapped in try/except that escalates on any `Exception` — CONFIRMED.

**Cited sibling issues/PRs re-checked:**
- #1636 (merged in `b4545fbd`) — centralized **model selection** into `config/models.py` / `config/settings.py`. This issue is the **transport-layer** follow-on; #1636's config layer is not revisited. `tests/unit/test_ollama_consolidation.py` covers #1636's config layer only and must stay green.

**Commits on main since issue filed (touching referenced files):** none. The last
commit touching any of the three files is `b4545fbd` (#1636), which predates this
issue. No drift.

**Active plans in `docs/plans/` overlapping this area:**
- `gemma4_ollama_consolidation.md` and `gemma4-ollama-standardization-671.md` exist
  but both target the **model-selection / standardization** layer (the #1636 line of
  work), not the HTTP transport. No overlap with this transport refactor.

**Notes:** Codebase matches the issue exactly. No corrected line numbers needed.

## Prior Art

- **#1636 (PR `b4545fbd`)**: "Consolidate gemma4:e2b onto granite + Ollama Cloud" —
  centralized model selection (`OLLAMA_CLASSIFIER_MODEL`, `ensure_generation_model()`,
  RAM guards, cloud-tag detection) into `config/models.py`. Succeeded. This issue
  explicitly does NOT re-implement that; it finishes the job one layer down at the
  HTTP transport.
- **PR `2f684b62`**: "Prefer local Ollama over cloud Haiku for knowledge indexer
  summarization" — introduced `_summarize_via_ollama()` in the indexer. That is one
  of the three call sites this plan consolidates.
- No prior failed attempts at transport consolidation found. This is the first.

## Research

No relevant external findings needed beyond the `ollama` package's own API surface,
which was validated directly against the installed package (see Spike Results).
The `ollama` Python package is already declared (`pyproject.toml:14`,
`ollama>=0.3.0`) and is the canonical client; no external best-practice research
changes the approach.

## Spike Results

### spike-1: Does the `ollama` package support a per-call (or per-client) timeout equivalent to the urllib `timeout=` the generate callers rely on?
- **Assumption**: "The `ollama` package can honor the configurable timeout the urllib code currently passes, so we can standardize all three callers on the package and drop hand-rolled urllib."
- **Method**: code-read against the installed package (`.venv/bin/python -c "import ollama, inspect; ..."`)
- **Finding**: The module-level `ollama.generate()` and `ollama.chat()` do **NOT** accept a `timeout` kwarg. Timeout is configured at the **client** level: `ollama.Client(host=..., timeout=N)` forwards `timeout` to the underlying `httpx` client (verified: `ollama.Client(host='http://localhost:11434', timeout=3.0)` constructs cleanly; `Client.generate`/`Client.chat` exist and work). So the seam is: construct one `Client` per call with the resolved `(host, timeout)`, then call `client.generate(...)` / `client.chat(...)`.
- **Confidence**: high
- **Impact on plan**: Resolves Open Question "one transport or two" → **one** transport (the `ollama` package), with the shared module owning `Client(host=base_url, timeout=timeout_s)` construction. Hand-rolled urllib is removed entirely. The per-caller timeout is preserved because the shared `generate()` accepts a `timeout_s` arg and passes it into the `Client`.

### spike-2: Will switching triage from module-level `ollama.chat()` to `Client.chat()` break the existing triage test's `sys.modules["ollama"]` stub?
- **Assumption**: "Tests stub the `ollama` module; the seam change must keep them working with minimal churn."
- **Method**: code-read of `tests/unit/test_email_cs_triage.py`
- **Finding**: `_stub_ollama()` injects a fake module into `sys.modules["ollama"]` exposing a `chat` attribute, and triage calls `ollama.chat(...)` directly. If transport moves into `tools/ollama_client.py` and triage calls `ollama_client.chat(...)`, the test must patch the new seam (`tools.ollama_client` internals or `triage`'s imported `chat` reference) instead. This is an expected UPDATE, not a blocker. The cleanest seam: `tools/ollama_client.chat(messages, *, model, options)` builds a `Client` and calls `.chat()`; the triage test patches `tools.ollama_client.chat` (or the name triage imports) to raise / return canned content.
- **Confidence**: high
- **Impact on plan**: Triage test is UPDATE (re-point the stub at the new seam). Triage's observable contract (escalate-on-exception) is preserved by having shared `chat()` **raise** on failure.

## Data Flow

Three independent, single-shot call paths converge on one new transport module:

1. **Title path**: Memory save → `title_generator._do_generate()` → `_resolve_ollama_config()` (now delegates to `ollama_client.resolve_config()`) → `<private>` strip + `ensure_generation_model()` gate (stay at caller) → `_post_ollama_generate()` (now delegates to `ollama_client.generate()`) → `None`-on-failure → normalize → save title.
2. **Indexer path**: `_summarize_content()` → `_summarize_via_ollama()` (now delegates to `ollama_client.generate()`) → `None`-on-failure → falls back to Anthropic Haiku → first-N-chars truncation.
3. **Triage path**: `triage_local()` → `ollama_client.chat()` (raises on failure) → caller's existing `try/except` catches and calls `escalate_triage(...)`.

Entry points differ (memory save, file index, inbound email); they share only the
transport. No new cross-component coupling is introduced — the dependency direction
is callers → `ollama_client` → `ollama` package / `config.settings`.

## Architectural Impact

- **New dependencies**: None. `ollama>=0.3.0` already declared; `pyproject.toml` unchanged (possibly a comment tweak only).
- **Interface changes**: New internal module `tools/ollama_client.py` with three public functions: `resolve_config()`, `generate()`, `chat()`. Caller-local functions (`_resolve_ollama_config`, `_post_ollama_generate`, `_summarize_via_ollama`) are kept as thin adapters that delegate, preserving each module's existing internal seam so test churn stays minimal.
- **Coupling**: Decreases. Transport logic moves from three copies to one; callers depend on a single seam.
- **Data ownership**: Unchanged. `config.settings` still owns config values; the new module only reads them.
- **Reversibility**: High. Pure internal refactor; revert is a single-file deletion plus restoring three call sites.

## Appetite

**Size:** Small

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 0 (issue is fully specified; open questions resolved by spikes)
- Review rounds: 1 (behavior-preserving refactor; review confirms no behavior change)

## Prerequisites

No external prerequisites — `ollama>=0.3.0` is already installed and `urllib` is
stdlib. The refactor needs no live Ollama server (all tests stub transport).

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ollama` package importable | `python -c "import ollama; ollama.Client(host='http://localhost:11434', timeout=1.0)"` | Shared client construction |

## Solution

### Key Elements

- **`tools/ollama_client.py`** (new): the single owner of Ollama HTTP transport and config resolution.
  - `resolve_config() -> tuple[str, str, float]` — returns `(base_url, model, timeout_s)`, reading `settings.models.ollama_host`, `settings.models.ollama_generation_model`, `settings.models.memory_title_timeout_s`, with the existing hardcoded fallbacks (`http://localhost:11434`, `gemma4:31b-cloud`, `5.0`) for environments where settings can't load. Single home for the `localhost:11434` and `gemma4:31b-cloud` literals.
  - `generate(prompt, *, model, timeout_s, base_url=None) -> str | None` — builds `ollama.Client(host=base_url-or-resolved, timeout=timeout_s)`, calls `.generate(model=model, prompt=prompt, stream=False)`, returns the response text. **Fail-silent**: returns `None` on any connection/timeout/parse error (logged at DEBUG). Replaces the urllib transport in `_post_ollama_generate` and `_summarize_via_ollama`.
  - `chat(messages, *, model, options=None, base_url=None, timeout_s=None) -> str` — builds a `Client` and calls `.chat(...)`, returning `response["message"]["content"]`. **Raises** on any failure (does NOT swallow), preserving triage's escalate-on-exception contract.
- **Caller-local adapters stay** (minimize test churn):
  - `title_generator._resolve_ollama_config()` → delegates to `ollama_client.resolve_config()`.
  - `title_generator._post_ollama_generate(base_url, model, prompt, timeout_s)` → delegates to `ollama_client.generate(...)`.
  - `indexer._summarize_via_ollama(prompt)` → resolves config + delegates to `ollama_client.generate(...)` with the (now centralized) 8.0-vs-configurable timeout decision (see Technical Approach).
  - `triage_local()` → calls `ollama_client.chat(...)` inside its existing try/except.

### Flow

Memory save → title_generator → ollama_client.generate() → None-or-text → title written
File index → indexer → ollama_client.generate() → None → Haiku fallback → summary
Inbound email → triage_local → ollama_client.chat() → text-or-raise → classify-or-escalate

### Technical Approach

- **One transport.** Standardize all three call sites on the `ollama` package via
  `ollama.Client(host=..., timeout=...)`. Hand-rolled `urllib` is removed from both
  generate callers (spike-1: the package's `Client` honors a per-client timeout via
  httpx passthrough; module-level `generate()`/`chat()` do not accept `timeout`, so
  the shared module constructs a `Client` per call).
- **Error contract.** Shared `generate()` returns `None` on failure (both generate
  callers want this). Shared `chat()` **raises** on failure (triage wants
  exception-to-escalate; its existing `except Exception` block is unchanged and
  catches whatever the package/Client raises). This preserves each caller's existing
  observable behavior without forcing one contract on the other.
- **Timeout sourcing.** `resolve_config()` returns the configurable
  `memory_title_timeout_s` (default 5.0). The title caller passes that through. The
  indexer currently hardcodes `8.0`; to preserve its observable behavior exactly,
  the indexer adapter passes `timeout_s=8.0` explicitly to `ollama_client.generate()`
  (the centralization is in transport + config-literal ownership, not in unifying the
  two timeouts — unifying them would be a behavior change, out of scope). The `8.0`
  thus appears once, at the indexer caller, as an explicit argument rather than buried
  in a urllib call. Triage uses the package default timeout (preserved) unless we pass
  one; keep it unspecified to avoid changing triage timing behavior.
- **Caller-specific safety stays at the caller.** The `<private>`-tag strip and the
  `ensure_generation_model()` gate in `title_generator._do_generate` are NOT moved
  into the shared client (per the issue — caller-specific safety policy).
- **JSON/response parsing** for `/api/generate` moves into `ollama_client.generate()`
  (the package returns a typed `GenerateResponse`, so `.response` access replaces the
  manual `json.loads(...).get("response")`); the `None`-on-missing-field guard is
  preserved.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `ollama_client.generate()` swallows all transport/parse errors and returns `None` — add a unit test asserting `None` is returned and a DEBUG log is emitted when the underlying `Client.generate` raises (connection error, timeout, bad response).
- [ ] `ollama_client.chat()` does NOT swallow — add a unit test asserting it re-raises when `Client.chat` raises, so triage's escalation path fires.
- [ ] `title_generator`: existing tests already assert silent-on-failure (`test_silent_on_ollama_failure`); re-point them at the new seam and confirm DEBUG-log behavior preserved.
- [ ] `indexer`: existing `_summarize_via_ollama` catches `(URLError, TimeoutError, OSError, Exception)` → that broad catch collapses into the shared `generate()` returning `None`. Test asserts Haiku fallback still triggers when `generate()` returns `None`.
- [ ] `triage`: existing `test_ollama_failure_escalates` asserts escalation on transport failure — re-point stub at new seam; the raise-propagation must keep this green.

### Empty/Invalid Input Handling
- [ ] `ollama_client.generate()` with empty/whitespace prompt: behavior matches current (the package is called; empty prompt is the caller's concern). Title caller already guards `if not memory_id or not content: return` before transport — unchanged.
- [ ] `generate()` returns `None` when the response field is missing or non-string (preserve current `isinstance(response, str)` guard).
- [ ] No agent-output-loop concern: these are single-shot calls, not loops.

### Error State Rendering
- [ ] No user-visible UI. Failure rendering = the existing fallback chains (title: skip write; indexer: Haiku → truncation; triage: `escalate_triage(...)`). Tests above cover each.

## Test Impact

- [ ] `tests/unit/test_memory_title_generator.py` — UPDATE: 5+ `patch("tools.memory_search.title_generator._post_ollama_generate")` sites stay valid IF `_post_ollama_generate` is kept as a delegating adapter (preferred). Add new assertions only if the adapter signature changes; otherwise no patch-target change needed. Verify `test_silent_on_ollama_failure`, `test_writes_title_on_success`, `test_no_op_on_empty_inputs` stay green.
- [ ] `tests/unit/test_memory_title_writer_paths.py` — UPDATE (likely no change): patches `_do_generate` / module wiring, not transport. Confirm green after refactor; no seam moved at this layer.
- [ ] `tests/unit/test_knowledge_indexer.py` — UPDATE: `test_summarize_prefers_ollama` and `test_summarize_falls_back_to_haiku_when_ollama_unavailable` patch `tools.knowledge.indexer._summarize_via_ollama`. Keep `_summarize_via_ollama` as a delegating adapter so these patches stay valid; confirm green. Optionally add a test patching `tools.ollama_client.generate` to prove delegation.
- [ ] `tests/unit/test_email_cs_triage.py` — UPDATE: `_stub_ollama()` stubs `sys.modules["ollama"]` and triage calls `ollama.chat`. Re-point the stub to patch the new seam (`tools.ollama_client.chat`, or the name triage imports). All eight escalation/parse/validation cases must stay green.
- [ ] `tests/integration/test_email_cs_handler.py` — UPDATE (likely no change): stubs `triage_local` at the handler level, above the transport seam. Confirm green; transport change is invisible here.
- [ ] `tests/unit/test_ollama_consolidation.py` — NO CHANGE (verify green): covers #1636 config layer only (`OLLAMA_CLASSIFIER_MODEL`, `ensure_generation_model`, settings defaults, /update gate). This transport refactor must not break it. Note: `test_no_gemma_literal_in_indexer` asserts no `gemma4` literal in the indexer — keep the `gemma4:31b-cloud` fallback literal in `ollama_client.resolve_config()` only, NOT in the indexer, so this test stays green.
- [ ] NEW: `tests/unit/test_ollama_client.py` — CREATE: unit tests for `resolve_config()` (settings-present and settings-absent fallback), `generate()` (success returns text, failure returns `None` + DEBUG log), `chat()` (success returns content, failure re-raises). All stub the `ollama.Client` — no live server.

## Rabbit Holes

- **Unifying the two timeouts (5.0 vs 8.0).** Tempting to "clean up" by making both
  generate callers share one timeout. That is a behavior change, not a refactor —
  out of scope. Preserve each caller's existing timeout exactly.
- **Adding retry/backoff/headers to the shared client.** The issue lists these as
  *future* things that would now be easy — do NOT add them now. Behavior-preserving
  only.
- **Moving `ensure_generation_model()` or `<private>` strip into the client.**
  Explicitly forbidden by the issue — caller-specific safety policy.
- **Switching triage's `chat` to a `None`-returning contract** to "match" generate.
  Triage's escalate-on-exception is individually tested; keep `chat()` raising.
- **Deleting the caller-local adapter functions** to force every test to patch the
  new module. Keeping the thin adapters preserves existing patch targets and
  minimizes test churn — that is the lower-risk path for a behavior-preserving refactor.

## Risks

### Risk 1: `ollama` package `Client.generate`/`Client.chat` response shape differs from the raw HTTP JSON the urllib code parsed.
**Impact:** A wrong attribute access (`.response` vs `["response"]`) returns `None`/raises spuriously, silently degrading title-gen and indexer summaries.
**Mitigation:** Unit tests stub `ollama.Client` returning the typed `GenerateResponse`/`ChatResponse` shapes; assert text extraction. Verified package exposes `.generate`/`.chat` on `Client` (spike-1).

### Risk 2: Triage timing behavior changes if the shared `chat()` imposes a timeout the module-level `ollama.chat()` did not.
**Impact:** Triage could start timing out (and escalating) on slow-but-valid classifications it previously waited on.
**Mitigation:** Do not pass a `timeout_s` for the triage path — leave the `Client` at the package default, matching today's `ollama.chat()` default. Documented in Technical Approach.

### Risk 3: `test_no_gemma_literal_in_indexer` breaks if the `gemma4:31b-cloud` fallback literal lands in the indexer adapter.
**Impact:** Red unit test, blocks merge.
**Mitigation:** The literal lives ONLY in `ollama_client.resolve_config()`. The indexer adapter calls `resolve_config()` for its model; no literal in `indexer.py`.

## Race Conditions

No race conditions identified. All three call sites are independent single-shot HTTP
requests. The title generator runs its call in a daemon thread, but each invocation
constructs its own `ollama.Client` and shares no mutable state with the others — the
new module holds no module-level mutable state (config is resolved per call, clients
are constructed per call). Operations are effectively stateless from the transport's
perspective.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The consolidation
is self-contained: one new module, three delegating call sites, test re-pointing, and
docs. No external action, no ordered deploy, no destructive operation.

## Update System

No update system changes required — this is a purely internal refactor. No new
dependency, config file, env var, or service is introduced. The `ollama>=0.3.0`
dependency is already declared and propagated by existing `/update` machinery.
`pyproject.toml` is unchanged (or only a comment is touched).

## Agent Integration

No agent integration required — this is an internal transport refactor with no new
agent-facing surface. None of the three call sites is a CLI entry point or a
bridge-invoked path that changes shape; they remain internal functions called by the
memory save path, the knowledge indexer, and the email-CS handler respectively. No
`pyproject.toml [project.scripts]` entry, no `.mcp.json` change, no bridge import
change.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/ollama-client.md` documenting the single internal Ollama
      transport module: `resolve_config()` / `generate()` / `chat()` contracts (None-on-failure
      vs raise), which callers delegate to it, and the "config literals live here only" rule.
- [ ] Add an entry to `docs/features/README.md` index table for the new doc.
- [ ] Update any reference in `docs/features/subconscious-memory.md` (title generator)
      and the knowledge-indexer / email-CS docs that describe the old per-module transport,
      pointing them at `ollama-client.md`. (Surgical: only if such transport descriptions exist.)

### External Documentation Site
- [ ] N/A — repo has no external docs site for this layer.

### Inline Documentation
- [ ] Module docstring on `tools/ollama_client.py` stating it is the sole owner of
      Ollama HTTP transport + config resolution, and the None-vs-raise contract split.
- [ ] Docstrings on `generate()` (fail-silent → `None`) and `chat()` (raises) making
      the differing error contracts explicit.

## Success Criteria

- [ ] A single internal module (`tools/ollama_client.py`) owns Ollama HTTP transport + config resolution.
- [ ] `localhost:11434` literal and `/api/generate` URL each appear in exactly one place (`grep -rn "localhost:11434\|/api/generate" tools/` shows a single source — the new module).
- [ ] `gemma4:31b-cloud` fallback literal appears in exactly one place (`resolve_config()`); `test_no_gemma_literal_in_indexer` stays green.
- [ ] `title_generator.py`, `indexer.py`, and `email_cs/triage.py` all delegate transport to the new module.
- [ ] Each caller's existing fallback/escalation behavior is preserved (title: skip-on-None; indexer: Haiku fallback on None; triage: escalate-on-exception).
- [ ] No dependency added, removed, or version-bumped (`git diff pyproject.toml` shows no dependency line change).
- [ ] Tests pass (`/do-test`) — including unchanged `test_ollama_consolidation.py`.
- [ ] Documentation updated (`/do-docs`).
- [ ] `python -m ruff check` and `python -m ruff format` clean.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The
lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (ollama-client)**
  - Name: `ollama-client-builder`
  - Role: Create `tools/ollama_client.py` and re-point the three call sites + tests
  - Agent Type: builder
  - Resume: true

- **Validator (ollama-client)**
  - Name: `ollama-client-validator`
  - Role: Verify behavior preservation, literal-deduplication, and test greenness
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `ollama-client-doc`
  - Role: Author `docs/features/ollama-client.md` + index entry
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

See template list. This Small refactor uses `builder`, `validator`, `documentarian`.

## Step by Step Tasks

### 1. Create the shared client module
- **Task ID**: build-ollama-client
- **Depends On**: none
- **Validates**: tests/unit/test_ollama_client.py (create)
- **Informed By**: spike-1 (Client honors per-client timeout via httpx; module-level fns do not accept timeout), spike-2 (chat must raise to preserve triage escalation)
- **Assigned To**: ollama-client-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/ollama_client.py` with `resolve_config()`, `generate()` (fail-silent → `None`), `chat()` (raises).
- Construct `ollama.Client(host=..., timeout=...)` per call; extract `.response` / `["message"]["content"]`.
- House the `localhost:11434`, `gemma4:31b-cloud`, and default-timeout literals here ONLY.
- Write `tests/unit/test_ollama_client.py` stubbing `ollama.Client` (no live server).

### 2. Re-point title_generator
- **Task ID**: build-title-generator
- **Depends On**: build-ollama-client
- **Validates**: tests/unit/test_memory_title_generator.py, tests/unit/test_memory_title_writer_paths.py
- **Assigned To**: ollama-client-builder
- **Agent Type**: builder
- **Parallel**: false
- Make `_resolve_ollama_config()` delegate to `ollama_client.resolve_config()`; make `_post_ollama_generate()` delegate to `ollama_client.generate()`.
- Keep `<private>` strip + `ensure_generation_model()` gate at the caller.
- Remove now-unused `urllib`/`json` imports from `title_generator.py`.
- Confirm existing patch targets still resolve; update assertions only if signatures shift.

### 3. Re-point indexer
- **Task ID**: build-indexer
- **Depends On**: build-ollama-client
- **Validates**: tests/unit/test_knowledge_indexer.py, tests/unit/test_ollama_consolidation.py
- **Assigned To**: ollama-client-builder
- **Agent Type**: builder
- **Parallel**: false
- Make `_summarize_via_ollama()` delegate to `ollama_client.generate(...)`, passing `timeout_s=8.0` explicitly (preserve behavior).
- Ensure NO `gemma4`/`localhost:11434`/`/api/generate` literal remains in `indexer.py` (keep `test_no_gemma_literal_in_indexer` green).
- Remove inline `urllib`/`json` imports from `_summarize_via_ollama`.

### 4. Re-point triage
- **Task ID**: build-triage
- **Depends On**: build-ollama-client
- **Validates**: tests/unit/test_email_cs_triage.py, tests/integration/test_email_cs_handler.py
- **Assigned To**: ollama-client-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace inline `ollama.chat(...)` with `ollama_client.chat(messages=..., model=OLLAMA_CLASSIFIER_MODEL, options={"temperature": 0})`.
- Do NOT pass a timeout (preserve triage's package-default timing).
- Keep the existing try/except → `escalate_triage(...)` (chat raises, caller catches).
- Re-point the test stub from `sys.modules["ollama"]` to the new seam.

### 5. Validate refactor
- **Task ID**: validate-ollama-client
- **Depends On**: build-title-generator, build-indexer, build-triage
- **Assigned To**: ollama-client-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all five affected test files + the new test; confirm green.
- `grep -rn "localhost:11434\|/api/generate" tools/` returns a single source file.
- `git diff pyproject.toml` shows no dependency change.
- `python -m ruff check` and `python -m ruff format --check` clean.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-ollama-client
- **Assigned To**: ollama-client-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/ollama-client.md`; add index entry in `docs/features/README.md`.
- Surgically update any transport descriptions in memory/indexer/email-CS docs to point at the new module.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: ollama-client-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full affected-test set + ruff; verify every Success Criterion; confirm doc exists and is indexed.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Affected tests pass | `pytest tests/unit/test_ollama_client.py tests/unit/test_memory_title_generator.py tests/unit/test_memory_title_writer_paths.py tests/unit/test_knowledge_indexer.py tests/unit/test_email_cs_triage.py tests/integration/test_email_cs_handler.py tests/unit/test_ollama_consolidation.py -q` | exit code 0 |
| Single transport source | `grep -rln "/api/generate" tools/ \| wc -l` | output contains 1 |
| Single host literal source | `grep -rln "localhost:11434" tools/ \| wc -l` | output contains 1 |
| No gemma literal in indexer | `grep -c "gemma4" tools/knowledge/indexer.py` | output contains 0 |
| No dependency change | `git diff --stat pyproject.toml` | output contains 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

All open questions from the issue were resolved by spikes against the installed
`ollama` package and codebase:

1. **One transport or two?** → RESOLVED: One. Standardize all three on the `ollama`
   package via `ollama.Client(host=..., timeout=...)`. Module-level `generate()`/`chat()`
   do not accept a `timeout`, but `Client(timeout=...)` honors it (httpx passthrough,
   spike-1). Hand-rolled urllib is removed.
2. **Error contract?** → RESOLVED: Split. Shared `generate()` returns `None` on
   failure (both generate callers want this); shared `chat()` raises (triage's
   escalate-on-exception is preserved by its existing try/except, spike-2).
3. **Where does the module live?** → RESOLVED: `tools/ollama_client.py`.
4. **`<private>` strip + `ensure_generation_model()` gate?** → Stay at the title
   caller (per the issue; not moved into the shared client).

No outstanding questions require supervisor input. Ready for critique.
