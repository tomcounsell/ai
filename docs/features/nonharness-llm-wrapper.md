# Non-Harness LLM Wrapper

A single PydanticAI-based call point for every LLM call that is not a `claude -p` harness session. Classification, extraction, and judgment calls declare a typed output model and an `LLMTask`, and get a schema-validated result back from whichever backend leg the router picks. The site declarations, the router rules, eligibility, and the acceptance bar are in [LLM Task Taxonomy](llm-task-taxonomy.md); this page is the transport underneath them.

## Two Ways to Call an LLM

The system makes two categories of LLM calls, and they stay deliberately separate:

- **Harness calls** are Claude Code sessions (skills, hooks, tools, resume) driven through `claude -p` via the headless session runner (`agent/session_runner/`). See [Headless Session Runner](headless-session-runner.md).
- **Non-harness calls** classify, extract, judge, or refine text outside a harness session: routing decisions, memory extraction, the promise gate, read-the-room, catch-up judging, email triage, intent classification.

`agent/llm/` is the one call point for the non-harness half. Every site goes through it, so the provider client, the timers, the semaphore, the routing, and the fallback are written once. The harness transport is untouched.

## The Wrapper

`agent/llm/wrapper.py` exposes one async function:

```python
from agent.llm import run_typed, LLMCallError, LLMTask

async def run_typed(
    prompt: str,
    output_type: type[BaseModel],
    *,
    task: LLMTask,                        # required: the call site's declaration
    project_key: str | None = None,       # the router's eligibility input
    model: str = MODEL_FAST,              # the Anthropic model on every Anthropic route
    system: str | None = None,            # system prompt for the PydanticAI Agent
    sdk_timeout: float | None = None,     # None: the leg's default from settings.timeouts
    slot_timeout: float | None = None,    # bounds the Anthropic semaphore wait
    hard_timeout: float | None = DEFAULT_HARD_TIMEOUT,  # 35.0 s outer cap, outside the leg
    max_retries: int | None = None,       # SDK retry count on the primary Anthropic client
    _skip_guard: bool = False,            # internal (#3001), compat gate only
) -> BaseModel:
    ...
```

Call it with a prompt, a `pydantic.BaseModel` subclass describing the desired output shape, and the site's `LLMTask`. PydanticAI validates the model's response against the schema and auto-retries once on mismatch. A validated instance of `output_type` comes back, or the call raises `LLMCallError`. A call without `task=` raises `TypeError` naming the kwarg, so a site the taxonomy missed fails loudly.

`model` defaults to `config.models.MODEL_FAST` (Haiku) and names the model on every Anthropic route, the fallback included; the Ollama leg always runs `config.models.OLLAMA_CLASSIFIER_MODEL` (`granite4.1:3b`); the local encoder leg runs the head named by `task.site`.

`sdk_timeout` is the leg's SDK-level request timer. `None` means the leg's default, read from `settings.timeouts` at call time: `anthropic_sdk_s` (30 s) for the Anthropic leg, `local_typed_hard_s` (20 s) for the Ollama and local encoder legs, through `agent.llm.backends.default_sdk_timeout(backend)`. An explicit value always wins on whichever leg the router picks, and it is also the fallback budget. On the encoder leg, which makes no request, the value only bounds the fallback's deadline arithmetic. The wrapper owns no SDK timeout constant of its own. `DEFAULT_HARD_TIMEOUT` is `settings.timeouts.anthropic_hard_s` (35 s). See [Config Timeout Catalog](config-timeout-catalog.md).

`_skip_guard` is internal: it skips both `_guard_stack` calls (primary and fallback) and nothing else, so the call never reaches `stack_axes()` -> `resolve_degraded_flag()`. Its one caller is `agent/llm/compat.py::_check_network`, the auto-bump `llm` gate's live probe, which declares `NETWORK_PROBE = LLMTask(site="compat.network_probe", kind=THINKING, backend=ANTHROPIC)` and must stay pure (never touch the memoized degraded flag) while still getting the shared `semaphore_slot()` and both timers. `tests/unit/test_llm_wrapper.py` pins that it is the only caller. See [LLM Stack Compat Gate](llm-stack-compat-gate.md).

### What the wrapper does, in order

1. Validates the prompt. An empty, `None`, or whitespace-only `prompt` raises `ValueError` before any routing or network work.
2. Resolves the route: `agent/llm/router.py::resolve(task, project_key, model=model)` returns `Route(backend, model, fallback)` by the six rules in the [taxonomy page](llm-task-taxonomy.md#router-rules).
3. Runs the degraded-stack guard with the axis the route needs (below).
4. Loads the stack through `agent/anthropic_client.py::_load_stack` and hands it to the leg.
5. Runs the primary leg. On `LLMCallError` with a `fallback` on the route, runs the fallback leg once inside the caller's remaining budget (below).
6. Logs `llm_route` after whichever leg answered and returns the validated instance. The result carries no marker of which leg answered; the log line does, and it carries the result's `confidence` when the output type has one.

When `hard_timeout` is not `None`, steps 5 and 6 run under one `asyncio.wait_for(..., timeout=hard_timeout)`: the one coroutine-level cap, wrapping the legs from outside and never the live request.

## The Backend Legs

`agent/llm/backends/` holds one leg per `Backend`, each a module exposing:

```python
async def call(
    prompt, output_type, route, *,
    system, sdk_timeout, slot_timeout, max_retries, deadline=None, stack,
) -> BaseModel
```

The leg protocol:

- A leg returns a validated instance of `output_type` or raises `LLMCallError` with a `reason` in `timeout`, `slot_timeout`, `transport`, `validation`. It never invents a default; the call site's fail-safe does that.
- `sdk_timeout` is a required float and the only timer around the live request: an SDK-level client timeout, never a coroutine-level `asyncio.wait_for`. The wrapper applies `hard_timeout` outside the leg, and only when the caller passes one. A leg that makes no request (the local encoder) has no request timer at all and uses the value for the deadline check only.
- `slot_timeout` bounds the wait for the shared Anthropic semaphore; a leg with no semaphore accepts and ignores it. `max_retries` is the SDK retry count, `None` meaning the SDK default; the Ollama leg pins 0 and the encoder leg has no SDK.
- `deadline` is an absolute `time.monotonic()` instant the wrapper passes on the fallback leg only. A leg re-checks it once, after any queue wait and before constructing its client: under `MIN_REMAINDER_S` (0.5 s) of remainder raises `LLMCallError(reason="timeout")` with no client ever built; otherwise the request timer shrinks to the remainder. `None` means no check.
- `stack` is the `LLMStack` the wrapper resolved through its `_load_stack` seam. A leg takes every third-party symbol it needs from it and imports none at module scope (#3001), so `dataclasses.replace(real_stack, <Symbol>=Fake)` on that seam is the network-isolation point for the Anthropic and Ollama legs. The encoder leg takes nothing from the stack; its seam is its own `_load_runtime`.
- `CancelledError` (the wrapper's `hard_timeout`, a caller going away) is never caught: the `async with` blocks release the slot and close the client on the way out.

### The Anthropic leg (`backends/anthropic.py`)

The hotfix #1055 / #1111 invariant lives here: `agent/anthropic_client.py` holds no long-lived shared client, `semaphore_slot()` only gates concurrency, and the leg builds its own client per call.

1. `async with semaphore_slot(timeout=slot_timeout):` holds the shared slot for the entire `Agent.run()` call. A slot wait that outlives `slot_timeout` raises `LLMCallError(reason="slot_timeout")` before any client exists.
2. Inside the slot, the deadline re-check (fallback calls only).
3. A fresh `async with stack.anthropic.AsyncAnthropic(api_key=..., timeout=sdk_timeout, max_retries=...)`: the SDK-level timer is the only timer around the live request, and `async with` preserves the httpx cleanup. `max_retries=None` leaves the SDK default (2 on anthropic 1.7.0); the fallback leg passes 0 so one timer bounds one attempt.
4. That client is injected into PydanticAI: `AnthropicProvider(anthropic_client=client)` -> `AnthropicModel(route.model, provider=...)` -> `Agent(model, output_type=output_type, system_prompt=system)`.
5. The slot releases on `__aexit__`.

The leg publishes how long it waited for the slot through `slot_wait_ms`, a `ContextVar` set the moment the slot is held, so a caller that audits queue time (`bridge/promise_gate.py`'s `queue_wait_ms` audit column) resets it before `run_typed` and reads it after. It stays at whatever the caller set when the slot was never acquired.

### The Ollama leg (`backends/ollama.py`)

Runs against the local daemon's OpenAI-compatible surface (`settings.models.ollama_host` + `/v1`) on `route.model`. It differs from the Anthropic leg in three deliberate ways: no Anthropic client and no shared Anthropic semaphore (the call never leaves this machine, so `slot_timeout` is accepted and unused); a per-call `async with stack.AsyncOpenAI(base_url=..., api_key="ollama", timeout=sdk_timeout, max_retries=0)` client with `stack.OllamaProvider(openai_client=client)` inside the block, so no call leaves an unclosed httpx client; and native JSON-schema output (`stack.NativeOutput(output_type)`, Ollama's `response_format`), because PydanticAI's tool-mode default fails validation on about a third of granite calls through llama-server, which emits the tool call as message content. The deadline re-check runs once before the client is built.

### The local encoder leg (`backends/local_encoder.py`)

A supervised local classifier with no request: a pinned sentence-embedding model (`Xenova/bge-small-en-v1.5` int8 ONNX, served in-process on CPU through `onnxruntime`) and one committed linear head per landed site under `agent/llm/backends/heads/<site>.json`, named by `route.model` (the site id). `call` runs the deadline re-check first, loads the head (memoized per process), checks the output type's shape against the head's classes, and runs the embedding and the softmax inside `asyncio.to_thread`, returning `output_type(**{label_field: value, "confidence": score})` in a few milliseconds. `system` is accepted and ignored: the head was fit on the text alone, and the instruction block is carried for the Anthropic fallback. There is no request timer, because there is no request: the CPU work is bounded by the 512-token truncation window, and the leg honors the deadline re-check only (`sdk_timeout` feeds that arithmetic and nothing else). Every failure is an `LLMCallError` (`transport` for a missing extra, missing or mismatched weights, a missing head, or an ONNX exception; `validation` for an empty prompt, a malformed head, or an output type the head cannot answer), so the wrapper's Anthropic fallback answers. The model, the head file, the shape rule, and the fit protocol are in [Local Encoder Classifier](local-encoder-classifier.md).

### The hotfix #1055 invariant as the legs carry it

No `asyncio.wait_for` appears in `agent/llm/backends/`. The only timers around a live request are the SDK-level client timeouts (the encoder leg has none, having no request), and the queue wait is bounded separately by `semaphore_slot(timeout=slot_timeout)`, which the Anthropic leg enters before constructing the client. The wrapper's `hard_timeout` wraps the legs from outside and is the one coroutine-level cap; the three 3 s hot-path sites (the promise gate, read-the-room, the completion judge) pass `hard_timeout=None` and rely on `sdk_timeout=3.0, slot_timeout=3.0, max_retries=0` alone. `tests/unit/test_llm_task_taxonomy.py` check 6 pins both halves by function body: no `wait_for` inside the four named hot-path bodies or any backend function, and `hard_timeout=None` on every `run_typed` call in those bodies.

## The Degraded-Stack Guard, by Route Axis

`_guard_stack(caller, *, signature_axis)` forces resolution of the memoized degraded flag and raises `LLMStackIncompatible` on a bad stack. It subclasses `LLMCallError`, so every existing `except LLMCallError` fail-safe applies unchanged. The gate is two-axis and the wrapper picks the axis from the route:

| Route | Blocked by a stack that fails to import | Blocked by an `anthropic`/`pydantic-ai` signature mismatch |
|---|---|---|
| Anthropic-routed `run_typed` | yes | yes |
| Ollama-routed `run_typed` | yes | no |
| Encoder-routed `run_typed` | yes | no |

The wrapper calls `_guard_stack("run_typed", signature_axis=(route.backend is Backend.ANTHROPIC))` between `resolve()` and the primary leg. A locally routed call never touches `anthropic`, so an Anthropic create-signature break must not fall the hot-path classifiers on a local leg back to their conservative defaults fleet-wide. Before an Anthropic fallback leg the wrapper runs `_guard_stack("run_typed:fallback", signature_axis=True)`, so a signature-broken fallback raises `LLMStackIncompatible` and the caller's fail-safe applies. Full design in [LLM Stack Compat Gate](llm-stack-compat-gate.md).

## The Fallback Budget

A route with a `fallback` (an `OLLAMA` or `LOCAL_ENCODER` site for eligible context) gets one Anthropic attempt when the local leg raises `LLMCallError`, inside the caller's budget and never the primary leg's default timer:

- `budget = sdk_timeout if sdk_timeout is not None else hard_timeout` (both `None` means uncapped); `start = monotonic()` before the primary; `deadline = start + budget`.
- On failure, `elapsed = monotonic() - start`. When `budget - elapsed < MIN_REMAINDER_S` (0.5 s) the fallback is skipped, the wrapper logs `llm_no_fallback`, and the primary's error propagates.
- `fb_timeout = default_sdk_timeout(fallback.backend)` when `budget is None`, else `min(default_sdk_timeout(fallback.backend), budget - elapsed)`; `fb_slot = fb_timeout if slot_timeout is None else min(slot_timeout, fb_timeout)`.
- The fallback leg runs with `sdk_timeout=fb_timeout, slot_timeout=fb_slot, max_retries=0, deadline=deadline`. `max_retries=0` is load-bearing: on anthropic 1.7.0 the SDK default retries `APITimeoutError` twice, so one timer could otherwise bound three attempts. `deadline` is load-bearing too: `fb_slot` and `fb_timeout` bound two sequential phases (the slot wait, then the request), so the leg re-checks the deadline after the slot is acquired and shrinks the request timer to what is left.

So a routing classifier with no `sdk_timeout` whose granite call times out at 20 s gets one Haiku attempt whose slot wait plus request fit inside the 15 s left under the 35 s `hard_timeout`; a 3 s site's explicit `sdk_timeout` is its budget. The primary leg always runs with `deadline=None` and the caller's `max_retries`.

## Log Lines

Three fixed-prefix lines are the operator's evidence of which backend served a site; `docs/infra/llm-task-routing.md` has the greps.

| Line | Level | When |
|------|-------|------|
| `llm_route site=<site> backend=<backend> elapsed_ms=<int>` | INFO | After whichever leg answered (the primary on the normal path, the fallback on the degraded one). ` confidence=<score>` (`%.3f`) is appended whenever the returned instance carries a `confidence` attribute that is not `None`, on either leg, so one grep reads a site's score distribution; a result without the attribute logs the line without it. |
| `llm_fallback site=<site> primary=<backend> fallback=<backend> reason=<reason> elapsed_ms=<int>` | WARNING | Ahead of a fallback leg. `reason` is the primary's `LLMCallError.reason`: `timeout` for a slow daemon, `transport` for a stopped one or for a missing encoder extra, weights, or head. |
| `llm_no_fallback site=<site> primary=<backend> reason=<reason> elapsed_ms=<int> budget_s=<float>` | WARNING | The primary failed with under 0.5 s of budget left, so no fallback ran. Distinct from `llm_fallback`, so `grep -c llm_fallback` counts real fallbacks only. |

## Fail-Safe Posture

`run_typed` does not pick a fail-safe default. Provider errors, slot starvation, exhausted schema-validation retries, and the outer hard timeout are logged, then raised as `LLMCallError` with a `reason` (the original exception is chained via `__cause__`). Each call site keeps its own conservative default on failure, named in the one-line comment above its `LLMTask` declaration: routing defaults to respond, email triage escalates, memory extraction skips, the job router mints NEW. The right default is site-specific, and the wrapper cannot decide it for every caller.

## Import Safety

Module scope in `agent/llm/wrapper.py`, `agent/llm/backends/*.py`, `agent/llm/compat.py`, and `agent/anthropic_client.py` holds stdlib and our own code only. Every third-party LLM-stack symbol (`anthropic`, `openai.AsyncOpenAI`, `pydantic_ai.*`) is resolved through `agent/anthropic_client.py::_load_stack`, one `functools.cache`-memoized whole-stack loader, called only from inside `run_typed` and handed to the leg as `stack`; the encoder leg imports `onnxruntime`, `tokenizers`, and `numpy` inside its own memoized `_load_runtime`. A machine whose installed stack is broken, or without the `classification-local` extra, can still `import agent.llm`, and therefore `import bridge.telegram_bridge`, with the failure surfacing at the call. `tests/unit/test_llm_import_safety.py` enforces this out-of-process, with `openai`, `onnxruntime`, and `tokenizers` in its raising shim.

`_load_stack` is imported into `wrapper.py`'s namespace, so `monkeypatch.setattr(wrapper_mod, "_load_stack", ...)` is the network-isolation seam for tests on either leg.

## Adding a New Site

1. Define a `pydantic.BaseModel` describing the decision shape (a label field plus a confidence or reasoning field; `Literal` fields for a closed set).
2. Declare the site beside it: `NAME = LLMTask(site="module.decision", kind=..., backend=..., error_cost=...)`, literal values only, with a one-line comment naming the fail-safe. A classification site enters on `backend=Backend.ANTHROPIC`; a local landing needs a comparison record (see the [acceptance bar](llm-task-taxonomy.md#acceptance-bar)).
3. Build a prompt string.
4. Call `await run_typed(prompt, YourDecisionModel, task=NAME, project_key=project_key)` inside a `try/except LLMCallError`, passing the project key the site can know. A hot-path site with a budget passes `sdk_timeout`, `slot_timeout`, `max_retries=0`, and `hard_timeout=None`.
5. On `LLMCallError`, apply the site's conservative default and log it.
6. Add the site's row to the [taxonomy table](llm-task-taxonomy.md#site-table); the parity test fails until it is there.

No new client construction, no `json.loads`-shape parsing, no fallback plumbing.

## Migrated Call Sites

Every classification and wrapper-served thinking site goes through `run_typed` with a dedicated output model, keeping its own conservative default on failure. Every raw Anthropic, Ollama, and OpenRouter client is gone from these modules.

| Id | Site | Module (function) | Output model | Call shape |
|----|------|-------------------|--------------|------------|
| C1 | `routing.needs_response` | `bridge/routing.py` (`classify_needs_response`) | `NeedsResponseDecision` | `project_key` from `should_respond_async` |
| C2 | `routing.terminus` | `bridge/routing.py` (`classify_conversation_terminus`) | `TerminusDecision` | `project_key` from `should_respond_async` |
| C3 | `routing.work_request` | `bridge/routing.py` (`classify_work_request`) | `RoutingDecision` | `project_key` from `should_respond_async` |
| C4 | `intent_classifier.intent` | `agent/intent_classifier.py` | `IntentClassification` | `parsed.model_dump()` keeps the dict-returning cached contract |
| C5 | `classifier.work_type` | `tools/classifier.py` (`classify_request_async`) | `WorkTypeDecision` | returns the same dict shape as before; the sync `classify_request` is deleted |
| C6 | `agent_catchup.judge` | `bridge/agent_catchup.py` (`judge_message`) | `CatchupJudgeVerdict` | `project_key` threaded through `sweep_chat` |
| C7 | `injection_inspection.risk` | `bridge/injection_inspection.py` | `_InjectionJudgment` (`risk: Literal["suspected", "none"]`) | `sdk_timeout` and `hard_timeout` at `INJECTION_INSPECT_TIMEOUT_S` |
| C8 | `context_recall.advised` | `bridge/context_recall.py` | `ContextRecallVerdict` | `sdk_timeout=3.0` |
| C9 | `promise_gate.verdict` | `bridge/promise_gate.py` (`_evaluate_promise_async`) | `PromiseVerdictDecision` | `system=PROMISE_GATE_SYSTEM_PROMPT`, `sdk_timeout=slot_timeout=RTR_SDK_TIMEOUT` (3.0), `max_retries=0`, `hard_timeout=None`; `project_key` from `session_id` and from the drafter's `session` |
| C10 | `session_completion.novelty` | `agent/session_completion.py` (`_judge_completion_novelty`) | `CompletionNoveltyDecision` | `system=`, 3 s `sdk_timeout` and `slot_timeout`, `max_retries=0`, `hard_timeout=None` |
| C11 | `health_check.judge` | `agent/health_check.py` | `HealthDecision` | `project_key` from `AgentSession.project_key` |
| C12 | `job_router.route` | `bridge/job_router.py` | `JobRouteDecision` | `backend=OLLAMA`; total fail-open to NEW |
| C13 | `classifier.intake_intent` | `tools/classifier.py` (`classify_message_intent_async`) | `IntentDecision` / `IntentDecisionWithRecall` | `backend=OLLAMA`; fails open to `new_work` |
| C14 | `memory_audit.classify` | `reflections/memory/memory_quality_audit.py` (`_gemma_classify`) | `MemoryAuditDecision` | `backend=OLLAMA`; `sdk_timeout=GEMMA_CALL_TIMEOUT_SEC`, `hard_timeout=None`, awaited directly from `_layer3_classify` |
| C15 | `improvement_collect.promise_judge` | `reflections/improvement_collect.py` (`collect_promises`) | `PromiseJudgeDecision` | `sdk_timeout=PROMISE_JUDGE_SDK_TIMEOUT_S` (30 s); `collect_promises` and `run_improvement_collect` are coroutines the reflection scheduler awaits; unmetered |
| C16 | `email_cs.triage` | `tools/email_cs/triage.py` | `EmailTriageDecision` | `client_only` |
| RTR | `read_the_room.verdict` | `bridge/read_the_room.py` (`read_the_room`) | `RoomVerdict` (`action: Literal["send", "trim", "suppress"]`) | one thinking call for action and rewrite; `system=READ_THE_ROOM_SYSTEM_PROMPT`, `sdk_timeout=slot_timeout=RTR_SDK_TIMEOUT`, `max_retries=0`, `hard_timeout=None`; `project_key` from `session` |
| | `memory_extraction.extract` | `agent/memory_extraction.py` (`_llm_call`) | `ExtractionResult` | shared by four call sites in the module; `_EXTRACTION_SDK_TIMEOUT` / `_EXTRACTION_HARD_TIMEOUT` |
| | `memory_eval.query_generation`, `memory_eval.relevance_grading` | `tools/memory_eval/query_set.py` | `GeneratedQuery`, `RelevanceGrade` | thinking |
| | `compat.network_probe` | `agent/llm/compat.py` (`_check_network`) | `_Probe` | `_skip_guard=True` |

Thinking sites on a raw transport for a reason (vision, audio, streaming, a cross-vendor client, the harness) declare a `THINKING` task at module level and keep their transport; the taxonomy page's [site table](llm-task-taxonomy.md#site-table) lists them.

### Out of scope

- `agent/sdk_client.py` and the harness transport: the `claude -p` path.
- The ollama embedding path (`agent/embedding_provider.py`, `reflections/memory/*embedding*`): this wrapper standardizes LLM/chat calls, not embeddings.
- The Ollama runtime and machine-level provisioning: an operator action, recorded in [`docs/infra/llm-task-routing.md`](../infra/llm-task-routing.md).

## Tests

`tests/unit/test_llm_wrapper.py` covers structured-output success, the single auto-retry on schema mismatch, error surfacing as `LLMCallError`, the required `task=`, the per-backend SDK timer, the fallback budget (the C1 remainder case, the capped slot wait, an explicit `sdk_timeout` as the budget, the spent-budget skip, `max_retries=0`, one `llm_route` line and no fallback on the normal path, `hard_timeout` capping both legs together), the `_skip_guard` single-caller pin, and the outer `asyncio.wait_for` hard-timeout bound. `tests/unit/test_llm_stack_degraded_start.py` pins the guard axis per route (a signature break leaves an Ollama-routed call running; a signature-broken fallback raises the typed error). `tests/unit/test_llm_backend_anthropic.py` covers slot starvation raising before any client exists and inside the budget, cancellation releasing the slot and closing the client, the post-slot deadline re-check, `slot_wait_ms`, client construction, and the failure reasons; `tests/unit/test_llm_backend_ollama.py` covers the native output request, the explicit timer, the fresh client closed once per call, the deadline re-check, and the failure reasons; `tests/unit/test_llm_backend_local_encoder.py` covers the head loader, the shape rule, the deadline re-check, the single runtime load under concurrency, and the failure reasons. Every leg file asserts no third-party import at module scope and no `wait_for` inside the leg. `tests/helpers/llm_fakes.py` is the one shared fake implementing the leg protocol, used by every migrated site's test. Each migrated call site's own test file asserts its typed output model and preserved fail-safe default.

## See Also

- [LLM Task Taxonomy](llm-task-taxonomy.md): the declarations, the router rules, eligibility, the acceptance bar, every site's landed backend.
- [Local Encoder Classifier](local-encoder-classifier.md): the third leg's model, head file, shape rule, and fit protocol.
- [`docs/infra/llm-task-routing.md`](../infra/llm-task-routing.md): Ollama service settings, the encoder weights, the log greps, rollback levers.
- [LLM Stack Compat Gate](llm-stack-compat-gate.md): the import-safety contract, the compat predicate, degraded posture, and coupled-set auto-bumping.
- [Config Timeout Catalog](config-timeout-catalog.md): `anthropic_sdk_s`, `anthropic_hard_s`, `local_typed_hard_s`.
- [Headless Session Runner](headless-session-runner.md): the harness half of LLM calls.
- `agent/llm/wrapper.py`, `agent/llm/backends/__init__.py`: the implementation and its full invariant docstrings.
- `docs/plans/pydantic-ai-nonharness-llm-standardization.md`, `docs/plans/llm-task-taxonomy-routing-layer.md`: the two plans behind this page.
