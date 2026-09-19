---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-18
tracking: https://github.com/tomcounsell/ai/issues/3410
last_comment_id: 5738173689
revision_applied: true
revision_applied_at: 2026-09-19T03:09:10Z
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

**Notes:** Two corrections carried into the plan: the emoji site (originally C16; the issue table was renumbered on 2026-09-18 and email triage is now C16) leaves the classification list (no model on `main`; the dead embedding path is deleted under this plan and a decision-backed emoji choice is filed separately), and read-the-room's action set is `send/trim/suppress`. The issue's live Jev probe is confirmed: the `jev-issue` agent ran it on 2026-09-18 from this repo's venv, and the verbatim request and response are in Research. Revision 1 (2026-09-19) incorporates Tom's answers in comment 5737918683 and the round-1 critique; the hotfix #1055 invariants were re-read on `main` at `001d9040e` (`bridge/promise_gate.py:685-689`, `bridge/read_the_room.py:26-28`, `agent/session_completion.py:444`) and the raw-transport module census was re-run. Revision 2 (2026-09-19) re-ran the census on `main` at `d780d4d99`: the plan's grep (Task 4) returns exactly 30 files, and all 30 are accounted for: 18 declared in Task 4, 7 migrated in Tasks 5 and 6, 3 allowlisted, `agent/memory_extraction.py` (a `run_typed` site; its `:43` hit is a comment), and `tools/image_gen/tests/test_image_gen.py` (a test file the walk skips). Revision 2 also verified the `OllamaProvider` injection point on the pinned stack and rewrote the bodies of #3420 and #3421 to the comparison-runner-promotes-on-record design.

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
5. **Backend leg** (`agent/llm/backends/{anthropic,ollama}.py`): each returns a validated instance of the caller's output type or raises `LLMCallError(reason=...)`. The Anthropic leg is today's `run_typed` body (`semaphore_slot(timeout=slot_timeout)` then `AsyncAnthropic(timeout=sdk_timeout, max_retries=...)`, PydanticAI `Agent(system_prompt=system)`); the Ollama leg is today's `run_typed_local` body with its `asyncio.wait_for` replaced by an SDK-level timer (`OllamaProvider(openai_client=AsyncOpenAI(base_url=..., timeout=sdk_timeout, max_retries=0))`) so both legs honor the hotfix #1055 invariant the same way. The SDK timer is per leg: the wrapper's `sdk_timeout` defaults to `None`, and after `resolve()` the effective value is the caller's explicit number when given, else `agent/llm/backends/__init__.py::default_sdk_timeout(route.backend)`, which reads `settings.timeouts.anthropic_sdk_s` (30 s) for `ANTHROPIC` and `settings.timeouts.local_typed_hard_s` (20 s) for `OLLAMA`. The wrapper carries no timeout constant of its own; `DEFAULT_SDK_TIMEOUT` is deleted.
6. **Fallback**: on `LLMCallError` from the primary leg with a `fallback` on the route, the wrapper runs the fallback leg once with `sdk_timeout` set to the remainder of the primary's deadline (`deadline = monotonic() + effective_sdk_timeout` taken before the primary call; the fallback is skipped when under 0.5 s remains) and logs one warning naming the site and both backends. The result carries no marker; the caller cannot tell which leg answered, and does not need to.
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
- **Interface changes**: `run_typed` gains keyword-only `task: LLMTask` (required), `project_key: str | None = None`, `system: str | None = None`, `slot_timeout: float | None = None`, `max_retries: int | None = None`; `sdk_timeout` becomes `float | None = None` (resolved per backend leg from `TimeoutSettings` after routing; an explicit caller value always wins); `hard_timeout` keeps its default for thinking sites and must be `None` at the three 3 s sites (Technical Approach). `run_typed_local` is deleted; its body becomes `agent/llm/backends/ollama.py`. `LLMCallError` gains `reason: Literal["timeout", "slot_timeout", "transport", "validation"]` so tests and the fallback logic can distinguish them. Six classifier functions and `should_respond_async`'s callees gain a `project_key` keyword with a `None` default. No fields exist for #3421's question builder (`question`, `noul_threshold`, `thresholds`, `decision_options`); frozen-dataclass fields with defaults and keyword-only kwargs are additive, so that lane adds them without touching a lane-A call site.
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
- **Eligibility** (`tools/improvement_eligibility.py::is_eligible`, `peek_open_source`, `_schedule_refresh`, `warm_cache`): `valor` pinned `True` in code ahead of the cache; cache-only read for every other key with a fail-closed miss and one background refresh per key, scheduled only when a loop is running (a caller with no loop, such as `tools/doctor`, gets the miss and no refresh); `bridge/telegram_bridge.py` warms `ACTIVE_PROJECTS` and `worker/__main__.py::_run_worker` warms its `projects` at startup. The blocking `is_open_source` keeps its `gh` shell-out for the improvement tooling and the refresh.
- **Enumeration test** (`tests/unit/test_llm_task_taxonomy.py`): AST walk over `agent/ bridge/ worker/ tools/ reflections/ scripts/` (skipping `tests/` directories and `test_*.py`) that (1) requires `task=` on every `run_typed(` call, (2) asserts `run_typed_local` no longer exists anywhere, (3) requires a module-level `LLMTask(` declaration in any module whose AST contains an LLM call token, where the tokens are exactly `messages.create(`, `chat.completions.create(`, `ollama.chat(`, and a reference to `OPENROUTER_URL`, outside the allowlist (`agent/llm/backends/`, `agent/anthropic_client.py`, `agent/session_runner/harness/`, `tools/ollama_client.py`, and `tools/image_gen/__init__.py` with the reason "image generation: no text decision or thinking output"), (4) asserts site ids are unique and every `classification` task is reached through `run_typed`, (5) asserts the taxonomy table in `docs/features/llm-task-taxonomy.md` lists every declared site id (doc/code parity, same shape as `tests/unit/test_sdlc_skill_md_parity.py`), and (6) asserts the hotfix #1055 invariant on the LLM call path by function body, not by file: no `asyncio.wait_for` call node inside `bridge/promise_gate.py::_evaluate_promise_async`, `bridge/read_the_room.py::read_the_room`, `agent/session_completion.py::_judge_completion_novelty`, `reflections/memory/memory_quality_audit.py::_gemma_classify`, or any function in `agent/llm/backends/` (`ast.walk` over the named `FunctionDef`/`AsyncFunctionDef` bodies looking for `Call` nodes whose func is the attribute `wait_for` on the name `asyncio`). The `asyncio.wait_for` around the Telegram send callback at `agent/session_completion.py:1229` and the two around `proc.communicate()` in `memory_quality_audit.py` (`:617`, `:696`) are legitimate bounds on non-LLM awaits and stay outside the assertion. Check (1) is AST-based too (a `Call` whose func resolves to `run_typed` must carry a `task` keyword), so a call whose kwargs sit on the next line (`agent/memory_extraction.py:371`, `bridge/context_recall.py:269`, `bridge/injection_inspection.py:170` today) passes on its content. Embeddings (`embeddings.create(`), transcription (`audio.transcriptions`), and image generation are not LLM task sites under this taxonomy: none of them takes a prompt and returns a decision or a thought, so `tools/transcribe/`, `tools/link_analysis/`, and the embedding call in `tools/impact_finder_core.py:200` need no declaration (its `messages.create(` at `:395` does).
- **Settings**: no new keys. The SDK timer is chosen per backend leg from `config/settings.py::TimeoutSettings`: `default_sdk_timeout(Backend.ANTHROPIC)` is `settings.timeouts.anthropic_sdk_s` (30 s) and `default_sdk_timeout(Backend.OLLAMA)` is `settings.timeouts.local_typed_hard_s` (20 s; its meaning narrows from "outer wall-clock cap" to "the Ollama leg's single SDK-level timer"; catalog entry updated, and the `anthropic_sdk_s` description's reference to the wrapper's `DEFAULT_SDK_TIMEOUT` is rewritten to name the Anthropic leg). The wrapper's `sdk_timeout` parameter defaults to `None` and the wrapper owns no timeout constant, so a site that passes nothing gets its leg's timer and a site that passes a number (C8's 3.0, the three `RTR_SDK_TIMEOUT` sites, C14's `GEMMA_CALL_TIMEOUT_SEC`) gets that number on whichever leg the router picks. `config/models.py` is unchanged in lane A; `JEV`, `OPENROUTER_DECISIONS_URL`, and `MODEL_INFO[JEV]` belong to #3421.
- **Comparison runner** (`tools/classification_eval/`): `compare(site, inputs, arms) -> ComparisonRecord` with per-arm agreement against the reference arm, p50/p95 latency at concurrency 1 and 4, cost per call, error rate, price with retrieval date, and `n`; writes `ImprovementEvidence(kind="classifier_comparison")` and records claims on an investigation of case `1ec40086`. The reference arm is the site's backend on `main` before this PR (Haiku for C1 through C11, gemma via OpenRouter for C15) with the site's prompt verbatim; the candidate arm is granite through the new Ollama leg with whatever prompt the builder settles on. Inputs: the parametrized examples in each site's existing unit tests plus a sample of real inbound messages for project `valor` from the subconscious memory store (`memory_search`, project `valor`), at least 50 per site and at least 200 for C1 through C4. C12, C13, and C14 stay on granite, so the runner records their latency only (the measurements #2494 lacks).
- **Acceptance bar** (documented in `docs/features/llm-task-taxonomy.md`; applied by the PR reviewer per site, reading the comparison record linked from the PR): a site lands on granite when all hold: agreement with the reference arm at or above the tier bar (`high` 95%, `medium` 90%, `low` 85%), p95 latency at concurrency 4 within the site's budget (3.0 s for C8, C9, C10; otherwise at or below the reference arm's p95 plus one second, since the local leg buys independence and cost rather than speed), candidate error rate at or below 2%, and the comparison `n` at or above the minimum above. A site that misses the bar after the builder's iteration lands with `backend=ANTHROPIC` and its record attached, and the PR says so per site. Tiers: `high` = C1, C2, C3, C4, C12 (a wrong answer drops or misroutes a human's message or binds to the wrong job); `medium` = C5, C7, C8, C10, C11, C13, C14; `low` = C6, C9 (regex floor beneath it), C15 (pre-screen only). C16 (email triage) is `client_only`, lands on `ANTHROPIC`, and is never a candidate. The same bar governs #3420 and #3421.

### Flow

Inbound message → bridge resolves project → classifier declares its task → `run_typed` asks the router → router picks the declared backend for eligible context, or the subscription backend for client context, with the subscription backend as fallback when the local leg fails → leg returns the typed answer → caller applies its own fail-safe → message routed.

### Technical Approach

- **Kind at the call site, as an argument.** The issue left open whether kind is an argument, a marker on the output model, or a separate entry point. Argument wins: an output model can serve two sites with different error costs (`IntentDecision` and `IntentDecisionWithRecall` share a site; `RoutingDecision` could serve a thinking use), and a second entry point would recreate the per-site function choice this plan removes. The `LLMTask` constant is declared next to the output model so the two read together.
- **The hotfix #1055 invariant moves into the legs; the three 3 s sites never see a coroutine-level timeout.** `run_typed` today wraps `agent.run(prompt)` in `asyncio.wait_for(hard_timeout)` (`agent/llm/wrapper.py:215-216`), the exact pattern `bridge/promise_gate.py:685-689`, `bridge/read_the_room.py:26-28`, and `agent/session_completion.py:444` forbid because it leaks httpx connections under cancellation. So C9, C10, and read-the-room call `run_typed(..., sdk_timeout=RTR_SDK_TIMEOUT, slot_timeout=RTR_SDK_TIMEOUT, max_retries=0, hard_timeout=None)`: the only timer around the API call is the SDK-level `AsyncAnthropic(timeout=...)`, and queue wait is bounded separately by `semaphore_slot(timeout=slot_timeout)` (`agent/anthropic_client.py:211`), which the Anthropic leg enters before constructing the client; a slot timeout raises `LLMCallError(reason="slot_timeout")` without ever entering the client context (tested with a cancellation test). The `hard_timeout` kwarg stays for thinking sites that use it today and is applied by the wrapper outside the leg; the module docstrings of the three hot-path modules keep their invariant text and point at the leg. The Ollama leg gets the same treatment: its `asyncio.wait_for` becomes `AsyncOpenAI(timeout=sdk_timeout, max_retries=0)` on the provider's client, so a granite-backed C8, C9, or C10 has one SDK-level timer and no coroutine cancellation around a live request.
- **Read-the-room migrates in lane A as one thinking task** (design call under Tom's answer 3). The action and the rewrite come from one call under a 3 s budget; splitting them doubles the budget on every outbound message. It migrates onto `run_typed(task=READ_THE_ROOM, system=READ_THE_ROOM_SYSTEM_PROMPT, sdk_timeout=RTR_SDK_TIMEOUT, slot_timeout=RTR_SDK_TIMEOUT, max_retries=0, hard_timeout=None)` with `RoomVerdict` as a `BaseModel` (`action: Literal["send", "trim", "suppress"]`), keeping every short-circuit and the `rtr_error` fail-safe. Migrating it is what makes the enumeration test allowlist-free in `bridge/`. It is a thinking task, so it stays on Anthropic and needs no comparison.
- **The six raw classification sites migrate with their fail-safes byte-identical, then land on granite.** C5's dict result becomes `WorkTypeDecision(type: Literal[bug, feature, chore, sdlc], confidence, reason)` and `classify_request_async` returns the same dict shape it returns today; the dead sync `classify_request` is deleted. C9 keeps `max_retries=0` and the 3 s slot timeout via the new kwargs. C10 and C11 keep their `True`/`False` defaults. C14 becomes `MemoryAuditDecision(is_junk: bool, anomaly_signal: str | None, why: str)` on `run_typed(task=MEMORY_AUDIT)` with `backend=OLLAMA` (already granite today); `_gemma_classify` becomes `async def _gemma_classify(content: str) -> MemoryAuditDecision | None`, awaited directly from `_layer3_classify` with `sdk_timeout=GEMMA_CALL_TIMEOUT_SEC` and `hard_timeout=None`, which deletes the raw `ollama.chat(...)` call at `reflections/memory/memory_quality_audit.py:442`, the `import ollama`, the per-invocation `ThreadPoolExecutor`, the `loop.run_in_executor` dispatch, and the `asyncio.wait_for` at `:513`; the layer-3 wallclock deadline check is unchanged and the subprocess `asyncio.wait_for` calls at `:617` and `:696` stay, since they bound `proc.communicate()` and never an LLM call. C15 is the one migration that changes backend as well as transport. Its backend on `main` is gemma via OpenRouter, and lane A's router has exactly two legs (`ANTHROPIC`, `OLLAMA`), so no lane-A leg can carry that transport: C15 becomes `PromiseJudgeDecision(answer: bool, span: str, confidence: float)` on `run_typed(task=PROMISE_JUDGE)` with `backend=ANTHROPIC` (Haiku, the subscription backend every `OLLAMA` site falls back to anyway), and `_OpenRouterJudge`, its `requests` call, and its meter reserve/settle under purpose `promise_detector` are deleted in that same commit. Gemma via OpenRouter survives only as C15's reference arm inside the comparison runner, metered under the existing `promise_detector` purpose. Task 7 then promotes C15 to `backend=OLLAMA` on a passing comparison record, exactly like the Haiku-backed sites. The Ollama leg therefore needs no `base_url`/`api_key` override.
- **Landing on granite is the builder's iteration loop, not a phase.** For each classification site that leaves Task 5 on `backend=ANTHROPIC`, the builder runs the comparison (reference arm: the prompt verbatim on the site's `main` backend, Haiku for every site except C15, whose reference arm is gemma via OpenRouter; candidate: granite through the leg), reads the record, and iterates on the candidate side only (prompt shaping for a 3B model, a tighter output schema, a `system` string) until the site clears its tier bar or the iteration budget (half a build day per site) is spent. It then sets `backend=OLLAMA` or `backend=ANTHROPIC`, commits the site with the record id in the commit message, and moves on. Lower tiers land first (C6, C9, C15, then `medium`, then `high`) so the loop is calibrated on cheap mistakes.
- **C7's `risk: str` becomes `Literal["suspected", "none"]`.** Same comparison, tighter schema; a 3B model does better with a closed set and #3421's question builder needs the values.
- **Thinking sites are tagged, not migrated.** Sites on `run_typed` add `task=`; sites on raw transports for a reason (vision, audio, streaming, the harness) add a module-level `LLMTask(kind=THINKING)` declaration. The enumeration test enforces both. The full declaration list is Task 4; it was reconciled against a token census of `main` (30 modules) so the test cannot fail on an untouched file.
- **Project key threading.** Sites that can know their project pass it; sites that cannot leave it `None` and resolve to the subscription backend by the fail-closed rule. `should_respond_async` and `classify_work_request` gain `project_key`; the promise gate and read-the-room resolve it from `chat_id` through `find_project_for_chat`; session-side sites (C10, C11) read `AgentSession.project_key`; C14 reads the memory row's project; C15 already has it. Client rooms therefore run C12 and C13 on Haiku after this PR, where today they run on granite for every room; that is §7 applied consistently and is called out in the PR.
- **Confidence thresholds stay per site.** `JOB_ROUTER_CONFIDENCE_THRESHOLD`, `INTENT_CONFIDENCE_THRESHOLD`, and `TEAMMATE_CONFIDENCE_THRESHOLD` are site constants tuned to the site's backend. C12 and C13 keep granite and their thresholds unchanged. If C4 lands on granite, the builder re-tunes `TEAMMATE_CONFIDENCE_THRESHOLD` on the comparison record (self-reported confidence is model-specific) and the record shows the chosen value. Per-backend threshold maps belong to #3421, where a probability-calibrated backend first appears.
- **Deleted from lane A by Tom's answer 2**: the five `MODELS__*` CSV switches, `TIMEOUTS__DECISIONS_S`, shadow routes and shadow dispatch, the "no site switched on by default" anti-criterion, and the notion of an incumbent distinct from the declared backend. The decisions leg's question builder, its metering under purpose `structured_decision`, and the Jev live-listing probe test are #3421's, written against Research finding 1.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every migrated site keeps its `except Exception` or `except LLMCallError` block; for each, a test asserts the fail-safe value AND the log line (`logger.warning` with the site id) when the leg raises `LLMCallError`: C5 `{}`-equivalent dict, C9 `None` → heuristic, C10 `False`, C11 `healthy=True`, C14 `None`, C15 `promises-judge-failed`, read-the-room `send`/`rtr_error`.
- [ ] Router fallback: a test makes the Ollama leg raise `LLMCallError(reason="transport")` and asserts the Anthropic leg ran exactly once with `sdk_timeout` equal to the remaining deadline and a warning names the site and both backends; a second test spends the deadline in the primary and asserts the fallback is skipped and the primary's `LLMCallError` propagates.
- [ ] Anthropic leg slot timeout: with the semaphore held, a call with `slot_timeout=0.05` raises `LLMCallError(reason="slot_timeout")` and the `AsyncAnthropic` constructor was never entered (fake client records construction); no `asyncio.wait_for` appears around the API call in either leg (asserted by the enumeration test's check 6, an AST walk over every function body in `agent/llm/backends/`).
- [ ] Ollama leg: connection refused, HTTP 5xx, schema-validation exhaustion, and SDK timeout each raise `LLMCallError` with a distinct `reason`; four unit tests against a fake `AsyncOpenAI`.
- [ ] `is_eligible("valor")` is `True` with the cache empty and `gh` unavailable (monkeypatched to raise); `peek_open_source` never raises; a test asserts a cache miss returns `None` and, under a running loop, `is_eligible` schedules exactly one refresh per key; a second test asserts `is_eligible` with no running loop returns `False` without scheduling or raising.
- [ ] Per-backend SDK timer: the table-driven test in Task 2 (Ollama fake receives `local_typed_hard_s`, Anthropic fake receives `anthropic_sdk_s`, an explicit value wins on both).

### Empty/Invalid Input Handling
- [ ] `run_typed` keeps its `ValueError` on empty or whitespace prompt before any routing; test unchanged.
- [ ] `run_typed` without `task=` raises `TypeError` naming the kwarg (pinned so a missed site fails loudly, Risk 2).
- [ ] `resolve` with `project_key=None` on an `OLLAMA` classification task returns the Anthropic route (fail closed, tested).
- [ ] The comparison runner with fewer inputs than the site's minimum refuses to write a record and prints the shortfall (tested).

### Error State Rendering
- [ ] `python -m tools.doctor` gains an "LLM routing" section listing every declared site, its kind, its declared backend, the route `resolve` returns for `valor` and for a client key, and the per-process eligibility cache state; the check is synchronous like every other doctor check, and `tests/unit/test_doctor.py::test_llm_routing_section_cold_client_key_sync` renders it with one cold client key and no event loop, asserting the `miss (no loop; refresh not scheduled)` line.
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
- [ ] `tests/unit/test_memory_quality.py`: UPDATE the `_gemma_classify` fakes from `ollama.chat` to an async `run_typed` fake returning `MemoryAuditDecision`; the executor-timeout case becomes `LLMCallError(reason="timeout")` with the same `None` verdict and the same `unavailable_count` outcome; any assertion on `ThreadPoolExecutor` or `run_in_executor` is DELETED with the dispatch it tested.
- [ ] `tests/unit/test_improvement_evidence.py` (84 tests): UPDATE the `_OpenRouterJudge` fakes to a `run_typed` fake returning `PromiseJudgeDecision`; the `promise_detector` reserve/settle assertions are DELETED with the OpenRouter call (the judge runs on the Anthropic leg after Task 5 and on whichever leg Task 7 lands it, both unmetered; the purpose survives only for the runner's gemma reference arm).
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
**Mitigation:** These three migrate last among the raw sites, each in its own commit, each calling `run_typed` with `hard_timeout=None`, `sdk_timeout=RTR_SDK_TIMEOUT`, `slot_timeout=RTR_SDK_TIMEOUT`, `max_retries=0`, each with a slot-starvation test proving the leg raises without entering the client, and each behind the byte-identical fail-safe. The enumeration test's check 6 asserts no `asyncio.wait_for` inside the three functions (`_evaluate_promise_async`, `read_the_room`, `_judge_completion_novelty`) or in any function in `agent/llm/backends/`. The PR review checks the p95 of `read_the_room` on the local bridge before and after via `tests/unit/test_read_the_room.py`'s timing fixture.

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
**Mitigation:** A per-key `_REFRESHING: set[str]` guarded by a module-level `threading.Lock` added beside `_CACHE` (the module has no lock today); `_schedule_refresh` checks for a running loop before touching the set and returns without scheduling when there is none (the synchronous `tools/doctor` path); the refresh runs in `run_in_executor`; a test fires 50 concurrent `is_eligible` calls under one loop and asserts one `gh` call, and a second test calls `is_eligible` with no loop and asserts `False`, no exception, and an empty `_REFRESHING`.

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
- [ ] Every existing unit test in the "no change expected" list passes unchanged; `resolve()` returns the Anthropic route for every thinking and `client_only` site and for every `OLLAMA` site with a client key or `None`, and the Ollama route with an Anthropic fallback for every `OLLAMA` site with key `valor` (table-driven test over all declarations, `gh` monkeypatched unavailable).
- [ ] C5, C9, C10, C11, C14, C15, and read-the-room go through `run_typed`; `grep` finds no `AsyncAnthropic(`, `anthropic_slot(`, `ollama.chat(`, or `requests.post(` in those seven modules; the enumeration test's check 6 finds no `asyncio.wait_for` inside the LLM call-path function bodies it names; `run_typed_local` no longer exists.
- [ ] Every classification site except C16 declares `backend=OLLAMA` or `backend=ANTHROPIC` with a `classifier_comparison` record on case `1ec40086` whose id appears in the site's row of the taxonomy table; every `OLLAMA` landing clears its tier bar in that record, and every `ANTHROPIC` landing's record names the failing criterion (C15 included: its reference arm is gemma via OpenRouter, and an `ANTHROPIC` C15 carries the record saying why granite missed). C12, C13, C14 carry latency-only records.
- [ ] `email_cs.triage` and `tools/email_cs/agents.py` resolve to the Anthropic leg for every project key.
- [ ] The dead emoji embedding path is gone; `find_best_emoji_for_message` behavior and tests unchanged.
- [ ] `tools/doctor` shows the routing section with per-process eligibility cache state.
- [ ] Tests pass (`/do-test`); documentation updated (`/do-docs`); `python -m ruff check` and `python -m ruff format --check` clean.
- [ ] Issues #3420, #3421, #3422 exist and reference this plan, and the bodies of #3420 and #3421 describe the landed design (lane A's runner via `--site <id> --candidate <backend>`, `classifier_comparison` records, a one-word `backend` landing; the pre-revision tokens `classifier_shadow`, `SHADOW_SITES`, `--arms incumbent`, `STRUCTURED_DECISION_SITES`, and `CLASSIFICATION_LOCAL_SITES` appear in neither body; rewritten by revision 2 on 2026-09-19); #3410 closes with lane A.

## Team Orchestration

### Team Members

- **Builder (taxonomy, router, legs, runner)**
  - Name: taxonomy-builder
  - Role: `agent/llm/tasks.py`, `router.py`, `backends/` split with the #1055 invariant in the legs, `run_typed` signature, eligibility pin and peek, `tools/classification_eval/`, enumeration test
  - Agent Type: builder
  - Domain: async/concurrency (Race 1, Race 2)
  - Resume: true

- **Builder (site migration and landing)**
  - Name: sites-builder
  - Role: tag the `run_typed` sites, migrate the six raw classification sites and read-the-room, thread `project_key`, delete the dead emoji path, run the per-site comparison loop and set each site's `backend`
  - Agent Type: builder
  - Resume: true

- **Validator (behavior parity and the bar)**
  - Name: parity-validator
  - Role: run the "no change expected" test list first, then the full suite; mutation-check each fail-safe and the enumeration test; re-read every comparison record against the acceptance bar
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
- **Validates**: `tests/unit/test_llm_wrapper.py`, `tests/unit/test_llm_backend_anthropic.py` (create), `tests/unit/test_llm_backend_ollama.py` (create, replaces `test_llm_wrapper_local.py`), `tests/unit/test_llm_import_safety.py`, `tests/unit/test_llm_stack_degraded_start.py`, `tests/unit/test_llm_tasks.py` (create)
- **Informed By**: spike-1 (only `choice`/`noul` needed later), spike-4 (eligibility peek)
- **Assigned To**: taxonomy-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/llm/tasks.py` with `TaskKind`, `Backend` (`ANTHROPIC`, `OLLAMA`), `ErrorCost`, `LLMTask` (frozen dataclass: `site`, `kind`, `backend`, `error_cost=MEDIUM`, `client_only=False`). Nothing for #3421's question builder.
- Move the Anthropic body of `run_typed` to `agent/llm/backends/anthropic.py::call` and the Ollama body of `run_typed_local` to `agent/llm/backends/ollama.py::call`, each implementing `call(prompt, output_type, route, *, system, sdk_timeout, slot_timeout, max_retries)`; delete `run_typed_local`.
- Anthropic leg: `semaphore_slot(timeout=slot_timeout)` entered before the client is constructed, `TimeoutError` there → `LLMCallError(reason="slot_timeout")`; `AsyncAnthropic(timeout=sdk_timeout, max_retries=max_retries)`; PydanticAI `Agent(system_prompt=system)`. No `asyncio.wait_for` inside the leg; the wrapper applies `hard_timeout` outside it when the caller passes one.
- Ollama leg: `OllamaProvider(openai_client=AsyncOpenAI(base_url=..., api_key="ollama", timeout=sdk_timeout, max_retries=0))`; the `asyncio.wait_for` is removed. Injection point verified on the pinned stack (2026-09-19, `pydantic-ai-slim==2.45.0`, `openai==3.8.0`): `OllamaProvider.__init__(self, base_url: str | None = None, api_key: str | None = None, openai_client: AsyncOpenAI | None = None, http_client=None)`; the builder does not re-check it. `LLMCallError.reason` in `{timeout, slot_timeout, transport, validation}`.
- `agent/llm/backends/__init__.py::default_sdk_timeout(backend) -> float`: `ANTHROPIC` → `settings.timeouts.anthropic_sdk_s`, `OLLAMA` → `settings.timeouts.local_typed_hard_s`, read at call time so an env bump takes effect without a reload. Both legs take `sdk_timeout` as a required float; the wrapper resolves it (Task 2). `DEFAULT_SDK_TIMEOUT` is deleted from `wrapper.py`; `DEFAULT_HARD_TIMEOUT` stays.
- Slot-starvation and cancellation tests for the Anthropic leg; the four-reason tests for the Ollama leg.

### 2. Router, eligibility, wrapper, doctor
- **Task ID**: build-router
- **Depends On**: build-taxonomy
- **Validates**: `tests/unit/test_llm_router.py` (create), `tests/unit/test_llm_router_eligibility.py` (create), `tests/unit/test_improvement_eligibility.py` (extend), `tests/unit/test_worker_startup_warm_cache.py` (create), `tests/unit/test_llm_wrapper.py` (per-backend `sdk_timeout` table), `tests/unit/test_doctor.py::test_llm_routing_section_cold_client_key_sync` (create)
- **Informed By**: spike-4
- **Assigned To**: taxonomy-builder
- **Agent Type**: builder
- **Parallel**: false
- `agent/llm/router.py::resolve(task, project_key) -> Route(backend, model, fallback=None)` with the four rules in Data Flow step 4.
- `tools/improvement_eligibility.py`: `is_eligible(project_key)` with `if project_key == "valor": return True` ahead of the cache read (the blocking `is_open_source` gets the same pin); `peek_open_source(project_key) -> bool | None` is a pure cache read that never schedules anything; `_schedule_refresh(project_key)` does `try: loop = asyncio.get_running_loop() except RuntimeError: return False` first, then, under a new module-level `threading.Lock` next to `_CACHE`, adds the key to `_REFRESHING` and runs `is_open_source` via `loop.run_in_executor` with a done-callback that discards the key (Race 2 test); `is_eligible` calls `_schedule_refresh` on a miss and returns `False` either way; `warm_cache(keys)` calls the blocking `is_open_source` per key. Bridge warms `ACTIVE_PROJECTS`; `worker/__main__.py::_run_worker` warms `list(projects)`.
- `run_typed(prompt, output_type, *, task, project_key=None, model=MODEL_FAST, system=None, sdk_timeout=None, slot_timeout=None, hard_timeout=DEFAULT_HARD_TIMEOUT, max_retries=None)`: validate, resolve, then `effective = sdk_timeout if sdk_timeout is not None else default_sdk_timeout(route.backend)`, deadline from `effective`, primary leg with `sdk_timeout=effective`, fallback once within the remaining deadline (Race 1 test), `hard_timeout` applied outside the leg only when not `None`. Table-driven test in `tests/unit/test_llm_wrapper.py`: with both legs faked, a C12-shaped call (`backend=OLLAMA`, key `valor`, no `sdk_timeout`) reaches the Ollama fake with `sdk_timeout == 20.0`; a C1-shaped call routed to Anthropic (`backend=ANTHROPIC`, or `OLLAMA` with a client key) reaches the Anthropic fake with `30.0`; a C8-shaped call with `sdk_timeout=3.0` reaches either fake with `3.0`; the settings values are read from `settings.timeouts` in the test, never hard-coded twice.
- `tools/doctor` "LLM routing" section: every declared site with kind, backend, the route for `valor` and for a client key, and the per-process eligibility cache state; plus the Ollama loaded model and keep-alive (Risk 5). Doctor stays synchronous: `resolve` and `is_eligible` are plain functions, and the refresh scheduler behind `peek_open_source` degrades to a no-op without a running loop (below), so the check calls `resolve` directly with no `asyncio.run` and reports a cold client key as `miss (no loop; refresh not scheduled)`. `tests/unit/test_doctor.py::test_llm_routing_section_cold_client_key_sync` invokes the check function synchronously, the way `run_checks` does, with the cache cleared and `gh` monkeypatched unavailable, and asserts that line and no exception.

### 3. Comparison runner and evidence kind
- **Task ID**: build-comparison-runner
- **Depends On**: build-taxonomy
- **Validates**: `tests/unit/test_classification_eval.py` (create), `tests/unit/test_improvement_models.py` (UPDATE `VOCABULARY_MAXIMUMS`), `tests/unit/test_improvement_evidence.py`
- **Informed By**: spike-3
- **Assigned To**: taxonomy-builder
- **Agent Type**: builder
- **Parallel**: true (with build-router)
- `tools/classification_eval/`: `compare(site, inputs, arms) -> ComparisonRecord` (agreement with `clustered_bootstrap_ci`, p50/p95 at concurrency 1 and 4, cost, error rate, `n` with input-source split, `contended` flag from Race 3); input loaders for a site's unit-test examples and for `valor` messages from the memory store; the reference-arm callers for Haiku (prompt verbatim through the Anthropic leg) and gemma via OpenRouter (metered under the existing purpose, used once for C15); `python -m tools.classification_eval --site <id> --candidate ollama` writes the record and attaches claims to an investigation on case `1ec40086`; the miss report names the failing criterion; `--audit` walks every declared classification site, prints declared backend, record id, and bar result, and exits 1 on an `OLLAMA` landing without a passing record or a site without a record (Verification row).
- Append `"classifier_comparison"` to `EVIDENCE_KINDS` and raise `VOCABULARY_MAXIMUMS[(ImprovementEvidence, "kind")]` to 11 with the reasoned comment, in the same commit.

### 4. Tag the wrapper sites and thread project keys
- **Task ID**: build-tag-sites
- **Depends On**: build-router
- **Validates**: `tests/unit/test_routing.py`, `test_intent_classifier.py`, `test_agent_catchup.py`, `test_injection_inspection.py`, `test_context_recall.py`, `test_context_recall_wiring.py`, `test_email_cs_triage.py`, `test_job_router.py`, `test_intake_classifier.py`, `tests/integration/test_job_routing.py`, `tests/integration/test_bridge_routing_project_key.py` (create), `tests/unit/memory_extraction/*`
- **Assigned To**: sites-builder
- **Agent Type**: builder
- **Parallel**: false
- Declare `LLMTask` constants at C1, C2, C3, C4, C6, C7, C8, C12, C13, C16 (C16 `client_only=True`, `backend=ANTHROPIC`; C12 and C13 `backend=OLLAMA`; the rest `backend=ANTHROPIC` at this step, changed per site in Task 7) and at every thinking site on `run_typed` (`agent/memory_extraction.py`, `tools/memory_eval/query_set.py`, and the rest of the issue's thinking list that already uses the wrapper); C7's `risk` becomes a `Literal`; C12 and C13 call `run_typed`.
- Add `project_key` keywords and pass `project["_key"]` from `should_respond_async`; C6 from the chat's project; C8 from the outbound chat.
- Module-level `LLMTask(kind=THINKING, backend=ANTHROPIC)` declarations in every raw-transport module the census found on `main` that is not migrated in Tasks 5 and 6 and not on the allowlist: `bridge/media.py`, `tools/image_analysis/__init__.py`, `tools/image_tagging/__init__.py`, `tools/test_judge/__init__.py`, `reflections/pm_briefings/builder.py`, `reflections/utilities.py`, `scripts/memory_consolidation.py`, `scripts/evaluate_build.py`, `tools/knowledge/indexer.py`, `tools/knowledge/converter.py`, `tools/valor_calendar.py`, `tools/doc_summary/__init__.py`, `tools/documentation/__init__.py`, `tools/cross_vendor_judge.py`, `tools/impact_finder_core.py` (its `messages.create(` at `:395`), `tools/improvement_eval/arm_worker.py`, `tools/improvement_eval/judges/serves_charter.py`, and `tools/email_cs/agents.py` with `client_only=True`. `tools/image_gen/__init__.py` goes on the allowlist with its reason; `tools/transcribe/__init__.py` and `tools/link_analysis/__init__.py` match no token once `audio.transcriptions` and `embeddings.create(` are out of the list. Re-run the census (`/usr/bin/grep -rln "messages.create(\|ollama.chat(\|chat.completions.create(\|OPENROUTER_URL" --include='*.py' agent bridge worker tools reflections scripts`) at the head this task builds on and reconcile any new module before Task 8.

### 5. Migrate the six raw classification sites
- **Task ID**: build-migrate-raw
- **Depends On**: build-tag-sites
- **Validates**: `tests/unit/test_pr_classification_fastpath.py`, `test_session_completion.py`, `test_session_completion_zombie.py`, `test_health_check.py`, `test_memory_quality.py`, `test_improvement_evidence.py`, `test_promise_gate*.py`, `tests/helpers/llm_fakes.py` (create)
- **Assigned To**: sites-builder
- **Agent Type**: builder
- **Parallel**: false
- Order: C5, C11, C14, C15, C10, then C9 (hot path last). One commit per site. The migration commit changes transport only for C5, C9, C10, and C11 (each stays on Haiku, `backend=ANTHROPIC`) and for C14 (stays on granite, `backend=OLLAMA`). C15 is the exception: its `main` backend is gemma via OpenRouter, which no lane-A leg carries, so its migration commit changes backend too, landing it on `backend=ANTHROPIC` (Haiku) while deleting the OpenRouter transport. Each site keeps its fail-safe value and its log line; each test asserts both under `LLMCallError`.
- C5: `WorkTypeDecision` model; delete sync `classify_request` and the `anthropic` import; `classify_request_async` returns the same dict.
- C14: `async def _gemma_classify(content) -> MemoryAuditDecision | None` awaiting `run_typed(GEMMA_AUDIT_PROMPT.format(content=content[:1000]), MemoryAuditDecision, task=MEMORY_AUDIT, project_key=<the memory row's project>, sdk_timeout=GEMMA_CALL_TIMEOUT_SEC, hard_timeout=None)`, called directly from `_layer3_classify`; delete the `ollama.chat(...)` call, the `import ollama`, the `ThreadPoolExecutor`, the `run_in_executor` dispatch, and the `asyncio.wait_for` at `:513`; `_layer3_classify` reads `verdict.is_junk`, `verdict.anomaly_signal`, `verdict.why` as attributes; the wallclock deadline check and the two subprocess `asyncio.wait_for` calls (`:617`, `:696`) are unchanged. Fail-safe: `None` on `LLMCallError`, so the `unavailable_count` accounting holds.
- C15: `PromiseJudgeDecision` on `run_typed(task=PROMISE_JUDGE)` with `backend=ANTHROPIC`; `_OpenRouterJudge`, the `requests` call, and the `promise_detector` reserve/settle deleted in the same commit. Gemma via OpenRouter remains reachable only from the comparison runner's reference arm (Task 3), metered under `promise_detector`.
- C10 and C9: `run_typed(..., sdk_timeout=RTR_SDK_TIMEOUT, slot_timeout=RTR_SDK_TIMEOUT, max_retries=0, hard_timeout=None)`; C9's slot timeout and SDK timeout each still route to the heuristic with source `timeout`; the #1055 docstring text in both modules now points at the leg.

### 6. Migrate read-the-room and delete the dead emoji path
- **Task ID**: build-migrate-rtr
- **Depends On**: build-migrate-raw
- **Validates**: `tests/unit/test_read_the_room.py`, `tests/unit/test_emoji_embedding.py`, `tests/unit/test_react_with_emoji.py`
- **Assigned To**: sites-builder
- **Agent Type**: builder
- **Parallel**: false
- `RoomVerdict` becomes a `BaseModel` with `action: Literal["send", "trim", "suppress"]`; `read_the_room` calls `run_typed(task=READ_THE_ROOM, system=READ_THE_ROOM_SYSTEM_PROMPT, project_key=..., sdk_timeout=RTR_SDK_TIMEOUT, slot_timeout=RTR_SDK_TIMEOUT, max_retries=0, hard_timeout=None)`; `trim` without `revised_text` still becomes `send`; every error path still yields `send`/`rtr_error`; the module docstring's #1055 paragraph points at the leg.
- Delete `find_best_emoji`, `_compute_embedding`, the embedding cache, `OPENROUTER_EMBEDDINGS_URL`, `EMBEDDING_MODEL`, `REACTION_TOP_K`, `REACTION_TEMPERATURE`, `_softmax_sample`; fix the stale comment at `bridge/telegram_bridge.py:1894`; keep `EMOJI_LABELS` (it is the option list #3422 will need) with a comment saying so.

### 7. Land each classification site on its backend
- **Task ID**: build-land-sites
- **Depends On**: build-migrate-rtr, build-comparison-runner
- **Validates**: `tests/unit/test_llm_router.py` (table-driven route test over all declarations), the site's own test file, and the comparison record per site (`valor-improve investigation list --case 1ec40086ca1d422e90ef747775ff7f64`)
- **Assigned To**: sites-builder
- **Agent Type**: builder
- **Parallel**: false
- Stop the local services for latency runs (Race 3). Every site in this loop enters it on `backend=ANTHROPIC` (Task 4 for the wrapper sites, Task 5 for the raw ones, C15 included). For each site in order C6, C9, C15, C5, C7, C8, C10, C11, C1, C2, C3, C4: run the comparison against the reference arm (Haiku with the prompt verbatim; for C15, gemma via OpenRouter metered under `promise_detector`); iterate on the candidate arm (prompt, `system`, schema) within half a build day; set `backend=OLLAMA` when the record clears the tier bar, else keep `backend=ANTHROPIC` with the record naming the failing criterion; if C4 lands on `OLLAMA`, re-tune `TEAMMATE_CONFIDENCE_THRESHOLD` on the record; commit per site with the record id in the message and the site's row in the taxonomy table.
- Run latency-only records for C12, C13, C14 on granite.
- Write the per-site landing summary (site, backend, agreement, p95 at 4, error rate, `n`, record id, failing criterion if any) into the PR body; the reviewer applies the bar from it.

### 8. Enumeration and parity tests
- **Task ID**: build-enumeration-test
- **Depends On**: build-land-sites
- **Validates**: `tests/unit/test_llm_task_taxonomy.py` (create)
- **Assigned To**: taxonomy-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement the six checks from Key Elements with the narrowed token list and the allowlist; seed a fake untagged call in a temp module to prove check 1 bites, and seed an `asyncio.wait_for` inside a copy of `_judge_completion_novelty` to prove check 6 bites (both red-state proofs pasted into the PR). Check 6 walks function bodies by name (`_evaluate_promise_async`, `read_the_room`, `_judge_completion_novelty`, `_gemma_classify`) plus every function in `agent/llm/backends/`; it must stay green against the unrelated `asyncio.wait_for` at `agent/session_completion.py:1229` and the subprocess bounds in `memory_quality_audit.py`.

### 9. Validate lane A
- **Task ID**: validate-lane-a
- **Depends On**: build-enumeration-test
- **Validates**: the "no change expected" list, `scripts/pytest-clean.sh tests/unit/`, the mutation checks (one per migrated fail-safe plus the enumeration test), and every Verification row except the three doc rows and the follow-up-issues row
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the "no change expected" list, then `scripts/pytest-clean.sh tests/unit/`; mutation-check each migrated fail-safe and the enumeration test; re-read every comparison record against the bar and flag any `OLLAMA` landing whose record misses; confirm the Verification table.

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-lane-a
- **Validates**: `scripts/pytest-clean.sh tests/unit/test_llm_task_taxonomy.py -q` (doc/code parity check 5), Verification rows "Taxonomy doc exists with the site table", "Feature index updated", "Infra doc exists"
- **Assigned To**: taxonomy-docs
- **Agent Type**: documentarian
- **Parallel**: false
- The Documentation section; the taxonomy table must list every declared site id with its landed backend and record id (parity test).

### 11. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Validates**: the full Verification table and every Success Criteria checkbox
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
| Every site declares a task (AST, multi-line calls included) | `scripts/pytest-clean.sh tests/unit/test_llm_task_taxonomy.py -q -k "task_kwarg"` | exit code 0 |
| `run_typed_local` deleted | `/usr/bin/grep -rc "run_typed_local" agent bridge worker tools reflections scripts tests --include=*.py \| grep -v ":0$" \| wc -l` | 0 |
| Raw Anthropic client gone from migrated modules | `/usr/bin/grep -c "AsyncAnthropic(\|anthropic_slot(" bridge/promise_gate.py bridge/read_the_room.py agent/session_completion.py agent/health_check.py tools/classifier.py` | every count == 0 |
| Raw Ollama and OpenRouter calls gone from migrated modules | `/usr/bin/grep -c "ollama.chat(\|requests.post(" reflections/memory/memory_quality_audit.py reflections/improvement_collect.py` | every count == 0 |
| Hotfix #1055: no coroutine-level timeout inside the LLM call-path functions (AST by function name; the unrelated `wait_for` at `agent/session_completion.py:1229` and the subprocess bounds in `memory_quality_audit.py` are outside the walk) | `scripts/pytest-clean.sh tests/unit/test_llm_task_taxonomy.py -q -k "hotfix_1055"` | exit code 0 |
| Raw Ollama dispatch gone from C14 | `/usr/bin/grep -c "run_in_executor\|ThreadPoolExecutor\|import ollama" reflections/memory/memory_quality_audit.py` | match count == 0 |
| Anti-criterion: emoji embedding path deleted | `/usr/bin/grep -c "OPENROUTER_EMBEDDINGS_URL\|def find_best_emoji(\|_softmax_sample" tools/emoji_embedding.py` | match count == 0 |
| Anti-criterion: no per-site backend switch in settings | `/usr/bin/grep -c "_sites\|structured_decision\|classification_local" config/settings.py` | match count == 0 |
| Every landed site's declared backend matches its record | `.venv/bin/python -m tools.classification_eval --audit` (prints one row per classification site: declared backend, record id, bar result; exits 1 on any `OLLAMA` landing without a passing record or any site without a record) | exit code 0 |
| Anti-criterion: sync `classify_request` deleted | `/usr/bin/grep -c "^def classify_request(" tools/classifier.py` | match count == 0 |
| Evidence kind registered | `/usr/bin/grep -c '"classifier_comparison"' models/improvement_evidence.py` | output > 0 |
| Taxonomy doc exists with the site table | `/usr/bin/grep -c "routing.needs_response" docs/features/llm-task-taxonomy.md` | output > 0 |
| Feature index updated | `grep -c "llm-task-taxonomy.md" docs/features/README.md` | output > 0 |
| Infra doc exists | `test -f docs/infra/llm-task-routing.md` | exit code 0 |
| Follow-up issues exist | `gh issue view 3420 --json state -q .state && gh issue view 3421 --json state -q .state && gh issue view 3422 --json state -q .state` | exit code 0 |
| Follow-up issues carry the landed design | `for n in 3420 3421; do gh issue view $n --json body -q .body \| grep -c "classifier_shadow\|SHADOW_SITES\|--arms incumbent\|STRUCTURED_DECISION_SITES\|CLASSIFICATION_LOCAL_SITES"; done` | both counts 0 |
| Follow-up issues name lane A's runner interface | `for n in 3420 3421; do gh issue view $n --json body -q .body \| grep -c "\-\-candidate"; done` | both counts > 0 |

## Critique Results

Round 2 (2026-09-19, FULL depth, independent roster of 3 critics, re-critique of revision 1). Verdict: NEEDS REVISION (1 blocker, 4 concerns, 3 nits); every row closed by revision 2 (2026-09-19), which also rewrote the bodies of #3420 and #3421. Round 1 (NEEDS REVISION: 3 blockers, 3 concerns, 1 nit) was closed in full by revision 1; its table is in git history at `95f7955bb`.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency | Three accounts of C15's landing disagree. Technical Approach ("The six raw classification sites migrate...") sets C15 to `backend=OLLAMA` at migration and deletes `_OpenRouterJudge`, its `requests` call, and the `promise_detector` reserve/settle "with it"; Task 5 says every raw site lands on `backend=ANTHROPIC` except C14 "so the migration commit changes transport only", which is false for C15 (its backend on `main` is gemma via OpenRouter, never Haiku) and never names C15 as an exception; Task 7 then lists C15 in the loop that sets `backend=OLLAMA` on a passing record "else keep `backend=ANTHROPIC`", a backend C15 was never on, after its prior transport is already deleted. The Success Criterion "every `OLLAMA` landing clears its tier bar in that record" cannot hold for a C15 that landed on `OLLAMA` in Task 5 ahead of the record Task 7 produces. | Technical Approach (raw-site bullet and iteration-loop bullet), Task 5 (order line, C15 bullet), Task 7 (entry state and reference arm), Success Criteria (landings), Test Impact (`test_improvement_evidence.py`) | One story everywhere: Task 5 migrates C15 onto `run_typed(task=PROMISE_JUDGE)` with `backend=ANTHROPIC` and deletes `_OpenRouterJudge`, its `requests` call, and the `promise_detector` reserve/settle in that commit, because lane A's two legs cannot carry gemma via OpenRouter; gemma survives only as the runner's reference arm for C15 (metered under `promise_detector`); Task 7 promotes C15 to `OLLAMA` on a passing record or keeps `ANTHROPIC` with the record naming the failing criterion. The "changes transport only" statement now names C5, C9, C10, C11, C14 and calls C15 the exception. Original note: Make Technical Approach, Task 5, and Task 7 tell one story for C15. Driver constraint: lane A's router has exactly two legs (`ANTHROPIC`, `OLLAMA`), so gemma via OpenRouter is a comparison-runner reference arm only, never a runtime backend or fallback; do not add a third leg. One consistent shape: Task 5 names C15 as a second explicit exception alongside C14, migrating it onto `run_typed(task=PROMISE_JUDGE)` with `backend=ANTHROPIC` (Haiku, the subscription backend every `OLLAMA` site falls back to anyway) while deleting the OpenRouter transport in that commit because no lane-A leg can carry it; Task 7 then runs C15's comparison with the gemma reference arm (metered under `promise_detector`) and sets `backend=OLLAMA` on a passing record; the Success Criterion and the taxonomy table say C15's `ANTHROPIC` state carries the record naming the failing criterion. Whatever shape is chosen, the phrase "changes transport only" must exclude C15 explicitly. |
| CONCERN | Risk & Robustness | The Ollama leg's SDK timeout can never take its stated default. Key Elements (Settings) and Task 1 say the Ollama leg's `sdk_timeout` defaults to `settings.timeouts.local_typed_hard_s` (20 s, `config/settings.py:376`), but Task 2's signature is `run_typed(..., sdk_timeout=DEFAULT_SDK_TIMEOUT, ...)` where `DEFAULT_SDK_TIMEOUT = settings.timeouts.anthropic_sdk_s` (30 s, `agent/llm/wrapper.py:78`), and the wrapper always forwards that value to whichever leg the router picks. Every `OLLAMA`-routed site that does not pass `sdk_timeout` (C1 to C4, C6, C7, C11 to C14) would run granite under the Anthropic 30 s ceiling, and under Risk 5's burst the longer ceiling compounds the pile-up. | Data Flow steps 5 and 6, Architectural Impact (interface changes), Key Elements (Settings), Task 1 (`default_sdk_timeout`), Task 2 (signature and table-driven test), Failure Path Test Strategy | `run_typed(..., sdk_timeout=None, ...)`; after `resolve()` the effective timer is the caller's explicit value or `agent/llm/backends/__init__.py::default_sdk_timeout(route.backend)`, which reads `settings.timeouts.anthropic_sdk_s` (30 s) for `ANTHROPIC` and `settings.timeouts.local_typed_hard_s` (20 s) for `OLLAMA`; `DEFAULT_SDK_TIMEOUT` is deleted from the wrapper; the fallback leg still receives `remaining`. Table-driven test in `tests/unit/test_llm_wrapper.py` (C12-shaped → Ollama fake gets 20.0; C1-shaped → Anthropic fake gets 30.0; explicit 3.0 wins on both), reading the values from `settings.timeouts`. Original note: Change the wrapper signature to `sdk_timeout: float \| None = None` and resolve the effective timer after `resolve()`: `sdk_timeout = route_default(route.backend)` where `ANTHROPIC` maps to `settings.timeouts.anthropic_sdk_s` and `OLLAMA` to `settings.timeouts.local_typed_hard_s`; an explicit caller value (C8's 3.0, the three `RTR_SDK_TIMEOUT` sites) always wins. The fallback leg still receives `remaining`. Add a table-driven test asserting a fake Ollama leg receives 20.0 for a C12-shaped call with no `sdk_timeout` and a fake Anthropic leg receives 30.0 for a C1-shaped call. |
| CONCERN | Risk & Robustness | `tools/doctor` is synchronous end to end (`tools/doctor.py` is plain `def _check_*()` functions, no asyncio), yet the new "LLM routing" section calls `resolve` for a client key, which reaches `peek_open_source`, whose cache-miss path schedules a refresh through `run_in_executor` (Race 2). With no running event loop that is `RuntimeError: no running event loop` inside the one tool meant to report state without crashing. The plan exempts only the comparison runner from the loop requirement. | Key Elements (Eligibility), Task 2 (eligibility bullet and doctor bullet, Validates line), Race 2, Failure Path Test Strategy, Error State Rendering | Doctor keeps a synchronous check (the chosen option: `resolve` and `is_eligible` are plain functions, so no `asyncio.run` boundary). `peek_open_source` is a pure cache read; `_schedule_refresh` does `asyncio.get_running_loop()` first and returns without scheduling on `RuntimeError`, under a new module-level `threading.Lock` (the module has none today); doctor reports a cold client key as `miss (no loop; refresh not scheduled)`. `tests/unit/test_doctor.py::test_llm_routing_section_cold_client_key_sync` invokes the check synchronously with the cache cleared and `gh` unavailable and is named in Task 2's Validates line. Original note: Make the refresh scheduling defensive: in `peek_open_source`, `try: loop = asyncio.get_running_loop() except RuntimeError: return None` (record the miss, schedule nothing) before touching `_REFRESHING`; doctor then reports the cold key as `miss (no loop; refresh not scheduled)`. Extend `tests/unit/test_doctor.py` with a cold-cache client-key case invoked synchronously, the way doctor's real entry point runs it, and name that test in Task 2's Validates line. |
| CONCERN | Scope & Value, History & Consistency | Revision 1 moved the comparison runner into lane A and removed shadow mode, but the follow-up issues still carry the pre-revision design: #3420's body lists building `tools/classification_eval/` as its own Desired outcome 1 with `--arms incumbent,ollama` and an `is_open_source`-sampled input population, and #3421's body still specifies `MODELS__STRUCTURED_DECISION_SHADOW_SITES`, `ImprovementEvidence(kind="classifier_shadow")`, and a 24-hour shadow run, while this plan widens `EVIDENCE_KINDS` by `classifier_comparison` only. The Success Criterion checks only that the issues exist (`gh issue view ... --json state`), so the drift survives merge and the next lane either rebuilds the runner or writes an unregistered evidence kind. | #3420 and #3421 bodies and titles (rewritten via `gh issue edit` on 2026-09-19, during revision 2, so no post-merge task is needed); Success Criteria (follow-up issues); Verification (two new rows); Freshness Check notes | Both bodies now describe lane A's runner (`--site <id> --candidate <backend>`, reference/candidate naming, `valor`-only inputs), `classifier_comparison` as the only evidence kind, the one-word `backend` landing on a passing record, and the router rules for `LOCAL_ZERO_SHOT` (Anthropic fallback) and `DECISIONS` (Ollama fallback, only on sites whose lane-A record shows granite clearing the bar); #3420 drops the runner deliverable and the granite-baseline task (lane A produces both); #3421 depends on lane A only. Success Criterion and two Verification rows check body content: the tokens `classifier_shadow`, `SHADOW_SITES`, `--arms incumbent`, `STRUCTURED_DECISION_SITES`, `CLASSIFICATION_LOCAL_SITES` absent from both, `--candidate` present in both (both rows run green against the live issues). Original note: Add a checkbox to the Documentation section (or a Task 10 bullet) that edits #3420 and #3421 after lane A merges: strike the "build `tools/classification_eval/`" deliverable and the `incumbent` arm name from #3420 (point at the shipped `compare(site, inputs, arms)` and `--site <id> --candidate <backend>` interface, `reference`/`candidate` naming, `valor`-only inputs), and replace #3421's shadow-mode mechanism and `classifier_shadow` kind with the lane-A landing protocol and `classifier_comparison`. Change the Success Criterion to check content, for example `gh issue view 3421 --json body -q .body` contains no `classifier_shadow` and no `SHADOW_SITES`. |
| CONCERN | structural | The `asyncio.wait_for(` assertions cannot pass as written. The Success Criterion greps "those seven modules", the Verification row "Hotfix #1055" greps `agent/session_completion.py`, and Task 8 asserts absence in four modules, but `agent/session_completion.py:1229` already wraps a Telegram send callback in `asyncio.wait_for` (unrelated to the LLM call and a legitimate bound) and `reflections/memory/memory_quality_audit.py` has three uses: `:513` around `loop.run_in_executor(executor, _gemma_classify, content)` and `:617`, `:696` around subprocess `communicate()`. The C14 migration is also under-specified: `_gemma_classify` is a sync function run in a thread executor, so it cannot simply "call `run_typed`" (a coroutine) without reworking `_layer3_classify`'s dispatch. | Key Elements (enumeration test check 6), Technical Approach (raw-site bullet, C14), Task 5 (C14 bullet), Task 8, Risk 1, Failure Path Test Strategy, Success Criteria (seven-module grep), Test Impact (`test_memory_quality.py`), Verification ("Hotfix #1055" row and a new C14 row) | Check 6 of the enumeration test walks the ASTs of `_evaluate_promise_async`, `read_the_room`, `_judge_completion_novelty`, `_gemma_classify`, and every function in `agent/llm/backends/` for `asyncio.wait_for` call nodes; `agent/session_completion.py:1229` and the subprocess bounds at `memory_quality_audit.py:617`/`:696` are outside the walk; the Verification row runs that test and the Success Criterion grep no longer lists `asyncio.wait_for(`. C14 fully specified: `async def _gemma_classify(content) -> MemoryAuditDecision \| None` awaiting `run_typed(GEMMA_AUDIT_PROMPT.format(...), MemoryAuditDecision, task=MEMORY_AUDIT, project_key=<memory row's project>, sdk_timeout=GEMMA_CALL_TIMEOUT_SEC, hard_timeout=None)` on `backend=OLLAMA`, awaited directly from `_layer3_classify`; the raw `ollama.chat(...)` at `:442`, `import ollama`, the `ThreadPoolExecutor`, `run_in_executor`, and the `wait_for` at `:513` are deleted; the deadline check is unchanged; `_layer3_classify` reads attributes; fail-safe `None`. Original note: Scope every #1055 assertion to the LLM call path, not the file: in Task 8 and the Verification row, assert no `asyncio.wait_for` inside the function bodies of `_evaluate_promise_async`, `read_the_room`, `_judge_completion_novelty`, and anywhere in `agent/llm/backends/` (AST walk by function name, as the enumeration test already does for tokens), and drop `asyncio.wait_for(` from the seven-module Success Criterion grep. For C14, specify that `_gemma_classify` becomes `async def` awaiting `run_typed(task=MEMORY_AUDIT, sdk_timeout=GEMMA_CALL_TIMEOUT_SEC)` directly from `_layer3_classify`, replacing the executor + `wait_for` pair for that call only (the subprocess timeouts at `:617` and `:696` stay), and that the deadline check on the layer-3 wallclock budget is unchanged; update Test Impact for `tests/unit/test_memory_quality.py` accordingly. |
| NIT | Risk & Robustness | The plan relies on `OllamaProvider(openai_client=AsyncOpenAI(...))` without citing the pinned signature. Driver verification: on the installed `pydantic-ai-slim==2.45.0`, `OllamaProvider.__init__(self, base_url=None, api_key=None, openai_client: AsyncOpenAI \| None = None, http_client=None)`, and `openai==3.8.0` is installed, so the injection point exists; the critic's original CONCERN premise ("nothing confirms") is closed by this check and the row is carried as a NIT. | Task 1 (Ollama leg bullet) | Cited verbatim from the pinned stack on 2026-09-19 (`pydantic-ai-slim==2.45.0`, `openai==3.8.0`): `OllamaProvider.__init__(self, base_url: str \| None = None, api_key: str \| None = None, openai_client: AsyncOpenAI \| None = None, http_client=None)`. |
| NIT | structural | Freshness Check notes say the raw-transport census matched "32 files"; the plan's own grep returns 30 at both `001d9040e` and HEAD `95f7955bb`. The reconciled list (18 declared, 7 migrated, 3 allowlisted, `agent/memory_extraction.py` as a `run_typed` site, `tools/image_gen/tests/test_image_gen.py` skipped) accounts for all 30, so only the number is wrong. | Freshness Check notes | Re-run on `main` at `d780d4d99`: the grep returns 30 files; the note now says 30 and itemizes all 30 (18 declared, 7 migrated, 3 allowlisted, `agent/memory_extraction.py`, `tools/image_gen/tests/test_image_gen.py`). |
| NIT | structural | The Verification row "Every site declares a task" is a line-based grep (`grep -rn "run_typed("` then `grep -vc "task="`); three sites today open the call on one line and put kwargs on the next (`agent/memory_extraction.py:371`, `bridge/context_recall.py:269`, `bridge/injection_inspection.py:170`), so the row can fail on a correctly tagged site. | Verification ("Every site declares a task" row), Key Elements (check 1 is AST-based) | The row now runs `tests/unit/test_llm_task_taxonomy.py -k task_kwarg`, whose check 1 inspects each `run_typed` `Call` node for a `task` keyword, so the three multi-line calls pass on content. Original note: Point the row at the enumeration test (`scripts/pytest-clean.sh tests/unit/test_llm_task_taxonomy.py -q`) or require `task=` on the opening line. |
