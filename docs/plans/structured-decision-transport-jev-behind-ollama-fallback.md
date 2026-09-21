---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-09-21
tracking: https://github.com/tomcounsell/ai/issues/3421
last_comment_id: 5745559079
---

# Structured-Decision Transport (TypeSafe Jev 1.13) Behind an Ollama Fallback

## Problem

Lane A (#3410, PR #3524, merged as `4703bce23`) gave every non-harness LLM call a declared `LLMTask`, one routing point, two backend legs (Anthropic, Ollama), and a comparison runner whose records are the argument for a site's backend. It also measured that granite (`granite4.1:3b` via Ollama) clears the agreement bar on four comparison sites (C7 0.981, C8 0.923, C11 0.900, C15 0.885) and misses it on eight, and that on the build machine every inbound-shaped site fails `n_real` because the memory store holds 12 real `valor` messages. No comparison site moved off Anthropic.

TypeSafe's Jev 1.13 is a structured-decision model: it takes a `state` and typed `questions` and answers with per-option probabilities in about a second for two hundred-thousandths of a dollar. It answers on its own endpoint, `POST https://api.typesafe.ai/v1/systemone`, and a direct TypeSafe key now sits in the vault `.env` (`TYPESAFE_API_KEY`, added 2026-09-21), so the transport goes straight to TypeSafe with no proxy in the path. Neither lane-A leg can reach a `{state, questions}` API, so this is a third leg. Tom's ordering (2026-09-18): local first, an external structured-decision provider only behind a local fallback. His delivery shape (2026-09-19, #3410 comment 5737918683): no transition phase, no switches; the builder iterates on the comparison until the PR is approved, and PR approval is the gate.

**Current behavior:**

Verified on `main` at `149f0d0da` (Freshness Check):

- `agent/llm/tasks.py::Backend` has `ANTHROPIC` and `OLLAMA`; `agent/llm/router.py::resolve` raises `ValueError` for any other backend (`:70`); `agent/llm/wrapper.py::_LEGS` (`:95`) maps the two legs; `agent/llm/backends/__init__.py::default_sdk_timeout` (`:51`) knows two timers.
- `tools/classification_eval/__main__.py::CANDIDATE_BACKENDS` (`:47`) is derived from the enum, but `_candidate_arms` (`:115`) builds only `ollama` and `anthropic` arms, so `--candidate decisions` would raise `KeyError` after passing the vocabulary check.
- `tools/classification_eval/core.py::evaluate_bar` (`:411`) applies six criteria; there is no cost criterion, because the only paid candidate so far (Haiku) was the reference.
- `tools/classification_eval/records.py::_audit_row` (`:126`) knows `OLLAMA` and `ANTHROPIC` landings and reads the newest record per site regardless of which arms it carries.
- `config/models.py` has `OPENROUTER_URL` (`:56`, chat/completions) and no TypeSafe URL, no `JEV`, no `MODEL_INFO` entry for it.
- `tools/paid_inference_meter.py` meters in integer cents (`_valid_amount`, `:149`): a per-call reservation at Jev's 32k-state bound (32000 × 0.000000042 = 0.001344 USD) rounds to 0 cents, and every `settle` writes one `spend_receipt` `ImprovementEvidence` row (`:256`). Per-call reserve/settle on the C1 hot path would write one evidence row per inbound message and meter nothing.
- `config/settings.py::APISettings` (`:29-56`) has no TypeSafe field. The vault `.env` carries `TYPESAFE_API_KEY` (verified 2026-09-21, value never printed) and `.env.example` does not declare it, so the env completeness check knows nothing of it. A flat key does not populate a nested `api.*` field on its own (the group is exploded from `API__*`; spike-1 of the earlier draft showed `settings.api.openrouter_api_key` is `None` beside a set `OPENROUTER_API_KEY`); the pattern this file already uses for a flat key on a nested field is a `default_factory` reading `os.getenv` (`HybridEvalSettings`, `:539-560`).
- The site walk (`agent/llm/tasks.py::_literal_field`, `:142`) accepts exactly five `LLMTask` fields with literal values; any new keyword on a declaration raises `ValueError` unless the walk learns it.

**Desired outcome:**

1. A third backend leg, `agent/llm/backends/decisions.py`, implementing lane A's leg protocol as it landed (`call(prompt, output_type, route, *, system, sdk_timeout, slot_timeout, max_retries, deadline=None, stack) -> BaseModel`): one `httpx` POST per call to `https://api.typesafe.ai/v1/systemone`, questions built from the output type's `Literal` and `bool` fields, `noul` to `bool` at a per-field threshold, `confidence` from the answer, reason fields empty, validated with `output_type.model_validate`, `LLMCallError` on every failure class, metered under purpose `structured_decision` from `usage.input_tokens` times the pinned price, without a Redis write per call.
2. Router rule 5, `task.backend == DECISIONS and is_eligible(project_key)` → `Route(DECISIONS, JEV, fallback=Route(OLLAMA, OLLAMA_CLASSIFIER_MODEL))`, ahead of rule 3; the ineligible case resolves to `Route(ANTHROPIC, model)` (charter §7). `Backend.DECISIONS`, `JEV`, `TYPESAFE_DECISIONS_URL`, `MODEL_INFO[JEV]`, `APISettings.typesafe_api_key`, `TimeoutSettings.decisions_sdk_s`.
3. The comparison runner grows a `decisions` candidate arm, a `cost` criterion (at Haiku-backed sites the candidate costs at most one tenth of a Haiku call), and an audit branch for `DECISIONS` landings that also checks the Ollama fallback is a passing backend.
4. A comparison record for every eligible classification site (C1 through C15; C16 is `client_only`) with `--candidate decisions,ollama`, so one record carries both the candidate and its fallback on the same inputs. Sites land on `DECISIONS` one word at a time on a passing record; a miss stays where lane A landed it and the record names the failing criterion; Jev is rejected as a backend, and the case records it, if it returns non-200 on more than 2% of comparison calls, agreement is below 80% at every Haiku-backed site, or C12's threshold cannot be set without raising the wrong-bind rate.
5. `tests/unit/test_models.py::test_typesafe_jev_pinned_model_answers`: one live minimal call against the endpoint asserting the response `model` equals the pinned `JEV` and the answer shape holds, fail-closed on network error like its OpenRouter sibling.
6. The C15 pre-screen cascade (Jev `noul` on every draft, an Anthropic call for the `span` on positives) only if C15's decisions record clears its bar.

## Freshness Check

**Baseline commit:** `149f0d0da` (`main`, 2026-09-21)
**Issue filed at:** 2026-09-18T13:08:37Z
**Disposition:** Minor drift

The issue was written against the parent plan before lane A landed, so most of its references are to interfaces that now exist in a slightly different shape. Every claim below was re-read on the baseline.

**File:line references and interface claims re-verified:**

| Issue claim | On `main` at `149f0d0da` | Status |
|---|---|---|
| Leg protocol `call(prompt, output_type, route, *, system, sdk_timeout, slot_timeout, max_retries) -> BaseModel` | `agent/llm/backends/__init__.py:5-8`: the protocol also takes `deadline: float \| None = None` and `stack: LLMStack`; `ollama.py:52-63` and `anthropic.py:70-81` implement it | drifted: two extra keywords; this lane's leg takes both, and the Ollama fallback receives `deadline` from the wrapper (`wrapper.py:295`) |
| `LLMTask` fields `question`, `noul_threshold`, `thresholds`, `decision_options` land here with defaults | `agent/llm/tasks.py:86-98`: five fields; the static walk (`:142-184`) accepts only those five, each as a literal; the leg never receives the task (`wrapper.py:244-254` passes `prompt, output_type, route` and keywords) | drifted: the four fields cannot reach the leg through the protocol and one `question` cannot describe a type with two decision fields (`IntentDecisionWithRecall`); replaced by a per-field `Decision` marker (Technical Approach) |
| "Comparison through lane A's runner, unchanged: `--candidate decisions`" | `tools/classification_eval/__main__.py:47` derives the vocabulary from `Backend` (so the new member is accepted at parse time) but `_candidate_arms` at `:115-122` builds only two arms | drifted: the runner needs an arm builder, a `cost` criterion, and an audit branch |
| "Metered via `paid_inference_meter.reserve(..., purpose="structured_decision")` reserving the 32k-context bound and `settle_from_response`" | `tools/paid_inference_meter.py:149-163` rounds to cents (the bound is 0 cents); `settle` (`:234-266`) writes one `ImprovementEvidence` row per settlement | drifted: per-call reserve/settle would write an evidence row per inbound message and reserve nothing; replaced by a one-cent envelope per process (Technical Approach) |
| The transport is `POST https://openrouter.ai/api/alpha/decisions` with `OPENROUTER_API_KEY` | Superseded 2026-09-21 (Tom): a direct TypeSafe key exists (`TYPESAFE_API_KEY` in the vault `.env`; 1Password vault `m-valor`, item "TypeSafe API", field `api_key`), so the transport is `POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer`. `config/settings.py:29-56` has no TypeSafe field; `.env.example` does not declare the key | drifted: the leg reads `settings.api.typesafe_api_key`, a new field populated by `default_factory=lambda: os.getenv("TYPESAFE_API_KEY")` (the file's existing pattern for a flat key on a nested group, `:539`), and `.env.example` gains the declaration |
| `httpx` is a dependency | `pyproject.toml:16` `httpx>=0.27.0` | holds |
| Router docstring reserves rule 5 for this lane | `agent/llm/router.py:31-32` | holds |
| Live probe: HTTP 200, typed answers with probabilities, `usage.cost` | re-run 2026-09-21 from this venv against the native endpoint (spike-1): HTTP 200 in 997 to 1475 ms, response `model` `jev-1.13.0`, `usage` carries `input_tokens` and `output_tokens` and no `cost` | drifted: no cost field; the leg meters `input_tokens × price` from a pinned constant, and a response without `input_tokens` marks the envelope `unknown` |
| Pricing: prompt `0.000000042`, completion `0`, context 32000 | https://docs.typesafe.ai/models (retrieved 2026-09-21): "$0.042 per million" input tokens, "Output tokens are free", "64k tokens per request; 32k tokens for `state` plus the longest question"; there is no unauthenticated listing endpoint on the native API | holds for the price; the per-call bound stays 32000 × 4.2e-8 USD; the probe test becomes one live authenticated call rather than a listing read |
| C16 email triage is `client_only` | `tools/email_cs/triage.py:100`, `client_only=True` | holds |
| "A site may declare `backend=DECISIONS` only if its lane-A record shows granite clearing the bar" | `docs/features/llm-task-taxonomy.md:171-194`: no comparison site's granite arm cleared all six criteria; the misses were `n_real` (12 real messages on the build machine) and `p95_c4` (the build machine's GPU serialised at concurrency 4), plus `agreement` on eight sites; granite's agreement cleared the tier bar on C7, C8, C11, C15; C12 to C14 hold latency-only records that pass | drifted: read literally the landable set is empty; the plan reads the rule as "the Ollama fallback clears the bar on the same record as the decisions arm, or on the site's latency-only record" (Technical Approach, landing rule), which is what the rule is for (no single external provider on a hot path, and a fallback that is itself a passing backend) |

**Cited sibling issues/PRs re-checked:**
- #3410: closed 2026-09-20 by PR #3524 (merged as `4703bce23`); its plan was archived to `docs/archive/plans-completed/llm-task-taxonomy-routing-layer.md` in `1ab8d429e`. Everything this lane builds on is on `main`.
- #3420 (lane B): open; its `/do-plan` is in progress in parallel (ledger slug `sdlc-3420`, no plan doc on `main` yet). Both lanes append to `Backend`, `_LEGS`, `default_sdk_timeout`, `resolve`, `_candidate_arms`, `test_llm_router.py::_expected`, `test_llm_tasks.py`'s enum pin, and the two doc tables. The additions are disjoint members and rows, so the second lane to merge rebases through a small mechanical conflict.
- #3422 (emoji as a classification site): open, untouched by this lane.
- #3338 / PR #3381: the fail-closed live probe pattern this lane's `test_typesafe_jev_pinned_model_answers` mirrors; `tests/unit/test_models.py:62-70`.
- #3177 (RSI): owns `tools/paid_inference_meter.py`; this lane consumes it and adds no controller logic.

**Commits on main since issue was filed (touching referenced files):**
- `4703bce23` LLM task taxonomy and routing layer (lane A) (#3524): created every file this lane extends. Read in full; the drift rows above are its effect.
- `1ab8d429e` Migrate completed plan: the parent plan moved to `docs/archive/plans-completed/`; this plan cites it there.

**Active plans in `docs/plans/` overlapping this area:** none on `main`. `recursive-self-improvement.md` (#3177) owns the meter and the case substrate this lane writes records to; coordination, not conflict. Lane B's plan (#3420) will overlap on the enum and the router and is being written in parallel.

**Notes:** Five drifts change the plan's mechanism but none its outcome: the leg signature gains `deadline` and `stack`; the question metadata moves from `LLMTask` to a per-field `Decision` marker; metering moves from per-call reserve/settle to a one-cent envelope fed by `input_tokens × price`; the transport targets TypeSafe directly with its own key and settings field; the probe test is one live call instead of a listing read. The sixth drift (the landing rule) is a scoping decision recorded in Technical Approach and raised in Open Questions only for the real-input sample it depends on.

## Prior Art

- **#3410 / PR #3524** (merged 2026-09-20): lane A. Everything this lane plugs into: `LLMTask`, `resolve`, the leg protocol with its `deadline` re-check and `stack` seam, `run_typed`'s one-shot fallback inside the caller's budget, the comparison runner with its six-criterion bar, the `classifier_comparison` evidence kind on case `1ec40086ca1d422e90ef747775ff7f64`, the taxonomy doc's parity test. Its outcome table (no site moved to granite; `n_real` short on the build machine) is the starting state.
- **#3338 / PR #3381** (merged 2026-09-17): the live probe pattern in `tests/unit/test_models.py`, fail-closed on network error, with the `_configured_openrouter_ids()` sweep that warns on other `OPENROUTER_*` ids. This lane's Jev probe mirrors the first (one authenticated call instead of a listing read, because the native API publishes no listing) and stays out of the second (`JEV` is a TypeSafe id, not an `OPENROUTER_*` name).
- **#3215 / PR #3337 and PR #3350** (merged 2026-09-15/16): `tools/paid_inference_meter.py` (reserve/settle in cents, `spend_receipt` rows, the reconcile sweep) and the metered `OpenRouterGemmaArm` in the runner (one reservation per run, settled from accumulated `usage.cost`, `metering="unknown"` the moment a response carries no cost). The runner arm pattern is reused; the per-run reservation is the model for this lane's envelope; the "no usage means unknown, never zero" rule carries over to a response without `input_tokens`.
- **RSI lineage** on case `1ec40086ca1d422e90ef747775ff7f64`: `b5c1b5d6` (Tom's inspiration: Jev as a structured-decision candidate), `6265b1ea` (the accuracy prior: TypeSafe's 67.8% against Opus 5 73.1% and GPT-5.6 Sol 74.1%, https://geotoolbox.ai/blog/what-is-jev-ai, retrieved 2026-09-18), `996af74c` (local-first and no single external provider), `c4e1199d` (lane A's outcome), `9984d338` (lane A's merge), `cdea91a2` (the direct TypeSafe key and the native endpoint, 2026-09-21). The comparison records this lane writes attach to the same case.
- **#3001**: the import-safety contract (`LLMStack`, `_load_stack`, no third-party symbol at module scope in `agent/llm`). The decisions leg gets its HTTP client from the stack for the same reason and the same test seam.
- **#1055 / #1111**: the hotfix invariant every leg carries (an SDK-level timer around the live request, no `asyncio.wait_for`). `httpx.AsyncClient(timeout=...)` is that timer here.
- **pydantic/pydantic-ai#8552** (open upstream, found in Research): a request to route Jev through PydanticAI. Nothing shipped; this lane keeps the transport as one `httpx` POST and does not wait for it.

## Research

**Queries used:**
- https://docs.typesafe.ai/api and https://docs.typesafe.ai/models (retrieved 2026-09-21); https://docs.typesafe.ai/pricing returns 404, so the models page is the price source.
- The live probe from this venv (Spike Results, spike-1) against the native endpoint: eight POSTs, about 2,500 input tokens, about 0.0001 USD.
- The parent plan's Research finding 1 (the OpenRouter-proxied probe of 2026-09-18) re-read for what carries over: the model, the question types, the probability vector, the quality prior.

**Key findings:**

1. **The native wire shape.** `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <TYPESAFE_API_KEY>`, body `{"model": "jev-1.13.0", "state": <str | object | array>, "questions": {<id>: {"type": "noul" | "choice" | "score", "instructions": <str | object | array>, "criteria": ...}}}`. `choice` criteria is a map `option -> rubric string` (`null` allowed, at most 255 options); `noul` criteria is `{"true": <str>, "false": <str>}`; `score` criteria is an ordered array of 2 to 10 level strings. Response `{"model": "jev-1.13.0", "answers": {<id>: {"type": "choice", "choice": <option>, "probabilities": {<option>: <float>}, "confidence": <float>} | {"type": "noul", "noul": <float>}}, "usage": {"input_tokens": <int>, "output_tokens": <int>}}`. Both `instructions` and `noul` criteria are optional on the wire (a question with neither answered HTTP 200), which is looser than the OpenRouter proxy was. *Informs:* the question builder always emits `instructions` (a plain string from the `Decision` marker or a generated default) and both `noul` criteria anyway, because the rubric is where the builder's leverage lives; rubric strings replace the proxy's `{what, examples}` objects, with examples folded into the rubric text. Sources: https://docs.typesafe.ai/api; live probe.
2. **Errors.** The docs list 401 (key), 422 (validation, body names the field), 429 (rate limit, back off), 529 (overloaded). Observed: 401 with `{"detail": {"error_type": "authentication_error", "message": ...}}` for a bad key; HTTP 400 with `{"detail": {"error_type": "api_usage_error", "message": "Invalid request."}}` for an unknown question type and `"Unknown model: jev-9.9.9"` for an unknown model. *Informs:* the leg treats every non-200 as `LLMCallError(reason="transport")` with the status and `detail.message` (truncated) in the message; no retry in the leg (a 429 or 529 falls to granite inside the budget); the docs' SDK retry policy is not reproduced.
3. **A prompt-shaped `state` works.** Posting an instruction preamble plus the message as `state` returned a typed answer; the same held for C3's 753-character reference prompt on the proxied probe. *Informs:* the leg sends `system + "\n\n" + prompt` (or `prompt` alone) as `state`, so a site's production prompt is usable unchanged; the builder trims a site's `candidate_prompt` toward a bare state while iterating, exactly as lane A's rows already do for granite, and the site's production prompt becomes whatever the passing record measured.
4. **Dynamic options work as `choice` criteria keyed by id** (proxied probe: two job ids as keys plus a `bind`/`new` question answered `bind` / `job_a1` at 0.99 / 1.0; the native API's criteria map is the same shape). *Informs:* C12's per-call output type carries the candidate job ids as a `Literal`, and the leg needs no special case for dynamic options.
5. **Pricing and limits.** https://docs.typesafe.ai/models: "$0.042" per million input tokens, "Output tokens are free", "64k tokens per request; 32k tokens for `state` plus the longest question". No listing endpoint, no published rate limit. *Informs:* `MODEL_INFO[JEV]` records `input_cost_per_mtoken: 0.042`, `output_cost_per_mtoken: 0.0`, `context_window: 32000`, `price_retrieved_at: "2026-09-21"`; `JEV_PRICE_USD_PER_MTOKEN: float | None = 0.042` in `config/models.py` is the metering constant and a `None` there (the price withdrawn at a future re-read) makes every envelope `unknown`, never zero; the per-call bound for the headroom check is 32000 × 4.2e-8 = 0.001344 USD.
6. **The usage block carries tokens and no cost.** `usage.input_tokens` and `usage.output_tokens` only. *Informs:* the envelope accumulates `input_tokens × JEV_PRICE_USD_PER_MTOKEN / 1e6` per 200; `settle_from_response`'s estimate path (`prompt_tokens`/`completion_tokens`) is not used; a 200 without `usage.input_tokens` marks the envelope `metering="unknown"` (charter §8: uncertain metering is not zero cost).
7. **Model ids and aliases.** `jev-1.13.0` is the pinned id; `jev-latest` and `jev-preview` are aliases that move with releases, and the docs recommend pinning when confidence thresholds were tuned against a version. *Informs:* `JEV = "jev-1.13.0"`, never an alias; the probe test asserts the response `model` equals `JEV`, so a silent re-point fails by name.
8. **Quality prior** (parent plan finding 2): TypeSafe's own 67.8% agreement figure sits under every tier bar. Today's native probe answered `statement` at 1.0 and `noul` 0.30 for "thanks, that looks great" with rubric strings, where the proxied probe without rubrics answered `other` at 0.72. *Informs:* the expected outcome is a leg, a rule, a runner arm, and records, with landings on few sites or none; rubric text is the builder's lever; the plan's success criteria are written so that a zero-landing result with complete records is a pass.

A `tools.memory_search save` of findings 1, 2, 5, and 6 is attempted at plan time; this section is the capture point.

## Spike Results

### spike-1: Does the native TypeSafe endpoint accept the documented body with the vault key, and what do its answers and errors look like?
- **Assumption**: "`POST /v1/systemone` with `TYPESAFE_API_KEY` answers a choice plus a noul on a prompt-shaped state with the documented shapes, and its error bodies are parseable."
- **Method**: prototype (eight live POSTs from this venv, about 2,500 input tokens, about 0.0001 USD; script in the session scratchpad, the key loaded from the vault `.env` into the process and never printed)
- **Finding**: Yes. HTTP 200 in 1475 ms (first call), 1207, 1269, 997, 1225 ms; response `model` `jev-1.13.0` for both the pinned id and `jev-latest`; a 4-way `choice` answered `statement` at confidence 1.0 with a full probability vector, `noul` 0.30 / 0.31 across two calls; `usage` `{"input_tokens": 417, "output_tokens": 64}` with no cost field. A `choice` with one option, a question with no `instructions`, and a `noul` with no criteria all answered 200. A bad key: 401 `{"detail": {"error_type": "authentication_error", ...}}`. An unknown question type and an unknown model: 400 `{"detail": {"error_type": "api_usage_error", "message": ...}}`. The team-lead's probe earlier the same day: 200 in 1439 ms, a 3-way choice at 0.99, noul 0.48, usage 356 / 58.
- **Confidence**: high
- **Impact on plan**: the leg is one `httpx` POST with the body in Research finding 1; latency is about 1 to 1.5 s per call, so the 3 s SDK timer leaves room and the fallback budget arithmetic (Race 2) matters more than under the proxy's 0.5 s; the recorded-response fixtures are these bodies; every non-200 maps to `transport` with `detail.message`; metering is token-based; the C12 per-call `Literal` type needs no special casing in the leg.

### spike-2: How many real `valor` messages does this machine's memory store hold?
- **Assumption**: "The build machine has enough real inbound traffic for the routing sites' `n_real` minimum (100) and the others' (25)."
- **Method**: code-read plus one ORM read (`tools.classification_eval.arms.real_messages(5000)`)
- **Finding**: 12. The same number lane A's outcome table records, so this is the lane-A build machine and nothing has been added since.
- **Confidence**: high
- **Impact on plan**: on this machine every inbound-shaped site (C1 to C10, C12, C13, C15) fails `n_real` again unless the comparison replays a sample drawn elsewhere (`--inputs` with a file written by `--save-inputs` on a machine that holds the traffic, or the comparison runs on that machine). C11 (transcript windows, 40 real) and C14 (memory rows, 30 real) have real inputs of their own shape. Open Question 1.

### spike-3: Can per-field decision metadata ride on the output type without reaching the JSON schema?
- **Assumption**: "`Annotated[<type>, Decision(...)]` is retrievable from `FieldInfo.metadata` and absent from `model_json_schema()`, so the Anthropic and Ollama legs are unaffected."
- **Method**: prototype (in-venv, no network)
- **Finding**: Yes. `M.model_fields["verdict"].metadata == [Decision(...)]`; the schema for `verdict` is `{"enum": ["a", "b"], "type": "string"}` with no trace of the marker. A `Literal` field, a `bool` field and an unannotated `str` field coexist.
- **Confidence**: high
- **Impact on plan**: the `Decision` marker replaces the issue's four `LLMTask` fields; the leg reads it from `output_type.model_fields`; the site walk is untouched.

### spike-4: What does the meter do with a sub-cent per-call reservation?
- **Assumption**: "`reserve(valor, 0.001344, purpose="structured_decision")` per call meters the spend."
- **Method**: code-read (`_valid_amount`, `reserve`, `settle`, `record_receipt`) plus `_valid_amount(32000 * 4.2e-8) == 0`
- **Finding**: The bound rounds to 0 cents, so the reservation admits unconditionally and reserves nothing; each `settle` writes one `spend_receipt` `ImprovementEvidence` row and three Redis round trips. Per call on C1 that is one evidence row per inbound message.
- **Confidence**: high
- **Impact on plan**: the leg meters through a one-cent envelope per process (reserve 0.01 USD once, accumulate `input_tokens × price` per response, settle and re-reserve when the next call's 0.001344 bound would not fit), so the hot path does no Redis I/O on a typical call and the case sees one receipt per cent of spend; the runner arm uses the same envelope class with a per-run reservation like the gemma arm.

## Data Flow

Inbound Telegram message crossing a site declared `backend=DECISIONS` (C1 shown; the same frame for every site that lands):

1. **Entry point**: `bridge/telegram_bridge.py` resolves `project` and calls `should_respond_async(..., project, ...)`, which passes `project_key` to `classify_needs_response` (lane A, unchanged).
2. **Call site**: `await run_typed(prompt, NeedsResponseDecision, task=NEEDS_RESPONSE, project_key=project_key)` with `NEEDS_RESPONSE = LLMTask(site="routing.needs_response", kind=TaskKind.CLASSIFICATION, backend=Backend.DECISIONS, error_cost=ErrorCost.HIGH)`. `NeedsResponseDecision.needs_response` is `Annotated[bool, Decision("Does this message need a reply or action?", criteria={"true": "...", "false": "..."}, threshold=0.5)]`.
3. **`agent/llm/wrapper.py::run_typed`** calls `resolve(task, project_key)`. Rule 5: `backend is DECISIONS and is_eligible(project_key)` → `Route(DECISIONS, JEV, fallback=Route(OLLAMA, OLLAMA_CLASSIFIER_MODEL))`; otherwise `Route(ANTHROPIC, model)`. The degraded-stack guard runs with `signature_axis=False` (the route never touches `anthropic`; `loader_ok` is still required). `effective = sdk_timeout or default_sdk_timeout(DECISIONS)` (3.0 s from `settings.timeouts.decisions_sdk_s`); `budget = sdk_timeout or hard_timeout`.
4. **`agent/llm/backends/decisions.py::call`**: `bound_to_deadline` (a no-op on the primary leg); read `settings.api.typesafe_api_key` (`None` → `LLMCallError(reason="transport")` before any I/O); build the questions from `output_type.model_fields` (`Literal[...]` and `Literal[...] | None` → `choice` with the literal values as criteria keys; `bool` → `noul` with `true`/`false` criteria; every other field is filled with its default, `""` for a required `str`, `None` for a required `X | None`, and `confidence: float` from the answer); `state = prompt` or `system + "\n\n" + prompt`; the envelope's headroom check (`accumulated + 0.001344 <= envelope` else settle and re-reserve; a `Refusal` → `LLMCallError(reason="transport")`); `async with stack.AsyncHTTPClient(timeout=sdk_timeout) as client: response = await client.post(TYPESAFE_DECISIONS_URL, headers={"Authorization": f"Bearer {key}"}, json={"model": JEV, "state": state, "questions": questions})`; non-200 → `transport`; JSON decode failure → `transport`; a question id missing from `answers` → `validation`; a `choice` not among the options → `validation`; `noul` → `bool` at the field's threshold; `confidence` (when the type has that field) = the minimum `confidence` over the `choice` answers, else the probability of the chosen `noul` answer (`noul` for `True`, `1 - noul` for `False`); `output_type.model_validate(values)` (a `ValidationError` → `validation`); `usage.input_tokens × JEV_PRICE_USD_PER_MTOKEN / 1e6` added to the envelope (`input_tokens` missing, or the price constant `None` → the envelope is marked `unknown`); return the instance. `httpx.TimeoutException` → `timeout`; any other `httpx` error → `transport`. `CancelledError` passes through: the `async with` closes the client, the envelope is untouched (no usage was read), and nothing is settled twice. The key never appears in a log line or an error message.
5. **Fallback**: on `LLMCallError` from the decisions leg the wrapper runs the Ollama leg once inside the remaining budget with `deadline=start + budget` (lane A's Data Flow step 6, unchanged), logging `llm_fallback site=routing.needs_response primary=decisions fallback=ollama reason=<reason> elapsed_ms=<int>`. If the Ollama leg fails too, the primary error propagates and the site's fail-safe applies (C1: respond). There is no third leg: a `DECISIONS` site never reaches Anthropic for eligible context, which is the "no single external provider on a hot path" rule with a local backend as the second provider. Abstain-to-fallback on a `choice`: a `Decision` marker may carry `min_confidence`; a `choice` answer whose `confidence` is under it raises `LLMCallError(reason="validation")` and the wrapper's ordinary fallback runs granite on the same inputs, so a low-confidence Jev answer is never the site's answer. The builder sets it per site from the record (Technical Approach).
6. **Output**: the caller receives a `NeedsResponseDecision` with no marker of which leg answered; `llm_route site=routing.needs_response backend=decisions elapsed_ms=<int>` is the evidence.

Comparison flow (`python -m tools.classification_eval --site routing.needs_response --candidate decisions,ollama`): `_run_site` builds the reference arm from the site's `reference` (Haiku for C1 to C11; gemma for C15; the new `"ollama"` value for C12, C13, and C14, whose landed backend is granite); `_candidate_arms` builds `decisions_arm(site_id)` (the decisions leg called directly with a per-run `SpendEnvelope` reserved for `max(0.01, 0.001344 * 2 * n)` and settled once in `finally`, so a leg failure is the arm's own error and never a fallback answer) and `ollama_arm(site_id)`; `compare` runs every arm on the same inputs; `evaluate_bar` applies the six criteria plus `cost` when the reference is on Anthropic; the record is written and its claims attached to case `1ec40086`. The landing decision reads that record: the `decisions` candidate passes and the `ollama` candidate passes (or, for a site whose reference is the ollama arm, the site's latency-only ollama record passes) → the builder edits `backend=Backend.DECISIONS` on the declaration and the doc row, and commits with the record id.

The worker-side sites (C10, C11) and the reflection sites (C14, C15) run in processes with no shared Anthropic semaphore involvement on this route; the envelope is per process, so each of the bridge, worker, and reflection worker holds at most one open one-cent reservation at a time.

## Architectural Impact

- **New dependencies**: none. `httpx` is already a dependency (`pyproject.toml:16`); TypeSafe's SDK is not added (its retry policy is the opposite of the leg's one-attempt contract, and it is a dependency outside the coupled anthropic + pydantic-ai-slim pin set). The coupled pin set is untouched.
- **Interface changes**: `Backend` gains `DECISIONS`; `LLMStack` gains `AsyncHTTPClient: Any` (`httpx.AsyncClient`, imported inside `_load_stack`) so the leg gets its client through the same seam as the other two; `Route` is unchanged; the leg protocol is unchanged (the new leg implements it as it stands); `agent/llm/tasks.py` gains the `Decision` marker (a frozen dataclass used as `Annotated` metadata) and `agent/llm/__init__.py` exports it; `LLMTask` gains no field; `APISettings` gains `typesafe_api_key`; `TimeoutSettings` gains `decisions_sdk_s`; `config/models.py` gains `JEV`, `TYPESAFE_DECISIONS_URL`, `JEV_PRICE_USD_PER_MTOKEN`, `MODEL_INFO[JEV]`; `.env.example` gains the `TYPESAFE_API_KEY` declaration; `tools/classification_eval/core.py::Site.reference` gains the `"ollama"` value and `evaluate_bar` a seventh criterion (`cost`, conditional); `records.py` gains `landing_record(site_id, backend)` and the audit's `DECISIONS` branch; `agent/llm/backends/decisions.py` is new. Sites that land change one word on their declaration plus the `Decision` markers on their output type; C12 additionally builds a per-call output type; C15 additionally gains a second declared task for the span cascade.
- **Coupling**: the decisions leg couples the hot path to one more external service (TypeSafe directly, no proxy), always with a local leg behind it; the router is the only place that knows about the coupling; the meter is consumed through its public functions only.
- **Data ownership**: comparison records and receipts stay on case `1ec40086` in the runner machine's Redis (lane A's convention); the envelope's reservation rows are the meter's own plain Redis keys, one per process per cent of spend.
- **Reversibility**: one-word revert per site (`backend=Backend.OLLAMA` or `ANTHROPIC`) with the record saying why; unsetting `TYPESAFE_API_KEY` on a machine turns every decisions call into an immediate fallback to granite without a code change; removing the leg is deleting one module and three enum/table entries.

## Appetite

**Size:** Medium

**Team:** Solo dev (one builder iterating on the leg and the per-site comparisons), plan critic, PR reviewer applying the per-site bar.

**Interactions:**
- PM check-ins: 1-2 (the real-input sample, Open Question 1; the landing summary before review)
- Review rounds: 1-2 (the reviewer reads the per-site landing summary in the PR body against the bar)

The code is bounded (one leg of about 200 lines, a rule, an arm, a criterion, an audit branch, a probe test, docs). The appetite is in the iteration: fifteen comparison runs, each a few minutes of granite time plus about 0.01 USD of Jev, repeated per site while the builder tunes `Decision` markers and candidate prompts, and the landings that follow. Two build days; a third only if C15's cascade or C12's per-call type is reached.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Lane A on `main` | `git merge-base --is-ancestor 4703bce23 HEAD` | the router, legs, runner, and records this lane extends |
| `TYPESAFE_API_KEY` in the vault `.env` | `.venv/bin/python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('TYPESAFE_API_KEY')"` | the decisions leg and the runner's decisions arm (other machines: 1Password vault `m-valor`, item "TypeSafe API", field `api_key`, via the service account) |
| TypeSafe endpoint reachable | `.venv/bin/python -c "import httpx; assert httpx.post('https://api.typesafe.ai/v1/systemone', json={}, timeout=15).status_code in (401, 403)"` | the probe test and every comparison run (an unauthenticated POST answers 403 `authentication_error` on 2026-09-21, which proves the host answers without spending) |
| Ollama daemon with granite pulled | `.venv/bin/python -c "import httpx; from config.settings import settings; from config.models import OLLAMA_CLASSIFIER_MODEL; tags=httpx.get(settings.models.ollama_host.rstrip('/')+'/api/tags', timeout=5).json(); assert any(m['name']==OLLAMA_CLASSIFIER_MODEL for m in tags['models'])"` | the Ollama fallback arm on every comparison and the fallback leg in production |
| Redis reachable | `.venv/bin/python -c "from utils.redis_client import text_redis; assert text_redis().ping()"` | the meter envelope, the comparison records, the audit |
| `httpx` importable in the venv | `.venv/bin/python -c "import httpx"` | the leg's client |
| Contention check runs | `.venv/bin/python -c "from tools.classification_eval import is_contended; print(is_contended())"` | prints `True` while `com.valor.*` services are loaded; stop them (`./scripts/valor-service.sh stop`) before a latency run so the record carries `contended: false` (Race 3 of the parent plan) |

Run all checks via `python scripts/check_prerequisites.py docs/plans/structured-decision-transport-jev-behind-ollama-fallback.md`.

## Solution

### Key Elements

- **`Backend.DECISIONS` and its constants**: the enum member (value `"decisions"`, the log token); `config/models.py` gains `JEV = "jev-1.13.0"` (pinned, never `jev-latest` or `jev-preview`), `TYPESAFE_DECISIONS_URL = os.environ.get("TYPESAFE_DECISIONS_URL", "https://api.typesafe.ai/v1/systemone")` in the shape of `OPENROUTER_URL` (`:56`), `JEV_PRICE_USD_PER_MTOKEN: float | None = 0.042` (input tokens; output is free; source https://docs.typesafe.ai/models retrieved 2026-09-21; `None` means the price is unknown and every envelope settles `unknown`), and `MODEL_INFO[JEV]` (`provider: "typesafe"`, `context_window: 32000`, `input_cost_per_mtoken: 0.042`, `output_cost_per_mtoken: 0.0`, `price_retrieved_at: "2026-09-21"`, `endpoint: "systemone"`).
- **The credential**: `APISettings.typesafe_api_key: str | None = Field(default_factory=lambda: os.getenv("TYPESAFE_API_KEY"), description=...)` in `config/settings.py`, joined to the `validate_api_keys` validator list (the file's `default_factory` pattern for a flat key on a nested group, `:539`); `.env.example` gains a comment line and `TYPESAFE_API_KEY=ts-****` under the OpenRouter block, unmarked (a credential is required, never `@optional`); the vault `.env` already carries it, other machines take it from 1Password (`m-valor`, "TypeSafe API", `api_key`) through the service account. The leg reads `settings.api.typesafe_api_key` inside `call`; nothing prints, logs, or echoes the value.
- **The `Decision` marker** (`agent/llm/tasks.py`, exported from `agent.llm`): `@dataclass(frozen=True) class Decision: question: str; criteria: Mapping[str, str] | None = None; threshold: float = 0.5; min_confidence: float = 0.0`. Attached as `Annotated[<type>, Decision(...)]` metadata on a `bool` or `Literal` field of an output type. `question` becomes the wire `instructions`; `criteria` values become the rubric strings (examples written into the rubric text); `threshold` is the `noul` cut; `min_confidence` is the abstain floor for a `choice` (an answer under it is a `validation` failure and the fallback leg answers instead). Only the decisions leg reads it; the Anthropic and Ollama legs never see it because pydantic keeps `Annotated` metadata out of the JSON schema (spike-3). A `Literal` or `bool` field with no marker still becomes a question: the field's `description` if it has one, else `"What is the {field name}?"`, with `null` rubrics. This is the mechanism the issue described as `LLMTask.question`, `noul_threshold`, and `decision_options`, moved to where the leg can read it and made per field (Technical Approach, "Reconciliation with the issue body").
- **The decisions leg** (`agent/llm/backends/decisions.py`): the leg protocol as landed; the question builder; `state` from `prompt` and `system`; one `httpx` POST through `stack.AsyncHTTPClient(timeout=sdk_timeout)` with the bearer header; the answer decoder; `LLMCallError` with a `reason` for each of the six failure classes plus the missing-key case and the abstain case; the envelope metering; `slot_timeout` and `max_retries` accepted and unused (no semaphore, no retry: one POST is one attempt, so a 429 or 529 is a fast fall to granite); `deadline` honored through `bound_to_deadline` before the client is built. No `asyncio.wait_for`, no third-party symbol at module scope.
- **`SpendEnvelope`** (in `decisions.py`): `reserve(usd)` once, `fits(bound)`, `add_tokens(input_tokens)`, `mark_unknown()`, `settle()`; the process singleton reserves one cent, checks headroom for the 32k bound before every call, accumulates `input_tokens × JEV_PRICE_USD_PER_MTOKEN / 1e6` per 200, settles exactly and re-reserves when the next call would not fit or the UTC day rolls over (the meter's window is per day; `Reservation.day_key` tells), and settles with `metering="unknown"` if any 200 in the envelope carried no `usage.input_tokens` or the price constant is `None`. A `Refusal` from `reserve` raises `LLMCallError(reason="transport")` with the refusal reason, before any request. The runner arm constructs its own envelope with a per-run amount and settles it in `finally`, matching `OpenRouterGemmaArm`; the record's `cost_per_call_usd` is the same arithmetic per input.
- **Router rule 5** (`agent/llm/router.py`): between rule 2 and rule 3, `task.backend is Backend.DECISIONS`: `is_eligible(project_key)` → `Route(Backend.DECISIONS, JEV, fallback=Route(Backend.OLLAMA, OLLAMA_CLASSIFIER_MODEL))`, else `anthropic`. One late import of `is_eligible` shared with rule 3.
- **Timer**: `TimeoutSettings.decisions_sdk_s` (default 3.0, `ge=0.5`, `le=60.0`, env `TIMEOUTS__DECISIONS_SDK_S`, commented override in `.env.example`); `default_sdk_timeout(Backend.DECISIONS)` reads it per call.
- **Wrapper**: `_LEGS[Backend.DECISIONS] = decisions_leg.call`; nothing else changes (the fallback budget, the guard axis, the two log lines all work as written).
- **Runner**: `decisions_arm(site_id)` in `arms.py` with `JEV_PRICE` (retrieval date 2026-09-21); `_candidate_arms` builds it; `Site.reference` accepts `"ollama"` and `_run_site` builds `ollama_arm(site.id, name="ollama")` as the reference for it; C12, C13, C14 rows flip to `reference="ollama"`; `evaluate_bar` gains `cost` (applied when the record's reference arm is on `anthropic`: candidate `cost_per_call_usd` at or under one tenth of the reference's); `render_report` and `claims_for` print it; `landing_record(site_id, backend)` returns the newest record in which `backend` is a candidate arm, else the newest latency-only record whose reference-free measurement is that backend; `_audit_row` reads the landing record for the declared backend and gains the `DECISIONS` branch (the `decisions` candidate clears the bar, and the fallback is a passing backend: the same record's `ollama` candidate clears the bar, or, when the record's reference is the ollama arm, the site's latency-only ollama record clears its criteria). The `OLLAMA` branch keeps reading a record with an ollama candidate or a latency-only record, so a later decisions comparison at C12 never displaces C12's landing evidence.
- **The probe test** (`tests/unit/test_models.py::test_typesafe_jev_pinned_model_answers`): `integration` marker; `pytest.skip` naming `TYPESAFE_API_KEY` when `settings.api.typesafe_api_key` is `None` (the only skip, and it names the missing credential); one `urllib` POST of a two-word `state` and a single `noul` question to `TYPESAFE_DECISIONS_URL` with a 30 s timeout (about 300 input tokens, 0.00001 USD); `pytest.fail` naming "unreachable" on any exception; then asserts HTTP 200, `body["model"] == JEV`, the answer is `{"type": "noul", "noul": <float in [0, 1]>}`, and `usage.input_tokens` is a positive int. A silent alias re-point, a withdrawn pinned id, or a usage-shape change fails by name; there is no listing to read, so the price is a documented constant re-read at review (Research finding 5).
- **Doctor**: `_check_llm_routing` gains a `decisions_endpoint` row: `passed=False` when a `DECISIONS` site is declared and `settings.api.typesafe_api_key` is `None` (every call would fall to granite), with `fix` naming the vault `.env` and the 1Password item; no network call. The per-site rows already print `decisions (fallback ollama)` through `_describe`.
- **Per-site landing** by the bar, one commit per site with the record id, exactly lane A's protocol. Sites that land also get their `Decision` markers and, where the passing record measured a trimmed candidate prompt, that prompt as the production prompt.
- **C15 cascade** (only if C15's decisions record clears its bar): `PromiseJudgeDecision.answer` carries a `Decision`; on a positive with an empty `span`, the adapter runs a second `run_typed` on a new `PROMISE_SPAN = LLMTask(site="improvement_collect.promise_span", kind=TaskKind.THINKING, backend=Backend.ANTHROPIC, error_cost=ErrorCost.LOW)` with a `PromiseSpan(span: str)` output type and the same prompt, so the span (an extraction, a thinking task) comes from the subscription backend on positives only; a failed span call keeps the positive with `span=""`.
- **C12 per-call type** (only if C12 is reached): `bridge/job_router.py::_classify` builds `output_type = job_route_decision_type(candidate_ids)` per call, a `create_model` subclass of `JobRouteDecision` whose `job_id` is `Annotated[Literal[<ids>] | None, Decision(...)]`; the site's label in `sites.py` becomes `f"{decision}:{job_id if decision == 'bind' and confidence >= JOB_ROUTER_CONFIDENCE_THRESHOLD else ''}"` so the record measures the wrong-bind rate the issue's rejection criterion names.

### Flow

Inbound message → `run_typed(task=<site>, project_key)` → `resolve`: rule 5 → decisions leg: envelope headroom → one POST (3 s timer) → typed answers → `model_validate` → the site's output type → the site's own threshold and fail-safe. On any `LLMCallError` from the leg → the Ollama leg once, inside the budget → the same output type. Both legs failing → the site's fail-safe.

Builder → `python -m tools.classification_eval --site <id> --candidate decisions,ollama` (services stopped) → report and record → tune `Decision` markers and `candidate_prompt`, re-run → passing record → one-word landing plus markers, commit with record id → `--audit` green → PR body landing summary → reviewer applies the bar → approval is the gate.

### Technical Approach

**Question construction, precisely.** For each field of `output_type.model_fields` in declaration order:

| Field annotation | Question | Answer to value |
|---|---|---|
| `Literal[a, b, ...]` or `Literal[...] \| None` | `choice`; criteria keys are the literal values as strings (a non-string literal is a `ValueError` at build time, named by field; more than 255 is one too); each value's rubric from `Decision.criteria[value]`, else `null`; `instructions` from `Decision.question`, else `field.description`, else `"What is the {name}?"` | the `choice` string, cast back to the literal's type; a `confidence` under `Decision.min_confidence` → `validation` (abstain to the fallback leg) |
| `bool` | `noul`; criteria `{"true": ..., "false": ...}` from `Decision.criteria` (keys `"true"`/`"false"`), else `{"true": "yes", "false": "no"}`; instructions as above | `noul >= Decision.threshold` (default 0.5) |
| `float` named `confidence` | no question | the minimum `confidence` over the `choice` answers, else the probability of the chosen `noul` answer |
| `str`, `str \| None`, any other type | no question | the field default; `""` for a required `str`; `None` for a required optional; a required field of any other type is a `ValueError` at build time, named by field, so a site whose type the leg cannot fill fails at the first call in the builder's hands rather than silently |

A type with no `Literal` and no `bool` field raises `ValueError` at build time (the leg has nothing to ask). The builder is a pure function `questions_for(output_type) -> dict` with its own unit tests, and `decode_answers(output_type, answers) -> dict` is its inverse, so both are tested without a transport.

**`state`.** `prompt` alone, or `f"{system}\n\n{prompt}"` when `system` is given (the API has no system field; C9 and C10 pass one). The API accepts an object or array `state` too; the leg sends a string, because every site's prompt is one, and a 32k-token `state` bound is the documented ceiling (Research finding 5). The parent plan's finding that Jev "reads a description and decides" holds with a prompt-shaped state (spike-1, finding 3), and the runner's `candidate_prompt` is where the builder trims a site's state toward the bare message plus the context the question needs.

**Failure classes and `reason`.** Non-200 → `transport` (message carries the status and the first 200 characters of `detail.message`, the body shape observed for 400, 401, and 403; a body without it contributes nothing); JSON decode error → `transport`; a question id absent from `answers` → `validation`; a `choice` outside the options → `validation`; a `choice` under `min_confidence` → `validation`; `model_validate` failure → `validation`; `httpx.TimeoutException` → `timeout` (the SDK-level timer, the only timer); any other `httpx.HTTPError` → `transport`; meter `Refusal` → `transport` with the refusal reason; `settings.api.typesafe_api_key is None` → `transport`. The `Reason` literal is unchanged. Every failure logs once at ERROR with the site-free leg prefix `[agent.llm] decisions leg <reason> for model=<model>: <detail>` (the wrapper adds the site on `llm_fallback`); the bearer value is never part of any message.

**Metering.** `SpendEnvelope(project_key="valor", purpose="structured_decision", case_id=CASE_ID)`; `ENVELOPE_USD = 0.01`; `CALL_BOUND_USD = 32000 * 0.000000042`. Before a request: if no reservation or `accumulated + CALL_BOUND_USD > envelope` or `day_key != today`: `settle()` (no-op when nothing is reserved) then `reserve(ENVELOPE_USD)`. After a 200: `add_tokens(usage.input_tokens)` (cost = tokens × `JEV_PRICE_USD_PER_MTOKEN` / 1e6, output tokens free) or `mark_unknown()` when `input_tokens` is absent or the price constant is `None`. `settle()` calls `paid_inference_meter.settle(project_key, reservation_id, accumulated, metering="exact" | "unknown")` and clears state. The process-level singleton is module state in `decisions.py` guarded by the event loop (the meter calls are synchronous Redis round trips of about a millisecond, taken once per ~500 calls at the observed 400 input tokens); a process that exits with an open envelope leaves a reservation the reconcile pass receipts as `unknown` at one cent on the next day (charter §8: the uncertain cent is charged, never zeroed). The purpose `structured_decision` is not `rsi`, so it never counts against the RSI pool; it is visible in `valor-improve budget` receipts like `sdlc_review`. **Expected spend of the full comparison**: at the observed 300 to 600 input tokens per call (0.000013 to 0.000025 USD), fifteen sites × about three iterations × about 300 calls (accuracy pass plus the concurrency-1 and concurrency-4 latency passes) is about 13,500 calls, about 0.30 USD in total; the per-run reservation `max(0.01, CALL_BOUND_USD × 2 × n)` bounds one run at the 32k ceiling (about 0.80 USD for n = 300) and the whole comparison at about 18 USD if every state were at the ceiling, which no site's prompt approaches. Two build days at well under charter §8's 10 USD per day.

**The landing rule for a `DECISIONS` site.** The site's landing record (the newest record with a `decisions` candidate) shows the decisions arm clearing all seven criteria (`agreement` at the tier bar, `p95_c4` within budget, `contended: false`, `error_rate` at or under 2%, `n`, `n_real`, and `cost` at Haiku-backed sites), and the Ollama fallback is a passing backend: the same record's `ollama` candidate clears its six, or, where the record's reference is the ollama arm (C12, C13, C14), the site's latency-only ollama record clears its criteria. This is the plan's reading of the issue's "only if its lane-A record shows granite clearing the bar": the record that matters is the one measured on the same inputs as the decisions arm, and a new run on a machine with enough real traffic can clear the `n_real` and `p95_c4` misses lane A's machine produced. The audit applies it mechanically; the reviewer reads the same numbers in the PR body.

**Rejection verdict.** If, over the comparison runs, the decisions arm's non-200 rate exceeds 2% in aggregate, or its agreement is under 80% at every Haiku-backed site, or C12's single threshold cannot be set without raising the wrong-bind rate over granite's, the builder records `Jev rejected as a backend` as a claim on the case investigation with the numbers, lands no site, and the PR ships the leg, the rule, the arm, the records, and the docs with that verdict in the taxonomy doc's Lane C Outcome. The leg stays: a rejected backend with zero declarations costs nothing and the next model on the endpoint is a slug change.

**Reconciliation with the issue body** (the upstream notice of 2026-09-19 asked for these):

1. Leg signature: this lane implements the landed protocol including `deadline` and `stack`.
2. Runner: the `decisions` arm, `Site.reference = "ollama"`, the `cost` criterion, `landing_record`, and the audit branch are in scope; "unchanged" did not hold.
3. Landing rule: read as above; the landable set is not empty by construction, and it is bounded by the real-input sample the comparison can draw (Open Question 1).
4. The four `LLMTask` fields become the per-field `Decision` marker: the leg never receives the task, one `question` cannot describe `IntentDecisionWithRecall`'s two decision fields, `decision_options` are per call and belong on a per-call type, and `thresholds` (per-backend confidence thresholds) cannot be applied by a site that cannot tell which leg answered (lane A's deliberate design). A site keeps its one threshold constant; the builder tunes it on the record with both arms present, and C12's rejection criterion covers the case where one value cannot serve both. The marker's `min_confidence` is the abstain-to-fallback floor the issue's confidence-vector rationale asks for: it lives on the decisions leg alone, so the fallback leg's answer stands on its own threshold.
5. Metering: the one-cent envelope replaces per-call reserve/settle, which would meter nothing and write an evidence row per call (spike-4); the native response carries tokens and no cost, so the envelope multiplies `input_tokens` by the pinned price and settles `unknown` when either is missing.
6. Transport and credential (Tom, 2026-09-21, lineage row `cdea91a2`): the issue was written against OpenRouter's `/api/alpha/decisions` proxy with `OPENROUTER_API_KEY`; a direct TypeSafe key now exists, so the leg targets `https://api.typesafe.ai/v1/systemone` with `TYPESAFE_API_KEY`, read through a new `settings.api.typesafe_api_key` populated by `default_factory` (the flat-key pattern already in `config/settings.py`). One transport, no proxy path kept. The OpenRouter-specific pieces the issue named (the per-model listing probe, `usage.cost`, the `instructions.question`/`what` object shapes) are replaced by their native equivalents in Research findings 1, 5, and 6.

**Import safety and the test seam.** `LLMStack.AsyncHTTPClient = httpx.AsyncClient` (imported in `_load_stack`). Tests do `dataclasses.replace(real_stack, AsyncHTTPClient=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw))` on the wrapper's `_load_stack` seam, with recorded responses from spike-1 as fixtures (the 200 bodies, the 400 and 401 `detail` bodies, a 200 whose `usage` lacks `input_tokens`, a 200 with a `choice` outside the options, a 200 with a `choice` under `min_confidence`, a 200 missing a question id, invalid JSON, a raised `httpx.ReadTimeout`). The fake transport also asserts the request: the bearer header is present, `model == JEV`, and the questions match `questions_for(output_type)`.

**Thresholds at the sites.** `JOB_ROUTER_CONFIDENCE_THRESHOLD`, `INTENT_CONFIDENCE_THRESHOLD`, `TEAMMATE_CONFIDENCE_THRESHOLD` stay per site; a site that lands on `DECISIONS` keeps one value that its record shows working for both arms.

**Not touched.** `Route`, the wrapper's fallback arithmetic, the guard, `LLMTask`'s five fields, the site walk, `run_typed`'s signature, every thinking site, C16, `email_cs.*`, `#3420`'s files.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The decisions leg has exactly one `except Exception` (around the POST and decode, mapped through the failure table) and it never swallows: every branch logs once at ERROR and raises `LLMCallError` with a `reason`. `tests/unit/test_llm_backend_decisions.py` drives each of the eight failure classes (non-200, invalid JSON, missing answer id, unknown choice, choice under `min_confidence`, `model_validate` failure, `httpx.ReadTimeout`, meter `Refusal`) plus the missing-key case through the `AsyncHTTPClient` seam and asserts the `reason`, the one ERROR log line via `caplog`, that the log line and the exception message contain no substring of the key, and that no client outlived the call.
- [ ] `SpendEnvelope.settle()` is the only place a meter write can fail; a Redis error there is caught, logged at WARNING, and does not fail the call (the answer was already produced; charter §8 is honored by the reconcile pass receipting the open reservation). Test: a settle whose `paid_inference_meter.settle` raises leaves the call's result intact and logs.
- [ ] The wrapper's fallback path is lane A's and has its tests; this lane adds the decisions → ollama case to `tests/unit/test_llm_wrapper.py` (primary `transport` error, fallback answers; primary `timeout` with the budget nearly spent, `llm_no_fallback` logged and the primary error propagates).
- [ ] The C15 cascade's span call failure keeps the positive with `span=""` and logs at WARNING (test in `tests/unit/test_improvement_evidence.py`, only if the cascade ships).

### Empty/Invalid Input Handling
- [ ] `run_typed` already rejects an empty or whitespace prompt before routing; the leg receives a non-empty prompt. `system=""` is treated as absent (state is the prompt alone). Test both.
- [ ] `questions_for(output_type)` raises `ValueError` naming the field for: a type with no `Literal` and no `bool` field; a `Literal` with a non-string member; a required field the leg cannot fill. Tests for each, plus the happy shapes of every classification output type on `main` (parametrised over `declared_sites()` filtered to classification, importing each site's output type by the declaration's module and the `run_typed` call's second argument found by AST, so a new site with an unaskable type fails here before any comparison runs).
- [ ] `decode_answers` handles `answers == {}` (missing id → `validation`), a `noul` exactly at the threshold (`>=` → `True`), a `choice` exactly at `min_confidence` (`>=` passes), a `choice` answer whose `probabilities` are absent (confidence falls back to the answer's `confidence`, else 0.0), and a `usage` block without `input_tokens` (envelope marked unknown, result still returned).
- [ ] `settings.api.typesafe_api_key` with the env var unset is `None` (no exception, the doctor row fires); with a value shorter than 10 characters the shared validator rejects it like the other keys; the value never appears in `repr(settings)` output beyond what the sibling keys already expose.
- [ ] The comparison runner's `parse_candidates` rejects `decisions` only while the enum lacks it (no longer); the `cost` criterion with a reference `cost_per_call_usd` of 0.0 (a gemma or ollama reference) is skipped, never a division.

### Error State Rendering
- [ ] `render_report` prints `cost` among the failing criteria with the threshold line `cost/call $x > one tenth of reference $y`; test through the existing report fixture.
- [ ] `--audit` prints, for a `DECISIONS` landing whose fallback misses, `MISS fallback` with the failing criteria of the ollama arm, and exits 1; test through the existing audit fixtures.
- [ ] `tools/doctor`'s `decisions_endpoint` row renders the missing-key case with the fix text; test with the environment variable removed and one `DECISIONS` declaration injected through the doctor's site list seam.
- [ ] The probe test's two failure messages ("unreachable" vs "not listed") are distinct strings, as in the sibling.

## Test Impact

- [ ] `tests/unit/test_llm_tasks.py::TestEnums::test_lane_a_backends_are_anthropic_and_ollama`: UPDATE: the `Backend` value set gains `"decisions"` (rename the test to name the three members; lane B adds a fourth on its own branch).
- [ ] `tests/unit/test_llm_router.py::_expected` and `TestFourRules`: UPDATE: `_expected` gains the `DECISIONS` branch (`valor` → `Route(DECISIONS, JEV, fallback=Route(OLLAMA, OLLAMA_CLASSIFIER_MODEL))`, any other key → `ANTHROPIC_ROUTE`); the class gains `test_rule_5_eligible_decisions_carries_an_ollama_fallback`, `test_rule_5_ineligible_decisions_fails_closed_to_anthropic`, and `test_only_the_local_rules_consult_eligibility` (the spy sees one call for a `DECISIONS` task too). The module docstring's "four rules" becomes five.
- [ ] `tests/unit/test_llm_router_eligibility.py`: UPDATE: the `OLLAMA_CLASSIFICATION` list becomes `LOCAL_CLASSIFICATION` (`backend in {OLLAMA, DECISIONS}`); the client-key case asserts the Anthropic leg for `DECISIONS` sites, the `valor` case with `gh` unavailable asserts the decisions leg with the Ollama leg as fallback (both faked at `_LEGS`).
- [ ] `tests/unit/test_llm_wrapper.py`: UPDATE: the `_LEGS` table tests gain one case, "decisions primary raises `LLMCallError(reason="transport")`, the Ollama fallback answers inside the budget"; and a new `test_legs_table_covers_every_backend` (`set(_LEGS) == set(Backend)`).
- [ ] `tests/unit/test_classification_eval.py`: UPDATE: `parse_candidates` accepts `decisions`; `_candidate_arms` builds the decisions arm; `evaluate_bar` gains the `cost` criterion cases (Haiku reference: candidate at or under one tenth passes, over misses; Ollama or gemma reference: no `cost` criterion); `render_report` prints the `cost` threshold line; `_audit_row` gains the `DECISIONS` branch cases (decisions candidate passes and the same record's ollama candidate passes → PASS; ollama candidate misses → MISS naming `fallback`; reference on ollama plus a passing latency-only ollama record → PASS) and the `OLLAMA` branch keeps passing when the newest record is a decisions comparison whose reference is the ollama arm (the landing record is the newest record carrying the landed backend as a candidate or a latency-only measurement).
- [ ] `tests/unit/test_llm_task_taxonomy.py::test_taxonomy_doc_row_matches_declaration`: no code change; it reads the `Backend` column of the site table in `docs/features/llm-task-taxonomy.md`, so every site that lands on `DECISIONS` needs its doc row edited in the same commit as its declaration.
- [ ] `tests/unit/test_settings.py`: UPDATE only if it pins the `TimeoutSettings` or `APISettings` field lists; `decisions_sdk_s` joins `TimeoutSettings` and `typesafe_api_key` joins `APISettings`.
- [ ] `tests/unit/test_env_completeness*.py` (whichever file exercises `check_env_completeness` against `.env.example`): no code change; the new `TYPESAFE_API_KEY` declaration is required, so a machine without it in the vault `.env` gets the fail-closed warning by design.
- [ ] `tests/unit/test_doctor*.py` (whichever file covers `_check_llm_routing`): UPDATE: the section gains a `decisions_endpoint` row; the existing row-count or name-set assertions include it.
- [ ] `tests/unit/test_llm_backend_decisions.py`: CREATE (greenfield: the leg's unit tests with recorded responses through the `LLMStack.AsyncHTTPClient` seam and `httpx.MockTransport`).
- [ ] `tests/unit/test_models.py`: CREATE `test_typesafe_jev_pinned_model_answers` beside `test_openrouter_gemma4_free_is_listed`; `_configured_openrouter_ids()` keeps excluding `JEV` and `TYPESAFE_DECISIONS_URL` (neither is an `OPENROUTER_*` name), so the catalog-warning test stays quiet about a model that is not an OpenRouter id.
- [ ] `tests/unit/test_job_router.py`: UPDATE only if C12 lands on `DECISIONS` (the per-call output type carries the candidate job ids as `Literal` options; the existing fakes return `JobRouteDecision` instances and keep working because the per-call type subclasses it).
- [ ] `tests/unit/test_improvement_evidence.py`: UPDATE only if C15 lands on `DECISIONS` (the cascade adds a second `run_typed` call on positives; the transport fakes that return a positive with an empty `span` exercise it).

## Rabbit Holes

- **Routing Jev through PydanticAI** (pydantic/pydantic-ai#8552). A custom `Model` subclass would let the leg reuse `Agent`, at the cost of shoehorning `{state, questions}` into a chat-shaped request. One `httpx` POST is smaller and the wire shape is pinned by probe. Out.
- **OpenRouter's `/api/alpha/decisions` proxy.** The issue's original transport: one more provider in the path, an alpha route with no deprecation policy, a stricter body (`instructions` required, `{what, examples}` rubric objects), and a key shared with unrelated uses. The direct key makes it unnecessary; the leg has exactly one transport. Out.
- **TypeSafe's SDK.** Automatic retries with default policies (the docs) against a leg whose contract is one attempt inside a 3 s budget with granite behind it; one more dependency outside the coupled pin set. Out.
- **Jev `score` questions, object or array `state`.** Documented, unexercised by the probe, and unneeded by any site (no classification site has an ordinal output). Out (parent plan).
- **Per-backend confidence thresholds.** Requires the site to know which leg answered, which lane A deliberately hides. A site keeps one threshold; the record shows it working for both arms or the site does not land. Out (Reconciliation 4).
- **Chaining a third leg (decisions → ollama → anthropic).** The wrapper runs one fallback by design and the budget arithmetic is written for one; a `DECISIONS` site with both legs down takes its fail-safe, exactly as an `OLLAMA` site with both legs down does today. Out.
- **Fixing `settings.api.openrouter_api_key`.** A real defect (the field is never populated by the flat key) but every reader in the repo already bypasses it; the new `typesafe_api_key` field uses the `default_factory` pattern instead and leaves its sibling alone (Open Question 3). Out of this lane.
- **Batching several sites' questions into one Jev call** (C1, C2, C3 all read the same message). The sites are called from different places with different fail-safes and budgets; sharing one call couples them. Out.
- **Warming the envelope or the HTTP connection at startup.** The first call in a process pays one reservation round trip (about a millisecond) and one TLS handshake (spike-1's first call: 925 ms against 517 ms warm); both sit inside a 3 s budget with granite behind them. A persistent client would violate the per-call-client convention every leg follows. Out.
- **Growing the real-input corpus by any means other than replaying a saved draw.** Synthesising "real" messages defeats Risk 7; the runner's `--inputs` replay of a `--save-inputs` file from a machine that holds the traffic is the sanctioned path. Out.

## Risks

### Risk 1: The comparison cannot clear `n_real` on the build machine
**Impact:** Every inbound-shaped site holds (as in lane A), whatever Jev's agreement, and the lane's per-site outcome is decided by a sampling artefact rather than by the model.
**Mitigation:** Open Question 1 asks for a replayable sample (`--save-inputs` on a machine with the traffic, `--inputs` here) or a comparison run on that machine. The plan's success criteria count a complete set of records with named failing criteria as done, so the lane ships either way; C11 and C14 draw real inputs of their own shape and can land on this machine alone.

### Risk 2: Jev's agreement is under the bar everywhere (the 67.8% prior)
**Impact:** Zero landings; the leg and the rule ship with no declaration using them.
**Mitigation:** That is an acceptable, recorded outcome (Rejection verdict in Technical Approach). The `Decision` markers give the builder real leverage (criteria descriptions with examples are what the model is built to read; the probe answered the routing bucket at probability 1.0 with them and 0.72 without). The builder iterates on markers and `candidate_prompt` per site within the appetite before recording a miss.

### Risk 3: The external endpoint fails or slows on the hot path
**Impact:** C1 to C4 sit in the message path; a 3 s stall per message would be felt.
**Mitigation:** The single SDK-level timer at 3.0 s (`decisions_sdk_s`, an operator lever; the native endpoint answered in 1.0 to 1.5 s on every probe, so the timer has about 2× headroom rather than the 5× the proxy's numbers suggested), the granite fallback inside the remaining budget, and no retry on the leg. The `llm_fallback` grep in the infra doc is the steady-degraded-state signal; lever 0 in the Rollback section (unset the key, or `TIMEOUTS__DECISIONS_SDK_S` at its floor) turns the leg off fleet-wide without a code change.

### Risk 4: Client context reaches TypeSafe
**Impact:** Charter §7 breach.
**Mitigation:** Rule 5 fires only for `is_eligible(project_key)`, the same fail-closed read as rule 3 (`valor` pinned, cache-only for every other key, `None` → ineligible). `tests/unit/test_llm_router_eligibility.py` feeds a client-mapped message through every `DECISIONS` site and asserts the Anthropic leg; the runner's `real_messages` refuses a non-public project (lane A).

### Risk 5: The envelope double-counts or leaks spend
**Impact:** Wrong receipts on the case; charter §8 accounting off by cents.
**Mitigation:** The meter's Lua CAS makes a second settle of one reservation a no-op; the envelope settles at most once per reservation and the tests assert one `settle` call per envelope across a cancelled call, a failed call, a day rollover, and a headroom-driven roll. An open envelope at process exit is receipted as `unknown` at one cent by the existing reconcile sweep, which is the charter's "uncertain metering is not zero" rule, not a leak.

### Risk 6: Lane B lands first (or second) and the enum, router, and tables conflict
**Impact:** A rebase with conflicts in `tasks.py`, `router.py`, `wrapper.py`, `backends/__init__.py`, `__main__.py`, two tests, two docs.
**Mitigation:** Every touch is an added member, branch, row, or dict entry; the builder rebases on `main` before opening the PR and again before merge, and the plan's Verification rows re-run after the rebase. The two plans name the same files so the critique can see the overlap.

### Risk 7: A recorded-response fixture drifts from the live shape
**Impact:** Unit tests stay green while production decodes fail.
**Mitigation:** The listing probe is live (fails on delisting or a pricing/context change); the comparison runs are live and every 200 passes through the same decoder the unit tests exercise; the fixtures are the spike-1 bodies verbatim with their retrieval date in a comment.

## Race Conditions

### Race 1: Two concurrent calls in one process roll the envelope at the same time
**Location:** `agent/llm/backends/decisions.py::SpendEnvelope.ensure_headroom`
**Trigger:** Concurrency-4 latency pass in the runner, or C1/C2/C3 firing on one message in the bridge, all finding the envelope one call short of its cap.
**Data prerequisite:** One reservation id per envelope; accumulated cost read and reset atomically with the settle.
**State prerequisite:** Single event loop per process (true for the bridge, the worker, the reflection worker, and the runner).
**Mitigation:** The roll is synchronous from the first `await`-free check to the new reservation id (the meter calls are blocking Redis round trips with no `await` in between), so no other coroutine interleaves; the second caller sees the fresh envelope. A test runs four concurrent calls through a fake meter with the envelope one bound short and asserts exactly one settle and one reserve.

### Race 2: The Ollama fallback queues behind the runner's or the bridge's own granite traffic past the budget
**Location:** `agent/llm/wrapper.py` fallback path, `agent/llm/backends/ollama.py`
**Trigger:** The decisions leg times out at 3 s, the wrapper computes the remainder, the Ollama leg waits on a busy daemon.
**Data prerequisite:** `deadline = start + budget` passed to the fallback leg.
**State prerequisite:** The daemon at `OLLAMA_NUM_PARALLEL=4`.
**Mitigation:** Lane A's arithmetic unchanged: the fallback leg's timer is `min(local_typed_hard_s, budget - elapsed)`, the deadline re-check refuses to build a client under 0.5 s, and under a 3 s `sdk_timeout` with the primary having spent it all the fallback is skipped with `llm_no_fallback` and the site's fail-safe applies. A `DECISIONS` site with a 3 s budget therefore gets granite only when Jev fails fast (non-200, refusal, missing key), and times out to its fail-safe when Jev stalls; the record's `p95_c4` for the ollama arm tells the builder whether that is acceptable per site.

### Race 3: The comparison's latency pass shares Jev's rate limit with live bridge traffic
**Location:** `tools/classification_eval/arms.py::decisions_arm`
**Trigger:** A comparison run on a machine whose bridge is serving `DECISIONS` sites.
**Data prerequisite:** none
**State prerequisite:** `is_contended()` false (services stopped), lane A's Race 3 rule.
**Mitigation:** The runner already stamps `contended: true` when any `com.valor.*` service is loaded and the bar refuses such a record. TypeSafe publishes no rate-limit figure (429 "use exponential backoff", 529 "overloaded") and the probe saw neither at concurrency 1; a 429 or 529 on the decisions arm is counted as an arm error (it is one), and an error rate over 2% is a miss the record names, which is the correct reading of "the provider throttled us at concurrency 4".

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3420] Lane B: the `LOCAL_ZERO_SHOT` backend (GLiClass ONNX). This lane adds nothing under that name and touches no `onnxruntime` extra.
- [SEPARATE-SLUG #3422] The emoji reaction choice as a classification site; the embedding path stays as it is.
- [SEPARATE-SLUG #3177] Any change to the RSI controller, the meter's pool arithmetic, or the case substrate; this lane consumes `reserve`/`settle`/`record_claims` and adds no controller logic.
- [EXTERNAL] Drawing a real `valor` input sample on a machine that holds the traffic (`--save-inputs`) and handing the file to the builder, or running the comparisons on that machine. The build machine holds 12 real messages; only a human with access to the bridge machine can produce the sample (Open Question 1).
- [EXTERNAL] Populating `TYPESAFE_API_KEY` in the vault `.env` of every machine that will run a `DECISIONS` site (1Password `m-valor`, item "TypeSafe API", field `api_key`, through the service account); the doctor row and the env completeness check report its absence, the leg falls back to granite without it.

Nothing else is deferred: the leg, the rule, the timer, the runner arm, the criterion, the audit branch, the probe test, the doctor row, the comparisons, the landings, the rejection verdict if earned, the C15 cascade and the C12 per-call type when their sites are reached, and the docs are all in scope.

## Update System

No update script or skill changes. `httpx` is already installed on every machine (`uv sync` has carried it since before lane A); no new extra. Two new environment keys: `TYPESAFE_API_KEY`, a required credential declared in `.env.example` (the env completeness check warns on every machine whose vault `.env` lacks it, and the launchd plist merge at `scripts/update/service.py:303` carries it from the vault `.env` into every service plist like its siblings); and `TIMEOUTS__DECISIONS_SDK_S`, a commented override with a default in code, carried only where an operator sets it. No Popoto model changes, so no migration in `scripts/update/migrations.py`. After merge the usual `./scripts/valor-service.sh restart` (or fleet `/update`) picks up the new leg; the taxonomy doc's Lane C Outcome names the deploy step and the 1Password item for machines that still need the key.

## Agent Integration

No new CLI entry point in `pyproject.toml [project.scripts]`: the runner stays `python -m tools.classification_eval` (developer tooling, lane A's decision), and the leg is reached only through `run_typed`. The bridge imports nothing new: `agent/llm/wrapper.py` imports the leg module, and every site keeps its `run_typed` call. Integration tests: `tests/unit/test_llm_router_eligibility.py` drives a `valor` message and a client-mapped message through every `DECISIONS` site's wrapper call with both legs faked at `_LEGS`; `tests/integration/test_bridge_routing_project_key.py` (lane A) keeps pinning the three hot-path `project_key` resolutions, which is what makes rule 5's eligibility read correct at the entry point.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/llm-task-taxonomy.md`: the `backend` field row names `Backend.DECISIONS`; the Router Rules table gains rule 5 (`backend == DECISIONS and is_eligible` → `Route(DECISIONS, JEV, fallback=Route(OLLAMA, OLLAMA_CLASSIFIER_MODEL))`) ahead of rule 3 and its "otherwise" row; the Acceptance Bar gains the `cost` criterion and the DECISIONS landing rule (the decisions arm clears every criterion and the Ollama fallback is a passing backend on the same record or on the site's latency-only record); the site table's `Backend` column changes for every site this lane lands (the parity test reads it); a new `## Lane C Outcome` section with the per-site results table (agreement, p95@4, cost per call, error rate, failing criteria, record id) and the rejection verdict if one was recorded; the `## Tests` paragraph names `tests/unit/test_llm_backend_decisions.py`.
- [ ] Update `docs/features/nonharness-llm-wrapper.md`: a `### The decisions leg (backends/decisions.py)` subsection under "The Backend Legs" (state and question construction from the output type, the `Decision` field marker, the noul threshold, the reason fields, the envelope metering, the six failure classes and their `reason` values, the SDK timer); the fallback-budget section's example gains the decisions → Ollama shape; "Adding a New Site" shows a `Decision`-annotated field.
- [ ] Update `docs/infra/llm-task-routing.md`: a new `## Decisions Endpoint` section (`https://api.typesafe.ai/v1/systemone`, model id pinned to `jev-1.13.0` and why the aliases are avoided, pricing `0.042 USD per million input tokens, output free, 32k-token state` from https://docs.typesafe.ai/models with retrieval date 2026-09-21, the `TYPESAFE_API_KEY` requirement on every machine that runs a `DECISIONS` site with the 1Password item and what happens without it, the `structured_decision` meter purpose and the token-metered one-cent envelope, the `llm_route ... backend=decisions` and `llm_fallback ... primary=decisions fallback=ollama` greps); the Rollback section gains lever 0 for this lane (unset the key or set `TIMEOUTS__DECISIONS_SDK_S=1`: every call falls to granite; then the one-word `backend` edit).
- [ ] Update `docs/features/config-timeout-catalog.md`: the `TimeoutSettings` table gains `decisions_sdk_s` (3.0 s, `TIMEOUTS__DECISIONS_SDK_S`, the decisions leg's single SDK-level timer).
- [ ] Update `docs/features/README.md` only if a new feature page is created (none planned; the three pages above exist).

### Inline Documentation
- [ ] Module docstring on `agent/llm/backends/decisions.py` in the shape of `ollama.py`'s: the wire shape, the question builder, the metering envelope, the import-safety contract.
- [ ] `agent/llm/router.py` docstring: five rules; the lane-C sentence that reserved rule 5 is replaced by the rule itself.
- [ ] `agent/llm/tasks.py` docstring: the `Decision` marker's contract (which field types it may annotate, defaults, how the decisions leg reads it, that the Anthropic and Ollama legs ignore it because pydantic keeps `Annotated` metadata out of the JSON schema).
- [ ] `tools/classification_eval/__init__.py` and `arms.py` docstrings: the decisions candidate arm, the `cost` criterion, the landing-record selection rule.

## Success Criteria

- [ ] `agent/llm/backends/decisions.py` implements the landed leg protocol against `https://api.typesafe.ai/v1/systemone` with `settings.api.typesafe_api_key`; `tests/unit/test_llm_backend_decisions.py` covers the success path with a recorded response, the eight failure classes plus the missing key (each with its `reason` and one ERROR log line that carries no fragment of the key), the question builder over every classification output type on `main`, the decoder's edge cases, and the envelope (token-priced accumulation, `unknown` on a missing `input_tokens` or a `None` price, one settle per reservation across a cancelled call, a failed call, a headroom roll, a day rollover, four concurrent callers).
- [ ] `TYPESAFE_API_KEY` is declared in `.env.example` (unmarked, a required credential) and read only through `settings.api.typesafe_api_key`; `grep -rn "TYPESAFE_API_KEY" agent/ tools/` finds no direct environment read outside `config/settings.py`.
- [ ] `resolve` has rule 5; `tests/unit/test_llm_router.py` covers the eligible route with its Ollama fallback and the ineligible route to Anthropic; the table-driven test passes over every declaration; `tests/unit/test_llm_router_eligibility.py` proves client context never reaches the decisions leg.
- [ ] `default_sdk_timeout(Backend.DECISIONS)` reads `settings.timeouts.decisions_sdk_s` (3.0); `.env.example` and the timeout catalog carry the key.
- [ ] `tests/unit/test_models.py::test_typesafe_jev_pinned_model_answers` passes live and fails by name on an alias re-point, a withdrawn pinned id, or a usage-shape change (`integration` marker, fail-closed on network error, skips only by naming a missing `TYPESAFE_API_KEY`).
- [ ] `python -m tools.classification_eval --site <id> --candidate decisions,ollama` runs for every eligible site (C1 through C15); each record carries agreement with CI, p50/p95 at concurrency 1 and 4, cost per call with the price's retrieval date, error rate, `n`/`n_real`, `contended: false`; every record's claims are on case `1ec40086ca1d422e90ef747775ff7f64`; the per-site landing summary is in the PR body.
- [ ] Every site declaring `backend=Backend.DECISIONS` has a landing record whose decisions arm clears all seven criteria and whose Ollama fallback is a passing backend; its route for `valor` carries `fallback.backend is Backend.OLLAMA`; its doc row says `decisions`; `python -m tools.classification_eval --audit` exits 0.
- [ ] Every site that holds names its failing criteria in its record and in the Lane C Outcome table; a rejection verdict, if earned, is a claim on the case with the numbers.
- [ ] The C15 cascade ships only with a passing C15 record; the C12 per-call type ships only with a passing C12 record.
- [ ] No `MODELS__*` switch, no shadow route, no TypeSafe or `openrouter` SDK, no OpenRouter path in the leg, no new `LLMTask` field, no `asyncio.wait_for` in the leg, no third-party import at module scope in `agent/llm`.
- [ ] Tests pass (`/do-test`), lint and format clean.
- [ ] Documentation updated (`/do-docs`): the four pages in Documentation.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead never builds directly; it deploys team members and coordinates.

### Team Members

- **Builder (leg and router)**
  - Name: decisions-builder
  - Role: `Backend.DECISIONS`, constants, timer, the `Decision` marker, the leg with its envelope, the wrapper entry, the router rule, the doctor row, the probe test, and their unit tests
  - Agent Type: builder
  - Domain: async/concurrency (the envelope's roll, `CancelledError` pass-through), untrusted-input (the endpoint's error bodies are data)
  - Resume: true

- **Builder (runner and landings)**
  - Name: comparison-builder
  - Role: the decisions arm, `Site.reference = "ollama"`, the `cost` criterion, `landing_record`, the audit branch, the fifteen comparison runs, the per-site landings with their `Decision` markers and candidate prompts, the C15 cascade and C12 per-call type when reached, the rejection verdict if earned
  - Agent Type: builder
  - Domain: Redis/Popoto data (records and claims through the ORM and the meter's public functions only)
  - Resume: true

- **Validator (lane C)**
  - Name: decisions-validator
  - Role: runs the Verification table, re-derives every landing from its record with `evaluate_bar` and the fallback rule, checks each `DECISIONS` declaration's doc row and route, confirms the anti-criteria
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: decisions-documentarian
  - Role: the four pages in Documentation plus the module docstrings
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

**Tier 1 (default choices):** `builder`, `validator`, `code-reviewer`, `test-engineer`, `documentarian`, `plan-maker`, `frontend-tester`.

**Domain expertise:** no standing specialist pool. The `Domain:` lines above name the rules to paste from `DOMAIN_FRAMING.md` into each builder's assignment.

## Step by Step Tasks

Commit early on `session/sdlc-3421`: one commit per task below at minimum, one per landed site in task 6.

### 1. Enum, constants, timer, marker
- **Task ID**: build-taxonomy-additions
- **Depends On**: none
- **Validates**: `tests/unit/test_llm_tasks.py`, `tests/unit/test_settings.py`, `tests/unit/test_llm_task_taxonomy.py`
- **Informed By**: spike-3 (the marker stays out of the JSON schema), Research finding 4 (pricing and context)
- **Assigned To**: decisions-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `Backend.DECISIONS = "decisions"`; update the docstring's lane sentence; update the enum pin test.
- Add `Decision` (frozen dataclass: `question`, `criteria`, `threshold=0.5`, `min_confidence=0.0`) to `agent/llm/tasks.py` with its contract in the module docstring; export from `agent/llm/__init__.py`.
- Add `JEV = "jev-1.13.0"`, `TYPESAFE_DECISIONS_URL` (env override like `OPENROUTER_URL`), `JEV_PRICE_USD_PER_MTOKEN` (with the source URL and retrieval date in a comment), and `MODEL_INFO[JEV]` to `config/models.py`.
- Add `APISettings.typesafe_api_key` (`default_factory` reading `TYPESAFE_API_KEY`, joined to `validate_api_keys`) and the `.env.example` declaration under the OpenRouter block; confirm `python -m tools.doctor` reports the key present on this machine.
- Add `TimeoutSettings.decisions_sdk_s` (3.0, `ge=0.5`, `le=60.0`, description naming the leg and the env key), the commented `TIMEOUTS__DECISIONS_SDK_S` in `.env.example`, and the catalog row; `default_sdk_timeout` gains the branch.
- Add `AsyncHTTPClient` to `LLMStack` and `_load_stack`.

### 2. The decisions leg and its envelope
- **Task ID**: build-decisions-leg
- **Depends On**: build-taxonomy-additions
- **Validates**: `tests/unit/test_llm_backend_decisions.py` (create), `tests/unit/test_llm_import_safety.py`
- **Informed By**: spike-1 (native wire shape, `detail` error bodies, prompt-shaped state, latency), spike-4 (the cent floor), Research findings 2, 5, and 6 (errors, price, token-only usage)
- **Assigned To**: decisions-builder
- **Agent Type**: builder
- **Parallel**: false
- Write `agent/llm/backends/decisions.py`: `questions_for`, `decode_answers`, `SpendEnvelope`, `call`, per the Technical Approach table and failure table; the module docstring in `ollama.py`'s shape.
- Recorded-response fixtures from spike-1 (verbatim bodies, retrieval date in a comment).
- Tests: success; each failure class with `reason` and one ERROR line; missing key; `questions_for` over every classification output type on `main` (AST-located) and the three `ValueError` shapes; `decode_answers` edge cases; the envelope's five invariants (Success Criteria) with a fake meter; four concurrent callers roll once.

### 3. Router rule 5, wrapper entry, doctor row
- **Task ID**: build-router-rule
- **Depends On**: build-decisions-leg
- **Validates**: `tests/unit/test_llm_router.py`, `tests/unit/test_llm_router_eligibility.py`, `tests/unit/test_llm_wrapper.py`, the doctor test that covers `_check_llm_routing`
- **Informed By**: parent plan Data Flow step 4 (rule order), Risk 4
- **Assigned To**: decisions-builder
- **Agent Type**: builder
- **Parallel**: false
- Rule 5 in `resolve` ahead of rule 3; docstring to five rules.
- `_LEGS[Backend.DECISIONS]`; `test_legs_table_covers_every_backend`; the decisions → ollama fallback case and the `llm_no_fallback` case.
- `_expected` in the router test gains the branch; the eligibility test's local list includes `DECISIONS`.
- `_check_llm_routing` gains the `decisions_endpoint` row (no network).

### 4. Jev pinned-model probe test
- **Task ID**: build-probe-test
- **Depends On**: build-taxonomy-additions
- **Validates**: `tests/unit/test_models.py`
- **Informed By**: Research finding 7 (aliases move; `jev-1.13.0` is the pin), spike-1 (the response shape)
- **Assigned To**: decisions-builder
- **Agent Type**: builder
- **Parallel**: true
- `test_typesafe_jev_pinned_model_answers` beside the gemma probe, same fail-closed shape, one minimal authenticated POST asserting `model == JEV`, the `noul` answer shape, and `usage.input_tokens`; skip only by naming a missing key; confirm `_configured_openrouter_ids()` stays quiet about `JEV`.

### 5. Runner: arm, reference, criterion, landing record, audit
- **Task ID**: build-runner-arm
- **Depends On**: build-decisions-leg
- **Validates**: `tests/unit/test_classification_eval.py`
- **Informed By**: spike-4 (per-run envelope like the gemma arm), Technical Approach (landing rule)
- **Assigned To**: comparison-builder
- **Agent Type**: builder
- **Parallel**: true
- `decisions_arm(site_id)` with `JEV_PRICE` and a per-run `SpendEnvelope` reserved for `max(0.01, CALL_BOUND_USD * 2 * n)` and settled in `finally`; `_candidate_arms` entry; `--candidate` help text.
- `Site.reference` accepts `"ollama"`; `_run_site` builds the ollama reference; C12, C13, C14 rows set it; C12's label applies the site threshold as in Technical Approach (only if C12 is reached; otherwise the label stays `decision`).
- `evaluate_bar` gains `cost` (reference on `anthropic` only); `render_report`, `claims_for` print it; `TIER_BAR` untouched.
- `landing_record(site_id, backend)`; `_audit_row` reads it and gains the `DECISIONS` branch (`MISS fallback` naming the ollama arm's failures); the `OLLAMA` branch keeps its latency-only reading.
- Tests for each of the above through the existing fixtures.

### 6. Comparisons and landings
- **Task ID**: build-landings
- **Depends On**: build-router-rule, build-runner-arm, build-probe-test
- **Validates**: `python -m tools.classification_eval --audit`, `tests/unit/test_llm_task_taxonomy.py` (doc parity), the site tests named in Test Impact for any site that lands
- **Informed By**: spike-2 (12 real messages here; Open Question 1), Research finding 7 (the quality prior), Risk 1, Risk 2
- **Assigned To**: comparison-builder
- **Agent Type**: builder
- **Parallel**: false
- Stop services (`./scripts/valor-service.sh stop`); confirm granite warm and the key present.
- For each of C1 through C15 in the order C11, C14, C7, C8, C15, C12, C13, C9, C10, C5, C6, C1, C2, C3, C4 (the sites with real inputs of their own shape first, then granite's passing-agreement sites, then the rest): add `Decision` markers to the site's output type (question, criteria with `what` from the site's prompt text, examples where the prompt has them); run `--candidate decisions,ollama` (with `--inputs <sample>` when Open Question 1 provides one); read the report; iterate on markers and `candidate_prompt` within reason; keep the last record.
- On a passing record: set `backend=Backend.DECISIONS`, make the measured candidate prompt the production prompt if it differs, edit the doc row, commit `Land <site> on decisions (record <id>)`.
- On a miss: leave the declaration, commit `Hold <site> (record <id>: <failing criteria>)`.
- C15 cascade only on a passing C15 record; C12 per-call type only when C12's run is reached (build it before C12's run so the record measures it).
- If the rejection criteria are met, record the verdict claim on the case and stop landing.
- Write the per-site landing summary (the Lane C Outcome table) into the PR body.

### 7. Validate lane C
- **Task ID**: validate-lane-c
- **Depends On**: build-landings
- **Assigned To**: decisions-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row; re-derive each landing from its record with `evaluate_bar` and the fallback rule; confirm each `DECISIONS` declaration's route and doc row; confirm the anti-criteria; report per site.

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-lane-c
- **Assigned To**: decisions-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- The four pages and the docstrings in Documentation; the Lane C Outcome table from the records.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: decisions-validator
- **Agent Type**: validator
- **Parallel**: false
- Rebase on `main` (lane B may have merged); re-run the Verification table; confirm the docs greps; final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Leg, router, runner, wrapper, taxonomy, settings tests pass | `scripts/pytest-clean.sh tests/unit/test_llm_backend_decisions.py tests/unit/test_llm_router.py tests/unit/test_llm_router_eligibility.py tests/unit/test_llm_wrapper.py tests/unit/test_llm_tasks.py tests/unit/test_llm_task_taxonomy.py tests/unit/test_classification_eval.py tests/unit/test_llm_import_safety.py tests/unit/test_settings.py -q -p no:randomly` | exit code 0 |
| Jev pinned-model probe passes live | `scripts/pytest-clean.sh tests/unit/test_models.py -q -k jev` | exit code 0 |
| The key is read only through settings | `grep -rln "TYPESAFE_API_KEY" agent/ tools/ bridge/ worker/` | output is empty |
| The settings field is populated from the flat key on this machine | `.venv/bin/python -c "from config.settings import settings; print(settings.api.typesafe_api_key is not None)"` | output contains True |
| `.env.example` declares the credential unmarked | `grep -B2 "^TYPESAFE_API_KEY=" .env.example \| grep -c "@optional"` | match count == 0 |
| The leg targets TypeSafe directly and no OpenRouter path remains in it | `grep -c "openrouter" agent/llm/backends/decisions.py` | match count == 0 |
| The pinned id is a version, not an alias | `.venv/bin/python -c "from config.models import JEV; assert JEV == 'jev-1.13.0' and 'latest' not in JEV and 'preview' not in JEV; print('ok')"` | output contains ok |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `Backend.DECISIONS` exists and the wrapper's leg table covers every backend | `.venv/bin/python -c "from agent.llm.tasks import Backend; from agent.llm.wrapper import _LEGS; assert Backend.DECISIONS.value == 'decisions'; assert set(_LEGS) == set(Backend); print('ok')"` | output contains ok |
| Rule 5 routes an eligible DECISIONS task to Jev with an Ollama fallback and a client key to Anthropic | `.venv/bin/python -c "from agent.llm.tasks import *; from agent.llm.router import resolve; from config.models import JEV; t=LLMTask('x.y', TaskKind.CLASSIFICATION, Backend.DECISIONS); r=resolve(t,'valor'); assert (r.backend, r.model, r.fallback.backend) == (Backend.DECISIONS, JEV, Backend.OLLAMA) and r.fallback.fallback is None; c=resolve(t,'acme'); assert c.backend is Backend.ANTHROPIC and c.fallback is None; print('ok')"` | output contains ok |
| The decisions leg reads its timer from `TimeoutSettings` | `.venv/bin/python -c "from agent.llm.backends import default_sdk_timeout; from agent.llm.tasks import Backend; from config.settings import settings; assert default_sdk_timeout(Backend.DECISIONS) == settings.timeouts.decisions_sdk_s == 3.0; print('ok')"` | output contains ok |
| The leg holds no third-party import at module scope (#3001) | `.venv/bin/python -c "import ast; t=ast.parse(open('agent/llm/backends/decisions.py').read()); names=[a.name.split('.')[0] for n in t.body if isinstance(n,ast.Import) for a in n.names]+[n.module.split('.')[0] for n in t.body if isinstance(n,ast.ImportFrom) and n.module]; bad=[n for n in names if n in ('httpx','anthropic','openai','pydantic_ai')]; print(bad)"` | output contains [] |
| No `asyncio.wait_for` inside the decisions leg (hotfix #1055) | `grep -c "wait_for" agent/llm/backends/decisions.py` | match count == 0 |
| No `openrouter` or TypeSafe SDK dependency was added | `grep -c '"openrouter\|"typesafe' pyproject.toml` | match count == 0 |
| No per-call `spend_receipt` row: the leg never calls `record_receipt` and never settles per call | `grep -c "record_receipt\|settle_from_response" agent/llm/backends/decisions.py` | match count == 0 |
| The runner accepts the decisions candidate | `.venv/bin/python -c "from tools.classification_eval.__main__ import parse_candidates; assert parse_candidates(['decisions,ollama']) == ['decisions','ollama']; print('ok')"` | output contains ok |
| Every DECISIONS declaration has a passing landing record and a passing Ollama fallback | `.venv/bin/python -m tools.classification_eval --audit` | exit code 0 |
| Every DECISIONS site's route carries an Ollama fallback for `valor` | `.venv/bin/python -c "from agent.llm.tasks import Backend, declared_sites; from agent.llm.router import resolve; bad=[d.task.site for d in declared_sites() if d.task.backend is Backend.DECISIONS and (resolve(d.task,'valor').fallback is None or resolve(d.task,'valor').fallback.backend is not Backend.OLLAMA)]; print(bad)"` | output contains [] |
| No `MODELS__*` switch and no shadow route was added (parent plan, Tom's answer 2) | `grep -rc "classifier_shadow\|SHADOW_SITES\|STRUCTURED_DECISION_SITES\|MODELS__DECISIONS" agent/llm config/settings.py tools/classification_eval` | match count == 0 |
| The four issue-era `LLMTask` fields were not added (the `Decision` marker replaced them) | `.venv/bin/python -c "from dataclasses import fields; from agent.llm.tasks import LLMTask; print(sorted(f.name for f in fields(LLMTask)))"` | output does not contain noul_threshold |
| `.env.example` declares the new timer key | `grep -c "TIMEOUTS__DECISIONS_SDK_S" .env.example` | output > 0 |
| The taxonomy doc lists rule 5 and the lane C outcome | `grep -c "DECISIONS\|Lane C Outcome" docs/features/llm-task-taxonomy.md` | output > 2 |
| The infra doc has the decisions section with the native endpoint and a retrieval date | `grep -c "Decisions Endpoint\|api.typesafe.ai\|2026-09-21" docs/infra/llm-task-routing.md` | output > 2 |
| The new timer key is a commented override, never a required declaration | `.venv/bin/python -c "from pathlib import Path; from scripts.update.verify import check_env_completeness; c=check_env_completeness(Path('.')); print('DECISIONS_SDK' in (c.error or '') + (c.detail or ''))"` | output contains False |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Real-input sample (blocks the inbound sites' landings, not the build).** The build machine holds 12 real `valor` human messages (spike-2), so C1 to C10, C12, C13, and C15 fail `n_real` here exactly as in lane A, whatever Jev scores. Which do you prefer: (a) run `python -m tools.classification_eval --site routing.needs_response --save-inputs <file> --candidate ollama --latency-only` (or any site; the draw is the same inbound set) on the bridge machine and hand the JSON-lines file to the builder for `--inputs` (the file stays out of git); (b) run the comparisons on the bridge machine from the lane branch with services stopped; or (c) proceed on this machine and let those sites hold with `n_real` named, landing only what C11 and C14 can carry. The plan proceeds under (c) until told otherwise; (a) is the cheapest unlock.
2. **Departures from the issue body.** The four `LLMTask` fields become a per-field `Decision` marker (with `min_confidence` as the abstain floor) and per-backend `thresholds` are dropped; per-call metering becomes a token-priced one-cent envelope; the transport is TypeSafe's native endpoint with `TYPESAFE_API_KEY` through a new settings field, replacing the OpenRouter proxy (Reconciliation 1 to 6, each with its reason). The plan proceeds on these unless you object.
3. **Observation, no action requested.** `settings.api.openrouter_api_key` is never populated by the vault's flat `OPENROUTER_API_KEY` (the field needs `API__OPENROUTER_API_KEY`), so that field is dead and every reader uses the environment variable directly. The new `typesafe_api_key` sidesteps this with a `default_factory` read. A one-line chore issue could give `openrouter_api_key` the same treatment; this lane leaves it alone.
