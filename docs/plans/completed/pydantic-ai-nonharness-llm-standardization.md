---
status: docs_complete
type: chore
appetite: Large
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/1925
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-12T14:30:38Z
---

# PydanticAI standardization for non-harness LLM calls

## Problem

The system makes two categories of LLM calls: (1) **harness** calls that ARE Claude Code sessions (skills, hooks, tools, resume), driven through `claude -p`; and (2) **non-harness** calls that classify, extract, judge, and refine text. The non-harness calls are the subject of this plan.

**Current behavior:**
Every non-harness call site hand-rolls its own provider client. Most construct `anthropic.Anthropic` / `anthropic.AsyncAnthropic` inline and pin Haiku directly (`agent/intent_classifier.py`, `agent/memory_extraction.py`, `bridge/read_the_room.py`, `bridge/session_router.py`). Three sites (`bridge/routing.py`, `bridge/agent_catchup.py`, `tools/email_cs/triage.py`) still call `ollama.chat()` directly with a Haiku fallback. There is no shared wrapper, no shared structured-output contract, and no seam to swap models. Each site re-implements auth, timeout, retry, and output-parsing quirks (see the `_looks_like_refusal` / `json.loads`-shape workarounds in `agent/memory_extraction.py`). Switching a model, or making any of these calls model-agnostic, means editing N call sites.

**Desired outcome:**
All non-harness LLM calls route through a single PydanticAI-based wrapper. Each call declares a typed `output_type` (BaseModel) and gets schema-validated output with automatic retry. The model is a wrapper-level default (Haiku), swappable per-call and per-provider without touching call sites. Adding a new classifier means writing a prompt + an output model, not a new client.

## Freshness Check

**Baseline commit:** `35301b579a38844764f679e4da647d1bc84d27d3`
**Issue filed at:** 2026-07-06 (issue #1925, still open)
**Disposition:** Major drift — the issue body's scope is stale in three places; re-scoped below and confirmed with the invoking supervisor.

**File:line references re-verified:**
- `agent/sdk_client.py` two-transport split (`:1512`, `:2340`) — **still present**, but explicitly **out of scope for #1925** (see No-Gos). The `claude_code_sdk` / `ClaudeSDKClient` deletion was folded into **#2000's** PR (dead-code excision in the shared `sdk_client.py`) to avoid a rebase collision on that file. This plan does **not** touch `agent/sdk_client.py`.
- `agent/memory_extraction.py` Haiku extraction path (`async with anthropic.AsyncAnthropic` at `:288`, `_llm_call` helper) — **still present**; confirmed a non-harness call site and in scope. **#1829's LLM refusal-detector complement has MERGED** and is now live in this module at `:122` — no longer a parallel lane; the migration must simply preserve the merged detector.
- `bridge/routing.py:675`, `bridge/agent_catchup.py:187`, `tools/email_cs/triage.py:90` — direct `ollama.chat()` calls — **still present**.

**Cited sibling issues/PRs re-checked:**
- **#1923** ("Drop ollama entirely: replace bridge routing + email triage classifier calls") — **CLOSED 2026-07-10 as NOT_PLANNED** (abandoned; no PR ever referenced it). The issue-body claim that "#1923 solves the ollama sites" is FALSE: the ollama call sites still exist on the three files above. This is the primary stale premise. The ollama runtime purge / model-pull cleanup / nightly canary work #1923 also owned did **not** happen. (Nightly-canary spike **#1854** is separately CLOSED, so the canary concern is moot regardless.)
- **#2000** ("Phase 2: HarnessAdapter seam") — **CLOSED COMPLETED 2026-07-11** (verified 2026-07-12). Its harness-half PR merged; the `claude_code_sdk` / two-transport removal and any `agent/sdk_client.py` excision are **its** work and have shipped. #1925 stays the non-harness half and does not touch `sdk_client.py`. The prior "coordinate, don't duplicate" gate is resolved — there is no live sibling lane to race.
- **#1829** ("Memory extraction: LLM-based refusal-detector") — **CLOSED COMPLETED 2026-07-11** (verified 2026-07-12). The refusal detector merged to main and is live in `agent/memory_extraction.py:122`. **The build-ordering wait is resolved** — there is no concurrent lane on this file. The migration preserves the already-merged detector; the former `[ORDERED]` gate no longer applies (see No-Gos, Risk 1, Race 1).
- **#1924** (granite PTY teardown, the issue's cited prerequisite) — the teardown has landed; the harness is the sole role-execution path. This removes the "#1924 must land first" gate.

**Commits on main since issue was filed (touching referenced files):**
- Memory-extraction fixes #2016/#1822 merged (PRs #2023, #1831) and #1829's refusal detector merged — all touched `agent/memory_extraction.py` but none change the LLM-call *shape* this plan wraps. The file is no longer under concurrent development (all sibling lanes closed); the migration reads the current main state and preserves the merged detector.

**Active plans in `docs/plans/` overlapping this area:** `harness-cross-compat.md` (#1996/#2000 tracking) — **#2000 has since merged**. It owned the **harness half** and the `sdk_client.py` excision, now shipped. No overlap with the non-harness wrapper scope; this plan still does not touch `sdk_client.py`.

**Notes:** The re-scoping (PydanticAI half only; harness/SDK-deletion → #2000; ollama premise corrected) was directed by the invoking supervisor and is encoded throughout. The ollama-site scope decision is **RESOLVED — migrate now, option A** (human correction at commit `8cbf0a22`): #1923 closed NOT_PLANNED so the three surviving ollama chat-classifier sites are this plan's to migrate (task 3). A second re-verification (2026-07-12) confirmed both former build-gate siblings — #2000 and #1829 — are now CLOSED COMPLETED, lifting the build-ordering wait entirely.

## Prior Art

- **#1923** ("Drop ollama entirely") — CLOSED NOT_PLANNED. Intended to replace the ollama classifier calls with small Claude calls and retire the ollama runtime. Never implemented. Its own recon noted the synergy: "should be implemented AS a PydanticAI call — model-agnostic solves it by construction." This plan is the vehicle that makes that true for the call-site half.
- **#2000 / `harness-cross-compat.md`** — **CLOSED COMPLETED 2026-07-11**. Owned the harness adapter seam and the `sdk_client.py` two-transport cleanup; that work has shipped. #1925 stays the non-harness half and does not touch `sdk_client.py` — no live sibling lane to coordinate with.
- **#1829** — **CLOSED COMPLETED 2026-07-11**. Its Haiku-based refusal detector merged to `agent/memory_extraction.py` (live at `:122`). The former file-overlap wait is resolved; this plan's migration preserves the merged detector rather than sequencing behind a build event.
- **hotfix #1055 / #1111** — established the shared `agent/anthropic_client.py` (`semaphore_slot` / `anthropic_slot`, `AsyncAnthropic` + double-timeout) that most non-harness Anthropic sites already use. The PydanticAI wrapper must preserve these rate-limit and timeout invariants, not regress them.

## Research

External research on PydanticAI to ground the wrapper design.

**Queries used:**
- "PydanticAI model-agnostic Agent Anthropic OpenAI provider configuration 2026"
- "pydantic-ai structured output result_type BaseModel classification example"

**Key findings:**
- **Model-agnostic by construction** — a single PydanticAI `Agent` is portable across vendors by swapping the `Model` class (`AnthropicModel`, `OpenAIChatModel`, `GoogleModel`, …); auth/connection lives in a `Provider` passed to the Model. Supports Anthropic, OpenAI, **Ollama**, LiteLLM, and others. Source: https://ai.pydantic.dev/models/overview/ and https://ai.pydantic.dev/models/anthropic/ . This validates the "one wrapper, swappable model" design and means the surviving ollama sites can be expressed as the same wrapper with an Ollama-backed model if local inference is still wanted.
- **Typed structured output** — pass `output_type=SomeBaseModel` (formerly `result_type`) to the Agent; PydanticAI validates the LLM output against the schema and auto-reprompts on mismatch. Multiple output types are allowed (`output_type=[Model, str]`). Source: https://ai.pydantic.dev/output/ . This replaces the hand-rolled `json.loads`-shape repair currently scattered across `agent/memory_extraction.py` and the string-parsing (fence-strip + `json.loads`) verdict extractor in `bridge/session_router.py`. It does **not** target `bridge/read_the_room.py`, whose `:429` call already uses forced tool-calling for schema-validated output (SKIP — see inventory Disposition).
- **Custom AsyncClient injection** — `AnthropicProvider` / `OpenAIProvider` accept a caller-supplied async client (`AnthropicProvider(anthropic_client=...)`), so the wrapper hands PydanticAI a client *it* constructs rather than letting PydanticAI build an unmanaged one. Source: https://ai.pydantic.dev/models/anthropic/ . **Important caveat (see Spike Results):** `agent/anthropic_client.py` exposes no single long-lived shared client to inject — `anthropic_slot()` builds a *fresh, ephemeral* `AsyncAnthropic` per call, and `semaphore_slot()` gates concurrency while the caller constructs its own client with a *site-specific timeout*. So the wrapper injects a *freshly-constructed-per-call* client built inside a held semaphore slot, not one reused module-level client. The reconciled design is in Spike Results.

## Spike Results

### spike-1: How does the wrapper preserve the #1055/#1111 rate-limiter + per-site timeout when there is no shared client to inject?
- **Assumption tested:** "The wrapper injects *the* existing rate-limited `AsyncAnthropic` client into PydanticAI's `AnthropicProvider`."
- **Method:** code-read of `agent/anthropic_client.py` (full file) and `agent/memory_extraction.py:300–340`.
- **Result — assumption FALSE as stated; reconciled below.** `agent/anthropic_client.py` deliberately holds **no** long-lived client. It exposes two context managers over one module-level `asyncio.Semaphore`:
  - `anthropic_slot()` — acquires a slot, then constructs a **fresh, ephemeral** `AsyncAnthropic(api_key=...)` (no timeout kwarg) and yields it; releases the slot on exit. The docstring is explicit: "each `anthropic_slot()` call creates a new `AsyncAnthropic` instance so httpx connection pools are not shared across call sites."
  - `semaphore_slot()` — acquires the slot **only**; the caller constructs its own client. `agent/memory_extraction.py` uses this because it needs a **site-specific double-timeout**: `async with anthropic.AsyncAnthropic(timeout=_EXTRACTION_SDK_TIMEOUT)` (SDK-level) wrapped in `asyncio.wait_for(..., timeout=_EXTRACTION_HARD_TIMEOUT)` (outer hard cap). Different sites carry different timeouts.
- **Reconciled design — the wrapper is a per-call slot, not a single injected client.** `run_typed(prompt, output_type, *, model=MODEL_FAST, sdk_timeout=..., hard_timeout=...)` performs, on **every** invocation:
  1. `async with semaphore_slot():` — hold the shared semaphore for the **entire** PydanticAI `Agent.run`, so concurrency gating covers the whole call (matching how `semaphore_slot()` is used today).
  2. Inside the slot, construct a **fresh** `async with anthropic.AsyncAnthropic(api_key=..., timeout=sdk_timeout)` (per-call, per-site timeout; `async with` preserves the #1055 httpx cleanup).
  3. Inject **that** client: `AnthropicProvider(anthropic_client=client)` → `AnthropicModel(model, provider=...)` → `Agent(model, output_type=...)`.
  4. `await asyncio.wait_for(agent.run(prompt), timeout=hard_timeout)` when the site wants the outer hard cap (memory-extraction does; others may pass `hard_timeout=None`).
  5. Release the slot on `__aexit__`.
  So "the shared rate-limited client" is preserved **as a pattern** (shared semaphore + fresh per-call client + `async with` cleanup + per-site timeout), not as a single reused object. Timeout is a **per-call parameter**, not a wrapper constant.
- **Confidence:** high (grounded in the actual `agent/anthropic_client.py` and `memory_extraction.py` source at baseline).
- **Impact if false:** if PydanticAI does not honor an externally-constructed client's `timeout`, the wrapper falls back to enforcing the cap purely via the outer `asyncio.wait_for` (step 4), which already bounds wall-clock regardless of the SDK kwarg — build must assert this with a timeout test.

### spike-2: Does `AnthropicProvider` accept an externally-constructed `AsyncAnthropic`?
- **Assumption tested:** "`AnthropicProvider(anthropic_client=...)` is a supported constructor arg."
- **Method:** web-research (PydanticAI Anthropic model docs, captured in Research above).
- **Result:** yes — `AnthropicProvider` accepts a caller-supplied async client. Confirmed against https://ai.pydantic.dev/models/anthropic/ .
- **Confidence:** medium-high (doc-confirmed; build pins a known-good `pydantic-ai` version and a unit test asserts the injected client is the one used).
- **Impact if false:** the wrapper cannot inject its slot-managed client and the whole rate-limit-preservation approach fails — this is why the **first build task is the wrapper + an injection test**, before any call-site migration.

## Data Flow

1. **Entry point** — a non-harness caller (e.g. `bridge/routing.py::classify_message`, `agent/memory_extraction.py::extract_observations`, `bridge/session_router.py`'s semantic router) needs a typed decision from an LLM. (`bridge/read_the_room.py` is deliberately not in this list — it is SKIP, already returning a schema-validated verdict via forced tool-calling.)
2. **Wrapper call** — the caller invokes the new wrapper (`agent/llm/…`), passing a prompt + an `output_type` BaseModel (and optionally a model override + per-site timeouts). The wrapper selects the configured default model (Haiku) and, per call, acquires the shared `semaphore_slot()` and injects a freshly-constructed rate-limited async client into the `Provider` (see Spike Results spike-1).
3. **PydanticAI Agent.run** — PydanticAI sends the request, receives the response, validates it against `output_type`, and auto-reprompts once on schema mismatch.
4. **Typed result** — the caller receives a validated BaseModel instance (e.g. `RoutingDecision(action="respond")`, `ExtractionResult(observations=[...])`) instead of raw text it must parse. Failure (timeout, exhausted retry) returns the caller's declared conservative default (respond / escalate / send) — the same fail-safe posture the current sites implement by hand.
5. **Output** — the decision flows onward exactly as today (route the message, save the memory, send/hold the draft, escalate the email).

## Architectural Impact

- **New dependency:** `pydantic-ai` (not currently in `pyproject.toml`; only transitive `pydantic` is present). This is the one meaningful new third-party surface.
- **Interface changes:** each migrated call site's internal LLM helper changes signature to return a typed BaseModel; public behavior (the routing/extraction/verdict decision) is preserved.
- **Coupling:** decreases. N bespoke client constructions collapse to one wrapper. Model choice becomes a single config point (`config/models.py` default + wrapper).
- **Data ownership:** unchanged. The wrapper owns client construction and output validation; callers still own their prompts and their fail-safe defaults.
- **Reversibility:** medium. The wrapper is additive; migration is per-site, so a single site can be reverted independently. Removing `pydantic-ai` after full migration would require re-inlining clients.

## Appetite

**Size:** Large

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (wrapper API design sign-off; per-site migration review)
- Review rounds: 1-2 (wrapper API design review; per-site migration review)

The coding is mechanical once the wrapper exists; the cost is breadth (7+ call sites, each with its own fail-safe semantics and tests). The prior concurrency coordination with #1829 and #2000 is **no longer a factor** — both closed COMPLETED 2026-07-11, so there is no sibling lane to sequence against.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` present | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | Default (Haiku) model backend for the wrapper |
| `pydantic-ai` installed | `python -c "import pydantic_ai"` | The wrapper's framework (added by this plan) |

Run via `python scripts/check_prerequisites.py docs/plans/pydantic-ai-nonharness-llm-standardization.md`.

## Solution

### Key Elements

- **`agent/llm/` wrapper package** — a thin PydanticAI facade exposing a typed call helper (e.g. `run_typed(prompt, output_type, *, model=DEFAULT_FAST, sdk_timeout=..., hard_timeout=...) -> BaseModel`). Owns model/provider selection and, **per call**, acquires the shared `semaphore_slot()`, constructs a fresh `AsyncAnthropic(timeout=sdk_timeout)` inside it, injects that client into `AnthropicProvider(anthropic_client=...)`, and optionally wraps `Agent.run` in `asyncio.wait_for(hard_timeout)` — so the #1055/#1111 semaphore + per-site double-timeout invariants are preserved as a **per-call slot pattern**, not a single reused client (see Spike Results spike-1 for the reconciliation).
- **Per-call output models** — one BaseModel per **MIGRATE** call site's decision (`RoutingDecision`, `SessionRouteDecision`, `ExtractionResult`, `EmailTriageDecision`, `IntentClassification`, and the catch-up judge verdict). These replace ad-hoc string/JSON parsing. `RoomVerdict` is **not** in this list — read_the_room is SKIP (already tool-calling) and keeps its own existing `RoomVerdict` type.
- **Model config seam** — the wrapper's default fast model reads `config/models.py` (`MODEL_FAST`/`HAIKU`), so a model swap is one edit. Model-agnostic by construction (PydanticAI Model classes are swappable).
- **Call-site migrations** — each non-harness site swaps its inline client for a wrapper call, keeping its existing conservative fail-safe default on error.

### Flow

Caller needs a decision → calls `agent/llm` wrapper with prompt + `output_type` → PydanticAI validates output against the schema (auto-retry once) → caller receives a typed BaseModel (or its conservative default on failure) → decision flows onward unchanged.

### Technical Approach

- **Wrapper first, migrate second.** Land the `agent/llm/` wrapper + its unit tests before touching any call site. Each site migration is then a small, independently reviewable, independently revertible change.
- **Preserve fail-safe posture per site.** Every current site has a deliberate default on LLM failure (routing → respond; read-the-room → send; email triage → escalate; extraction → empty/skip). The wrapper surfaces failures as exceptions or a sentinel; each migrated site keeps its own default. Do not centralize the default — the conservative choice is site-specific.
- **Inject a per-call slot-managed client (NOT a single shared object).** `agent/anthropic_client.py` holds no long-lived client — `anthropic_slot()` builds a fresh client per call and `semaphore_slot()` gates concurrency while the caller builds its own timeout-carrying client. The wrapper follows the `semaphore_slot()` pattern: **per call**, hold the shared semaphore for the whole `Agent.run`, construct a fresh `async with AsyncAnthropic(timeout=<per-site>)` inside the slot, and pass it to `AnthropicProvider(anthropic_client=...)`. This preserves the #1055 httpx-cleanup + #1111 semaphore + per-site double-timeout invariants without inventing a shared client that does not exist. Full reconciliation and the `run_typed` timeout parameters are in **Spike Results (spike-1)**.
- **`intent_classifier.py` returns a dict via `dataclasses.asdict`, not a dataclass.** `agent/intent_classifier.py:230` calls `dataclasses.asdict(parsed)` and the function's cached public contract is a **dict**. A PydanticAI `output_type` returns a BaseModel, on which `dataclasses.asdict` raises `TypeError`. The migration MUST swap `dataclasses.asdict(parsed)` → `parsed.model_dump()` so the dict-returning public contract (and its cache shape) is preserved. Do not change the caller-visible return type in this migration.
- **Confirmed non-harness inventory** (grep-verified at baseline `35301b5`). The **Disposition** column is the single source of truth — Step 2, Step 3, and Test Impact all follow it. A site is **SKIP** when its current LLM call already returns a schema-validated result via forced tool-calling (re-wrapping it churns working code for no gain — see Rabbit Holes); it is **MIGRATE** when it hand-rolls `json.loads` / string parsing (or `ollama.chat()`), which is exactly what the wrapper replaces.
  | Site | Current mechanism | Decision type | Disposition |
  |------|-------------------|---------------|-------------|
  | `agent/intent_classifier.py:219` | sync `anthropic.Anthropic` (Haiku), hand-rolled parse | intent classification | **MIGRATE** |
  | `agent/memory_extraction.py:288` (`_llm_call`) | `AsyncAnthropic` (Haiku), hand-rolled `json.loads`-shape repair | observation extraction | **MIGRATE** (preserve #1829's merged refusal detector at `:122`) |
  | `bridge/routing.py:675,927,1122` | `ollama.chat()` + Haiku fallback | message routing | **MIGRATE** |
  | `bridge/read_the_room.py:429` | `AsyncAnthropic` **forced `tool_choice={"type":"tool","name":"room_verdict"}`** — schema-validated structured output | send/hold verdict | **SKIP** — already-structured tool-calling; the wrapper adds nothing (see Rabbit Holes). `RoomVerdict` stays read_the_room's own return type; do not touch this file. |
  | `bridge/session_router.py:125` | `anthropic_slot` (Haiku), hand-rolled `json.loads` + fence-strip | session-route classification | **MIGRATE** |
  | `bridge/agent_catchup.py:187,197` | `ollama.chat()` + Haiku fallback | catch-up judge | **MIGRATE** |
  | `tools/email_cs/triage.py:90` | `ollama_client.chat()` (from `tools.ollama_client`) | email triage | **MIGRATE** |
  | `agent/session_completion.py:495`, `bridge/promise_gate.py:510` | `AsyncAnthropic` | candidate sites | **CONFIRM AT BUILD** — MIGRATE only if confirmed non-harness AND not already forced tool-calling; otherwise SKIP |
- **`session_completion.py` drafter (`:755`,`:817`) uses `get_response_via_harness`** — that is the harness path and is **out of scope** (do not migrate).
- **Explicitly do NOT touch `agent/sdk_client.py`** — the `claude_code_sdk` deletion is #2000's PR.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Each migrated call site currently swallows LLM failure into a conservative default (e.g. `bridge/routing.py:715` "default to respond", `tools/email_cs/triage.py:102` "escalate"). Each must retain a test asserting the observable default is returned (and the failure is logged) when the wrapper raises.
- [ ] The wrapper itself must log (not silently swallow) provider errors and schema-validation exhaustion before surfacing them to callers.

### Empty/Invalid Input Handling
- [ ] Preserve `agent/memory_extraction.py`'s whitespace-dominant short-circuit (`test_whitespace_dominant_input_skips_llm_call`) — the wrapper must not be called for inputs the site already skips.
- [ ] Add wrapper tests for empty-string / None / whitespace-only prompts (assert a clear error, no hang).
- [ ] Verify a schema-invalid LLM response triggers PydanticAI's single auto-retry, then the site's conservative default — not an infinite loop.

### Error State Rendering
- [ ] For user-visible paths (routing decides whether the agent responds; read-the-room decides send/hold), test that a wrapper failure yields the conservative default so the user still gets a response rather than silence.

## Test Impact

- [ ] `tests/unit/test_intent_classifier*.py` — UPDATE: intent classification now returns a typed `IntentClassification`; assert the model instance, keep the same intent semantics.
- [ ] `tests/unit/test_memory_extraction*.py` (incl. `test_whitespace_dominant_input_skips_llm_call`, refusal-detector tests) — UPDATE: preserve short-circuit and refusal behavior; assert typed `ExtractionResult`. **Coordinate with #1829's test additions** on this file.
- [ ] `tests/unit/test_routing*.py` / bridge routing tests — UPDATE: assert typed `RoutingDecision`, unchanged fail-safe (default respond), preserved pre-LLM fast-paths (Risk 5), and **decision parity on fixtures** (granite→Haiku quality shift); mocked ollama expectations are replaced by wrapper mocks.
- [ ] `tests/unit/test_read_the_room*.py` — **NO CHANGE**: read_the_room is SKIP (already forced tool-calling), so its call is not migrated and its tests stay as-is. Listed here to make the SKIP disposition explicit in the test audit.
- [ ] `tests/unit/test_session_router*.py` — UPDATE: assert typed `SessionRouteDecision`.
- [ ] `tests/*/test_agent_catchup*.py` — UPDATE: assert typed judge verdict AND **decision parity on fixtures** — the granite→Haiku model swap changes decision *quality*, not just cost/latency, so assert the verdict matches expected labels across a representative fixture set, not merely that the call returns.
- [ ] `tests/*/test_email*triage*.py` — UPDATE: assert typed `EmailTriageDecision`, escalate-on-failure default, AND **decision parity on fixtures** (same granite→Haiku quality-shift rationale as catch-up).
- [ ] New: `tests/unit/test_llm_wrapper.py` — CREATE: wrapper structured-output success, auto-retry on schema mismatch, error surfacing, per-call semaphore-slot acquisition, injected-client-took-effect, and outer `asyncio.wait_for` hard-timeout bound (per Spike Results spike-1).

Exact test paths are confirmed during build via `grep -rl` on each touched module; the dispositions above are the audit.

## Rabbit Holes

- **Rewriting harness calls.** The `claude -p` path and `agent/sdk_client.py` are out of scope. Do not "while we're here" refactor them — that is #2000.
- **Turning this into the ollama-purge project.** Removing the ollama runtime, model pulls, and canary machine-wide was #1923's abandoned operational scope. Migrating the three code call sites is bounded; uninstalling ollama from machines is not this plan's job (see No-Gos).
- **A universal "one signature fits all" LLM function.** Each site has genuinely different output shapes and fail-safe defaults. Force-fitting one mega-signature will cost more than seven small typed models.
- **Streaming / token-level UX.** These are non-harness one-shot classification/extraction calls; none need streaming. Do not add it.
- **Provider fallback chains.** PydanticAI supports fallback models, but adding a routing/ollama fallback ladder is scope creep — default to Haiku, keep the site's error default.
- **Re-wrapping already-structured tool-calling sites.** A call site that already gets a validated, typed result via Anthropic tool-calling (a structured-output schema) gains nothing from being re-expressed through the wrapper — it just churns working code and inflates scope. **The concrete case is `bridge/read_the_room.py:429`**, which forces `tool_choice={"type":"tool","name":"room_verdict"}` and is therefore already schema-validated — its Disposition is SKIP in the inventory. During the inventory audit, skip any site whose current LLM call is already schema-validated tool-calling; the wrapper is for the sites that hand-roll `json.loads`/string parsing, not for ones that are already typed.
- **Preserving per-site analytics counters.** Some sites increment bespoke metrics/log counters around their LLM call. These are **not** required to survive the migration byte-for-byte — matching observable *decisions* and fail-safe defaults is the parity bar, not reproducing every counter. Do not spend scope re-plumbing per-site analytics; if a counter is genuinely load-bearing for a dashboard, note it and keep it, otherwise let it go.

## Risks

### Risk 1: The migration clobbers #1829's merged refusal detector in `agent/memory_extraction.py`
**Impact:** The wrapper rewrite of the extraction call could drop or bypass #1829's LLM refusal-detector complement (live at `:122`).
**Status:** **#1829 CLOSED COMPLETED 2026-07-11 — the former build-ordering wait is resolved.** There is no concurrent lane; the detector is already in main. The remaining risk is a careless rewrite, not a race.
**Mitigation:** Before migrating `agent/memory_extraction.py`, read the merged refusal-detector path (`:122` onward) and preserve it in the wrapped call. A test asserts the refusal detector still fires after migration. No build gate on a merge event is needed.

### Risk 2: PydanticAI regresses the #1055/#1111 rate-limit + timeout invariants
**Impact:** If PydanticAI constructs its own unmanaged `AsyncAnthropic`, the shared semaphore and double-timeout protections are bypassed → event-loop stalls under load (the exact class of bug #1055 fixed).
**Mitigation (reconciled with the real client module — see Spike Results spike-1):** There is no single shared client to inject. The wrapper instead follows `agent/anthropic_client.py`'s `semaphore_slot()` pattern **per call**: hold the shared semaphore for the entire `Agent.run`, construct a fresh `async with AsyncAnthropic(timeout=<per-site>)` inside the slot, inject *that* into `AnthropicProvider(anthropic_client=...)`, and optionally wrap the run in `asyncio.wait_for(hard_timeout)`. Tests must assert (a) the wrapper acquires the shared `semaphore_slot`/semaphore for the whole call, (b) the client PydanticAI uses is the wrapper-constructed one (injection took effect), and (c) a slow provider is bounded by the outer `asyncio.wait_for` hard cap regardless of the SDK timeout kwarg.

**Sizing note — shared-pool contention (accepted risk).** Routing these sites through the shared `agent/anthropic_client.py` semaphore adds two new demand sources to the default 5-slot pool: `intent_classifier` and, more significantly, the `bridge/routing.py` per-inbound-message hot path (Risk 5). Because a slot is now held across PydanticAI's **whole** `Agent.run` — including its single schema-mismatch auto-retry — each wrapped call can occupy a slot noticeably longer than the old one-shot `messages.create`. Under a burst of inbound messages this could starve the pool and back up other Anthropic callers. This is an **accepted risk** for the initial migration, bounded by three existing levers: (a) the pre-LLM fast-paths keep most inbound messages off the wrapper entirely (Risk 5); (b) the per-call `hard_timeout` caps how long any one slot is held; (c) the semaphore size is a single config point in `agent/anthropic_client.py` and can be raised if pool-wait telemetry shows contention. Build should not re-architect the pool; it should preserve the fast-paths and leave the slot count tunable. If contention shows up in practice, raising the pool size or pinning routing to a separate/local model (Risk 5, Open Question 3) is the follow-up — not a blocker for this plan.

### Risk 3: New dependency weight / version drift
**Impact:** `pydantic-ai` pulls its own transitive tree; a bad pin could break installs across machines.
**Mitigation:** Pin a known-good version in `pyproject.toml`; the update system propagates it (see Update System). Smoke-test `import pydantic_ai` in the prerequisite checker.

### Risk 4: Silent behavior change in a fail-safe default
**Impact:** A migrated site's conservative default (respond/escalate/send) subtly changes, causing the agent to go silent or over-escalate.
**Mitigation:** Per-site tests assert the exact default on wrapper failure; migrate one site per commit for isolated review.

### Risk 5: Hot-path routing trades free local ollama for paid, higher-latency cloud calls
**Impact:** `bridge/routing.py::classify_needs_response` (`:675`) runs `ollama.chat()` **per inbound message** — a hot path today served by a free, local, sub-second model. Migrating it to the Haiku-default wrapper turns every inbound-message classification into a paid Anthropic call with network latency. Across the fleet this is a real per-message cost and latency regression, not a one-off.
**Mitigation:** This is an **acknowledged, accepted** trade (the whole point of #1925 is model-agnostic routing; #1923's own recon said the routing classifier "should be implemented AS a PydanticAI call"). To bound the cost/latency: (a) keep the existing cheap fast-paths (`<3` chars, acknowledgment-token set) *before* any LLM call so most messages never hit the wrapper; (b) the wrapper is model-agnostic — Open Question 3 resolves the default, and a cheaper/local PydanticAI model may be pinned for this specific high-frequency site via `run_typed(..., model=...)` without touching the call site again; (c) preserve the conservative default-respond on failure so a slow/failed cloud call never drops a work message. The migration must **not** silently remove the pre-LLM fast-paths.

## Race Conditions

### Race 1: #1925 build vs #1829 build both writing `agent/memory_extraction.py` — RESOLVED
**Location:** `agent/memory_extraction.py` (extraction `_llm_call` path, `:288`–`:930`)
**Status:** **Resolved — #1829 CLOSED COMPLETED 2026-07-11.** Its refusal-detector merged to main (`:122`); there is no second lane to race. The data prerequisite ("#1829's refusal-detector present before this plan rewrites the extraction call") is now satisfied on main.
**Mitigation:** No ordering gate remains. The build reads the merged detector and preserves it (see Risk 1). This entry is retained as a record; the hazard no longer exists.

### Race 2: #1925 vs #2000 both editing `agent/sdk_client.py`
**Location:** `agent/sdk_client.py`
**Trigger:** Both plans edit the shared transport file → rebase collision.
**Data prerequisite:** none.
**State prerequisite:** clean separation of concerns.
**Mitigation:** This plan **does not touch `agent/sdk_client.py`** at all; the `claude_code_sdk` deletion is #2000's PR. No shared file, no race.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2000] Deleting `ClaudeSDKClient` / `claude_code_sdk`, migrating the `ValorAgent` conversational path to the harness, and any edit to `agent/sdk_client.py`. This was #2000's harness-half work and **#2000 CLOSED COMPLETED 2026-07-11** — it has shipped. This plan still does not touch `agent/sdk_client.py`; if the file is already clean of `claude_code_sdk`, that is #2000's result, not this plan's to redo.
- [RESOLVED — no longer a gate] The former `[ORDERED]` "start BUILD only after #1829 merges" no-go is **lifted**: **#1829 CLOSED COMPLETED 2026-07-11**. Its refusal detector is live on main; the build proceeds with no wait and simply preserves the merged detector (see Risk 1, Race 1).
- [EXTERNAL] Removing the ollama runtime, model pulls, or any machine-level ollama provisioning. That was #1923's abandoned operational scope (closed NOT_PLANNED); the nightly-canary spike #1854 is separately closed. Uninstalling machine software is a human/operator action, not a code change in this plan.
- [SEPARATE-SLUG #2000] Normalized turn-event / json-schema routing for the harness. Belongs to #2000's HarnessAdapter seam.

## Update System

- **New dependency propagation:** `pydantic-ai` is added to `pyproject.toml`. The `/update` skill (`scripts/remote-update.sh`, `scripts/update/run.py`) already reinstalls the project venv from `pyproject.toml` on each machine, so propagation is automatic once the pin lands — confirm the update run reinstalls deps (it does) and note the new package in the update changelog.
- **No new config files or secrets:** the wrapper reuses `ANTHROPIC_API_KEY` (already in `.env`) and `config/models.py`. No `.env.example` addition needed.
- **No Popoto model changes:** no `scripts/update/migrations.py` entry required.
- If the surviving ollama sites are migrated (Open Question), a follow-up update note can record that machines no longer need the `ollama` Python package for those code paths — the runtime removal itself stays an operator action (No-Go).

## Agent Integration

- **No new MCP surface / `.mcp.json` change.** The wrapper is internal plumbing consumed by existing bridge and agent code paths; it is not a new agent-callable tool.
- **No new bridge entry point.** The bridge (`bridge/telegram_bridge.py`) already calls the affected functions (routing, read-the-room, session-router) internally; those functions keep their signatures, so the bridge wiring is unchanged.
- **Integration coverage:** the existing bridge routing / read-the-room integration tests exercise the agent-facing behavior end-to-end; they are updated (Test Impact) to assert the typed decisions still drive the same agent actions.
- No agent-integration wiring is required beyond preserving the existing internal call paths.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/nonharness-llm-wrapper.md` describing the `agent/llm/` wrapper, the "two ways to call an LLM" model (harness vs PydanticAI), the per-call output-model pattern, and how to add a new classifier.
- [ ] Add an entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] Docstring on the wrapper's public helper covering model selection, structured output, and the per-call semaphore-slot + fresh-client + per-site-timeout invariant (#1055/#1111) reconciled in Spike Results spike-1.
- [ ] A short note at each migrated call site pointing to the wrapper doc.

### External Documentation Site
- [ ] Not applicable — this repo has no external docs site for this surface.

## Success Criteria

- [ ] `agent/llm/` wrapper exists, is unit-tested (structured output, auto-retry, error surfacing, per-call semaphore-slot + injected-client + hard-timeout per spike-1), and is the single construction point for non-harness LLM calls.
- [ ] Every **MIGRATE**-disposition call site from the inventory routes through the wrapper with a typed `output_type` and its original conservative fail-safe default preserved.
- [ ] `agent/sdk_client.py` is unchanged by this plan's PR (`git diff --name-only main | grep -c 'agent/sdk_client.py'` → 0).
- [ ] `bridge/read_the_room.py` is unchanged by this plan's PR (`git diff --name-only main -- bridge/read_the_room.py | wc -l` → 0) — SKIP disposition; it is already schema-validated tool-calling.
- [ ] #1829's merged refusal detector (`agent/memory_extraction.py:122`) still fires after the extraction-call migration (asserted by test).
- [ ] `pydantic-ai` pinned in `pyproject.toml` and importable after `/update`.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] Migrated sites reference the wrapper (grep confirms each site imports from `agent/llm`).

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (wrapper)**
  - Name: `llm-wrapper-builder`
  - Role: Build the `agent/llm/` PydanticAI wrapper + unit tests. First and blocking.
  - Agent Type: builder
  - Domain: async (event-loop safety, shared-client injection, #1055/#1111 invariants)
  - Resume: true

- **Builder (call-site migration)**
  - Name: `callsite-migrator`
  - Role: Migrate each non-harness call site to the wrapper, one site per commit, preserving fail-safe defaults.
  - Agent Type: builder
  - Resume: true

- **Validator (behavior parity)**
  - Name: `parity-validator`
  - Role: Verify each migrated site preserves its decision semantics and conservative default; confirm `sdk_client.py` untouched.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `wrapper-documenter`
  - Role: Feature doc + index entry.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1: builder, validator, code-reviewer, test-engineer, documentarian. Domain framing (async) pasted into the wrapper builder's task per `docs/sdlc/DOMAIN_FRAMING.md`.

## Step by Step Tasks

### 1. Build the wrapper
- **Task ID**: build-wrapper
- **Depends On**: none (the former #1829 build gate is lifted — #1829 CLOSED COMPLETED; build may start immediately)
- **Validates**: `tests/unit/test_llm_wrapper.py` (create)
- **Informed By**: Research (PydanticAI `output_type`, custom async client injection)
- **Assigned To**: llm-wrapper-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `pydantic-ai` to `pyproject.toml` (pinned).
- Create `agent/llm/` with a typed `run_typed(prompt, output_type, *, model=MODEL_FAST, sdk_timeout=..., hard_timeout=...)` helper that, **per call**, holds `agent.anthropic_client.semaphore_slot()` for the whole `Agent.run`, constructs a fresh `async with AsyncAnthropic(timeout=sdk_timeout)` inside the slot, injects it via `AnthropicProvider(anthropic_client=...)`, and optionally wraps the run in `asyncio.wait_for(hard_timeout)`. See Spike Results spike-1 for the full reconciliation (there is no single shared client to reuse).
- Unit-test structured output, single auto-retry on schema mismatch, error surfacing, per-call semaphore-slot acquisition, injected-client-took-effect, and the outer hard-timeout bound.

### 2. Migrate Anthropic-native sites
- **Task ID**: migrate-anthropic-sites
- **Depends On**: build-wrapper
- **Validates**: intent-classifier, read-the-room, session-router, memory-extraction unit tests
- **Assigned To**: callsite-migrator
- **Agent Type**: builder
- **Parallel**: false
- Migrate the **MIGRATE**-disposition Anthropic-native sites: `agent/intent_classifier.py`, `bridge/session_router.py`, `agent/memory_extraction.py` — to the wrapper with typed output models; preserve each fail-safe default and the #1055 invariants. (#1829's build gate is lifted; when touching `memory_extraction.py`, preserve the already-merged refusal detector at `:122`.)
- **Do NOT touch `bridge/read_the_room.py` — it is SKIP.** Its `:429` call already uses forced `tool_choice={"type":"tool","name":"room_verdict"}`, so it is already schema-validated structured output. Re-wrapping it churns working code for no gain (see the inventory Disposition column and Rabbit Holes). `read_the_room` keeps its existing `RoomVerdict` return type unchanged.
- **`intent_classifier.py`: swap `dataclasses.asdict(parsed)` → `parsed.model_dump()`** (line 230) — the pydantic `output_type` result is a BaseModel, not a dataclass, and `dataclasses.asdict` raises on it. Keep the function's dict-returning cached contract unchanged.
- Confirm the two candidate sites (`agent/session_completion.py:495`, `bridge/promise_gate.py:510`) are non-harness decisions before migrating; skip if harness-adjacent. Also skip any site whose LLM call is already structured tool-calling with a validated schema — re-wrapping an already-typed call adds churn without payoff (see Rabbit Holes).

### 3. Migrate the ollama classifier sites
- **Task ID**: migrate-ollama-sites
- **Depends On**: migrate-anthropic-sites
- **Assigned To**: callsite-migrator
- **Agent Type**: builder
- **Parallel**: false
- **DECIDED — IN SCOPE (option A).** The supervisor directive resolves Open Question 1: since #1923 was closed NOT_PLANNED and never shipped, the ollama→PydanticAI migration of the classifier sites belongs to this plan. Migrate the surviving ollama LLM-classifier call sites — `bridge/routing.py` (three sites: `:675,927,1122`), `bridge/agent_catchup.py`, `tools/email_cs/triage.py` — to the wrapper with typed output models, defaulting to Haiku and preserving each site's conservative fail-safe default (routing → default respond, catch-up judge → its existing default, triage → its existing default). The completion signal is that none of the three files reference ollama any more: `bridge/routing.py` and `bridge/agent_catchup.py` drop their bare `import ollama`, and `tools/email_cs/triage.py` drops its `from tools import ollama_client` / `ollama_client.chat()` usage (it never used a bare `import ollama`). The Verification grep matches all three shapes.
- **Scope guard — LLM calls only, NOT embeddings.** ollama also backs the memory *embedding* path (`agent/embedding_provider.py`, `reflections/memory/*embedding*`, `models/graceful_embedding_field.py`). PydanticAI standardizes non-harness *LLM/chat* calls, not embeddings. Do NOT touch the embedding provider or any embedding-backed ollama usage in this plan. Only the three chat-classifier sites migrate.
- Do NOT remove the ollama runtime, model pulls, or machine-level provisioning (that stays an operator No-Go); this task only rewrites the three code call sites.

### 4. Behavior-parity validation
- **Task ID**: validate-parity
- **Depends On**: migrate-anthropic-sites, migrate-ollama-sites
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify each site's decision semantics + conservative default unchanged; assert `agent/sdk_client.py` untouched; assert `bridge/read_the_room.py` untouched (SKIP disposition); run the full affected test set.
- **Fixture parity for the ollama→Haiku swaps (routing, catch-up, triage).** These three sites move from `granite4.1:3b` to Haiku, which changes decision *quality*, not just cost/latency. For each, assert the migrated decision matches expected labels across a representative fixture set of inputs — do NOT accept "the call succeeds" as parity. Any label drift on the fixtures is a blocker, not a rounding error.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-parity
- **Assigned To**: wrapper-documenter
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/nonharness-llm-wrapper.md`; add index entry; cross-link migrated sites.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification checks; confirm every success criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| PydanticAI installed | `python -c "import pydantic_ai"` | exit code 0 |
| sdk_client.py untouched by this PR | `git diff --name-only main -- agent/sdk_client.py \| wc -l` | output contains 0 |
| read_the_room.py untouched by this PR (SKIP) | `git diff --name-only main -- bridge/read_the_room.py \| wc -l \| tr -d ' '` | `0` (SKIP disposition) |
| Wrapper is the shared call point | `grep -rl "from agent.llm" agent/ bridge/ tools/ \| wc -l` | output > 0 |
| Migrated ollama chat-classifier sites no longer reference ollama | `grep -lE "import ollama\|ollama_client\|ollama\.chat" bridge/routing.py bridge/agent_catchup.py tools/email_cs/triage.py 2>/dev/null \| wc -l \| tr -d ' '` | `0` (all three migrated) |
| Ollama embedding path untouched by this PR | `git diff --name-only main -- agent/embedding_provider.py \| wc -l \| tr -d ' '` | `0` (embedding path is a No-Go, stays) |

## Critique Results

Critique returned **NEEDS REVISION** (two blockers + concerns). Resolved in this revision pass:

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER 1 | critique | Risk-2 mitigation targeted a nonexistent shared client; `agent/anthropic_client.py:63` builds a fresh ephemeral client per call with site-specific timeouts. | Spike Results spike-1/spike-2; Solution "Inject a per-call slot-managed client"; Risk 2; Step 1; wrapper Key Element | Reconciled to a **per-call slot pattern**: hold `semaphore_slot()` for the whole `Agent.run`, construct a fresh `AsyncAnthropic(timeout=<per-site>)` inside, inject via `AnthropicProvider(anthropic_client=...)`, optional outer `asyncio.wait_for`. Grounded by reading `agent/anthropic_client.py` + `memory_extraction.py` double-timeout. |
| BLOCKER 2 | critique | Build gate on #1829/#2000 was stale — both described OPEN but are CLOSED COMPLETED. | Freshness Check, Prior Art, Risk 1, Race 1, `[ORDERED]` No-Go, Appetite, Success Criteria, Steps 1-2 | Both verified CLOSED COMPLETED 2026-07-11 (checked 2026-07-12). Wait-gate lifted everywhere; #1829's refusal detector is live on main (`:122`) and the build preserves it; #2000's harness/`sdk_client.py` work has shipped. |
| Concern | critique | `intent_classifier` `dataclasses.asdict` breaks on a pydantic result. | Solution bullet; Step 2 | Migration swaps `dataclasses.asdict(parsed)` → `parsed.model_dump()`; dict-returning cached contract preserved. |
| Concern | critique | Per-site analytics counters need not survive migration. | Rabbit Holes | Parity bar is observable decisions + fail-safe defaults, not byte-for-byte counters. Dismissed as non-goal unless a counter is dashboard-load-bearing. |
| Concern | critique | Hot-path `bridge/routing.py` trades free ollama for paid cloud. | Risk 5; Open Question 3 | Acknowledged/accepted trade; keep pre-LLM fast-paths, per-site model pin available, conservative default-respond preserved. |
| Concern | critique | Already-structured tool-calling sites may inflate scope. | Rabbit Holes; Step 2 | Skip any site already returning a schema-validated tool-calling result; wrapper targets hand-rolled parsers only. |
| Concern | critique | `grep -v -f /dev/null` no-op check in Verification. | Verification table | Replaced with two meaningful checks: migrated sites drop `import ollama`; embedding path untouched by PR. |

### Re-critique pass (2026-07-12) — NEEDS REVISION resolved

| Severity | Finding | Addressed By | Implementation Note |
|----------|---------|--------------|---------------------|
| BLOCKER | Internal contradiction: `bridge/read_the_room.py` was listed as BOTH a migration target (inventory + Step 2 + Test Impact `RoomVerdict` UPDATE) AND an explicit SKIP (Rabbit Hole). `:429` already forces `tool_choice={"type":"tool","name":"room_verdict"}` — schema-validated. | Inventory table (new **Disposition** column, single source of truth); Solution output-models list; Data Flow; Research; Rabbit Holes; Step 2; Step 4; Test Impact; Success Criteria; Verification table | **read_the_room disposition = SKIP** (deliberate: already-structured tool-calling). Added a Disposition column (MIGRATE / SKIP / CONFIRM AT BUILD) so the table, Step 2, and Test Impact agree. Every inventory site now carries an unambiguous disposition; read_the_room removed from the migrate list and output-models list; two "untouched" guards (Success Criteria + Verification) added mirroring the sdk_client guard. |
| Concern 1 | granite4.1:3b→Haiku changes decision *quality*, not just cost/latency; validation should assert verdict parity on fixtures. | Step 4 (validate-parity); Test Impact (routing/catch-up/triage) | Added an explicit fixture-parity assertion for the three ollama→Haiku sites: assert migrated decisions match expected labels across a representative fixture set; label drift is a blocker. "Call succeeds" is not accepted as parity. |
| Concern 2 | Shared-semaphore contention: adding `intent_classifier` + the routing hot path to the default-5 pool, slot held across auto-retry, could starve the pool. | Risk 2 (new sizing note) | Acknowledged as an accepted risk, bounded by pre-LLM fast-paths, per-call `hard_timeout`, and a tunable single-config-point pool size. Build preserves fast-paths and leaves slot count tunable; raising the pool or pinning routing to a separate model is the follow-up, not a blocker. |
| Concern 3 | Verification grep was a no-op for triage: it imports `from tools import ollama_client`, never bare `import ollama`. | Verification table; Step 3 completion signal | Grep changed to `grep -lE "import ollama\|ollama_client\|ollama\.chat"` so it matches all three real shapes (bare import in routing/catch-up, `ollama_client` in triage). Step 3 completion signal updated to name the real triage import. Also corrected routing to three ollama sites (`:675,927,1122`). |

---

## Open Questions

1. **Ollama sites — migrate now or defer? → RESOLVED: migrate now (option A).** The original re-scoping instruction assumed #1923 had already removed the three ollama call sites (`bridge/routing.py`, `bridge/agent_catchup.py`, `tools/email_cs/triage.py`). It did not — #1923 was closed NOT_PLANNED and the sites remain live (verified: 35 / 11 / 8 ollama references respectively). The supervisor directive resolves this: the ollama→PydanticAI migration of the three chat-classifier sites IS in scope for this plan (task 3). Scope is bounded to those three *LLM* call sites; the ollama *embedding* path and the ollama runtime/machine provisioning stay out of scope (No-Gos).
2. **Candidate sites in scope? → RESOLVED: build-time confirmation, not a blocker.** `agent/session_completion.py:495` and `bridge/promise_gate.py:510` migrate **only if** the build confirms they are non-harness decisions (not the harness drafter path at `:755`/`:817`, which is out of scope). Task 2 already encodes this as a per-site check; a site that turns out harness-adjacent is skipped. No human input needed before build.
3. **Default model policy. → RESOLVED: Haiku (`MODEL_FAST`) is the universal default; per-site pins are allowed from day one.** The wrapper defaults to `MODEL_FAST` (Haiku) so a single config edit swaps every site. Per-site `run_typed(..., model=...)` overrides are supported from the start precisely so the high-frequency `bridge/routing.py` hot path (Risk 5) can pin a cheaper/local PydanticAI-backed model later without touching the call site. The migration lands on the Haiku default; choosing a cheaper model for routing is a follow-up tuning decision, not a blocker for this plan.
