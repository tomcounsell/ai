---
status: Planning
type: chore
appetite: Large
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/1925
last_comment_id:
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
- `agent/memory_extraction.py` Haiku extraction path (`async with anthropic.AsyncAnthropic` at `:288`, `_llm_call` helper) — **still present**; confirmed a non-harness call site and in scope. **Overlaps issue #1829** (LLM refusal-detector), currently building in a parallel lane on this same file.
- `bridge/routing.py:675`, `bridge/agent_catchup.py:187`, `tools/email_cs/triage.py:90` — direct `ollama.chat()` calls — **still present**.

**Cited sibling issues/PRs re-checked:**
- **#1923** ("Drop ollama entirely: replace bridge routing + email triage classifier calls") — **CLOSED 2026-07-10 as NOT_PLANNED** (abandoned; no PR ever referenced it). The issue-body claim that "#1923 solves the ollama sites" is FALSE: the ollama call sites still exist on the three files above. This is the primary stale premise. The ollama runtime purge / model-pull cleanup / nightly canary work #1923 also owned did **not** happen. (Nightly-canary spike **#1854** is separately CLOSED, so the canary concern is moot regardless.)
- **#2000** ("Phase 2: HarnessAdapter seam") — **OPEN**. Its body states: "#1925: remove `claude_code_sdk` / two-transport split — this extraction IS its harness half; the SDK-removal issue shrinks accordingly (coordinate, don't duplicate)." Confirms the harness half is #2000's, not #1925's.
- **#1829** ("Memory extraction: LLM-based refusal-detector") — **OPEN**, building in a parallel lane, writes `agent/memory_extraction.py`. Build-ordering dependency recorded (see Race Conditions and No-Gos).
- **#1924** (granite PTY teardown, the issue's cited prerequisite) — the teardown has landed; the harness is the sole role-execution path. This removes the "#1924 must land first" gate.

**Commits on main since issue was filed (touching referenced files):**
- Memory-extraction fixes #2016/#1822 merged (PRs #2023, #1831) — touched `agent/memory_extraction.py` noise-source handling; do not change the LLM-call shape this plan wraps, but confirm the file is under active concurrent development → reinforces the #1829 build-ordering dependency.

**Active plans in `docs/plans/` overlapping this area:** `harness-cross-compat.md` (#1996/#2000 tracking). It owns the **harness half** and the `sdk_client.py` excision. No overlap with the non-harness wrapper scope, but the two must not both edit `sdk_client.py` — this plan does not.

**Notes:** The re-scoping (PydanticAI half only; harness/SDK-deletion → #2000; ollama premise corrected) was directed by the invoking supervisor and is encoded throughout. The one genuinely open scope decision — whether to migrate the three surviving ollama sites in THIS plan or defer them — is surfaced as an Open Question rather than silently decided, because the original re-scoping instruction assumed those sites were already gone.

## Prior Art

- **#1923** ("Drop ollama entirely") — CLOSED NOT_PLANNED. Intended to replace the ollama classifier calls with small Claude calls and retire the ollama runtime. Never implemented. Its own recon noted the synergy: "should be implemented AS a PydanticAI call — model-agnostic solves it by construction." This plan is the vehicle that makes that true for the call-site half.
- **#2000 / `harness-cross-compat.md`** — OPEN. Owns the harness adapter seam and the `sdk_client.py` two-transport cleanup. Coordinates with this plan by taking the harness half so #1925 shrinks to the non-harness half.
- **#1829** — OPEN. Adds an optional Haiku-based refusal detector to `agent/memory_extraction.py` behind a default-OFF flag, reusing that module's `_llm_call`. Direct file overlap; must merge before this plan's build touches the file.
- **hotfix #1055 / #1111** — established the shared `agent/anthropic_client.py` (`semaphore_slot` / `anthropic_slot`, `AsyncAnthropic` + double-timeout) that most non-harness Anthropic sites already use. The PydanticAI wrapper must preserve these rate-limit and timeout invariants, not regress them.

## Research

External research on PydanticAI to ground the wrapper design.

**Queries used:**
- "PydanticAI model-agnostic Agent Anthropic OpenAI provider configuration 2026"
- "pydantic-ai structured output result_type BaseModel classification example"

**Key findings:**
- **Model-agnostic by construction** — a single PydanticAI `Agent` is portable across vendors by swapping the `Model` class (`AnthropicModel`, `OpenAIChatModel`, `GoogleModel`, …); auth/connection lives in a `Provider` passed to the Model. Supports Anthropic, OpenAI, **Ollama**, LiteLLM, and others. Source: https://ai.pydantic.dev/models/overview/ and https://ai.pydantic.dev/models/anthropic/ . This validates the "one wrapper, swappable model" design and means the surviving ollama sites can be expressed as the same wrapper with an Ollama-backed model if local inference is still wanted.
- **Typed structured output** — pass `output_type=SomeBaseModel` (formerly `result_type`) to the Agent; PydanticAI validates the LLM output against the schema and auto-reprompts on mismatch. Multiple output types are allowed (`output_type=[Model, str]`). Source: https://ai.pydantic.dev/output/ . This replaces the hand-rolled `json.loads`-shape repair currently scattered across `agent/memory_extraction.py` and the string-parsing verdict extractors in `bridge/read_the_room.py` / `bridge/session_router.py`.
- **Custom AsyncClient injection** — `AnthropicProvider` / `OpenAIProvider` accept a caller-supplied async client, so the wrapper can hand PydanticAI the existing rate-limited `AsyncAnthropic` client (preserving the #1055/#1111 semaphore + timeout invariants) rather than letting PydanticAI construct an unmanaged one. Source: https://ai.pydantic.dev/models/anthropic/ .

## Data Flow

1. **Entry point** — a non-harness caller (e.g. `bridge/routing.py::classify_message`, `agent/memory_extraction.py::extract_observations`, `bridge/read_the_room.py::should_send`) needs a typed decision from an LLM.
2. **Wrapper call** — the caller invokes the new wrapper (`agent/llm/…`), passing a prompt + an `output_type` BaseModel (and optionally a model override). The wrapper selects the configured default model (Haiku) via a `Provider` wired to the shared rate-limited async client.
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
- PM check-ins: 1-2 (confirm ollama-site migrate-vs-defer decision; confirm build lands after #1829)
- Review rounds: 1-2 (wrapper API design review; per-site migration review)

The coding is mechanical once the wrapper exists; the cost is breadth (7+ call sites, each with its own fail-safe semantics and tests) plus the concurrency coordination with #1829 and #2000.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` present | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | Default (Haiku) model backend for the wrapper |
| `pydantic-ai` installed | `python -c "import pydantic_ai"` | The wrapper's framework (added by this plan) |

Run via `python scripts/check_prerequisites.py docs/plans/pydantic-ai-nonharness-llm-standardization.md`.

## Solution

### Key Elements

- **`agent/llm/` wrapper package** — a thin PydanticAI facade exposing a typed call helper (e.g. `run_typed(prompt, output_type, *, model=DEFAULT_FAST) -> BaseModel`). Owns model/provider selection and hands PydanticAI the shared rate-limited async client so the #1055/#1111 semaphore + double-timeout invariants are preserved.
- **Per-call output models** — one BaseModel per call site's decision (`RoutingDecision`, `TerminusVerdict`, `RoomVerdict`, `SessionRouteDecision`, `ExtractionResult`, `EmailTriageDecision`, `IntentClassification`). These replace ad-hoc string/JSON parsing.
- **Model config seam** — the wrapper's default fast model reads `config/models.py` (`MODEL_FAST`/`HAIKU`), so a model swap is one edit. Model-agnostic by construction (PydanticAI Model classes are swappable).
- **Call-site migrations** — each non-harness site swaps its inline client for a wrapper call, keeping its existing conservative fail-safe default on error.

### Flow

Caller needs a decision → calls `agent/llm` wrapper with prompt + `output_type` → PydanticAI validates output against the schema (auto-retry once) → caller receives a typed BaseModel (or its conservative default on failure) → decision flows onward unchanged.

### Technical Approach

- **Wrapper first, migrate second.** Land the `agent/llm/` wrapper + its unit tests before touching any call site. Each site migration is then a small, independently reviewable, independently revertible change.
- **Preserve fail-safe posture per site.** Every current site has a deliberate default on LLM failure (routing → respond; read-the-room → send; email triage → escalate; extraction → empty/skip). The wrapper surfaces failures as exceptions or a sentinel; each migrated site keeps its own default. Do not centralize the default — the conservative choice is site-specific.
- **Inject the shared async client.** Use `AnthropicProvider(anthropic_client=<shared AsyncAnthropic>)` so PydanticAI reuses the rate-limited client from `agent/anthropic_client.py` rather than constructing an unmanaged one (protects the #1055/#1111 invariants).
- **Confirmed non-harness inventory** (grep-verified at baseline `35301b5`):
  | Site | Current mechanism | Decision type |
  |------|-------------------|---------------|
  | `agent/intent_classifier.py:219` | sync `anthropic.Anthropic` (Haiku) | intent classification |
  | `agent/memory_extraction.py:288` (`_llm_call`) | `AsyncAnthropic` (Haiku) | observation extraction — **#1829 overlap** |
  | `bridge/routing.py:675,927` | `ollama.chat()` + Haiku fallback | message routing |
  | `bridge/read_the_room.py:429` | `AsyncAnthropic` (Haiku) | send/hold verdict |
  | `bridge/session_router.py:125` | `anthropic_slot` (Haiku) | session-route classification |
  | `bridge/agent_catchup.py:187,197` | `ollama.chat()` + Haiku fallback | catch-up judge |
  | `tools/email_cs/triage.py:90` | `ollama.chat()` | email triage |
  | `agent/session_completion.py:495`, `bridge/promise_gate.py:510` | `AsyncAnthropic` | **candidate** sites — confirm during build whether these are non-harness decisions in scope |
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
- [ ] `tests/unit/test_routing*.py` / bridge routing tests — UPDATE: assert typed `RoutingDecision` and unchanged fail-safe (default respond); mocked ollama expectations change if the site is migrated.
- [ ] `tests/unit/test_read_the_room*.py` — UPDATE: assert typed `RoomVerdict`; preserve #1055 semaphore/timeout invariant assertions.
- [ ] `tests/unit/test_session_router*.py` — UPDATE: assert typed `SessionRouteDecision`.
- [ ] `tests/*/test_agent_catchup*.py` — UPDATE (if catchup migrated): assert typed judge verdict.
- [ ] `tests/*/test_email*triage*.py` — UPDATE (if triage migrated): assert typed `EmailTriageDecision` and escalate-on-failure default.
- [ ] New: `tests/unit/test_llm_wrapper.py` — CREATE: wrapper structured-output success, auto-retry on schema mismatch, error surfacing, shared-client injection.

Exact test paths are confirmed during build via `grep -rl` on each touched module; the dispositions above are the audit.

## Rabbit Holes

- **Rewriting harness calls.** The `claude -p` path and `agent/sdk_client.py` are out of scope. Do not "while we're here" refactor them — that is #2000.
- **Turning this into the ollama-purge project.** Removing the ollama runtime, model pulls, and canary machine-wide was #1923's abandoned operational scope. Migrating the three code call sites is bounded; uninstalling ollama from machines is not this plan's job (see No-Gos).
- **A universal "one signature fits all" LLM function.** Each site has genuinely different output shapes and fail-safe defaults. Force-fitting one mega-signature will cost more than seven small typed models.
- **Streaming / token-level UX.** These are non-harness one-shot classification/extraction calls; none need streaming. Do not add it.
- **Provider fallback chains.** PydanticAI supports fallback models, but adding a routing/ollama fallback ladder is scope creep — default to Haiku, keep the site's error default.

## Risks

### Risk 1: Concurrent edits to `agent/memory_extraction.py` collide with #1829
**Impact:** Two lanes editing the same file → merge conflict, or one lane's refusal-detector / extraction changes clobbered.
**Mitigation:** This plan's BUILD is gated to land **after #1829 merges** (see Race Conditions and No-Gos). The plan lane itself only writes a `docs/plans/` file, so it is safe to author now.

### Risk 2: PydanticAI regresses the #1055/#1111 rate-limit + timeout invariants
**Impact:** If PydanticAI constructs its own unmanaged `AsyncAnthropic`, the shared semaphore and double-timeout protections are bypassed → event-loop stalls under load (the exact class of bug #1055 fixed).
**Mitigation:** Inject the shared rate-limited async client into PydanticAI's `AnthropicProvider`. Add a test asserting the wrapper uses the shared client / respects the semaphore.

### Risk 3: New dependency weight / version drift
**Impact:** `pydantic-ai` pulls its own transitive tree; a bad pin could break installs across machines.
**Mitigation:** Pin a known-good version in `pyproject.toml`; the update system propagates it (see Update System). Smoke-test `import pydantic_ai` in the prerequisite checker.

### Risk 4: Silent behavior change in a fail-safe default
**Impact:** A migrated site's conservative default (respond/escalate/send) subtly changes, causing the agent to go silent or over-escalate.
**Mitigation:** Per-site tests assert the exact default on wrapper failure; migrate one site per commit for isolated review.

## Race Conditions

### Race 1: #1925 build vs #1829 build both writing `agent/memory_extraction.py`
**Location:** `agent/memory_extraction.py` (extraction `_llm_call` path, `:288`–`:930`)
**Trigger:** Both lanes build concurrently and open PRs touching the same LLM-call region.
**Data prerequisite:** #1829's refusal-detector must be present in the module before this plan rewrites the extraction call to the wrapper, so the wrapper migration preserves it.
**State prerequisite:** #1829 merged to main.
**Mitigation:** **Ordered dependency — this plan's BUILD starts only after #1829 merges.** Encoded as an `[ORDERED]` No-Go. The plan-authoring lane (this document) writes only `docs/plans/`, so it has no race now.

### Race 2: #1925 vs #2000 both editing `agent/sdk_client.py`
**Location:** `agent/sdk_client.py`
**Trigger:** Both plans edit the shared transport file → rebase collision.
**Data prerequisite:** none.
**State prerequisite:** clean separation of concerns.
**Mitigation:** This plan **does not touch `agent/sdk_client.py`** at all; the `claude_code_sdk` deletion is #2000's PR. No shared file, no race.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2000] Deleting `ClaudeSDKClient` / `claude_code_sdk`, migrating the `ValorAgent` conversational path to the harness, and any edit to `agent/sdk_client.py`. Folded into #2000's PR (harness half) to avoid a rebase collision on `sdk_client.py`. This plan does not touch that file.
- [ORDERED] Starting the BUILD before **#1829** merges. #1829 is building the refusal-detector in `agent/memory_extraction.py` in a parallel lane; this plan's migration of that same file waits for #1829's merge event to avoid a commit race. (Plan authoring — this doc — is unaffected.)
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
- [ ] Docstring on the wrapper's public helper covering model selection, structured output, and the shared-client injection invariant (#1055/#1111).
- [ ] A short note at each migrated call site pointing to the wrapper doc.

### External Documentation Site
- [ ] Not applicable — this repo has no external docs site for this surface.

## Success Criteria

- [ ] `agent/llm/` wrapper exists, is unit-tested (structured output, auto-retry, error surfacing, shared-client injection), and is the single construction point for non-harness LLM calls.
- [ ] Every in-scope non-harness call site from the inventory routes through the wrapper with a typed `output_type` and its original conservative fail-safe default preserved.
- [ ] `agent/sdk_client.py` is unchanged by this plan's PR (`git diff --name-only main | grep -c 'agent/sdk_client.py'` → 0).
- [ ] Build landed after #1829 merged (no `agent/memory_extraction.py` conflict; #1829's refusal detector preserved).
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
- **Depends On**: none (but BUILD phase gated on #1829 merge — see No-Gos)
- **Validates**: `tests/unit/test_llm_wrapper.py` (create)
- **Informed By**: Research (PydanticAI `output_type`, custom async client injection)
- **Assigned To**: llm-wrapper-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `pydantic-ai` to `pyproject.toml` (pinned).
- Create `agent/llm/` with a typed `run_typed(prompt, output_type, *, model=MODEL_FAST)` helper wired to a `Provider` that reuses the shared rate-limited `AsyncAnthropic` client.
- Unit-test structured output, single auto-retry on schema mismatch, error surfacing, and shared-client usage.

### 2. Migrate Anthropic-native sites
- **Task ID**: migrate-anthropic-sites
- **Depends On**: build-wrapper
- **Validates**: intent-classifier, read-the-room, session-router, memory-extraction unit tests
- **Assigned To**: callsite-migrator
- **Agent Type**: builder
- **Parallel**: false
- Migrate `agent/intent_classifier.py`, `bridge/read_the_room.py`, `bridge/session_router.py`, `agent/memory_extraction.py` (after #1829) to the wrapper with typed output models; preserve each fail-safe default and the #1055 invariants.
- Confirm the two candidate sites (`agent/session_completion.py:495`, `bridge/promise_gate.py:510`) are non-harness decisions before migrating; skip if harness-adjacent.

### 3. Migrate the ollama classifier sites
- **Task ID**: migrate-ollama-sites
- **Depends On**: migrate-anthropic-sites
- **Assigned To**: callsite-migrator
- **Agent Type**: builder
- **Parallel**: false
- **DECIDED — IN SCOPE (option A).** The supervisor directive resolves Open Question 1: since #1923 was closed NOT_PLANNED and never shipped, the ollama→PydanticAI migration of the classifier sites belongs to this plan. Migrate the three surviving `ollama.chat()` LLM-classifier call sites — `bridge/routing.py`, `bridge/agent_catchup.py`, `tools/email_cs/triage.py` — to the wrapper with typed output models, defaulting to Haiku and preserving each site's conservative fail-safe default (routing → default respond, catch-up judge → its existing default, triage → its existing default). Removing the code-level `import ollama` from these three files is the completion signal.
- **Scope guard — LLM calls only, NOT embeddings.** ollama also backs the memory *embedding* path (`agent/embedding_provider.py`, `reflections/memory/*embedding*`, `models/graceful_embedding_field.py`). PydanticAI standardizes non-harness *LLM/chat* calls, not embeddings. Do NOT touch the embedding provider or any embedding-backed ollama usage in this plan. Only the three chat-classifier sites migrate.
- Do NOT remove the ollama runtime, model pulls, or machine-level provisioning (that stays an operator No-Go); this task only rewrites the three code call sites.

### 4. Behavior-parity validation
- **Task ID**: validate-parity
- **Depends On**: migrate-anthropic-sites, migrate-ollama-sites
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify each site's decision semantics + conservative default unchanged; assert `agent/sdk_client.py` untouched; run the full affected test set.

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
| Wrapper is the shared call point | `grep -rl "from agent.llm" agent/ bridge/ tools/ \| wc -l` | output > 0 |
| No new direct ollama import added | `grep -rn "import ollama" agent/ bridge/ tools/ --include=*.py \| grep -v -f /dev/null \| wc -l` | (records baseline; must not increase) |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Ollama sites — migrate now or defer? → RESOLVED: migrate now (option A).** The original re-scoping instruction assumed #1923 had already removed the three ollama call sites (`bridge/routing.py`, `bridge/agent_catchup.py`, `tools/email_cs/triage.py`). It did not — #1923 was closed NOT_PLANNED and the sites remain live (verified: 35 / 11 / 8 ollama references respectively). The supervisor directive resolves this: the ollama→PydanticAI migration of the three chat-classifier sites IS in scope for this plan (task 3). Scope is bounded to those three *LLM* call sites; the ollama *embedding* path and the ollama runtime/machine provisioning stay out of scope (No-Gos).
2. **Candidate sites in scope?** Are `agent/session_completion.py:495` and `bridge/promise_gate.py:510` (both `AsyncAnthropic` calls) non-harness decisions that should migrate, or are they harness-adjacent and out of scope?
3. **Default model policy.** Keep Haiku (`MODEL_FAST`) as the universal wrapper default, or allow per-site model pins from the start (e.g. a cheaper local model for high-frequency routing)?
