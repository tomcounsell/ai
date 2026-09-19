---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-18
tracking: https://github.com/tomcounsell/ai/issues/3410
last_comment_id: 5737918683
---

# LLM Task Taxonomy and Routing Layer

## Problem

Outside the `claude -p` harness, this repo makes about thirty smaller LLM calls. Some think (extract memories, draft a briefing, judge a test). Many only decide (does this message need a reply, which routing bucket, is this an interjection, bind to which job). Both kinds are written the same way at the call site: a hand-picked model constant and a hand-picked transport. Nothing in code says "this call returns one of four labels" versus "this call writes a paragraph".

That absence has a cost we have already paid twice. #1923/#1925 moved the bridge classifiers off Ollama onto Haiku by editing every call site. #2494 moved two of them back onto local granite by editing those call sites again. Each move was a per-site rewrite because there is no routing point; the model choice lives in seventeen places. Tom's framing on 2026-09-18: classification with a model is fundamentally different from standard LLM work, so divide the AI tasks into classification and thinking and route each appropriately. His follow-up the same day set the order: taxonomy and routing point first, local CPU backends first, an external structured-decision provider (TypeSafe Jev 1.13) only behind a local fallback and only once the market settles. His answers to this plan's open questions on 2026-09-19 (issue comment 5737918683) set the delivery shape, verbatim: "this is a must win. actually remove backward compatibility. the builder will flip and revise it to make it work until the PR is approved. Do not engineer a transition phase." So there are no default-off switches and no shadow phase. The routing layer lands with the classification sites moved onto their backends, the builder iterates until quality holds, the 95/90/85 agreement tiers are the reviewer's per-site acceptance bar, and PR approval is the gate.

**Current behavior:**

Verified on `main` at `2cf8da648` (details in Freshness Check):

- Fifteen live fixed-choice call sites, across four transports: `run_typed` (Haiku), `run_typed_local` (granite), a raw Anthropic client with a forced tool or free-text JSON, and a raw `requests` call to OpenRouter chat/completions. Six of them bypass `agent/llm/wrapper.py` entirely, so no single point sees them.
- Roughly fifteen thinking sites share the same constants and transports. Nothing distinguishes the populations.
- Charter §7 (client work and its private context stay on the Claude and Codex subscriptions) is enforced at exactly three places today, all inside the improvement tooling (`tools/improvement_eligibility.py::is_open_source` consumers). Every bridge classifier sees client rooms and Valor-internal rooms alike and has no project key in hand.
- The emoji site the issue originally listed as C16 (row since removed from the issue table; email triage is now C16) is no longer an LLM or embedding call: `find_best_emoji_for_message` is a `random.choice` keyed on the C5 work type. The embedding path (`find_best_emoji`) is dead code with no production caller.
- Jev is reachable only on `POST /api/alpha/decisions` (chat/completions returns HTTP 400 for the slug), so adopting it means a new transport, and the wire shape is now pinned by a verbatim probe (Research).

**Desired outcome:**

1. Every non-harness LLM call site declares its task kind in code. A test enumerates the sites and fails on an undeclared one.
2. One routing point in `agent/llm/` resolves backend and transport from the task's declaration and per-call context eligibility (charter §7: `valor` pinned eligible in code, client projects fail closed). Thinking sites keep their current backend and behavior; unit tests that fake the wrapper pass unchanged.
3. Every classification site lands on a local backend (granite via Ollama) in lane A, with the subscription backend as its runtime fallback and as the backend for client-keyed context. Each landing carries a paired comparison record against the site's prior backend, attached to improvement case `1ec40086ca1d422e90ef747775ff7f64`; the reviewer applies the error-cost tier bar per site, and a site that misses the bar after the builder's iteration lands on Haiku with the record saying why. Later backends (GLiClass in #3420, the Jev decisions transport in #3421) plug in behind the same leg protocol and land the same way, each external one behind a local fallback.

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

**Notes:** Two corrections carried into the plan: the emoji site (originally C16; the issue table was renumbered on 2026-09-18 and email triage is now C16) leaves the classification list (no model on `main`; the dead embedding path is deleted under this plan and a decision-backed emoji choice is filed separately), and read-the-room's action set is `send/trim/suppress`. The issue's live Jev probe is confirmed: the `jev-issue` agent ran it on 2026-09-18 from this repo's venv, and the verbatim request and response are in Research. Revision 1 (2026-09-19) incorporates Tom's answers in comment 5737918683 and the round-1 critique; the hotfix #1055 invariants were re-read on `main` at `001d9040e` (`bridge/promise_gate.py:685-689`, `bridge/read_the_room.py:26-28`, `agent/session_completion.py:444`) and the raw-transport module census was re-run (32 files match the plan's original token list; 30 excluding `tools/image_gen/tests/test_image_gen.py` and the comment-only hit in `agent/memory_extraction.py:43`).

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

2. **Quality prior** (issue comment 5727818556, from TypeSafe's own numbers at https://geotoolbox.ai/blog/what-is-jev-ai): Jev agrees with reference answers 67.8% across four workflows against 73.1% for Claude Opus 5; latency 70 to 500 ms; choice caps at 255 options. *Informs:* a flat 90% agreement bar is above Jev's published quality, so the per-site acceptance bar is error-cost-tiered and the taxonomy plus router is the deliverable even if Jev lands on zero sites in #3421.

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
- **Impact on plan**: #3420's `build-local-zero-shot` task is time-boxed (one build day) with a rejection exit: if the ONNX path cannot produce per-label scores matching the reference pipeline on a 20-item fixture, the lane records GLiClass as rejected and granite stays the local backend. Either outcome keeps "every classification site lands on a local backend" true, because the Ollama leg (granite via PydanticAI, today's `run_typed_local` body) already accepts any output type.

### spike-3: Can `tools/improvement_eval/` host an incumbent-vs-candidate classifier comparison?
- **Assumption**: "The existing paired-arm harness can score agreement and latency."
- **Method**: code-read (`tools/improvement_eval/runner.py`, `arm_worker.py`, `statistics.py`, `tools/improvement_experiment.py::ENVELOPES`)
- **Finding**: No. Its envelopes are `retrieval_parameters` and `agent_task`; its endpoints are ranked-id metrics and agent-task outcomes; a classifier-backend candidate is rejected at envelope validation. `tools/improvement_recursion/arms.py::ArmRunner` is a lighter protocol but scores gains, not agreement. `cross_vendor_judge.py` has no agreement or latency fields.
- **Confidence**: high
- **Impact on plan**: a small standalone runner `tools/classification_eval/` (lane A, because the reviewer's per-site bar needs its numbers) writes `ImprovementEvidence` rows (kind `classifier_comparison`, appended to the closed `EVIDENCE_KINDS` vocabulary in the same commit) and attaches its claims to an investigation on case `1ec40086` via `tools/improvement_investigations.py::record_claims`. `statistics.py::clustered_bootstrap_ci` is reused for the agreement interval.

### spike-4: Is `is_open_source` safe on the message hot path?
- **Assumption**: "Eligibility can be checked per call."
- **Method**: code-read (`tools/improvement_eligibility.py:83`)
- **Finding**: It shells `gh repo view --json visibility` with a 10 s timeout on a cache miss (process-local cache, TTL 900 s, fails closed). A miss on the C1/C2 path would blow a 3 s budget.
- **Confidence**: high
- **Impact on plan**: `valor` is pinned eligible in code ahead of any cache read (Tom, answer 4); for every other project key the router reads the cache only (`peek_open_source`), treats a miss as ineligible for that call, and schedules one background refresh per key; the bridge warms the cache for `ACTIVE_PROJECTS` and the worker for its loaded `projects` at startup, since the cache is process-local.

## Data Flow

Inbound Telegram message through the routing classifiers, after this plan (C1 shown; C2, C3 are the same frame):

1. **Entry point**: `bridge/telegram_bridge.py` NewMessage handler resolves `project = find_project_for_dm(...) or find_project_for_chat(chat_title)` (:1477) and calls `should_respond_async(..., project, ...)` (:1770).
2. **`bridge/routing.py::should_respond_async`** (:1266) passes `project_key=project["_key"]` down to `classify_needs_response(text, project_key=...)`.
3. **Call site**: `await run_typed(prompt, NeedsResponseDecision, task=NEEDS_RESPONSE, project_key=project_key)` where `NEEDS_RESPONSE = LLMTask(site="routing.needs_response", kind=CLASSIFICATION, backend=OLLAMA, error_cost=HIGH)` is a module constant. `backend` names the backend the site lands on in this PR; it is the only per-site backend choice in the repo and changing it is a one-word reviewable diff.
4. **`agent/llm/wrapper.py::run_typed`** calls `agent/llm/router.py::resolve(task, project_key)`, a pure function with four rules in order:
   - `kind == THINKING` or `task.client_only` → `Route(ANTHROPIC, model)` (`model` is the call's `model=` kwarg, default `MODEL_FAST`). Thinking sites never leave the subscription backend under this plan.
   - `task.backend == ANTHROPIC` → `Route(ANTHROPIC, model)`.
   - `task.backend == OLLAMA` and `is_eligible(project_key)` → `Route(OLLAMA, OLLAMA_CLASSIFIER_MODEL, fallback=Route(ANTHROPIC, model))`. `is_eligible` returns `True` for `project_key == "valor"` before any cache read; for any other key it is `peek_open_source(project_key)` (cache-only, miss → `False` plus one scheduled refresh); `None` → `False`.
   - `task.backend == OLLAMA` and not eligible → `Route(ANTHROPIC, model)` (charter §7: client context stays on the subscription; the fail-closed rule covers cache misses).
   #3421 adds a fifth rule ahead of the third for `backend == DECISIONS` (`Route(DECISIONS, JEV, fallback=Route(OLLAMA, ...))`, always with a local fallback); #3420 adds `LOCAL_ZERO_SHOT` the same way. There are no per-site settings switches and no shadow routes.
5. **Backend leg** (`agent/llm/backends/{anthropic,ollama}.py`): each returns a validated instance of the caller's output type or raises `LLMCallError(reason=...)`. The Anthropic leg is today's `run_typed` body (`semaphore_slot(timeout=slot_timeout)` then `AsyncAnthropic(timeout=sdk_timeout, max_retries=...)`, PydanticAI `Agent(system_prompt=system)`); the Ollama leg is today's `run_typed_local` body with its `asyncio.wait_for` replaced by an SDK-level timer (`OllamaProvider(openai_client=AsyncOpenAI(base_url=..., timeout=sdk_timeout, max_retries=0))`) so both legs honor the hotfix #1055 invariant the same way.
6. **Fallback**: on `LLMCallError` from the primary leg with a `fallback` on the route, the wrapper runs the fallback leg once with `sdk_timeout` set to the remainder of the primary's deadline (`deadline = monotonic() + sdk_timeout` taken before the primary call; the fallback is skipped when under 0.5 s remains) and logs one warning naming the site and both backends. The result carries no marker; the caller cannot tell which leg answered, and does not need to.
7. **Output**: the caller receives the same output type it receives today and applies its own fail-safe on `LLMCallError`, exactly as before. The wrapper never invents a default.

Thinking sites: step 4 short-circuits at the first rule, so their data flow is unchanged. C10 and C11 run in the worker process, where `project_key` comes from `AgentSession.project_key`; the worker warms the eligibility cache for its loaded projects at startup (Race 2, Risk 3).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1923 / #1925 (PR #2045) | Moved seven classifiers onto Haiku through a typed wrapper | The wrapper standardized the *transport*, not the *decision*. It carried no task kind and no context, so a later change of backend still meant touching every site. It also left the hottest sites (promise gate, read-the-room, completion judge, health judge, C5) raw because they needed a system prompt, a forced tool, or a 3 s budget the wrapper did not expose. |
| #2494 Task 13 | Moved C12 and C13 onto granite via a second wrapper leg | Chose the backend per site by editing the call to a different function. No measurement, no switch, no fallback. The plan itself records "zero recorded latency measurements". |
| #1636 | Split local Ollama into a classifier model and a generation model | Encoded the classification/thinking split as two *constants*, invisible at the call site and unenforced. |

**Root cause pattern:** the backend choice has always been a property of the call site. None of the fixes gave the repo a place to say "this is a decision" and let one resolver pick the backend. This plan makes the kind a declared property of the call and puts the choice in one function.

## Architectural Impact

- **New dependencies**: none. `openai` (the Ollama leg's client) already ships with `pydantic-ai-slim`'s Ollama provider. #3420 adds an optional extra `classification-local = ["onnxruntime>=1.25.0", "tokenizers>=0.20"]`; #3421 adds nothing (`httpx` is already a dependency). The coupled anthropic + pydantic-ai-slim pin set is untouched.
- **Interface changes**: `run_typed` gains keyword-only `task: LLMTask` (required), `project_key: str | None = None`, `system: str | None = None`, `slot_timeout: float | None = None`, `max_retries: int | None = None`; `hard_timeout` keeps its default for thinking sites and must be `None` at the three 3 s sites (Technical Approach). `run_typed_local` is deleted; its body becomes `agent/llm/backends/ollama.py`. `LLMCallError` gains `reason: Literal["timeout", "slot_timeout", "transport", "validation"]` so tests and the fallback logic can distinguish them. Six classifier functions and `should_respond_async`'s callees gain a `project_key` keyword with a `None` default. No fields exist for #3421's question builder (`question`, `noul_threshold`, `thresholds`, `decision_options`); frozen-dataclass fields with defaults and keyword-only kwargs are additive, so that lane adds them without touching a lane-A call site.
- **Coupling**: decreases. Every non-harness call site depends on `agent.llm` only; `bridge/`, `agent/session_completion.py`, `agent/health_check.py`, `tools/classifier.py`, `reflections/memory/memory_quality_audit.py`, `reflections/improvement_collect.py` stop importing `anthropic`, `ollama`, or `requests` for LLM calls. `agent/llm/router.py` gains one late import of `tools.improvement_eligibility` (mirrors `serves_charter.py`).
- **Data ownership**: one new `ImprovementEvidence` kind, `classifier_comparison`, appended to the closed `EVIDENCE_KINDS` tuple (`models/improvement_evidence.py:85`) with `tests/unit/test_improvement_models.py::VOCABULARY_MAXIMUMS[(ImprovementEvidence, "kind")]` raised from 10 to 11 and the reasoned comment the module header demands, in the same commit as the runner that writes it. No field change and no Redis migration: `kind` is an `IndexedField` on a string value, and `record_once` would otherwise coerce the unknown kind to `"other"` with a warning. Comparison records belong to improvement case `1ec40086ca1d422e90ef747775ff7f64`.
- **Reversibility**: a site's backend is one word in its `LLMTask`; moving a site back is a one-line diff with the comparison record as the argument. There are no switches to unset and no shadow state to drain.

## Appetite

**Size:** Large, split into three ordered lanes (Tom's ordering, issue comment 5727973163; split confirmed in comment 5737918683). Lane A ships under this issue and closes it; #3420 and #3421 are planned against the interfaces lane A lands. Lane A now includes the comparison runner, because the reviewer's per-site bar needs its numbers in the PR.

**Team:** Solo dev (Valor), plan critic, PR reviewer.

**Interactions:**
- PM check-ins: 1-2 (the per-site landings and their comparison records)
- Review rounds: 2+ (lane A touches seventeen sites; the hot-path migrations and the per-site landings get a dedicated review pass against the acceptance bar)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `OPENROUTER_API_KEY` in vault `.env` | `.venv/bin/python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('OPENROUTER_API_KEY')"` | C15's gemma reference arm in the comparison runner |
| `ANTHROPIC_API_KEY` or `CLAUDE_API_KEY` | `.venv/bin/python -c "from agent.anthropic_client import get_anthropic_api_key; assert get_anthropic_api_key()"` | Haiku reference arm in the comparison runner; subscription backend and fallback leg |
| Ollama serving granite | `.venv/bin/python -c "import ollama; assert any('granite4.1:3b' in m.model for m in ollama.list().models)"` | Local backend for every classification site |
| Redis reachable | `.venv/bin/python -c "from config.settings import settings; import redis; redis.from_url(settings.redis.url).ping()"` | Evidence rows, meter, eligibility |
| `gh` authenticated | `gh auth status` | `is_open_source` visibility reads |

Run via `python scripts/check_prerequisites.py docs/plans/llm-task-taxonomy-routing-layer.md`.

## Solution

### Key Elements

- **Taxonomy** (`agent/llm/tasks.py`): `TaskKind` (`classification`, `thinking`), `Backend` (`anthropic`, `ollama`; #3420 and #3421 append their own members), `ErrorCost` (`low`, `medium`, `high`), and a frozen `LLMTask(site, kind, backend, error_cost=MEDIUM, client_only=False)`. Each call site declares one as a module constant next to its output type. The site id is a stable dotted name (`routing.needs_response`, `promise_gate.verdict`); it keys the comparison records and the doc table. `backend` is the backend the site lands on; there is no separate "incumbent" concept once the PR merges.
- **Routing point** (`agent/llm/router.py::resolve`): pure function of `(task, project_key)` returning `Route(backend, model, fallback)` by the four rules in Data Flow step 4. It is the only place that consults eligibility. It reads model names from `config.models` and nothing from per-site settings, because there are none.
- **Backend legs** (`agent/llm/backends/`): the existing Anthropic and Ollama bodies moved out of `wrapper.py`, each with an SDK-level timer and no coroutine-level timeout on its hot path; `decisions.py` (#3421) and `local_zero_shot.py` (#3420) join later. One protocol: `async def call(prompt, output_type, route, *, system, sdk_timeout, slot_timeout, max_retries) -> BaseModel`. `hard_timeout` is applied by the wrapper, outside the leg, and only when the caller passes one.
- **Wrapper** (`agent/llm/wrapper.py::run_typed`): validates the prompt, resolves the route, runs the primary leg, runs the fallback leg once on `LLMCallError` within the remaining deadline, and returns the validated output type. `LLMCallError` semantics for the caller are unchanged.
- **Eligibility** (`tools/improvement_eligibility.py::is_eligible`, `peek_open_source`, `warm_cache`): `valor` pinned `True` in code ahead of the cache; cache-only read for every other key with a fail-closed miss and one background refresh per key; `bridge/telegram_bridge.py` warms `ACTIVE_PROJECTS` and `worker/__main__.py::_run_worker` warms its `projects` at startup. The blocking `is_open_source` keeps its `gh` shell-out for the improvement tooling and the refresh.
- **Enumeration test** (`tests/unit/test_llm_task_taxonomy.py`): AST walk over `agent/ bridge/ worker/ tools/ reflections/ scripts/` (skipping `tests/` directories and `test_*.py`) that (1) requires `task=` on every `run_typed(` call, (2) asserts `run_typed_local` no longer exists anywhere, (3) requires a module-level `LLMTask(` declaration in any module whose AST contains an LLM call token, where the tokens are exactly `messages.create(`, `chat.completions.create(`, `ollama.chat(`, and a reference to `OPENROUTER_URL`, outside the allowlist (`agent/llm/backends/`, `agent/anthropic_client.py`, `agent/session_runner/harness/`, `tools/ollama_client.py`, and `tools/image_gen/__init__.py` with the reason "image generation: no text decision or thinking output"), (4) asserts site ids are unique and every `classification` task is reached through `run_typed`, and (5) asserts the taxonomy table in `docs/features/llm-task-taxonomy.md` lists every declared site id (doc/code parity, same shape as `tests/unit/test_sdlc_skill_md_parity.py`). Embeddings (`embeddings.create(`), transcription (`audio.transcriptions`), and image generation are not LLM task sites under this taxonomy: none of them takes a prompt and returns a decision or a thought, so `tools/transcribe/`, `tools/link_analysis/`, and the embedding call in `tools/impact_finder_core.py:200` need no declaration (its `messages.create(` at `:395` does).
- **Settings**: no new keys. The Ollama leg's SDK timeout defaults to the existing `settings.timeouts.local_typed_hard_s` (its meaning narrows from "outer wall-clock cap" to "the leg's single SDK-level timer"; catalog entry updated). `config/models.py` is unchanged in lane A; `JEV`, `OPENROUTER_DECISIONS_URL`, and `MODEL_INFO[JEV]` belong to #3421.
- **Comparison runner** (`tools/classification_eval/`): `compare(site, inputs, arms) -> ComparisonRecord` with per-arm agreement against the reference arm, p50/p95 latency at concurrency 1 and 4, cost per call, error rate, price with retrieval date, and `n`; writes `ImprovementEvidence(kind="classifier_comparison")` and records claims on an investigation of case `1ec40086`. The reference arm is the site's backend on `main` before this PR (Haiku for C1 through C11, gemma via OpenRouter for C15) with the site's prompt verbatim; the candidate arm is granite through the new Ollama leg with whatever prompt the builder settles on. Inputs: the parametrized examples in each site's existing unit tests plus a sample of real inbound messages for project `valor` from the subconscious memory store (`memory_search`, project `valor`), at least 50 per site and at least 200 for C1 through C4. C12, C13, and C14 stay on granite, so the runner records their latency only (the measurements #2494 lacks).
- **Acceptance bar** (documented in `docs/features/llm-task-taxonomy.md`; applied by the PR reviewer per site, reading the comparison record linked from the PR): a site lands on granite when all hold: agreement with the reference arm at or above the tier bar (`high` 95%, `medium` 90%, `low` 85%), p95 latency at concurrency 4 within the site's budget (3.0 s for C8, C9, C10; otherwise at or below the reference arm's p95 plus one second, since the local leg buys independence and cost rather than speed), candidate error rate at or below 2%, and the comparison `n` at or above the minimum above. A site that misses the bar after the builder's iteration lands with `backend=ANTHROPIC` and its record attached, and the PR says so per site. Tiers: `high` = C1, C2, C3, C4, C12 (a wrong answer drops or misroutes a human's message or binds to the wrong job); `medium` = C5, C7, C8, C10, C11, C13, C14; `low` = C6, C9 (regex floor beneath it), C15 (pre-screen only). C16 (email triage) is `client_only`, lands on `ANTHROPIC`, and is never a candidate. The same bar governs #3420 and #3421.

### Flow

Inbound message → bridge resolves project → classifier declares its task → `run_typed` asks the router → router picks the declared backend for eligible context, or the subscription backend for client context, with the subscription backend as fallback when the local leg fails → leg returns the typed answer → caller applies its own fail-safe → message routed.

### Technical Approach

- **Kind at the call site, as an argument.** The issue left open whether kind is an argument, a marker on the output model, or a separate entry point. Argument wins: an output model can serve two sites with different error costs (`IntentDecision` and `IntentDecisionWithRecall` share a site; `RoutingDecision` could serve a thinking use), and a second entry point would recreate the per-site function choice this plan removes. The `LLMTask` constant is declared next to the output model so the two read together.
- **The hotfix #1055 invariant moves into the legs; the three 3 s sites never see a coroutine-level timeout.** `run_typed` today wraps `agent.run(prompt)` in `asyncio.wait_for(hard_timeout)` (`agent/llm/wrapper.py:215-216`), the exact pattern `bridge/promise_gate.py:685-689`, `bridge/read_the_room.py:26-28`, and `agent/session_completion.py:444` forbid because it leaks httpx connections under cancellation. So C9, C10, and read-the-room call `run_typed(..., sdk_timeout=RTR_SDK_TIMEOUT, slot_timeout=RTR_SDK_TIMEOUT, max_retries=0, hard_timeout=None)`: the only timer around the API call is the SDK-level `AsyncAnthropic(timeout=...)`, and queue wait is bounded separately by `semaphore_slot(timeout=slot_timeout)` (`agent/anthropic_client.py:211`), which the Anthropic leg enters before constructing the client; a slot timeout raises `LLMCallError(reason="slot_timeout")` without ever entering the client context (tested with a cancellation test). The `hard_timeout` kwarg stays for thinking sites that use it today and is applied by the wrapper outside the leg; the module docstrings of the three hot-path modules keep their invariant text and point at the leg. The Ollama leg gets the same treatment: its `asyncio.wait_for` becomes `AsyncOpenAI(timeout=sdk_timeout, max_retries=0)` on the provider's client, so a granite-backed C8, C9, or C10 has one SDK-level timer and no coroutine cancellation around a live request.
- **Read-the-room migrates in lane A as one thinking task** (design call under Tom's answer 3). The action and the rewrite come from one call under a 3 s budget; splitting them doubles the budget on every outbound message. It migrates onto `run_typed(task=READ_THE_ROOM, system=READ_THE_ROOM_SYSTEM_PROMPT, sdk_timeout=RTR_SDK_TIMEOUT, slot_timeout=RTR_SDK_TIMEOUT, max_retries=0, hard_timeout=None)` with `RoomVerdict` as a `BaseModel` (`action: Literal["send", "trim", "suppress"]`), keeping every short-circuit and the `rtr_error` fail-safe. Migrating it is what makes the enumeration test allowlist-free in `bridge/`. It is a thinking task, so it stays on Anthropic and needs no comparison.
- **The six raw classification sites migrate with their fail-safes byte-identical, then land on granite.** C5's dict result becomes `WorkTypeDecision(type: Literal[bug, feature, chore, sdlc], confidence, reason)` and `classify_request_async` returns the same dict shape it returns today; the dead sync `classify_request` is deleted. C9 keeps `max_retries=0` and the 3 s slot timeout via the new kwargs. C10 and C11 keep their `True`/`False` defaults. C14 becomes `MemoryAuditDecision(is_junk: bool, anomaly_signal: str | None, why: str)` on `run_typed(task=MEMORY_AUDIT)` with `backend=OLLAMA` (already granite today). C15 becomes `PromiseJudgeDecision(answer: bool, span: str, confidence: float)` with `backend=OLLAMA`: it is an offline, low-tier pre-screen, so local-first applies to it too; `_OpenRouterJudge`, its `requests` call, and its meter reserve/settle under purpose `promise_detector` are deleted with it, and the comparison runner meters its own gemma reference arm under the existing purpose. The Ollama leg therefore needs no `base_url`/`api_key` override.
- **Landing on granite is the builder's iteration loop, not a phase.** For each Haiku-backed classification site, the builder runs the comparison (reference arm: the prompt verbatim on Haiku; candidate: granite through the leg), reads the record, and iterates on the candidate side only (prompt shaping for a 3B model, a tighter output schema, a `system` string) until the site clears its tier bar or the iteration budget (half a build day per site) is spent. It then sets `backend=OLLAMA` or `backend=ANTHROPIC`, commits the site with the record id in the commit message, and moves on. Lower tiers land first (C6, C9, C15, then `medium`, then `high`) so the loop is calibrated on cheap mistakes.
- **C7's `risk: str` becomes `Literal["suspected", "none"]`.** Same comparison, tighter schema; a 3B model does better with a closed set and #3421's question builder needs the values.
- **Thinking sites are tagged, not migrated.** Sites on `run_typed` add `task=`; sites on raw transports for a reason (vision, audio, streaming, the harness) add a module-level `LLMTask(kind=THINKING)` declaration. The enumeration test enforces both. The full declaration list is Task 4; it was reconciled against a token census of `main` (30 modules) so the test cannot fail on an untouched file.
- **Project key threading.** Sites that can know their project pass it; sites that cannot leave it `None` and resolve to the subscription backend by the fail-closed rule. `should_respond_async` and `classify_work_request` gain `project_key`; the promise gate and read-the-room resolve it from `chat_id` through `find_project_for_chat`; session-side sites (C10, C11) read `AgentSession.project_key`; C14 reads the memory row's project; C15 already has it. Client rooms therefore run C12 and C13 on Haiku after this PR, where today they run on granite for every room; that is §7 applied consistently and is called out in the PR.
- **Confidence thresholds stay per site.** `JOB_ROUTER_CONFIDENCE_THRESHOLD`, `INTENT_CONFIDENCE_THRESHOLD`, and `TEAMMATE_CONFIDENCE_THRESHOLD` are site constants tuned to the site's backend. C12 and C13 keep granite and their thresholds unchanged. If C4 lands on granite, the builder re-tunes `TEAMMATE_CONFIDENCE_THRESHOLD` on the comparison record (self-reported confidence is model-specific) and the record shows the chosen value. Per-backend threshold maps belong to #3421, where a probability-calibrated backend first appears.
- **Deleted from lane A by Tom's answer 2**: the five `MODELS__*` CSV switches, `TIMEOUTS__DECISIONS_S`, shadow routes and shadow dispatch, the "no site switched on by default" anti-criterion, and the notion of an incumbent distinct from the declared backend. The decisions leg's question builder, its metering under purpose `structured_decision`, and the Jev live-listing probe test are #3421's, written against Research finding 1.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every migrated site keeps its `except Exception` or `except LLMCallError` block; for each, a test asserts the fail-safe value AND the log line (`logger.warning` with the site id) when the leg raises `LLMCallError`: C5 `{}`-equivalent dict, C9 `None` → heuristic, C10 `False`, C11 `healthy=True`, C14 `None`, C15 `promises-judge-failed`, read-the-room `send`/`rtr_error`.
- [ ] Router fallback: a test makes the Ollama leg raise `LLMCallError(reason="transport")` and asserts the Anthropic leg ran exactly once with `sdk_timeout` equal to the remaining deadline and a warning names the site and both backends; a second test spends the deadline in the primary and asserts the fallback is skipped and the primary's `LLMCallError` propagates.
- [ ] Anthropic leg slot timeout: with the semaphore held, a call with `slot_timeout=0.05` raises `LLMCallError(reason="slot_timeout")` and the `AsyncAnthropic` constructor was never entered (fake client records construction); no `asyncio.wait_for` appears around the API call in either leg (asserted by the enumeration test's token walk over `agent/llm/backends/`).
- [ ] Ollama leg: connection refused, HTTP 5xx, schema-validation exhaustion, and SDK timeout each raise `LLMCallError` with a distinct `reason`; four unit tests against a fake `AsyncOpenAI`.
- [ ] `is_eligible("valor")` is `True` with the cache empty and `gh` unavailable (monkeypatched to raise); `peek_open_source` never raises; a test asserts a cache miss returns `None` and schedules exactly one refresh per key.

### Empty/Invalid Input Handling
- [ ] `run_typed` keeps its `ValueError` on empty or whitespace prompt before any routing; test unchanged.
- [ ] `run_typed` without `task=` raises `TypeError` naming the kwarg (pinned so a missed site fails loudly, Risk 2).
- [ ] `resolve` with `project_key=None` on an `OLLAMA` classification task returns the Anthropic route (fail closed, tested).
- [ ] The comparison runner with fewer inputs than the site's minimum refuses to write a record and prints the shortfall (tested).

### Error State Rendering
- [ ] `python -m tools.doctor` gains an "LLM routing" section listing every declared site, its kind, its declared backend, the route `resolve` returns for `valor` and for a client key, and the per-process eligibility cache state; a test renders it with one cold key.
- [ ] The comparison runner's report renders the miss case (agreement under the tier bar, or candidate error rate above 2%) with the failing criterion named, not a blank table; tested with a recorded failing arm.

## Test Impact

- [ ] `tests/unit/test_job_router.py` (fakes `bridge.job_router.run_typed_local`): UPDATE: patch `bridge.job_router.run_typed`; the fake keeps `**kwargs`.
- [ ] `tests/unit/test_intake_classifier.py` (fakes `run_typed_local`): UPDATE: patch `run_typed` in `tools.classifier`.
- [ ] `tests/unit/test_llm_wrapper_local.py`: REPLACE with `tests/unit/test_llm_backend_ollama.py` (the leg's four failure reasons, SDK-level timeout, no `wait_for`); `run_typed_local` is deleted.
- [ ] `tests/unit/test_improvement_models.py::VOCABULARY_MAXIMUMS`: UPDATE `(ImprovementEvidence, "kind")` from 10 to 11 with the reasoned comment.
- [ ] `tests/integration/test_job_routing.py`: UPDATE: same patch target change.
- [ ] `tests/unit/test_promise_gate.py` (monkeypatches `promise_gate.anthropic.AsyncAnthropic` and `get_anthropic_api_key`; 56 tests): REPLACE the transport-level fakes with a `run_typed` fake returning `PromiseVerdictDecision`; the timeout test asserts `LLMCallError(reason="timeout")` and `LLMCallError(reason="slot_timeout")` each still route to the heuristic with source `timeout`.
- [ ] `tests/unit/test_promise_gate_measurement.py`, `test_promise_gate_audit.py`, `test_promise_gate_session_events.py`: UPDATE where they construct the client fake; behavior assertions unchanged.
- [ ] `tests/unit/test_read_the_room.py` (monkeypatches `rtr_module.anthropic.AsyncAnthropic`; 37 tests): REPLACE the fake constructor with a `run_typed` fake returning `RoomVerdict`; every short-circuit and error-path assertion stays.
- [ ] `tests/unit/test_session_completion.py`, `tests/unit/test_session_completion_zombie.py`: UPDATE the novelty-judge fakes to `run_typed`.
- [ ] `tests/unit/test_health_check.py` (38 tests): UPDATE the `_judge_health` fakes from `anthropic_slot` to `run_typed`; the "unparseable judge response" case becomes an `LLMCallError` case with the same `healthy=True` outcome.
- [ ] `tests/unit/test_pr_classification_fastpath.py`: UPDATE: `classify_request_async` fake returns the same dict; tests for the deleted sync `classify_request` are DELETED.
- [ ] `tests/unit/test_memory_quality.py`: UPDATE the `_gemma_classify` fakes from `ollama.chat` to `run_typed`.
- [ ] `tests/unit/test_improvement_evidence.py` (84 tests): UPDATE the `_OpenRouterJudge` fakes to a `run_typed` fake returning `PromiseJudgeDecision`; the `promise_detector` reserve/settle assertions are DELETED with the OpenRouter call (the judge is local and unmetered after this PR).
- [ ] `tests/unit/test_emoji_embedding.py`: DELETE the `find_best_emoji` / `_compute_embedding` / cache tests (dead path removed); KEEP the `find_best_emoji_for_message` tests.
- [ ] `tests/unit/test_llm_wrapper.py`: UPDATE: calls pass `task=`; the `hard_timeout` test moves to the wrapper level (outside the leg); add router, fallback-deadline, and slot-timeout tests.
- [ ] `tests/unit/test_routing.py`, `test_intent_classifier.py`, `test_agent_catchup.py`, `test_injection_inspection.py`, `test_context_recall.py`, `test_context_recall_wiring.py`, `test_email_cs_triage.py`, `tests/unit/memory_extraction/test_memory_extraction_event_loop_safety.py`: no change expected (they fake `run_typed` itself, which is backend-agnostic; fakes accept `**kwargs`; `call_args[0]` positional assertions still hold). Listed so the builder runs them first as the "wrapper contract unchanged" proof.
- [ ] `tests/unit/test_improvement_eligibility.py`: UPDATE: add `is_eligible` (valor pin, client miss, `None`), `peek_open_source`, `warm_cache`, and the single-refresh race test.

## Rabbit Holes

- **Re-scoring the reference prompts.** The reference arm runs each site's prompt verbatim on the site's `main` backend so the record measures the backend change alone. The builder shapes only the candidate arm's prompt; improving the reference is a different experiment with its own comparison.
- **A general "provider registry" with dynamic plugin discovery.** Two legs in lane A, one enum, one `if` chain in `resolve`. Add a leg when it exists.
- **Making eligibility finer than the project.** §7 is per message's project. Per-message content classification of "private context" is a research question, not a routing rule.
- **Tuning the Ollama server** (`OLLAMA_NUM_PARALLEL`, `OLLAMA_KEEP_ALIVE`, quantization). The infra doc records the two settings the hot path depends on; the comparison runner measures under the server as configured. Server tuning past that is #3420's territory.
- **Jev `score`, `not_for`, `inspect`, JSON-object `state`.** Unexercised by the probe; out of scope.
- **Emoji choice as a structured decision.** No model on `main` today, no ground truth, a new network call on every message. Filed separately (No-Gos).
- **Replacing `is_open_source`'s `gh` shell-out with a projects.json field.** Would change the source of truth for §7 across the improvement tooling. The `valor` pin plus the cache peek is enough here.
- **Rewriting `tools/improvement_eval/` to accept a classifier envelope.** Spike-3 says no; the standalone runner is 200 lines.

## Risks

### Risk 1: Hot-path regressions from migrating promise gate, read-the-room, and the completion judge
**Impact:** Every outbound message crosses at least two of these; a regression drops or delays replies, and a coroutine-level timeout around the API call leaks httpx connections (hotfix #1055).
**Mitigation:** These three migrate last among the raw sites, each in its own commit, each calling `run_typed` with `hard_timeout=None`, `sdk_timeout=RTR_SDK_TIMEOUT`, `slot_timeout=RTR_SDK_TIMEOUT`, `max_retries=0`, each with a slot-starvation test proving the leg raises without entering the client, and each behind the byte-identical fail-safe. The enumeration test's token walk asserts no `asyncio.wait_for` in the three modules or in `agent/llm/backends/`. The PR review checks the p95 of `read_the_room` on the local bridge before and after via `tests/unit/test_read_the_room.py`'s timing fixture.

### Risk 2: Required `task=` breaks an unlisted caller
**Impact:** A `TypeError` at call time in a path the enumeration test did not cover.
**Mitigation:** The enumeration test runs the same AST walk the builder uses to find sites; `python -m ruff check` plus the full `tests/unit/` run are gates; `run_typed` raises a clear `TypeError` naming the missing kwarg.

### Risk 3: Eligibility fails closed in a process that never warmed its cache
**Impact:** A client-keyed classification call in a cold process resolves to the subscription backend when the project is in fact open source. (`valor` is pinned, so this repo's own rooms are never affected.)
**Mitigation:** The bridge warms `ACTIVE_PROJECTS` and the worker warms its loaded `projects` at startup; a miss schedules one refresh so the second call in a burst sees the answer; `tools/doctor` prints the per-process cache state; the comparison runner is offline and uses the blocking `is_open_source`. The failure direction is the safe one under §7.

### Risk 4: Granite misses the bar on the high-tier sites after iteration
**Impact:** C1 through C4 land on Haiku and the "local-first" outcome is partial in lane A.
**Mitigation:** The taxonomy, the router, the legs, the runner, and the records ship regardless; each miss is a recorded result with the failing criterion named, which is exactly the evidence #3420 (GLiClass) needs to target the right sites. Lower tiers land first so the iteration loop is calibrated before the expensive sites.

### Risk 5: Ollama serialization or cold start breaks the 3 s budget under a burst
**Impact:** Ollama runs one request at a time by default and unloads granite after five idle minutes; a burst of messages, or the first message after a quiet spell, times out the local leg on C8, C9, or C10 and the fallback has too little deadline left, so the caller sees its fail-safe where Haiku answered today.
**Mitigation:** The comparison runner measures p95 at concurrency 4 and the bar requires it within budget; the infra doc pins `OLLAMA_KEEP_ALIVE=-1` and `OLLAMA_NUM_PARALLEL=4` for the bridge machine and `tools/doctor` reports the loaded model and its keep-alive; a budgeted site whose p95 misses lands on `ANTHROPIC` by the bar.

### Risk 6: Test fakes drift from the leg protocol
**Impact:** Green tests that reach no leg.
**Mitigation:** One shared fake in `tests/helpers/llm_fakes.py` implementing the leg protocol, used by every migrated test; a mutation check per migrated site during review (each fail-safe test must fail when the fail-safe line is deleted).

### Risk 7: The comparison inputs are too few or too clean
**Impact:** A site clears 95% on its unit-test examples and misbehaves on real traffic.
**Mitigation:** The minimum `n` (50, and 200 for C1 through C4) must include real inbound `valor` messages from the memory store, not only test fixtures; the record carries the input source split; a site whose real-message sample cannot reach the minimum lands on `ANTHROPIC`.

## Race Conditions

### Race 1: Fallback leg queues on the Anthropic semaphore past the caller's budget
**Location:** `agent/llm/wrapper.py::run_typed` fallback dispatch; `agent/llm/backends/anthropic.py`
**Trigger:** The Ollama leg fails fast (connection refused) on a budgeted site while the Anthropic semaphore is saturated; the fallback waits for a slot.
**Data prerequisite:** The deadline is captured before the primary call.
**State prerequisite:** The fallback's `slot_timeout` is the remaining deadline, never the caller's original value.
**Mitigation:** The wrapper passes `min(slot_timeout, remaining)` and `sdk_timeout=remaining` to the fallback and skips it under 0.5 s; a slot timeout raises before the client is constructed; a test saturates the semaphore and asserts the fallback raises `LLMCallError(reason="slot_timeout")` inside the budget.

### Race 2: Eligibility refresh storms on a cold cache
**Location:** `tools/improvement_eligibility.py::peek_open_source`
**Trigger:** Burst of messages for one project before the first refresh lands.
**Data prerequisite:** none
**State prerequisite:** At most one in-flight refresh per project key.
**Mitigation:** A per-key `_REFRESHING: set[str]` guarded by the existing module lock; refresh runs in `run_in_executor`; a test fires 50 concurrent peeks and asserts one `gh` call.

### Race 3: Comparison runner arms interleave with live bridge traffic on one Ollama server
**Location:** `tools/classification_eval/`
**Trigger:** The builder runs a concurrency-4 latency measurement while the local bridge is serving messages through the same Ollama daemon.
**Data prerequisite:** none
**State prerequisite:** Latency numbers in a record must come from a run with no other Ollama clients, or the record must say otherwise.
**Mitigation:** The runner checks whether the bridge and worker services are loaded (`launchctl list` for the `com.valor.*` labels) before a latency run and stamps `contended: true` on the record when either is; the reviewer's bar reads only uncontended latency, and the builder stops the services (`./scripts/valor-service.sh stop`) for the measurement. Tested with a fake `launchctl` output.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3420] Lane B: the GLiClass ONNX candidate (`local_zero_shot` leg, optional extra, lazy weights), compared against granite with lane A's runner on the sites granite missed first, landing per site by the same bar. Depends on lane A's `LLMTask`, router, leg protocol, and runner. Its design is fixed in this document; its own plan is written against the merged interfaces.
- [SEPARATE-SLUG #3421] Lane C: the decisions transport (`decisions` leg, question builder from Research finding 1, metering under purpose `structured_decision`, live-listing probe test), compared with lane A's runner and landing per site by the same bar, always with the Ollama leg as its fallback and never on a site whose local backend missed the bar (no single external provider on a hot path). Per Tom's answer 2 there is no shadow phase: the comparison is the builder's iteration evidence, and PR approval is the gate. Depends on lane A; can run in parallel with #3420.
- [SEPARATE-SLUG #3422] Emoji reaction choice as a structured-decision site (72-way `choice` over `EMOJI_LABELS`). Today's path makes no model call; adding one is a new capability with no ground truth, and Tom's ordering says local first.
- [EXTERNAL] Any change to the RSI charter's §7 boundary or to the $10/day unit-2 budget. Tom-owned.
- [EXTERNAL] Choosing a second structured-decision provider when one appears. The leg protocol admits it; the choice is a research investigation on the case.

Anti-criteria for the code-level No-Gos are in Verification: no emoji embedding call path remains, no per-site backend switch exists in settings, and no raw Anthropic, Ollama, or OpenRouter client remains in the migrated modules.

## Update System

- `pyproject.toml`: lane A adds no dependency. #3420 adds the optional extra `classification-local`; `/update`'s `uv sync` does not install extras by default, so the local zero-shot leg raises `LLMCallError("classification-local extra not installed")` and the router falls back on machines without it. `tools/doctor` reports the extra's presence.
- Ollama on every bridge machine must serve `granite4.1:3b` (already required by C12 and C13 on `main`) and should run with `OLLAMA_KEEP_ALIVE=-1` and `OLLAMA_NUM_PARALLEL=4` (Risk 5). These are launchd environment settings on the Ollama service, outside this repo; `docs/infra/llm-task-routing.md` records them and `tools/doctor` reports the loaded model and keep-alive so a machine without them is visible after `/update`. No update-script change.
- Config propagation: none. No new env keys in lane A; `TIMEOUTS__LOCAL_TYPED_HARD_S` keeps its name with narrowed semantics.
- Migrations: none. No Popoto field changes; the `EVIDENCE_KINDS` vocabulary widens by one string value on an existing `IndexedField`.
- Services: after lane A merges, `./scripts/valor-service.sh restart` (the bridge and worker import the wrapper); `/update` already does this.

## Agent Integration

- No new `[project.scripts]` entry for the agent. The comparison runner is a developer tool invoked as `python -m tools.classification_eval`; results reach the agent through `valor-improve case show` and `valor-improve investigation list --case 1ec40086ca1d422e90ef747775ff7f64`, which already exist.
- The bridge and the worker call the new code directly: `bridge/telegram_bridge.py` startup calls `tools.improvement_eligibility.warm_cache(ACTIVE_PROJECTS)` and `worker/__main__.py::_run_worker` calls `warm_cache(list(projects))` before its loops start; every classifier in `bridge/` and the two session-side judges reach the router through `agent.llm.run_typed`.
- Integration tests: `tests/unit/test_llm_task_taxonomy.py` (enumeration and parity); `tests/unit/test_llm_router_eligibility.py` feeds a message mapped to a client project through every `OLLAMA`-backed classification site and asserts the Anthropic leg was called, feeds a `valor` message through the same sites with `gh` unavailable and asserts the Ollama leg was called, and asserts `email_cs.triage` and `tools/email_cs/agents.py` resolve to the Anthropic leg for every project key; `tests/integration/test_bridge_routing_project_key.py` asserts `should_respond_async` passes `project["_key"]` through to the three routing classifiers; `tests/unit/test_worker_startup_warm_cache.py` asserts `_run_worker` warms the cache for its loaded projects.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/llm-task-taxonomy.md`: the two kinds, the `LLMTask` declaration, the full site table (id, kind, landed backend, error-cost tier, §7 class, comparison record id) that the parity test reads, the router rules in order, eligibility with the `valor` pin and the fail-closed rule, the acceptance bar tiers and criteria as the reviewer applies them, and where comparison records live.
- [ ] Update `docs/features/nonharness-llm-wrapper.md`: the `run_typed` signature, the leg protocol, the hotfix #1055 invariant as the legs now carry it (`slot_timeout`, SDK timers, `hard_timeout` outside the leg), the "Migrated Call Sites" table extended to every site (read-the-room no longer "skipped"; C5, C9, C10, C11, C14, C15 added), `run_typed_local` removed, and a pointer to the taxonomy page.
- [ ] Update `docs/features/local-model-policy.md`: granite is the declared backend for the classification sites that cleared the bar and the fallback rule; the classifier/generation constant split now lives in the taxonomy.
- [ ] Update `docs/features/config-timeout-catalog.md`: `TIMEOUTS__LOCAL_TYPED_HARD_S` is the Ollama leg's SDK-level timer.
- [ ] Add a row to `docs/features/README.md` for the taxonomy page.
- [ ] Create `docs/infra/llm-task-routing.md`: Ollama service requirements for the hot path (`granite4.1:3b` pulled, `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_NUM_PARALLEL=4`, how to set them on launchd), the comparison runner's reference-arm spend (Haiku and gemma, metered purposes), and the rollback (set a site's `backend` back and restart). #3421 adds the decisions endpoint section.

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

Round 1 (2026-09-19, FULL depth, independent roster of 3 critics). Verdict: NEEDS REVISION (3 blockers, 3 concerns, 1 nit).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Scope & Value, History & Consistency, Risk & Robustness, structural | The plan predates Tom's 2026-09-19 answers (issue comment 5737918683; frontmatter `last_comment_id: 5730470971` is stale). Tom: "actually remove backward compatibility ... Do not engineer a transition phase ... no default-off switches, no shadow phase as a prerequisite step. The routing layer lands with the classification sites moved onto it, the builder iterates until quality holds, and PR approval is the gate"; the 95/90/85 tiers are the reviewer's per-site acceptance bar; pin `valor` §7-eligible in code. The plan still ships five empty-by-default CSV switches, "Shadow mode is the only Jev mode until the market settles", `Route(fallback, shadow)` shadow dispatch, the "no site switched on by default" anti-criterion, Risk 5 and Race 1 (both about shadow spend), Data Flow step 4 routing `valor` through the `gh`-backed fail-closed cache, and Open Question 4 still open. Local-first and no-single-external-provider-on-hot-paths (comment 5727973163) still govern which backend each site lands on. | pending | Revise: each site's `LLMTask.incumbent` names the backend it lands on in lane A (local-first per Tom's ordering; Haiku where no local backend is acceptable at review), with no `structured_decision_sites` / `structured_decision_shadow_sites` / `classification_local_sites` CSV switches and no shadow dispatch in `run_typed`; delete the Verification row `grep -c 'structured_decision_sites: str = ""\|...' config/settings.py` and replace it with a check that every migrated site's declared incumbent matches the backend it landed on; rewrite Risk 5 / Race 1 (or drop them) and reframe the Flip Policy as the PR reviewer's per-site acceptance bar; add `if project_key == "valor": return True` ahead of the cache read in `peek_open_source` / `is_open_source` (client projects keep the fail-closed `gh` check); bump `last_comment_id` to 5737918683 and close Open Questions 1, 2, 4 with Tom's answers. Where shadow-style side-by-side measurement survives, it is an engineering technique the builder may use to iterate, not a settings-gated phase. |
| BLOCKER | Risk & Robustness | Tasks 4 and 5 migrate C9 (promise gate), C10 (completion judge), and read-the-room onto `run_typed` with `hard_timeout=RTR_SDK_TIMEOUT`, but `run_typed` implements `hard_timeout` as `asyncio.wait_for(agent.run(prompt), timeout=hard_timeout)` around the live API call (`agent/llm/wrapper.py:215-216`), which is exactly the pattern each of those modules forbids under the hotfix #1055 invariant ("Coroutine-level timeouts around the API call are forbidden, they leak httpx connections under cancellation": `bridge/promise_gate.py:685-689`, `bridge/read_the_room.py:26-28`, `agent/session_completion.py:444`). The plan's "hard_timeout must bound semaphore acquisition as well as the SDK call" makes the hazard worse, not better. | pending | For C9, C10, and read-the-room call `run_typed(..., sdk_timeout=RTR_SDK_TIMEOUT, hard_timeout=None)` so the only timer is the SDK-level `AsyncAnthropic(timeout=...)`; bound queue wait separately with `semaphore_slot(timeout=RTR_SDK_TIMEOUT)` (`agent/anthropic_client.py:211`) by giving the Anthropic leg a `slot_timeout` kwarg rather than widening `hard_timeout`; add a cancellation test proving the leg raises `LLMCallError` on slot timeout without entering the client context, and keep the #1055 docstring invariants in the three modules pointing at the leg. Alternatively (design call, per Tom's answer 3) leave read-the-room on its raw client behind an allowlist entry; either way the plan must state which. |
| BLOCKER | History & Consistency, structural | Enumeration-test check 3 requires every module carrying a raw-transport token to declare a module-level `LLMTask` or sit in the four-module allowlist, but Task 3's declaration list omits seven modules that match the plan's own token list on `main`: `scripts/evaluate_build.py`, `tools/cross_vendor_judge.py`, `tools/image_gen/__init__.py`, `tools/impact_finder_core.py` (`embeddings.create(`), `tools/knowledge/converter.py`, `tools/link_analysis/__init__.py`, `tools/transcribe/__init__.py` (`audio.transcriptions`). Task 6's test fails on untouched files mid-build, and the "Every site declares a task" success criterion is unmeetable as written. | pending | Extend Task 3's declaration list with the seven modules (each tagged `kind=THINKING`, or `client_only=True` where applicable), or add them to the check-3 allowlist with a one-line reason each; decide explicitly whether embeddings (`embeddings.create(`), transcription (`audio.transcriptions`), and image generation count as LLM call sites for the taxonomy, and if not, drop those tokens from the check-3 list so the test and the doc table agree. Verify with `/usr/bin/grep -rln "messages.create(\|ollama.chat(\|chat.completions.create(\|audio.transcriptions\|embeddings.create(\|OPENROUTER_URL" --include='*.py' agent bridge worker tools reflections scripts` (30 modules on `main`) before Task 6. |
| CONCERN | Scope & Value | `LLMTask` carries `question`, `noul_threshold`, `thresholds: dict[Backend, float]`, and `run_typed` gains `decision_options`, all of which exist only to serve the lane-C decisions leg's question builder and per-backend thresholds; no lane A task constructs anything that reads them. | pending | Ship lane A with `LLMTask(site, kind, incumbent, error_cost, client_only)` and `run_typed(..., task, project_key, system, max_retries)` only; frozen-dataclass fields with defaults and keyword-only optional kwargs are additive, so #3421 adds `noul_threshold`, `thresholds`, `question`, and `decision_options` without touching any lane-A call site. Keep the leg protocol's `decision_options` slot out of lane A's `backends/__init__.py` signature as well. |
| CONCERN | History & Consistency, structural | Architectural Impact and Update System say the new `ImprovementEvidence` kinds `classifier_comparison` / `classifier_shadow` are "string kinds on an existing model; no Popoto schema change", but `EVIDENCE_KINDS` (`models/improvement_evidence.py:85`) is a closed 10-value tuple, `record_once` (`:254`) coerces an unknown kind to `"other"` with only a warning, and `tests/unit/test_improvement_models.py::VOCABULARY_MAXIMUMS[(ImprovementEvidence, "kind")] = 10` caps the count. Rows written under the new kinds would silently land as `"other"`. | pending | Wherever the comparison design survives (lane B/C plans written from this document), add an explicit step: append both kinds to `EVIDENCE_KINDS` and raise `VOCABULARY_MAXIMUMS[(ImprovementEvidence, "kind")]` to 12 with the reasoned comment the module header demands, in the same commit as the first writer; reword "no schema change" to "no field change; the closed kind vocabulary widens by two". No Redis migration is needed because the index is an `IndexedField` on a string value. |
| CONCERN | Risk & Robustness | `peek_open_source` reads a process-local cache (`tools/improvement_eligibility.py`: module-level dict, TTL 900 s, no Redis), and the only warm-up the plan names is `bridge/telegram_bridge.py` startup; C10 and C11 run in the worker process, which has no described warm-up, so every worker restart fails those sites closed to the incumbent until a scheduled refresh lands. | pending | The `valor` pin from the first blocker closes most of this (`if project_key == "valor": return True` ahead of the cache read); for client-keyed sessions add a `warm_cache(...)` call at `worker` startup mirroring the bridge one, and have `tools/doctor`'s routing section print per-process cache state so a cold worker is visible. |
| NIT | structural | Tasks 7, 8, and 9 (validate-lane-a, document-feature, validate-all) carry no `Validates` line; Task 8 in particular has no command even though the Verification table already holds the three doc greps. | pending | |

---

## Open Questions

1. **Lane split as three issues.** This plan ships lane A under #3410 and files #3420 (local backend evaluation) and #3421 (decisions transport and shadow comparison) with this document as their design source, so #3410's Jev-specific acceptance criteria are met by #3421. Alternative: keep #3410 open across all three PRs with `Refs #3410`, which needs a merge-gate override per PR. Preference: the split.
2. **Flip-policy bars.** `high` 95% / `medium` 90% / `low` 85% agreement, cost at or below one tenth for Haiku sites, p95 at or below the incumbent for granite sites, candidate error rate at or below 2%. These are the issue's numbers reshaped by Tom's error-cost comment. Adjust before #3421 runs its comparison, not after.
3. **Read-the-room migrates in lane A.** It is the last raw Anthropic client in the bridge and a thinking task by this taxonomy. Migrating it is the cleanest way to make the enumeration test allowlist-free in `bridge/`. If the hot-path risk is judged too high for one PR, it moves to its own commit series behind a review gate, still in lane A.
4. **`is_open_source` as the §7 oracle for the hot path.** The router trusts its cache and fails closed on a miss. Should the `valor` project (this repo) be pinned eligible in code rather than depend on a `gh` visibility read at startup?
