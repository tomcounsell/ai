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

Placeholder.

## Freshness Check

Placeholder.

## Prior Art

Placeholder.

## Research

Placeholder.

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
