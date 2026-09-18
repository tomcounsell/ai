---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-18
tracking: https://github.com/tomcounsell/ai/issues/3410
last_comment_id: 5730470971
---

# LLM Task Taxonomy and Routing Layer

## Problem

Outside the `claude -p` harness, this repo makes about thirty smaller LLM calls. Some think (extract memories, draft a briefing, judge a test). Many only decide (does this message need a reply, which routing bucket, is this an interjection, bind to which job). Both kinds are written the same way at the call site: a hand-picked model constant and a hand-picked transport. Nothing in code says "this call returns one of four labels" versus "this call writes a paragraph".

That absence has a cost we have already paid twice. #1923/#1925 moved the bridge classifiers off Ollama onto Haiku by editing every call site. #2494 moved two of them back onto local granite by editing those call sites again. Each move was a per-site rewrite because there is no routing point; the model choice lives in seventeen places. Tom's framing on 2026-09-18: classification with a model is fundamentally different from standard LLM work, so divide the AI tasks into classification and thinking and route each appropriately. His follow-up the same day set the order: taxonomy and routing point first, local CPU backends evaluated first, an external structured-decision provider (TypeSafe Jev 1.13) in shadow mode only until the market settles, and no site flips to an external provider without a local fallback wired.

**Current behavior:**

Verified on `main` at `2cf8da648` (details in Freshness Check):

- Fifteen live fixed-choice call sites, across four transports: `run_typed` (Haiku), `run_typed_local` (granite), a raw Anthropic client with a forced tool or free-text JSON, and a raw `requests` call to OpenRouter chat/completions. Six of them bypass `agent/llm/wrapper.py` entirely, so no single point sees them.
- Roughly fifteen thinking sites share the same constants and transports. Nothing distinguishes the populations.
- Charter §7 (client work and its private context stay on the Claude and Codex subscriptions) is enforced at exactly three places today, all inside the improvement tooling (`tools/improvement_eligibility.py::is_open_source` consumers). Every bridge classifier sees client rooms and Valor-internal rooms alike and has no project key in hand.
- The emoji site the issue originally listed as C16 (row since removed from the issue table; email triage is now C16) is no longer an LLM or embedding call: `find_best_emoji_for_message` is a `random.choice` keyed on the C5 work type. The embedding path (`find_best_emoji`) is dead code with no production caller.
- Jev is reachable only on `POST /api/alpha/decisions` (chat/completions returns HTTP 400 for the slug), so adopting it means a new transport, and the wire shape is now pinned by a verbatim probe (Research).

**Desired outcome:**

1. Every non-harness LLM call site declares its task kind in code. A test enumerates the sites and fails on an undeclared one.
2. One routing point in `agent/llm/` resolves backend and transport from task kind, per-call context eligibility, and settings. With default settings, behavior on `main` is byte-for-byte what it is today, and existing unit tests pass unchanged.
3. Every classification site can resolve a local backend through the routing point. Candidate backends (local zero-shot, then the Jev decisions transport) plug in behind one interface, are evaluated by paired comparison against each site's incumbent with the record attached to improvement case `1ec40086ca1d422e90ef747775ff7f64`, and flip on per site only where an error-cost-aware bar is met. A rejection is a recorded result.

## Freshness Check

**Baseline commit:** `2cf8da648c824d3f848574b11411b7aaddf8ec2f` (plan written against this; `56a980d8a` is HEAD after a docs-only pull)
**Issue filed at:** 2026-09-18T06:59:27Z
**Disposition:** Minor drift

**File:line references re-verified** (every site read on the baseline; line numbers are the LLM call):

| # | Site | Call | Transport | Output | Fail-safe | Status |
|---|------|------|-----------|--------|-----------|--------|
| C1 | `bridge/routing.py::classify_needs_response` | :745 | `run_typed`, `MODEL_FAST` | `NeedsResponseDecision.needs_response: bool` | `True` (respond) | holds |
| C2 | `bridge/routing.py::classify_conversation_terminus` | :966 | `run_typed`, `MODEL_FAST` | `TerminusDecision.verdict: Literal[RESPOND, REACT, SILENT]` | `"RESPOND"` | holds |
| C3 | `bridge/routing.py::_classify_work_request_llm` | :1112 | `run_typed`, `MODEL_FAST` | `RoutingDecision.category: Literal[sdlc, collaboration, other, question]` | `QUESTION` (in caller `classify_work_request` :1056) | holds |
| C4 | `agent/intent_classifier.py::classify_intent` | :213 | `run_typed`, `MODEL_FAST` | `IntentClassification.intent: Literal[teammate, collaboration, other, work]` + `confidence` + `reasoning` | `work`, conf 0.0 | holds |
| C5 | `tools/classifier.py::classify_request_async` | :193 | raw `anthropic_slot()` client, free-text JSON | dict `type` in bug/feature/chore/sdlc | re-raises; bridge caller `classify_work_type` (:1930) swallows to `{}` | holds; sync `classify_request` (:90, `anthropic.Anthropic`) has no production caller |
| C6 | `bridge/agent_catchup.py::judge_message` | :229 | `run_typed`, `MODEL_FAST` | `CatchupJudgeVerdict.verdict` (3 values) | `ANSWERED` | holds |
| C7 | `bridge/injection_inspection.py::inspect_untrusted_input` | :170 | `run_typed`, default model | `_InjectionJudgment.risk: str` (compared to `"suspected"`) | `inspected=False, flagged=False` | holds; `risk` is `str`, becomes `Literal` |
| C8 | `bridge/context_recall.py::check_outbound_context_recall` | :269 | `run_typed`, default model, `sdk_timeout=3.0` | `ContextRecallVerdict.advised: bool` | `advised=False` | holds |
| C9 | `bridge/promise_gate.py::_evaluate_promise_async` | :697 | raw `AsyncAnthropic` forced tool, `max_retries=0`, `semaphore_slot(timeout=3.0)` | tool `promise_verdict.action` allow/block | `None` then regex heuristic | holds |
| C10 | `agent/session_completion.py::_judge_completion_novelty` | :520 | raw `AsyncAnthropic` forced tool, timeout 3.0 | tool `completion_novelty_verdict.action` restate/new | `False` (deliver) | holds |
| C11 | `agent/health_check.py::_judge_health` | :450 | raw `anthropic_slot()` client, free-text JSON | dict `healthy: bool` | `healthy=True` | holds |
| C12 | `bridge/job_router.py::_classify` | :274 | `run_typed_local`, granite | `JobRouteDecision.decision: Literal[bind, new]` + `job_id` + `confidence`; threshold `JOB_ROUTER_CONFIDENCE_THRESHOLD` :56 = 0.70 | `None` (new) | holds |
| C13 | `tools/classifier.py::classify_message_intent_async` | :415 | `run_typed_local`, granite | `IntentDecision.intent: Literal[interjection, new_work]` (+ recall variant); threshold `INTENT_CONFIDENCE_THRESHOLD` :247 = 0.80 | `new_work`, conf 0.0 | holds |
| C14 | `reflections/memory/memory_quality_audit.py::_gemma_classify` | :442 | raw `ollama.chat`, `OLLAMA_CLASSIFIER_MODEL` (granite, despite the name) | JSON `is_junk: bool`, `anomaly_signal` | `None` (unavailable) | holds |
| C15 | `reflections/improvement_collect.py::_OpenRouterJudge` | :817 | `requests.post(OPENROUTER_URL)`, gemma free tier, metered | JSON `answer` yes/no + `span` + `confidence` | `promises-judge-failed`, break | holds |
| ex-C16 (emoji) | `tools/emoji_embedding.py::find_best_emoji_for_message` | :439 | none: `random.choice(ACTION_EMOJI_MAP[action])` | one emoji | `DEFAULT_EMOJI` | **drifted**: no model call; embedding path `find_best_emoji` (:325) has zero production callers |
| C16 | `tools/email_cs/triage.py::triage_local` | :127 | `run_typed`, `MODEL_FAST` | `EmailTriageDecision.category: Category` (4 values) | `RAISE_TO_HUMAN` | holds; client (Cuttlefish) |
| edge | `bridge/read_the_room.py::read_the_room` | :575 | raw `AsyncAnthropic` forced tool, timeout 3.0 | tool `room_verdict.action` send/trim/**suppress** + `revised_text` | `send`, `rtr_error` | holds; third value is `suppress`, the issue wrote `hold` |

Wrapper: `run_typed(prompt, output_type, *, model=MODEL_FAST, sdk_timeout, hard_timeout, _skip_guard)` at `agent/llm/wrapper.py:139` and `run_typed_local(prompt, output_type, *, model=OLLAMA_CLASSIFIER_MODEL, hard_timeout=None)` at `:241`. Neither takes a task, site, or project parameter. `LLMCallError` at `:80`. No test enumerates non-harness call sites; the closest pattern is `tests/unit/test_harness_model_coverage.py::test_all_harness_call_sites_pass_model_kwarg` (AST walk over `agent/*.py` for `get_response_via_harness(` calls).

**Cited sibling issues/PRs re-checked:**
- #1925 (closed 2026-07-12, PR #2045 merged): the `run_typed` wrapper and the first seven migrated sites. Still the seam.
- #2494 (open): durability refactor; its Task 13 put C12 and C13 on granite via `run_typed_local`. Its plan (`docs/plans/durability-room-job-agentrun.md:104`) records that granite had zero latency measurements and was reintroduced deliberately. This plan's comparison runner produces those measurements.
- #3338 (closed by PR #3381, merged 2026-09-17): `OPENROUTER_GEMMA4_FREE` repointed at a listed model; live-listing probe test in `tests/unit/test_models.py`. Nothing blocks.
- #3177 (open, plan `docs/plans/recursive-self-improvement.md`, status Planning): the RSI controller. Its lane 3 already shipped `tools/paid_inference_meter.py`, which this plan reuses. Its Research section names cheap inference as a research target and says "propose an adapter only after evaluating suitability", which is exactly this plan's shape.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since=2026-09-18T06:59:27Z` over all 18 files is empty).

**Active plans in `docs/plans/` overlapping this area:** `recursive-self-improvement.md` (#3177) owns the improvement case substrate and the meter; this plan consumes both and adds no controller logic. `durability-room-job-agentrun.md` (#2494) owns C12; this plan changes only C12's transport call and keeps its threshold semantics on the incumbent. Coordination, not conflict.

**Notes:** Two corrections carried into the plan: the emoji site (originally C16; the issue table was renumbered on 2026-09-18 and email triage is now C16) leaves the classification list (no model on `main`; the dead embedding path is deleted under this plan and a decision-backed emoji choice is filed separately), and read-the-room's action set is `send/trim/suppress`. The issue's live Jev probe is confirmed: the `jev-issue` agent ran it on 2026-09-18 from this repo's venv, and the verbatim request and response are in Research.

## Prior Art

- **#1925 / PR #2045** (merged 2026-07-12): "Standardize non-harness LLM calls on a PydanticAI wrapper". Created `agent/llm/wrapper.py::run_typed`, migrated intent classifier, memory extraction, three routing classifiers, catch-up judge, and email triage. Explicitly skipped `read_the_room.py` (forced tool, system prompt, tight timeout) and left C5, C9, C10, C11 raw. This plan finishes that migration.
- **#1923** (closed 2026-07-10): "Drop ollama entirely: replace bridge routing + email triage classifier calls with a small Claude call". Succeeded; then reversed in part by #2494.
- **#2494 Task 13** (open): put job routing (C12) and intake intent (C13) on granite via `run_typed_local`, a second transport leg in the wrapper. Reintroduced local classification with a documented gap: no latency measurements.
- **#1636** (closed 2026-06-13): consolidated local Ollama onto granite for classification, Ollama Cloud for generation. Established the classification/generation split at the model-constant level (`OLLAMA_CLASSIFIER_MODEL` vs `settings.models.ollama_generation_model`), which is the seed of this taxonomy.
- **#3338 / PR #3381**: delisted free-tier model fixed, live-listing probe pattern in `tests/unit/test_models.py`. The Jev probe test mirrors it.
- **#3177 (RSI controller)**: `tools/paid_inference_meter.py` (reserve/settle per purpose), `tools/improvement_eligibility.py::is_open_source`, `tools/improvement_eval/` (a frozen-input experiment harness whose envelopes are retrieval parameters and agent tasks, so a classifier comparison does not fit it; see Spike Results), `valor-improve investigation` for attaching records to a case.
- **#3089 / #3140**: the coupled anthropic + pydantic-ai-slim pin set and the degraded-stack guard (`agent/llm/compat.py::stack_axes`). Any new transport must stay outside that coupled set or join it deliberately.

## Research

**Queries used:**
- OpenRouter decisions endpoint request/response schema, Python SDK (`openrouter` package, `client.alpha.decisions.create`)
- TypeSafe System One primitives (`choice`, `noul`, `score`), option limits, state format
- GLiClass zero-shot classification on CPU via ONNX, model ids, dependencies, licensing
- onnxruntime arm64 macOS wheels; OpenRouter alpha endpoint stability

**Key findings:**

1. **Jev wire shape on OpenRouter, verified by live probe** (run 2026-09-18 by the `jev-issue` agent from this repo's venv, key from `settings.api.openrouter_api_key`; HTTP 200 in 611 ms, first attempt). Request to `POST https://openrouter.ai/api/alpha/decisions` with `Authorization: Bearer` and `Content-Type: application/json`:

   ```json
   {"model": "typesafe/jev-1.13",
    "state": "can you fix the bug in the login page and open a PR",
    "questions": {
      "route": {"type": "choice",
                "instructions": {"question": "Which routing bucket does this chat message belong to?", "focus": "the action the sender is asking for"},
                "criteria": {"sdlc": {"what": "work request that could result in code changes or a PR", "examples": ["fix the bug", "add a feature"]},
                             "collaboration": {"what": "direct task the PM can handle without coding"},
                             "other": {"what": "ambiguous task that does not clearly fit sdlc or collaboration"},
                             "question": {"what": "purely asking for info, explanation, opinion, or social chat"}}},
      "needs_response": {"type": "noul",
                         "instructions": {"question": "Does this message need a reply or action?"},
                         "criteria": {"true": {"what": "a question, request, instruction, or bug report"}, "false": {"what": "an acknowledgment, thanks, greeting, or side chat"}}}}}
   ```

   Response:

   ```json
   {"model": "typesafe/jev-1.13-20260917",
    "answers": {"route": {"type": "choice", "choice": "sdlc", "probabilities": {"other": 0, "collaboration": 0, "sdlc": 1, "question": 0}, "confidence": 1},
                "needs_response": {"type": "noul", "noul": 0.98}},
    "usage": {"input_tokens": 571, "output_tokens": 69, "cost": 2.3982e-05},
    "id": "gen-dec-1789714562-PFn5CNeomXHZS5zGUkdc", "provider": "TypeSafe"}
   ```

   Option ids are the keys of `criteria`; a `noul` answer carries `noul` (probability of yes) and no `confidence`; a `choice` answer carries `probabilities` and `confidence`. `usage.cost` is present, so `paid_inference_meter.settle_from_response` meters exactly. Chat/completions refuses the slug with HTTP 400 (with or without a forced tool). Endpoint listing `GET /api/v1/models/typesafe/jev-1.13/endpoints` needs no auth: prompt `0.000000042`/token, completion `0`, `context_length` 32000, `supported_parameters: []`, `modality: text->decisions`; the slug is absent from the public `/api/v1/models` catalog, so the listing probe must hit the per-model endpoint. Sources: probe transcript relayed by `jev-issue`; https://openrouter.ai/docs/client-sdks/python/sdks/decisions/README.md; https://docs.typesafe.ai/concepts/system-one.

   *Informs:* the decisions transport is one `httpx` POST with this exact body, built from the output type's `Literal` and `bool` fields; no `openrouter` SDK (alpha endpoint, no verified async client, one more dependency outside the coupled pin set).

2. **Quality prior** (issue comment 5727818556, from TypeSafe's own numbers at https://geotoolbox.ai/blog/what-is-jev-ai): Jev agrees with reference answers 67.8% across four workflows against 73.1% for Claude Opus 5; latency 70 to 500 ms; choice caps at 255 options. *Informs:* a flat 90% agreement bar is above Jev's published quality, so the flip policy is error-cost-tiered and the taxonomy plus router is the deliverable even if Jev flips zero sites.

3. **GLiClass** (https://github.com/Knowledgator/GLiClass, https://docs.knowledgator.com/docs/frameworks/gliclass/): `pip install gliclass` pulls `torch` and `transformers`; models `knowledgator/gliclass-small-v1.0`, `-base-v1.0`, `-edge-v3.0` are Apache-2.0; single-label and multi-label via `classification_type`; labels are packed into the input so the practical label bound is the encoder's 512-token window. Knowledgator publishes pre-exported ONNX (`model.onnx`, `model-int8-quantized.onnx`) for `gliclass-base-v1.0`, and `GLiClass.c` shows the onnxruntime packing and post-processing. No Python path from ONNX file to scores is documented; it has to be written. Latency per text on CPU: not published (UNVERIFIED; expect tens of ms for small/int8). *Informs:* the local zero-shot backend is a bounded prototype with an explicit rejection exit (Spike Results, spike-2), not a promise.

4. **onnxruntime** ships arm64 macOS CPU wheels (https://onnxruntime.ai/docs/get-started/with-python.html); the repo already floors `onnxruntime>=1.25.0` in two optional extras. *Informs:* the local zero-shot backend adds `onnxruntime` and `tokenizers` under a new optional extra, never `torch`.

5. **OpenRouter `/api/alpha/*` has no published deprecation policy** (UNVERIFIED; nothing found). *Informs:* pin `typesafe/jev-1.13`, isolate the wire shape in one module, and keep the live-listing probe test so a delisting fails by name.

A `tools.memory_search save` of findings 1 and 3 was attempted at plan time and filtered by the memory store, so this section is the capture point.

## Spike Results

### spike-1: Does the OpenRouter decisions endpoint accept a plain `{model, state, questions}` body?
- **Assumption**: "The SDK README's top-level shape is what the raw HTTP endpoint accepts, and option ids are `criteria` keys."
- **Method**: code-read of the `jev-issue` agent's probe transcript (relayed verbatim; no new spend)
- **Finding**: Yes. HTTP 200 with typed answers, per-option probabilities, and `usage.cost`. `score`, `not_for`, `inspect`, and JSON-object `state` were not exercised.
- **Confidence**: high for `choice` and `noul`; low for anything else
- **Impact on plan**: the decisions transport uses only `choice` and `noul`; `score` is out of scope.

### spike-2: Can GLiClass run on CPU from Python without `torch`?
- **Assumption**: "An int8 ONNX export plus `onnxruntime` and `tokenizers` is enough."
- **Method**: web-research
- **Finding**: Pre-exported ONNX exists for `gliclass-base-v1.0`; the packing and post-processing exist only in C (`GLiClass.c`). Feasible in principle, unmeasured. Not resolved at plan time.
- **Confidence**: medium
- **Impact on plan**: lane B task `build-local-zero-shot` is time-boxed (one build day) with a rejection exit: if the ONNX path cannot produce per-label scores matching the reference pipeline on a 20-item fixture, the lane records GLiClass as rejected and granite stays the local backend. Either outcome satisfies the acceptance criterion "every classification site can resolve a local backend", because `run_typed_local` (granite via PydanticAI) already accepts any output type.

### spike-3: Can `tools/improvement_eval/` host an incumbent-vs-candidate classifier comparison?
- **Assumption**: "The existing paired-arm harness can score agreement and latency."
- **Method**: code-read (`tools/improvement_eval/runner.py`, `arm_worker.py`, `statistics.py`, `tools/improvement_experiment.py::ENVELOPES`)
- **Finding**: No. Its envelopes are `retrieval_parameters` and `agent_task`; its endpoints are ranked-id metrics and agent-task outcomes; a classifier-backend candidate is rejected at envelope validation. `tools/improvement_recursion/arms.py::ArmRunner` is a lighter protocol but scores gains, not agreement. `cross_vendor_judge.py` has no agreement or latency fields.
- **Confidence**: high
- **Impact on plan**: a small standalone runner `tools/classification_eval/` writes `ImprovementEvidence` rows (kind `classifier_comparison`) and attaches its claims to an investigation on case `1ec40086` via `tools/improvement_investigations.py::record_claims`. `statistics.py::clustered_bootstrap_ci` is reused for the agreement interval.

### spike-4: Is `is_open_source` safe on the message hot path?
- **Assumption**: "Eligibility can be checked per call."
- **Method**: code-read (`tools/improvement_eligibility.py:83`)
- **Finding**: It shells `gh repo view --json visibility` with a 10 s timeout on a cache miss (process-local cache, TTL 900 s, fails closed). A miss on the C1/C2 path would blow a 3 s budget.
- **Confidence**: high
- **Impact on plan**: the router reads the cache only (`peek_open_source`), treats a miss as ineligible for that call, and schedules a background refresh; the bridge warms the cache for `ACTIVE_PROJECTS` at startup.

## Data Flow

Inbound Telegram message through the routing classifiers, after this plan (C1 shown; C2, C3 are the same frame):

1. **Entry point**: `bridge/telegram_bridge.py` NewMessage handler resolves `project = find_project_for_dm(...) or find_project_for_chat(chat_title)` (:1477) and calls `should_respond_async(..., project, ...)` (:1770).
2. **`bridge/routing.py::should_respond_async`** (:1266) passes `project_key=project["_key"]` down to `classify_needs_response(text, project_key=...)`.
3. **Call site**: `await run_typed(prompt, NeedsResponseDecision, task=NEEDS_RESPONSE, project_key=project_key, model=MODEL_FAST)` where `NEEDS_RESPONSE = LLMTask(site="routing.needs_response", kind=CLASSIFICATION, incumbent=ANTHROPIC, error_cost=HIGH)` is a module constant.
4. **`agent/llm/wrapper.py::run_typed`** calls `agent/llm/router.py::resolve(task, project_key, settings)`:
   - `kind == THINKING` or `task.client_only` → `Route(task.incumbent, model)`.
   - `peek_open_source(project_key)` is not `True` → `Route(task.incumbent, model)` (fail closed; refresh scheduled).
   - site in `settings.models.classification_local_sites` → `Route(LOCAL_ZERO_SHOT, fallback=Route(task.incumbent))`.
   - site in `settings.models.structured_decision_sites` → `Route(DECISIONS, model=settings.models.structured_decision_model, fallback=Route(OLLAMA, granite))`. A decisions route always carries a local fallback.
   - site in `settings.models.structured_decision_shadow_sites` → `Route(task.incumbent, shadow=Route(DECISIONS, ...))`.
   - otherwise `Route(task.incumbent, model)`.
5. **Backend leg** (`agent/llm/backends/{anthropic,ollama,decisions,local_zero_shot}.py`): each returns a validated instance of the caller's output type or raises `LLMCallError`. The decisions leg builds `questions` from the output type (`Literal` field → `choice` with the values as `criteria` keys; `bool` → `noul`; `confidence: float` filled from the answer; free-text fields left `""`), posts once with `settings.timeouts.decisions_s`, meters via `paid_inference_meter.reserve(..., purpose="structured_decision")` and `settle_from_response`, and validates with `output_type.model_validate`.
6. **Fallback and shadow**: on `LLMCallError` from a primary leg with a `fallback`, the wrapper runs the fallback leg once and tags the result source. A `shadow` route is fired as a bounded background task after the primary result is in hand; its answer is compared to the primary and written as a `classifier_shadow` evidence row; it never changes the returned value and never holds the Anthropic semaphore.
7. **Output**: the caller receives the same output type it receives today and applies its own fail-safe on `LLMCallError`, exactly as before. The wrapper never invents a default.

Thinking sites: step 4 short-circuits at the first rule, so their data flow is unchanged.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1923 / #1925 (PR #2045) | Moved seven classifiers onto Haiku through a typed wrapper | The wrapper standardized the *transport*, not the *decision*. It carried no task kind and no context, so a later change of backend still meant touching every site. It also left the hottest sites (promise gate, read-the-room, completion judge, health judge, C5) raw because they needed a system prompt, a forced tool, or a 3 s budget the wrapper did not expose. |
| #2494 Task 13 | Moved C12 and C13 onto granite via a second wrapper leg | Chose the backend per site by editing the call to a different function. No measurement, no switch, no fallback. The plan itself records "zero recorded latency measurements". |
| #1636 | Split local Ollama into a classifier model and a generation model | Encoded the classification/thinking split as two *constants*, invisible at the call site and unenforced. |

**Root cause pattern:** the backend choice has always been a property of the call site. None of the fixes gave the repo a place to say "this is a decision" and let one resolver pick the backend. This plan makes the kind a declared property of the call and puts the choice in one function.

## Architectural Impact

- **New dependencies**: none in the default install. Lane B adds an optional extra `classification-local = ["onnxruntime>=1.25.0", "tokenizers>=0.20"]`. Lane C adds nothing (`httpx` is already a dependency). The coupled anthropic + pydantic-ai-slim pin set is untouched.
- **Interface changes**: `run_typed` gains keyword-only `task: LLMTask` (required), `project_key: str | None = None`, `system: str | None = None`, `max_retries: int | None = None`, `decision_options: dict[str, list[str]] | None = None`. `run_typed_local` stays as the Ollama leg used by the router and tests; call sites stop calling it directly. Six classifier functions and `should_respond_async`'s callees gain a `project_key` keyword with a `None` default.
- **Coupling**: decreases. Every non-harness call site depends on `agent.llm` only; `bridge/`, `agent/session_completion.py`, `agent/health_check.py`, `tools/classifier.py`, `reflections/memory/memory_quality_audit.py` stop importing `anthropic`, `ollama`, or `requests` for LLM calls. `agent/llm/router.py` gains one late import of `tools.improvement_eligibility` (mirrors `serves_charter.py`).
- **Data ownership**: new `ImprovementEvidence` kinds `classifier_comparison` and `classifier_shadow` (string kinds on an existing model; no Popoto schema change, so no migration). Comparison records belong to improvement case `1ec40086ca1d422e90ef747775ff7f64`.
- **Reversibility**: every switch defaults off; removing the decisions leg is deleting one module and three settings fields. The taxonomy declarations are inert metadata when no switch is on.

## Appetite

**Size:** Large, split into three ordered lanes (Tom's ordering, issue comment 5727973163). Lane A ships under this issue; lanes B and C are filed as their own issues (No-Gos) and planned against the interfaces lane A lands.

**Team:** Solo dev (Valor), plan critic, PR reviewer.

**Interactions:**
- PM check-ins: 2-3 (the flip-policy thresholds, the lane split, and the comparison results)
- Review rounds: 2+ (lane A touches seventeen sites; the hot-path migrations get a dedicated review pass)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `OPENROUTER_API_KEY` in vault `.env` | `.venv/bin/python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('OPENROUTER_API_KEY')"` | Decisions transport and live-listing probe (lane C) |
| `ANTHROPIC_API_KEY` or `CLAUDE_API_KEY` | `.venv/bin/python -c "from agent.anthropic_client import get_anthropic_api_key; assert get_anthropic_api_key()"` | Incumbent Haiku leg in the comparison runner |
| Ollama serving granite | `.venv/bin/python -c "import ollama; assert any('granite4.1:3b' in m.model for m in ollama.list().models)"` | Local fallback leg and granite arm |
| Redis reachable | `.venv/bin/python -c "from config.settings import settings; import redis; redis.from_url(settings.redis.url).ping()"` | Evidence rows, meter, eligibility |
| `gh` authenticated | `gh auth status` | `is_open_source` visibility reads |

Run via `python scripts/check_prerequisites.py docs/plans/llm-task-taxonomy-routing-layer.md`.

## Solution

### Key Elements

- **Taxonomy** (`agent/llm/tasks.py`): `TaskKind` (`classification`, `thinking`), `Backend` (`anthropic`, `ollama`, `decisions`, `local_zero_shot`), `ErrorCost` (`low`, `medium`, `high`), and a frozen `LLMTask(site, kind, incumbent, error_cost, client_only, noul_threshold)`. Each call site declares one as a module constant next to its output type. The site id is a stable dotted name (`routing.needs_response`, `promise_gate.verdict`); it is the key the settings switches use.
- **Routing point** (`agent/llm/router.py::resolve`): pure function of `(task, project_key, settings)` returning a `Route(backend, model, fallback, shadow)`. It is the only place that reads the switches and the only place that consults eligibility.
- **Backend legs** (`agent/llm/backends/`): the existing Anthropic and Ollama bodies moved out of `wrapper.py` unchanged, plus `decisions.py` (lane C) and `local_zero_shot.py` (lane B). One protocol: `async def call(prompt, output_type, route, *, system, sdk_timeout, hard_timeout, max_retries, decision_options) -> BaseModel`.
- **Wrapper** (`agent/llm/wrapper.py::run_typed`): resolves the route, runs the primary leg, runs the fallback leg once on `LLMCallError` when present, fires the shadow leg when present, and returns the validated output type. `LLMCallError` semantics for the caller are unchanged.
- **Eligibility peek** (`tools/improvement_eligibility.py::peek_open_source`, `warm_cache`): cache-only read for the hot path; background refresh; bridge warms `ACTIVE_PROJECTS` at startup.
- **Enumeration test** (`tests/unit/test_llm_task_taxonomy.py`): AST walk over `agent/ bridge/ worker/ tools/ reflections/ scripts/` that (1) requires `task=` on every `run_typed(` call, (2) forbids `run_typed_local(` outside `agent/llm/`, (3) requires a module-level `LLMTask(` declaration in any module that contains a raw transport token (`messages.create(`, `ollama.chat(`, `chat.completions.create(`, `audio.transcriptions`, `embeddings.create(`, `OPENROUTER_URL`) outside the allowlist (`agent/llm/backends/`, `agent/anthropic_client.py`, `agent/session_runner/harness/`, `tools/ollama_client.py`), (4) asserts site ids are unique and every `classification` task is reached through `run_typed`, and (5) asserts the taxonomy table in `docs/features/llm-task-taxonomy.md` lists every declared site id (doc/code parity, same shape as `tests/unit/test_sdlc_skill_md_parity.py`).
- **Settings** (`config/settings.py::ModelSettings`): `structured_decision_model: str = "typesafe/jev-1.13"`, `structured_decision_sites: str = ""`, `structured_decision_shadow_sites: str = ""`, `classification_local_sites: str = ""`, `classification_local_model: str = "knowledgator/gliclass-small-v1.0"`, each a CSV of site ids with a parsed `*_set` property; `TimeoutSettings.decisions_s: float = 3.0`. `config/models.py`: `JEV = "typesafe/jev-1.13"`, `OPENROUTER_DECISIONS_URL`, `MODEL_INFO[JEV]`. `.env.example` entries with `# @optional`.
- **Comparison runner** (`tools/classification_eval/`, lane B): `compare(site, inputs, arms) -> ComparisonRecord` with per-arm agreement against the incumbent, p50/p95 latency, cost per call, error rate, price with retrieval date, and `n`; writes `ImprovementEvidence(kind="classifier_comparison")` and records claims on an investigation of case `1ec40086`. Inputs: the parametrized examples in each site's existing unit tests plus a sample of real inbound messages from rooms whose project `is_open_source`, with at least 50 per site and at least 200 for C1 through C4.
- **Flip policy** (documented in `docs/features/llm-task-taxonomy.md`, enforced by review of the comparison record): a site moves to a candidate backend by default only when all hold: agreement at or above the tier bar (`high` 95%, `medium` 90%, `low` 85%), cost per call at or below one tenth of the incumbent's for Haiku sites, p95 latency at or below the incumbent's for granite sites, error rate on the candidate at or below 2%, and (for an external backend) a local fallback resolvable for that site. Tiers: `high` = C1, C2, C3, C4, C12 (a wrong answer drops or misroutes a human's message or binds to the wrong job); `medium` = C5, C7, C8, C10, C11, C13, C14; `low` = C6, C9 (regex floor beneath it), C15 (pre-screen only). C16 (email triage) is `client_only` and never a candidate.

### Flow

Inbound message → bridge resolves project → classifier declares its task → `run_typed` asks the router → router picks incumbent (default) or a switched-on candidate with a local fallback → leg returns the typed answer → caller applies its own fail-safe → message routed. Shadow answers, when enabled, land in evidence rows and never in the message path.

### Technical Approach

- **Kind at the call site, as an argument.** The issue left open whether kind is an argument, a marker on the output model, or a separate entry point. Argument wins: an output model can serve two sites with different error costs (`IntentDecision` and `IntentDecisionWithRecall` share a site; `RoutingDecision` could serve a thinking use), and a second entry point would recreate the per-site function choice this plan removes. The `LLMTask` constant is declared next to the output model so the two read together.
- **Read-the-room is one thinking task.** The action and the rewrite come from one call under a 3 s budget; splitting them doubles the budget on every outbound message. It migrates onto `run_typed(task=READ_THE_ROOM, system=READ_THE_ROOM_SYSTEM_PROMPT, sdk_timeout=RTR_SDK_TIMEOUT, ...)` with `RoomVerdict` as a `BaseModel` (`action: Literal["send", "trim", "suppress"]`), keeping every short-circuit and the `rtr_error` fail-safe. The wrapper's `hard_timeout` must bound semaphore acquisition as well as the SDK call so the 3 s budget holds end to end; the builder verifies this with a slot-starvation test.
- **The six raw classification sites migrate with their fail-safes byte-identical.** C5's dict result becomes `WorkTypeDecision(type: Literal[bug, feature, chore, sdlc], confidence, reason)` and `classify_request_async` returns the same dict shape it returns today; the dead sync `classify_request` is deleted. C9 keeps `max_retries=0` and the 3 s slot timeout via the new kwargs. C10 and C11 keep their `True`/`False` defaults. C14 becomes `MemoryAuditDecision(is_junk: bool, anomaly_signal: str | None, why: str)` on `run_typed(task=MEMORY_AUDIT)` with `incumbent=OLLAMA`. C15 becomes `PromiseJudgeDecision(answer: bool, span: str, confidence: float)` and stays on OpenRouter chat/completions, because that is its incumbent and it is metered: the Ollama leg already speaks OpenAI-compatible chat through PydanticAI's `OpenAIChatModel`, so the same leg with `base_url=OPENROUTER_URL` and the OpenRouter key serves C15. The leg gains an optional `base_url`/`api_key` pair and the meter reserve/settle moves into it under purpose `promise_detector`.
- **C7's `risk: str` becomes `Literal["suspected", "none"]`.** Same comparison, tighter schema; the decisions leg needs the values.
- **Thinking sites are tagged, not migrated.** Sites on `run_typed` add `task=`; sites on raw transports for a reason (vision, audio, streaming, the harness) add a module-level `LLMTask(kind=THINKING)` declaration. The enumeration test enforces both.
- **Project key threading.** Sites that can know their project pass it; sites that cannot leave it `None` and stay on the incumbent by the fail-closed rule. `should_respond_async` and `classify_work_request` gain `project_key`; the promise gate and read-the-room resolve it from `chat_id` through `find_project_for_chat`; session-side sites (C10, C11) read `AgentSession.project_key`; C14 reads the memory row's project; C15 already has it.
- **Decisions leg builds questions from the schema.** One `choice` or `noul` question per `Literal`/`bool` field, `criteria` values from the field's `description` when present else the literal value, `instructions.question` from `task.question` when set else the field description, `state` = the prompt text. `decision_options` overrides the option list for dynamic fields (C12's `job_id`). `noul` maps to `bool` at `task.noul_threshold` (default 0.5). `confidence` fields take the answer's `confidence` (choice) or `max(p, 1-p)` (noul). Reason fields are `""`. Anything else raises `LLMCallError` at question-build time, before any spend.
- **Per-backend thresholds.** Incumbents self-report confidence; Jev's is a probability. `JOB_ROUTER_CONFIDENCE_THRESHOLD` and `INTENT_CONFIDENCE_THRESHOLD` keep their meaning on the incumbent. A site with a threshold cannot be switched to a candidate until lane C's comparison sets a per-backend threshold (`LLMTask.thresholds: dict[Backend, float]`), and the router logs and stays on the incumbent if a switched site lacks one.
- **Shadow mode is the only Jev mode until the market settles** (Tom). `structured_decision_shadow_sites` runs Jev beside the incumbent on eligible messages and records agreement; `structured_decision_sites` is the flip switch and, by the flip policy, stays empty until the recorded comparison for that site clears the bar and a local fallback exists.
- **Metering.** The decisions leg reserves `32000 * 0.042e-6` USD (the context-length bound) per call under purpose `structured_decision`, settles from `usage.cost`, and treats a `Refusal` as an `LLMCallError` (so the fallback runs). Shadow calls meter the same way and are dropped, not queued, when refused.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every migrated site keeps its `except Exception` or `except LLMCallError` block; for each, a test asserts the fail-safe value AND the log line (`logger.warning` with the site id) when the leg raises `LLMCallError`: C5 `{}`-equivalent dict, C9 `None` → heuristic, C10 `False`, C11 `healthy=True`, C14 `None`, C15 `promises-judge-failed`, read-the-room `send`/`rtr_error`.
- [ ] Router fallback: a test makes the primary leg raise and asserts the fallback leg ran exactly once, the result carries `source="fallback"`, and a warning names the site and backend.
- [ ] Shadow leg exceptions are caught inside the background task, counted on a per-site Redis counter, and never propagate; a test asserts the primary result is unaffected when the shadow leg raises or times out.
- [ ] Decisions leg: HTTP non-200, JSON decode error, missing answer id, unknown choice value, timeout, and meter `Refusal` each raise `LLMCallError` with a distinct message; six unit tests with recorded responses.
- [ ] `peek_open_source` never raises; a test asserts a cache miss returns `None` and schedules exactly one refresh per key.

### Empty/Invalid Input Handling
- [ ] `run_typed` keeps its `ValueError` on empty or whitespace prompt before any routing; test unchanged.
- [ ] Decisions question builder on an output type with no `Literal`/`bool` field raises `LLMCallError` before any HTTP call (tested).
- [ ] `decision_options` with an empty list for a dynamic field raises before spend (C12 with zero candidates never reaches the leg today; the test pins that).
- [ ] Settings CSV parsing: empty string → empty set; whitespace and duplicate ids tolerated; unknown site ids logged once at startup by `tools/doctor` (tested).

### Error State Rendering
- [ ] `python -m tools.doctor` gains a "LLM routing" section listing every switched site, its resolved backend, and whether a local fallback resolves; a test renders it with one switched site and one unknown site id.
- [ ] The comparison runner's report renders the rejection case (candidate error rate above 2%) with the reason, not a blank table; tested with a recorded failing arm.

## Test Impact

- [ ] `tests/unit/test_job_router.py` (fakes `bridge.job_router.run_typed_local`): UPDATE: patch `bridge.job_router.run_typed`; the fake keeps `**kwargs`.
- [ ] `tests/unit/test_intake_classifier.py` (fakes `run_typed_local`): UPDATE: patch `run_typed` in `tools.classifier`.
- [ ] `tests/integration/test_job_routing.py`: UPDATE: same patch target change.
- [ ] `tests/unit/test_promise_gate.py` (monkeypatches `promise_gate.anthropic.AsyncAnthropic` and `get_anthropic_api_key`; 56 tests): REPLACE the transport-level fakes with a `run_typed` fake returning `PromiseVerdictDecision`; the timeout test asserts `LLMCallError` from a timing-out leg still routes to the heuristic.
- [ ] `tests/unit/test_promise_gate_measurement.py`, `test_promise_gate_audit.py`, `test_promise_gate_session_events.py`: UPDATE where they construct the client fake; behavior assertions unchanged.
- [ ] `tests/unit/test_read_the_room.py` (monkeypatches `rtr_module.anthropic.AsyncAnthropic`; 37 tests): REPLACE the fake constructor with a `run_typed` fake returning `RoomVerdict`; every short-circuit and error-path assertion stays.
- [ ] `tests/unit/test_session_completion.py`, `tests/unit/test_session_completion_zombie.py`: UPDATE the novelty-judge fakes to `run_typed`.
- [ ] `tests/unit/test_health_check.py` (38 tests): UPDATE the `_judge_health` fakes from `anthropic_slot` to `run_typed`; the "unparseable judge response" case becomes an `LLMCallError` case with the same `healthy=True` outcome.
- [ ] `tests/unit/test_pr_classification_fastpath.py`: UPDATE: `classify_request_async` fake returns the same dict; tests for the deleted sync `classify_request` are DELETED.
- [ ] `tests/unit/test_memory_quality.py`: UPDATE the `_gemma_classify` fakes from `ollama.chat` to `run_typed`.
- [ ] `tests/unit/test_improvement_evidence.py` (84 tests): UPDATE the `_OpenRouterJudge` fakes to the leg-level fake; the metering assertions move to the leg.
- [ ] `tests/unit/test_emoji_embedding.py`: DELETE the `find_best_emoji` / `_compute_embedding` / cache tests (dead path removed); KEEP the `find_best_emoji_for_message` tests.
- [ ] `tests/unit/test_llm_wrapper.py`, `tests/unit/test_llm_wrapper_local.py`: UPDATE: calls pass `task=`; add router and fallback tests.
- [ ] `tests/unit/test_routing.py`, `test_intent_classifier.py`, `test_agent_catchup.py`, `test_injection_inspection.py`, `test_context_recall.py`, `test_context_recall_wiring.py`, `test_email_cs_triage.py`, `tests/unit/memory_extraction/test_memory_extraction_event_loop_safety.py`: no change expected (fakes accept `**kwargs`; `call_args[0]` positional assertions still hold). Listed so the builder runs them first as the "unchanged with default settings" proof.
- [ ] `tests/unit/test_models.py`: UPDATE: add `test_openrouter_jev_endpoint_is_listed` against `/api/v1/models/typesafe/jev-1.13/endpoints` (no auth needed), `integration` marker, fail-closed like its sibling.

## Rabbit Holes

- **Re-scoring the incumbents' prompts while migrating.** Every prompt string moves verbatim. Prompt improvements are a different experiment with their own comparison.
- **A general "provider registry" with dynamic plugin discovery.** Four legs, one enum, one `if` chain in `resolve`. Add the fifth leg when it exists.
- **Making eligibility finer than the project.** §7 is per message's project. Per-message content classification of "private context" is a research question, not a routing rule.
- **Jev `score`, `not_for`, `inspect`, JSON-object `state`.** Unexercised by the probe; out of scope.
- **Emoji choice as a structured decision.** No model on `main` today, no ground truth, a new network call on every message. Filed separately (No-Gos).
- **Replacing `is_open_source`'s `gh` shell-out with a projects.json field.** Would change the source of truth for §7 across the improvement tooling. The cache peek is enough here.
- **Rewriting `tools/improvement_eval/` to accept a classifier envelope.** Spike-3 says no; the standalone runner is 200 lines.

## Risks

### Risk 1: Hot-path regressions from migrating promise gate, read-the-room, and the completion judge
**Impact:** Every outbound message crosses at least two of these; a regression drops or delays replies.
**Mitigation:** These three migrate last in lane A, each in its own commit, each with a slot-starvation test proving the 3 s budget holds through the wrapper, and each behind the byte-identical fail-safe. `run_typed` gains `max_retries` so C9's `max_retries=0` survives. The PR review checks the p95 of `read_the_room` on the local bridge before and after via `tests/unit/test_read_the_room.py`'s timing fixture.

### Risk 2: Required `task=` breaks an unlisted caller
**Impact:** A `TypeError` at call time in a path the enumeration test did not cover.
**Mitigation:** The enumeration test runs the same AST walk the builder uses to find sites; `python -m ruff check` plus the full `tests/unit/` run are gates; `run_typed` raises a clear `TypeError` naming the missing kwarg.

### Risk 3: Eligibility fails closed so hard that no site ever sees a candidate
**Impact:** The comparison collects nothing on the shadow path.
**Mitigation:** Bridge warms the cache for `ACTIVE_PROJECTS` at startup; `tools/doctor` reports per-project eligibility; the comparison runner uses the blocking `is_open_source` (it is offline) and reports how many inputs were excluded and why.

### Risk 4: GLiClass ONNX from Python cannot be made to match the reference pipeline
**Impact:** Lane B has no candidate to compare against granite.
**Mitigation:** Spike-2's rejection exit. Granite is already a local backend; lane B still produces the granite latency numbers #2494 lacks and the runner both later lanes use.

### Risk 5: Jev shadow spend or a runaway shadow loop
**Impact:** Unit-2 budget ($10/day) consumed by shadow calls, or shadow tasks piling up.
**Mitigation:** Every shadow call reserves through the meter; a `Refusal` drops the call. Shadow tasks are created with `asyncio.create_task`, bounded by `decisions_s`, and capped by a per-process semaphore of 4; a counter on refusal and overflow shows in `valor-improve budget` via receipts under purpose `structured_decision`.

### Risk 6: Test fakes drift from the leg protocol
**Impact:** Green tests that reach no leg.
**Mitigation:** One shared fake in `tests/helpers/llm_fakes.py` implementing the leg protocol, used by every migrated test; a mutation check per migrated site during review (each fail-safe test must fail when the fail-safe line is deleted).

### Risk 7: OpenRouter changes the alpha shape
**Impact:** Decisions leg returns `LLMCallError` on every call.
**Mitigation:** The leg's fallback runs; the live-listing probe and one recorded-response test fail by name; the wire shape lives in one module.

## Race Conditions

### Race 1: Shadow task outlives the request and holds the Anthropic semaphore
**Location:** `agent/llm/wrapper.py::run_typed` shadow dispatch
**Trigger:** Primary result returns; shadow task still running when the caller's coroutine completes.
**Data prerequisite:** The primary result must be captured before the shadow task is created.
**State prerequisite:** Shadow legs never enter `semaphore_slot()` (the decisions leg is HTTP to OpenRouter, not Anthropic).
**Mitigation:** Shadow runs only the decisions or local legs, never the Anthropic leg; bounded by `asyncio.wait_for(decisions_s)`; per-process `asyncio.Semaphore(4)`; exceptions swallowed and counted; a test asserts the semaphore count is unchanged during a shadow call.

### Race 2: Eligibility refresh storms on a cold cache
**Location:** `tools/improvement_eligibility.py::peek_open_source`
**Trigger:** Burst of messages for one project before the first refresh lands.
**Data prerequisite:** none
**State prerequisite:** At most one in-flight refresh per project key.
**Mitigation:** A per-key `_REFRESHING: set[str]` guarded by the existing module lock; refresh runs in `run_in_executor`; a test fires 50 concurrent peeks and asserts one `gh` call.

### Race 3: Meter reserve without settle on a cancelled shadow task
**Location:** `agent/llm/backends/decisions.py`
**Trigger:** `asyncio.wait_for` cancels the shadow leg after the reservation, before the response.
**Data prerequisite:** reservation id captured before the HTTP call.
**State prerequisite:** the reservation must be released or settled exactly once.
**Mitigation:** `try/finally` releases the reservation on `CancelledError`; `sweep_unsettled_reservations` (existing) covers a hard crash. Tested with a cancelled task.

### Race 4: Settings read mid-flip
**Location:** `agent/llm/router.py::resolve`
**Trigger:** `.env` edited while the bridge runs.
**Data prerequisite:** none
**State prerequisite:** Settings are read once per process (pydantic-settings), so a flip needs a restart, which is the existing contract for every `MODELS__*` key.
**Mitigation:** Documented; `tools/doctor` shows the live-resolved routes.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3420] Lane B: local backend evaluation (granite baseline measurements, GLiClass ONNX candidate, comparison runner, records on case `1ec40086`). Depends on lane A's `LLMTask`, router, and leg protocol. Its design is fixed in this document; its own plan is written against the merged interfaces.
- [SEPARATE-SLUG #3421] Lane C: decisions transport, shadow mode, Jev paired comparison, per-site flip decisions, C15 pre-screen cascade if the numbers justify it. Depends on lanes A and B (a flip needs a local fallback and a per-backend threshold).
- [SEPARATE-SLUG #3422] Emoji reaction choice as a structured-decision site (72-way `choice` over `EMOJI_LABELS`). Today's path makes no model call; adding one is a new capability with no ground truth, and Tom's ordering says local first.
- [EXTERNAL] Any change to the RSI charter's §7 boundary or to the $10/day unit-2 budget. Tom-owned.
- [EXTERNAL] Choosing a second structured-decision provider when one appears. The leg protocol admits it; the choice is a research investigation on the case.

Anti-criteria for the code-level No-Gos are in Verification: no emoji embedding call path remains, no site is switched on by default, and no raw Anthropic client remains in the migrated modules.

## Update System

- `pyproject.toml`: lane A adds no dependency. Lane B (#3420) adds the optional extra `classification-local`; `/update`'s `uv sync` does not install extras by default, so the local zero-shot leg raises `LLMCallError("classification-local extra not installed")` and the router falls back to the incumbent on machines without it. `tools/doctor` reports the extra's presence.
- Model weights for the local zero-shot leg (lane B) download lazily to `data/models/gliclass/` on first use with a checksum; no update-script step. Documented in `docs/infra/llm-task-routing.md`.
- Config propagation: five new `MODELS__*` keys and one `TIMEOUTS__DECISIONS_S`, all with in-code defaults and `# @optional` in `.env.example`; nothing to copy into the vault `.env`.
- Migrations: none. No Popoto schema changes (new evidence kinds are string values on an existing model).
- Services: after lane A merges, `./scripts/valor-service.sh restart` (the bridge imports the wrapper); `/update` already does this.

## Agent Integration

- No new `[project.scripts]` entry for the agent. The comparison runner is a developer tool invoked as `python -m tools.classification_eval` (lane B); results reach the agent through `valor-improve case show` and `valor-improve investigation list --case 1ec40086ca1d422e90ef747775ff7f64`, which already exist.
- The bridge calls the new code directly: `bridge/telegram_bridge.py` startup calls `tools.improvement_eligibility.warm_cache(ACTIVE_PROJECTS)`; every classifier in `bridge/` reaches the router through `agent.llm.run_typed`.
- Integration tests: `tests/unit/test_llm_task_taxonomy.py` (enumeration and parity); `tests/unit/test_llm_router_eligibility.py` feeds a message mapped to a client project through each switched classification site with the switch on and asserts the subscription backend was called, and asserts `email_cs.triage` and `tools/email_cs/agents.py` resolve to the subscription backend under every switch combination; `tests/integration/test_bridge_routing_project_key.py` asserts `should_respond_async` passes `project["_key"]` through to the three routing classifiers.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/llm-task-taxonomy.md`: the two kinds, the `LLMTask` declaration, the full site table (id, kind, incumbent, error-cost tier, §7 class) that the parity test reads, the router rules in order, eligibility and the fail-closed rule, the switches and shadow mode, the flip policy tiers and bars, and where comparison records live.
- [ ] Update `docs/features/nonharness-llm-wrapper.md`: the `run_typed` signature, the leg protocol, the "Migrated Call Sites" table extended to every site (read-the-room no longer "skipped"; C5, C9, C10, C11, C14, C15 added), and a pointer to the taxonomy page.
- [ ] Update `docs/features/local-model-policy.md`: granite is the local fallback leg for every classification site; the classifier/generation constant split now lives in the taxonomy.
- [ ] Update `docs/features/config-timeout-catalog.md`: `TIMEOUTS__DECISIONS_S`.
- [ ] Update `docs/features/env-completeness-validation.md` only if a new sigil is needed (expected: none; the six keys are `# @optional`).
- [ ] Add a row to `docs/features/README.md` for the taxonomy page.
- [ ] Create `docs/infra/llm-task-routing.md`: OpenRouter decisions endpoint (URL, auth, pricing with retrieval date, 32k context, alpha status), unit-2 metering purpose `structured_decision`, local model weights location and size, and the rollback (empty the switches, restart).

### Inline Documentation
- [ ] Module docstrings for `agent/llm/tasks.py`, `router.py`, `backends/__init__.py` stating the protocol and the fail-closed rule.
- [ ] Each `LLMTask` declaration carries a one-line comment naming the fail-safe the caller applies.

## Success Criteria

- [ ] Every non-harness LLM call site in `agent/ bridge/ worker/ tools/ reflections/ scripts/` declares an `LLMTask`; `tests/unit/test_llm_task_taxonomy.py` passes and fails when a `task=` kwarg is removed from any one site (mutation-checked in review).
- [ ] With default settings every existing unit test in the "no change expected" list passes unchanged, and `resolve()` returns `task.incumbent` for every declared site (table-driven test over all declarations).
- [ ] C5, C9, C10, C11, C14, C15, and read-the-room go through `run_typed`; `grep` finds no `AsyncAnthropic(`, `anthropic_slot(`, `ollama.chat(`, or `requests.post(OPENROUTER_URL` in those seven modules.
- [ ] A client-project message through each switched classification site resolves to the subscription backend; `email_cs.triage` resolves to it under every switch combination.
- [ ] Every classification site resolves a local backend (`resolve(task, key, settings_with_site_switched).fallback.backend == OLLAMA`, table-driven).
- [ ] The dead emoji embedding path is gone; `find_best_emoji_for_message` behavior and tests unchanged.
- [ ] `tools/doctor` shows the routing section.
- [ ] Tests pass (`/do-test`); documentation updated (`/do-docs`); `python -m ruff check` and `python -m ruff format --check` clean.
- [ ] Issues #3420, #3421, #3422 exist and reference this plan; #3410 closes with lane A.

## Team Orchestration

### Team Members

- **Builder (taxonomy and router)**
  - Name: taxonomy-builder
  - Role: `agent/llm/tasks.py`, `router.py`, `backends/` split, `run_typed` signature, settings, eligibility peek, enumeration test
  - Agent Type: builder
  - Domain: async/concurrency (Race 1, Race 2)
  - Resume: true

- **Builder (site migration)**
  - Name: sites-builder
  - Role: tag the `run_typed` sites, migrate the six raw classification sites and read-the-room, thread `project_key`, delete the dead emoji path
  - Agent Type: builder
  - Resume: true

- **Validator (behavior parity)**
  - Name: parity-validator
  - Role: run the "no change expected" test list first, then the full suite; mutation-check each fail-safe and the enumeration test
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: taxonomy-docs
  - Role: the Documentation section
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

`builder`, `validator`, `code-reviewer`, `test-engineer`, `documentarian`, `plan-maker`, `frontend-tester`. Domain framing per `DOMAIN_FRAMING.md` for the async tasks.

## Step by Step Tasks

Lane A only (this issue). Lanes B and C are #3420 and #3421.

### 1. Taxonomy types and leg split
- **Task ID**: build-taxonomy
- **Depends On**: none
- **Validates**: `tests/unit/test_llm_wrapper.py`, `tests/unit/test_llm_wrapper_local.py`, `tests/unit/test_llm_import_safety.py`, `tests/unit/test_llm_stack_degraded_start.py`, `tests/unit/test_llm_tasks.py` (create)
- **Informed By**: spike-1 (only `choice`/`noul` needed later), spike-4 (eligibility peek)
- **Assigned To**: taxonomy-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/llm/tasks.py` with `TaskKind`, `Backend`, `ErrorCost`, `LLMTask` (frozen dataclass, `site`, `kind`, `incumbent`, `error_cost=MEDIUM`, `client_only=False`, `noul_threshold=0.5`, `question=None`, `thresholds=None`).
- Move the Anthropic body of `run_typed` to `agent/llm/backends/anthropic.py::call` and the Ollama body of `run_typed_local` to `agent/llm/backends/ollama.py::call`, each implementing the leg protocol; `run_typed_local` becomes a thin call into the Ollama leg (kept for the router and tests, forbidden at call sites by the enumeration test).
- Add `system`, `max_retries`, `decision_options` to the Anthropic leg (PydanticAI `Agent(system_prompt=...)`, `AsyncAnthropic(max_retries=...)`); add optional `base_url`/`api_key` to the Ollama leg so it can serve OpenRouter chat/completions for C15.
- Ensure `hard_timeout` wraps semaphore acquisition; add a slot-starvation test.

### 2. Router, settings, eligibility peek
- **Task ID**: build-router
- **Depends On**: build-taxonomy
- **Validates**: `tests/unit/test_llm_router.py` (create), `tests/unit/test_llm_router_eligibility.py` (create), `tests/unit/test_improvement_eligibility.py` (extend), `tests/unit/test_env_declaration_readers.py`, `tests/unit/test_settings*.py`
- **Informed By**: spike-4
- **Assigned To**: taxonomy-builder
- **Agent Type**: builder
- **Parallel**: false
- `agent/llm/router.py::resolve(task, project_key, settings) -> Route` with the rule order in Data Flow step 4; `Route(backend, model, fallback=None, shadow=None)`.
- `ModelSettings` fields and `_set` properties; `TimeoutSettings.decisions_s`; `.env.example` entries with `# @optional`; `config/models.py` `JEV`, `OPENROUTER_DECISIONS_URL`, `MODEL_INFO[JEV]`.
- `tools/improvement_eligibility.py::peek_open_source`, `warm_cache`, single in-flight refresh per key (Race 2 test).
- `run_typed(prompt, output_type, *, task, project_key=None, model=..., system=None, sdk_timeout, hard_timeout, max_retries=None, decision_options=None)`: resolve, primary leg, fallback-once, shadow dispatch (Race 1 test; the decisions and local legs do not exist yet, so `resolve` returns incumbent-only routes until #3420/#3421 register their legs, and a switched-on site with no registered leg logs once and stays on the incumbent).
- `tools/doctor` routing section.

### 3. Tag the wrapper sites and thread project keys
- **Task ID**: build-tag-sites
- **Depends On**: build-router
- **Validates**: `tests/unit/test_routing.py`, `test_intent_classifier.py`, `test_agent_catchup.py`, `test_injection_inspection.py`, `test_context_recall.py`, `test_context_recall_wiring.py`, `test_email_cs_triage.py`, `test_job_router.py`, `test_intake_classifier.py`, `tests/integration/test_job_routing.py`, `tests/integration/test_bridge_routing_project_key.py` (create), `tests/unit/memory_extraction/*`
- **Assigned To**: sites-builder
- **Agent Type**: builder
- **Parallel**: false
- Declare `LLMTask` constants at C1, C2, C3, C4, C6, C7, C8, C12, C13, C16 and at every thinking site on `run_typed` (`agent/memory_extraction.py`, `tools/memory_eval/query_set.py`, and the rest of the issue's thinking list that already uses the wrapper); C7's `risk` becomes a `Literal`; C12 and C13 call `run_typed` with `incumbent=OLLAMA`.
- Add `project_key` keywords and pass `project["_key"]` from `should_respond_async`; C6 from the chat's project; C8 from the outbound chat.
- Module-level `LLMTask(kind=THINKING)` declarations in raw-transport thinking modules (`bridge/media.py`, `tools/image_analysis/`, `tools/image_tagging/`, `tools/test_judge/`, `reflections/pm_briefings/builder.py`, `reflections/utilities.py`, `scripts/memory_consolidation.py`, `tools/knowledge/indexer.py`, `tools/valor_calendar.py`, `tools/doc_summary/`, `tools/documentation/`, `tools/improvement_eval/arm_worker.py` and judges, `tools/email_cs/agents.py` with `client_only=True`), exactly as the enumeration test's allowlist requires.

### 4. Migrate the six raw classification sites
- **Task ID**: build-migrate-raw
- **Depends On**: build-tag-sites
- **Validates**: `tests/unit/test_pr_classification_fastpath.py`, `test_session_completion.py`, `test_session_completion_zombie.py`, `test_health_check.py`, `test_memory_quality.py`, `test_improvement_evidence.py`, `test_promise_gate*.py`, `tests/helpers/llm_fakes.py` (create)
- **Assigned To**: sites-builder
- **Agent Type**: builder
- **Parallel**: false
- Order: C5, C11, C10, C14, C15, then C9 (hot path last). One commit per site. Each keeps its fail-safe value and its log line; each test asserts both under `LLMCallError`.
- C5: `WorkTypeDecision` model; delete sync `classify_request` and the `anthropic` import; `classify_request_async` returns the same dict.
- C15: the Ollama leg with `base_url=OPENROUTER_URL`; the meter reserve/settle moves into the leg under purpose `promise_detector`; `_OpenRouterJudge` deleted.
- C9: `max_retries=0`, `sdk_timeout=RTR_SDK_TIMEOUT`, `hard_timeout=RTR_SDK_TIMEOUT`; timeout still routes to the heuristic with source `timeout`.

### 5. Migrate read-the-room and delete the dead emoji path
- **Task ID**: build-migrate-rtr
- **Depends On**: build-migrate-raw
- **Validates**: `tests/unit/test_read_the_room.py`, `tests/unit/test_emoji_embedding.py`, `tests/unit/test_react_with_emoji.py`
- **Assigned To**: sites-builder
- **Agent Type**: builder
- **Parallel**: false
- `RoomVerdict` becomes a `BaseModel` with `action: Literal["send", "trim", "suppress"]`; `read_the_room` calls `run_typed(task=READ_THE_ROOM, system=READ_THE_ROOM_SYSTEM_PROMPT, project_key=..., sdk_timeout=RTR_SDK_TIMEOUT, hard_timeout=RTR_SDK_TIMEOUT)`; `trim` without `revised_text` still becomes `send`; every error path still yields `send`/`rtr_error`.
- Delete `find_best_emoji`, `_compute_embedding`, the embedding cache, `OPENROUTER_EMBEDDINGS_URL`, `EMBEDDING_MODEL`, `REACTION_TOP_K`, `REACTION_TEMPERATURE`, `_softmax_sample`; fix the stale comment at `bridge/telegram_bridge.py:1894`; keep `EMOJI_LABELS` (it is the option list #3422 will need) with a comment saying so.

### 6. Enumeration and parity tests
- **Task ID**: build-enumeration-test
- **Depends On**: build-migrate-rtr
- **Validates**: `tests/unit/test_llm_task_taxonomy.py` (create)
- **Assigned To**: taxonomy-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement the five checks from Key Elements; seed a fake untagged call in a temp module to prove the test bites (red-state proof pasted into the PR).

### 7. Validate lane A
- **Task ID**: validate-lane-a
- **Depends On**: build-enumeration-test
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the "no change expected" list, then `scripts/pytest-clean.sh tests/unit/`; mutation-check each migrated fail-safe and the enumeration test; confirm the Verification table.

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-lane-a
- **Assigned To**: taxonomy-docs
- **Agent Type**: documentarian
- **Parallel**: false
- The Documentation section; the taxonomy table must list every declared site id (parity test).

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm Success Criteria; generate the report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `scripts/pytest-clean.sh tests/unit/ -x -q` | exit code 0 |
| Lint clean | `.venv/bin/python -m ruff check .` | exit code 0 |
| Format clean | `.venv/bin/python -m ruff format --check .` | exit code 0 |
| Taxonomy test exists and passes | `scripts/pytest-clean.sh tests/unit/test_llm_task_taxonomy.py -q` | exit code 0 |
| Router tests pass | `scripts/pytest-clean.sh tests/unit/test_llm_router.py tests/unit/test_llm_router_eligibility.py -q` | exit code 0 |
| Every site declares a task | `grep -rn "run_typed(" agent bridge worker tools reflections scripts --include=*.py \| grep -v "agent/llm/" \| grep -vc "task="` | match count == 0 |
| No direct `run_typed_local` at call sites | `grep -rn "run_typed_local(" agent bridge worker tools reflections scripts --include=*.py \| grep -vc "agent/llm/"` | match count == 0 |
| Raw Anthropic client gone from migrated modules | `grep -c "AsyncAnthropic(\|anthropic_slot(" bridge/promise_gate.py bridge/read_the_room.py agent/session_completion.py agent/health_check.py tools/classifier.py` | match count == 0 |
| Raw Ollama and OpenRouter calls gone from migrated modules | `grep -c "ollama.chat(\|requests.post(" reflections/memory/memory_quality_audit.py reflections/improvement_collect.py` | match count == 0 |
| Anti-criterion: emoji embedding path deleted | `grep -c "OPENROUTER_EMBEDDINGS_URL\|def find_best_emoji(\|_softmax_sample" tools/emoji_embedding.py` | match count == 0 |
| Anti-criterion: no site switched on by default | `grep -c 'structured_decision_sites: str = ""\|structured_decision_shadow_sites: str = ""\|classification_local_sites: str = ""' config/settings.py` | output contains 3 |
| Anti-criterion: sync `classify_request` deleted | `grep -c "^def classify_request(" tools/classifier.py` | match count == 0 |
| Env keys declared | `grep -c "MODELS__STRUCTURED_DECISION_SITES\|MODELS__STRUCTURED_DECISION_SHADOW_SITES\|MODELS__CLASSIFICATION_LOCAL_SITES\|TIMEOUTS__DECISIONS_S" .env.example` | output contains 4 |
| Taxonomy doc exists with the site table | `grep -c "routing.needs_response" docs/features/llm-task-taxonomy.md` | output > 0 |
| Feature index updated | `grep -c "llm-task-taxonomy.md" docs/features/README.md` | output > 0 |
| Infra doc exists | `test -f docs/infra/llm-task-routing.md` | exit code 0 |
| Follow-up issues exist | `gh issue view 3420 --json state -q .state && gh issue view 3421 --json state -q .state && gh issue view 3422 --json state -q .state` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Lane split as three issues.** This plan ships lane A under #3410 and files #3420 (local backend evaluation) and #3421 (decisions transport and shadow comparison) with this document as their design source, so #3410's Jev-specific acceptance criteria are met by #3421. Alternative: keep #3410 open across all three PRs with `Refs #3410`, which needs a merge-gate override per PR. Preference: the split.
2. **Flip-policy bars.** `high` 95% / `medium` 90% / `low` 85% agreement, cost at or below one tenth for Haiku sites, p95 at or below the incumbent for granite sites, candidate error rate at or below 2%. These are the issue's numbers reshaped by Tom's error-cost comment. Adjust before #3421 runs its comparison, not after.
3. **Read-the-room migrates in lane A.** It is the last raw Anthropic client in the bridge and a thinking task by this taxonomy. Migrating it is the cleanest way to make the enumeration test allowlist-free in `bridge/`. If the hot-path risk is judged too high for one PR, it moves to its own commit series behind a review gate, still in lane A.
4. **`is_open_source` as the §7 oracle for the hot path.** The router trusts its cache and fails closed on a miss. Should the `valor` project (this repo) be pinned eligible in code rather than depend on a `gh` visibility read at startup?
