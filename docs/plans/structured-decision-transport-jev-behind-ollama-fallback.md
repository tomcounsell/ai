---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-09-21
tracking: https://github.com/tomcounsell/ai/issues/3421
last_comment_id: 5745559079
---

# Structured-Decision Transport (OpenRouter Decisions, Jev 1.13) Behind an Ollama Fallback

## Problem

Lane A (#3410, PR #3524, merged as `4703bce23`) gave every non-harness LLM call a declared `LLMTask`, one routing point, two backend legs (Anthropic, Ollama), and a comparison runner whose records are the argument for a site's backend. It also measured that granite (`granite4.1:3b` via Ollama) clears the agreement bar on four comparison sites (C7 0.981, C8 0.923, C11 0.900, C15 0.885) and misses it on eight, and that on the build machine every inbound-shaped site fails `n_real` because the memory store holds 12 real `valor` messages. No comparison site moved off Anthropic.

TypeSafe's Jev 1.13 is a structured-decision model: it takes a `state` and typed `questions` and answers with per-option probabilities in about half a second for two hundred-thousandths of a dollar. It answers only on `POST https://openrouter.ai/api/alpha/decisions`; chat/completions returns HTTP 400 for the slug, so neither lane-A leg can reach it. Tom's ordering (2026-09-18): local first, an external structured-decision provider only behind a local fallback. His delivery shape (2026-09-19, #3410 comment 5737918683): no transition phase, no switches; the builder iterates on the comparison until the PR is approved, and PR approval is the gate.

**Current behavior:**

Verified on `main` at `149f0d0da` (Freshness Check):

- `agent/llm/tasks.py::Backend` has `ANTHROPIC` and `OLLAMA`; `agent/llm/router.py::resolve` raises `ValueError` for any other backend (`:70`); `agent/llm/wrapper.py::_LEGS` (`:95`) maps the two legs; `agent/llm/backends/__init__.py::default_sdk_timeout` (`:51`) knows two timers.
- `tools/classification_eval/__main__.py::CANDIDATE_BACKENDS` (`:47`) is derived from the enum, but `_candidate_arms` (`:115`) builds only `ollama` and `anthropic` arms, so `--candidate decisions` would raise `KeyError` after passing the vocabulary check.
- `tools/classification_eval/core.py::evaluate_bar` (`:411`) applies six criteria; there is no cost criterion, because the only paid candidate so far (Haiku) was the reference.
- `tools/classification_eval/records.py::_audit_row` (`:126`) knows `OLLAMA` and `ANTHROPIC` landings and reads the newest record per site regardless of which arms it carries.
- `config/models.py` has `OPENROUTER_URL` (`:56`, chat/completions) and no decisions URL, no `JEV`, no `MODEL_INFO` entry for it.
- `tools/paid_inference_meter.py` meters in integer cents (`_valid_amount`, `:149`): a per-call reservation at Jev's 32k-context bound (32000 × 0.000000042 = 0.001344 USD) rounds to 0 cents, and every `settle` writes one `spend_receipt` `ImprovementEvidence` row (`:256`). Per-call reserve/settle on the C1 hot path would write one evidence row per inbound message and meter nothing.
- `settings.api.openrouter_api_key` is `None` on this machine although the vault `.env` carries `OPENROUTER_API_KEY`: the field lives under the `api` group, so only `API__OPENROUTER_API_KEY` would populate it. Every reader in the repo (`tools/emoji_embedding.py:270`, `tools/improvement_eval/arm_worker.py:297`, the gemma arm at `tools/classification_eval/arms.py:271` via its `or os.environ.get(...)`) reads the flat environment variable.
- The site walk (`agent/llm/tasks.py::_literal_field`, `:142`) accepts exactly five `LLMTask` fields with literal values; any new keyword on a declaration raises `ValueError` unless the walk learns it.

**Desired outcome:**

1. A third backend leg, `agent/llm/backends/decisions.py`, implementing lane A's leg protocol as it landed (`call(prompt, output_type, route, *, system, sdk_timeout, slot_timeout, max_retries, deadline=None, stack) -> BaseModel`): one `httpx` POST per call to the decisions endpoint, questions built from the output type's `Literal` and `bool` fields, `noul` to `bool` at a per-field threshold, `confidence` from the answer, reason fields empty, validated with `output_type.model_validate`, `LLMCallError` on every failure class, metered under purpose `structured_decision` without a Redis write per call.
2. Router rule 5, `task.backend == DECISIONS and is_eligible(project_key)` → `Route(DECISIONS, JEV, fallback=Route(OLLAMA, OLLAMA_CLASSIFIER_MODEL))`, ahead of rule 3; the ineligible case resolves to `Route(ANTHROPIC, model)` (charter §7). `Backend.DECISIONS`, `JEV`, `OPENROUTER_DECISIONS_URL`, `MODEL_INFO[JEV]`, `TimeoutSettings.decisions_sdk_s`.
3. The comparison runner grows a `decisions` candidate arm, a `cost` criterion (at Haiku-backed sites the candidate costs at most one tenth of a Haiku call), and an audit branch for `DECISIONS` landings that also checks the Ollama fallback is a passing backend.
4. A comparison record for every eligible classification site (C1 through C15; C16 is `client_only`) with `--candidate decisions,ollama`, so one record carries both the candidate and its fallback on the same inputs. Sites land on `DECISIONS` one word at a time on a passing record; a miss stays where lane A landed it and the record names the failing criterion; Jev is rejected as a backend, and the case records it, if it returns non-200 on more than 2% of comparison calls, agreement is below 80% at every Haiku-backed site, or C12's threshold cannot be set without raising the wrong-bind rate.
5. `tests/unit/test_models.py::test_openrouter_jev_endpoint_is_listed` against the per-model endpoint listing, fail-closed like its sibling.
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
| `OPENROUTER_API_KEY` exists as `settings.api.openrouter_api_key` | `config/settings.py:34-36`; the flat env key does not populate the nested field (spike-1: `settings.api.openrouter_api_key is None` while `os.environ["OPENROUTER_API_KEY"]` is set); every repo reader uses `os.environ.get("OPENROUTER_API_KEY")` | drifted: the leg reads the flat variable, with a `# env-scope-guard: allow` marker if the read must sit at module scope (it will not; the read is inside `call`) |
| `httpx` is a dependency | `pyproject.toml:16` `httpx>=0.27.0` | holds |
| Router docstring reserves rule 5 for this lane | `agent/llm/router.py:31-32` | holds |
| Live probe: HTTP 200, typed answers, `usage.cost`; chat/completions 400 | re-run 2026-09-21 from this venv (spike-1): HTTP 200 in 517 to 925 ms, `usage.cost` 1.96e-5 to 2.35e-5, response `model` `typesafe/jev-1.13-20260917` | holds |
| Endpoint listing needs no auth: prompt `0.000000042`, completion `0`, context 32000, `supported_parameters: []` | `GET /api/v1/models/typesafe/jev-1.13/endpoints` on 2026-09-21: HTTP 200, `data.endpoints[0]` carries exactly those values and `status: 0`; the top-level `data.context_length` is `null`, so the probe test reads the endpoint entry | holds, with the field location noted |
| C16 email triage is `client_only` | `tools/email_cs/triage.py:100`, `client_only=True` | holds |
| "A site may declare `backend=DECISIONS` only if its lane-A record shows granite clearing the bar" | `docs/features/llm-task-taxonomy.md:171-194`: no comparison site's granite arm cleared all six criteria; the misses were `n_real` (12 real messages on the build machine) and `p95_c4` (the build machine's GPU serialised at concurrency 4), plus `agreement` on eight sites; granite's agreement cleared the tier bar on C7, C8, C11, C15; C12 to C14 hold latency-only records that pass | drifted: read literally the landable set is empty; the plan reads the rule as "the Ollama fallback clears the bar on the same record as the decisions arm, or on the site's latency-only record" (Technical Approach, landing rule), which is what the rule is for (no single external provider on a hot path, and a fallback that is itself a passing backend) |

**Cited sibling issues/PRs re-checked:**
- #3410: closed 2026-09-20 by PR #3524 (merged as `4703bce23`); its plan was archived to `docs/archive/plans-completed/llm-task-taxonomy-routing-layer.md` in `1ab8d429e`. Everything this lane builds on is on `main`.
- #3420 (lane B): open; its `/do-plan` is in progress in parallel (ledger slug `sdlc-3420`, no plan doc on `main` yet). Both lanes append to `Backend`, `_LEGS`, `default_sdk_timeout`, `resolve`, `_candidate_arms`, `test_llm_router.py::_expected`, `test_llm_tasks.py`'s enum pin, and the two doc tables. The additions are disjoint members and rows, so the second lane to merge rebases through a small mechanical conflict.
- #3422 (emoji as a classification site): open, untouched by this lane.
- #3338 / PR #3381: the live-listing probe pattern this lane's `test_openrouter_jev_endpoint_is_listed` mirrors; `tests/unit/test_models.py:62-70`.
- #3177 (RSI): owns `tools/paid_inference_meter.py`; this lane consumes it and adds no controller logic.

**Commits on main since issue was filed (touching referenced files):**
- `4703bce23` LLM task taxonomy and routing layer (lane A) (#3524): created every file this lane extends. Read in full; the drift rows above are its effect.
- `1ab8d429e` Migrate completed plan: the parent plan moved to `docs/archive/plans-completed/`; this plan cites it there.

**Active plans in `docs/plans/` overlapping this area:** none on `main`. `recursive-self-improvement.md` (#3177) owns the meter and the case substrate this lane writes records to; coordination, not conflict. Lane B's plan (#3420) will overlap on the enum and the router and is being written in parallel.

**Notes:** Four drifts change the plan's mechanism but none its outcome: the leg signature gains `deadline` and `stack`; the question metadata moves from `LLMTask` to a per-field `Decision` marker; metering moves from per-call reserve/settle to a one-cent envelope; the key is read from the flat environment variable. The fifth drift (the landing rule) is a scoping decision recorded in Technical Approach and raised in Open Questions only for the real-input sample it depends on.

## Prior Art

- **#3410 / PR #3524** (merged 2026-09-20): lane A. Everything this lane plugs into: `LLMTask`, `resolve`, the leg protocol with its `deadline` re-check and `stack` seam, `run_typed`'s one-shot fallback inside the caller's budget, the comparison runner with its six-criterion bar, the `classifier_comparison` evidence kind on case `1ec40086ca1d422e90ef747775ff7f64`, the taxonomy doc's parity test. Its outcome table (no site moved to granite; `n_real` short on the build machine) is the starting state.
- **#3338 / PR #3381** (merged 2026-09-17): the live-listing probe pattern in `tests/unit/test_models.py`, fail-closed on network error, with the `_configured_openrouter_ids()` sweep that warns on other `OPENROUTER_*` ids. This lane's Jev probe mirrors the first and stays out of the second (the slug is absent from the public catalog by design and the constant is named `JEV`).
- **#3215 / PR #3337 and PR #3350** (merged 2026-09-15/16): `tools/paid_inference_meter.py` (reserve/settle in cents, `spend_receipt` rows, the reconcile sweep) and the metered `OpenRouterGemmaArm` in the runner (one reservation per run, settled from accumulated `usage.cost`, `metering="unknown"` the moment a response carries no cost). The runner arm pattern is reused; the per-run reservation is the model for this lane's envelope.
- **#3001**: the import-safety contract (`LLMStack`, `_load_stack`, no third-party symbol at module scope in `agent/llm`). The decisions leg gets its HTTP client from the stack for the same reason and the same test seam.
- **#1055 / #1111**: the hotfix invariant every leg carries (an SDK-level timer around the live request, no `asyncio.wait_for`). `httpx.AsyncClient(timeout=...)` is that timer here.
- **pydantic/pydantic-ai#8552** (open upstream, found in Research): a request to route Jev through PydanticAI. Nothing shipped; this lane keeps the transport as one `httpx` POST and does not wait for it.

## Research

**Queries used:**
- `OpenRouter "api/alpha/decisions" TypeSafe Jev decisions endpoint instructions criteria noul` (WebSearch, 2026-09-21)
- The live probe from this venv (Spike Results, spike-1) against the endpoint and the per-model listing; the parent plan's Research finding 1 (verbatim probe of 2026-09-18) re-read.

**Key findings:**

1. **The wire shape holds and `instructions` is required.** `POST https://openrouter.ai/api/alpha/decisions` with `{"model": "typesafe/jev-1.13", "state": <str>, "questions": {<id>: {"type": "choice"|"noul", "instructions": {"question": <str>, "focus"?: <str>}, "criteria": {<option>: {"what": <str>, "examples"?: [...]}}}}}` answers `{"model": "typesafe/jev-1.13-20260917", "answers": {<id>: {"type": "choice", "choice": <option>, "probabilities": {<option>: <float>}, "confidence": <float>} | {"type": "noul", "noul": <float>}}, "usage": {"input_tokens", "output_tokens", "cost"}, "id", "provider": "TypeSafe"}`. A question without `instructions` is refused with HTTP 400 and a zod-style error body `{"error": {"message": <json-encoded issue list>, "code": 400}}`. A `noul` question's `criteria` carry both `true` and `false` (the integrations found by the search normalise to that shape too). *Informs:* the question builder always emits `instructions.question` (from the `Decision` marker or a generated default) and both `noul` criteria; the leg treats any non-200 as `LLMCallError(reason="transport")` with the body's `error.message` in the message, truncated. Sources: live probe; https://openrouter.ai/docs/client-sdks/python/sdks/decisions/README.md; https://docs.typesafe.ai/concepts/system-one; https://github.com/lahfir/agent-desktop/pull/208 (noul normalisation).
2. **A prompt-shaped `state` works.** Posting C3's reference prompt verbatim (753 characters of instructions plus the message) as `state` returned a typed answer in 517 ms. *Informs:* the leg sends `system + "\n\n" + prompt` (or `prompt` alone) as `state`, so a site's production prompt is usable unchanged; the builder trims a site's `candidate_prompt` toward a bare state while iterating, exactly as lane A's rows already do for granite, and the site's production prompt becomes whatever the passing record measured.
3. **Dynamic options work as `choice` criteria keyed by id.** Two job ids as `criteria` keys plus a `bind`/`new` question answered `bind` / `job_a1` with confidence 0.99 / 1.0. *Informs:* C12's per-call output type carries the candidate job ids as a `Literal`, and the leg needs no special case for dynamic options.
4. **Pricing and listing.** `GET /api/v1/models/typesafe/jev-1.13/endpoints` (no auth): `data.endpoints[0]` has `context_length 32000`, `pricing.prompt "0.000000042"`, `pricing.completion "0"`, `supported_parameters []`, `status 0`; `data.context_length` at the top level is `null`. The slug is absent from `GET /api/v1/models`. *Informs:* the probe test asserts the endpoint entry, not the top-level field; `MODEL_INFO[JEV]` records `context_window: 32000` and the price with retrieval date 2026-09-21; the per-call cost bound used for the metering headroom check is 32000 × 4.2e-8 = 0.001344 USD.
5. **The usage block is Anthropic-style (`input_tokens`/`output_tokens`).** *Informs:* `settle_from_response`'s token estimate path (`prompt_tokens`/`completion_tokens`) would find nothing; the leg settles from `usage.cost` only and marks the envelope `metering="unknown"` when a 200 carries no `cost` (charter §8: uncertain metering is not zero cost).
6. **No published deprecation policy for `/api/alpha/*`** (unchanged from the parent plan; the search found several third-party integrations making the endpoint path configurable for exactly this reason). *Informs:* `OPENROUTER_DECISIONS_URL` is one constant with an env override in the shape of `OPENROUTER_URL`, the model slug is pinned, and the listing probe fails by name.
7. **Quality prior** (parent plan finding 2): TypeSafe's own 67.8% agreement figure sits under every tier bar, and the probe answered `other` (0.72) for "thanks, that looks great" on C3's prompt where the reference says `question`. *Informs:* the expected outcome is a leg, a rule, a runner arm, and records, with landings on few sites or none; the plan's success criteria are written so that a zero-landing result with complete records is a pass.

A `tools.memory_search save` of findings 1, 4, and 5 is attempted at plan time; this section is the capture point.

## Spike Results

Placeholder.

## Data Flow

Placeholder.

## Architectural Impact

Placeholder.

## Appetite

Placeholder.

## Prerequisites

Placeholder.

## Solution

Placeholder.

## Failure Path Test Strategy

Placeholder.

## Test Impact

- [ ] `tests/unit/test_llm_tasks.py::TestEnums::test_lane_a_backends_are_anthropic_and_ollama` — UPDATE: the `Backend` value set gains `"decisions"` (rename the test to name the three members; lane B adds a fourth on its own branch).
- [ ] `tests/unit/test_llm_router.py::_expected` and `TestFourRules` — UPDATE: `_expected` gains the `DECISIONS` branch (`valor` → `Route(DECISIONS, JEV, fallback=Route(OLLAMA, OLLAMA_CLASSIFIER_MODEL))`, any other key → `ANTHROPIC_ROUTE`); the class gains `test_rule_5_eligible_decisions_carries_an_ollama_fallback`, `test_rule_5_ineligible_decisions_fails_closed_to_anthropic`, and `test_only_the_local_rules_consult_eligibility` (the spy sees one call for a `DECISIONS` task too). The module docstring's "four rules" becomes five.
- [ ] `tests/unit/test_llm_router_eligibility.py` — UPDATE: the `OLLAMA_CLASSIFICATION` list becomes `LOCAL_CLASSIFICATION` (`backend in {OLLAMA, DECISIONS}`); the client-key case asserts the Anthropic leg for `DECISIONS` sites, the `valor` case with `gh` unavailable asserts the decisions leg with the Ollama leg as fallback (both faked at `_LEGS`).
- [ ] `tests/unit/test_llm_wrapper.py` — UPDATE: the `_LEGS` table tests gain one case, "decisions primary raises `LLMCallError(reason="transport")`, the Ollama fallback answers inside the budget"; and a new `test_legs_table_covers_every_backend` (`set(_LEGS) == set(Backend)`).
- [ ] `tests/unit/test_classification_eval.py` — UPDATE: `parse_candidates` accepts `decisions`; `_candidate_arms` builds the decisions arm; `evaluate_bar` gains the `cost` criterion cases (Haiku reference: candidate at or under one tenth passes, over misses; Ollama or gemma reference: no `cost` criterion); `render_report` prints the `cost` threshold line; `_audit_row` gains the `DECISIONS` branch cases (decisions candidate passes and the same record's ollama candidate passes → PASS; ollama candidate misses → MISS naming `fallback`; reference on ollama plus a passing latency-only ollama record → PASS) and the `OLLAMA` branch keeps passing when the newest record is a decisions comparison whose reference is the ollama arm (the landing record is the newest record carrying the landed backend as a candidate or a latency-only measurement).
- [ ] `tests/unit/test_llm_task_taxonomy.py::test_taxonomy_doc_row_matches_declaration` — no code change; it reads the `Backend` column of the site table in `docs/features/llm-task-taxonomy.md`, so every site that lands on `DECISIONS` needs its doc row edited in the same commit as its declaration.
- [ ] `tests/unit/test_settings.py` — UPDATE only if it pins the `TimeoutSettings` field list; `decisions_sdk_s` joins `TimeoutSettings`.
- [ ] `tests/unit/test_doctor*.py` (whichever file covers `_check_llm_routing`) — UPDATE: the section gains a `decisions_endpoint` row; the existing row-count or name-set assertions include it.
- [ ] `tests/unit/test_llm_backend_decisions.py` — CREATE (greenfield: the leg's unit tests with recorded responses through the `LLMStack.AsyncHTTPClient` seam and `httpx.MockTransport`).
- [ ] `tests/unit/test_models.py` — CREATE `test_openrouter_jev_endpoint_is_listed` beside `test_openrouter_gemma4_free_is_listed`; `_configured_openrouter_ids()` keeps excluding `JEV` (not an `OPENROUTER_*` name) and `OPENROUTER_DECISIONS_URL` (starts with `http`), so the catalog-warning test stays quiet about a slug that is absent from the public catalog by design.
- [ ] `tests/unit/test_job_router.py` — UPDATE only if C12 lands on `DECISIONS` (the per-call output type carries the candidate job ids as `Literal` options; the existing fakes return `JobRouteDecision` instances and keep working because the per-call type subclasses it).
- [ ] `tests/unit/test_improvement_evidence.py` — UPDATE only if C15 lands on `DECISIONS` (the cascade adds a second `run_typed` call on positives; the transport fakes that return a positive with an empty `span` exercise it).

## Rabbit Holes

Placeholder.

## Risks

Placeholder.

## Race Conditions

Placeholder.

## No-Gos (Out of Scope)

Placeholder.

## Update System

Placeholder.

## Agent Integration

Placeholder.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/llm-task-taxonomy.md`: the `backend` field row names `Backend.DECISIONS`; the Router Rules table gains rule 5 (`backend == DECISIONS and is_eligible` → `Route(DECISIONS, JEV, fallback=Route(OLLAMA, OLLAMA_CLASSIFIER_MODEL))`) ahead of rule 3 and its "otherwise" row; the Acceptance Bar gains the `cost` criterion and the DECISIONS landing rule (the decisions arm clears every criterion and the Ollama fallback is a passing backend on the same record or on the site's latency-only record); the site table's `Backend` column changes for every site this lane lands (the parity test reads it); a new `## Lane C Outcome` section with the per-site results table (agreement, p95@4, cost per call, error rate, failing criteria, record id) and the rejection verdict if one was recorded; the `## Tests` paragraph names `tests/unit/test_llm_backend_decisions.py`.
- [ ] Update `docs/features/nonharness-llm-wrapper.md`: a `### The decisions leg (backends/decisions.py)` subsection under "The Backend Legs" (state and question construction from the output type, the `Decision` field marker, the noul threshold, the reason fields, the envelope metering, the six failure classes and their `reason` values, the SDK timer); the fallback-budget section's example gains the decisions → Ollama shape; "Adding a New Site" shows a `Decision`-annotated field.
- [ ] Update `docs/infra/llm-task-routing.md`: a new `## Decisions Endpoint` section (URL, model slug pinned to `typesafe/jev-1.13`, the no-auth listing URL the probe test hits, pricing `0.000000042 USD per prompt token, completion 0, context 32000` with retrieval date 2026-09-21, the `OPENROUTER_API_KEY` requirement on every machine that runs a `DECISIONS` site and what happens without it, the `structured_decision` meter purpose and the one-cent envelope, the `llm_route ... backend=decisions` and `llm_fallback ... primary=decisions fallback=ollama` greps); the Rollback section gains lever 0 for this lane (unset the key or set `TIMEOUTS__DECISIONS_SDK_S=1`: every call falls to granite; then the one-word `backend` edit).
- [ ] Update `docs/features/config-timeout-catalog.md`: the `TimeoutSettings` table gains `decisions_sdk_s` (3.0 s, `TIMEOUTS__DECISIONS_SDK_S`, the decisions leg's single SDK-level timer).
- [ ] Update `docs/features/README.md` only if a new feature page is created (none planned; the three pages above exist).

### Inline Documentation
- [ ] Module docstring on `agent/llm/backends/decisions.py` in the shape of `ollama.py`'s: the wire shape, the question builder, the metering envelope, the import-safety contract.
- [ ] `agent/llm/router.py` docstring: five rules; the lane-C sentence that reserved rule 5 is replaced by the rule itself.
- [ ] `agent/llm/tasks.py` docstring: the `Decision` marker's contract (which field types it may annotate, defaults, how the decisions leg reads it, that the Anthropic and Ollama legs ignore it because pydantic keeps `Annotated` metadata out of the JSON schema).
- [ ] `tools/classification_eval/__init__.py` and `arms.py` docstrings: the decisions candidate arm, the `cost` criterion, the landing-record selection rule.

## Success Criteria

Placeholder.

## Team Orchestration

Placeholder.

## Step by Step Tasks

Placeholder.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Leg, router, runner, wrapper, taxonomy, settings tests pass | `scripts/pytest-clean.sh tests/unit/test_llm_backend_decisions.py tests/unit/test_llm_router.py tests/unit/test_llm_router_eligibility.py tests/unit/test_llm_wrapper.py tests/unit/test_llm_tasks.py tests/unit/test_llm_task_taxonomy.py tests/unit/test_classification_eval.py tests/unit/test_llm_import_safety.py tests/unit/test_settings.py -q -p no:randomly` | exit code 0 |
| Jev listing probe passes live | `scripts/pytest-clean.sh tests/unit/test_models.py -q -k jev` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `Backend.DECISIONS` exists and the wrapper's leg table covers every backend | `.venv/bin/python -c "from agent.llm.tasks import Backend; from agent.llm.wrapper import _LEGS; assert Backend.DECISIONS.value == 'decisions'; assert set(_LEGS) == set(Backend); print('ok')"` | output contains ok |
| Rule 5 routes an eligible DECISIONS task to Jev with an Ollama fallback and a client key to Anthropic | `.venv/bin/python -c "from agent.llm.tasks import *; from agent.llm.router import resolve; from config.models import JEV; t=LLMTask('x.y', TaskKind.CLASSIFICATION, Backend.DECISIONS); r=resolve(t,'valor'); assert (r.backend, r.model, r.fallback.backend) == (Backend.DECISIONS, JEV, Backend.OLLAMA) and r.fallback.fallback is None; c=resolve(t,'acme'); assert c.backend is Backend.ANTHROPIC and c.fallback is None; print('ok')"` | output contains ok |
| The decisions leg reads its timer from `TimeoutSettings` | `.venv/bin/python -c "from agent.llm.backends import default_sdk_timeout; from agent.llm.tasks import Backend; from config.settings import settings; assert default_sdk_timeout(Backend.DECISIONS) == settings.timeouts.decisions_sdk_s == 3.0; print('ok')"` | output contains ok |
| The leg holds no third-party import at module scope (#3001) | `.venv/bin/python -c "import ast,sys; t=ast.parse(open('agent/llm/backends/decisions.py').read()); names=[n.names[0].name.split('.')[0] for n in t.body if isinstance(n,(ast.Import,ast.ImportFrom)) for _ in [0]] + [n.module.split('.')[0] for n in t.body if isinstance(n,ast.ImportFrom) and n.module]; bad=[n for n in names if n in ('httpx','anthropic','openai','pydantic_ai')]; print(bad)"` | output contains [] |
| No `asyncio.wait_for` inside the decisions leg (hotfix #1055) | `grep -c "wait_for" agent/llm/backends/decisions.py` | match count == 0 |
| No `openrouter` SDK dependency was added | `grep -c '"openrouter' pyproject.toml` | match count == 0 |
| No per-call `spend_receipt` row: the leg never calls `record_receipt` and never settles per call | `grep -c "record_receipt\|settle_from_response" agent/llm/backends/decisions.py` | match count == 0 |
| The runner accepts the decisions candidate | `.venv/bin/python -c "from tools.classification_eval.__main__ import parse_candidates; assert parse_candidates(['decisions,ollama']) == ['decisions','ollama']; print('ok')"` | output contains ok |
| Every DECISIONS declaration has a passing landing record and a passing Ollama fallback | `.venv/bin/python -m tools.classification_eval --audit` | exit code 0 |
| Every DECISIONS site's route carries an Ollama fallback for `valor` | `.venv/bin/python -c "from agent.llm.tasks import Backend, declared_sites; from agent.llm.router import resolve; bad=[d.task.site for d in declared_sites() if d.task.backend is Backend.DECISIONS and (resolve(d.task,'valor').fallback is None or resolve(d.task,'valor').fallback.backend is not Backend.OLLAMA)]; print(bad)"` | output contains [] |
| No `MODELS__*` switch and no shadow route was added (parent plan, Tom's answer 2) | `grep -rc "classifier_shadow\|SHADOW_SITES\|STRUCTURED_DECISION_SITES\|MODELS__DECISIONS" agent/llm config/settings.py tools/classification_eval` | match count == 0 |
| The four issue-era `LLMTask` fields were not added (the `Decision` marker replaced them) | `.venv/bin/python -c "from dataclasses import fields; from agent.llm.tasks import LLMTask; print(sorted(f.name for f in fields(LLMTask)))"` | output does not contain noul_threshold |
| `.env.example` declares the new timer key | `grep -c "TIMEOUTS__DECISIONS_SDK_S" .env.example` | output > 0 |
| The taxonomy doc lists rule 5 and the lane C outcome | `grep -c "DECISIONS\|Lane C Outcome" docs/features/llm-task-taxonomy.md` | output > 2 |
| The infra doc has the decisions section with a retrieval date | `grep -c "Decisions Endpoint\|0.000000042" docs/infra/llm-task-routing.md` | output > 1 |
| The new timer key is a commented override, never a required declaration | `.venv/bin/python -c "from pathlib import Path; from scripts.update.verify import check_env_completeness; c=check_env_completeness(Path('.')); print('DECISIONS_SDK' in (c.error or '') + (c.detail or ''))"` | output contains False |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

Placeholder.
