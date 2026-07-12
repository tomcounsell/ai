# Non-Harness LLM Wrapper

A single PydanticAI-based call point for every LLM call that is not a `claude -p` harness session. Classification, extraction, and judgment calls declare a typed output model and get a schema-validated result back, with model choice as one config edit instead of N hand-rolled clients.

## Two Ways to Call an LLM

The system makes two categories of LLM calls, and they stay deliberately separate:

- **Harness calls** are Claude Code sessions — skills, hooks, tools, resume — driven through `claude -p` via the headless session runner (`agent/session_runner/`). See [Headless Session Runner](headless-session-runner.md).
- **Non-harness calls** classify, extract, judge, or refine text outside a harness session: routing decisions, memory extraction, session-route matching, catch-up judging, email triage, intent classification.

Before this wrapper, every non-harness call site hand-rolled its own provider client: most constructed `anthropic.Anthropic` / `AsyncAnthropic` inline and pinned Haiku directly; three sites called `ollama.chat()` with a Haiku fallback. There was no shared structured-output contract and no single point to swap models. `agent/llm/` closes that gap for the non-harness half only — it does not touch the harness transport.

## The Wrapper

`agent/llm/wrapper.py` exposes one async function:

```python
from agent.llm import run_typed, LLMCallError

async def run_typed(
    prompt: str,
    output_type: type[BaseModel],
    *,
    model: str = MODEL_FAST,
    sdk_timeout: float = DEFAULT_SDK_TIMEOUT,      # 30.0s
    hard_timeout: float | None = DEFAULT_HARD_TIMEOUT,  # 35.0s
) -> BaseModel:
    ...
```

Call it with a prompt and a `pydantic.BaseModel` subclass describing the desired output shape. PydanticAI validates the model's response against that schema and auto-retries once on mismatch. A validated instance of `output_type` comes back, or the call raises `LLMCallError`.

`model` defaults to `config.models.MODEL_FAST` (Haiku), so a single config edit swaps the model for every non-harness call. Per-call overrides are supported — a high-frequency hot path can pin a cheaper or local model without touching its own call site.

### Per-call slot pattern (event-loop safety)

`agent/anthropic_client.py` holds no long-lived shared client — `semaphore_slot()` only gates concurrency; each caller has always constructed its own client (hotfix #1055/#1111). `run_typed` follows that same pattern on every invocation instead of inventing a shared client that doesn't exist:

1. `async with semaphore_slot():` holds the shared semaphore for the *entire* `Agent.run()` call, not just client construction.
2. Inside the slot, it constructs a fresh `async with anthropic.AsyncAnthropic(api_key=..., timeout=sdk_timeout)` — per-call, per-site timeout, with `async with` preserving hotfix #1055's httpx cleanup.
3. That client is injected into PydanticAI: `AnthropicProvider(anthropic_client=client)` → `AnthropicModel(model, provider=...)` → `Agent(model, output_type=output_type)`.
4. When `hard_timeout` is not `None`, `await agent.run(prompt)` is wrapped in `asyncio.wait_for(..., timeout=hard_timeout)` — an outer wall-clock cap that fires even when the SDK-level timer doesn't (for example, a half-open TCP socket with no event to fire on).
5. The slot releases on `__aexit__`.

### Fail-safe posture

`run_typed` does not pick a fail-safe default. Provider errors and exhausted schema-validation retries are logged, then raised as `LLMCallError` (the original exception is chained via `__cause__`). Each call site keeps its own conservative default on failure — routing defaults to respond, email triage escalates, memory extraction skips — because the right default is site-specific, not something the wrapper can decide for every caller.

An empty, `None`, or whitespace-only `prompt` raises `ValueError` before any client is built or network call made, so a bad prompt fails fast instead of hanging.

## Adding a New Classifier

1. Define a `pydantic.BaseModel` describing the decision shape (mirror the existing per-call models below for the level of detail to include — usually a label field plus a confidence or reasoning field).
2. Build a prompt string.
3. Call `await run_typed(prompt, YourDecisionModel, model=MODEL_FAST)` inside a `try/except LLMCallError`.
4. On `LLMCallError`, apply your site's own conservative default and log it.

No new client construction, no `json.loads`-shape parsing, no ollama fallback plumbing.

## Migrated Call Sites

Every site below moved from a hand-rolled client (or `ollama.chat()`) to `run_typed` with a dedicated output model, keeping its original conservative default on failure.

| Site | Output model | Was | Now |
|------|--------------|-----|-----|
| `agent/intent_classifier.py` | `IntentClassification` | sync `anthropic.Anthropic` (Haiku), hand-rolled text parse | `run_typed`; `parsed.model_dump()` replaces `dataclasses.asdict(parsed)` to keep the function's dict-returning cached contract |
| `agent/memory_extraction.py` (`_llm_call`) | `ExtractionResult` | `AsyncAnthropic` (Haiku), hand-rolled `json.loads`-shape repair | `run_typed`, shared by four call sites in the module; the merged LLM refusal-detector stays in place |
| `bridge/session_router.py` | `SessionRouteDecision` | `anthropic_slot()` (Haiku), fence-strip + `json.loads` | `run_typed` |
| `bridge/routing.py` (`classify_needs_response`) | `NeedsResponseDecision` | `ollama.chat()` + Haiku fallback | `run_typed` |
| `bridge/routing.py` (`classify_conversation_terminus`) | `TerminusDecision` | `ollama.chat()` + Haiku fallback | `run_typed` |
| `bridge/routing.py` (`classify_work_request`) | `RoutingDecision` | `ollama.chat()` + Haiku fallback | `run_typed` |
| `bridge/agent_catchup.py` (`judge_message`) | `CatchupJudgeVerdict` | `ollama.chat()` + Haiku fallback | `run_typed` |
| `tools/email_cs/triage.py` | `EmailTriageDecision` | `ollama_client.chat()` + tolerant post-hoc JSON extraction | `run_typed` |

`bridge/routing.py` and `bridge/agent_catchup.py` no longer import `ollama`; `tools/email_cs/triage.py` no longer imports `tools.ollama_client`.

### Skipped: `bridge/read_the_room.py`

`read_the_room.py:429` already forces `tool_choice={"type": "tool", "name": "room_verdict"}` on its Anthropic call — that is already schema-validated structured output via tool-calling. Re-wrapping it through `run_typed` would churn working code for no gain, so it keeps its own `RoomVerdict` type and its own client construction, unchanged by this migration.

### Out of scope

- `agent/sdk_client.py` and the harness transport — that is the `claude -p` path, not a non-harness call.
- The ollama *embedding* path (`agent/embedding_provider.py`, `reflections/memory/*embedding*`) — this wrapper standardizes LLM/chat calls, not embeddings.
- Removing the ollama runtime or machine-level provisioning — that stays an operator action.

## Tests

`tests/unit/test_llm_wrapper.py` covers structured-output success, the single auto-retry on schema mismatch, error surfacing as `LLMCallError`, per-call semaphore-slot acquisition, that the injected client is the one PydanticAI actually uses, and the outer `asyncio.wait_for` hard-timeout bound. Each migrated call site's own test file asserts its typed output model and preserved fail-safe default.

## See Also

- `agent/llm/wrapper.py` — the wrapper implementation and its full per-call invariant docstring
- [Headless Session Runner](headless-session-runner.md) — the harness half of LLM calls
- `docs/plans/pydantic-ai-nonharness-llm-standardization.md` — design spikes and the full call-site inventory audit
