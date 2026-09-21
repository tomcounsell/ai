---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-21
tracking: https://github.com/tomcounsell/ai/issues/3420
last_comment_id: 5745559073
---

# Local Encoder Classification Backend Behind Lane A's Router (Lane B of #3410)

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

Verified on `main` at `149f0d0da` by reading each file; the router table test, the eligibility test, the wrapper's `_LEGS` tests, the doctor test, and the runner's audit tests all key on the two-member `Backend` enum and on `Backend.OLLAMA` as the only local backend.

- [ ] `tests/unit/test_llm_tasks.py:24` (`{b.value for b in Backend} == {"anthropic", "ollama"}`) — UPDATE: the set gains `"local_encoder"`.
- [ ] `tests/unit/test_llm_router.py::_expected` (`:120-123`) and `TestEveryDeclaration::test_route_matches_the_declaration` — UPDATE: `_expected` returns `Route(LOCAL_ENCODER, task.site, fallback=ANTHROPIC_ROUTE)` for a `LOCAL_ENCODER` declaration with key `valor`, `ANTHROPIC_ROUTE` otherwise; add `test_rule_3_eligible_local_encoder_carries_an_anthropic_fallback`, `test_rule_4_ineligible_local_encoder_fails_closed`, and extend `test_only_the_ollama_rule_consults_eligibility` (`:103`) to the two local rules (rename to `test_only_the_local_rules_consult_eligibility`).
- [ ] `tests/unit/test_llm_router_eligibility.py` (`:76-96`, the `OLLAMA`-site parametrization) — UPDATE: parametrize over every declaration whose backend is not `ANTHROPIC`; the "valor message reaches the local leg with `gh` unavailable" case asserts the leg named by the declaration (`legs[task.backend]`), so a `LOCAL_ENCODER` site asserts the encoder leg.
- [ ] `tests/unit/test_llm_wrapper.py::TestPerBackendSdkTimer` (`:633-656`) — UPDATE: add the `local_encoder` rows (`local_typed_hard_s` default, explicit `sdk_timeout` wins) and a third fake in the `_LEGS` table fixture at `:607-608`; the fallback tests at `:553-708` gain one case: encoder leg raises `LLMCallError` → Anthropic fallback inside the budget, `llm_fallback ... primary=local_encoder` on `caplog`.
- [ ] `tests/unit/test_llm_backend_ollama.py` — no change; kept as the template for `tests/unit/test_llm_backend_local_encoder.py` (create).
- [ ] `tests/unit/test_llm_import_safety.py` — UPDATE: the raising shim fixture also writes `onnxruntime.py` and `tokenizers.py`, and the assertion that `import agent.llm` (and `bridge.telegram_bridge`) succeeds with every third-party module broken now covers the encoder leg's module scope.
- [ ] `tests/unit/test_llm_task_taxonomy.py` (doc/code parity, check 5; hotfix #1055 check 6 over every function in `agent/llm/backends/`) — no test change; the new leg module and the new site-table rows must satisfy both as written. Check 6 is the guard that keeps `asyncio.wait_for` out of the encoder leg.
- [ ] `tests/unit/test_doctor.py:881-909` (routing section: `Backend.OLLAMA` sites and the `ollama_daemon` row) — UPDATE: add the `local_encoder` row assertions (extra importable, weights present with matching checksum, one head per declared `LOCAL_ENCODER` site) with a fake models dir and a fake heads dir; the `ollama_daemon` assertions are unchanged.
- [ ] `tests/unit/test_classification_eval.py::test_audit_exit_codes` (`:395`) and `test_audit_over_two_sites_fails_when_either_misses` (`:412`) — UPDATE: parametrize the local-landing cases over `ollama` and `local_encoder` (the audit's landed-arm rule now applies to every backend other than `ANTHROPIC`), and add the head-provenance case: a `LOCAL_ENCODER` landing whose committed head `run_id` differs from the record's `fit.head_run_id` exits 1.
- [ ] `tests/unit/test_classification_eval.py::test_parse_candidates_rejects_an_unknown_backend` (`:444`) — no change (`CANDIDATE_BACKENDS` is derived from the enum, so `local_encoder` is accepted automatically); add `test_candidate_arm_builders_cover_every_backend` so a member without an arm builder fails by name.
- [ ] `tests/unit/test_classification_eval.py` — ADD (create alongside the existing runner tests): the `--fit` split is deterministic by digest and refuses under the held-out minimum before any arm runs; the fitted head round-trips through the leg's loader; the record carries the `fit` provenance block; `--preflight` prints the real-message count against each site's need and exits 1 when the routing sites cannot be met.
- [ ] The landed sites' own test files (for example `tests/unit/test_routing_classifiers.py` for C1, `tests/unit/test_promise_gate.py` for C9) — UPDATE only where a landing restructures the call (`prompt` becomes the text under classification and the instructions move to `system`): any test asserting the prompt string the fake receives is updated to the new `(prompt, system)` split. The fail-safe tests are byte-identical, since the wrapper contract does not change.

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
- [ ] Create `docs/features/local-encoder-classifier.md`: the leg (embedding model, per-site head, the output-type shape a landed site must have, `system` ignored by the leg and carried for the fallback), the fit protocol (digest split, held-out record, `fit` provenance, the head-run-id audit rule), the text-first composition rule for context-bearing sites, the rejection of zero-shot GLiClass with the plan-time numbers, and the per-site outcome table for this lane.
- [ ] Update `docs/features/llm-task-taxonomy.md`: the `backend` field row (`Backend.LOCAL_ENCODER`), the Router Rules table (the local-encoder rule ahead of the Ollama rules, renumbered), the Site Table rows for every landed site (backend `local_encoder`, the record id), the Acceptance Bar section (a fitted head is measured on the held-out split only; the `fit` block; the audit's head-provenance rule), and a "Lane B Outcome" section beside "Lane A Outcome" with the per-site numbers.
- [ ] Update `docs/features/nonharness-llm-wrapper.md`: the third leg under `agent/llm/backends/`, `default_sdk_timeout` for `LOCAL_ENCODER`, and the note that the encoder leg has no request timer (CPU work bounded by the 512-token window) and honors the deadline re-check only.
- [ ] Update `docs/infra/llm-task-routing.md`: a "Local encoder weights" section (model id, revision, file checksums, `LOCAL_ENCODER_MODELS_DIR`, `scripts/download_local_encoder_models.py`, the `/update` step, what doctor reports), the `llm_route ... backend=local_encoder` grep, and the rollback lever for an encoder landing (the one-word `backend` edit; no daemon to stop).
- [ ] Update `docs/features/config-timeout-catalog.md`: `TIMEOUTS__LOCAL_TYPED_HARD_S` is also the encoder leg's default budget for the fallback deadline check.
- [ ] Update `docs/features/local-model-policy.md`: the local encoder joins granite as a local classification backend; which sites each serves.
- [ ] Add rows to `docs/features/README.md` for `local-encoder-classifier.md` and update the taxonomy and wrapper rows' one-line summaries (three legs).

### Inline Documentation
- [ ] Module docstring for `agent/llm/backends/local_encoder.py` stating the leg protocol as this leg meets it, the import-safety contract, the head file format, and the output-type shape rule.
- [ ] Each landed `LLMTask` declaration keeps its fail-safe comment and gains one line naming the head file and the record id.
- [ ] Head files carry their provenance inline (`site`, `classes`, `embedding_model`, `embedding_sha256`, `run_id`, `n_train`, `n_train_real`, `reference_model`, `created_at`).

## Success Criteria

Placeholder.

## Team Orchestration

Placeholder.

## Step by Step Tasks

Placeholder.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `scripts/pytest-clean.sh tests/unit/ -x -q` | exit code 0 |
| Lint clean | `.venv/bin/python -m ruff check .` | exit code 0 |
| Format clean | `.venv/bin/python -m ruff format --check .` | exit code 0 |
| Encoder leg tests pass | `scripts/pytest-clean.sh tests/unit/test_llm_backend_local_encoder.py tests/unit/test_classifier_heads.py -q` | exit code 0 |
| Router and eligibility tests cover the new member | `scripts/pytest-clean.sh tests/unit/test_llm_router.py tests/unit/test_llm_router_eligibility.py tests/unit/test_llm_tasks.py -q` | exit code 0 |
| Doc/code parity and the hotfix #1055 walk over the new leg | `scripts/pytest-clean.sh tests/unit/test_llm_task_taxonomy.py -q` | exit code 0 |
| Import safety covers onnxruntime and tokenizers | `scripts/pytest-clean.sh tests/unit/test_llm_import_safety.py -q` | exit code 0 |
| No third-party import at the leg's module scope | `/usr/bin/grep -c "^import onnxruntime\|^import tokenizers\|^import numpy\|^from onnxruntime\|^from tokenizers\|^from numpy" agent/llm/backends/local_encoder.py` | match count == 0 |
| No `asyncio.wait_for` in any backend leg | `/usr/bin/grep -c "wait_for" agent/llm/backends/local_encoder.py` | match count == 0 |
| `agent.llm` imports without the extra | `.venv/bin/python -c "import sys; sys.modules['onnxruntime']=None; sys.modules['tokenizers']=None; import agent.llm, agent.llm.backends.local_encoder"` | exit code 0 |
| Runner tests pass (fit, preflight, audit provenance) | `scripts/pytest-clean.sh tests/unit/test_classification_eval.py -q` | exit code 0 |
| Every backend member has a runner arm builder | `scripts/pytest-clean.sh tests/unit/test_classification_eval.py -q -k "arm_builders_cover_every_backend"` | exit code 0 |
| Doctor reports the encoder row | `scripts/pytest-clean.sh tests/unit/test_doctor.py -q -k "local_encoder"` | exit code 0 |
| Every landed site's declared backend matches its record and its head | `.venv/bin/python -m tools.classification_eval --audit` | exit code 0 |
| Every committed head matches a declared `LOCAL_ENCODER` site and the pinned embedding checksum | `scripts/pytest-clean.sh tests/unit/test_classifier_heads.py -q` | exit code 0 |
| No head without a landed site (no orphan weights) | `.venv/bin/python -c "import json,pathlib,sys; from agent.llm.tasks import Backend, declared_sites; landed={d.task.site for d in declared_sites() if d.task.backend is Backend.LOCAL_ENCODER}; heads={p.stem for p in pathlib.Path('agent/llm/backends/heads').glob('*.json')}; sys.exit(0 if heads==landed else 1)"` | exit code 0 |
| Anti-criterion: no `torch`, `transformers`, or `gliclass` dependency | `/usr/bin/grep -c "torch\|transformers\|gliclass" pyproject.toml` | match count == 0 |
| Anti-criterion: no per-site backend switch in settings | `/usr/bin/grep -c "_sites\|local_encoder\|classification_local" config/settings.py` | match count == 0 |
| Anti-criterion: the leg never downloads weights | `/usr/bin/grep -c "urlopen\|httpx\|requests\.\|hf_hub_download" agent/llm/backends/local_encoder.py` | match count == 0 |
| Anti-criterion: C12 is not a `LOCAL_ENCODER` site | `/usr/bin/grep -c "Backend.LOCAL_ENCODER" bridge/job_router.py` | match count == 0 |
| Anti-criterion: the emoji embedding path is untouched (#3422 owns it) | `git diff main --stat -- tools/emoji_embedding.py agent/constants.py tools/react_with_emoji.py \| wc -l` | match count == 0 |
| Weights script verifies checksums | `/usr/bin/grep -c "sha256" scripts/download_local_encoder_models.py` | output > 0 |
| `/update` fetches the weights | `/usr/bin/grep -c "local_encoder.ensure_models" scripts/update/run.py` | output > 0 |
| Extra declared | `/usr/bin/grep -c "^classification-local = \[" pyproject.toml` | output > 0 |
| Feature doc exists | `test -f docs/features/local-encoder-classifier.md` | exit code 0 |
| Feature index updated | `grep -c "local-encoder-classifier.md" docs/features/README.md` | output > 0 |
| Infra doc names the weights | `grep -c "download_local_encoder_models" docs/infra/llm-task-routing.md` | output > 0 |
| Taxonomy doc carries the new rule | `grep -c "LOCAL_ENCODER" docs/features/llm-task-taxonomy.md` | output > 0 |

**Live evidence (manual, deployed bridge):** after `/update` has run on the `valor`-owning host (the extra installed, the weights fetched, services restarted) and one inbound message in a `valor` room: `grep -c "llm_route site=<landed site> backend=local_encoder" logs/bridge.log` is above 0 for the site the PR names, and `grep -c llm_fallback logs/bridge.log` is unchanged across that message. Needs a deployed bridge and a human reading two counts, so it stays outside the table.

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

Placeholder.
