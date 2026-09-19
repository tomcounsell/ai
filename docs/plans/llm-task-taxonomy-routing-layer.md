---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-18
tracking: https://github.com/tomcounsell/ai/issues/3410
last_comment_id: 5739123237
revision_applied: true
revision_applied_at: 2026-09-19T03:48:04Z
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
- The emoji site the issue originally listed as C16 (row since removed from the issue table; email triage is now C16) is no longer an LLM or embedding call: `find_best_emoji_for_message` is a `random.choice` keyed on the C5 work type. The embedding path (`find_best_emoji`, an OpenRouter embeddings call over `data/emoji_embeddings.json`) is live: `agent/constants.py:190-192` resolves `REACTION_SUCCESS` and `REACTION_COMPLETE` through it for the terminal reaction `agent/session_executor.py:2755-2757` sends on every completed session, and `tools/react_with_emoji.py:94-96` and `:171-173` call it from the reaction CLI that `agent/hooks/stop.py:53` prescribes. It takes no prompt and returns no decision, so it is outside this taxonomy and this plan leaves it untouched; #3422 owns it.
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
| ex-C16 (emoji) | `tools/emoji_embedding.py::find_best_emoji_for_message` | :439 | none: `random.choice(ACTION_EMOJI_MAP[action])` | one emoji | `DEFAULT_EMOJI` | **drifted**: no model call; the embedding path `find_best_emoji` (:325) is live (callers `agent/constants.py:190-192`, `tools/react_with_emoji.py:94-96,171-173`; tests `tests/unit/test_reaction_never_hostile.py`, `tests/unit/test_stall_detection.py:831`) and outside this taxonomy; #3422 owns it |
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

**Notes:** Two corrections carried into the plan: the emoji site (originally C16; the issue table was renumbered on 2026-09-18 and email triage is now C16) leaves the classification list (no model on `main`; the embedding path and its callers stay as they are, and a decision-backed emoji choice is filed separately as #3422), and read-the-room's action set is `send/trim/suppress`. The issue's live Jev probe is confirmed: the `jev-issue` agent ran it on 2026-09-18 from this repo's venv, and the verbatim request and response are in Research. Revision 1 (2026-09-19) incorporates Tom's answers in comment 5737918683 and the round-1 critique; the hotfix #1055 invariants were re-read on `main` at `001d9040e` (`bridge/promise_gate.py:685-689`, `bridge/read_the_room.py:26-28`, `agent/session_completion.py:444`) and the raw-transport module census was re-run. Revision 2 (2026-09-19) re-ran the census on `main` at `d780d4d99`: the plan's grep (Task 4) returns exactly 30 files, and all 30 are accounted for: 18 declared in Task 4, 7 migrated in Tasks 5 and 6, 3 allowlisted, `agent/memory_extraction.py` (a `run_typed` site; its `:43` hit is a comment), and `tools/image_gen/tests/test_image_gen.py` (a test file the walk skips). Revision 2 also verified the `OllamaProvider` injection point on the pinned stack and rewrote the bodies of #3420 and #3421 to the comparison-runner-promotes-on-record design. Revision 3 (2026-09-19) re-ran the wrapper caller census on `main` at `ed3f8a9c3` (`/usr/bin/grep -rn "run_typed\(_local\)\?(" --include='*.py' agent bridge worker tools reflections scripts`, excluding `agent/llm/wrapper.py`): 14 hits, 12 `run_typed` and 2 `run_typed_local`, the fourteenth being the live network probe at `agent/llm/compat.py:525`, now declared in Task 4; and re-ran the `find_best_emoji` caller census (six production lines, listed above), which moved the emoji deletion out of lane A.

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
5. **Backend leg** (`agent/llm/backends/{anthropic,ollama}.py`): each returns a validated instance of the caller's output type or raises `LLMCallError(reason=...)`. The Anthropic leg is today's `run_typed` body (`semaphore_slot(timeout=slot_timeout)` then `AsyncAnthropic(timeout=sdk_timeout, max_retries=...)`, PydanticAI `Agent(system_prompt=system)`); the Ollama leg is today's `run_typed_local` body with its `asyncio.wait_for` replaced by an SDK-level timer (`OllamaProvider(openai_client=AsyncOpenAI(base_url=..., timeout=sdk_timeout, max_retries=0))`) so both legs honor the hotfix #1055 invariant the same way. The SDK timer is per leg: the wrapper's `sdk_timeout` defaults to `None`, and after `resolve()` the effective value is the caller's explicit number when given, else `agent/llm/backends/__init__.py::default_sdk_timeout(route.backend)`, which reads `settings.timeouts.anthropic_sdk_s` (30 s) for `ANTHROPIC` and `settings.timeouts.local_typed_hard_s` (20 s) for `OLLAMA`. The wrapper carries no timeout constant of its own; `DEFAULT_SDK_TIMEOUT` is deleted. Between `resolve()` and the primary leg the wrapper runs the degraded-stack guard with the axis the route needs: `_guard_stack("run_typed", signature_axis=(route.backend is Backend.ANTHROPIC))`, so an Anthropic create-signature break still raises `LLMStackIncompatible` for Anthropic-routed calls and never for Ollama-routed ones (today's two-axis split at `agent/llm/wrapper.py:112-114`, `:192`, `:281`, kept behind one entry point). `_skip_guard=True` skips the guard, exactly as today, for the one caller that needs a pure probe (`agent/llm/compat.py:525`).
6. **Fallback**: on `LLMCallError` from the primary leg with a `fallback` on the route, the wrapper runs the fallback leg once inside the caller's budget and logs one warning naming the site and both backends. The budget is the caller's, never the primary leg's default timer: `budget = sdk_timeout if sdk_timeout is not None else hard_timeout` (both `None` means uncapped); `start = monotonic()` before the primary; on failure `elapsed = monotonic() - start`, `fb_timeout = default_sdk_timeout(route.fallback.backend)` when `budget is None`, else `min(default_sdk_timeout(route.fallback.backend), budget - elapsed)`; the fallback is skipped only when `budget is not None and budget - elapsed < 0.5`; `fb_slot = fb_timeout if slot_timeout is None else min(slot_timeout, fb_timeout)`. So a C1 with no `sdk_timeout` whose granite call times out at 20 s still gets a 15 s Haiku attempt inside the 35 s `hard_timeout`, and the fallback's live request always finishes before the wrapper's outer cap can cancel it; a 3 s site's explicit `sdk_timeout` is its budget, so its math is unchanged. Before an Anthropic fallback leg the wrapper runs `_guard_stack("run_typed:fallback", signature_axis=True)`, so a signature-broken fallback raises `LLMStackIncompatible` (a subclass of `LLMCallError`) and the caller's fail-safe applies. The result carries no marker; the caller cannot tell which leg answered, and does not need to.
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
- **Interface changes**: `run_typed` gains keyword-only `task: LLMTask` (required), `project_key: str | None = None`, `system: str | None = None`, `slot_timeout: float | None = None`, `max_retries: int | None = None`; `sdk_timeout` becomes `float | None = None` (resolved per backend leg from `TimeoutSettings` after routing; an explicit caller value always wins); `hard_timeout` keeps its default for thinking sites and must be `None` at the three 3 s sites (Technical Approach); `_skip_guard: bool = False` stays on the signature with its single caller (`agent/llm/compat.py:525`, pinned by `tests/unit/test_llm_wrapper.py:516`). `run_typed_local` is deleted; its body becomes `agent/llm/backends/ollama.py`, and `agent/llm/__init__.py` (`:13,17,22,25`) drops `DEFAULT_SDK_TIMEOUT` and `run_typed_local` from its imports and `__all__`. `LLMCallError` gains `reason: Literal["timeout", "slot_timeout", "transport", "validation"]` so tests and the fallback logic can distinguish them. Six classifier functions and `should_respond_async`'s callees gain a `project_key` keyword with a `None` default. No fields exist for #3421's question builder (`question`, `noul_threshold`, `thresholds`, `decision_options`); frozen-dataclass fields with defaults and keyword-only kwargs are additive, so that lane adds them without touching a lane-A call site.
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
- **Wrapper** (`agent/llm/wrapper.py::run_typed`): validates the prompt, requires `task`, resolves the route, runs the degraded-stack guard with the route's axis, runs the primary leg, runs the fallback leg once on `LLMCallError` within the caller's budget (Data Flow step 6), and returns the validated output type. `LLMCallError` semantics for the caller are unchanged.
- **Eligibility** (`tools/improvement_eligibility.py::is_eligible`, `peek_open_source`, `_schedule_refresh`, `warm_cache`): `valor` pinned `True` in code ahead of the cache; cache-only read for every other key with a fail-closed miss and one background refresh per key, scheduled only when a loop is running (a caller with no loop, such as `tools/doctor`, gets the miss and no refresh); `bridge/telegram_bridge.py` warms `ACTIVE_PROJECTS` and `worker/__main__.py::_run_worker` warms its `projects` at startup. The blocking `is_open_source` keeps its `gh` shell-out for the improvement tooling and the refresh.
- **Enumeration test** (`tests/unit/test_llm_task_taxonomy.py`): AST walk over `agent/ bridge/ worker/ tools/ reflections/ scripts/` (skipping `tests/` directories and `test_*.py`) that (1) requires `task=` on every `run_typed(` call, (2) asserts `run_typed_local` no longer exists anywhere, (3) requires a module-level `LLMTask(` declaration in any module whose AST contains an LLM call token, where the tokens are exactly `messages.create(`, `chat.completions.create(`, `ollama.chat(`, and a reference to `OPENROUTER_URL`, outside the allowlist (`agent/llm/backends/`, `agent/anthropic_client.py`, `agent/session_runner/harness/`, `tools/ollama_client.py`, and `tools/image_gen/__init__.py` with the reason "image generation: no text decision or thinking output"), (4) asserts site ids are unique and every `classification` task is reached through `run_typed`, (5) asserts the taxonomy table in `docs/features/llm-task-taxonomy.md` lists every declared site id (doc/code parity, same shape as `tests/unit/test_sdlc_skill_md_parity.py`), and (6) asserts the hotfix #1055 invariant on the LLM call path by function body, not by file: no `asyncio.wait_for` call node inside `bridge/promise_gate.py::_evaluate_promise_async`, `bridge/read_the_room.py::read_the_room`, `agent/session_completion.py::_judge_completion_novelty`, `reflections/memory/memory_quality_audit.py::_gemma_classify`, or any function in `agent/llm/backends/` (`ast.walk` over the named `FunctionDef`/`AsyncFunctionDef` bodies looking for `Call` nodes whose func is the attribute `wait_for` on the name `asyncio`), and, in the same walk over the four named bodies, that every `Call` whose func is `Name(id="run_typed")` or `Attribute(attr="run_typed")` carries a `hard_timeout` keyword whose value is `Constant(None)`, failing with the site name and line otherwise. The second half guards the mutation that happens by default: the only coroutine-level timeout on these sites' call path after migration is the wrapper's `hard_timeout` (35 s default, applied outside the leg), so dropping `hard_timeout=None` from one call would silently reintroduce the #1055 hazard while a `wait_for`-only scan stayed green. The assertion is per named body, never global; `DEFAULT_HARD_TIMEOUT` stays the wrapper default for thinking sites. The `asyncio.wait_for` around the Telegram send callback at `agent/session_completion.py:1229` and the two around `proc.communicate()` in `memory_quality_audit.py` (`:617`, `:696`) are legitimate bounds on non-LLM awaits and stay outside the assertion. Check (1) is AST-based too (a `Call` whose func resolves to `run_typed` must carry a `task` keyword), so a call whose kwargs sit on the next line (`agent/memory_extraction.py:371`, `bridge/context_recall.py:269`, `bridge/injection_inspection.py:170` today) passes on its content. Embeddings (`embeddings.create(`), transcription (`audio.transcriptions`), and image generation are not LLM task sites under this taxonomy: none of them takes a prompt and returns a decision or a thought, so `tools/transcribe/`, `tools/link_analysis/`, and the embedding call in `tools/impact_finder_core.py:200` need no declaration (its `messages.create(` at `:395` does).
- **Settings**: no new keys. The SDK timer is chosen per backend leg from `config/settings.py::TimeoutSettings`: `default_sdk_timeout(Backend.ANTHROPIC)` is `settings.timeouts.anthropic_sdk_s` (30 s) and `default_sdk_timeout(Backend.OLLAMA)` is `settings.timeouts.local_typed_hard_s` (20 s; its meaning narrows from "outer wall-clock cap" to "the Ollama leg's single SDK-level timer"; catalog entry updated, and the `anthropic_sdk_s` description's reference to the wrapper's `DEFAULT_SDK_TIMEOUT` is rewritten to name the Anthropic leg). The wrapper's `sdk_timeout` parameter defaults to `None` and the wrapper owns no timeout constant, so a site that passes nothing gets its leg's timer and a site that passes a number (C8's 3.0, the three `RTR_SDK_TIMEOUT` sites, C14's `GEMMA_CALL_TIMEOUT_SEC`) gets that number on whichever leg the router picks. `config/models.py` is unchanged in lane A; `JEV`, `OPENROUTER_DECISIONS_URL`, and `MODEL_INFO[JEV]` belong to #3421.
- **Comparison runner** (`tools/classification_eval/`): `compare(site, inputs, arms) -> ComparisonRecord` with per-arm agreement against the reference arm, p50/p95 latency at concurrency 1 and 4, cost per call, error rate, price with retrieval date, and `n`; writes `ImprovementEvidence(kind="classifier_comparison")` and records claims on an investigation of case `1ec40086`. The reference arm is the site's backend on `main` before this PR (Haiku for C1 through C11, gemma via OpenRouter for C15) with the site's prompt verbatim; the candidate arm is granite through the new Ollama leg with whatever prompt the builder settles on. Inputs: the parametrized examples in each site's existing unit tests plus a sample of real inbound messages for project `valor` from the subconscious memory store (`memory_search`, project `valor`), at least 50 per site and at least 200 for C1 through C4. C12, C13, and C14 stay on granite, so the runner records their latency only (the measurements #2494 lacks).
- **Acceptance bar** (documented in `docs/features/llm-task-taxonomy.md`; applied by the PR reviewer per site, reading the comparison record linked from the PR): a site lands on granite when all hold: agreement with the reference arm at or above the tier bar (`high` 95%, `medium` 90%, `low` 85%), p95 latency at concurrency 4 within the site's budget (3.0 s for C8, C9, C10; otherwise at or below the reference arm's p95 plus one second, since the local leg buys independence and cost rather than speed), candidate error rate at or below 2%, and the comparison `n` at or above the minimum above. A site that misses the bar after the builder's iteration lands with `backend=ANTHROPIC` and its record attached, and the PR says so per site. Tiers: `high` = C1, C2, C3, C4, C12 (a wrong answer drops or misroutes a human's message or binds to the wrong job); `medium` = C5, C7, C8, C10, C11, C13, C14; `low` = C6, C9 (regex floor beneath it), C15 (pre-screen only). C16 (email triage) is `client_only`, lands on `ANTHROPIC`, and is never a candidate. The same bar governs #3420 and #3421.

### Flow

Inbound message → bridge resolves project → classifier declares its task → `run_typed` asks the router → router picks the declared backend for eligible context, or the subscription backend for client context, with the subscription backend as fallback when the local leg fails → leg returns the typed answer → caller applies its own fail-safe → message routed.

### Technical Approach

- **Kind at the call site, as an argument.** The issue left open whether kind is an argument, a marker on the output model, or a separate entry point. Argument wins: an output model can serve two sites with different error costs (`IntentDecision` and `IntentDecisionWithRecall` share a site; `RoutingDecision` could serve a thinking use), and a second entry point would recreate the per-site function choice this plan removes. The `LLMTask` constant is declared next to the output model so the two read together.
- **The hotfix #1055 invariant moves into the legs; the three 3 s sites never see a coroutine-level timeout.** `run_typed` today wraps `agent.run(prompt)` in `asyncio.wait_for(hard_timeout)` (`agent/llm/wrapper.py:215-216`), the exact pattern `bridge/promise_gate.py:685-689`, `bridge/read_the_room.py:26-28`, and `agent/session_completion.py:444` forbid because it leaks httpx connections under cancellation. So C9, C10, and read-the-room call `run_typed(..., sdk_timeout=RTR_SDK_TIMEOUT, slot_timeout=RTR_SDK_TIMEOUT, max_retries=0, hard_timeout=None)`: the only timer around the API call is the SDK-level `AsyncAnthropic(timeout=...)`, and queue wait is bounded separately by `semaphore_slot(timeout=slot_timeout)` (`agent/anthropic_client.py:211`), which the Anthropic leg enters before constructing the client; a slot timeout raises `LLMCallError(reason="slot_timeout")` without ever entering the client context (tested with a cancellation test). The `hard_timeout` kwarg stays for thinking sites that use it today and is applied by the wrapper outside the leg; the module docstrings of the three hot-path modules keep their invariant text and point at the leg. The Ollama leg gets the same treatment: its `asyncio.wait_for` becomes `AsyncOpenAI(timeout=sdk_timeout, max_retries=0)` on the provider's client, so a granite-backed C8, C9, or C10 has one SDK-level timer and no coroutine cancellation around a live request.
- **Read-the-room migrates in lane A as one thinking task** (design call under Tom's answer 3). The action and the rewrite come from one call under a 3 s budget; splitting them doubles the budget on every outbound message. It migrates onto `run_typed(task=READ_THE_ROOM, system=READ_THE_ROOM_SYSTEM_PROMPT, sdk_timeout=RTR_SDK_TIMEOUT, slot_timeout=RTR_SDK_TIMEOUT, max_retries=0, hard_timeout=None)` with `RoomVerdict` as a `BaseModel` (`action: Literal["send", "trim", "suppress"]`), keeping every short-circuit and the `rtr_error` fail-safe. Migrating it is what makes the enumeration test allowlist-free in `bridge/`. It is a thinking task, so it stays on Anthropic and needs no comparison.
- **The six raw classification sites migrate with their fail-safes byte-identical, then land on granite.** C5's dict result becomes `WorkTypeDecision(type: Literal[bug, feature, chore, sdlc], confidence, reason)` and `classify_request_async` returns the same dict shape it returns today; the sync `classify_request` (`tools/classifier.py:90`, no production caller in `agent bridge worker tools reflections scripts`) is deleted together with the two module-docstring examples that name it (`tools/classifier.py:16,19`), and its two test callers (`tests/tools/test_classifier.py:11`, `tests/unit/test_work_request_classifier.py:174-176`) go with it (Test Impact). C9 keeps `max_retries=0` and the 3 s slot timeout via the new kwargs. C10 and C11 keep their `True`/`False` defaults. C14 becomes `MemoryAuditDecision(is_junk: bool, anomaly_signal: str | None, why: str)` on `run_typed(task=MEMORY_AUDIT)` with `backend=OLLAMA` (already granite today); `_gemma_classify` becomes `async def _gemma_classify(content: str) -> MemoryAuditDecision | None`, awaited directly from `_layer3_classify` with `sdk_timeout=GEMMA_CALL_TIMEOUT_SEC` and `hard_timeout=None`, which deletes the raw `ollama.chat(...)` call at `reflections/memory/memory_quality_audit.py:442`, the `import ollama`, the per-invocation `ThreadPoolExecutor`, the `loop.run_in_executor` dispatch, and the `asyncio.wait_for` at `:513`; the layer-3 wallclock deadline check is unchanged and the subprocess `asyncio.wait_for` calls at `:617` and `:696` stay, since they bound `proc.communicate()` and never an LLM call. C15 is the one migration that changes backend as well as transport. Its backend on `main` is gemma via OpenRouter, and lane A's router has exactly two legs (`ANTHROPIC`, `OLLAMA`), so no lane-A leg can carry that transport: C15 becomes `PromiseJudgeDecision(answer: bool, span: str, confidence: float)` on `run_typed(task=PROMISE_JUDGE)` with `backend=ANTHROPIC` (Haiku, the subscription backend every `OLLAMA` site falls back to anyway), and `_OpenRouterJudge`, its `requests` call, and its meter reserve/settle under purpose `promise_detector` are deleted in that same commit. Gemma via OpenRouter survives only as C15's reference arm inside the comparison runner, metered under the existing `promise_detector` purpose. Task 7 then promotes C15 to `backend=OLLAMA` on a passing comparison record, exactly like the Haiku-backed sites. The Ollama leg therefore needs no `base_url`/`api_key` override.
- **Landing on granite is the builder's iteration loop, not a phase.** For each classification site that leaves Task 5 on `backend=ANTHROPIC`, the builder runs the comparison (reference arm: the prompt verbatim on the site's `main` backend, Haiku for every site except C15, whose reference arm is gemma via OpenRouter; candidate: granite through the leg), reads the record, and iterates on the candidate side only (prompt shaping for a 3B model, a tighter output schema, a `system` string) until the site clears its tier bar or the iteration budget (half a build day per site) is spent. It then sets `backend=OLLAMA` or `backend=ANTHROPIC`, commits the site with the record id in the commit message, and moves on. Lower tiers land first (C6, C9, C15, then `medium`, then `high`) so the loop is calibrated on cheap mistakes.
- **C7's `risk: str` becomes `Literal["suspected", "none"]`.** Same comparison, tighter schema; a 3B model does better with a closed set and #3421's question builder needs the values.
- **Thinking sites are tagged, not migrated.** Sites on `run_typed` add `task=`; sites on raw transports for a reason (vision, audio, streaming, the harness) add a module-level `LLMTask(kind=THINKING)` declaration. The enumeration test enforces both. The full declaration list is Task 4; it was reconciled against a token census of `main` (30 modules) and against the wrapper caller census (14 calls: 12 `run_typed`, 2 `run_typed_local`) so the test cannot fail on an untouched file. The one wrapper caller outside the issue's thinking list is the live network probe in `agent/llm/compat.py:525` (`python -m agent.llm.compat --json --allow-network`, the auto-bump `llm` gate that `scripts/update/deps.py:660` runs): it declares `NETWORK_PROBE = LLMTask(site="compat.network_probe", kind=THINKING, backend=ANTHROPIC)` and passes `task=NETWORK_PROBE, _skip_guard=True`. `THINKING` short-circuits at router rule 1, so the probe still exercises the Anthropic leg with `semaphore_slot()` and both timers, and `_skip_guard=True` still means the call never reaches `_guard_stack` → `stack_axes()` → `resolve_degraded_flag()`, which is the purity `compat.py:490-498` documents and `tests/unit/test_llm_stack_compat.py:434-452` pins.
- **Project key threading.** Sites that can know their project pass it; sites that cannot leave it `None` and resolve to the subscription backend by the fail-closed rule. `should_respond_async` and `classify_work_request` gain `project_key`; the promise gate and read-the-room resolve it from `chat_id` through `find_project_for_chat`; session-side sites (C10, C11) read `AgentSession.project_key`; C14 reads the memory row's project; C15 already has it. Client rooms therefore run C12 and C13 on Haiku after this PR, where today they run on granite for every room; that is §7 applied consistently and is called out in the PR.
- **Confidence thresholds stay per site.** `JOB_ROUTER_CONFIDENCE_THRESHOLD`, `INTENT_CONFIDENCE_THRESHOLD`, and `TEAMMATE_CONFIDENCE_THRESHOLD` are site constants tuned to the site's backend. C12 and C13 keep granite and their thresholds unchanged. If C4 lands on granite, the builder re-tunes `TEAMMATE_CONFIDENCE_THRESHOLD` on the comparison record (self-reported confidence is model-specific) and the record shows the chosen value. Per-backend threshold maps belong to #3421, where a probability-calibrated backend first appears.
- **Deleted from lane A by Tom's answer 2**: the five `MODELS__*` CSV switches, `TIMEOUTS__DECISIONS_S`, shadow routes and shadow dispatch, the "no site switched on by default" anti-criterion, and the notion of an incumbent distinct from the declared backend. The decisions leg's question builder, its metering under purpose `structured_decision`, and the Jev live-listing probe test are #3421's, written against Research finding 1.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every migrated site keeps its `except Exception` or `except LLMCallError` block; for each, a test asserts the fail-safe value AND the log line (`logger.warning` with the site id) when the leg raises `LLMCallError`: C5 `{}`-equivalent dict, C9 `None` → heuristic, C10 `False`, C11 `healthy=True`, C14 `None`, C15 `promises-judge-failed`, read-the-room `send`/`rtr_error`.
- [ ] Router fallback: a test makes the Ollama leg raise `LLMCallError(reason="transport")` on a call with `sdk_timeout=3.0` and asserts the Anthropic leg ran exactly once with `sdk_timeout` equal to the remaining budget and a warning names the site and both backends; a second test spends the budget in the primary and asserts the fallback is skipped and the primary's `LLMCallError` propagates; a third test, on a faked monotonic clock, makes the Ollama fake raise `LLMCallError(reason="timeout")` after consuming 20 s on a call with no `sdk_timeout` (so `budget` is the 35 s `hard_timeout`) and asserts the Anthropic fake is called once with `sdk_timeout == 15.0` and `slot_timeout == 15.0`.
- [ ] Degraded-stack guard by route: with `_load_stack` faked to a signature-broken pair, an `OLLAMA`-routed call (key `valor`) returns its result and an `ANTHROPIC`-routed call raises `LLMStackIncompatible`; with the fallback leg reached under the same fake, the wrapper raises `LLMStackIncompatible` (an `LLMCallError`) rather than a provider `TypeError`; `_skip_guard=True` reaches neither guard (`tests/unit/test_llm_stack_degraded_start.py`, `tests/unit/test_llm_stack_compat.py`).
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
- [ ] `tests/unit/test_pr_classification_fastpath.py`: no change expected (it exercises `bridge.routing.classify_work_request` and never names `classify_request`); listed so the builder runs it with the C5 commit.
- [ ] `tests/tools/test_classifier.py` (skipif-marked live-API file importing the sync `classify_request` at `:11`): DELETE, or rewrite against `classify_request_async` if the live check is worth keeping.
- [ ] `tests/unit/test_work_request_classifier.py:174-176` (patches `tools.classifier.anthropic.Anthropic` and calls the sync `classify_request`): UPDATE to the `run_typed` fake returning `WorkTypeDecision` through `classify_request_async`.
- [ ] `tests/unit/test_memory_quality.py`: UPDATE the `_gemma_classify` fakes from `ollama.chat` to an async `run_typed` fake returning `MemoryAuditDecision`; the executor-timeout case becomes `LLMCallError(reason="timeout")` with the same `None` verdict and the same `unavailable_count` outcome; any assertion on `ThreadPoolExecutor` or `run_in_executor` is DELETED with the dispatch it tested.
- [ ] `tests/unit/test_improvement_evidence.py` (84 tests): UPDATE the `_OpenRouterJudge` fakes to a `run_typed` fake returning `PromiseJudgeDecision`; the `promise_detector` reserve/settle assertions are DELETED with the OpenRouter call (the judge runs on the Anthropic leg after Task 5 and on whichever leg Task 7 lands it, both unmetered; the purpose survives only for the runner's gemma reference arm).
- [ ] `tests/unit/test_llm_wrapper.py`: UPDATE: calls pass `task=`; the `hard_timeout` test moves to the wrapper level (outside the leg); add router, fallback-budget (including the faked-clock case), and slot-timeout tests; `test_skip_guard_has_exactly_one_call_site_outside_wrapper` (`:516`) passes unchanged because `compat.py:525` remains the one `_skip_guard=True` caller.
- [ ] `tests/unit/test_llm_stack_degraded_start.py`: UPDATE: `test_two_axis_split_leaves_the_local_leg_running` (`:412-448`) and `test_anthropic_import_error_local_path` (`:490`) call `run_typed_local` today; both become `await wrapper_mod.run_typed("hello", Decision, task=LLMTask(site="test.local", kind=CLASSIFICATION, backend=OLLAMA), project_key="valor")` under the same `_load_stack` patch (FunctionModel in place of `OpenAIChatModel`). The first asserts `result.decision == "ok"` while the `ANTHROPIC`-routed call still raises `LLMStackIncompatible`; the second keeps `pytest.raises(LLMStackIncompatible)` because `loader_ok=False` is stack-wide.
- [ ] `tests/unit/test_llm_stack_compat.py`: no change expected; `test_check_network_returns_none_on_a_successful_probe` (`:347`) and the `allow_network=True` purity test (`:434-452`) drive the real `_check_network` body through the `_load_stack` seam and must pass with `task=NETWORK_PROBE` on the probe call. Listed so the builder runs it with the Task 2 signature change.
- [ ] `tests/unit/test_settings.py:40`: UPDATE the `test_anthropic_sdk_default` docstring from "Must match agent/llm/wrapper.py DEFAULT_SDK_TIMEOUT" to name `anthropic_sdk_s` as the Anthropic leg's timer.
- [ ] `tests/unit/test_routing.py`, `test_intent_classifier.py`, `test_agent_catchup.py`, `test_injection_inspection.py`, `test_context_recall.py`, `test_context_recall_wiring.py`, `test_email_cs_triage.py`, `tests/unit/memory_extraction/test_memory_extraction_event_loop_safety.py`: no change expected (they fake `run_typed` itself, which is backend-agnostic; fakes accept `**kwargs`; `call_args[0]` positional assertions still hold). Listed so the builder runs them first as the "wrapper contract unchanged" proof.
- [ ] `tests/unit/test_improvement_eligibility.py`: UPDATE: add `is_eligible` (valor pin, client miss, `None`), `peek_open_source`, `warm_cache`, and the single-refresh race test.

## Rabbit Holes

- **Re-scoring the reference prompts.** The reference arm runs each site's prompt verbatim on the site's `main` backend so the record measures the backend change alone. The builder shapes only the candidate arm's prompt; improving the reference is a different experiment with its own comparison.
- **A general "provider registry" with dynamic plugin discovery.** Two legs in lane A, one enum, one `if` chain in `resolve`. Add a leg when it exists.
- **Making eligibility finer than the project.** §7 is per message's project. Per-message content classification of "private context" is a research question, not a routing rule.
- **Tuning the Ollama server** (`OLLAMA_NUM_PARALLEL`, `OLLAMA_KEEP_ALIVE`, quantization). The infra doc records the two settings the hot path depends on; the comparison runner measures under the server as configured. Server tuning past that is #3420's territory.
- **Jev `score`, `not_for`, `inspect`, JSON-object `state`.** Unexercised by the probe; out of scope.
- **Emoji choice as a structured decision, and the embedding path under it.** No decision model on `main` today, no ground truth, a new network call on every message; the live `find_best_emoji` embedding lookup and its two callers are that issue's to redesign. Filed separately (No-Gos, #3422).
- **Replacing `is_open_source`'s `gh` shell-out with a projects.json field.** Would change the source of truth for §7 across the improvement tooling. The `valor` pin plus the cache peek is enough here.
- **Rewriting `tools/improvement_eval/` to accept a classifier envelope.** Spike-3 says no; the standalone runner is 200 lines.

## Risks

### Risk 1: Hot-path regressions from migrating promise gate, read-the-room, and the completion judge
**Impact:** Every outbound message crosses at least two of these; a regression drops or delays replies, and a coroutine-level timeout around the API call leaks httpx connections (hotfix #1055).
**Mitigation:** These three migrate last among the raw sites, each in its own commit, each calling `run_typed` with `hard_timeout=None`, `sdk_timeout=RTR_SDK_TIMEOUT`, `slot_timeout=RTR_SDK_TIMEOUT`, `max_retries=0`, each with a slot-starvation test proving the leg raises without entering the client, and each behind the byte-identical fail-safe. The enumeration test's check 6 asserts no `asyncio.wait_for` inside the three functions (`_evaluate_promise_async`, `read_the_room`, `_judge_completion_novelty`) or in any function in `agent/llm/backends/`, and that every `run_typed` call in those bodies passes `hard_timeout=None`, so the wrapper's 35 s default cannot be inherited by a dropped kwarg. The PR review checks the p95 of `read_the_room` on the local bridge before and after via `tests/unit/test_read_the_room.py`'s timing fixture.

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
**Impact:** Ollama runs one request at a time by default and unloads granite after five idle minutes; a burst of messages, or the first message after a quiet spell, times out the local leg on C8, C9, or C10 and the fallback has too little budget left, so the caller sees its fail-safe where Haiku answered today. On the sites that pass no `sdk_timeout` (C1 to C4, C6, C7, C11 to C14) the same hang costs the 20 s Ollama timer before the fallback fires.
**Mitigation:** The fallback budget is the caller's (`sdk_timeout`, else `hard_timeout`), so a 20 s granite timeout on a routing classifier still leaves a 15 s Haiku attempt under the 35 s cap (Data Flow step 6) and the `high`-tier sites answer from Haiku rather than their fail-safe; the comparison runner measures p95 at concurrency 4 and the bar requires it within budget; the infra doc pins `OLLAMA_KEEP_ALIVE=-1` and `OLLAMA_NUM_PARALLEL=4` for the bridge machine and `tools/doctor` reports the loaded model and its keep-alive; a budgeted site whose p95 misses lands on `ANTHROPIC` by the bar.

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
**Data prerequisite:** `start = monotonic()` is captured before the primary call and the budget is the caller's (`sdk_timeout`, else `hard_timeout`).
**State prerequisite:** The fallback's slot wait is bounded by the remaining budget, never by the caller's original `slot_timeout` alone, and a `None` `slot_timeout` (the default on every non-hot site) bounds the slot wait to the fallback's SDK timer.
**Mitigation:** The wrapper passes `sdk_timeout=fb_timeout` and `slot_timeout=fb_slot` per Data Flow step 6 (`fb_slot = fb_timeout if slot_timeout is None else min(slot_timeout, fb_timeout)`, so no `min(None, ...)` `TypeError`) and skips the fallback under 0.5 s of budget; a slot timeout raises before the client is constructed; a test saturates the semaphore on a `sdk_timeout=3.0` call and asserts the fallback raises `LLMCallError(reason="slot_timeout")` inside the budget.

### Race 2: Eligibility refresh storms on a cold cache
**Location:** `tools/improvement_eligibility.py::is_eligible` / `_schedule_refresh`
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
- [SEPARATE-SLUG #3422] Emoji reaction choice as a structured-decision site (72-way `choice` over `EMOJI_LABELS`). Today's message-reaction path makes no model call; adding one is a new capability with no ground truth, and Tom's ordering says local first. The live embedding lookup `find_best_emoji` (callers `agent/constants.py:190-192` for the terminal reactions, `tools/react_with_emoji.py:94-96,171-173` for the reaction CLI; tests `tests/unit/test_reaction_never_hostile.py`, `tests/unit/test_stall_detection.py:831`) is that issue's to keep or replace; lane A touches none of `tools/emoji_embedding.py`.
- [EXTERNAL] Any change to the RSI charter's §7 boundary or to the $10/day unit-2 budget. Tom-owned.
- [EXTERNAL] Choosing a second structured-decision provider when one appears. The leg protocol admits it; the choice is a research investigation on the case.

Anti-criteria for the code-level No-Gos are in Verification: `tools/emoji_embedding.py` is untouched, no per-site backend switch exists in settings, and no raw Anthropic, Ollama, or OpenRouter client remains in the migrated modules.

## Update System

- `pyproject.toml`: lane A adds no dependency. #3420 adds the optional extra `classification-local`; `/update`'s `uv sync` does not install extras by default, so the local zero-shot leg raises `LLMCallError("classification-local extra not installed")` and the router falls back on machines without it. `tools/doctor` reports the extra's presence.
- Ollama on every machine that runs the bridge, the worker, or the reflections jobs must serve `granite4.1:3b` (already required by C12 and C13 on `main`; C10 and C11 run in the worker process and C14 in the reflections job, so a worker-only machine that lands them on `OLLAMA` without a daemon pays a connection-refused fallback to Anthropic on every call) and should run with `OLLAMA_KEEP_ALIVE=-1` and `OLLAMA_NUM_PARALLEL=4` (Risk 5). These are launchd environment settings on the Ollama service, outside this repo; `docs/infra/llm-task-routing.md` records them and `tools/doctor` reports the loaded model and keep-alive, and flags any declared `OLLAMA` site whose process runs on this machine with no reachable Ollama daemon, so a machine without them is visible after `/update`. No update-script change.
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
- [ ] Update `docs/features/nonharness-llm-wrapper.md`: the `run_typed` signature (including the retained `_skip_guard` and its one caller), the leg protocol, the degraded-stack guard by route axis and the fallback budget, the hotfix #1055 invariant as the legs now carry it (`slot_timeout`, SDK timers, `hard_timeout` outside the leg), the "Migrated Call Sites" table extended to every site (read-the-room no longer "skipped"; C5, C9, C10, C11, C14, C15 added), `run_typed_local` removed, and a pointer to the taxonomy page.
- [ ] Update `docs/features/local-model-policy.md`: granite is the declared backend for the classification sites that cleared the bar and the fallback rule; the classifier/generation constant split now lives in the taxonomy.
- [ ] Update `docs/features/config-timeout-catalog.md`: `TIMEOUTS__LOCAL_TYPED_HARD_S` is the Ollama leg's SDK-level timer.
- [ ] Add a row to `docs/features/README.md` for the taxonomy page.
- [ ] Create `docs/infra/llm-task-routing.md`: Ollama service requirements on every machine that runs the bridge, the worker, or the reflections jobs (`granite4.1:3b` pulled, `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_NUM_PARALLEL=4`, how to set them on launchd), the comparison runner's reference-arm spend (Haiku and gemma, metered purposes), and the rollback (set a site's `backend` back and restart). #3421 adds the decisions endpoint section.

### Inline Documentation
- [ ] Module docstrings for `agent/llm/tasks.py`, `router.py`, `backends/__init__.py` stating the protocol and the fail-closed rule.
- [ ] Each `LLMTask` declaration carries a one-line comment naming the fail-safe the caller applies.

## Success Criteria

- [ ] Every non-harness LLM call site in `agent/ bridge/ worker/ tools/ reflections/ scripts/` declares an `LLMTask`; `tests/unit/test_llm_task_taxonomy.py` passes and fails when a `task=` kwarg is removed from any one site (mutation-checked in review).
- [ ] Every existing unit test in the "no change expected" list passes unchanged; `resolve()` returns the Anthropic route for every thinking and `client_only` site and for every `OLLAMA` site with a client key or `None`, and the Ollama route with an Anthropic fallback for every `OLLAMA` site with key `valor` (table-driven test over all declarations, `gh` monkeypatched unavailable).
- [ ] C5, C9, C10, C11, C14, C15, and read-the-room go through `run_typed`; `grep` finds no `AsyncAnthropic(`, `anthropic_slot(`, `ollama.chat(`, or `requests.post(` in those seven modules; the enumeration test's check 6 finds no `asyncio.wait_for` inside the LLM call-path function bodies it names; `run_typed_local` no longer exists.
- [ ] Every classification site except C16 declares `backend=OLLAMA` or `backend=ANTHROPIC` with a `classifier_comparison` record on case `1ec40086` whose id appears in the site's row of the taxonomy table; every `OLLAMA` landing clears its tier bar in that record, and every `ANTHROPIC` landing's record names the failing criterion (C15 included: its reference arm is gemma via OpenRouter, and an `ANTHROPIC` C15 carries the record saying why granite missed). C12, C13, C14 carry latency-only records.
- [ ] `email_cs.triage` and `tools/email_cs/agents.py` resolve to the Anthropic leg for every project key.
- [ ] `python -m agent.llm.compat --json --allow-network` still runs its probe through `run_typed` with `task=NETWORK_PROBE, _skip_guard=True`; `tests/unit/test_llm_stack_compat.py` and `tests/unit/test_llm_stack_degraded_start.py` pass, the latter with its two local-leg tests rewritten onto `run_typed(task=..., backend=OLLAMA, project_key="valor")`.
- [ ] `tools/emoji_embedding.py` and its callers are untouched by the lane-A diff (`git diff main --stat -- tools/emoji_embedding.py agent/constants.py tools/react_with_emoji.py` is empty).
- [ ] `tools/doctor` shows the routing section with per-process eligibility cache state and flags declared `OLLAMA` sites with no reachable daemon on this machine.
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
  - Role: tag the `run_typed` sites (the compat probe included), migrate the six raw classification sites and read-the-room, thread `project_key`, run the per-site comparison loop and set each site's `backend`
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
- `agent/llm/backends/__init__.py::default_sdk_timeout(backend) -> float`: `ANTHROPIC` → `settings.timeouts.anthropic_sdk_s`, `OLLAMA` → `settings.timeouts.local_typed_hard_s`, read at call time so an env bump takes effect without a reload. Both legs take `sdk_timeout` as a required float; the wrapper resolves it (Task 2). `DEFAULT_SDK_TIMEOUT` is deleted from `wrapper.py`; `DEFAULT_HARD_TIMEOUT` stays. `agent/llm/__init__.py` (`:13,17,22,25`) drops `DEFAULT_SDK_TIMEOUT` and `run_typed_local` from its imports and `__all__` in the same commit, and the `tests/unit/test_settings.py:40` docstring is reworded to name `anthropic_sdk_s` as the Anthropic leg's timer.
- `_guard_stack` keeps its two axes behind the one entry point: the wrapper calls it after `resolve()` with `signature_axis=(route.backend is Backend.ANTHROPIC)` (Task 2), so `agent/llm/wrapper.py:112-114`'s reason ("an Anthropic create-signature break must not fall the two hot-path classifiers over") still holds for every `OLLAMA`-routed site. `tests/unit/test_llm_stack_degraded_start.py` `:447` and `:490` are rewritten onto `run_typed(task=..., backend=OLLAMA, project_key="valor")` as the Test Impact row specifies, in the commit that deletes `run_typed_local`.
- Slot-starvation and cancellation tests for the Anthropic leg; the four-reason tests for the Ollama leg.

### 2. Router, eligibility, wrapper, doctor
- **Task ID**: build-router
- **Depends On**: build-taxonomy
- **Validates**: `tests/unit/test_llm_router.py` (create), `tests/unit/test_llm_router_eligibility.py` (create), `tests/unit/test_improvement_eligibility.py` (extend), `tests/unit/test_worker_startup_warm_cache.py` (create), `tests/unit/test_llm_wrapper.py` (per-backend `sdk_timeout` table, fallback budget on a faked clock, `_skip_guard` single-call-site pin at `:516`), `tests/unit/test_llm_stack_compat.py` (the `allow_network=True` purity test at `:434-452` and the success-path probe at `:347`, with `_load_stack` faked), `tests/unit/test_llm_stack_degraded_start.py`, `tests/unit/test_doctor.py::test_llm_routing_section_cold_client_key_sync` (create)
- **Informed By**: spike-4
- **Assigned To**: taxonomy-builder
- **Agent Type**: builder
- **Parallel**: false
- `agent/llm/router.py::resolve(task, project_key) -> Route(backend, model, fallback=None)` with the four rules in Data Flow step 4.
- `tools/improvement_eligibility.py`: `is_eligible(project_key)` with `if project_key == "valor": return True` ahead of the cache read (the blocking `is_open_source` gets the same pin); `peek_open_source(project_key) -> bool | None` is a pure cache read that never schedules anything; `_schedule_refresh(project_key)` does `try: loop = asyncio.get_running_loop() except RuntimeError: return False` first, then, under a new module-level `threading.Lock` next to `_CACHE`, adds the key to `_REFRESHING` and runs `is_open_source` via `loop.run_in_executor` with a done-callback that discards the key (Race 2 test); `is_eligible` calls `_schedule_refresh` on a miss and returns `False` either way; `warm_cache(keys)` calls the blocking `is_open_source` per key. Bridge warms `ACTIVE_PROJECTS`; `worker/__main__.py::_run_worker` warms `list(projects)`.
- `run_typed(prompt, output_type, *, task, project_key=None, model=MODEL_FAST, system=None, sdk_timeout=None, slot_timeout=None, hard_timeout=DEFAULT_HARD_TIMEOUT, max_retries=None, _skip_guard=False)`, in this order: the empty-prompt `ValueError`; the `task` `TypeError` naming the kwarg (Risk 2); `route = resolve(task, project_key)`; unless `_skip_guard`, `_guard_stack("run_typed", signature_axis=(route.backend is Backend.ANTHROPIC))`; `effective = sdk_timeout if sdk_timeout is not None else default_sdk_timeout(route.backend)`; `budget = sdk_timeout if sdk_timeout is not None else hard_timeout`; `start = monotonic()`; primary leg with `sdk_timeout=effective`; on `LLMCallError` with `route.fallback`, the budget math of Data Flow step 6 (`fb_timeout`, `fb_slot`, skip under 0.5 s of remaining budget), then unless `_skip_guard`, `_guard_stack("run_typed:fallback", signature_axis=True)` immediately before an Anthropic fallback leg, then the fallback once; `hard_timeout` applied outside the legs only when not `None`. `_skip_guard=True` skips both guard calls and nothing else, so `agent/llm/compat.py:525` keeps the purity its docstring (`:490-498`) promises. Table-driven test in `tests/unit/test_llm_wrapper.py`: with both legs faked, a C12-shaped call (`backend=OLLAMA`, key `valor`, no `sdk_timeout`) reaches the Ollama fake with `sdk_timeout == 20.0`; a C1-shaped call routed to Anthropic (`backend=ANTHROPIC`, or `OLLAMA` with a client key) reaches the Anthropic fake with `30.0`; a C8-shaped call with `sdk_timeout=3.0` reaches either fake with `3.0`; the settings values are read from `settings.timeouts` in the test, never hard-coded twice. Fallback-budget test on a faked monotonic clock: a C1-shaped call (`backend=OLLAMA`, key `valor`, no `sdk_timeout`, default `hard_timeout`) whose Ollama fake raises `LLMCallError(reason="timeout")` after advancing the clock 20 s reaches the Anthropic fake exactly once with `sdk_timeout == 15.0` and `slot_timeout == 15.0`; the same call with `slot_timeout=None` raises no `TypeError`.
- `tools/doctor` "LLM routing" section: every declared site with kind, backend, the route for `valor` and for a client key, and the per-process eligibility cache state; plus the Ollama loaded model and keep-alive (Risk 5), and a flag on any declared `OLLAMA` site when no Ollama daemon answers on this machine (the worker-only and reflections-only machines from Update System). Doctor stays synchronous: `resolve` and `is_eligible` are plain functions, and the refresh scheduler behind `peek_open_source` degrades to a no-op without a running loop (below), so the check calls `resolve` directly with no `asyncio.run` and reports a cold client key as `miss (no loop; refresh not scheduled)`. `tests/unit/test_doctor.py::test_llm_routing_section_cold_client_key_sync` invokes the check function synchronously, the way `run_checks` does, with the cache cleared and `gh` monkeypatched unavailable, and asserts that line and no exception.

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
- Declare `LLMTask` constants at C1, C2, C3, C4, C6, C7, C8, C12, C13, C16 (C16 `client_only=True`, `backend=ANTHROPIC`; C12 and C13 `backend=OLLAMA`; the rest `backend=ANTHROPIC` at this step, changed per site in Task 7) and at every thinking site on `run_typed`: `agent/memory_extraction.py:371`, `tools/memory_eval/query_set.py:117,190`, and `agent/llm/compat.py:525`, which declares `NETWORK_PROBE = LLMTask(site="compat.network_probe", kind=THINKING, backend=ANTHROPIC)` and calls `run_typed("Reply with answer=hi", _Probe, task=NETWORK_PROBE, _skip_guard=True)` (the auto-bump `llm` gate's live probe; `THINKING` short-circuits at rule 1, `_skip_guard` keeps the probe pure); that is the complete `run_typed` caller list on `main` (12 calls) plus the two `run_typed_local` calls (C12, C13). C7's `risk` becomes a `Literal`; C12 and C13 call `run_typed`.
- Add `project_key` keywords and pass `project["_key"]` from `should_respond_async`; C6 from the chat's project; C8 from the outbound chat.
- Module-level `LLMTask(kind=THINKING, backend=ANTHROPIC)` declarations in every raw-transport module the census found on `main` that is not migrated in Tasks 5 and 6 and not on the allowlist: `bridge/media.py`, `tools/image_analysis/__init__.py`, `tools/image_tagging/__init__.py`, `tools/test_judge/__init__.py`, `reflections/pm_briefings/builder.py`, `reflections/utilities.py`, `scripts/memory_consolidation.py`, `scripts/evaluate_build.py`, `tools/knowledge/indexer.py`, `tools/knowledge/converter.py`, `tools/valor_calendar.py`, `tools/doc_summary/__init__.py`, `tools/documentation/__init__.py`, `tools/cross_vendor_judge.py`, `tools/impact_finder_core.py` (its `messages.create(` at `:395`), `tools/improvement_eval/arm_worker.py`, `tools/improvement_eval/judges/serves_charter.py`, and `tools/email_cs/agents.py` with `client_only=True`. `tools/image_gen/__init__.py` goes on the allowlist with its reason; `tools/transcribe/__init__.py` and `tools/link_analysis/__init__.py` match no token once `audio.transcriptions` and `embeddings.create(` are out of the list. Re-run both censuses at the head this task builds on and reconcile any new hit before Task 8: the raw-transport census (`/usr/bin/grep -rln "messages.create(\|ollama.chat(\|chat.completions.create(\|OPENROUTER_URL" --include='*.py' agent bridge worker tools reflections scripts`, 30 modules on `main`) and the wrapper caller census (`/usr/bin/grep -rn "run_typed\(_local\)\?(" --include='*.py' agent bridge worker tools reflections scripts | grep -v "^agent/llm/wrapper.py"`, 14 calls on `main` at `ed3f8a9c3`: C1 to C4, C6, C7, C8, C12, C13, C16, `agent/memory_extraction.py:371`, `tools/memory_eval/query_set.py:117,190`, `agent/llm/compat.py:525`). Every hit of the second must carry `task=` or be `agent/llm/wrapper.py` itself.

### 5. Migrate the six raw classification sites
- **Task ID**: build-migrate-raw
- **Depends On**: build-tag-sites
- **Validates**: `tests/unit/test_pr_classification_fastpath.py`, `tests/unit/test_work_request_classifier.py`, `tests/tools/test_classifier.py` (delete or rewrite), `test_session_completion.py`, `test_session_completion_zombie.py`, `test_health_check.py`, `test_memory_quality.py`, `test_improvement_evidence.py`, `test_promise_gate*.py`, `tests/helpers/llm_fakes.py` (create)
- **Assigned To**: sites-builder
- **Agent Type**: builder
- **Parallel**: false
- Order: C5, C11, C14, C15, C10, then C9 (hot path last). One commit per site. The migration commit changes transport only for C5, C9, C10, and C11 (each stays on Haiku, `backend=ANTHROPIC`) and for C14 (stays on granite, `backend=OLLAMA`). C15 is the exception: its `main` backend is gemma via OpenRouter, which no lane-A leg carries, so its migration commit changes backend too, landing it on `backend=ANTHROPIC` (Haiku) while deleting the OpenRouter transport. Each site keeps its fail-safe value and its log line; each test asserts both under `LLMCallError`.
- C5: `WorkTypeDecision` model; delete sync `classify_request` (`tools/classifier.py:90`), the two module-docstring examples that call it (`:16`, `:19`), and the `anthropic` import; `classify_request_async` returns the same dict; `tests/unit/test_work_request_classifier.py:174-176` moves to the `run_typed` fake and `tests/tools/test_classifier.py` is deleted or rewritten against the async function.
- C14: `async def _gemma_classify(content) -> MemoryAuditDecision | None` awaiting `run_typed(GEMMA_AUDIT_PROMPT.format(content=content[:1000]), MemoryAuditDecision, task=MEMORY_AUDIT, project_key=<the memory row's project>, sdk_timeout=GEMMA_CALL_TIMEOUT_SEC, hard_timeout=None)`, called directly from `_layer3_classify`; delete the `ollama.chat(...)` call, the `import ollama`, the `ThreadPoolExecutor`, the `run_in_executor` dispatch, and the `asyncio.wait_for` at `:513`; `_layer3_classify` reads `verdict.is_junk`, `verdict.anomaly_signal`, `verdict.why` as attributes; the wallclock deadline check and the two subprocess `asyncio.wait_for` calls (`:617`, `:696`) are unchanged. Fail-safe: `None` on `LLMCallError`, so the `unavailable_count` accounting holds.
- C15: `PromiseJudgeDecision` on `run_typed(task=PROMISE_JUDGE)` with `backend=ANTHROPIC`; `_OpenRouterJudge`, the `requests` call, and the `promise_detector` reserve/settle deleted in the same commit. Gemma via OpenRouter remains reachable only from the comparison runner's reference arm (Task 3), metered under `promise_detector`.
- C10 and C9: `run_typed(..., sdk_timeout=RTR_SDK_TIMEOUT, slot_timeout=RTR_SDK_TIMEOUT, max_retries=0, hard_timeout=None)`; C9's slot timeout and SDK timeout each still route to the heuristic with source `timeout`; the #1055 docstring text in both modules now points at the leg.

### 6. Migrate read-the-room
- **Task ID**: build-migrate-rtr
- **Depends On**: build-migrate-raw
- **Validates**: `tests/unit/test_read_the_room.py`
- **Assigned To**: sites-builder
- **Agent Type**: builder
- **Parallel**: false
- `RoomVerdict` becomes a `BaseModel` with `action: Literal["send", "trim", "suppress"]`; `read_the_room` calls `run_typed(task=READ_THE_ROOM, system=READ_THE_ROOM_SYSTEM_PROMPT, project_key=..., sdk_timeout=RTR_SDK_TIMEOUT, slot_timeout=RTR_SDK_TIMEOUT, max_retries=0, hard_timeout=None)`; `trim` without `revised_text` still becomes `send`; every error path still yields `send`/`rtr_error`; the module docstring's #1055 paragraph points at the leg. `tools/emoji_embedding.py` and its callers are not touched in lane A (#3422).

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
- Implement the six checks from Key Elements with the narrowed token list and the allowlist. Three red-state proofs, all pasted into the PR: seed a fake untagged call in a temp module to prove check 1 bites; seed an `asyncio.wait_for` inside a copy of `_judge_completion_novelty` to prove check 6's `wait_for` scan bites; delete `hard_timeout=None` from a copy of `_evaluate_promise_async` to prove check 6's `hard_timeout` assertion bites (this is the mutation a builder writes by default, so it is the one that matters). Check 6 walks function bodies by name (`_evaluate_promise_async`, `read_the_room`, `_judge_completion_novelty`, `_gemma_classify`) plus every function in `agent/llm/backends/`, scanning each for `asyncio.wait_for` calls and each of the four named bodies for `run_typed` calls lacking `hard_timeout=Constant(None)`; it must stay green against the unrelated `asyncio.wait_for` at `agent/session_completion.py:1229`, the subprocess bounds in `memory_quality_audit.py`, and every thinking site that keeps the wrapper's default `hard_timeout`. Both censuses from Task 4 are re-run at this head first.

### 9. Validate lane A
- **Task ID**: validate-lane-a
- **Depends On**: build-enumeration-test
- **Validates**: the "no change expected" list, `scripts/pytest-clean.sh tests/unit/`, the mutation checks (one per migrated fail-safe plus the enumeration test), and every Verification row except the three doc rows and the follow-up-issues row
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the "no change expected" list, then `scripts/pytest-clean.sh tests/unit/`; mutation-check each migrated fail-safe and the enumeration test, including one mutation per named hot-path body that deletes its `hard_timeout=None` kwarg (check 6 must go red each time) and one that removes `task=` from `agent/llm/compat.py:525` (check 1 must go red); re-read every comparison record against the bar and flag any `OLLAMA` landing whose record misses; confirm the Verification table.

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
| Hotfix #1055: no coroutine-level timeout inside the LLM call-path functions, and every `run_typed` call in the four named bodies passes `hard_timeout=None` (AST by function name; the unrelated `wait_for` at `agent/session_completion.py:1229` and the subprocess bounds in `memory_quality_audit.py` are outside the walk) | `scripts/pytest-clean.sh tests/unit/test_llm_task_taxonomy.py -q -k "hotfix_1055"` | exit code 0 |
| Raw Ollama dispatch gone from C14 | `/usr/bin/grep -c "run_in_executor\|ThreadPoolExecutor\|import ollama" reflections/memory/memory_quality_audit.py` | match count == 0 |
| Wrapper caller census reconciled: every `run_typed` call outside the wrapper carries `task=` | `scripts/pytest-clean.sh tests/unit/test_llm_task_taxonomy.py -q -k "task_kwarg" && /usr/bin/grep -rn "_skip_guard=True" --include='*.py' agent bridge worker tools reflections scripts \| grep -v "^agent/llm/wrapper.py" \| wc -l` | exit code 0 and count == 1 (`agent/llm/compat.py`) |
| Auto-bump `llm` gate probe still runs through the wrapper | `scripts/pytest-clean.sh tests/unit/test_llm_stack_compat.py tests/unit/test_llm_stack_degraded_start.py -q` | exit code 0 |
| Anti-criterion: emoji embedding path untouched (#3422 owns it) | `git diff main --stat -- tools/emoji_embedding.py agent/constants.py tools/react_with_emoji.py tests/unit/test_emoji_embedding.py \| wc -l` | 0 |
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

Round 4 (2026-09-19, FULL depth, independent roster of 3 critics, re-critique of revision 3 at `4fa0a1c35`). Verdict: READY TO BUILD (with concerns) (0 blockers, 8 concerns, 1 nit). All seven round-3 rows were verified closed in the plan text and on `main` by all three critics and by the driver (both censuses re-run: 14 wrapper callers, 30 raw-transport modules; `compat.py:525` probe and its purity docstring; `test_llm_wrapper.py:516` pin; `test_llm_stack_degraded_start.py:447,:490`; `agent/llm/__init__.py` re-exports; `OllamaProvider(openai_client=...)` on the pinned stack; six live `find_best_emoji` caller lines and #3422's body). Round 3's table is in git history at `4fa0a1c35`, round 2's at `9b6c39952`, round 1's at `95f7955bb`. No finding is a build-breaking defect; each concern below carries the implementation note the builder needs.

| Severity | Critic | Location | Finding | Addressed By | Implementation Note |
|----------|--------|----------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | Data Flow step 6; Task 2; Race 1 | The step-6 guarantee that the fallback's live request always finishes before the outer cap fails for two independent reasons. `fb_slot` and `fb_timeout` bound two sequential phases (the slot wait in `semaphore_slot` at `agent/anthropic_client.py:211`, then the request), each set to the full remainder, so the fallback can consume 2 x (budget - elapsed) and a C1-shaped call with a 6 s slot wait is cancelled mid-request by the 35 s cap. And `max_retries=None` on every non-hot site means the anthropic SDK retries timeouts twice by default (`DEFAULT_MAX_RETRIES == 2` on pinned 1.7.0; `APITimeoutError` subclasses `APIConnectionError`, which the base client retries), so `timeout=15` bounds one attempt, not the leg. The Task 2 faked-clock test asserts kwargs only, never wall time, so it stays green over both. | pending | `deadline = None if budget is None else start + budget`; the fallback passes `slot_timeout=fb_slot, max_retries=0, deadline=deadline`; the leg protocol gains an additive `deadline: float \| None = None`. In `backends/anthropic.py::call`, right after `semaphore_slot(timeout=slot_timeout)` enters: `remaining = deadline - monotonic()`; raise `LLMCallError(reason="timeout")` if `remaining < 0.5` before any client construction; else `AsyncAnthropic(timeout=min(sdk_timeout, remaining), max_retries=max_retries)`. Two more faked-clock tests: semaphore fake consumes 10 s of 15, assert the client `timeout <= 5.0` and `max_retries == 0`; consumes 14.8 s, assert `reason="timeout"` with the constructor never entered. Check 6 stays green (client timeout, never a coroutine cancellation). |
| CONCERN | Risk & Robustness | Risk 5; Documentation (infra doc rollback); Update System; Key Elements (Eligibility, `warm_cache`) | The only documented rollback is a code edit plus fleet `/update`, and Risk 5 models Ollama failure as a burst; the steady degraded state (daemon up but reloading, CPU-starved, or queue full) costs every `OLLAMA` site the full 20 s `local_typed_hard_s` (`config/settings.py:376`) before fallback on every call, and each classifier a message crosses (C1 via `should_respond_async` at `bridge/telegram_bridge.py:1770`, then C3, C13, C12) pays it in turn, with no operator lever named. Separately, `warm_cache` puts one blocking `gh repo view` (10 s timeout, `tools/improvement_eligibility.py:103-108`) per github-mapped key on the bridge and worker startup path, ahead of "Connected to Telegram". | pending | Infra doc rollback, three levers in order: (1) stop the Ollama service (launchd label or `brew services stop ollama`), expected signature one fallback warning per call with `reason=transport`; (2) `TIMEOUTS__LOCAL_TYPED_HARD_S=3` in the vault `.env` plus `valor-service.sh restart` (an existing catalog entry, inside Tom's no-switches rule); (3) the code rollback. Fixed-prefix fallback warning carrying `site`, `primary`, `fallback`, `reason`, `elapsed_ms`; doctor prints `local_typed_hard_s` beside the loaded model. Startup: `warm_cache(keys)` runs `is_open_source` per key via `loop.run_in_executor` under `asyncio.gather(..., return_exceptions=True)` as a `create_task` background task, never awaited before connect or before the worker loops; `test_worker_startup_warm_cache.py` asserts the task is scheduled with the loaded keys. |
| CONCERN | Scope & Value | Technical Approach (C15); Task 5 (C15 bullet); Test Impact | Deleting `_OpenRouterJudge` and `_judge_model` (`reflections/improvement_collect.py:790-795`) orphans `ImprovementSettings.cheap_inference_model` (`config/settings.py:743`; `improvement_collect.py:795` is its only production consumer), leaving a per-site backend switch for C15 as dead config with its `.env.example:378-384` block, two docs rows (`docs/features/improvement-research-cycle.md:638`, `docs/features/improvement-controller.md:246`), and tests at `tests/unit/test_settings.py:203,210` and `tests/unit/test_improvement_evidence.py:985-1002`. The plan's anti-criterion grep (`_sites\|structured_decision\|classification_local`) cannot see it. Same-commit gotcha: `collect_promises` (`:951-957`) is synchronous with a `Callable[[str], str]` transport while `run_typed` is a coroutine; the plan specifies the async shape for C14 and says nothing for C15. | pending | In the C15 commit delete the `cheap_inference_model` field, its `.env.example` block, the two docs rows, and the tests at `test_settings.py:203,210` and `test_improvement_evidence.py:985-1002`; reword the `promise_detector_enabled` description. State the C15 call shape: either `collect_promises` becomes `async def` with `transport: Callable[[str], Awaitable[PromiseJudgeDecision \| None]] \| None` and `run_improvement_collect` awaits it, or the sync collector wraps the judge in `asyncio.run(run_typed(...))` per candidate (only valid when no loop is running; assert with `asyncio.get_running_loop()` raising `RuntimeError`). Pick one and write it in Task 5 and the `test_improvement_evidence.py` Test Impact row. |
| CONCERN | Scope & Value | Task 7 (landing summary); Key Elements (Acceptance bar); Task 3 (`--audit`) | The Task 7 landing summary omits `contended` and the real-message input split, which Race 3 ("the reviewer's bar reads only uncontended latency") and Risk 7 (the real-message sample must reach the minimum) make part of the bar; the full record lives only in the builder machine's Redis, so the reviewer cannot apply two of the bar's criteria from the PR body. | pending | Add `contended` and `n_real` (with the site minimum) to the Task 7 summary columns and to the Key Elements bar criteria; have `python -m tools.classification_eval --audit` fail an `OLLAMA` landing whose record has `contended: true` or `n_real` below the site minimum, in the same exit-1 path as a missing record. |
| CONCERN | Scope & Value | Technical Approach (C15); Task 5; Task 7; Success Criteria | C15 is the one site whose `ANTHROPIC` landing is a new backend rather than its `main` backend, and it is never measured: Task 5 moves it from gemma to Haiku with no record, and Task 7's record compares granite against gemma. If granite misses, C15 ships on Haiku with no agreement number for Haiku. | pending | Run C15's comparison with both `anthropic` and `ollama` candidate arms against the gemma reference in one run (`compare(site, inputs, arms)` already reports per-arm agreement), so the landing row carries a number for whichever backend lands; `--candidate` accepts a repeated flag or a comma list for this one site. |
| CONCERN | History & Consistency | Technical Approach (Project key threading); Task 5 (C9); Task 6 | The plan threads `project_key` into C9 and read-the-room "from `chat_id` through `find_project_for_chat`", but `find_project_for_chat(chat_title: str \| None)` is title-keyed (`bridge/routing.py:382-392`), no chat_id-keyed resolver exists in `bridge/ agent/ tools/ worker/`, and `bridge/promise_gate.py` has no `chat_id` in scope at all (zero occurrences). Built as written, C9 gets `None` on every call and silently resolves to Anthropic in production regardless of its record and declared `backend`. | pending | Use the sources that exist: `session.project_key` in `read_the_room` (`bridge/read_the_room.py:447-450` takes `session`) and `AgentSession.get_by_id(session_id)` on the promise-gate path (the same real-session guard `_emit_session_event_if_real` uses at `bridge/promise_gate.py:615`), `None` for synthetic ids. `project_key = getattr(session, "project_key", None)`; add `project_key: str \| None = None` to `_evaluate_promise_async` (`:664`) and `_evaluate_promise_llm_or_heuristic` (`:743`), resolved once by each caller. `AgentSession.project_key` is a `KeyField()`. Add both paths to `tests/integration/test_bridge_routing_project_key.py` so `"valor"` reaches the fake from the real caller; otherwise the C9 `OLLAMA` landing is unverifiable in production. |
| CONCERN | History & Consistency | Key Elements (Backend legs); Task 1 (Ollama leg bullet); Architectural Impact | The Ollama leg constructs `AsyncOpenAI(...)`, but `LLMStack` has no `AsyncOpenAI` field (`agent/anthropic_client.py:77-89`; `_load_stack` at `:93-115`) and the plan never says where the symbol comes from; the #3001 lazy-import contract (`agent/llm/wrapper.py:37-48`) plus the plan's "fake `AsyncOpenAI`" tests push the builder to a module-scope `from openai import AsyncOpenAI` in `backends/ollama.py`, which `import agent.llm` then executes, and `tests/unit/test_llm_import_safety.py` cannot catch it (its shim covers only `anthropic` and `pydantic_ai`, `:54-60`). | pending | Add `AsyncOpenAI: Any` to `LLMStack`, import it inside `_load_stack`, and have the leg read `stack.AsyncOpenAI`: `stack = _load_stack(); client = stack.AsyncOpenAI(base_url=..., api_key="ollama", timeout=sdk_timeout, max_retries=0); provider = stack.OllamaProvider(openai_client=client)`. Existing fakes keep working since they use `dataclasses.replace(real, ...)` (`test_llm_stack_degraded_start.py:433-438`); the new leg tests fake with `dataclasses.replace(real, AsyncOpenAI=Fake)`. Extend the import-safety shim with a raising `openai.py` and assert `import agent.llm` and `import bridge.telegram_bridge` still succeed. Close the per-call client on exit (`async with client:`) as the Anthropic leg does; `AsyncOpenAI.__aenter__` exists on openai 3.8.0. |
| CONCERN | History & Consistency | Test Impact ("no change expected" row, `test_intake_classifier.py` row); Success Criteria 2; Verification ("`run_typed_local` deleted" row) | `tests/unit/test_context_recall.py` is listed "no change expected" and Success Criterion 2 requires it to pass unchanged, yet four C13 tests monkeypatch `"agent.llm.run_typed_local"` (`:296`, `:318`, `:344`, `:366`) and raise `AttributeError` once Task 1 drops the name; `tools/classifier.py:397` imports `run_typed_local` lazily inside the function, so "patch `run_typed` in `tools.classifier`" only holds if the import moves to module scope; and the Verification grep expects zero matching files while comments still carry `run_typed_local` (`agent/llm/compat.py:136`, `:168`, `:777`; `tests/unit/test_llm_stack_degraded_start.py:415`, `:608`; `test_intake_classifier.py:6`, `:9`; `test_job_router.py:11`), with `config/settings.py:382` (`local_typed_hard_s` description), `config/settings.py:351` and `agent/memory_extraction.py:52` (`DEFAULT_SDK_TIMEOUT`) going stale outside the grep. | pending | Move `test_context_recall.py` to an UPDATE row (the four fakes already accept `**kwargs`; only the patch target changes). Pick one seam and write it in both the intake and context-recall rows: a module-scope import in `tools/classifier.py` (patch `tools.classifier.run_typed`) or keep the lazy import (patch `agent.llm.run_typed`). List the comment sites plus the two settings descriptions and the `memory_extraction.py:52` comment for rewording in Task 1 so the Verification grep reaches 0. Docs naming `run_typed_local` (`llm-stack-compat-gate.md`, `durability-model.md`, `intake-classifier.md`, `README.md`) belong in the Documentation cascade. |
| NIT | Risk & Robustness | Data Flow step 5; Task 1 (Ollama leg bullet) | The plan says both legs honor #1055 "the same way", but only the Anthropic leg keeps `async with` around its client; the Ollama leg builds `OllamaProvider(openai_client=AsyncOpenAI(...))` per call with no close, so each call leaves an unclosed httpx client and an outer `hard_timeout` cancellation lands on a live localhost request with no cleanup. Status quo on main (`agent/llm/wrapper.py:295`), so no regression. | pending | `async with stack.AsyncOpenAI(base_url=..., api_key="ollama", timeout=sdk_timeout, max_retries=0) as client:` then `OllamaProvider(openai_client=client)` inside the block. |
